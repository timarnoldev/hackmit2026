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
    """Nanopore profile reduced to the simple model: no position, homopolymer, spread,
    malformed-read or dropout effects."""
    base = load_profile("nanopore_budget")
    defaults = dict(
        homopolymer_factor=1.0,
        end_factor=1.0,
        dropout_rate=0.0,
        decay_per_year=0.0,
        gc_dropout_factor=0.0,
        homopolymer_run_factors=None,
        read_quality_spread=0.0,
        position_rate_spread=0.0,
        malformed_read_rate=0.0,
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


def test_calibration_measurement_recovers_simulated_parameters():
    from scripts.calibrate import error_stats, nb_shape_mle

    strands = random_strands(1500)
    plain = flat_profile(sub_rate=0.02, ins_rate=0.015, del_rate=0.02, coverage_mean=4.0)
    stats = error_stats(strands, simulate(strands, plain, seed=21))
    assert stats.sub == pytest.approx(0.02, rel=0.15)
    assert stats.ins == pytest.approx(0.015, rel=0.15)
    assert stats.end_ratio == pytest.approx(1.0, abs=0.15)
    ramped = replace(plain, end_factor=2.0, homopolymer_factor=1.4)
    shaped = error_stats(strands, simulate(strands, ramped, seed=22))
    assert shaped.end_ratio > 1.5
    assert shaped.hp_ratio > stats.hp_ratio + 0.15

    rng = np.random.default_rng(3)
    sizes = rng.negative_binomial(2.5, 2.5 / (2.5 + 20.0), size=20_000)
    assert nb_shape_mle(sizes) == pytest.approx(2.5, rel=0.08)


def per_read_errors(strands, clusters):
    return np.array([Levenshtein.distance(s, r) for s, c in zip(strands, clusters) for r in c])


def test_defaults_reproduce_simple_model():
    strands = random_strands(300)
    simple = flat_profile()
    explicit = replace(
        simple, homopolymer_run_factors=None, read_quality_spread=0.0,
        position_rate_spread=0.0, malformed_read_rate=0.0,
    )
    assert simulate(strands, simple, seed=3) == simulate(strands, explicit, seed=3)


def test_run_factors_act_on_deletions_only():
    runs = ["AAAACCCCGGGGTTTT" * 6 + "ACGT"] * 1500
    base = flat_profile(sub_rate=0.01, ins_rate=0.0, del_rate=0.0)
    boosted = replace(base, homopolymer_run_factors=(1.0, 2.0, 3.0, 5.0))
    subs = lambda cl: np.mean([sum(a != b for a, b in zip(s, r)) for s, c in zip(runs, cl) for r in c])
    assert subs(simulate(runs, boosted, seed=1)) == pytest.approx(subs(simulate(runs, base, seed=1)), rel=0.1)

    dels = flat_profile(sub_rate=0.0, ins_rate=0.0, del_rate=0.01)
    plain = ["ACGT" * 25] * 1500
    for strands, factors, expected in (
        (plain, (1.0, 2.0, 3.0, 5.0), 0.01),  # no runs, factor 1 everywhere
        (runs, (1.0, 2.0, 3.0, 5.0), 0.01 * (96 * 5.0 + 4) / 100),
        (runs, (1.0, 2.0), 0.01 * (96 * 2.0 + 4) / 100),  # last value applies to longer runs
    ):
        clusters = simulate(strands, replace(dels, homopolymer_run_factors=factors), seed=2)
        missing = np.mean([len(s) - len(r) for s, c in zip(strands, clusters) for r in c]) / 100
        assert missing == pytest.approx(expected, rel=0.08)


def test_read_quality_spread_keeps_mean_and_widens_spread():
    strands = random_strands(1500)
    base = flat_profile(coverage_mean=6.0)
    a = per_read_errors(strands, simulate(strands, base, seed=4))
    b = per_read_errors(strands, simulate(strands, replace(base, read_quality_spread=0.8), seed=4))
    assert b.mean() == pytest.approx(a.mean(), rel=0.08)
    assert b.std() > 1.5 * a.std()
    assert np.mean(b == 0) > 3 * np.mean(a == 0)  # many more near-perfect reads


def test_position_rate_spread_errors_are_shared_within_a_strand():
    strands = random_strands(400)
    base = flat_profile(sub_rate=0.02, ins_rate=0.0, del_rate=0.0, coverage_mean=30.0, coverage_dispersion=1e4)

    def hotspot_share(profile):
        fractions = []
        for s, reads in zip(strands, simulate(strands, profile, seed=5)):
            if len(reads) < 10:
                continue
            wrong = np.array([[a != b for a, b in zip(s, r)] for r in reads])
            fractions.append(wrong.mean(axis=0))
        return np.mean(np.concatenate(fractions) >= 0.2)

    iid, hot = hotspot_share(base), hotspot_share(replace(base, position_rate_spread=1.2))
    assert iid < 0.002
    assert hot > 10 * max(iid, 1e-4)


def test_malformed_reads_come_from_other_strands():
    strands = random_strands(2000)
    profile = flat_profile(malformed_read_rate=0.1, coverage_mean=5.0)
    clusters = simulate(strands, profile, seed=6)
    far = per_read_errors(strands, clusters) > 0.3 * 110
    assert far.mean() == pytest.approx(0.1, abs=0.015)
    # a single strand has no other strand to borrow from
    assert simulate(strands[:1], replace(profile, coverage_mean=20.0, coverage_dispersion=1e4), seed=1)[0]
