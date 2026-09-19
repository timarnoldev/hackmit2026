"""Channel simulator. Owner: Agent A.

Turns encoded strands into clusters of noisy reads for a given situation profile.
"""

from __future__ import annotations

from typing import Sequence

from .profiles import SituationProfile
from .types import Cluster, Strand


def simulate(strands: Sequence[Strand], profile: SituationProfile, seed: int) -> list[Cluster]:
    """Return one cluster per input strand, same order. Empty cluster = dropout.

    Must model, driven only by the profile:
    - dropouts: profile.total_dropout, plus gc_dropout_factor per 0.1 GC deviation from 0.5
    - coverage: negative binomial with coverage_mean and coverage_dispersion
    - per read: substitutions, insertions, deletions at the profile rates, scaled up in
      homopolymer runs (homopolymer_factor) and toward the strand end (end_factor)

    Deterministic for a given seed. Uses numpy.random.default_rng(seed).
    """
    raise NotImplementedError("Agent A")
