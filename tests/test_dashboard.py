"""Smoke tests for the Streamlit dashboard.

Result files are written to a temporary RESULTS_DIR, never into the real results/ folder.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

import dnacodec.results as results  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.results import (  # noqa: E402
    AblationEntry,
    CoveragePoint,
    CrossoverEntry,
    ExperimentSummary,
    FirewallEntry,
    IterationResult,
    RuleAuditEntry,
    RunResult,
    save_run,
    save_summary,
)
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
    assert any("actually pays off on your channel" in t.value for t in at.title)
    headers = [h.value for h in at.header]
    assert "rule pay off" in headers[0]  # the rule audit is the first view
    for section in ("moves past the default", "wins at home", "Where the gain comes from", "encoder learned"):
        assert any(section in h for h in headers), (section, headers)
    # Rule audit: verdict badges, plain-words effect, and the cross-channel contrast sentence.
    assert "pays off" in text and "no measurable benefit" in text and "tuned is better" in text
    assert "Switching this rule off: reads needed 8.5 to 12.0" in text
    assert "No run of more than 3 identical letters" in text
    assert len(at.metric) == 2  # one reads-needed card per core channel
    assert "accepted" in text and "rejected" in text  # candidate examples from summary.json


def test_every_situation_and_live_mode(mock_run):
    at = run_app()
    situations = at.sidebar.selectbox[1].options
    assert len(situations) == 3  # "All situations" plus the two core channels
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


def run_result(situation: str, iterations, **kwargs) -> RunResult:
    return RunResult(situation=situation, profile=load_profile(situation), recovery_target=1.0, n_trials=10,
                     default_settings=EncoderSettings(), default_metrics=small_metrics(0.8, 1.4),
                     iterations=iterations, **kwargs)


def test_real_and_partial_runs_render_without_banner(results_dir):
    # A loop that has not finished an alternation, one that never met the target, and no summary.json.
    save_run(run_result("nanopore_budget", []), "partial")
    free = EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None, risk_threshold=0.6)
    save_run(run_result(
        "illumina_standard",
        [IterationResult(iteration=0, settings=free, metrics=small_metrics(0.85, None), min_reads_at_target=None)],
        default_min_reads_at_target=None,
        coverage_curve=[CoveragePoint("transformer", 2, 0.4), CoveragePoint("transformer", 8, 0.95),
                        CoveragePoint("tailored", 2, 0.7), CoveragePoint("tailored", 8, 0.99)],
    ), "partial")
    at = run_app()
    assert not at.exception, at.exception
    assert "MOCK DATA" not in markdown_text(at)


def test_summary_only_run_renders(results_dir):
    save_summary(ExperimentSummary(
        ablation=[AblationEntry("nanopore_budget", "A", small_metrics(0.6, 1.4), None),
                  AblationEntry("nanopore_budget", "B", small_metrics(0.8, 1.4), 8.0)],
        crossover=[CrossoverEntry("default", "nanopore_budget", small_metrics(0.8, 1.4), 8.0),
                   CrossoverEntry("nanopore_budget", "nanopore_budget", small_metrics(0.9, 1.5), None)],
        firewall=[FirewallEntry("nanopore_budget", "real", "risk_auc", 0.6)],
    ), "summary_only")
    at = run_app()
    assert not at.exception, at.exception


def test_older_summary_without_rule_audit(mock_run, results_dir):
    path = results_dir / "mock" / "summary.json"
    data = json.loads(path.read_text())
    del data["rule_audit"]
    path.write_text(json.dumps(data))
    at = run_app()
    assert not at.exception, at.exception
    assert not any("rule pay off" in h.value for h in at.header)
    assert any("moves past the default" in h.value for h in at.header)


def test_rule_audit_helpers():
    app = load_app_module()
    on = RuleAuditEntry("nanopore_budget", "max_homopolymer=3", 8.5, 12.0, 1.40, 1.40, "pays off")
    off = RuleAuditEntry("illumina_standard", "max_homopolymer=3", 9.0, 9.0, 1.40, 1.40, "no measurable benefit")
    tuned = RuleAuditEntry("illumina_standard", "redundancy 0.3 vs tuned", 9.0, 8.0, 1.40, 1.52, "tuned is better")
    unmet = RuleAuditEntry("nanopore_budget", "gc 0.4-0.6", None, 8.6, None, 1.4, "harmful")
    assert app.pretty_rule("max_homopolymer=3") == "No run of more than 3 identical letters"
    assert app.pretty_rule("gc 0.4-0.6") == "G+C share between 40% and 60%"
    assert app.pretty_rule("redundancy 0.3 vs tuned") == "Fixed 30% spare strands"
    assert app.pretty_rule("something new") == "something new"
    assert app.rule_effect(on) == "Switching this rule off: reads needed 8.5 to 12.0"
    assert app.rule_effect(off) == "Switching this rule off: reads needed stay at 9.0"
    assert app.rule_effect(tuned) == ("With the tuned value instead: reads needed 9.0 to 8.0, "
                                      "bits per base 1.40 to 1.52")
    assert "target not met" in app.rule_effect(unmet)
    sentence = app.audit_contrast([on, off, tuned], ["nanopore_budget", "illumina_standard"])
    assert sentence.startswith("“No run of more than 3 identical letters” pays off on Nanopore")
    assert "has no measurable benefit on Illumina" in sentence
    assert app.audit_contrast([tuned], ["illumina_standard"]) is None
    assert "not audited" in app.rule_audit_table([on, off, unmet], ["nanopore_budget", "illumina_standard"])


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


def test_setting_changes_and_format():
    app = load_app_module()
    default = EncoderSettings()
    free = EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None, risk_threshold=0.5)
    changes = app.setting_changes(default, free)
    assert "longest allowed letter run switched off" in changes
    assert any(c.startswith("learned risk filter on") for c in changes)
    assert app.setting_changes(default, default) == []
    assert app.fmt_setting(default, "gc") == "40 to 60%"
    assert app.fmt_setting(default, "redundancy") == "30%"
    assert app.fmt_setting(free, "max_homopolymer") == "off"
    assert app.highlight_runs("ACAAAAG") == 'AC<span class="run">AAAA</span>G'
