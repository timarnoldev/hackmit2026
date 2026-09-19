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
from dnacodec.seeds import is_heldout
from dnacodec.types import EncoderSettings
from scripts.run_experiments import (
    CORE,
    Codec,
    accepted_by,
    build_summary,
    candidate_examples,
    roc_auc,
    use_simulator,
)
from scripts.run_loop import MockRiskModel, mock_components, mock_generate_strands

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
                        gc_rule=(True,), risk_quantile=(None, 0.5), fallback_redundancy=(1.6,)),
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
    assert systems == {(s, x) for s in CORE for x in "ABCD"}
    runs = {r.situation: r for r in results.load_runs(finished_run)}
    for e in summary.ablation:
        run = runs[e.situation]
        if e.system == "B":
            assert e.metrics == run.default_metrics
        if e.system == "C":
            assert e.metrics == run.iterations[0].metrics
        if e.system == "D":
            assert e.metrics == run.iterations[-1].metrics

    pairs = {(e.codec, e.channel) for e in summary.crossover}
    assert pairs == {(c, ch) for c in ("default", *CORE) for ch in CORE}

    tests = {(e.situation, e.test) for e in summary.firewall}
    assert tests == {(s, t) for s in CORE for t in ("sim_a_heldout", "sim_b", "real")}
    sim_b = [e for e in summary.firewall if e.test == "sim_b"]
    assert all(e.metric == "not_run" and e.note for e in sim_b)  # mock runner / missing simulator_b

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
    assert all(len(seeds) == tiny_config().eval_trials for _, seeds, _ in calls)


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


def test_use_simulator_patches_and_restores():
    original = simulator.simulate
    calls = []

    def fake(strands, profile, seed):
        calls.append(seed)
        return [[s] for s in strands]

    with use_simulator(fake) as used:
        assert not used()
        out = simulator.simulate(["ACGT"], load_profile("nanopore_budget"), 3)
        assert out == [["ACGT"]] and used() and calls == [3]
    assert simulator.simulate is original


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
