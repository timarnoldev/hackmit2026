"""Tests for Simulator B (firewall channel)."""

from dataclasses import replace

import numpy as np
import pytest
from rapidfuzz.distance import Levenshtein

from dnacodec import simulator, simulator_b
from dnacodec.profiles import load_profile

ALPHABET = np.array(list("ACGT"))


def random_strands(n: int, length: int = 110, seed: int = 0) -> list[str]:
    rng = np.random.default_rng(seed)
    return ["".join(row) for row in ALPHABET[rng.integers(0, 4, size=(n, length))]]


def nanopore(**overrides):
    """The calibrated profile (with all realism fields) minus dropout."""
    base = replace(load_profile("nanopore_budget"), dropout_rate=0.0, decay_per_year=0.0, gc_dropout_factor=0.0)
    return replace(base, **overrides)


def mean_edits(strands, clusters):
    d = [Levenshtein.distance(s, r) for s, c in zip(strands, clusters) for r in c]
    return float(np.mean(d))


def test_contract_and_determinism():
    strands = random_strands(200) + random_strands(20, length=140, seed=1)
    profile = nanopore()
    a = simulator_b.simulate(strands, profile, seed=3)
    assert a == simulator_b.simulate(strands, profile, seed=3)
    assert a != simulator_b.simulate(strands, profile, seed=4)
    assert len(a) == len(strands)
    assert all(set(r) <= set("ACGT") for c in a for r in c)
    assert simulator_b.simulate([], profile, seed=1) == []


def test_differs_from_a_but_similar_error_level():
    strands = random_strands(2000)
    profile = nanopore(coverage_mean=5.0)
    a = simulator.simulate(strands, profile, seed=5)
    b = simulator_b.simulate(strands, profile, seed=5)
    assert a != b
    ea, eb = mean_edits(strands, a), mean_edits(strands, b)
    assert 0.7 < eb / ea < 1.4


def test_zero_rates_give_exact_copies():
    strands = random_strands(100)
    profile = nanopore(
        sub_rate=0.0, ins_rate=0.0, del_rate=0.0, homopolymer_run_factors=None,
        homopolymer_factor=1.0, read_quality_spread=0.0, position_rate_spread=0.0, malformed_read_rate=0.0,
    )
    for ref, reads in zip(strands, simulator_b.simulate(strands, profile, seed=2)):
        assert all(r == ref for r in reads)


def test_run_events_remove_at_most_one_base_per_run():
    strands = ["ACGT" * 5 + "A" * 8 + "CGTACG" * 5] * 500
    profile = nanopore(
        sub_rate=0.0, ins_rate=0.0, del_rate=0.01, homopolymer_run_factors=(1.0, 50.0),
        read_quality_spread=0.0, position_rate_spread=0.0, malformed_read_rate=0.0,
    )
    shortened = []
    for reads in simulator_b.simulate(strands, profile, seed=7):
        for r in reads:
            runs = [len(x) for x in r.replace("C", " ").replace("G", " ").replace("T", " ").split()]
            shortened.append(max(runs) if runs else 0)
    shortened = np.array(shortened)
    assert np.mean(shortened == 7) > 0.5  # most reads lose one base of the run
    assert np.mean(shortened <= 6) < 0.1  # losing two needs two independent base deletions


def test_segments_match_second_moment():
    for sigma in (0.5, 1.0, 1.4):
        f, inside, outside = simulator_b._segment_params(sigma)
        assert f * inside + (1 - f) * outside == pytest.approx(1.0)
        assert f * inside**2 + (1 - f) * outside**2 == pytest.approx(np.exp(sigma**2), rel=1e-6)
        assert outside > 0
