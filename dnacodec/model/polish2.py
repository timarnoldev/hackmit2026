"""Polisher v2: the same draft-and-edit idea, but the model sees every read, not the votes.

The v1 polisher (``dnacodec.model.polish``) feeds a 1D CNN 17 numbers per draft position, all
of them *marginal* counts: how many reads say A, how many say "deleted", how many want an
insertion here. That throws away which read said what. Two reads disagreeing in a block
because one of them slipped out of register look exactly like two independent single-base
disagreements, and a read that is bad everywhere counts as much as a good one.

v2 keeps the classic alignment (medoid draft, rapidfuzz opcodes, same draft as v1, so the two
are directly comparable) but hands the network the full alignment matrix:

    cols[r, i] in {A, C, G, T, deleted}    what read r has at draft position i
    insb[r, i] in {none, A, C, G, T}       what read r inserts in the gap before position i

That is a *set* of reads, so the network is permutation invariant by construction: a shared
per-read encoder, masked mean/max pooling over the read axis, the pooled summary broadcast
back to each read for a second per-read pass (so a read can be judged against the consensus),
pooled again, and only then the per-position trunk. The 17 v1 features are recomputed from
cols/insb on the GPU and concatenated, so v2 is a strict superset of v1's input.

Heads, edit application and the exactly-strand_length guarantee are v1's, unchanged
(``apply_edits`` is imported, not reimplemented).

Nothing in this module touches ``dnacodec.model.polish`` or the checkpoint it loads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F
from rapidfuzz.distance import Levenshtein
from torch import nn

from ..baseline import MAX_READS, _DEL, _pick_draft, _to_array, subsample
from ..types import Cluster, Strand
from .polish import (
    DEFAULT_THRESHOLDS,
    KEEP,
    N_FEATURES,
    N_INS,
    N_OPS,
    _EPS,
    apply_edits,
    labels_of,
    refine_draft,
    start_reads,
)

PAD_COL = 5  # cols value for "this read slot is empty"
READ_FEATURES = 10  # one-hot over 5 column states + one-hot over 5 insert states


# ---------------------------------------------------------------- alignment matrix


def read_columns(draft: str, reads: Sequence[str], arrays: Sequence[np.ndarray]):
    """Per-read alignment of the cluster against `draft`.

    Returns (cols, insb), both (len(reads), len(draft)) int8:
      cols[r, i]  0..3 the base read r has at draft position i, 4 if read r misses it
      insb[r, i]  0 none, 1..4 the first base read r inserts in the gap before position i

    Same alignment and the same gap convention as ``dnacodec.baseline._votes``: summing the
    one-hots of these two matrices over r reproduces its base_votes, ins_votes and ins_base
    (for the gaps in front of a position, which is all features_of() ever uses).
    """
    n_pos = len(draft)
    cols = np.full((len(reads), n_pos), _DEL, dtype=np.int8)
    insb = np.zeros((len(reads), n_pos), dtype=np.int8)
    for r, (read, arr) in enumerate(zip(reads, arrays)):
        col, ib = cols[r], insb[r]
        for tag, i1, i2, j1, j2 in Levenshtein.opcodes(draft, read):
            if tag == "equal" or tag == "replace":
                m = min(i2 - i1, j2 - j1)
                col[i1 : i1 + m] = arr[j1 : j1 + m]
                if j2 - j1 > m and i2 < n_pos and ib[i2] == 0:
                    ib[i2] = arr[j1 + m] + 1
            elif tag == "insert":
                if i1 < n_pos and ib[i1] == 0:
                    ib[i1] = arr[j1] + 1
            # "delete": those draft positions stay _DEL
    return cols, insb


def pack(draft: str, reads: Sequence[str], arrays: Sequence[np.ndarray], max_reads: int = MAX_READS):
    """(cols, insb, draft codes, n_reads) padded to max_reads rows, all int8."""
    cols, insb = read_columns(draft, reads, arrays)
    n, length = cols.shape
    full_cols = np.full((max_reads, length), PAD_COL, dtype=np.int8)
    full_ins = np.zeros((max_reads, length), dtype=np.int8)
    full_cols[:n] = cols
    full_ins[:n] = insb
    return full_cols, full_ins, _to_array(draft).astype(np.int8), n


def pack_cluster(cluster: Cluster, strand_length: int, truth: str | None = None):
    """One training/inference example from a raw cluster: the v1 draft plus its alignment."""
    reads = subsample([r for r in cluster if r], MAX_READS)
    if not reads:
        return None
    arrays = [_to_array(r) for r in reads]
    draft, _ = refine_draft(_pick_draft(reads, strand_length), reads, arrays, strand_length)
    cols, insb, draft_codes, n = pack(draft, reads, [_to_array(r) for r in reads])
    if truth is None:
        return cols, insb, draft_codes, n, None, None
    ops, ins = labels_of(draft, truth)
    return cols, insb, draft_codes, n, ops.astype(np.int8), ins.astype(np.int8)


# ---------------------------------------------------------------- features on the GPU


def expand_features(cols: torch.Tensor, insb: torch.Tensor, draft: torch.Tensor,
                    nreads: torch.Tensor):
    """(B, R, L) int -> per-read tensor (B, R, 10, L), v1 features (B, 17, L), mask (B, R, 1, 1).

    The 17 position features are recomputed here from the same matrices, so they are exactly
    ``dnacodec.model.polish.features_of`` up to float rounding, and v2 sees everything v1 saw.
    """
    b, r, length = cols.shape
    present = torch.arange(r, device=cols.device)[None, :] < nreads[:, None]
    mask = present.to(torch.float32)  # (B, R)
    mask4 = mask[:, :, None, None]  # (B, R, 1, 1)
    n = nreads.clamp(min=1).to(torch.float32)[:, None, None]  # (B, 1, 1)

    col_oh = F.one_hot(cols.clamp(max=_DEL).long(), 5).to(torch.float32)  # (B, R, L, 5)
    ins_oh = F.one_hot(insb.clamp(min=0, max=4).long(), 5).to(torch.float32)
    col_oh = col_oh * mask[:, :, None, None]
    ins_oh = ins_oh * mask[:, :, None, None]

    x_read = torch.cat([col_oh, ins_oh], dim=-1).permute(0, 1, 3, 2).contiguous()  # (B,R,10,L)

    base_counts = col_oh.sum(1).permute(0, 2, 1)  # (B, 5, L)
    ins_counts = ins_oh.sum(1).permute(0, 2, 1)  # (B, 5, L)
    draft_oh = F.one_hot(draft.long(), 4).to(torch.float32).permute(0, 2, 1)  # (B, 4, L)

    pos = torch.arange(length, device=cols.device, dtype=torch.float32) / max(1, length - 1)
    f = torch.empty(b, N_FEATURES, length, device=cols.device, dtype=torch.float32)
    f[:, 0:4] = base_counts[:, :4] / n
    f[:, 4] = base_counts[:, _DEL] / n[:, 0]
    f[:, 5] = (nreads.clamp(min=1).to(torch.float32)[:, None] - ins_counts[:, 0]) / n[:, 0]
    f[:, 6:10] = ins_counts[:, 1:5] / n
    f[:, 10:14] = draft_oh
    f[:, 14] = (nreads.to(torch.float32) / MAX_READS)[:, None]
    f[:, 15] = (base_counts[:, :4] * draft_oh).sum(1) / n[:, 0]
    f[:, 16] = pos[None, :]
    return x_read, f, mask4


# ---------------------------------------------------------------- model


@dataclass
class PolishConfig2:
    channels: int = 192
    blocks: tuple[int, ...] = (1, 2, 4, 8, 16, 1, 2, 4, 8, 16)
    read_channels: int = 48
    read_blocks: tuple[int, ...] = (1, 2, 4)
    read_blocks2: tuple[int, ...] = (1, 2)
    kernel: int = 3
    stem_kernel: int = 5
    dropout: float = 0.0
    groups: int = 8

    def to_dict(self) -> dict:
        return asdict(self)


class _Block(nn.Module):
    """Pre-activation residual block, same shape as v1's."""

    def __init__(self, c: int, k: int, dilation: int, dropout: float, groups: int):
        super().__init__()
        pad = dilation * (k - 1) // 2
        self.conv1 = nn.Conv1d(c, c, k, padding=pad, dilation=dilation)
        self.conv2 = nn.Conv1d(c, c, k, padding=pad, dilation=dilation)
        self.norm1 = nn.GroupNorm(groups, c)
        self.norm2 = nn.GroupNorm(groups, c)
        self.drop = nn.Dropout(dropout)
        self.act = nn.GELU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.conv1(self.act(self.norm1(x)))
        h = self.conv2(self.act(self.norm2(h)))
        return x + self.drop(h)


