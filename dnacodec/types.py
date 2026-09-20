"""Shared types for the whole pipeline.

Owned by the architect. Agents must not change these signatures on their own.
If you need a change, stop and ask a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

import numpy as np

ALPHABET = "ACGT"

# One DNA strand over ACGT.
Strand = str

# Noisy reads that all come from the same original strand. Empty list = dropout.
Cluster = list[str]

# Scores candidate strands by risk in [0, 1], lower is safer. Returns shape (len(strands),).
Scorer = Callable[[Sequence[Strand]], np.ndarray]


@dataclass(frozen=True)
class EncoderSettings:
    """The knobs the loop is allowed to turn."""

    strand_length: int = 110  # total bases per strand, including seed and any inner parity
    seed_bases: int = 16  # bases that hold the fountain seed (acts as the strand index)
    redundancy: float = 0.3  # extra strands as a fraction of the minimum needed to recover
    candidates_per_strand: int = 8  # candidates generated per strand, the scorer keeps the safest
    max_homopolymer: int | None = 3  # hard constraint, None disables it
    gc_min: float | None = 0.4  # hard constraint on GC fraction, None disables it
    gc_max: float | None = 0.6
    risk_threshold: float | None = None  # reject candidates the scorer rates above this, None disables
    constrain_seed: bool = True  # False applies the hard rules to the payload only, not the seed bases


@dataclass(frozen=True)
class FileMeta:
    """Everything recover() needs besides the strands. Stored off-DNA, like a file header."""

    settings: EncoderSettings
    n_bytes: int  # original file size
    n_chunks: int  # number of source chunks before fountain coding


@dataclass
class EncodedFile:
    strands: list[Strand]
    meta: FileMeta


@dataclass
class Metrics:
    """One evaluation of one codec on one situation. Produced only by dnacodec.evaluate."""

    n_strands: int
    strand_accuracy: float  # fraction of strands reconstructed exactly; dropouts count as wrong
    mean_edit_distance: float  # mean edit distance to the reference over non-dropout strands
    dropout_rate: float  # fraction of strands with an empty cluster
    reads_per_strand: float  # mean cluster size over all strands
    per_position_error: list[float]  # error rate per reference position, len == strand_length
    file_recovered: bool | None = None  # None when no file was encoded (e.g. real datasets)
    bits_per_base: float | None = None  # net payload bits per synthesized base
    write_cost_usd_per_mb: float | None = None
    read_cost_usd_per_mb: float | None = None
    recovery_rate: float | None = None  # fraction of independent file trials recovered exactly
    n_trials: int | None = None  # number of file trials behind recovery_rate
    extra: dict[str, float] = field(default_factory=dict)  # anything else worth logging


class Decoder(Protocol):
    """Reconstructs each original strand from its cluster of noisy reads.

    Optional attribute `main_process_only: bool` (default False when absent). GPU decoders set it
    to True: evaluation then runs encode and simulate in worker processes, gathers the clusters of
    all trials, calls decode() once in the main process (the decoder batches internally on the GPU),
    and runs recover() in workers again. CPU decoders like the baseline are sent to the workers.
    """

    name: str

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        """Return one strand per cluster, same order. None for an empty cluster.

        Must not use anything except the reads and strand_length. The order of clusters
        matches the references only so evaluation can compare them.
        """
        ...
