"""Learned polisher on top of the majority vote baseline (Agent D).

The baseline already aligns every read to a draft and forces the draft to strand_length.
It is strong at high coverage and weak at low coverage, where the votes are thin. Instead of
learning the alignment, we keep the baseline's draft and its vote columns and train a small
1D CNN to correct the draft's remaining errors, like a Nanopore assembly polisher.

Per draft position the model sees the votes (A/C/G/T, deletion, insertion and the inserted
base in the gap in front of it), the coverage, the draft base and the position, and predicts
two things:
  - op head, 6 classes: keep, substitute to A/C/G/T, delete this position
  - insert head, 5 classes: nothing, or insert A/C/G/T in the gap in front of this position
Both drafts and truth have exactly strand_length bases, so a correct edit script has as many
deletions as insertions. apply_edits() uses that: it keeps the k most confident of each,
which makes the output exactly strand_length by construction.

Only reads and strand_length are used, never the reference (Decoder protocol).
This module imports helpers from dnacodec.baseline (owned by Agent C) and does not change it.
"""

from __future__ import annotations

import atexit
import math
import multiprocessing as mp
import os
import sys
import weakref
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein
from torch import nn

from ..baseline import (
    MAX_READS,
    _DEL,
    _fix_length,
    _pick_draft,
    _rebuild,
    _to_array,
    _votes,
    subsample,
)
from ..types import ALPHABET, Cluster, Strand

N_FEATURES = 17
N_OPS = 6  # keep, ->A, ->C, ->G, ->T, delete
N_INS = 5  # none, +A, +C, +G, +T
KEEP, DELETE = 0, 5

_LETTERS = np.frombuffer(ALPHABET.encode(), dtype=np.uint8)


# ---------------------------------------------------------------- draft and features


def votes_of(draft: str, reads: Cluster, arrays: list[np.ndarray]):
    """Vote columns of `draft` against the reads, in the tuple shape features_of() wants."""
    return _votes(draft, reads, arrays) + (len(reads),)


def refine_draft(
    start: str, reads: Cluster, arrays: list[np.ndarray], strand_length: int, iterations: int = 3
):
    """The baseline's rebuild loop starting from `start`, then the length fix.

    Returns (draft, votes). Exactly what dnacodec.baseline.reconstruct does, plus one more
    alignment pass so the vote columns match the returned draft.
    """
    draft = start
    votes = _votes(draft, reads, arrays)
    for _ in range(iterations):
        new = _rebuild(draft, *votes, n_reads=len(reads))
        if new == draft:
            break
        draft = new
        votes = _votes(draft, reads, arrays)
    draft = _fix_length(draft, *votes, strand_length=strand_length)
    return draft, votes_of(draft, reads, arrays)


def draft_of(cluster: Cluster, strand_length: int, iterations: int = 3):
    """The baseline draft for one non-empty cluster plus the votes against that draft.

    Same procedure as dnacodec.baseline.reconstruct (medoid draft, majority vote, fixed
    length), then one more alignment pass so the vote columns match the returned draft.
    """
    reads = subsample([r for r in cluster if r], MAX_READS)
    if not reads:
        return None, None
    arrays = [_to_array(r) for r in reads]
    return refine_draft(_pick_draft(reads, strand_length), reads, arrays, strand_length, iterations)


def start_reads(reads: Cluster, strand_length: int, k: int) -> list[str]:
    """The k reads that make the best starting drafts: medoid first.

    Same ranking as dnacodec.baseline._pick_draft (total edit distance to the other reads,
    ties by closeness to strand_length), so the first entry is always the baseline's draft.
    """
    if k <= 1:
        return [_pick_draft(reads, strand_length)]
    if len(reads) <= 2:
        order = sorted(range(len(reads)), key=lambda i: abs(len(reads[i]) - strand_length))
    else:
        dist = process.cdist(reads, reads, scorer=Levenshtein.distance, dtype=np.int32)
        total = dist.sum(axis=1)
        order = sorted(
            range(len(reads)), key=lambda i: (total[i], abs(len(reads[i]) - strand_length))
        )
    out: list[str] = []
    for i in order:
        if reads[i] not in out:
            out.append(reads[i])
        if len(out) == k:
            break
    return out


