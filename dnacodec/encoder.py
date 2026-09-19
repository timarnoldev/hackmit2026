"""Fountain-style encoder with pluggable scorer. Owner: Agent B.

Layout of each strand: seed (settings.seed_bases) + payload, total settings.strand_length.
The seed drives a pseudo-random choice of source chunks that are XORed into the payload
(LT / DNA Fountain style). Several seeds are tried per strand and the scorer keeps the
lowest-risk candidate, after hard constraints are applied.

Details (all derived from EncoderSettings, nothing else is stored):

- Every base holds 2 bits (A=0, C=1, G=2, T=3). The strand is one big integer of
  2 * strand_length bits: seed bits first, then the payload bits.
- Payload = chunk (chunk_bits) + CRC-16 (16 bits), whitened with a mask derived from the
  seed so that low-entropy files (e.g. all zeros) still give balanced strands.
  The CRC covers seed and chunk, so a strand with any error in seed or payload is
  discarded by recover() instead of poisoning the fountain decoder.
- The file is prefixed with a 4-byte CRC-32 of its content before chunking. recover()
  checks it and returns None instead of a wrong file.
- Seeds are a bijective scramble of a running counter, so every emitted strand has a
  distinct seed and the seed bases look random (not AAAA...AC).
- Degree distribution: robust soliton (c=0.1, delta=0.5 as in Erlich and Zielinski 2017).
- Recovery: belief propagation (peeling), then Gaussian elimination over GF(2) on
  whatever is left if peeling stalls.
"""

from __future__ import annotations

import binascii
import math
import re
import zlib
from bisect import bisect_left
from functools import lru_cache
from typing import Sequence

import numpy as np

from .types import EncodedFile, EncoderSettings, FileMeta, Scorer, Strand

CRC_BITS = 16
FILE_CRC_BYTES = 4
SOLITON_C = 0.1
SOLITON_DELTA = 0.5
MAX_SEED_BITS = 64
# Tries per output strand before giving up on it (constraints too strict).
MIN_TRIES_PER_STRAND = 20000
TRIES_PER_CANDIDATE = 250
SCORER_BATCH = 8192
RETRIES_ON_BAD_FILE = 8

_MASK64 = (1 << 64) - 1
_TO_DIGITS = str.maketrans("ACGT", "0123")
_HEX_TO_BASES = {f"{i:x}": "ACGT"[i >> 2] + "ACGT"[i & 3] for i in range(16)}
_VALID = re.compile(r"[ACGT]*")


# ---------------------------------------------------------------- small helpers


