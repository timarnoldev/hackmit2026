"""Tests for the fountain encoder, scorer and recovery (Agent B)."""

from __future__ import annotations

import dataclasses
import time

import numpy as np
import pytest

from dnacodec.encoder import (
    encode,
    gc_fraction,
    max_run_length,
    payload_bits_per_base,
    recover,
    rule_scorer,
)
from dnacodec.seeds import train_seed
from dnacodec.types import EncoderSettings, FileMeta

DEFAULT = EncoderSettings()


def _random_bytes(n: int, seed: int) -> bytes:
    return np.random.default_rng(train_seed(seed)).integers(0, 256, n, dtype=np.uint8).tobytes()


@pytest.fixture(scope="module")
def big():
    data = _random_bytes(100_000, 1)
    t0 = time.perf_counter()
    enc = encode(data, DEFAULT)
    elapsed = time.perf_counter() - t0
    return data, enc, elapsed


def _check_constraints(strands, settings):
    for s in strands:
        assert len(s) == settings.strand_length
        assert set(s) <= set("ACGT")
        if settings.max_homopolymer is not None:
            assert max_run_length(s) <= settings.max_homopolymer
        if settings.gc_min is not None:
            assert gc_fraction(s) >= settings.gc_min
        if settings.gc_max is not None:
            assert gc_fraction(s) <= settings.gc_max


def test_roundtrip_100kb_noise_free(big):
    data, enc, elapsed = big
    assert elapsed < 30, f"encode took {elapsed:.1f}s"
    assert len(enc.strands) == int(np.ceil(enc.meta.n_chunks * (1 + DEFAULT.redundancy)))
    assert len(set(enc.strands)) == len(enc.strands)
    t0 = time.perf_counter()
    assert recover(enc.strands, enc.meta) == data
    assert time.perf_counter() - t0 < 30


def test_strands_obey_hard_constraints(big):
    _, enc, _ = big
    _check_constraints(enc.strands, DEFAULT)
    assert rule_scorer(DEFAULT)(enc.strands).max() == 0.0


def test_any_order(big):
    data, enc, _ = big
    order = np.random.default_rng(train_seed(2)).permutation(len(enc.strands))
    assert recover([enc.strands[i] for i in order], enc.meta) == data


@pytest.mark.parametrize("drop", [0.1, 0.2])
def test_roundtrip_with_dropout(big, drop):
    # redundancy 0.3 means at most 1 - 1/1.3 = 23% of strands can be lost in theory.
    data, enc, _ = big
    rng = np.random.default_rng(train_seed(10))
    keep = rng.random(len(enc.strands)) >= drop
    strands = [s if k else None for s, k in zip(enc.strands, keep)]
    assert recover(strands, enc.meta) == data


def test_too_much_dropout_returns_none(big):
    _, enc, _ = big
    strands = list(enc.strands[: enc.meta.n_chunks - 1]) + [None] * 10
    assert recover(strands, enc.meta) is None


def _corrupt(strand: str, rng: np.random.Generator) -> str:
    s = list(strand)
    for _ in range(int(rng.integers(1, 4))):
        kind = rng.integers(0, 3)
        i = int(rng.integers(0, len(s)))
        if kind == 0:
            s[i] = "ACGT"[(("ACGT".index(s[i])) + int(rng.integers(1, 4))) % 4]
        elif kind == 1:
            s.insert(i, "ACGT"[int(rng.integers(0, 4))])
        else:
            del s[i]
    out = "".join(s)
    # Indels change the length; decoders emit strand_length, so fix the length like they would.
    return (out + "A" * len(strand))[: len(strand)]


