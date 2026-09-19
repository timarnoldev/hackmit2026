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


def apply_edits(draft: str, op_prob: np.ndarray, ins_prob: np.ndarray, strand_length: int) -> str:
    """Edit the draft with the predicted ops. op_prob (N_OPS, L), ins_prob (N_INS, L).

    Substitutions are free. Deletions and insertions are paired: both draft and truth have
    strand_length bases, so a correct script has equally many, and keeping the k most
    confident of each makes the result exactly strand_length without any padding.
    """
    length = len(draft)
    op_choice = op_prob.argmax(0)
    base_choice = op_prob[1:5].argmax(0)  # best substitution if we substitute
    del_score = op_prob[DELETE]
    ins_choice = ins_prob.argmax(0)
    ins_score = 1.0 - ins_prob[0]

    del_pos = np.flatnonzero(op_choice == DELETE)
    ins_pos = np.flatnonzero(ins_choice > 0)
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
    out_codes = np.where(op_choice == KEEP, draft_codes, base_choice)
    out_codes = np.where(dropped, draft_codes, out_codes)  # value unused where dropped

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


# ---------------------------------------------------------------- decoder


@torch.no_grad()
def polish_clusters(
    model: PolishNet,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device: torch.device | str | None = None,
) -> list[Strand | None]:
    device = torch.device(device) if device is not None else next(model.parameters()).device
    out: list[Strand | None] = [None] * len(clusters)
    drafts: dict[int, str] = {}
    feats: dict[int, np.ndarray] = {}
    for i, cluster in enumerate(clusters):
        if not any(cluster):
            continue
        draft, votes = draft_of(cluster, strand_length)
        drafts[i] = draft
        feats[i] = features_of(draft, votes)
    idx = list(drafts)
    amp = device.type == "cuda"
    for start in range(0, len(idx), batch_size):
        chunk = idx[start : start + batch_size]
        x = torch.from_numpy(np.stack([feats[i] for i in chunk])).to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            op_logits, ins_logits = model(x)
        op_p = op_logits.float().softmax(1).cpu().numpy()
        ins_p = ins_logits.float().softmax(1).cpu().numpy()
        for j, i in enumerate(chunk):
            out[i] = apply_edits(drafts[i], op_p[j], ins_p[j], strand_length)
    return out


class PolishDecoder:
    """Majority vote draft plus a learned correction. Implements dnacodec.types.Decoder."""

    name = "polish"
    main_process_only = True  # the CNN runs on one GPU; feature building is CPU work

    def __init__(self, checkpoint_path: str | Path, device: str | None = None, batch_size: int = 512):
        from .net import pick_device

        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.batch_size = batch_size
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics", "source")}

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return polish_clusters(self.model, clusters, strand_length, self.batch_size, self.device)