def _splitmix(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK64
    return x ^ (x >> 31)


def _scramble(counter: int, bits: int) -> int:
    """Bijection on [0, 2**bits): makes consecutive counters look random."""
    mask = (1 << bits) - 1
    x = counter & mask
    shift = max(1, bits // 2)
    for mult in (0x9E3779B97F4A7C15, 0xD6E8FEB86659FD93):
        x = (x * (mult | 1)) & mask  # odd multiplier: bijective mod 2**bits
        x ^= x >> shift  # xorshift right: bijective
    return x


def _mask_bits(seed: int, nbits: int) -> int:
    """Whitening mask of nbits derived from the seed."""
    out, have, state = 0, 0, seed ^ 0x5DEECE66D
    while have < nbits:
        state = _splitmix(state)
        out = (out << 64) | state
        have += 64
    return out >> (have - nbits)


@lru_cache(maxsize=32)
def _soliton_cdf(k: int) -> tuple[float, ...]:
    """CDF of the robust soliton distribution over degrees 1..k."""
    if k <= 1:
        return (1.0,)
    d = np.arange(1, k + 1, dtype=np.float64)
    rho = np.empty(k)
    rho[0] = 1.0 / k
    rho[1:] = 1.0 / (d[1:] * (d[1:] - 1.0))
    r = SOLITON_C * math.log(k / SOLITON_DELTA) * math.sqrt(k)
    tau = np.zeros(k)
    pivot = int(round(k / r)) if r > 0 else k
    pivot = min(max(pivot, 1), k)
    tau[: pivot - 1] = r / (d[: pivot - 1] * k)
    if r > 0:
        tau[pivot - 1] = r * math.log(max(r / SOLITON_DELTA, 1.0 + 1e-12)) / k
    mu = rho + np.clip(tau, 0, None)
    cdf = np.cumsum(mu / mu.sum())
    cdf[-1] = 1.0
    return tuple(cdf.tolist())


def _neighbors(seed: int, n_chunks: int) -> list[int]:
    """Source chunks XORed into the strand with this seed. Shared by encode and recover."""
    cdf = _soliton_cdf(n_chunks)
    state = _splitmix(seed ^ 0xA5A5A5A5A5A5A5A5)
    degree = min(bisect_left(cdf, state / 2.0**64) + 1, n_chunks)
    # Floyd's algorithm: exactly `degree` distinct indices, no retry loop.
    chosen: set[int] = set()
    for j in range(n_chunks - degree, n_chunks):
        state = _splitmix(state)
        t = state % (j + 1)
        chosen.add(j if t in chosen else t)
    return sorted(chosen)


class _Layout:
    """Bit layout derived from settings."""

    def __init__(self, settings: EncoderSettings):
        L, sb = settings.strand_length, settings.seed_bases
        if sb < 1 or L <= sb:
            raise ValueError("need 1 <= seed_bases < strand_length")
        self.strand_length = L
        self.seed_bits = min(2 * sb, MAX_SEED_BITS)
        self.seed_field_bits = 2 * sb
        self.payload_bits = 2 * (L - sb)
        self.chunk_bits = self.payload_bits - CRC_BITS
        if self.chunk_bits < 8:
            raise ValueError(
                f"strand_length {L} with seed_bases {sb} leaves no room for data after the "
                f"{CRC_BITS}-bit checksum"
            )
        self.chunk_bytes = (self.chunk_bits + 7) // 8
        self.total_bits = 2 * L
        self.hex_pad = (-self.total_bits) % 4  # pad so the bit string is whole hex digits
        self.seed_bytes = (self.seed_field_bits + 7) // 8

    def n_chunks(self, n_bytes: int) -> int:
        return max(1, math.ceil((n_bytes + FILE_CRC_BYTES) * 8 / self.chunk_bits))

    def crc(self, seed: int, chunk: int) -> int:
        return binascii.crc_hqx(
            seed.to_bytes(self.seed_bytes, "big") + chunk.to_bytes(self.chunk_bytes, "big"),
            0xFFFF,
        )

    def to_strand(self, seed: int, chunk: int) -> Strand:
        payload = ((chunk << CRC_BITS) | self.crc(seed, chunk)) ^ _mask_bits(seed, self.payload_bits)
        x = ((seed << self.payload_bits) | payload) << self.hex_pad
        h = format(x, f"0{(self.total_bits + self.hex_pad) // 4}x")
        s = "".join(map(_HEX_TO_BASES.__getitem__, h))
        return s[: self.strand_length] if self.hex_pad else s

    def parse(self, strand: object) -> tuple[int, int] | None:
        """(seed, chunk) if the strand is well formed and its checksum matches, else None."""
        if not isinstance(strand, str) or len(strand) != self.strand_length:
            return None
        if not _VALID.fullmatch(strand):
            return None
        x = int(strand.translate(_TO_DIGITS), 4)
        seed = x >> self.payload_bits
        if seed >> self.seed_bits:
            return None  # seed field larger than any seed we emit
        payload = (x & ((1 << self.payload_bits) - 1)) ^ _mask_bits(seed, self.payload_bits)
        chunk, crc = payload >> CRC_BITS, payload & ((1 << CRC_BITS) - 1)
        if self.crc(seed, chunk) != crc:
            return None
        return seed, chunk


def _chunks_from_bytes(blob: bytes, layout: _Layout, n_chunks: int) -> list[int]:
    bits = np.unpackbits(np.frombuffer(blob, dtype=np.uint8))
    padded = np.zeros(n_chunks * layout.chunk_bits, dtype=np.uint8)
    padded[: bits.size] = bits
    rows = np.packbits(padded.reshape(n_chunks, layout.chunk_bits), axis=1)
    shift = layout.chunk_bytes * 8 - layout.chunk_bits
    return [int.from_bytes(r.tobytes(), "big") >> shift for r in rows]


def _bytes_from_chunks(chunks: Sequence[int], layout: _Layout, n_bytes: int) -> bytes:
    shift = layout.chunk_bytes * 8 - layout.chunk_bits
    raw = b"".join((c << shift).to_bytes(layout.chunk_bytes, "big") for c in chunks)
    rows = np.unpackbits(np.frombuffer(raw, dtype=np.uint8).reshape(len(chunks), -1), axis=1)
    bits = rows[:, : layout.chunk_bits].reshape(-1)[: n_bytes * 8]
    return np.packbits(bits).tobytes()


def _passes(strand: Strand, settings: EncoderSettings, homo_re: re.Pattern | None) -> bool:
    if homo_re is not None and homo_re.search(strand):
        return False
    if settings.gc_min is not None or settings.gc_max is not None:
        gc = (strand.count("G") + strand.count("C")) / len(strand)
        if settings.gc_min is not None and gc < settings.gc_min:
            return False
        if settings.gc_max is not None and gc > settings.gc_max:
            return False
    return True


def _homopolymer_re(settings: EncoderSettings) -> re.Pattern | None:
    if settings.max_homopolymer is None:
        return None
    if settings.max_homopolymer < 1:
        raise ValueError("max_homopolymer must be >= 1 or None")
    return re.compile(r"(.)\1{%d}" % settings.max_homopolymer)


# ---------------------------------------------------------------- public API


def rule_scorer(settings: EncoderSettings) -> Scorer:
    """Default hand-written scorer: 1.0 if a strand violates the hard constraints
    (max_homopolymer, gc_min, gc_max), else 0.0. This is the one-size-fits-all baseline."""
    homo_re = _homopolymer_re(settings)

    def score(strands: Sequence[Strand]) -> np.ndarray:
        return np.array(
            [0.0 if s and _passes(s, settings, homo_re) else 1.0 for s in strands],
            dtype=np.float64,
        )

    score.is_rule_scorer = True  # type: ignore[attr-defined]  # lets encode() skip ranking
    return score


def encode(data: bytes, settings: EncoderSettings, scorer: Scorer | None = None) -> EncodedFile:
    """Encode data into strands. scorer=None means rule_scorer(settings).

    Hard constraints always apply. The scorer only ranks candidates that pass them.
    Number of strands = ceil(n_chunks * (1 + settings.redundancy)).
    """
    if settings.redundancy < 0:
        raise ValueError("redundancy must be >= 0")
    if settings.candidates_per_strand < 1:
        raise ValueError("candidates_per_strand must be >= 1")
    data = bytes(data)
    layout = _Layout(settings)
    n_bytes = len(data)
    n_chunks = layout.n_chunks(n_bytes)
    blob = zlib.crc32(data).to_bytes(FILE_CRC_BYTES, "big") + data
    chunks = _chunks_from_bytes(blob, layout, n_chunks)
    n_strands = math.ceil(n_chunks * (1 + settings.redundancy))
    meta = FileMeta(settings=settings, n_bytes=n_bytes, n_chunks=n_chunks)

    if scorer is None:
        scorer = rule_scorer(settings)
    # All passing candidates score 0 under the rule scorer, so the first one wins anyway.
    k = 1 if getattr(scorer, "is_rule_scorer", False) else settings.candidates_per_strand
    homo_re = _homopolymer_re(settings)
    max_tries = max(MIN_TRIES_PER_STRAND, TRIES_PER_CANDIDATE * k)
    seed_space = 1 << layout.seed_bits

    def next_seed() -> int:
        nonlocal counter
        if counter >= seed_space:
            raise ValueError(
                f"seed space exhausted ({seed_space} seeds): increase seed_bases or relax constraints"
            )
        seed = _scramble(counter, layout.seed_bits)
        counter += 1
        return seed

    def build(seed: int) -> Strand:
        value = 0
        for i in _neighbors(seed, n_chunks):
            value ^= chunks[i]
        return layout.to_strand(seed, value)

    def too_strict() -> ValueError:
        return ValueError(
            f"no candidate passed the hard constraints in {max_tries} tries "
            f"(max_homopolymer={settings.max_homopolymer}, gc_min={settings.gc_min}, "
            f"gc_max={settings.gc_max}); constraints are too strict"
        )

    # 1. Candidates: the first k seeds per strand that pass the hard constraints.
    groups: list[list[tuple[int, Strand]]] = []
    counter = 0
    for _ in range(n_strands):
        group: list[tuple[int, Strand]] = []
        tries = 0
        while len(group) < k and tries < max_tries:
            seed = next_seed()
            tries += 1
            strand = build(seed)
            if _passes(strand, settings, homo_re):
                group.append((seed, strand))
        if not group:
            raise too_strict()
        groups.append(group)

    # 2. Rank candidates with the scorer (batched, so a CNN scorer runs efficiently).
    if k == 1:
        order = [[0] for _ in groups]
    else:
        flat = [s for g in groups for _, s in g]
        scores = np.empty(len(flat), dtype=np.float64)
        for start in range(0, len(flat), SCORER_BATCH):
            batch = flat[start : start + SCORER_BATCH]
            out = np.asarray(scorer(batch), dtype=np.float64).reshape(-1)
            if out.shape[0] != len(batch):
                raise ValueError(f"scorer returned {out.shape[0]} scores for {len(batch)} strands")
            scores[start : start + len(batch)] = out
        scores[~np.isfinite(scores)] = np.inf
        order, pos = [], 0
        for g in groups:
            order.append(np.argsort(scores[pos : pos + len(g)], kind="stable").tolist())
            pos += len(g)
    chosen = [(g[o[0]][0], g[o[0]][1]) for g, o in zip(groups, order)]

    # 3. Guarantee the full set decodes (matters for small files and low redundancy, where a
    #    random LT system is often rank deficient). Rarely needed for large files.
    if _solve({seed: 0 for seed, _ in chosen}, n_chunks, check_only=True) is None:
        chosen = _repair_rank(chosen, groups, order, n_chunks, next_seed, build,
                              lambda s: _passes(s, settings, homo_re), max_tries, too_strict)
    return EncodedFile(strands=[s for _, s in chosen], meta=meta)


def _repair_rank(chosen, groups, order, n_chunks, next_seed, build, passes, max_tries, too_strict):
    """Walk the strands in order and swap non-innovative ones (while rank < n_chunks) for the
    next best candidate of the same strand, or for fresh seeds, until the system is full rank."""
    pivots: dict[int, int] = {}

    def innovative(seed: int) -> bool:
        mask = 0
        for c in _neighbors(seed, n_chunks):
            mask |= 1 << c
        while mask:
            low = mask & -mask
            p = pivots.get(low)
            if p is None:
                pivots[low] = mask
                return True
            mask ^= p
        return False

    chosen = list(chosen)
    for j in range(len(chosen)):
        if len(pivots) == n_chunks:
            break
        if innovative(chosen[j][0]):
            continue
        replacement = next((groups[j][o] for o in order[j][1:] if innovative(groups[j][o][0])), None)
        tries = 0
        while replacement is None:
            if tries >= max_tries:
                raise too_strict()
            seed = next_seed()
            tries += 1
            strand = build(seed)
            if passes(strand) and innovative(seed):
                replacement = (seed, strand)
        chosen[j] = replacement
    if len(pivots) < n_chunks:
        raise ValueError(
            "could not reach a decodable set of strands; increase redundancy"
        )  # only possible if n_strands < n_chunks, which redundancy >= 0 rules out
    return chosen


def recover(strands: Sequence[Strand | None], meta: FileMeta) -> bytes | None:
    """Recover the file from decoded strands (any order, None for lost ones).

    Strands that are corrupted must not crash recovery. Returns None if the file
    cannot be recovered exactly.
    """
    try:
        layout = _Layout(meta.settings)
        n = meta.n_chunks
        if n != layout.n_chunks(meta.n_bytes) or strands is None:
            return None
        # Parse, verify checksum, drop duplicates (first valid copy of a seed wins).
        eqs: dict[int, int] = {}
        for s in strands:
            parsed = layout.parse(s)
            if parsed is not None and parsed[0] not in eqs:
                eqs[parsed[0]] = parsed[1]
        if len(eqs) < n:
            return None
        solution = _solve(eqs, n)
        if solution is None:
            return None
        data = _check_file(solution, layout, meta.n_bytes)
        if data is None and len(eqs) > n + 1:
            data = _recover_from_poison(eqs, n, solution, layout, meta.n_bytes)
        return data
    except Exception:  # noqa: BLE001  garbage in must never crash recovery
        return None


def _recover_from_poison(
    eqs: dict[int, int], n: int, solution: list[int], layout: _Layout, n_bytes: int
) -> bytes | None:
    """The solution failed the file CRC-32: a wrong strand passed its CRC-16 (probability
    2**-16 per corrupted strand) and poisoned the solve. The culprit agrees with the wrong
    solution, while many good strands that touch the wrong chunks disagree with it. So first
    drop strands that agree but touch mostly-disagreeing chunks, then fall back to leaving
    out pseudo-random parts of the surplus."""
    nbrs = {seed: _neighbors(seed, n) for seed in eqs}
    inc = np.zeros(n)
    tot = np.zeros(n)
    agrees: dict[int, bool] = {}
    for seed, value in eqs.items():
        acc = 0
        for c in nbrs[seed]:
            acc ^= solution[c]
        agrees[seed] = acc == value
        tot[nbrs[seed]] += 1
        if not agrees[seed]:
            inc[nbrs[seed]] += 1
    bad_share = inc / np.maximum(tot, 1)
    suspicion = {seed: float(bad_share[nbrs[seed]].max()) if agrees[seed] else 0.0 for seed in eqs}

    subsets = []
    for threshold in (0.5, 0.3, 0.15):
        subsets.append({seed: v for seed, v in eqs.items() if suspicion[seed] < threshold})
    surplus = len(eqs) - n
    cut = int(min(0.5, 0.8 * surplus / len(eqs)) * 2**64)
    for attempt in range(1, RETRIES_ON_BAD_FILE + 1):
        subsets.append(
            {seed: v for seed, v in eqs.items() if _splitmix(seed ^ (attempt * 0x9E37)) >= cut}
        )
    for subset in subsets:
        if len(subset) < n or len(subset) == len(eqs):
            continue
        sol = _solve(subset, n)
        if sol is not None:
            data = _check_file(sol, layout, n_bytes)
            if data is not None:
                return data
    return None


# ---------------------------------------------------------------- decoding


def _check_file(solution: list[int], layout: _Layout, n_bytes: int) -> bytes | None:
    blob = _bytes_from_chunks(solution, layout, n_bytes + FILE_CRC_BYTES)
    data = blob[FILE_CRC_BYTES:]
    if zlib.crc32(data).to_bytes(FILE_CRC_BYTES, "big") != blob[:FILE_CRC_BYTES]:
        return None
    return data


def _solve(eqs: dict[int, int], n: int, check_only: bool = False) -> list[int] | None:
    """Peeling decoder with a GF(2) Gaussian elimination fallback.

    check_only: stop after deciding solvability (returns a non-None dummy if solvable)."""
    rows: list[set[int]] = []
    vals: list[int] = []
    for seed, value in eqs.items():
        rows.append(set(_neighbors(seed, n)))
        vals.append(value)
    known: list[int | None] = [None] * n
    by_chunk: list[list[int]] = [[] for _ in range(n)]
    for r, row in enumerate(rows):
        for c in row:
            by_chunk[c].append(r)

    stack = [r for r, row in enumerate(rows) if len(row) == 1]
    n_known = 0
    while stack:
        r = stack.pop()
        row = rows[r]
        if len(row) != 1:
            continue
        c = row.pop()
        if known[c] is not None:
            continue
        known[c] = vals[r]
        n_known += 1
        for r2 in by_chunk[c]:
            row2 = rows[r2]
            if c in row2:
                row2.discard(c)
                vals[r2] ^= vals[r]
                if len(row2) == 1:
                    stack.append(r2)
    if n_known == n:
        return known  # type: ignore[return-value]

    # Peeling stalled: Gaussian elimination on the residual system (bitmask rows).
    unknown = [c for c in range(n) if known[c] is None]
    col = {c: i for i, c in enumerate(unknown)}
    pivots: dict[int, tuple[int, int]] = {}  # lowest set bit -> (mask, value)
    residual = sorted((r for r, row in enumerate(rows) if row), key=lambda r: len(rows[r]))
    for r in residual:  # sparse rows first keeps fill-in low
        row = rows[r]
        mask = 0
        for c in row:
            mask |= 1 << col[c]
        val = vals[r]
        while mask:
            low = mask & -mask
            p = pivots.get(low)
            if p is None:
                pivots[low] = (mask, val)
                break
            mask ^= p[0]
            val ^= p[1]
        if len(pivots) == len(unknown):
            break
    if len(pivots) < len(unknown):
        return None
    if check_only:
        return known  # type: ignore[return-value]
    # Back substitution from the highest pivot down; each row only has bits >= its pivot.
    sol: dict[int, int] = {}
    for low in sorted(pivots, reverse=True):
        mask, val = pivots[low]
        rest = mask ^ low
        while rest:
            b = rest & -rest
            val ^= sol[b]
            rest ^= b
        sol[low] = val
    for i, c in enumerate(unknown):
        known[c] = sol[1 << i]
    return known  # type: ignore[return-value]


# ---------------------------------------------------------------- metrics helpers


def payload_bits_per_base(meta: FileMeta, n_strands: int) -> float:
    """Net density: original file bits divided by all synthesized bases."""
    return meta.n_bytes * 8 / (n_strands * meta.settings.strand_length)


def gc_fraction(strand: Strand) -> float:
    return (strand.count("G") + strand.count("C")) / max(1, len(strand))


def max_run_length(strand: Strand) -> int:
    best = run = 1 if strand else 0
    for a, b in zip(strand, strand[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best
