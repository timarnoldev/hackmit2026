"""Channel simulator. Owner: Agent A.

Turns encoded strands into clusters of noisy reads for a given situation profile.

Error model, per reference base of each read (independently):
    - deletion with probability p_del[i]: the base is not emitted
    - insertion with probability p_ins[i]: a uniform random base is emitted, then the base
    - substitution with probability p_sub[i]: one of the other three bases is emitted
    - otherwise the base is emitted unchanged
where p_x[i] = x_rate * homopolymer_factor ** (run_length(i) - 1) * ramp(i), and ramp goes
linearly from 1 at the first base to end_factor at the last base. The three probabilities
are jointly capped at MAX_EVENT_PROB per base.

Everything is vectorized over all reads of a batch with numpy.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .profiles import SituationProfile
from .types import Cluster, Strand

MAX_EVENT_PROB = 0.5  # cap on p_sub + p_ins + p_del at a single base
MAX_DROPOUT = 0.99
READ_CHUNK = 16_384  # reads processed per vectorized block, bounds memory

_ASCII = np.frombuffer(b"ACGT", dtype=np.uint8)
_CODE = np.full(256, 255, dtype=np.uint8)
_CODE[_ASCII] = np.arange(4, dtype=np.uint8)
_PAD = 255


def _encode(strands: Sequence[Strand]) -> tuple[np.ndarray, np.ndarray]:
    """Strands to a padded (S, L) array of base codes 0..3 (255 = padding) and lengths."""
    lengths = np.fromiter((len(s) for s in strands), dtype=np.int64, count=len(strands))
    width = int(lengths.max()) if len(strands) else 0
    codes = np.full((len(strands), max(width, 1)), _PAD, dtype=np.uint8)
    if width:
        flat = _CODE[np.frombuffer("".join(strands).encode("ascii"), dtype=np.uint8)]
        if (flat == _PAD).any():
            raise ValueError("strands must only contain A, C, G, T")
        mask = np.arange(codes.shape[1])[None, :] < lengths[:, None]
        codes[mask] = flat
    return codes, lengths


def run_lengths(codes: np.ndarray) -> np.ndarray:
    """Length of the homopolymer run each base belongs to, same shape as codes."""
    s, w = codes.shape
    flat = codes.reshape(-1)
    starts = np.ones(flat.shape, dtype=bool)
    starts[1:] = flat[1:] != flat[:-1]
    starts[:: max(w, 1)] = True  # every row starts a new run
    run_id = np.cumsum(starts) - 1
    counts = np.bincount(run_id)
    return counts[run_id].reshape(s, w)


def gc_content(codes: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    gc = ((codes == 1) | (codes == 2)).sum(axis=1)
    return gc / np.maximum(lengths, 1)


def error_multipliers(codes: np.ndarray, lengths: np.ndarray, profile: SituationProfile) -> np.ndarray:
    """(S, L) per-base error multiplier from homopolymer runs and the position ramp."""
    width = codes.shape[1]
    pos = np.arange(width)[None, :]
    frac = pos / np.maximum(lengths[:, None] - 1, 1)
    ramp = 1.0 + (profile.end_factor - 1.0) * frac
    hp = profile.homopolymer_factor ** (run_lengths(codes) - 1).astype(np.float64)
    mult = ramp * hp
    mult[pos >= lengths[:, None]] = 0.0
    return mult


def base_probabilities(
    codes: np.ndarray, lengths: np.ndarray, profile: SituationProfile
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per strand and base: (p_del, p_ins, p_sub), each (S, L), jointly capped."""
    mult = error_multipliers(codes, lengths, profile)
    p_del = profile.del_rate * mult
    p_ins = profile.ins_rate * mult
    p_sub = profile.sub_rate * mult
    total = p_del + p_ins + p_sub
    scale = np.where(total > MAX_EVENT_PROB, MAX_EVENT_PROB / np.maximum(total, 1e-12), 1.0)
    return p_del * scale, p_ins * scale, p_sub * scale


def dropout_probabilities(codes: np.ndarray, lengths: np.ndarray, profile: SituationProfile) -> np.ndarray:
    deviation = np.abs(gc_content(codes, lengths) - 0.5)
    p = profile.total_dropout + profile.gc_dropout_factor * deviation / 0.1
    return np.clip(p, 0.0, MAX_DROPOUT)


def sample_coverage(rng: np.random.Generator, mean: float, dispersion: float, size: int) -> np.ndarray:
    """Negative binomial read counts with the given mean and shape (dispersion)."""
    return rng.negative_binomial(dispersion, dispersion / (dispersion + mean), size=size)


def _mutate(
    rng: np.random.Generator,
    ref: np.ndarray,
    p_del: np.ndarray,
    p_ins: np.ndarray,
    p_sub: np.ndarray,
) -> list[str]:
    """Apply the channel to R reads. ref and p_* are (R, L), ref padded with _PAD."""
    r, w = ref.shape
    u = rng.random((r, w))
    is_del = u < p_del
    c1 = p_del + p_ins
    is_ins = (u >= p_del) & (u < c1)
    is_sub = (u >= c1) & (u < c1 + p_sub)
    valid = ref != _PAD

    base = ref.copy()
    shift = rng.integers(1, 4, size=(r, w), dtype=np.uint8)
    base[is_sub] = (base[is_sub] + shift[is_sub]) % 4
    inserted = rng.integers(0, 4, size=(r, w), dtype=np.uint8)

    # Two output slots per reference base: [inserted base, the base itself].
    out = np.empty((r, w, 2), dtype=np.uint8)
    out[:, :, 0] = _ASCII[inserted]
    out[:, :, 1] = _ASCII[np.minimum(base, 3)]
    keep = np.empty((r, w, 2), dtype=bool)
    keep[:, :, 0] = is_ins & valid
    keep[:, :, 1] = valid & ~is_del

    text = out[keep].tobytes().decode("ascii")
    ends = np.cumsum(keep.reshape(r, -1).sum(axis=1))
    starts = np.concatenate(([0], ends[:-1]))
    return [text[a:b] for a, b in zip(starts.tolist(), ends.tolist())]


def simulate(strands: Sequence[Strand], profile: SituationProfile, seed: int) -> list[Cluster]:
    """Return one cluster per input strand, same order. Empty cluster = dropout.

    Must model, driven only by the profile:
    - dropouts: profile.total_dropout, plus gc_dropout_factor per 0.1 GC deviation from 0.5
    - coverage: negative binomial with coverage_mean and coverage_dispersion
    - per read: substitutions, insertions, deletions at the profile rates, scaled up in
      homopolymer runs (homopolymer_factor) and toward the strand end (end_factor)

    Deterministic for a given seed. Uses numpy.random.default_rng(seed).
    """
    n = len(strands)
    if n == 0:
        return []
    rng = np.random.default_rng(seed)
    codes, lengths = _encode(strands)

    survive = rng.random(n) >= dropout_probabilities(codes, lengths, profile)
    counts = sample_coverage(rng, profile.coverage_mean, profile.coverage_dispersion, n)
    counts = np.where(survive, counts, 0)

    p_del, p_ins, p_sub = base_probabilities(codes, lengths, profile)
    owner = np.repeat(np.arange(n), counts)
    reads: list[str] = []
    for start in range(0, len(owner), READ_CHUNK):
        idx = owner[start : start + READ_CHUNK]
        reads.extend(_mutate(rng, codes[idx], p_del[idx], p_ins[idx], p_sub[idx]))

    clusters: list[Cluster] = []
    pos = 0
    for c in counts.tolist():
        clusters.append(reads[pos : pos + c])
        pos += c
    return clusters