def _masked_pool(h: torch.Tensor, mask4: torch.Tensor, n: torch.Tensor) -> torch.Tensor:
    """(B, R, C, L) -> (B, 2C, L): masked mean and masked max over the read axis."""
    h = h * mask4
    mean = h.sum(1) / n
    big = h.masked_fill(mask4 == 0, -1e4)
    return torch.cat([mean, big.amax(1)], dim=1)


class PolishNet2(nn.Module):
    def __init__(self, cfg: PolishConfig2 | None = None):
        super().__init__()
        self.cfg = cfg = cfg or PolishConfig2()
        cr, c = cfg.read_channels, cfg.channels
        g = min(cfg.groups, cr)
        self.read_stem = nn.Conv1d(READ_FEATURES, cr, cfg.stem_kernel, padding=cfg.stem_kernel // 2)
        self.read_trunk = nn.Sequential(
            *[_Block(cr, cfg.kernel, d, cfg.dropout, g) for d in cfg.read_blocks]
        )
        self.read_mix = nn.Conv1d(3 * cr, cr, 1)
        self.read_trunk2 = nn.Sequential(
            *[_Block(cr, cfg.kernel, d, cfg.dropout, g) for d in cfg.read_blocks2]
        )
        self.stem = nn.Conv1d(4 * cr + N_FEATURES, c, cfg.stem_kernel, padding=cfg.stem_kernel // 2)
        self.trunk = nn.Sequential(
            *[_Block(c, cfg.kernel, d, cfg.dropout, cfg.groups) for d in cfg.blocks]
        )
        self.norm = nn.GroupNorm(cfg.groups, c)
        self.op_head = nn.Conv1d(c, N_OPS, 1)
        self.ins_head = nn.Conv1d(c, N_INS, 1)

    def forward(self, x_read: torch.Tensor, x_pos: torch.Tensor, mask4: torch.Tensor):
        """x_read (B, R, 10, L), x_pos (B, 17, L), mask4 (B, R, 1, 1) -> op, insert logits."""
        b, r, _, length = x_read.shape
        n = mask4.sum(1).clamp(min=1)  # (B, 1, 1)
        h = self.read_trunk(self.read_stem(x_read.reshape(b * r, READ_FEATURES, length)))
        cr = h.shape[1]
        h = h.view(b, r, cr, length)
        pooled = _masked_pool(h, mask4, n)  # (B, 2cr, L)
        h2 = torch.cat([h, pooled.unsqueeze(1).expand(b, r, 2 * cr, length)], dim=2)
        h2 = self.read_mix(h2.reshape(b * r, 3 * cr, length))
        h2 = self.read_trunk2(h2).view(b, r, cr, length)
        pooled2 = _masked_pool(h2, mask4, n)
        g = torch.cat([pooled, pooled2, x_pos], dim=1)
        out = self.trunk(self.stem(g))
        out = F.gelu(self.norm(out))
        return self.op_head(out), self.ins_head(out)


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def save_checkpoint(path, model: nn.Module, **extra) -> None:
    """Save a polisher checkpoint. `arch` marks v2 nets so v1 loaders cannot be fooled."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    arch = "polish2" if isinstance(model, PolishNet2) else "polish"
    torch.save(
        {"config": model.cfg.to_dict(), "model": model.state_dict(), "arch": arch, **extra}, tmp
    )
    tmp.replace(path)


def load_checkpoint(path, device: torch.device | str = "cpu") -> tuple[PolishNet2, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    if ckpt.get("arch") != "polish2":
        raise ValueError(f"{path} is not a polish2 checkpoint (arch={ckpt.get('arch')!r})")
    cfg = dict(ckpt["config"])
    for key in ("blocks", "read_blocks", "read_blocks2"):
        cfg[key] = tuple(cfg[key])
    model = PolishNet2(PolishConfig2(**cfg)).to(device)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model, ckpt


# ---------------------------------------------------------------- inference


@torch.no_grad()
def _forward_packed(model: PolishNet2, packed: list, batch_size: int, device):
    """Softmax outputs for a list of (cols, insb, draft, n) tuples, in input order."""
    amp = device.type == "cuda"
    out = []
    for start in range(0, len(packed), batch_size):
        chunk = packed[start : start + batch_size]
        cols = torch.from_numpy(np.stack([c[0] for c in chunk])).to(device).long()
        insb = torch.from_numpy(np.stack([c[1] for c in chunk])).to(device).long()
        draft = torch.from_numpy(np.stack([c[2] for c in chunk])).to(device).long()
        nreads = torch.tensor([c[3] for c in chunk], device=device)
        x_read, x_pos, mask4 = expand_features(cols, insb, draft, nreads)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            op_logits, ins_logits = model(x_read, x_pos, mask4)
        op_p = op_logits.float().softmax(1).cpu().numpy()
        ins_p = ins_logits.float().softmax(1).cpu().numpy()
        out.extend(zip(op_p, ins_p))
    return out


@dataclass
class _Candidate:
    cluster: int
    draft: str
    n_reads: int
    weight: int = 1
    score: float = 0.0


def _pass(model, items, reads_of, arrays_of, strand_length, th, rounds, mode, with_score,
          batch_size, device):
    cands: list[_Candidate] = []
    seen: dict[tuple[int, str], _Candidate] = {}
    for i, start in items:
        draft, _ = refine_draft(start, reads_of[i], arrays_of[i], strand_length)
        merged = seen.get((i, draft))
        if merged is not None:
            merged.weight += 1
            continue
        cand = _Candidate(i, draft, len(reads_of[i]))
        seen[(i, draft)] = cand
        cands.append(cand)
    if not cands:
        return cands
    for r in range(rounds):
        packed = [pack(c.draft, reads_of[c.cluster], arrays_of[c.cluster]) for c in cands]
        probs = _forward_packed(model, packed, batch_size, device)
        for cand, (op_p, ins_p) in zip(cands, probs):
            sub_t, ins_t = th["low"] if cand.n_reads <= th["low_max_reads"] else th["high"]
            cand.draft = apply_edits(cand.draft, op_p, ins_p, strand_length, sub_t, ins_t, mode)
    if with_score:
        packed = [pack(c.draft, reads_of[c.cluster], arrays_of[c.cluster]) for c in cands]
        for cand, (op_p, ins_p) in zip(cands, _forward_packed(model, packed, batch_size, device)):
            cand.score = float(np.log(op_p[KEEP] + _EPS).mean() + np.log(ins_p[0] + _EPS).mean())
    return cands


@torch.no_grad()
def polish2_clusters(
    model: PolishNet2,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device: torch.device | str | None = None,
    thresholds: dict | None = None,
    rounds: int = 1,
    drafts: int = 1,
    select: str = "confidence",
    mode: str = "topk",
) -> list[Strand | None]:
    """v2 decoding. rounds/drafts/select/mode mean what they mean in polish_clusters_multi."""
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
    items = [(i, s) for i, starts in starts_of.items() for s in starts]
    cands = _pass(model, items, reads_of, arrays_of, strand_length, th, rounds, mode, ranked,
                  batch_size, device)
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


class Polish2Decoder:
    """Drop-in ``dnacodec.types.Decoder`` for the v2 polisher."""

    name = "polish2"
    main_process_only = True

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
    ):
        from .net import pick_device

        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.batch_size = batch_size
        self.thresholds = thresholds or ckpt.get("thresholds") or DEFAULT_THRESHOLDS
        self.checkpoint_path = Path(checkpoint_path)
        self.rounds, self.drafts, self.select, self.mode = rounds, drafts, select, mode

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return polish2_clusters(self.model, clusters, strand_length, self.batch_size, self.device,
                                self.thresholds, self.rounds, self.drafts, self.select, self.mode)

    def close(self) -> None:  # symmetry with PolishDecoder; v2 has no worker pool
        pass
