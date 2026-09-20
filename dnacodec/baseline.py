"""Classic baseline decoder.

Every reported result shows this baseline next to our models.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from .types import ALPHABET, Cluster, Strand

MAX_READS = 16  # larger clusters are subsampled to this many reads
_DEL = 4  # column index for "this draft base is missing in the read"
_BASE_INDEX = {b: i for i, b in enumerate(ALPHABET)}
_LOOKUP = np.full(256, 0, dtype=np.int64)  # non-ACGT characters (e.g. N) vote as A
for _b, _i in _BASE_INDEX.items():
    _LOOKUP[ord(_b)] = _i


def _to_array(read: str) -> np.ndarray:
    return _LOOKUP[np.frombuffer(read.encode("ascii", "replace"), dtype=np.uint8)]


def subsample(reads: Cluster, k: int = MAX_READS) -> Cluster:
    """Deterministic, order-spread subsample: k evenly spaced reads, no randomness."""
    if len(reads) <= k:
        return list(reads)
    idx = np.linspace(0, len(reads) - 1, k).round().astype(int)
    return [reads[i] for i in idx]


def _pick_draft(reads: Cluster, strand_length: int) -> str:
    """Medoid by edit distance; ties go to the read whose length is closest to strand_length."""
    if len(reads) <= 2:
        return min(reads, key=lambda r: abs(len(r) - strand_length))
    dist = process.cdist(reads, reads, scorer=Levenshtein.distance, dtype=np.int32)
    total = dist.sum(axis=1)
    best = min(range(len(reads)), key=lambda i: (total[i], abs(len(reads[i]) - strand_length)))
    return reads[best]


def _votes(draft: str, reads: Cluster, arrays: list[np.ndarray]):
    """Align every read to the draft and count what each read says at each draft position.

    base_votes[i, b]: reads that have base b (0..3) at draft position i, or _DEL if they miss it.
    ins_votes[g]: reads with at least one extra base in gap g (in front of draft position g,
    g == len(draft) is after the last base). ins_base[g, b]: first inserted base in that gap.
    """
    n_pos = len(draft)
    columns = np.full((len(reads), n_pos), _DEL, dtype=np.int64)  # what each read says per position
    ins_votes = np.zeros(n_pos + 1, dtype=np.int64)
    ins_base = np.zeros((n_pos + 1, 4), dtype=np.int64)
    for r, (read, arr) in enumerate(zip(reads, arrays)):
        col = columns[r]
        for tag, i1, i2, j1, j2 in Levenshtein.opcodes(draft, read):
            if tag == "equal" or tag == "replace":
                m = min(i2 - i1, j2 - j1)
                col[i1 : i1 + m] = arr[j1 : j1 + m]
                # defensive: unequal replace blocks (rapidfuzz emits equal lengths);
                # extra draft positions stay _DEL, extra read bases count as an insertion
                if j2 - j1 > m:
                    ins_votes[i2] += 1
                    ins_base[i2, arr[j1 + m]] += 1
            elif tag == "insert":
                ins_votes[i1] += 1
                ins_base[i1, arr[j1]] += 1
            # "delete": the draft positions stay _DEL
    flat = (np.arange(n_pos, dtype=np.int64) * 5)[None, :] + columns
    base_votes = np.bincount(flat.ravel(), minlength=n_pos * 5).reshape(n_pos, 5)
    return base_votes, ins_votes, ins_base


def _rebuild(draft: str, base_votes, ins_votes, ins_base, n_reads: int) -> str:
    """Plurality per draft position (a base or a deletion) plus majority-voted insertions.
    Ties keep the draft: a draft base beats an equally voted other base or deletion,
    and an insertion needs a strict majority of reads."""
    n_pos = len(draft)
    rows = np.arange(n_pos)
    current = _to_array(draft)
    best = np.argmax(base_votes[:, :4], axis=1)
    best = np.where(base_votes[rows, best] == base_votes[rows, current], current, best)
    keep = base_votes[:, _DEL] <= base_votes[rows, best]
    inserted = ins_votes * 2 > n_reads
    ins_choice = np.argmax(ins_base, axis=1)
    if keep.all() and not inserted.any():
        return "".join(ALPHABET[b] for b in best.tolist())
    out: list[str] = []
    keep_l, best_l, ins_l, choice_l = keep.tolist(), best.tolist(), inserted.tolist(), ins_choice.tolist()
    for g in range(n_pos + 1):
        if ins_l[g]:
            out.append(ALPHABET[choice_l[g]])
        if g < n_pos and keep_l[g]:
            out.append(ALPHABET[best_l[g]])
    return "".join(out)


def _fix_length(draft: str, base_votes, ins_votes, ins_base, strand_length: int) -> str:
    """Force the draft to strand_length using the votes of the last alignment:
    drop the positions with the most deletion votes, or add bases at the gaps with the
    most insertion votes (padding at the end when no read suggests an insertion)."""
    if len(draft) > strand_length:
        n_drop = len(draft) - strand_length
        # most deletion votes first, then least support for the kept base
        order = sorted(
            range(len(draft)),
            key=lambda i: (-base_votes[i, _DEL], base_votes[i, :4].max(), -i),
        )
        drop = set(order[:n_drop])
        return "".join(b for i, b in enumerate(draft) if i not in drop)
    if len(draft) < strand_length:
        n_add = strand_length - len(draft)
        gaps = sorted(range(len(draft) + 1), key=lambda g: (-ins_votes[g], -g))
        chosen = gaps[:n_add]
        extra = {g: ALPHABET[int(np.argmax(ins_base[g]))] for g in chosen}
        out: list[str] = []
        for g in range(len(draft) + 1):
            if g in extra:
                out.append(extra[g])
            if g < len(draft):
                out.append(draft[g])
        result = "".join(out)
        # n_add can exceed the number of gaps for very short drafts
        return (result + "A" * strand_length)[:strand_length]
    return draft


def reconstruct(reads: Cluster, strand_length: int, iterations: int = 3) -> Strand:
    """Consensus of one non-empty cluster, exactly strand_length bases long."""
    reads = subsample([r for r in reads], MAX_READS)
    arrays = [_to_array(r) for r in reads]
    draft = _pick_draft(reads, strand_length)
    votes = _votes(draft, reads, arrays)
    for _ in range(iterations):
        new = _rebuild(draft, *votes, n_reads=len(reads))
        if new == draft:
            break
        draft = new
        votes = _votes(draft, reads, arrays)
    return _fix_length(draft, *votes, strand_length=strand_length)


class MajorityVoteDecoder:
    """Align reads to a draft (e.g. the read with smallest total edit distance to the others),
    then majority vote per position, iterating a few times. Handles insertions and deletions
    through the alignment, not just substitutions."""

    name = "baseline"

    def __init__(self, iterations: int = 3) -> None:
        self.iterations = iterations

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return [
            reconstruct(c, strand_length, self.iterations) if len(c) > 0 else None
            for c in clusters
        ]
