"""Channel simulator.

Turns encoded strands into clusters of noisy reads for a given situation profile.

Error model, per reference base of each read (independently):
    - deletion with probability p_del[i]: the base is not emitted
    - insertion with probability p_ins[i]: a uniform random base is emitted, then the base
    - substitution with probability p_sub[i]: one of the other three bases is emitted
    - otherwise the base is emitted unchanged
where p_x[i] = x_rate * homopolymer_factor ** (run_length(i) - 1) * ramp(i) * q, ramp goes
linearly from 1 at the first base to end_factor at the last base, and q is the read's
quality multiplier. The three probabilities are jointly capped at MAX_EVENT_PROB per base.

Realism extensions (profile defaults switch them off and reproduce the model above):
    - homopolymer_run_factors: when set, replaces homopolymer_factor. The deletion
      probability of a base in a run of length r is multiplied by factors[min(r, n) - 1];
      substitutions and insertions get no run effect. On real Nanopore reads (Microsoft
      train split) the run effect is almost purely deletions: per-base deletions rise 8x
      from runs of 1 to runs of 6, substitutions only 1.4x, insertions fall.
    - read_quality_spread: q ~ lognormal with mean 1 and this sigma, one draw per read.
    - position_rate_spread: a lognormal multiplier with mean 1 and this sigma, drawn once
      per (strand, position) and shared by all reads of that strand (sequence-context hot
      spots). This is what makes some errors systematic within a cluster, so that more
      reads don't help: real Nanopore clusters fail ~11% of the time even at full coverage.
    - malformed_read_rate: this fraction of reads is generated from a different, random
      strand of the same batch (a clustering error), then goes through the same channel.
      Needs at least two strands in the batch.
    - context_table: file (relative to profiles/, kept in profiles/context/ because every
      profiles/*.json is a profile; or an absolute path) with one multiplier per
      centered k-mer and error type, {"k": 5, "sub": [...], "ins": [...], "del": [...]}.
      Index = the k-mer read as a base-4 number (A=0, C=1, G=2, T=3, first base most
      significant). Bases closer than k//2 to a strand end get multiplier 1. On real
      Nanopore reads the centered 5-mer explains ~40% of the between-position variance
      of sub and del rates (held-apart train clusters), so this is sequence-dependent
      and learnable, unlike position_rate_spread which covers the rest.

Everything is vectorized over all reads of a batch with numpy.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import numpy as np

from .profiles import PROFILES_DIR, SituationProfile
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


def error_multipliers(
    codes: np.ndarray, lengths: np.ndarray, profile: SituationProfile
) -> tuple[np.ndarray, np.ndarray]:
    """(S, L) per-base multipliers: (for deletions, for substitutions and insertions).

    Both include the position ramp and are 0 on padding. The homopolymer effect goes into
    both with homopolymer_factor, and only into deletions with homopolymer_run_factors.
    """
    width = codes.shape[1]
    pos = np.arange(width)[None, :]
    frac = pos / np.maximum(lengths[:, None] - 1, 1)
    ramp = 1.0 + (profile.end_factor - 1.0) * frac
    ramp[pos >= lengths[:, None]] = 0.0
    runs = run_lengths(codes)
    if profile.homopolymer_run_factors:
        factors = np.asarray(profile.homopolymer_run_factors, dtype=np.float64)
        hp = factors[np.minimum(runs, len(factors)) - 1]
        return ramp * hp, ramp
    mult = ramp * profile.homopolymer_factor ** (runs - 1).astype(np.float64)
    return mult, mult


def cap_probabilities(
    p_del: np.ndarray, p_ins: np.ndarray, p_sub: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    total = p_del + p_ins + p_sub
    scale = np.where(total > MAX_EVENT_PROB, MAX_EVENT_PROB / np.maximum(total, 1e-12), 1.0)
    return p_del * scale, p_ins * scale, p_sub * scale


def kmer_ids(codes: np.ndarray, k: int) -> np.ndarray:
    """(S, L) id of the k-mer centered on each base, -1 where it doesn't fit in the strand."""
    s, width = codes.shape
    h = k // 2
    ids = np.full((s, width), -1, dtype=np.int64)
    if width < k:
        return ids
    val = np.zeros((s, width - 2 * h), dtype=np.int64)
    bad = np.zeros((s, width - 2 * h), dtype=bool)
    for j in range(k):
        window = codes[:, j : j + width - 2 * h]
        bad |= window == _PAD
        val = val * 4 + np.minimum(window, 3)
    ids[:, h : width - h] = np.where(bad, -1, val)
    return ids


