"""Smoke tests for the Streamlit dashboard.

Result files are written to a temporary RESULTS_DIR, never into the real results/ folder.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

import dnacodec.results as results  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.results import CoveragePoint, RunResult, save_run  # noqa: E402
from dnacodec.types import EncoderSettings, Metrics  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("dashboard_app", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # main() only runs under __main__, so this has no side effects
    return module


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(results, "RESULTS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def mock_run(results_dir):
    from scripts import make_mock_results

    make_mock_results.main()
    assert list((results_dir / "mock").glob("*.json"))
    return "mock"


def run_app() -> AppTest:
    return AppTest.from_file(str(APP), default_timeout=60).run()


def markdown_text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_mock_run_renders_with_banner(mock_run):
    at = run_app()
    assert not at.exception, at.exception
    text = markdown_text(at)
    assert "MOCK DATA" in text
    assert "Adaptive DNA codec" in [t.value for t in at.title]
    assert "Each situation ends up with a different codec" in [h.value for h in at.header][1]
    assert len(at.metric) == 3  # one accuracy card per mock situation


def test_every_situation_and_live_mode(mock_run):
    at = run_app()
    situations = at.sidebar.selectbox[1].options
    assert len(situations) == 4  # "All situations" plus the three profiles
    for option in at.sidebar.selectbox[1].options[1:]:
        at.sidebar.selectbox[1].set_value(at.sidebar.selectbox[1].options[0]).run()
        at.sidebar.selectbox[1].select(option).run()
        assert not at.exception, (option, at.exception)
    at.sidebar.toggle[0].set_value(True).run()
    assert not at.exception, at.exception
    assert "MOCK DATA" in markdown_text(at)


def small_metrics(accuracy: float, bits: float | None) -> Metrics:
    return Metrics(
        n_strands=100, strand_accuracy=accuracy, mean_edit_distance=0.5, dropout_rate=0.02,
        reads_per_strand=5.0, per_position_error=[0.01] * 20, bits_per_base=bits,
    )


def test_real_and_partial_runs_render_without_banner(results_dir):
    profile = load_profile("nanopore_budget")
    # A loop that has not finished an iteration yet, and one with a missing optional metric.
    save_run(RunResult(situation="nanopore_budget", profile=profile, default_settings=EncoderSettings(),
                       default_metrics=small_metrics(0.5, 1.4), iterations=[]), "partial")
    from dnacodec.results import IterationResult

    save_run(RunResult(
        situation="illumina_standard", profile=load_profile("illumina_standard"),
        default_settings=EncoderSettings(), default_metrics=small_metrics(0.8, None),
        iterations=[IterationResult(iteration=0, settings=EncoderSettings(max_homopolymer=None, gc_min=None,
                                                                          gc_max=None),
                                    metrics=small_metrics(0.85, None))],
        coverage_curve=[CoveragePoint("baseline", 2, 0.4), CoveragePoint("baseline", 8, 0.95),
                        CoveragePoint("tailored", 2, 0.7), CoveragePoint("tailored", 8, 0.99)],
    ), "partial")
    at = run_app()
    assert not at.exception, at.exception
    assert "MOCK DATA" not in markdown_text(at)


def test_no_runs_shows_hint(results_dir):
    at = run_app()
    assert not at.exception, at.exception
    assert any("No runs" in w.value for w in at.sidebar.warning)


def test_coverage_needed_interpolates():
    app = load_app_module()
    pts = [CoveragePoint("x", 2, 0.5), CoveragePoint("x", 4, 0.9), CoveragePoint("x", 1, 0.2)]
    assert app.coverage_needed(pts, 0.7) == pytest.approx(3.0)
    assert app.coverage_needed(pts, 0.1) == 1
    assert app.coverage_needed(pts, 0.95) is None


def test_strictness_and_settings_format():
    app = load_app_module()
    default = EncoderSettings()
    assert app.strictness(default, EncoderSettings(max_homopolymer=2)) > 0
    assert app.strictness(default, EncoderSettings(max_homopolymer=4, gc_min=0.3, gc_max=0.7)) < 0
    free = EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None)
    assert app.strictness(free, free) == 0
    assert app.fmt_setting(default, "gc") == "40 to 60%"
    assert app.fmt_setting(default, "redundancy") == "30%"
    assert app.fmt_setting(free, "max_homopolymer") == "no limit"
