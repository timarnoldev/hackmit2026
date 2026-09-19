"""Seed discipline: keeps training and held-out evaluation strictly apart.

Every random draw in training, tuning, or the loop uses train_seed().
Only dnacodec.evaluate and final benchmark scripts may use heldout_seeds().
"""

from __future__ import annotations

HELDOUT_START = 900_000
HELDOUT_COUNT = 1_000


def is_heldout(seed: int) -> bool:
    return HELDOUT_START <= seed < HELDOUT_START + HELDOUT_COUNT


def train_seed(seed: int) -> int:
    """Pass every training or tuning seed through this."""
    if is_heldout(seed):
        raise ValueError(f"seed {seed} is reserved for held-out evaluation")
    return seed


def heldout_seeds(n: int = HELDOUT_COUNT) -> list[int]:
    """Seeds for final evaluation only. Never train, tune, or pick settings on these."""
    if not 0 < n <= HELDOUT_COUNT:
        raise ValueError(f"n must be in 1..{HELDOUT_COUNT}")
    return list(range(HELDOUT_START, HELDOUT_START + n))
