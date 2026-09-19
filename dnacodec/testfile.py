"""The fixed test file every codec is judged on. Never change its size or seed."""

from __future__ import annotations

import numpy as np

TEST_FILE_BYTES = 20 * 1024
TEST_FILE_SEED = 20260919


def test_file() -> bytes:
    """20 KB of random bytes. Random because real stored data is compressed and looks random."""
    return np.random.default_rng(TEST_FILE_SEED).bytes(TEST_FILE_BYTES)
