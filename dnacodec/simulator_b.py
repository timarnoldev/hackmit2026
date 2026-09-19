"""Simulator B: a deliberately different channel for the sim-to-real firewall. Owner: Agent A.

Used ONLY to test whether a gain found against Simulator A (dnacodec.simulator) survives a
differently built channel. Never optimize, tune or train against it.

It reads the same profile but builds the channel from different mechanisms:

| Effect | Simulator A | Simulator B |
|---|---|---|
| Rates | profile rates | sub x1.3, ins x0.75, del x1.2 (fixed, within +-30%) |
| Position | linear ramp 1 -> end_factor | cubic rise toward the end plus a bump at the start, same mean |
| Homopolymers | per-base deletion multiplier in runs | one run-shortening event per run and read (at most one base lost), plus run-extension events |
| Shared (systematic) errors | i.i.d. lognormal per strand position | damaged segments: a few contiguous stretches per strand with a high multiplier, same second moment |
| Read quality | lognormal per read | gamma per read with the same variance |
| Errors within a read | independent per base | bursty: a two-state Markov chain along the read (bad state x6) |
| Substitutions | uniform over the other 3 bases | transitions (A<->G, C<->T) twice as likely as each transversion |
| Insertions | uniform random base | half duplicate the base, half random |
| Malformed reads | whole read from another strand | chimera: prefix of the own strand, suffix of another strand |
| Sequence context | 5-mer table fit on Microsoft train | 5-mer table fit on a different dataset (`<table>_b.json`, DNAformer Nanopore train for nanopore_budget); if missing, A's table with its log multipliers halved |

Dropout and coverage use the same model as A (they are the situation, not the channel).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from .profiles import PROFILES_DIR, SituationProfile
from .simulator import (
    MAX_EVENT_PROB,
    READ_CHUNK,
    _ASCII,
    _PAD,
    _encode,
    dropout_probabilities,
    kmer_ids,
    load_context_table,
    run_lengths,
    sample_coverage,
)
from .types import Cluster, Strand

SUB_SCALE, INS_SCALE, DEL_SCALE = 1.3, 0.75, 1.2
BURST_ENTER, BURST_EXIT, BURST_FACTOR = 0.01, 0.25, 6.0  # mean burst length 4 bases
SEGMENT_MEAN_LENGTH = 5.0
MAX_RUN_EVENT_PROB = 0.9
_TRANSITION = np.array([2, 3, 0, 1], dtype=np.uint8)  # A<->G, C<->T


def position_curve(width: int, lengths: np.ndarray, end_factor: float) -> np.ndarray:
    """(S, L) non-linear position multiplier with the same mean as A's linear ramp."""
    pos = np.arange(width)[None, :]
    x = pos / np.maximum(lengths[:, None] - 1, 1)
    valid = pos < lengths[:, None]
    shape = 1.0 + 0.5 * np.exp(-x / 0.05) + 4.0 * max(end_factor - 1.0, 0.0) * x**3
    shape = np.where(valid, shape, 0.0)
    target = (1.0 + end_factor) / 2.0  # mean of the linear ramp
    mean = shape.sum(axis=1, keepdims=True) / np.maximum(lengths[:, None], 1)
    return shape * target / np.maximum(mean, 1e-12)


def _segment_params(sigma: float) -> tuple[float, float, float]:
    """(fraction covered, multiplier inside, multiplier outside) with mean 1 and
    second moment exp(sigma^2), the same as a mean-1 lognormal with this sigma."""
    target = float(np.exp(sigma**2))
    inside = max(8.0, 2.0 * target)
    lo, hi = 0.0, 1.0 / inside
    for _ in range(60):  # E[m^2] increases in f on [0, 1/inside)
        f = 0.5 * (lo + hi)
        outside = (1.0 - f * inside) / (1.0 - f)
        second = f * inside**2 + (1.0 - f) * outside**2
        lo, hi = (f, hi) if second < target else (lo, f)
    f = 0.5 * (lo + hi)
    return f, inside, (1.0 - f * inside) / (1.0 - f)


