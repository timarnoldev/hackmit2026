"""Fountain-style encoder with pluggable scorer.

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
  whatever is left if peeling stalls. If the file CRC-32 fails because a wrong strand
  slipped past its CRC-16, the culprit is located by provenance tracking and dropped.
- Encode checks that the full strand set decodes and swaps non-innovative strands if not
  (needed for small files and redundancy near 0).
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
POISON_ROUNDS = 8

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
    # The seed sits in the first seed_bases letters. Screening it against the sequence rules
    # also filters the seed space, which biases which data chunks a droplet combines and leaves
    # some chunks covered by far fewer strands (see docs/LEARNED_RULES.md). constrain_seed=False
    # applies the rules to the payload only, so no droplet is ever rejected for its seed.
    if not settings.constrain_seed:
        strand = strand[settings.seed_bases:]
        if not strand:
            return True
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

    settings.risk_threshold (None disables): candidates the scorer rates above it are
    rejected like hard-constraint violations. Raises ValueError if the constraints are too
    strict to find any candidate within a bounded number of tries.
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
    is_rule = bool(getattr(scorer, "is_rule_scorer", False))
    k = 1 if is_rule else settings.candidates_per_strand
    # Optional field (added to EncoderSettings on main); getattr keeps older settings working.
    threshold = getattr(settings, "risk_threshold", None)
    if is_rule and threshold is not None and threshold < 0:
        raise ValueError(
            f"risk_threshold={threshold} rejects every candidate: the rule scorer gives passing "
            "strands 0.0; constraints are too strict"
        )
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
            f"no candidate passed the hard constraints"
            f"{' and risk_threshold' if threshold is not None else ''} in {max_tries} tries "
            f"(max_homopolymer={settings.max_homopolymer}, gc_min={settings.gc_min}, "
            f"gc_max={settings.gc_max}, risk_threshold={threshold}); constraints are too strict"
        )

    def score_all(strands: list[Strand]) -> np.ndarray:
        """Scorer in batches (efficient for a CNN). Non-finite risk counts as infinitely risky."""
        scores = np.empty(len(strands), dtype=np.float64)
        for start in range(0, len(strands), SCORER_BATCH):
            batch = strands[start : start + SCORER_BATCH]
            out = np.asarray(scorer(batch), dtype=np.float64).reshape(-1)
            if out.shape[0] != len(batch):
                raise ValueError(f"scorer returned {out.shape[0]} scores for {len(batch)} strands")
            scores[start : start + len(batch)] = out
        scores[~np.isfinite(scores)] = np.inf
        return scores

    def acceptable(score: float) -> bool:
        return threshold is None or score <= threshold

    # 1. Candidates: per strand, the first k seeds that pass the hard constraints and, if
    #    risk_threshold is set, score at or below it. Rounds over all strands keep scorer
    #    calls batched; the rule scorer gives 0.0 to every passing strand, so it skips scoring.
    groups: list[list[tuple[int, Strand, float]]] = [[] for _ in range(n_strands)]
    tries = [0] * n_strands
    counter = 0
    pending = list(range(n_strands))
    while pending:
        cand: list[tuple[int, int, Strand]] = []
        for j in pending:
            need = k - len(groups[j])
            while need > 0 and tries[j] < max_tries:
                seed = next_seed()
                tries[j] += 1
                strand = build(seed)
                if _passes(strand, settings, homo_re):
                    cand.append((j, seed, strand))
                    need -= 1
        if is_rule:
            scores = np.zeros(len(cand))
        else:
            scores = score_all([c[2] for c in cand])
        for (j, seed, strand), sc in zip(cand, scores.tolist()):
            if acceptable(sc):
                groups[j].append((seed, strand, sc))
        if any(not groups[j] and tries[j] >= max_tries for j in pending):
            raise too_strict()
        pending = [j for j in pending if len(groups[j]) < k and tries[j] < max_tries]

    # 2. Keep the lowest-risk candidate per strand (ties: the earliest seed).
    order = [sorted(range(len(g)), key=lambda i, g=g: g[i][2]) for g in groups]
    chosen = [(g[o[0]][0], g[o[0]][1]) for g, o in zip(groups, order)]

    # 3. Guarantee the full set decodes (matters for small files and low redundancy, where a
    #    random LT system is often rank deficient). Rarely needed for large files.
    if _solve({seed: 0 for seed, _ in chosen}, n_chunks, check_only=True) is None:
        def passes(strand: Strand) -> bool:
            if not _passes(strand, settings, homo_re):
                return False
            return is_rule or acceptable(float(score_all([strand])[0]))

        chosen = _repair_rank(
            chosen, groups, order, n_chunks, next_seed, build, passes, max_tries, too_strict
        )
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
        replacement = next(
            (groups[j][o][:2] for o in order[j][1:] if innovative(groups[j][o][0])), None
        )
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
            data = _recover_from_poison(eqs, n, layout, meta.n_bytes)
        return data
    except Exception:  # noqa: BLE001  garbage in must never crash recovery
        return None


def _recover_from_poison(
    eqs: dict[int, int], n: int, layout: _Layout, n_bytes: int
) -> bytes | None:
    """The solution failed the file CRC-32: some wrong strand passed its CRC-16 (probability
    2**-16 per corrupted strand) and poisoned the solve. Find and drop it.

    Every value gets a one-hot provenance bit appended, so the solver's XORs track which
    input strands each solved chunk came from. For strand i let P_i be the strands its
    equation check depends on. Then strand i disagrees with the solution exactly when P_i
    contains a bad strand. So every strand inside an agreeing P_i is good, and the bad ones
    are among the rest. Drop those suspects (or the most suspicious if too many) and re-solve.
    Falls back to leaving out pseudo-random parts of the surplus.
    """
    seeds = list(eqs)
    m = len(seeds)
    all_rows = (1 << m) - 1
    nbrs = [_neighbors(seed, n) for seed in seeds]
    excluded = 0  # bitmask over row indices
    for _ in range(POISON_ROUNDS):
        rows = [i for i in range(m) if not (excluded >> i) & 1]
        if len(rows) < n:
            break
        aug = {seeds[i]: (eqs[seeds[i]] << m) | (1 << i) for i in rows}
        sol = _solve(aug, n)
        if sol is None:
            break
        data = _check_file([a >> m for a in sol], layout, n_bytes)
        if data is not None:
            return data
        good, disagree = 0, []
        for i in rows:
            acc = 0
            for c in nbrs[i]:
                acc ^= sol[c]
            prov = (acc & all_rows) ^ (1 << i)
            if (acc >> m) == eqs[seeds[i]]:
                good |= prov
            else:
                disagree.append(prov)
        suspects = all_rows & ~good & ~excluded
        if not disagree or not suspects:
            break
        # One bad strand lies in every disagreeing P_i: the intersection pins it down.
        common = suspects
        for p in disagree:
            common &= p
        if common and len(rows) - common.bit_count() >= n:
            excluded |= common
            continue
        # Several bad strands: drop the suspects that sit in the most disagreeing checks.
        bits = np.unpackbits(
            np.frombuffer(
                b"".join((p & suspects).to_bytes((m + 7) // 8, "little") for p in disagree),
                dtype=np.uint8,
            ).reshape(len(disagree), -1),
            axis=1,
            bitorder="little",
        )[:, :m]
        counts = bits.sum(axis=0)
        top = np.flatnonzero(counts == counts.max())
        if counts.max() == 0:
            break
        for i in top.tolist():
            excluded |= 1 << i

    surplus = m - n
    cut = int(min(0.5, 0.8 * surplus / m) * 2**64)
    for attempt in range(1, RETRIES_ON_BAD_FILE + 1):
        subset = {
            seed: v for seed, v in eqs.items() if _splitmix(seed ^ (attempt * 0x9E37)) >= cut
        }
        sol = _solve(subset, n) if n <= len(subset) < m else None
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
