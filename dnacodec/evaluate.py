"""Evaluation harness. Owner: Agent C, reviewed by the ML verifier.

The only place Metrics are computed. Everything reported comes from here.
"""

from __future__ import annotations

from typing import Sequence

from .profiles import SituationProfile
from .types import Cluster, FileMeta, Metrics, Strand


def evaluate(
    references: Sequence[Strand],
    decoded: Sequence[Strand | None],
    clusters: Sequence[Cluster],
    profile: SituationProfile | None = None,
    meta: FileMeta | None = None,
    file_recovered: bool | None = None,
) -> Metrics:
    """Compare decoded strands to references.

    - strand_accuracy: exact matches / all strands (None and dropouts count as wrong)
    - mean_edit_distance: over strands with a non-None decode (use rapidfuzz Levenshtein)
    - per_position_error: from edit operations, attributed to reference positions
    - bits_per_base and costs: only when meta and profile are given.
      read cost uses the actual mean reads per strand, write cost uses strand_length
    """
    raise NotImplementedError("Agent C")