def shared_segments(rng: np.random.Generator, shape: tuple[int, int], sigma: float) -> np.ndarray:
    """(S, L) per strand multiplier: contiguous damaged stretches, shared by all reads."""
    if sigma <= 0:
        return np.ones(shape)
    f, inside, outside = _segment_params(sigma)
    s, w = shape
    start_p = f / SEGMENT_MEAN_LENGTH
    starts = rng.random(shape) < start_p
    lengths = rng.geometric(1.0 / SEGMENT_MEAN_LENGTH, size=shape)
    remaining = np.zeros(s, dtype=np.int64)
    damaged = np.zeros(shape, dtype=bool)
    for i in range(w):
        remaining = np.where(starts[:, i], np.maximum(remaining, lengths[:, i]), remaining)
        damaged[:, i] = remaining > 0
        remaining = np.maximum(remaining - 1, 0)
    return np.where(damaged, inside, outside)


def burst_multipliers(rng: np.random.Generator, shape: tuple[int, int]) -> np.ndarray:
    """(R, L) per read multiplier from a two-state Markov chain along the read, mean 1."""
    r, w = shape
    stationary = BURST_ENTER / (BURST_ENTER + BURST_EXIT)
    good = (1.0 - stationary * BURST_FACTOR) / (1.0 - stationary)
    u = rng.random(shape)
    bad = np.zeros(shape, dtype=bool)
    state = rng.random(r) < stationary
    for i in range(w):
        bad[:, i] = state
        state = np.where(state, u[:, i] >= BURST_EXIT, u[:, i] < BURST_ENTER)
    return np.where(bad, BURST_FACTOR, good)


def run_event_probabilities(codes: np.ndarray, profile: SituationProfile) -> tuple[np.ndarray, np.ndarray]:
    """(S, L) probability that the run ending at this base loses / gains one base per read.

    A spreads extra deletions over the bases of a run (it can lose several bases). B instead
    shortens the whole run by exactly one base, with the probability that A loses at least
    one extra base there, perturbed by DEL_SCALE. A smaller extension event (a quarter of
    it) models over-called runs, which A doesn't have.
    """
    runs = run_lengths(codes)
    flat = codes.reshape(-1)
    is_end = np.ones(flat.shape, dtype=bool)
    is_end[:-1] = flat[:-1] != flat[1:]
    is_end = is_end.reshape(codes.shape)
    is_end[:, -1] = True
    if profile.homopolymer_run_factors:
        factors = np.asarray(profile.homopolymer_run_factors, dtype=np.float64)
        mult = factors[np.minimum(runs, len(factors)) - 1]
    else:
        mult = profile.homopolymer_factor ** (runs - 1).astype(np.float64)
    extra = runs * profile.del_rate * np.maximum(mult - 1.0, 0.0)
    p_any = 1.0 - np.exp(-extra)  # chance that A loses at least one extra base in the run
    valid = (codes != _PAD) & is_end & (runs >= 2)
    shorten = np.where(valid, np.minimum(DEL_SCALE * p_any, MAX_RUN_EVENT_PROB), 0.0)
    extend = np.where(valid, np.minimum(0.25 * DEL_SCALE * p_any, MAX_RUN_EVENT_PROB), 0.0)
    return shorten, extend


def context_multipliers_b(codes: np.ndarray, profile: SituationProfile) -> np.ndarray | None:
    """(3, S, L) sub/ins/del multipliers from B's context table, or None without a table."""
    name = profile.context_table
    if not name:
        return None
    path = Path(name)
    alt = path.with_name(f"{path.stem}_b{path.suffix}")
    if (alt if alt.is_absolute() else PROFILES_DIR / alt).exists():
        k, table = load_context_table(str(alt))
    else:
        k, table = load_context_table(name)
        table = np.sqrt(table)  # same ranking, half the log amplitude
        table = table / table.mean(axis=1, keepdims=True)
    ids = kmer_ids(codes, k)
    inside = ids >= 0
    return np.where(inside[None], table[:, np.where(inside, ids, 0)], 1.0)


