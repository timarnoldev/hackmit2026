"""Risk model. Owner: Agent F.

Learns which strands the decoder fails on in a given situation, and scores candidates
for the encoder. Fits the Scorer type in dnacodec.types.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from .types import Strand


class RiskModel:
    """Small CNN (or k-mer logistic regression as a fallback) over one-hot strands."""

    def fit(self, strands: Sequence[Strand], failed: np.ndarray) -> RiskModel:
        """failed[i] = 1.0 if the decoder did not reconstruct strands[i] exactly."""
        raise NotImplementedError("Agent F")

    def __call__(self, strands: Sequence[Strand]) -> np.ndarray:
        """Risk in [0, 1] per strand. Usable directly as an encoder Scorer."""
        raise NotImplementedError("Agent F")

    def top_kmers(self, k: int = 6, n: int = 20) -> list[tuple[str, float]]:
        """Most risky k-mers with their risk, for the dashboard."""
        raise NotImplementedError("Agent F")
