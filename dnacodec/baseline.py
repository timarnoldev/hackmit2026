"""Classic baseline decoder. Owner: Agent C.

Every reported result shows this baseline next to our models.
"""

from __future__ import annotations

from typing import Sequence

from .types import Cluster, Strand


class MajorityVoteDecoder:
    """Align reads to a draft (e.g. the read with smallest total edit distance to the others),
    then majority vote per position, iterating a few times. Handles insertions and deletions
    through the alignment, not just substitutions."""

    name = "baseline"

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        raise NotImplementedError("Agent C")