def _mutate_b(
    rng: np.random.Generator,
    ref: np.ndarray,
    p_del: np.ndarray,
    p_ins: np.ndarray,
    p_sub: np.ndarray,
    shorten: np.ndarray,
    extend: np.ndarray,
) -> list[str]:
    r, w = ref.shape
    valid = ref != _PAD
    u = rng.random((r, w))
    is_del = u < p_del
    c1 = p_del + p_ins
    is_ins = (u >= p_del) & (u < c1)
    is_sub = (u >= c1) & (u < c1 + p_sub)
    is_del |= rng.random((r, w)) < shorten  # run shortened by one base
    dup_run = rng.random((r, w)) < extend  # run extended by one base

    base = ref.copy()
    clean = np.minimum(base, 3)
    transition = _TRANSITION[clean]
    # transversions of A/G are C/T and vice versa, each half as likely as the transition
    transversion = (1 - clean % 2) + 2 * rng.integers(0, 2, size=(r, w), dtype=np.uint8)
    new = np.where(rng.random((r, w)) < 0.5, transition, transversion).astype(np.uint8)
    base[is_sub] = new[is_sub]
    inserted = np.where(rng.random((r, w)) < 0.5, np.minimum(ref, 3), rng.integers(0, 4, size=(r, w)))

    # Three output slots per reference base: [inserted, base, run extension].
    out = np.empty((r, w, 3), dtype=np.uint8)
    out[:, :, 0] = _ASCII[inserted.astype(np.uint8)]
    out[:, :, 1] = _ASCII[np.minimum(base, 3)]
    out[:, :, 2] = _ASCII[np.minimum(ref, 3)]
    keep = np.empty((r, w, 3), dtype=bool)
    keep[:, :, 0] = is_ins & valid
    keep[:, :, 1] = valid & ~is_del
    keep[:, :, 2] = dup_run & valid
    text = out[keep].tobytes().decode("ascii")
    ends = np.cumsum(keep.reshape(r, -1).sum(axis=1))
    starts = np.concatenate(([0], ends[:-1]))
    return [text[a:b] for a, b in zip(starts.tolist(), ends.tolist())]


def simulate(strands: Sequence[Strand], profile: SituationProfile, seed: int) -> list[Cluster]:
    """Same contract as dnacodec.simulator.simulate, different channel (see module doc).

    Only for the firewall test. Deterministic for a given seed.
    """
    n = len(strands)
    if n == 0:
        return []
    rng = np.random.default_rng(seed)
    codes, lengths = _encode(strands)

    survive = rng.random(n) >= dropout_probabilities(codes, lengths, profile)
    counts = sample_coverage(rng, profile.coverage_mean, profile.coverage_dispersion, n)
    counts = np.where(survive, counts, 0)

    shared = position_curve(codes.shape[1], lengths, profile.end_factor)
    shared = shared * shared_segments(rng, codes.shape, profile.position_rate_spread)
    p_del = DEL_SCALE * profile.del_rate * shared
    p_ins = INS_SCALE * profile.ins_rate * shared
    p_sub = SUB_SCALE * profile.sub_rate * shared
    ctx = context_multipliers_b(codes, profile)
    if ctx is not None:
        p_sub, p_ins, p_del = p_sub * ctx[0], p_ins * ctx[1], p_del * ctx[2]
    shorten, extend = run_event_probabilities(codes, profile)

    source = np.repeat(np.arange(n), counts)
    n_reads = len(source)
    chimera = np.zeros(n_reads, dtype=bool)
    partner = source.copy()
    if profile.malformed_read_rate > 0 and n > 1:
        chimera = rng.random(n_reads) < profile.malformed_read_rate
        partner[chimera] = (source[chimera] + rng.integers(1, n, size=int(chimera.sum()))) % n
    splice = rng.integers(0, codes.shape[1], size=n_reads)
    sigma = profile.read_quality_spread
    if sigma > 0:
        var = float(np.exp(sigma**2) - 1.0)
        quality = rng.gamma(1.0 / var, var, size=n_reads)
    else:
        quality = np.ones(n_reads)

    reads: list[str] = []
    cols = np.arange(codes.shape[1])[None, :]
    for start in range(0, n_reads, READ_CHUNK):
        sl = slice(start, start + READ_CHUNK)
        own, other = source[sl], partner[sl]
        use_other = chimera[sl, None] & (cols >= splice[sl, None])
        idx = np.where(use_other, other[:, None], own[:, None])
        ref = codes[idx, cols]  # padding follows whichever strand a column comes from
        m = quality[sl, None] * burst_multipliers(rng, ref.shape)
        pd, pi, ps = p_del[idx, cols] * m, p_ins[idx, cols] * m, p_sub[idx, cols] * m
        total = pd + pi + ps
        scale = np.where(total > MAX_EVENT_PROB, MAX_EVENT_PROB / np.maximum(total, 1e-12), 1.0)
        reads.extend(
            _mutate_b(rng, ref, pd * scale, pi * scale, ps * scale, shorten[idx, cols], extend[idx, cols])
        )

    clusters: list[Cluster] = []
    pos = 0
    for c in counts.tolist():
        clusters.append(reads[pos : pos + c])
        pos += c
    return clusters
