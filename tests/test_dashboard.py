"""Smoke tests for the Streamlit dashboard.

Result files are written to a temporary RESULTS_DIR, never into the real results/ folder.
"""

from __future__ import annotations

import dataclasses
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
    Tier2Entry,
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
    for section in ("moves past the default", "at home and away", "Where the gain comes from", "encoder learned"):
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


def staged_run() -> RunResult:
    stages = [("tier1", 1.45, 7.4, 8.4), ("alternation 0", 1.48, 6.6, 8.3), ("alternation 1", 1.50, 6.2, 8.2)]
    its = []
    for i, (stage, bits, reads, matched) in enumerate(stages):
        its.append(IterationResult(iteration=i, settings=EncoderSettings(redundancy=0.25), metrics=small_metrics(0.9, bits),
                                   min_reads_at_target=reads, stage=stage, default_min_reads_matched=matched))
    return run_result("nanopore_budget", its, default_min_reads_at_target=8.5)


def test_stages_and_matched_default_render(results_dir):
    save_run(staged_run(), "staged")
    at = run_app()
    assert not at.exception, at.exception
    text = markdown_text(at)
    # The headline compares against the default at the same bits per base (8.2), not the default point (8.5).
    assert "Same decoder, same bits per base: 24% fewer reads per strand (6.2 instead of 8.2)" in text
    assert "default at the same bits per base" in text


def test_stage_helpers():
    app = load_app_module()
    run = staged_run()
    assert [app.stage_label(run, it) for it in run.iterations] == ["C: rules audited", "D: + learned", "E: loop"]
    assert [app.short_step(run, it) for it in run.iterations] == ["C", "D", "E"]
    assert app.reference_reads(run, run.iterations[-1]) == (8.2, True)
    # Worse than the default rules at the same density: said plainly, no switch to another comparison.
    worse = run_result("nanopore_budget", [IterationResult(0, EncoderSettings(), small_metrics(0.9, 0.72), 7.0,
                                                           stage="alternation 2", default_min_reads_matched=6.0)],
                       default_min_reads_at_target=20.0)
    assert app.headline(worse, worse.iterations[0]) == (
        "Same decoder, same bits per base: the tuned codec needs more reads per strand than the default rules "
        "(7.0 vs 6.0).")
    # Older files: no stage, no matched value.
    old = run_result("nanopore_budget", [IterationResult(0, EncoderSettings(), small_metrics(0.9, 1.4), 7.0)],
                     default_min_reads_at_target=8.5)
    assert app.stage_label(old, old.iterations[0]) is None
    assert app.step_name(old, old.iterations[0]) == "alternation 1"
    assert app.short_step(old, old.iterations[0]) == "1"
    assert app.reference_reads(old, old.iterations[0]) == (8.5, False)
    # Several later alternations get numbered.
    more = run_result("nanopore_budget", [IterationResult(i, EncoderSettings(), small_metrics(0.9, 1.4), 7.0,
                                                          stage=f"alternation {i}") for i in range(3)])
    assert [app.short_step(more, it) for it in more.iterations] == ["D", "E1", "E2"]


def tier2_entry(situation="nanopore_budget", diff=-0.0052, ci=(-0.0073, -0.0033), simulator="A") -> Tier2Entry:
    return Tier2Entry(situation=situation, coverage=6.0, candidates_per_strand=8, n_trials=300,
                      strand_fail_rule=0.0099, strand_fail_risk=0.0099 + diff, strand_fail_diff=diff,
                      strand_fail_diff_ci=ci, recovery_rule=0.9, recovery_risk=0.95, recovery_diff=0.05,
                      recovery_diff_ci=(0.01, 0.09), simulator=simulator)


def test_tier2_view_renders(mock_run, results_dir):
    path = results_dir / "mock" / "summary.json"
    data = json.loads(path.read_text())
    data["tier2"] = [dataclasses.asdict(e) for e in (
        tier2_entry(), tier2_entry(simulator="B", diff=0.0005, ci=(-0.001, 0.002)),
        tier2_entry("illumina_standard", diff=0.0, ci=(-0.0004, 0.0004)))]
    path.write_text(json.dumps(data))
    at = run_app()
    assert not at.exception, at.exception
    text = markdown_text(at)
    assert "Tier 2, measured directly" in [s.value for s in at.subheader]
    assert "The learned scorer cuts strand failures by 0.52 points (95% CI 0.33 to 0.73)." in text
    assert "No measurable difference in strand failures" in text
    assert any("rule pay off" in h.value for h in at.header)  # first view unchanged


def test_summary_without_tier2_key(mock_run, results_dir):
    path = results_dir / "mock" / "summary.json"
    data = json.loads(path.read_text())
    data.pop("tier2", None)
    path.write_text(json.dumps(data))
    at = run_app()
    assert not at.exception, at.exception
    assert "Tier 2, measured directly" not in [s.value for s in at.subheader]