def test_roundtrip_with_corrupted_strands(big):
    data, enc, _ = big
    rng = np.random.default_rng(train_seed(20))
    strands = list(enc.strands)
    bad = rng.choice(len(strands), size=len(strands) // 10, replace=False)
    for i in bad:
        strands[i] = _corrupt(strands[i], rng)
    assert sum(a != b for a, b in zip(strands, enc.strands)) > len(strands) // 20
    assert recover(strands, enc.meta) == data


def test_corruption_plus_dropout(big):
    data, enc, _ = big
    rng = np.random.default_rng(train_seed(21))
    strands: list[str | None] = list(enc.strands)
    idx = rng.permutation(len(strands))
    for i in idx[: len(strands) // 10]:
        strands[i] = None
    for i in idx[len(strands) // 10 : len(strands) // 10 + len(strands) // 20]:
        strands[i] = _corrupt(strands[i], rng)
    assert recover(strands, enc.meta) == data


def test_recover_never_crashes_on_garbage():
    data = _random_bytes(2_000, 3)
    enc = encode(data, DEFAULT)
    garbage = [None, "", "ACGT", "N" * 110, "acgt" * 30, 12345, b"ACGT" * 30, "A" * 110, "X" * 500]
    assert recover(garbage, enc.meta) is None
    assert recover([], enc.meta) is None
    assert recover(None, enc.meta) is None  # type: ignore[arg-type]
    # Garbage mixed with the real strands still recovers.
    assert recover(garbage + list(enc.strands) + garbage, enc.meta) == data
    # Wrong meta does not crash.
    bad_meta = dataclasses.replace(enc.meta, n_chunks=enc.meta.n_chunks + 5)
    assert recover(enc.strands, bad_meta) is None
    wrong_size = dataclasses.replace(enc.meta, n_bytes=enc.meta.n_bytes + 1)
    assert recover(enc.strands, wrong_size) is None


def test_duplicate_strands_are_fine():
    data = _random_bytes(3_000, 4)
    enc = encode(data, DEFAULT)
    assert recover(list(enc.strands) * 2, enc.meta) == data


@pytest.mark.parametrize(
    "settings",
    [
        EncoderSettings(strand_length=140, seed_bases=12),
        EncoderSettings(strand_length=111, seed_bases=13),  # odd, non-byte-aligned sizes
        EncoderSettings(strand_length=60, seed_bases=10, redundancy=0.0),
        EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None, redundancy=0.1),
        EncoderSettings(max_homopolymer=2, gc_min=0.45, gc_max=0.55),
    ],
)
def test_other_settings(settings):
    data = _random_bytes(5_000, 5)
    enc = encode(data, settings)
    _check_constraints(enc.strands, settings)
    assert recover(enc.strands, enc.meta) == data


@pytest.mark.parametrize("n", [0, 1, 7, 21, 22, 23, 300])
def test_small_files(n):
    data = _random_bytes(n, 6)
    enc = encode(data, DEFAULT)
    assert recover(enc.strands, enc.meta) == data


def test_low_entropy_file_is_whitened():
    data = bytes(20_000)  # all zeros
    enc = encode(data, DEFAULT)
    _check_constraints(enc.strands, DEFAULT)
    assert recover(enc.strands, enc.meta) == data


def test_deterministic():
    data = _random_bytes(4_000, 7)
    assert encode(data, DEFAULT).strands == encode(data, DEFAULT).strands


def test_rule_scorer():
    score = rule_scorer(DEFAULT)
    good = "ACGT" * 27 + "AC"
    out = score([good, "A" * 110, "GC" * 55, "ACGTTTTA" * 13 + "AC", ""])
    assert out.shape == (5,)
    assert out.tolist() == [0.0, 1.0, 1.0, 1.0, 1.0]
    assert rule_scorer(EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None))(
        ["A" * 110]
    ).tolist() == [0.0]


def test_custom_scorer_changes_picks():
    data = _random_bytes(20_000, 8)

    def a_scorer(strands):
        return np.array([s.count("A") / len(s) for s in strands])

    default = encode(data, DEFAULT)
    custom = encode(data, DEFAULT, scorer=a_scorer)
    frac_default = np.mean([s.count("A") / len(s) for s in default.strands])
    frac_custom = np.mean([s.count("A") / len(s) for s in custom.strands])
    assert frac_custom < frac_default - 0.02
    _check_constraints(custom.strands, DEFAULT)
    assert recover(custom.strands, custom.meta) == data


def test_scorer_cannot_break_constraints():
    data = _random_bytes(2_000, 9)

    def loves_homopolymers(strands):
        return np.array([-max_run_length(s) for s in strands], dtype=float)

    enc = encode(data, DEFAULT, scorer=loves_homopolymers)
    _check_constraints(enc.strands, DEFAULT)
    assert recover(enc.strands, enc.meta) == data


def test_bad_scorer_output_raises():
    with pytest.raises(ValueError):
        encode(_random_bytes(500, 11), DEFAULT, scorer=lambda s: np.zeros(1))


def test_impossible_constraints_raise_clearly():
    with pytest.raises(ValueError, match="constraints"):
        encode(b"hello", EncoderSettings(max_homopolymer=1))
    with pytest.raises(ValueError):
        encode(b"hello", EncoderSettings(strand_length=20, seed_bases=16))


def test_strand_that_fools_the_strand_crc_is_survived():
    # Simulates the 1 in 65536 case: a wrong strand whose CRC-16 still matches.
    from dnacodec.encoder import _Layout

    data = _random_bytes(10_000, 12)
    enc = encode(data, DEFAULT)
    layout = _Layout(DEFAULT)
    strands = list(enc.strands)
    # Two bad strands with independent errors (identical errors could cancel out).
    for i, delta in ((0, 0b1011), (1, 0b110001)):
        seed, value = layout.parse(strands[i])
        strands[i] = layout.to_strand(seed, value ^ delta)
    assert recover(strands, enc.meta) == data


def test_strand_that_fools_the_strand_crc_100kb(big):
    from dnacodec.encoder import _Layout

    data, enc, _ = big
    layout = _Layout(DEFAULT)
    strands = list(enc.strands)
    seed, value = layout.parse(strands[123])
    strands[123] = layout.to_strand(seed, value ^ (1 << 100))
    assert recover(strands, enc.meta) == data


def test_density(big):
    _, enc, _ = big
    density = payload_bits_per_base(enc.meta, len(enc.strands))
    # 94 payload bases = 188 bits, minus 16 CRC bits = 172 data bits per 110 bases, / 1.3
    assert 1.15 < density < 1.21
    assert isinstance(enc.meta, FileMeta)


def _a_fraction(strands):
    return np.array([s.count("A") / len(s) for s in strands])


def test_risk_threshold_rejects_risky_candidates():
    data = _random_bytes(2_000, 13)
    settings = dataclasses.replace(DEFAULT, risk_threshold=0.18)
    enc = encode(data, settings, scorer=_a_fraction)
    assert _a_fraction(enc.strands).max() <= 0.18
    _check_constraints(enc.strands, settings)
    assert recover(enc.strands, enc.meta) == data
    # Without the threshold, ranking alone leaves some strands above it.
    loose = encode(data, DEFAULT, scorer=_a_fraction)
    assert _a_fraction(loose.strands).max() > 0.18


def test_risk_threshold_none_is_disabled_and_rule_scorer_passes():
    data = _random_bytes(3_000, 14)
    base = encode(data, DEFAULT).strands
    assert encode(data, dataclasses.replace(DEFAULT, risk_threshold=None)).strands == base
    assert encode(data, dataclasses.replace(DEFAULT, risk_threshold=0.0)).strands == base


def test_impossible_risk_threshold_raises():
    with pytest.raises(ValueError, match="risk_threshold"):
        encode(b"hi", dataclasses.replace(DEFAULT, risk_threshold=-1.0))
    with pytest.raises(ValueError, match="risk_threshold"):
        encode(b"hi", dataclasses.replace(DEFAULT, risk_threshold=0.01), scorer=_a_fraction)


def test_through_simulated_channel():
    from dnacodec.baseline import MajorityVoteDecoder
    from dnacodec.profiles import load_profile
    from dnacodec.simulator import simulate

    data = _random_bytes(5_000, 15)
    enc = encode(data, DEFAULT)
    # A fixed channel, independent of calibration: this tests the encoder, not the profile.
    profile = dataclasses.replace(
        load_profile("nanopore_budget"),
        sub_rate=0.022, ins_rate=0.017, del_rate=0.02, homopolymer_factor=1.3, end_factor=1.3,
        homopolymer_run_factors=None, read_quality_spread=0.0, position_rate_spread=0.0,
        malformed_read_rate=0.0, context_table=None, coverage_mean=20,  # ~89% exact strands, clear margin
    )
    clusters = simulate(enc.strands, profile, train_seed(16))
    decoded = MajorityVoteDecoder().decode(clusters, DEFAULT.strand_length)
    exact = np.mean([a == b for a, b in zip(decoded, enc.strands)])
    assert exact < 1.0  # the channel really corrupted or lost strands
    assert recover(decoded, enc.meta) == data