def features_of(draft: str, votes) -> np.ndarray:
    """(N_FEATURES, len(draft)) float32 from the vote columns of that draft."""
    base_votes, ins_votes, ins_base, n_reads = votes
    n = max(1.0, float(n_reads))
    length = len(draft)
    draft_codes = _to_array(draft)
    f = np.zeros((N_FEATURES, length), dtype=np.float32)
    f[0:4] = base_votes[:, :4].T / n
    f[4] = base_votes[:, _DEL] / n
    f[5] = ins_votes[:length] / n
    f[6:10] = ins_base[:length].T / n
    f[10:14] = np.eye(4, dtype=np.float32)[draft_codes].T
    f[14] = n_reads / MAX_READS
    f[15] = base_votes[np.arange(length), draft_codes] / n
    f[16] = np.arange(length, dtype=np.float32) / max(1, length - 1)
    return f


def labels_of(draft: str, truth: str) -> tuple[np.ndarray, np.ndarray]:
    """Per draft position: the op class, and the insert class for the gap in front of it.

    Insertions after the last draft base (rare) are dropped: the output has to end at
    strand_length anyway.
    """
    ops = np.zeros(len(draft), dtype=np.int64)
    ins = np.zeros(len(draft), dtype=np.int64)
    for op in Levenshtein.editops(draft, truth):
        if op.tag == "replace":
            ops[op.src_pos] = 1 + int(_to_array(truth[op.dest_pos])[0])
        elif op.tag == "delete":
            ops[op.src_pos] = DELETE
        elif op.tag == "insert" and op.src_pos < len(draft) and ins[op.src_pos] == 0:
            ins[op.src_pos] = 1 + int(_to_array(truth[op.dest_pos])[0])
    return ops, ins


def example_of(cluster: Cluster, strand_length: int, truth: str | None = None):
    """(features, op labels, insert labels) for one cluster; labels are None without truth."""
    draft, votes = draft_of(cluster, strand_length)
    if draft is None:
        return None
    f = features_of(draft, votes)
    if truth is None:
        return f, None, None
    ops, ins = labels_of(draft, truth)
    return f, ops, ins


# ---------------------------------------------------------------- model


@dataclass
class PolishConfig:
    channels: int = 128
    blocks: tuple[int, ...] = (1, 2, 4, 8, 1, 2, 4, 8)  # dilations, receptive field ~65
    kernel: int = 3
    stem_kernel: int = 5
    dropout: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class _Block(nn.Module):
    def __init__(self, c: int, k: int, dilation: int, dropout: float):
        super().__init__()
        pad = dilation * (k - 1) // 2
        self.conv1 = nn.Conv1d(c, c, k, padding=pad, dilation=dilation)
        self.conv2 = nn.Conv1d(c, c, k, padding=pad, dilation=dilation)
        self.norm1 = nn.GroupNorm(8, c)
        self.norm2 = nn.GroupNorm(8, c)
        self.drop = nn.Dropout(dropout)
        self.act = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.conv1(self.act(self.norm1(x)))
        h = self.conv2(self.act(self.norm2(h)))
        return x + self.drop(h)


