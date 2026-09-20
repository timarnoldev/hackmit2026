"""Tests for the live demo (dnacodec/demo.py, scripts/demo_roundtrip.py, dashboard/pages/2_Demo.py).

Result and codec files go to temporary folders, never into the real results/ or checkpoints/.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

import dnacodec.loop as loop
import dnacodec.results as results
from dnacodec import demo
from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.profiles import load_profile
from dnacodec.results import IterationResult, RunResult, save_run
from dnacodec.seeds import heldout_seeds
from dnacodec.types import EncoderSettings, Metrics

PAGE = Path(__file__).resolve().parents[1] / "dashboard" / "pages" / "2_Demo.py"


def noise_free(coverage: float = 8.0):
    return replace(
        load_profile("nanopore_budget"), name="noise_free", sub_rate=0.0, ins_rate=0.0, del_rate=0.0,
        dropout_rate=0.0, gc_dropout_factor=0.0, decay_per_year=0.0, read_quality_spread=0.0,
        position_rate_spread=0.0, malformed_read_rate=0.0, context_table=None, coverage_mean=coverage,
        coverage_dispersion=1000.0,
    )


class ConstantScorer:
    """Picklable stand-in for a risk model."""

    def __call__(self, strands):
        return np.full(len(strands), 0.1)


def metrics(recovery: float = 1.0) -> Metrics:
    return Metrics(n_strands=100, strand_accuracy=0.5, mean_edit_distance=1.0, dropout_rate=0.05,
                   reads_per_strand=6.0, per_position_error=[0.0] * 110, file_recovered=recovery == 1.0,
                   bits_per_base=1.0, recovery_rate=recovery, n_trials=300)


def fake_run(situation: str = "nanopore_budget") -> RunResult:
    tier1 = EncoderSettings(strand_length=140, redundancy=1.6)
    alt = EncoderSettings(strand_length=110, redundancy=1.0, risk_threshold=0.47)
    return RunResult(
        situation=situation, profile=load_profile(situation), recovery_target=1.0, n_trials=300,
        default_settings=EncoderSettings(), default_metrics=metrics(0.0),
        iterations=[
            IterationResult(0, tier1, metrics(), 6.0, stage="tier1", default_min_reads_matched=6.0),
            IterationResult(1, alt, metrics(), 6.0, stage="alternation 0", default_min_reads_matched=8.0),
        ],
        default_min_reads_at_target=20.0,
    )


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    res, codecs = tmp_path / "results", tmp_path / "codecs"
    monkeypatch.setattr(results, "RESULTS_DIR", res)
    monkeypatch.setattr(loop, "CODEC_DIR", codecs)
    return res, codecs


# ---------------------------------------------------------------- round trip


@pytest.mark.parametrize("data", [demo.demo_image_png(), demo.DEFAULT_MESSAGE.encode()])
def test_round_trip_noise_free_recovers_exactly(data):
    r = demo.round_trip(data, EncoderSettings(), None, MajorityVoteDecoder(), noise_free(), seed=0)
    assert r.ok and r.recovered == data
    assert r.counts[demo.CAUGHT] == 0 and r.counts[demo.SLIPPED] == 0
    assert r.counts[demo.CORRECT] + r.counts[demo.LOST] == r.n_strands
    assert r.bits_per_base > 0 and set(r.timings) == {"encode", "simulate", "decode", "recover"}


def test_status_counts_add_up_on_a_noisy_channel():
    data = demo.demo_image_png()
    r = demo.round_trip(data, EncoderSettings(), None, MajorityVoteDecoder(), load_profile("nanopore_budget"), seed=1)
    c = r.counts
    assert sum(c.values()) == r.n_strands == len(r.cluster_sizes) == r.metrics.n_strands
    assert c[demo.LOST] == sum(1 for n in r.cluster_sizes if n == 0)
    assert c[demo.CORRECT] / r.n_strands == pytest.approx(r.strand_accuracy)
    assert c[demo.CAUGHT] > 0  # 6 reads on Nanopore: some strands are decoded wrong
    assert r.needed == r.n_chunks < r.n_strands
    if not r.ok:
        assert r.recovered is None  # never a wrong file


def test_round_trip_is_deterministic_per_seed():
    data = demo.DEFAULT_MESSAGE.encode()
    p = load_profile("nanopore_budget")
    a = demo.round_trip(data, EncoderSettings(), None, MajorityVoteDecoder(), p, seed=3)
    b = demo.round_trip(data, EncoderSettings(), None, MajorityVoteDecoder(), p, seed=3)
    assert a.statuses == b.statuses and a.ok == b.ok


def test_demo_never_uses_heldout_seeds():
    assert demo.demo_seeds() == [0, 1, 2, 3, 4]
    with pytest.raises(ValueError):
        demo.round_trip(b"x", EncoderSettings(), None, MajorityVoteDecoder(), noise_free(), seed=heldout_seeds(1)[0])


def test_outcome_covers_every_case():
    ok = demo.round_trip(b"hello", EncoderSettings(), None, MajorityVoteDecoder(), noise_free(), seed=0)
    lost = replace(ok, recovered=None)
    assert "Both codecs recovered" in demo.outcome(ok, ok)
    assert "tuned codec recovered" in demo.outcome(lost, ok)
    assert "default codec recovered" in demo.outcome(ok, lost)
    assert "Both codecs lost" in demo.outcome(lost, lost)


# ---------------------------------------------------------------- pictures and files


def test_demo_image_and_strand_maps():
    img = demo.demo_image_png()
    assert demo.sniff_kind(img) == "png" and len(img) <= demo.MAX_DEMO_BYTES
    assert demo.demo_image_png() == img  # drawn deterministically
    assert demo.sniff_kind(demo.DEFAULT_MESSAGE.encode()) == "text"
    assert demo.sniff_kind(bytes(range(256))) == "binary"
    r = demo.round_trip(img, EncoderSettings(), None, MajorityVoteDecoder(), load_profile("nanopore_budget"), seed=0)
    svg = demo.strand_map_svg(r)
    assert svg.startswith("<svg") and svg.count("<title>strand ") == r.n_strands
    assert demo.sniff_kind(demo.strand_map_png(r)) == "png"


# ---------------------------------------------------------------- loading the tuned codec


def test_missing_run_is_reported_clearly(dirs):
    with pytest.raises(demo.TunedCodecUnavailable, match="nope"):
        demo.load_tuned_codec("nope", "nanopore_budget")


def test_results_fallback_uses_the_exact_tier1_codec(dirs):
    save_run(fake_run(), "r1")
    codec = demo.load_tuned_codec("r1", "nanopore_budget")
    assert codec.source == "results" and codec.exact and codec.scorer is None
    assert codec.iteration == 0 and codec.settings.redundancy == 1.6
    assert codec.default_min_reads == 20.0 and codec.tuned_min_reads == 6.0 and codec.matched_min_reads == 6.0
    assert "risk model" in codec.note and "{" not in codec.note  # says why the later iteration is not used
    assert "same density" in demo.tradeoff_note(EncoderSettings(), codec, 1.2, 0.63)


def test_results_fallback_marks_alternations_as_approximation(dirs):
    save_run(fake_run(), "r1")
    codec = demo.load_tuned_codec("r1", "nanopore_budget", iteration=-1)
    assert not codec.exact and codec.settings.risk_threshold is None
    assert codec.note.startswith("APPROXIMATION")
    with pytest.raises(demo.TunedCodecUnavailable):
        demo.load_tuned_codec("r1", "nanopore_budget", iteration=7)


def test_frozen_codec_with_risk_model(dirs):
    _, codecs = dirs
    d = codecs / "r1" / "nanopore_budget"
    d.mkdir(parents=True)
    (d / "risk_it1.pkl").write_bytes(pickle.dumps(ConstantScorer()))
    settings = EncoderSettings(redundancy=1.0, risk_threshold=0.5)
    manifest = {"situation": "nanopore_budget", "default_settings": asdict(EncoderSettings()), "iterations": [
        {"iteration": 0, "settings": asdict(EncoderSettings(redundancy=1.6)), "risk_path": "/elsewhere/risk_it0.pkl",
         "decoder": {"kind": "baseline", "checkpoint": None}, "stage": "rules", "stage_name": "tier1"},
        # absolute path from another machine: resolved to the file next to the manifest
        {"iteration": 1, "settings": asdict(settings), "risk_path": "/gx10/checkpoints/risk_it1.pkl",
         "decoder": {"kind": "baseline", "checkpoint": None}, "stage": "alternation", "stage_name": "alternation 0"},
    ]}
    (d / "manifest.json").write_text(json.dumps(manifest))
    codec = demo.load_tuned_codec("r1", "nanopore_budget")
    assert codec.source == "checkpoint" and codec.exact and codec.iteration == 1
    assert isinstance(codec.scorer, ConstantScorer) and codec.settings == settings
    r = demo.round_trip(b"tuned", codec.settings, codec.scorer, MajorityVoteDecoder(), noise_free(), seed=0)
    assert r.ok and "learned risk model" in r.scorer_name
    # the tier-1 entry's pickle is missing: fall back to the result file, or report it
    with pytest.raises(demo.TunedCodecUnavailable, match="could not be loaded"):
        demo.load_tuned_codec("r1", "nanopore_budget", iteration=0)


# ---------------------------------------------------------------- CLI


def test_cli_runs_and_handles_a_missing_run(dirs, tmp_path, capsys):
    from scripts import demo_roundtrip

    out = tmp_path / "out"
    assert demo_roundtrip.main(["--message", "hi DNA", "--run-id", "nope", "--seed", "2", "--out", str(out)]) == 1
    text = capsys.readouterr().out
    assert "TUNED CODEC NOT AVAILABLE" in text and "default" in text
    assert (out / "message_seed2_default_strandmap.svg").exists()

    save_run(fake_run(), "r1")
    assert demo_roundtrip.main(["--run-id", "r1", "--reads", "8", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Seeds: [0, 1, 2, 3, 4]" in text and text.count("seed ") >= 5
    assert demo_roundtrip.main(["--run-id", "r1", "--seed", str(heldout_seeds(1)[0]), "--out", str(out)]) == 2


# ---------------------------------------------------------------- dashboard page


def test_demo_page_runs(dirs):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    save_run(fake_run(), "r1")
    at = AppTest.from_file(str(PAGE), default_timeout=120).run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "outcome" in page and "All five fixed passes" in " ".join(s.value for s in at.subheader)
    assert page.count("<svg") == 2

    at.radio(key="source").set_value("Type a message").run()
    assert not at.exception
    at.radio(key="pass").set_value(3).run()
    assert not at.exception


def test_demo_page_without_any_run(dirs):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PAGE), default_timeout=120).run()
    assert not at.exception
    assert any("not available" in w.value for w in at.warning)