@lru_cache(maxsize=16)
def _read_context_table(path: str, mtime_ns: int) -> tuple[int, np.ndarray]:
    data = json.loads(Path(path).read_text())
    k = int(data["k"])
    table = np.array([data["sub"], data["ins"], data["del"]], dtype=np.float64)
    if table.shape != (3, 4**k) or (table < 0).any():
        raise ValueError(f"context table {path}: need 3 x {4**k} non-negative multipliers")
    return k, table


def load_context_table(name: str) -> tuple[int, np.ndarray]:
    """(k, table of shape (3, 4**k) for sub, ins, del). Relative names live in profiles/."""
    path = Path(name)
    if not path.is_absolute():
        path = PROFILES_DIR / path
    return _read_context_table(str(path), path.stat().st_mtime_ns)


def context_multipliers(codes: np.ndarray, profile: SituationProfile) -> np.ndarray | None:
    """(3, S, L) multipliers for sub, ins, del from the profile's context table, or None."""
    name = profile.context_table
    if not name:
        return None
    k, table = load_context_table(name)
    ids = kmer_ids(codes, k)
    inside = ids >= 0
    safe = np.where(inside, ids, 0)
    return np.where(inside[None], table[:, safe], 1.0)


def base_probabilities(
    codes: np.ndarray, lengths: np.ndarray, profile: SituationProfile
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per strand and base for a read of average quality: (p_del, p_ins, p_sub), (S, L) each."""
    del_mult, mult = error_multipliers(codes, lengths, profile)
    p_del, p_ins, p_sub = profile.del_rate * del_mult, profile.ins_rate * mult, profile.sub_rate * mult
    ctx = context_multipliers(codes, profile)
    if ctx is not None:
        p_sub, p_ins, p_del = p_sub * ctx[0], p_ins * ctx[1], p_del * ctx[2]
    return p_del, p_ins, p_sub


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
    - optional realism fields: homopolymer_run_factors, read_quality_spread,
      position_rate_spread,
      malformed_read_rate (see the module docstring)

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
    source = np.repeat(np.arange(n), counts)  # which strand each read is generated from
    n_reads = len(source)
    if profile.malformed_read_rate > 0 and n > 1:
        bad = rng.random(n_reads) < profile.malformed_read_rate
        # a uniformly random strand other than the read's own cluster
        other = (source[bad] + rng.integers(1, n, size=int(bad.sum()))) % n
        source[bad] = other
    sigma = profile.read_quality_spread
    quality = rng.lognormal(-0.5 * sigma**2, sigma, size=n_reads) if sigma > 0 else None
    hot = profile.position_rate_spread
    if hot > 0:
        spots = rng.lognormal(-0.5 * hot**2, hot, size=p_del.shape)
        p_del, p_ins, p_sub = p_del * spots, p_ins * spots, p_sub * spots

    reads: list[str] = []
    for start in range(0, n_reads, READ_CHUNK):
        idx = source[start : start + READ_CHUNK]
        pd, pi, ps = p_del[idx], p_ins[idx], p_sub[idx]
        if quality is not None:
            q = quality[start : start + READ_CHUNK, None]
            pd, pi, ps = pd * q, pi * q, ps * q
        pd, pi, ps = cap_probabilities(pd, pi, ps)
        reads.extend(_mutate(rng, codes[idx], pd, pi, ps))

    clusters: list[Cluster] = []
    pos = 0
    for c in counts.tolist():
        clusters.append(reads[pos : pos + c])
        pos += c
    return clusters