class PolishNet(nn.Module):
    def __init__(self, cfg: PolishConfig | None = None):
        super().__init__()
        self.cfg = cfg = cfg or PolishConfig()
        c = cfg.channels
        self.stem = nn.Conv1d(N_FEATURES, c, cfg.stem_kernel, padding=cfg.stem_kernel // 2)
        self.blocks = nn.Sequential(*[_Block(c, cfg.kernel, d, cfg.dropout) for d in cfg.blocks])
        self.norm = nn.GroupNorm(8, c)
        self.op_head = nn.Conv1d(c, N_OPS, 1)
        self.ins_head = nn.Conv1d(c, N_INS, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """x (B, N_FEATURES, L) -> op logits (B, N_OPS, L), insert logits (B, N_INS, L)."""
        h = self.blocks(self.stem(x))
        h = torch.nn.functional.gelu(self.norm(h))
        return self.op_head(h), self.ins_head(h)


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def save_checkpoint(path, model: PolishNet, **extra) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save({"config": model.cfg.to_dict(), "model": model.state_dict(), **extra}, tmp)
    tmp.replace(path)


def load_checkpoint(path, device: torch.device | str = "cpu") -> tuple[PolishNet, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    cfg = dict(ckpt["config"])
    cfg["blocks"] = tuple(cfg["blocks"])
    model = PolishNet(PolishConfig(**cfg)).to(device)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model, ckpt


# ---------------------------------------------------------------- applying the edits


DEFAULT_THRESHOLDS = {"low_max_reads": 3, "low": (0.5, 0.5), "high": (0.5, 0.5)}
_EPS = 1e-9


def _gain_edits(op_prob: np.ndarray, ins_prob: np.ndarray, sub_margin: float, indel_margin: float):
    """Joint choice of the edit script by log odds, see apply_edits(mode="gain")."""
    log_op = np.log(op_prob + _EPS)
    log_ins = np.log(ins_prob + _EPS)
    base_choice = op_prob[1:5].argmax(0)
    sub_gain = log_op[1:5].max(0) - log_op[KEEP]
    substitute = sub_gain > sub_margin
    # the alternative to deleting a position is whatever we would otherwise do there
    alt = np.where(substitute, log_op[1:5].max(0), log_op[KEEP])
    del_gain = log_op[DELETE] - alt
    ins_choice = ins_prob[1:].argmax(0) + 1
    ins_gain = log_ins[1:].max(0) - log_ins[0]
    del_order = np.argsort(-del_gain)
    ins_order = np.argsort(-ins_gain)
    pair = del_gain[del_order] + ins_gain[ins_order]
    k = int(np.searchsorted(-pair, -indel_margin, side="left"))  # pair is sorted descending
    del_pos = np.sort(del_order[:k])
    ins_pos = np.sort(ins_order[:k])
    substitute = substitute.copy()
    substitute[del_pos] = False
    return substitute, base_choice, del_pos, ins_pos, ins_choice


def apply_edits(
    draft: str,
    op_prob: np.ndarray,
    ins_prob: np.ndarray,
    strand_length: int,
    sub_threshold: float = 0.5,
    indel_threshold: float = 0.5,
    mode: str = "topk",
) -> str:
    """Edit the draft with the predicted ops. op_prob (N_OPS, L), ins_prob (N_INS, L).

    mode="topk" (the default, unchanged behavior): an edit is only applied when the model is
    at least sub_threshold (substitutions) or indel_threshold (deletions and insertions)
    sure; thresholds are tuned on the validation split, because at very low coverage an
    unsure edit is worse than keeping the draft. Substitutions are free. Deletions and
    insertions are paired: both draft and truth have strand_length bases, so a correct script
    has equally many, and keeping the k most confident of each makes the result exactly
    strand_length without any padding.

    mode="gain": the same pairing constraint, but chosen jointly by log odds instead of by a
    probability threshold. A substitution is applied when log p(sub) - log p(keep) exceeds
    sub_threshold (now a margin in nats), and the j-th best deletion is paired with the j-th
    best insertion while their combined log odds exceed indel_threshold. Under independent
    per-position probabilities that maximizes the likelihood of the edit script subject to
    "as many deletions as insertions", which the top-k rule only approximates.
    """
    length = len(draft)
    if mode == "gain":
        substitute, base_choice, del_pos, ins_pos, ins_choice = _gain_edits(
            op_prob, ins_prob, sub_threshold, indel_threshold
        )
    elif mode == "topk":
        op_choice = op_prob.argmax(0)
        base_choice = op_prob[1:5].argmax(0)  # best substitution if we substitute
        sub_score = op_prob[1:5].max(0)
        del_score = op_prob[DELETE]
        ins_choice = ins_prob.argmax(0)
        ins_score = 1.0 - ins_prob[0]
        # unsure edits fall back to the draft
        substitute = (op_choice >= 1) & (op_choice <= 4) & (sub_score >= sub_threshold)
        op_choice = np.where(substitute, op_choice, np.where(op_choice == DELETE, DELETE, KEEP))

        del_pos = np.flatnonzero((op_choice == DELETE) & (del_score >= indel_threshold))
        ins_pos = np.flatnonzero((ins_choice > 0) & (ins_score >= indel_threshold))
        k = min(len(del_pos), len(ins_pos))
        if k < len(del_pos):
            del_pos = del_pos[np.argsort(-del_score[del_pos])[:k]]
        if k < len(ins_pos):
            ins_pos = ins_pos[np.argsort(-ins_score[ins_pos])[:k]]
    else:
        raise ValueError(f"unknown apply mode {mode!r}")
    dropped = np.zeros(length, dtype=bool)
    dropped[del_pos] = True
    inserted = np.zeros(length, dtype=bool)
    inserted[ins_pos] = True

    draft_codes = _to_array(draft)
    out_codes = np.where(substitute, base_choice, draft_codes)

    if not inserted.any() and not dropped.any():
        return _LETTERS[out_codes].tobytes().decode()
    out: list[int] = []
    ins_codes = (ins_choice - 1).clip(min=0)
    for i in range(length):
        if inserted[i]:
            out.append(int(ins_codes[i]))
        if not dropped[i]:
            out.append(int(out_codes[i]))
    result = _LETTERS[np.array(out, dtype=np.int64)].tobytes().decode()
    if len(result) != strand_length:  # defensive, pairing should make this impossible
        result = (result + draft)[:strand_length]
    return result


# ---------------------------------------------------------------- CPU work on a process pool

# Building the draft and its vote columns is pure CPU work (numpy plus rapidfuzz alignment) and
# it dominates decode(): the CNN itself is a few milliseconds per batch. The torch model cannot
# be shipped to worker processes, so PolishDecoder is main_process_only and evaluation hands it
# all clusters of a chunk of trials in one call - which used to leave that CPU part on a single
# core. _FeaturePool fans it out over a process pool of our own, inside decode(); the model stays
# here and only the finished feature arrays come back.
#
# Oversubscription: dnacodec.evaluate runs its own pool over trials, but the two are never busy
# at the same time. For a main_process_only decoder evaluate simulates a chunk of trials in its
# pool, then calls decode() (its pool idle), then recovers in its pool again (our pool idle).
# The phases alternate, so the idle pool only costs memory. DEFAULT_WORKERS still stays well
# below the core count, and a pool is never created inside a worker process (no nested pools).

CHUNK_CLUSTERS = 256  # upper bound of clusters per task, keeps the pickled payloads small
TASKS_PER_WORKER = 4  # aim for this many tasks per worker, so the last tasks even the load out


def _default_workers() -> int:
    """Safe default for the feature pool: half the cores, at most 8, override with POLISH_WORKERS.

    The loop already runs a pool of its own over trials (see above), so we leave it room.
    """
    env = os.environ.get("POLISH_WORKERS")
    if env:
        return max(1, int(env))
    return max(1, min(8, (os.cpu_count() or 1) // 2))


DEFAULT_WORKERS = _default_workers()


def _default_start_method() -> str:
    """fork on Linux, spawn elsewhere; override with POLISH_START_METHOD.

    fork is what dnacodec.evaluate's own pool already uses on Linux, from this very process,
    so the polisher's pool adds no new kind of risk. The workers only run numpy and rapidfuzz,
    never CUDA, which is what makes forking a process with a GPU context safe here (torch's
    own DataLoader forks the same way), and they start instantly with torch already imported.
    forkserver and spawn also work (POLISH_START_METHOD=forkserver), but they re-import the
    caller's __main__ in every worker, which needs an `if __name__ == "__main__"` guard and
    fails outright for a script piped into python.
    On macOS forking a process that has loaded torch is unreliable, so spawn is used there: it
    costs a few seconds once per pool, not once per decode(), because the pool is kept alive.
    """
    available = mp.get_all_start_methods()
    override = os.environ.get("POLISH_START_METHOD")
    if override:
        if override not in available:
            raise ValueError(f"start method {override!r} is not one of {available}")
        return override
    if "fork" in available and not sys.platform.startswith("darwin"):
        return "fork"
    return "spawn" if "spawn" in available else available[0]


def _in_worker_process() -> bool:
    """True inside any multiprocessing worker (ours, or one of evaluate's trial workers)."""
    return mp.parent_process() is not None


def _init_feature_worker() -> None:
    """One thread per worker: the parallelism is one process per chunk of clusters."""
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    try:
        torch.set_num_threads(1)
    except Exception:  # pragma: no cover - torch always has this, but never fail a worker here
        pass


def build_features(clusters: Sequence[Cluster], strand_length: int):
    """Drafts, features and read counts for non-empty clusters, in order.

    Module level and free of any torch state, so ProcessPoolExecutor can pickle it by reference
    and a worker never receives the model. The features come back stacked into one array when
    all drafts have the same length (they do, _fix_length forces strand_length), which pickles
    much faster than one small array per cluster.
    """
    drafts: list[str] = []
    feats: list[np.ndarray] = []
    sizes: list[int] = []
    for cluster in clusters:
        draft, votes = draft_of(cluster, strand_length)
        drafts.append(draft)
        feats.append(features_of(draft, votes))
        sizes.append(votes[3])
    if feats and len({f.shape for f in feats}) == 1:
        return drafts, np.stack(feats), sizes
    return drafts, feats, sizes


class _FeaturePool:
    """A process pool for build_features, created lazily and kept alive across decode() calls.

    decode() is called once per chunk of trials, hundreds of times in a loop run, and starting
    a pool costs more than one chunk of work, so the pool outlives the call. close() shuts it
    down; every live pool is also closed at interpreter exit.
    """

    def __init__(self, workers: int, start_method: str | None = None) -> None:
        if workers < 1:
            raise ValueError(f"workers={workers} must be >= 1")
        self.workers = int(workers)
        self.start_method = start_method or _default_start_method()
        self._pool: ProcessPoolExecutor | None = None
        _LIVE_POOLS.add(self)

    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            ctx = mp.get_context(self.start_method)
            if self.start_method == "forkserver":
                # the server imports this module once; the workers fork from it for free
                ctx.set_forkserver_preload([__name__])
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=ctx, initializer=_init_feature_worker
            )
        return self._pool

    def map_chunks(self, chunks: Sequence[Sequence[Cluster]], strand_length: int) -> list:
        """build_features for every chunk, results in submission order.

        A worker that raises re-raises here with its own traceback. A worker that dies (killed,
        out of memory) breaks the pool: we close it and raise, so the call fails loudly instead
        of hanging, and the next call starts a fresh pool.
        """
        pool = self._executor()
        futures = []
        try:
            for chunk in chunks:
                futures.append(pool.submit(build_features, chunk, strand_length))
            return [f.result() for f in futures]
        except BrokenExecutor as exc:
            self.close()
            raise RuntimeError(
                "the polish feature worker pool died (a worker process crashed or was killed). "
                "Pass workers=None to PolishDecoder to build the features in this process."
            ) from exc
        finally:
            for f in futures:
                f.cancel()  # no-op for finished tasks, drops queued ones after a failure

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)


_LIVE_POOLS: weakref.WeakSet[_FeaturePool] = weakref.WeakSet()


@atexit.register
def _close_feature_pools() -> None:
    for pool in list(_LIVE_POOLS):
        pool.close()


def _build_all(clusters: Sequence[Cluster], strand_length: int, pool: _FeaturePool | None):
    """build_features over all clusters, split across the pool when there is one.

    The result does not depend on the split: build_features is deterministic per cluster and the
    chunks come back in order, so the features are bit for bit the ones the serial path builds.
    """
    n = len(clusters)
    if pool is None or n < 2:
        drafts, feats, sizes = build_features(clusters, strand_length)
        return drafts, list(feats), sizes
    size = max(1, min(CHUNK_CLUSTERS, math.ceil(n / (pool.workers * TASKS_PER_WORKER))))
    chunks = [clusters[start : start + size] for start in range(0, n, size)]
    drafts: list[str] = []
    feats: list[np.ndarray] = []
    sizes: list[int] = []
    for chunk_drafts, chunk_feats, chunk_sizes in pool.map_chunks(chunks, strand_length):
        drafts.extend(chunk_drafts)
        feats.extend(chunk_feats)  # rows of the stacked array, or the per-cluster arrays
        sizes.extend(chunk_sizes)
    return drafts, feats, sizes


# ---------------------------------------------------------------- decoder


@torch.no_grad()
def predict_probs(
    model: PolishNet,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device: torch.device | str | None = None,
    pool: _FeaturePool | None = None,
):
    """Drafts and edit probabilities for every non-empty cluster.

    Returns (index, draft, op probabilities (N_OPS, L), insert probabilities (N_INS, L),
    number of reads) per non-empty cluster, in input order.

    pool: an optional _FeaturePool that builds the drafts and features in worker processes.
    The model itself always runs here, batched, and the output is identical either way.
    """
    device = torch.device(device) if device is not None else next(model.parameters()).device
    idx = [i for i, cluster in enumerate(clusters) if any(cluster)]
    drafts, feats, sizes = _build_all([clusters[i] for i in idx], strand_length, pool)
    amp = device.type == "cuda"
    out = []
    for start in range(0, len(idx), batch_size):
        stop = min(start + batch_size, len(idx))
        x = torch.from_numpy(np.stack(feats[start:stop])).to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            op_logits, ins_logits = model(x)
        op_p = op_logits.float().softmax(1).cpu().numpy()
        ins_p = ins_logits.float().softmax(1).cpu().numpy()
        out.extend(
            (idx[start + j], drafts[start + j], op_p[j], ins_p[j], sizes[start + j])
            for j in range(stop - start)
        )
    return out


def edits_from_probs(n: int, probs, strand_length: int, thresholds: dict | None = None):
    """Apply the predicted edits to n clusters' drafts with the given thresholds."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    out: list[Strand | None] = [None] * n
    for i, draft, op_p, ins_p, size in probs:
        sub_t, ins_t = th["low"] if size <= th["low_max_reads"] else th["high"]
        out[i] = apply_edits(draft, op_p, ins_p, strand_length, sub_t, ins_t)
    return out


def polish_clusters(
    model: PolishNet,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device: torch.device | str | None = None,
    thresholds: dict | None = None,
    pool: _FeaturePool | None = None,
) -> list[Strand | None]:
    """Baseline draft plus learned corrections. None for clusters without any read."""
    probs = predict_probs(model, clusters, strand_length, batch_size, device, pool)
    return edits_from_probs(len(clusters), probs, strand_length, thresholds)


# ------------------------------------------------- iterative and multi draft polishing


@dataclass
class _Candidate:
    cluster: int
    draft: str
    votes: tuple
    weight: int = 1  # how many starting reads led to this draft
    score: float = 0.0


def _forward_batched(model, feats, batch_size, device):
    """Softmax outputs of the net for a list of (N_FEATURES, L) arrays, in input order."""
    amp = device.type == "cuda"
    out = []
    for start in range(0, len(feats), batch_size):
        chunk = feats[start : start + batch_size]
        x = torch.from_numpy(np.stack(chunk)).to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            op_logits, ins_logits = model(x)
        op_p = op_logits.float().softmax(1).cpu().numpy()
        ins_p = ins_logits.float().softmax(1).cpu().numpy()
        out.extend(zip(op_p, ins_p))
    return out


def _polish_pass(model, items, reads_of, arrays_of, strand_length, th, rounds, mode,
                 with_score, batch_size, device):
    """Polish one starting draft per item ((cluster index, starting read)), `rounds` times.

    Returns the candidates, with .score set to the model's log probability that the result
    needs no further edit when with_score is on.
    """
    cands: list[_Candidate] = []
    seen: dict[tuple[int, str], _Candidate] = {}
    for i, start in items:
        draft, votes = refine_draft(start, reads_of[i], arrays_of[i], strand_length)
        merged = seen.get((i, draft))
        if merged is not None:  # two starting reads that ended in the same draft
            merged.weight += 1
            continue
        cand = _Candidate(i, draft, votes)
        seen[(i, draft)] = cand
        cands.append(cand)
    if not cands:
        return cands
    for r in range(rounds):
        probs = _forward_batched(
            model, [features_of(c.draft, c.votes) for c in cands], batch_size, device
        )
        last = r == rounds - 1
        for cand, (op_p, ins_p) in zip(cands, probs):
            sub_t, ins_t = th["low"] if cand.votes[3] <= th["low_max_reads"] else th["high"]
            cand.draft = apply_edits(cand.draft, op_p, ins_p, strand_length, sub_t, ins_t, mode)
            if not last or with_score:
                cand.votes = votes_of(cand.draft, reads_of[cand.cluster], arrays_of[cand.cluster])
    if with_score:  # one more pass: how sure is the model that nothing is left to fix?
        probs = _forward_batched(
            model, [features_of(c.draft, c.votes) for c in cands], batch_size, device
        )
        for cand, (op_p, ins_p) in zip(cands, probs):
            cand.score = float(np.log(op_p[KEEP] + _EPS).mean() + np.log(ins_p[0] + _EPS).mean())
    return cands


@torch.no_grad()
def polish_clusters_multi(
    model: PolishNet,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device: torch.device | str | None = None,
    thresholds: dict | None = None,
    rounds: int = 1,
    drafts: int = 1,
    select: str = "confidence",
    mode: str = "topk",
    escalate: float | None = None,
) -> list[Strand | None]:
    """Polishing with optional extra rounds and several starting drafts.

    rounds > 1: after applying the edits the vote columns are recomputed against the polished
    strand and the model runs again, like an iterative consensus.
    drafts > 1: the polishing runs once per starting read (the medoid first, then the next
    best ones), and the candidates are ranked by `select`:
      "confidence": the model's own log probability that the candidate needs no further edit,
      "agree": how many starting reads produced it, ties by confidence,
      "reads": lowest mean edit distance to the cluster's reads.
    escalate: only pay for the other drafts where the first one scores below this confidence
    (a negative number, mean log probability per position); the rest keep the first result.
    With rounds=1, drafts=1 this is polish_clusters() with the same numbers.
    """
    device = torch.device(device) if device is not None else next(model.parameters()).device
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reads_of: dict[int, list[str]] = {}
    arrays_of: dict[int, list[np.ndarray]] = {}
    starts_of: dict[int, list[str]] = {}
    for i, cluster in enumerate(clusters):
        if not any(cluster):
            continue
        reads = subsample([r for r in cluster if r], MAX_READS)
        reads_of[i] = reads
        arrays_of[i] = [_to_array(r) for r in reads]
        starts_of[i] = start_reads(reads, strand_length, drafts)
    if not reads_of:
        return [None] * len(clusters)

    ranked = drafts > 1 and select in ("confidence", "agree")
    if escalate is None:
        items = [(i, s) for i, starts in starts_of.items() for s in starts]
        cands = _polish_pass(model, items, reads_of, arrays_of, strand_length, th, rounds, mode,
                             ranked, batch_size, device)
    else:  # cheap first draft everywhere, the other drafts only where it looks shaky
        cands = _polish_pass(model, [(i, s[0]) for i, s in starts_of.items()], reads_of,
                             arrays_of, strand_length, th, rounds, mode, True, batch_size, device)
        unsure = {c.cluster for c in cands if c.score < escalate}
        extra = [(i, s) for i in unsure for s in starts_of[i][1:]]
        if extra:
            cands += _polish_pass(model, extra, reads_of, arrays_of, strand_length, th, rounds,
                                  mode, True, batch_size, device)
        ranked = True
    if drafts > 1 and select == "reads":
        for cand in cands:
            cand.score = -float(
                np.mean([Levenshtein.distance(cand.draft, r) for r in reads_of[cand.cluster]])
            )

    out: list[Strand | None] = [None] * len(clusters)
    best: dict[int, tuple] = {}
    for cand in cands:
        key = (cand.weight, cand.score) if select == "agree" and ranked else (cand.score,)
        if cand.cluster not in best or key > best[cand.cluster][0]:
            best[cand.cluster] = (key, cand.draft)
    for i, (_, draft) in best.items():
        out[i] = draft
    return out


class PolishDecoder:
    """Majority vote draft plus a learned correction. Implements dnacodec.types.Decoder.

    The defaults reproduce the original decoder exactly. rounds, drafts, select and mode
    switch on the extras described in polish_clusters_multi() and apply_edits().

    The CNN runs here on the GPU, the drafts and features are built on a process pool
    (workers > 1) that lives as long as the decoder. workers=None or 1 keeps everything in
    this process, exactly as before. Call close() when done, or let the atexit hook do it.
    Only the default path (rounds=1, drafts=1, mode="topk") uses the pool so far; the multi
    draft path interleaves CPU and GPU work per round and still runs in this process.
    """

    name = "polish"
    main_process_only = True  # the CNN runs on one GPU; feature building is CPU work

    def __init__(
        self,
        checkpoint_path: str | Path,
        device: str | None = None,
        batch_size: int = 512,
        thresholds: dict | None = None,
        rounds: int = 1,
        drafts: int = 1,
        select: str = "confidence",
        mode: str = "topk",
        escalate: float | None = None,
        workers: int | None = DEFAULT_WORKERS,
    ):
        from .net import pick_device

        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.batch_size = batch_size
        self.thresholds = thresholds or ckpt.get("thresholds") or DEFAULT_THRESHOLDS
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics", "source")}
        self.rounds, self.drafts, self.select, self.mode = rounds, drafts, select, mode
        self.escalate = escalate
        if workers is not None and int(workers) < 1:
            raise ValueError(f"workers={workers} must be >= 1 or None")
        self.workers = 1 if workers is None else int(workers)
        self._pool: _FeaturePool | None = None

    def _feature_pool(self) -> _FeaturePool | None:
        """The pool, started on first use. None when single process, or inside a worker."""
        if self.workers <= 1 or _in_worker_process():
            return None  # no nested pools: a worker keeps the CPU work to itself
        if self._pool is None:
            self._pool = _FeaturePool(self.workers)
        return self._pool

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        if self.rounds == 1 and self.drafts == 1 and self.mode == "topk":  # the original path
            return polish_clusters(self.model, clusters, strand_length, self.batch_size,
                                   self.device, self.thresholds, self._feature_pool())
        return polish_clusters_multi(self.model, clusters, strand_length, self.batch_size,
                                     self.device, self.thresholds, self.rounds, self.drafts,
                                     self.select, self.mode, self.escalate)

    def close(self) -> None:
        """Shut the feature workers down. Idempotent; a later decode() starts a new pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def __enter__(self) -> PolishDecoder:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
