"""Tests for the channel simulator."""

from dataclasses import replace

import numpy as np
import pytest
from rapidfuzz.distance import Levenshtein

from dnacodec.profiles import load_profile
from dnacodec.simulator import run_lengths, simulate

ALPHABET = np.array(list("ACGT"))


def random_strands(n: int, length: int = 110, seed: int = 0) -> list[str]:
    rng = np.random.default_rng(seed)
    return ["".join(row) for row in ALPHABET[rng.integers(0, 4, size=(n, length))]]


def flat_profile(**overrides):
    """Nanopore profile without position or homopolymer effects and without dropouts."""
    base = load_profile("nanopore_budget")
    defaults = dict(
        homopolymer_factor=1.0,
        end_factor=1.0,
        dropout_rate=0.0,
        decay_per_year=0.0,
        gc_dropout_factor=0.0,
    )
    return replace(base, **{**defaults, **overrides})


def measured_rates(strands, clusters):
    counts = {"replace": 0, "insert": 0, "delete": 0}
    bases = 0
    for ref, reads in zip(strands, clusters):
        for read in reads:
            for op in Levenshtein.editops(ref, read):
                counts[op.tag] += 1
            bases += len(ref)
    return {k: v / bases for k, v in counts.items()}


def test_same_seed_same_output_and_different_seed_differs():
    strands = random_strands(200)
    profile = load_profile("nanopore_budget")
    a = simulate(strands, profile, seed=7)
    assert a == simulate(strands, profile, seed=7)
    assert a != simulate(strands, profile, seed=8)


def test_shape_and_alphabet():
    strands = random_strands(50, length=140) + random_strands(50, length=90, seed=1)
    clusters = simulate(strands, load_profile("illumina_standard"), seed=1)
    assert len(clusters) == len(strands)
    for cluster in clusters:
        assert isinstance(cluster, list)
        for read in cluster:
            assert set(read) <= set("ACGT")
    assert simulate([], load_profile("illumina_standard"), seed=1) == []


def test_zero_error_rates_give_exact_copies():
    strands = random_strands(100)
    profile = flat_profile(sub_rate=0.0, ins_rate=0.0, del_rate=0.0)
    clusters = simulate(strands, profile, seed=3)
    for ref, reads in zip(strands, clusters):
        assert all(r == ref for r in reads)


def test_dropout_rate():
    strands = random_strands(20_000)
    profile = flat_profile(dropout_rate=0.1, coverage_mean=50.0, coverage_dispersion=50.0)
    clusters = simulate(strands, profile, seed=5)
    empty = np.mean([len(c) == 0 for c in clusters])
    assert empty == pytest.approx(0.1, abs=0.01)


def test_gc_dropout():
    profile = flat_profile(gc_dropout_factor=0.2, coverage_mean=50.0, coverage_dispersion=50.0)
    balanced = ["ACGT" * 25] * 5000  # GC 0.5, no extra dropout
    skewed = ["GCGA" * 25] * 5000  # GC 0.75, 0.25 deviation -> 0.5 dropout
    assert np.mean([len(c) == 0 for c in simulate(balanced, profile, seed=1)]) < 0.01
    lost = np.mean([len(c) == 0 for c in simulate(skewed, profile, seed=1)])
    assert lost == pytest.approx(0.5, abs=0.03)


def test_coverage_mean_and_dispersion():
    strands = random_strands(20_000, length=20)
    profile = flat_profile(coverage_mean=6.0, coverage_dispersion=2.0)
    sizes = np.array([len(c) for c in simulate(strands, profile, seed=9)])
    assert sizes.mean() == pytest.approx(6.0, rel=0.03)
    # negative binomial variance = mu + mu^2 / k = 6 + 18 = 24
    assert sizes.var() == pytest.approx(24.0, rel=0.1)


def test_measured_error_rates_match_profile():
    strands = random_strands(2000)
    profile = flat_profile(sub_rate=0.02, ins_rate=0.015, del_rate=0.02, coverage_mean=5.0)
    rates = measured_rates(strands, simulate(strands, profile, seed=11))
    assert rates["replace"] == pytest.approx(0.02, rel=0.15)
    assert rates["insert"] == pytest.approx(0.015, rel=0.15)
    assert rates["delete"] == pytest.approx(0.02, rel=0.15)


def test_errors_ramp_toward_strand_end():
    strands = random_strands(3000)
    profile = flat_profile(sub_rate=0.02, ins_rate=0.0, del_rate=0.0, end_factor=3.0)
    clusters = simulate(strands, profile, seed=2)
    first = last = 0
    for ref, reads in zip(strands, clusters):
        for read in reads:  # substitutions only, so positions line up
            first += sum(a != b for a, b in zip(ref[:20], read[:20]))
            last += sum(a != b for a, b in zip(ref[-20:], read[-20:]))
    assert last / first == pytest.approx((1 + 2 * 99.5 / 109) / (1 + 2 * 9.5 / 109), rel=0.1)


def test_homopolymer_runs_get_more_errors():
    profile = flat_profile(sub_rate=0.01, ins_rate=0.0, del_rate=0.0, homopolymer_factor=1.5)
    plain = ["ACGT" * 25] * 2000
    runs = ["AAAACCCCGGGGTTTT" * 6 + "ACGT"] * 2000
    for strands, expected in ((plain, 0.01), (runs, 0.01 * (96 * 1.5**3 + 4) / 100)):
        clusters = simulate(strands, profile, seed=4)
        errors = sum(sum(a != b for a, b in zip(s, r)) for s, c in zip(strands, clusters) for r in c)
        total = sum(len(s) * len(c) for s, c in zip(strands, clusters))
        assert errors / total == pytest.approx(expected, rel=0.1)


def test_run_lengths():
    from dnacodec.simulator import _encode

    codes, _ = _encode(["AACCCG", "TTTTTT", "A"])
    rl = run_lengths(codes)
    assert rl[0].tolist() == [2, 2, 3, 3, 3, 1]
    assert rl[1].tolist() == [6] * 6
    assert rl[2, 0] == 1
