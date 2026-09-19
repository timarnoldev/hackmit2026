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
from rapidfuzz.distance import Levenshtein
from torch import nn

from ..baseline import MAX_READS, _DEL, _pick_draft, _rebuild, _to_array, _votes, subsample
from ..types import ALPHABET, Cluster, Strand

N_FEATURES = 17
N_OPS = 6  # keep, ->A, ->C, ->G, ->T, delete
N_INS = 5  # none, +A, +C, +G, +T
KEEP, DELETE = 0, 5

_LETTERS = np.frombuffer(ALPHABET.encode(), dtype=np.uint8)


# ---------------------------------------------------------------- draft and features


def draft_of(cluster: Cluster, strand_length: int, iterations: int = 3):
    """The baseline draft for one non-empty cluster plus the votes against that draft.

    Same procedure as dnacodec.baseline.reconstruct (medoid draft, majority vote, fixed
    length), then one more alignment pass so the vote columns match the returned draft.
    """
    reads = subsample([r for r in cluster if r], MAX_READS)
    if not reads:
        return None, None
    arrays = [_to_array(r) for r in reads]
    draft = _pick_draft(reads, strand_length)
    votes = _votes(draft, reads, arrays)
    for _ in range(iterations):
        new = _rebuild(draft, *votes, n_reads=len(reads))
        if new == draft:
            break
        draft = new
        votes = _votes(draft, reads, arrays)
    from ..baseline import _fix_length

    draft = _fix_length(draft, *votes, strand_length=strand_length)
    return draft, _votes(draft, reads, arrays) + (len(reads),)


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


def apply_edits(
    draft: str,
    op_prob: np.ndarray,
    ins_prob: np.ndarray,
    strand_length: int,
    sub_threshold: float = 0.5,
    indel_threshold: float = 0.5,
) -> str:
    """Edit the draft with the predicted ops. op_prob (N_OPS, L), ins_prob (N_INS, L).

    An edit is only applied when the model is at least sub_threshold (substitutions) or
    indel_threshold (deletions and insertions) sure; thresholds are tuned on the validation
    split, because at very low coverage an unsure edit is worse than keeping the draft.
    Substitutions are free. Deletions and insertions are paired: both draft and truth have
    strand_length bases, so a correct script has equally many, and keeping the k most
    confident of each makes the result exactly strand_length without any padding.
    """
    length = len(draft)
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


class PolishDecoder:
    """Majority vote draft plus a learned correction. Implements dnacodec.types.Decoder.

    The CNN runs here on the GPU, the drafts and features are built on a process pool
    (workers > 1) that lives as long as the decoder. workers=None or 1 keeps everything in
    this process, exactly as before. Call close() when done, or let the atexit hook do it.
    """

    name = "polish"
    main_process_only = True  # the CNN runs on one GPU; feature building is CPU work

    def __init__(
        self,
        checkpoint_path: str | Path,
        device: str | None = None,
        batch_size: int = 512,
        thresholds: dict | None = None,
        workers: int | None = DEFAULT_WORKERS,
    ):
        from .net import pick_device

        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.batch_size = batch_size
        self.thresholds = thresholds or ckpt.get("thresholds") or DEFAULT_THRESHOLDS
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics", "source")}
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
        return polish_clusters(self.model, clusters, strand_length, self.batch_size, self.device,
                               self.thresholds, self._feature_pool())

    def close(self) -> None:
        """Shut the feature workers down. Idempotent; a later decode() starts a new pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def __enter__(self) -> PolishDecoder:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
