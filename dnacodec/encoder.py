"""Fountain-style encoder with pluggable scorer. Owner: Agent B.

Layout of each strand: seed (settings.seed_bases) + payload, total settings.strand_length.
The seed drives a pseudo-random choice of source chunks that are XORed into the payload
(LT / DNA Fountain style). Several seeds are tried per strand and the scorer keeps the
lowest-risk candidate, after hard constraints are applied.
"""

from __future__ import annotations

from typing import Sequence

from .types import EncodedFile, EncoderSettings, FileMeta, Scorer, Strand


def rule_scorer(settings: EncoderSettings) -> Scorer:
    """Default hand-written scorer: 1.0 if a strand violates the hard constraints
    (max_homopolymer, gc_min, gc_max), else 0.0. This is the one-size-fits-all baseline."""
    raise NotImplementedError("Agent B")


def encode(data: bytes, settings: EncoderSettings, scorer: Scorer | None = None) -> EncodedFile:
    """Encode data into strands. scorer=None means rule_scorer(settings).

    Hard constraints always apply. The scorer only ranks candidates that pass them.
    Number of strands = ceil(n_chunks * (1 + settings.redundancy)).
    """
    raise NotImplementedError("Agent B")


def recover(strands: Sequence[Strand | None], meta: FileMeta) -> bytes | None:
    """Recover the file from decoded strands (any order, None for lost ones).

    Strands that are corrupted must not crash recovery. Returns None if the file
    cannot be recovered exactly.
    """
    raise NotImplementedError("Agent B")


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

