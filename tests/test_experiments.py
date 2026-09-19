"""Tests for scripts/run_experiments.py on mocked loops."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

import numpy as np
import pytest

import dnacodec.loop as loop
import dnacodec.results as results
import dnacodec.simulator as simulator
from dnacodec.encoder import max_run_length
from dnacodec.loop import LoopConfig, SearchGrid, run_loop
from dnacodec.profiles import load_profile
from dnacodec.seeds import heldout_seeds, is_heldout
from dnacodec.types import EncoderSettings
from scripts.run_experiments import (
    CORE,
    Codec,
    accepted_by,
    build_summary,
    candidate_examples,
    load_simulator_b,
    roc_auc,
)
from scripts.run_loop import MockRiskModel, mock_components, mock_generate_strands, mock_min_reads

DATA = np.random.default_rng(11).bytes(2000)


def tiny_config() -> LoopConfig:
    return replace(
        LoopConfig.quick_mode(workers=1),
        label_strands=90,
        threshold_strands=100,
        coverages=(4, 8, 16, 30),
        curve_coverages=(6,),
        curve_trials=2,
        grid=SearchGrid(redundancy=(0.1, 0.5, 1.0), strand_length=(110,), max_homopolymer=(3, None),
                        gc_rule=(True,), risk_quantile=(None, 0.5), fallback_redundancy=(1.6,),
                        candidates_per_strand=(8,)),
    )


@pytest.fixture()
def finished_run(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(loop, "CODEC_DIR", tmp_path / "codecs")
    for name in CORE:
        run_loop(load_profile(name), "x", EncoderSettings(), 2, config=tiny_config(),
                 components=mock_components(), data=DATA)
    return "x"


def test_summary_has_every_piece_of_evidence(finished_run):
    comps = mock_components()
    summary = build_summary(finished_run, CORE, tiny_config(), comps, real_clusters=0, data=DATA)
    assert summary.is_mock

    systems = {(e.situation, e.system) for e in summary.ablation}
    assert systems == {(s, x) for s in CORE for x in "ABCDE"}
    runs = {r.situation: r for r in results.load_runs(finished_run)}
    for e in summary.ablation:
        run = runs[e.situation]
        if e.system == "B":
            assert e.metrics == run.default_metrics
        if e.system == "C":
            assert e.metrics == run.iterations[0].metrics  # tier 1
        if e.system == "D":
            assert e.metrics == run.iterations[1].metrics  # first alternation
        if e.system == "E":
            assert e.metrics == run.iterations[-1].metrics

    audit = {(e.situation, e.rule.split()[0]) for e in summary.rule_audit}
    assert audit == {(s, r) for s in CORE for r in ("max_homopolymer=3", "gc", "redundancy", "tuned")}
    for e in summary.rule_audit:
        assert e.verdict in ("pays off", "no measurable benefit", "harmful", "tuned is better", "default is fine")
        if not e.rule.startswith("tuned codec"):
            assert e.min_reads_on == runs[e.situation].default_min_reads_at_target
    assert any(e.rule == "redundancy 0.3 vs tuned" for e in summary.rule_audit)  # dashboard's pretty_rule format
    # Matched density: same bits per base on both sides, numbers straight from the loop's iterations.
    for s in CORE:
        run = runs[s]
        tier1 = next(e for e in summary.rule_audit if e.situation == s and e.rule.startswith("tuned codec (tier 1)"))
        assert tier1.bits_per_base_on == tier1.bits_per_base_off == run.iterations[0].metrics.bits_per_base
        assert tier1.min_reads_on == run.iterations[0].default_min_reads_matched
        assert tier1.min_reads_off == run.iterations[0].min_reads_at_target
        matched = [f for f in summary.firewall if f.situation == s and f.metric == "min_reads_default_matched"]
        assert len(matched) == 1

    pairs = {(e.codec, e.channel) for e in summary.crossover}
    assert pairs == {(c, ch) for c in ("default", *CORE) for ch in CORE}

    tests = {(e.situation, e.test) for e in summary.firewall}
    assert tests == {(s, t) for s in CORE for t in ("sim_a_heldout", "sim_b", "real")}
    sim_b = [e for e in summary.firewall if e.test == "sim_b"]
    assert all(e.metric == "not_run" and e.note for e in sim_b)  # mock runner / missing simulator_b

    assert not any(e.test == "tier2_direct" for e in summary.firewall)
    for s in CORE:
        mine = [e for e in summary.tier2 if e.situation == s]
        assert {e.candidates_per_strand for e in mine} == {8, 32}
        assert {e.simulator for e in mine} == {"A"}  # mock trial runner: no Simulator B comparison
        assert runs[s].profile.coverage_mean in {e.coverage for e in mine}
        for e in mine:
            assert e.n_trials == tiny_config().eval_trials
            assert e.strand_fail_diff == pytest.approx(e.strand_fail_risk - e.strand_fail_rule)
            assert e.strand_fail_diff_ci[0] - 1e-12 <= e.strand_fail_diff <= e.strand_fail_diff_ci[1] + 1e-12
            assert e.recovery_diff_ci[0] - 1e-12 <= e.recovery_diff <= e.recovery_diff_ci[1] + 1e-12
    assert loaded_ok(finished_run, summary)

    assert summary.examples
    for ex in summary.examples:
        assert set(ex.risk) == set(ex.accepted) == set(CORE)

    path = results.save_summary(summary, finished_run)
    assert path.exists()
    loaded = results.load_summary(finished_run)
    assert len(loaded.crossover) == 6 and loaded.is_mock


def test_experiments_only_use_heldout_seeds(finished_run):
    comps = mock_components()
    build_summary(finished_run, CORE, tiny_config(), comps, real_clusters=0, data=DATA)
    calls = comps.recovery_trials.calls
    assert calls, "crossover must evaluate the away codecs"
    assert all(all(is_heldout(s) for s in seeds) for _, seeds, _ in calls)
    # Full evaluations use all held-out seeds; the tier-2 measurement runs them one trial at a time.
    assert all(len(seeds) in (tiny_config().eval_trials, 1) for _, seeds, _ in calls)
    single = [seeds[0] for _, seeds, _ in calls if len(seeds) == 1]
    assert set(single) == set(heldout_seeds(tiny_config().eval_trials))


def test_roc_auc_matches_pairwise_definition():
    rng = np.random.default_rng(0)
    scores = rng.integers(0, 5, 60).astype(float)  # many ties
    positive = rng.random(60) < 0.4
    pairs = [
        1.0 if a > b else 0.5 if a == b else 0.0
        for (a, pa), (b, pb) in itertools.product(zip(scores, positive), repeat=2)
        if pa and not pb
    ]
    assert roc_auc(scores, positive) == pytest.approx(np.mean(pairs))
    assert roc_auc(np.arange(4.0), np.array([0, 0, 1, 1], dtype=bool)) == 1.0
    assert math.isnan(roc_auc(np.ones(3), np.zeros(3, dtype=bool)))


def test_sim_b_goes_through_the_simulator_argument(finished_run):
    seen = []
    comps = mock_components()
    comps.mocked = ("risk",)  # pretend the trial runner is real, so Simulator B is attempted
    inner = comps.recovery_trials

    def runner(*args, simulator=None, **kw):
        seen.append(simulator)
        return inner(*args, **kw)

    comps.recovery_trials = runner
    comps.min_reads_at_target = mock_min_reads(runner)
    summary = build_summary(finished_run, CORE, tiny_config(), comps, real_clusters=0, data=DATA)
    sim_b = load_simulator_b()
    assert sim_b is not None and sim_b in seen
    assert simulator.simulate not in seen  # never patched, never passed explicitly
    assert all(e.metric != "not_run" for e in summary.firewall if e.test == "sim_b")


def test_candidate_examples_disagree_between_channels():
    strands = mock_generate_strands(300, 110, 3)
    runs = np.array([max_run_length(s) for s in strands], dtype=float)
    nanopore = MockRiskModel().fit(strands, np.clip((runs - 2) / 6, 0, 1))
    illumina = MockRiskModel().fit(strands, np.full(len(strands), 0.01) + 0.001 * runs)
    codecs = {
        "nanopore_budget": Codec("nanopore_budget", EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None), nanopore),
        "illumina_standard": Codec("illumina_standard", EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None), illumina),
    }
    examples = candidate_examples(codecs, n=500, keep=6)
    assert len(examples) == 6
    assert any(len(set(ex.accepted.values())) == 2 for ex in examples)


def test_accepted_by_respects_hard_constraints_and_threshold():
    model = MockRiskModel().fit(["ACGT" * 25, "A" * 100], np.array([0.0, 1.0]))
    strands = ["ACGT" * 25, "AAAAAC" * 20]
    settings = EncoderSettings(max_homopolymer=3, risk_threshold=0.5)
    acc = accepted_by(settings, model, strands, np.array([0.1, 0.2]))
    assert list(acc) == [True, False]


def test_verdict_rules_are_fixed():
    from scripts.run_experiments import redundancy_verdict, rule_verdict

    cov = (2, 4, 6, 8, 10)
    assert rule_verdict(6.0, 8.0, cov) == "pays off"
    assert rule_verdict(6.0, 6.0, cov) == "no measurable benefit"
    assert rule_verdict(8.0, 6.0, cov) == "harmful"
    assert rule_verdict(10.0, None, cov) == "pays off"  # off never reaches the target
    assert redundancy_verdict(6, 8.0, 1.2, 6.0, 0.8, cov) == "tuned is better"  # only tuned meets the budget
    assert redundancy_verdict(6, 4.0, 1.2, 6.0, 1.5, cov) == "tuned is better"  # both meet, denser
    assert redundancy_verdict(6, 4.0, 1.2, 4.0, 1.1, cov) == "default is fine"


def test_matched_verdict():
    from scripts.run_experiments import matched_verdict

    cov = (2, 4, 6, 8, 10)
    assert matched_verdict(8.0, 7.5, cov) == "no measurable benefit"  # half steps map up to the grid
    assert matched_verdict(8.0, 5.5, cov) == "tuned is better"
    assert matched_verdict(8.0, 6.0, cov) == "tuned is better"
    assert matched_verdict(6.0, 6.0, cov) == "no measurable benefit"
    assert matched_verdict(6.0, 8.0, cov) == "harmful"
    assert matched_verdict(None, 10.0, cov) == "tuned is better"


def test_paired_bootstrap():
    from scripts.run_experiments import paired_bootstrap

    a = np.array([0.10, 0.12, 0.11, 0.09, 0.10] * 20)
    b = a - 0.02
    st = paired_bootstrap(a, b)
    assert st["diff"][0] == pytest.approx(-0.02)
    assert st["diff"][1] == pytest.approx(-0.02) and st["diff"][2] == pytest.approx(-0.02)  # perfectly paired
    lo, hi = st["a"][1], st["a"][2]
    assert lo < a.mean() < hi
    noisy = paired_bootstrap(np.zeros(50), np.r_[np.ones(5), np.zeros(45)])
    assert noisy["diff"][0] == pytest.approx(0.1) and noisy["diff"][1] < 0.1 < noisy["diff"][2]


def loaded_ok(run_id, summary) -> bool:
    results.save_summary(summary, run_id)
    loaded = results.load_summary(run_id)
    return [(e.situation, e.coverage, e.candidates_per_strand, tuple(e.strand_fail_diff_ci)) for e in loaded.tier2] == [
        (e.situation, e.coverage, e.candidates_per_strand, tuple(e.strand_fail_diff_ci)) for e in summary.tier2]


def test_tier2_runs_simulator_b_at_the_budget(finished_run):
    comps = mock_components()
    comps.mocked = ("risk",)  # pretend the trial runner is real, so Simulator B is attempted
    inner = comps.recovery_trials
    seen = []

    def runner(*args, simulator=None, **kw):
        seen.append(simulator)
        return inner(*args, **kw)

    comps.recovery_trials = runner
    comps.min_reads_at_target = mock_min_reads(runner)
    summary = build_summary(finished_run, CORE, tiny_config(), comps, real_clusters=0, data=DATA)
    runs = {r.situation: r for r in results.load_runs(finished_run)}
    for s in CORE:
        b = [e for e in summary.tier2 if e.situation == s and e.simulator == "B"]
        assert {e.candidates_per_strand for e in b} == {8, 32}
        assert all(e.coverage == runs[s].profile.coverage_mean for e in b)