def test_tier2_sentences():
    app = load_app_module()
    assert app.tier2_sentence(tier2_entry()) == ("The learned scorer cuts strand failures by 0.52 points "
                                                 "(95% CI 0.33 to 0.73).")
    assert app.tier2_sentence(tier2_entry(diff=0.001, ci=(-0.002, 0.004))).startswith("No measurable difference")
    assert app.tier2_sentence(tier2_entry(diff=0.003, ci=(0.001, 0.005))).startswith(
        "The learned scorer raises strand failures by 0.30 points")
    assert app.tier2_config(tier2_entry(simulator="B")) == "6 reads · 8 candidates · other simulator (B)"


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


def test_single_step_marker():
    app = load_app_module()
    # Illumina homopolymer rule: 4 vs 3 reads is one coverage step -> flagged. Nanopore 20 vs 25 is not.
    small = RuleAuditEntry("illumina_standard", "max_homopolymer=3", 4.0, 3.0, 1.2, 1.2, "harmful")
    big = RuleAuditEntry("nanopore_budget", "max_homopolymer=3", 20.0, 25.0, 1.2, 1.2, "pays off")
    same = RuleAuditEntry("illumina_standard", "gc 0.4-0.6", 4.0, 4.0, 1.2, 1.2, "no measurable benefit")
    assert app.single_step(small) and not app.single_step(big) and not app.single_step(same)
    table = app.rule_audit_table([small, big], ["nanopore_budget", "illumina_standard"])
    assert table.count("1 step, may be noise") == 1
    sentence = app.audit_contrast([big, small], ["nanopore_budget", "illumina_standard"])
    assert sentence.endswith("is harmful on Illumina, standard lab (one coverage step, may be noise).")
    matched = RuleAuditEntry("nanopore_budget", "tuned codec (full loop) vs default rules at the same bits per base",
                             6.0, 7.0, 0.72, 0.72, "harmful")
    assert app.rule_effect(matched) == ("Reads needed at 0.72 bits per base: default rules 6.0, tuned codec 7.0")


def test_crossover_trade():
    app = load_app_module()
    # Illumina channel: the Nanopore codec needs fewer reads but carries far less data per letter.
    s = app.trade_sentence("Illumina, standard lab", "the codec tuned for Nanopore, tight budget", 2.0, 0.72,
                           "the home codec", 6.0, 1.50)
    assert s == ("On Illumina, standard lab, the codec tuned for Nanopore, tight budget needs 2.0 reads instead "
                 "of 6.0 but stores 52% less per letter (0.72 vs 1.50 bits per base).")
    assert "never reaches the recovery target" in app.trade_sentence("X", "a", None, 1.5, "b", 4.0, 1.2)
    assert app.crossover_verdict(2.0, 0.72, 4.0, 1.20) == 0  # fewer reads, less density: a trade
    assert app.crossover_verdict(6.0, 1.50, 7.0, 1.20) == 1  # better in both
    assert app.crossover_verdict(None, 1.50, 20.0, 1.20) == -1  # target not met
    assert app.crossover_verdict(8.0, 1.00, 7.0, 1.20) == -1  # worse in both


def test_real_like_crossover_renders(mock_run, results_dir):
    path = results_dir / "mock" / "summary.json"
    data = json.loads(path.read_text())
    for e in data["crossover"]:
        if e["codec"] == "illumina_standard" and e["channel"] == "nanopore_budget":
            e["min_reads_at_target"] = None  # the away codec never meets the target, as in run1
    path.write_text(json.dumps(data))
    at = run_app()
    assert not at.exception, at.exception
    text = markdown_text(at)
    assert "never reaches the recovery target" in text
    assert "The trade in plain words" in text
    assert "per letter" in text or "at the same density" in text  # density is always named with reads


def test_presentation_mode(mock_run):
    at = run_app()
    assert any("Details" in h.value for h in at.header)  # laptop layout has a Details section
    at.sidebar.toggle[0].set_value(True).run()  # first sidebar toggle is presentation mode
    assert not at.exception, at.exception
    present_headers = [h.value for h in at.header]
    assert not any("Details" in h for h in present_headers)  # moved into a collapsed expander
    text = markdown_text(at)
    assert "rule pay off" in present_headers[0]  # same order, rule audit still first
    assert "font-size: 27px" in text  # projector stylesheet is applied
    assert "MOCK DATA" in text
    # Back to the laptop layout.
    at.sidebar.toggle[0].set_value(False).run()
    assert not at.exception, at.exception
    assert any("Details" in h.value for h in at.header)


def test_presentation_mode_via_url(mock_run):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.query_params["present"] = "1"
    at.run()
    assert not at.exception, at.exception
    assert at.sidebar.toggle[0].value is True
    assert "font-size: 27px" in markdown_text(at)


def test_present_tier2_filter():
    app = load_app_module()
    entries = [tier2_entry(), tier2_entry(simulator="B"),
               dataclasses.replace(tier2_entry(), candidates_per_strand=32),
               dataclasses.replace(tier2_entry(), coverage=19.5)]
    app.set_mode(False)
    assert app.present_tier2(entries) == entries
    app.set_mode(True)
    try:
        kept = app.present_tier2(entries)
        # One row per coverage, simulator A, the most candidates tried.
        assert [(e.coverage, e.candidates_per_strand, e.simulator) for e in kept] == [(6.0, 32, "A"), (19.5, 8, "A")]
    finally:
        app.set_mode(False)


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
