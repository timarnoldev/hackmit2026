"""Adaptive DNA codec dashboard.

Run with:  uv run streamlit run dashboard/app.py

Reads result files only through dnacodec.results.load_runs and load_summary. Never imports
training code. The point it has to make obvious: decoder failures tell the encoder what to avoid,
separately per channel, and the tailored codec reaches the recovery target with fewer reads per
strand (or more bits per base) than the default with the same decoder (system B).
"""

from __future__ import annotations

import dataclasses
import html
import re
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dnacodec.results import (
    RESULTS_DIR,
    CoveragePoint,
    ExperimentSummary,
    IterationResult,
    RunResult,
    load_runs,
    load_summary,
)
from dnacodec.types import EncoderSettings, Metrics

# ---------------------------------------------------------------------------
# Palette. Gray = the default codec (system B), blue = the tailored codec.
# Steps come from a CVD-validated categorical palette, with separate light and dark steps.
# ---------------------------------------------------------------------------

PALETTES = {
    "light": {
        "tailored": "#2a78d6",
        "default": "#7a7974",
        "baseline": "#b5b4ad",
        "tailored_soft": "#86b6ef",
        "ladder": ["#9ec5f4", "#5598e7", "#1c5cab"],
        "transformer": "#eb6834",
        "extra": ["#1baf7a", "#4a3aa7", "#e87ba4", "#008300"],
        "grid": "rgba(0,0,0,0.08)",
        "muted": "#52514e",
        "good": "#0f7a3d",
        "bad": "#c2362f",
        "better_fill": "rgba(42,120,214,0.08)",
        "heat": [[0.0, "#fcfcfb"], [0.2, "#cde2fb"], [0.5, "#6da7ec"], [0.8, "#256abf"], [1.0, "#0d366b"]],
        "diverging": [[0.0, "#c2362f"], [0.25, "#ef9a93"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]],
    },
    "dark": {
        "tailored": "#3987e5",
        "default": "#a3a29b",
        "baseline": "#5f5e5a",
        "tailored_soft": "#1c5cab",
        "ladder": ["#184f95", "#2a78d6", "#6da7ec"],
        "transformer": "#d95926",
        "extra": ["#199e70", "#9085e9", "#d55181", "#008300"],
        "grid": "rgba(255,255,255,0.10)",
        "muted": "#c3c2b7",
        "good": "#4cc38a",
        "bad": "#f07470",
        "better_fill": "rgba(57,135,229,0.12)",
        "heat": [[0.0, "#1a1a19"], [0.25, "#184f95"], [0.6, "#3987e5"], [1.0, "#b7d3f6"]],
        # Both poles stay mid-dark so the white cell text keeps its contrast.
        "diverging": [[0.0, "#b8403f"], [0.25, "#6e3432"], [0.5, "#383835"], [0.75, "#1c4f8f"], [1.0, "#2a6cc0"]],
    },
}

FONT_SIZE = 16

HEADLINE = ("We built a tool that measures whether a DNA coding rule actually pays off on your channel, "
            "and tunes the codec accordingly.")

# Display order: the demo opens on Nanopore, then Illumina. Unknown situations go last.
SITUATION_ORDER = ["nanopore_budget", "illumina_standard", "illumina_archive_100y"]
NICE_SITUATION_NAMES = {
    "nanopore_budget": "Nanopore, tight budget",
    "illumina_standard": "Illumina, standard lab",
    "illumina_archive_100y": "Illumina, 100-year archive",
    "default": "Default (B)",
}

DECODER_LABELS = {
    "baseline": "Default codec + majority vote (A)",
    "transformer": "Default codec + transformer (B)",
    "tailored": "Tailored codec, same transformer",
}

SYSTEMS = {
    "A": "fixed rules<br>majority vote",
    "B": "fixed rules<br>(default)",
    "C": "rules audited<br>(tier 1)",
    "D": "+ learned<br>(tier 2)",
    "E": "full loop",
}

FIREWALL_TESTS = {
    "sim_a_heldout": "Our simulator, unseen seeds",
    "sim_b": "Differently built simulator (B)",
    "real": "Real sequencing reads",
}
FIREWALL_METRICS = {
    "recovery_rate_gain": "file recovery gain vs default",
    "min_reads_default": "reads needed, default",
    "min_reads_tailored": "reads needed, tailored",
    "risk_auc": "risk ranking quality (AUC, 0.5 = chance)",
}

# Plain-language names for encoder knobs. Unknown fields fall back to their raw name.
SETTING_LABELS = {
    "redundancy": ("Spare strands", "extra strands so the file survives lost or misread DNA"),
    "risk_threshold": ("Learned risk filter", "reject candidates the risk model rates riskier than this"),
    "max_homopolymer": ("Longest allowed letter run", "hand-written rule, e.g. no AAAA"),
    "gc": ("Allowed G+C share", "hand-written rule on the G/C balance"),
    "strand_length": ("Letters per strand", "longer strands hold more data but break more"),
    "seed_bases": ("Index letters per strand", "address of the strand, carries no data"),
    "candidates_per_strand": ("Candidates tried per strand", "the encoder keeps the safest one"),
}


def pretty_situation(name: str) -> str:
    return NICE_SITUATION_NAMES.get(name, name.replace("_", " ").capitalize())


def situation_rank(name: str) -> tuple[int, str]:
    return (SITUATION_ORDER.index(name) if name in SITUATION_ORDER else len(SITUATION_ORDER), name)


def palette() -> dict:
    theme_type = None
    try:
        theme_type = st.context.theme.type
    except Exception:  # older Streamlit or no browser context (tests)
        pass
    return PALETTES["dark" if theme_type == "dark" else "light"]


# ---------------------------------------------------------------------------
# Data access. Only through load_runs and load_summary.
# ---------------------------------------------------------------------------


def list_run_ids() -> list[str]:
    """Subfolders of results/ that contain at least one result file, newest first."""
    if not RESULTS_DIR.exists():
        return []
    found = []
    for d in RESULTS_DIR.iterdir():
        files = list(d.glob("*.json")) if d.is_dir() else []
        if files:
            found.append((max(f.stat().st_mtime for f in files), d.name))
    return [name for _, name in sorted(found, reverse=True)]


def _retry(fn, *args):
    """The loop may be writing a file right now; retry briefly before giving up."""
    error = None
    for attempt in range(3):
        try:
            return fn(*args), None
        except Exception as exc:  # partially written JSON, format drift
            error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.3 * (attempt + 1))
    return None, error


def load(run_id: str) -> tuple[list[RunResult], ExperimentSummary | None, list[str]]:
    runs, run_err = _retry(load_runs, run_id)
    summary, sum_err = _retry(load_summary, run_id)
    runs = sorted(runs or [], key=lambda r: situation_rank(r.situation))
    return runs, summary, [e for e in (run_err, sum_err) if e]


def tailored(run: RunResult) -> IterationResult | None:
    return run.best if run.iterations else None


# ---------------------------------------------------------------------------
# Pure helpers (tested in tests/test_dashboard.py)
# ---------------------------------------------------------------------------


def coverage_needed(points: list[CoveragePoint], target: float) -> float | None:
    """Reads per strand at which a decoder first reaches the target accuracy (linear interpolation)."""
    pts = sorted(points, key=lambda p: p.coverage)
    for i, p in enumerate(pts):
        if p.strand_accuracy >= target:
            if i == 0:
                return p.coverage
            prev = pts[i - 1]
            span = p.strand_accuracy - prev.strand_accuracy
            if span <= 0:
                return p.coverage
            return prev.coverage + (target - prev.strand_accuracy) / span * (p.coverage - prev.coverage)
    return None


def rel_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return new / old - 1


def fmt_setting(settings: EncoderSettings, key: str) -> str:
    if key == "gc":
        if settings.gc_min is None and settings.gc_max is None:
            return "off"
        lo = "0" if settings.gc_min is None else f"{settings.gc_min * 100:.0f}"
        hi = "100" if settings.gc_max is None else f"{settings.gc_max * 100:.0f}"
        return f"{lo} to {hi}%"
    value = getattr(settings, key)
    if value is None:
        return "off"
    if key == "redundancy":
        return f"{value * 100:.0f}%"
    if key == "risk_threshold":
        return f"risk ≤ {value:.2f}"
    return f"{value:g}" if isinstance(value, float) else str(value)


def setting_keys() -> list[str]:
    keys = []
    for f in dataclasses.fields(EncoderSettings):
        if f.name in ("gc_min", "gc_max"):
            if "gc" not in keys:
                keys.append("gc")
        else:
            keys.append(f.name)
    order = list(SETTING_LABELS)
    return sorted(keys, key=lambda k: order.index(k) if k in order else len(order))


def setting_changes(default: EncoderSettings, new: EncoderSettings) -> list[str]:
    """Plain-language list of what the loop changed, e.g. 'letter-run rule off'."""
    out = []
    for key in setting_keys():
        old_v, new_v = fmt_setting(default, key), fmt_setting(new, key)
        if old_v == new_v:
            continue
        label = SETTING_LABELS.get(key, (key.replace("_", " "),))[0].lower()
        if new_v == "off":
            out.append(f"{label} switched off")
        elif old_v == "off":
            out.append(f"{label} on ({new_v})")
        else:
            out.append(f"{label} {old_v} → {new_v}")
    return out


def target_text(run: RunResult) -> str:
    if run.recovery_target >= 1.0:
        return f"file recovered in all {run.n_trials} test trials"
    return f"file recovered in {run.recovery_target:.0%} of {run.n_trials} test trials"


def headline(run: RunResult, best: IterationResult) -> str:
    d_reads, t_reads = run.default_min_reads_at_target, best.min_reads_at_target
    d, t = run.default_metrics, best.metrics
    parts = []
    if d_reads is not None and t_reads is not None and t_reads < d_reads:
        parts.append(f"{1 - t_reads / d_reads:.0%} fewer reads per strand ({t_reads:.1f} instead of {d_reads:.1f})")
    elif d_reads is None and t_reads is not None:
        parts.append(f"reaches the target at {t_reads:.1f} reads, the default never does")
    dens = rel_change(t.bits_per_base, d.bits_per_base)
    if dens is not None and dens >= 0.01:
        parts.append(f"{dens:.0%} more data per letter")
    if not parts and t.recovery_rate is not None and d.recovery_rate is not None and t.recovery_rate > d.recovery_rate:
        parts.append(f"recovers the file more often ({t.recovery_rate:.0%} vs {d.recovery_rate:.0%} of trials)")
    if not parts:
        return "No gain over the default yet."
    return "Same decoder, " + " and ".join(parts) + "."


def profile_summary(run: RunResult) -> str:
    p = run.profile
    err = p.sub_rate + p.ins_rate + p.del_rate
    return (
        f"{p.technology.capitalize()} · read budget about {p.coverage_mean:g} reads per strand · "
        f"{err:.1%} of letters misread · {p.total_dropout:.0%} strands lost"
    )


def highlight_runs(strand: str, min_run: int = 4) -> str:
    """HTML for a strand with long single-letter runs (e.g. AAAA) highlighted."""
    return re.sub(
        r"(A{%d,}|C{%d,}|G{%d,}|T{%d,})" % ((min_run,) * 4),
        lambda m: f'<span class="run">{m.group(0)}</span>',
        html.escape(strand),
    )


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

CSS = """
<style>
html { font-size: 18px; }
.block-container { padding-top: 2.2rem; max-width: 1700px; }
h1 { font-size: 2.3rem !important; letter-spacing: -0.01em; line-height: 1.2 !important; max-width: 62rem; }
h2 { font-size: 1.9rem !important; margin-top: 1.6rem !important; }
h3 { font-size: 1.35rem !important; }
[data-testid="stMetricValue"] { font-size: 2.3rem; font-weight: 650; }
[data-testid="stMetricLabel"] p { font-size: 1.0rem; font-weight: 600; }
[data-testid="stMetricDelta"] { font-size: 1.0rem; }
.mock-banner {
  background: #c62828; color: #ffffff; padding: 0.9rem 1.2rem; border-radius: 10px;
  font-size: 1.6rem; font-weight: 800; text-align: center; letter-spacing: 0.04em;
  margin: 0.4rem 0 1rem 0; border: 3px solid #8e0000;
}
.mock-banner small { display: block; font-size: 0.95rem; font-weight: 500; letter-spacing: 0; }
.lede { font-size: 1.3rem; opacity: 0.88; margin-top: -0.6rem; max-width: 70rem; }
.gloss { font-size: 1.0rem; line-height: 1.45; opacity: 0.9; }
.headline { font-size: 1.15rem; font-weight: 650; margin: 0.2rem 0 0.4rem 0; }
.changes { font-size: 0.98rem; margin: 0 0 0.5rem 0; }
.muted { opacity: 0.7; font-size: 0.95rem; }
table.dash { width: 100%; border-collapse: collapse; font-size: 1.08rem; }
table.dash th, table.dash td {
  padding: 0.5rem 0.75rem; border-bottom: 1px solid rgba(128,128,128,0.25); text-align: left; vertical-align: top;
}
table.dash td.knob small { display: block; opacity: 0.65; font-size: 0.85rem; }
table.dash td.chg { background: rgba(57,135,229,0.20); font-weight: 700; }
table.dash td.same { opacity: 0.55; }
table.dash tr.result td { font-weight: 650; border-top: 2px solid rgba(128,128,128,0.45); }
table.dash td.strand { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 0.85rem;
  word-break: break-all; max-width: 34rem; line-height: 1.5; }
table.dash .run { background: rgba(235,104,52,0.28); border-radius: 3px; font-weight: 700; }
table.compare { width: 100%; border-collapse: collapse; font-size: 1.05rem; margin: 0.4rem 0 0.3rem 0; }
table.compare th { text-align: right; font-size: 0.9rem; opacity: 0.7; font-weight: 600; padding: 0.2rem 0.4rem; }
table.compare td { text-align: right; padding: 0.35rem 0.4rem; border-top: 1px solid rgba(128,128,128,0.22);
  white-space: nowrap; font-variant-numeric: tabular-nums; }
table.compare td:first-child, table.compare th:first-child { text-align: left; white-space: normal; }
table.compare td:nth-child(4) { font-weight: 700; }
.flat { opacity: 0.6; font-weight: 500; }
table.audit td { padding: 0.8rem 0.9rem; }
table.audit th { font-size: 1.2rem; }
table.audit .verdict { font-size: 1.15rem; padding: 0.15rem 0.7rem; margin-bottom: 0.35rem; }
table.audit .effect { font-size: 1.05rem; line-height: 1.4; }
.contrast { font-size: 1.35rem; font-weight: 650; margin: 0.3rem 0 0.8rem 0; }
.overline { font-size: 1.0rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.65;
  margin-bottom: -0.4rem; }
.badge { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 999px; font-size: 0.9rem;
  font-weight: 700; border: 1.5px solid currentColor; white-space: nowrap; }
</style>
"""


def base_layout(fig: go.Figure, height: int = 360) -> go.Figure:
    pal = palette()
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        font=dict(size=FONT_SIZE),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=FONT_SIZE)),
        hoverlabel=dict(font_size=FONT_SIZE),
    )
    axis = dict(gridcolor=pal["grid"], zeroline=False, tickfont=dict(size=FONT_SIZE - 1),
                title_font=dict(size=FONT_SIZE))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def show(fig: go.Figure, key: str) -> None:
    st.plotly_chart(fig, key=key, config={"displayModeBar": False})


def in_columns(runs: list[RunResult], render) -> None:
    """Render one block per situation side by side (at most 3 per row)."""
    for start in range(0, len(runs), 3):
        chunk = runs[start:start + 3]
        cols = st.columns(len(chunk), gap="large")
        for col, run in zip(cols, chunk):
            with col:
                render(run)


def mock_banner() -> None:
    st.markdown(
        '<div class="mock-banner">MOCK DATA: NOT REAL RESULTS'
        "<small>These numbers come from scripts/make_mock_results.py and exist only to build the dashboard.</small></div>",
        unsafe_allow_html=True,
    )


def glossary() -> None:
    items = [
        ("Strand", "one short piece of DNA, about 110 letters (A, C, G, T). A file is split over thousands."),
        ("Reads per strand", "how many noisy copies of each strand we sequence. Fewer reads = cheaper reading."),
        ("Bits per base", "data stored per DNA letter (max 2). Spare strands for safety lower it."),
        ("Rule", "a hand-written constraint like “never AAAA”, or a fixed share of spare strands."),
        ("Default (B)", "the usual fixed rules and redundancy, with the same AI decoder as ours."),
    ]
    for col, (term, text) in zip(st.columns(len(items)), items):
        col.markdown(f'<div class="gloss"><b>{term}</b>: {text}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 1. Rule audit
# ---------------------------------------------------------------------------

# verdict -> (palette key for the badge color, icon, phrase used in the headline sentence)
VERDICTS = {
    "pays off": ("good", "✓", "pays off"),
    "no measurable benefit": ("muted", "–", "has no measurable benefit"),
    "harmful": ("bad", "✗", "is harmful"),
    "tuned is better": ("tailored", "↻", "is beaten by a tuned value"),
}


def pretty_rule(rule: str) -> str:
    """'max_homopolymer=3' -> 'No run of more than 3 identical letters', unknown rules unchanged."""
    m = re.fullmatch(r"\s*max_homopolymer\s*=\s*(\d+)\s*", rule)
    if m:
        return f"No run of more than {m.group(1)} identical letters"
    m = re.fullmatch(r"\s*gc\s*([\d.]+)\s*-\s*([\d.]+)\s*", rule)
    if m:
        return f"G+C share between {float(m.group(1)):.0%} and {float(m.group(2)):.0%}"
    m = re.fullmatch(r"\s*redundancy\s*([\d.]+)\s*vs\s*tuned\s*", rule)
    if m:
        return f"Fixed {float(m.group(1)):.0%} spare strands"
    return rule


def rule_effect(e) -> str:
    """Plain-words cost and benefit, e.g. 'Switching this rule off: reads needed 8.5 to 12.0'."""
    tuned = "tuned" in e.rule
    prefix = "With the tuned value instead" if tuned else "Switching this rule off"

    def reads(v):
        return "target not met" if v is None else f"{v:.1f}"

    if e.min_reads_on is not None and e.min_reads_off is not None and abs(e.min_reads_on - e.min_reads_off) < 0.05:
        parts = [f"reads needed stay at {e.min_reads_on:.1f}"]
    else:
        parts = [f"reads needed {reads(e.min_reads_on)} to {reads(e.min_reads_off)}"]
    if (e.bits_per_base_on is not None and e.bits_per_base_off is not None
            and abs(e.bits_per_base_on - e.bits_per_base_off) >= 0.005):
        parts.append(f"bits per base {e.bits_per_base_on:.2f} to {e.bits_per_base_off:.2f}")
    return f"{prefix}: " + ", ".join(parts)


def verdict_badge(verdict: str) -> str:
    pal = palette()
    key, icon, _ = VERDICTS.get(verdict, ("muted", "•", verdict))
    return f'<span class="badge verdict" style="color:{pal[key]}">{icon} {html.escape(verdict)}</span>'


def audit_contrast(entries, situations: list[str]) -> str | None:
    """One sentence for the first rule whose verdict differs between channels."""
    by_rule: dict[str, dict[str, str]] = {}
    for e in entries:
        if e.situation in situations:
            by_rule.setdefault(e.rule, {})[e.situation] = e.verdict
    for rule, verdicts in by_rule.items():
        if len(set(verdicts.values())) > 1:
            parts = [f"{VERDICTS.get(v, ('', '', v))[2]} on {pretty_situation(s)}"
                     for s, v in sorted(verdicts.items(), key=lambda sv: situation_rank(sv[0]))]
            return f"“{pretty_rule(rule)}” " + ", but ".join(parts) + "."
    return None


def rule_audit_table(entries, situations: list[str]) -> str:
    sits = [s for s in situations if any(e.situation == s for e in entries)]
    by = {(e.rule, e.situation): e for e in entries}
    rules = list(dict.fromkeys(e.rule for e in entries if e.situation in sits))
    head = "<th>Rule</th>" + "".join(f"<th>{html.escape(pretty_situation(s))}</th>" for s in sits)
    rows = []
    for rule in rules:
        cells = [f'<td class="knob"><b>{html.escape(pretty_rule(rule))}</b>'
                 f"<small>{html.escape(rule)}</small></td>"]
        for s in sits:
            e = by.get((rule, s))
            if e is None:
                cells.append('<td class="same">not audited</td>')
                continue
            note = f'<div class="muted">{html.escape(e.note)}</div>' if e.note else ""
            cells.append(f'<td>{verdict_badge(e.verdict)}<div class="effect">{html.escape(rule_effect(e))}</div>'
                         f"{note}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="dash audit"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


# ---------------------------------------------------------------------------
# 2. Pareto plot and headline numbers
# ---------------------------------------------------------------------------


def pareto_chart(run: RunResult) -> go.Figure | None:
    """x = bits per base, y = reads per strand needed at the recovery target. Better is bottom right."""
    pal = palette()
    dx, dy = run.default_metrics.bits_per_base, run.default_min_reads_at_target
    pts = [(it.iteration, it.metrics.bits_per_base, it.min_reads_at_target) for it in run.iterations]
    pts = [(i, x, y) for i, x, y in pts if x is not None and y is not None]
    if not pts and (dx is None or dy is None):
        return None
    xs = [x for _, x, _ in pts] + ([dx] if dx is not None and dy is not None else [])
    ys = [y for _, _, y in pts] + ([dy] if dx is not None and dy is not None else [])
    xpad = max(0.03, (max(xs) - min(xs)) * 0.35)
    ypad = max(0.5, (max(ys) - min(ys)) * 0.35)
    xr = [min(xs) - xpad, max(xs) + xpad]
    yr = [max(0, min(ys) - ypad), max(ys) + ypad]

    fig = go.Figure()
    if dx is not None and dy is not None:
        # Everything right of and below the default beats it on both axes.
        fig.add_shape(type="rect", x0=dx, x1=xr[1], y0=yr[0], y1=dy, line_width=0,
                      fillcolor=pal["better_fill"], layer="below")
        fig.add_annotation(x=xr[1], y=yr[0], xanchor="right", yanchor="bottom", showarrow=False,
                           text="better than default on both", font=dict(color=pal["tailored"], size=FONT_SIZE - 1))
    path = ([(None, dx, dy)] if dx is not None and dy is not None else []) + pts
    for (_, x0, y0), (_, x1, y1) in zip(path, path[1:]):
        fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y", axref="x", ayref="y", text="",
                           showarrow=True, arrowhead=2, arrowsize=1.3, arrowwidth=2, arrowcolor=pal["tailored_soft"],
                           standoff=11, startstandoff=11)
    if dx is not None and dy is not None:
        fig.add_trace(go.Scatter(
            x=[dx], y=[dy], mode="markers+text", name="Default codec (B)",
            marker=dict(symbol="diamond", size=20, color=pal["default"], line=dict(color="white", width=2)),
            text=["Default"], textposition="top center", textfont=dict(size=FONT_SIZE, color=pal["default"]),
            hovertemplate="Default (B)<br>%{x:.2f} bits per base<br>%{y:.1f} reads needed<extra></extra>",
        ))
    if pts:
        last = len(pts) - 1
        fig.add_trace(go.Scatter(
            x=[x for _, x, _ in pts], y=[y for _, _, y in pts], mode="markers+text",
            name="Loop alternations",
            marker=dict(size=[14] * last + [22], color=pal["tailored"], line=dict(color="white", width=2)),
            text=[f"{i + 1}" for i, _, _ in pts[:-1]] + ["Tailored"],
            textposition=["bottom center"] * last + ["bottom right"],
            textfont=dict(size=FONT_SIZE, color=pal["tailored"]),
            customdata=[i + 1 for i, _, _ in pts],
            hovertemplate="Alternation %{customdata}<br>%{x:.2f} bits per base<br>%{y:.1f} reads needed<extra></extra>",
        ))
    base_layout(fig, height=440)
    fig.update_layout(showlegend=False, title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 3)),
                      margin=dict(t=50))
    fig.update_xaxes(title="bits per base (more data →)", range=xr)
    fig.update_yaxes(title="reads per strand needed (fewer ↓)", range=yr)
    return fig


COMPARE_ROWS = [
    # (label, getter(run, iteration_or_None), format, higher is better, show change as points)
    ("Reads per strand needed", lambda r, it: r.default_min_reads_at_target if it is None else it.min_reads_at_target,
     "{:.1f}", False, False),
    ("Bits per base", lambda r, it: (r.default_metrics if it is None else it.metrics).bits_per_base,
     "{:.2f}", True, False),
    ("Spare strands", lambda r, it: (r.default_settings if it is None else it.settings).redundancy,
     "{:.0%}", False, False),
    ("File recovered at budget", lambda r, it: (r.default_metrics if it is None else it.metrics).recovery_rate,
     "{:.0%}", True, True),
    ("Strands read exactly at budget", lambda r, it: (r.default_metrics if it is None else it.metrics).strand_accuracy,
     "{:.1%}", True, True),
    ("Write cost per MB*", lambda r, it: (r.default_metrics if it is None else it.metrics).write_cost_usd_per_mb,
     "${:,.0f}", False, False),
    ("Read cost per MB*", lambda r, it: (r.default_metrics if it is None else it.metrics).read_cost_usd_per_mb,
     "${:,.2f}", False, False),
]


def compare_table(run: RunResult, best: IterationResult) -> str:
    """Default vs tailored, with a colored change (green = better, red = worse, plus arrow)."""
    pal = palette()
    rows = []
    for label, get, fmt, higher_better, as_points in COMPARE_ROWS:
        old, cur = get(run, None), get(run, best)
        show_old = "not met" if old is None else fmt.format(old)
        show_cur = "not met" if cur is None else fmt.format(cur)
        if old is None or cur is None:
            delta = ""
        else:
            diff = (cur - old) * 100 if as_points else rel_change(cur, old)
            if diff is None or abs(diff) < (0.05 if as_points else 0.005):
                delta = '<span class="flat">same</span>'
            else:
                good = (diff > 0) == higher_better
                text = f"{abs(diff):.1f} pts" if as_points else f"{abs(diff):.0%}"
                delta = (f'<span style="color:{pal["good"] if good else pal["bad"]}">'
                         f'{"▲" if diff > 0 else "▼"} {text}</span>')
        rows.append(f"<tr><td>{label}</td><td>{show_old}</td><td><b>{show_cur}</b></td><td>{delta}</td></tr>")
    return ('<table class="compare"><thead><tr><th></th><th>Default</th><th>Tailored</th><th>Change</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def verdict(run: RunResult) -> None:
    best = tailored(run)
    fig = pareto_chart(run)
    if fig is not None:
        show(fig, key=f"pareto-{run.situation}")
    else:
        st.subheader(pretty_situation(run.situation))
        st.info("No point with both bits per base and reads needed yet.")
    st.markdown(f'<div class="muted">{html.escape(profile_summary(run))}</div>', unsafe_allow_html=True)
    if best is None:
        st.info("The loop has not finished its first alternation yet.")
        return
    st.markdown(f'<div class="headline">{html.escape(headline(run, best))}</div>', unsafe_allow_html=True)
    changes = setting_changes(run.default_settings, best.settings)
    if changes:
        st.markdown(f'<div class="changes">What the loop changed: {html.escape("; ".join(changes))}.</div>',
                    unsafe_allow_html=True)
    with st.container(border=True):
        d_reads, t_reads = run.default_min_reads_at_target, best.min_reads_at_target
        spark = [y for y in [d_reads] + [it.min_reads_at_target for it in run.iterations] if y is not None]
        delta = None
        if d_reads is not None and t_reads is not None:
            delta = f"{t_reads - d_reads:+.1f} ({rel_change(t_reads, d_reads):+.0%})"
        st.metric(
            "Reads per strand needed",
            "not met" if t_reads is None else f"{t_reads:.1f}",
            delta,
            delta_color="inverse" if delta and abs(t_reads - d_reads) >= 0.05 else "off",
            delta_description=None if d_reads is None else f"vs default {d_reads:.1f}",
            chart_data=spark if len(spark) > 1 else None,
            help=f"Fewest mean reads per strand at which the {target_text(run)}. Sparkline: default, then each "
                 "loop alternation.",
        )
        st.markdown(compare_table(run, best), unsafe_allow_html=True)
        st.caption(f"Target: {target_text(run)}. Tailored = last loop alternation ({best.iteration + 1}). "
                   "* placeholder prices, not quotes.")


# ---------------------------------------------------------------------------
# 2. Crossover matrix
# ---------------------------------------------------------------------------


def crossover_chart(summary: ExperimentSummary) -> go.Figure | None:
    entries = summary.crossover
    if not entries:
        return None
    pal = palette()
    channels = sorted({e.channel for e in entries}, key=situation_rank)
    codecs = ["default"] + sorted({e.codec for e in entries if e.codec != "default"}, key=situation_rank)
    by = {(e.codec, e.channel): e for e in entries}
    z, text = [], []
    for codec in codecs:
        zr, tr = [], []
        for ch in channels:
            e = by.get((codec, ch))
            base = by.get(("default", ch))
            reads = None if e is None else e.min_reads_at_target
            base_reads = None if base is None else base.min_reads_at_target
            if e is None:
                zr.append(None)
                tr.append("")
            elif reads is None:
                zr.append(-1.0)
                tr.append("target<br>not met")
            elif codec == "default" or base_reads is None:
                zr.append(0.0)
                tr.append(f"<b>{reads:.1f}</b> reads<br>default")
            else:
                change = rel_change(reads, base_reads)
                zr.append(-change)  # positive = fewer reads = better
                tr.append(f"<b>{reads:.1f}</b> reads<br>{change:+.0%}")
        z.append(zr)
        text.append(tr)
    finite = [abs(v) for row in z for v in row if v is not None]
    lim = max(0.1, max(finite, default=0.1))
    fig = go.Figure(go.Heatmap(
        z=z, x=[pretty_situation(c) for c in channels], y=[pretty_situation(c) for c in codecs],
        text=text, texttemplate="%{text}", textfont=dict(size=FONT_SIZE + 2),
        colorscale=pal["diverging"], zmid=0, zmin=-lim, zmax=lim, showscale=False, xgap=4, ygap=4,
        hovertemplate="Codec for %{y}<br>on channel %{x}<extra></extra>",
    ))
    # Outline the "home" cells: codec evaluated on the channel it was tailored for.
    for ci, codec in enumerate(codecs):
        if codec in channels:
            xi = channels.index(codec)
            fig.add_shape(type="rect", x0=xi - 0.5, x1=xi + 0.5, y0=ci - 0.5, y1=ci + 0.5,
                          line=dict(color=pal["tailored"], width=4))
    base_layout(fig, height=130 + 110 * len(codecs))
    fig.update_layout(margin=dict(t=40, l=10))
    fig.update_xaxes(title="evaluated on channel", side="top", showgrid=False, tickfont=dict(size=FONT_SIZE + 1))
    fig.update_yaxes(title="codec tailored for", autorange="reversed", showgrid=False,
                     tickfont=dict(size=FONT_SIZE + 1))
    return fig


# ---------------------------------------------------------------------------
# 3. Ablation ladder
# ---------------------------------------------------------------------------


def ablation_chart(summary: ExperimentSummary, situation: str) -> go.Figure | None:
    entries = sorted((e for e in summary.ablation if e.situation == situation), key=lambda e: e.system)
    if not entries:
        return None
    pal = palette()
    colors = {"A": pal["baseline"], "B": pal["default"], "C": pal["ladder"][0], "D": pal["ladder"][1],
              "E": pal["ladder"][2]}
    ys = [e.min_reads_at_target for e in entries]
    top = max([y for y in ys if y is not None], default=1.0)
    reads = {e.system: e.min_reads_at_target for e in entries}
    order = [e.system for e in entries]
    fig = go.Figure(go.Bar(
        x=[f"<b>{e.system}</b><br>{SYSTEMS.get(e.system, '')}" for e in entries],
        y=[top * 1.05 if y is None else y for y in ys],
        marker=dict(color=[colors.get(e.system, pal["muted"]) for e in entries], cornerradius=4,
                    pattern=dict(shape=["/" if y is None else "" for y in ys])),
        text=["not met" if y is None else f"{y:.1f}" for y in ys], textposition="outside", cliponaxis=False,
        textfont=dict(size=FONT_SIZE + 1),
        customdata=[[e.metrics.recovery_rate if e.metrics.recovery_rate is not None else float("nan"),
                     e.metrics.bits_per_base if e.metrics.bits_per_base is not None else float("nan")]
                    for e in entries],
        hovertemplate="%{y:.1f} reads needed<br>recovery at budget %{customdata[0]:.0%}"
                      "<br>%{customdata[1]:.2f} bits per base<extra></extra>",
    ))
    # Mark the two tiers of the claim: B to C (rule audit) and C to D (learned selection).
    for a, b, label in (("B", "C", "tier 1"), ("C", "D", "tier 2")):
        ra, rb = reads.get(a), reads.get(b)
        if a in order and b in order and ra is not None and rb is not None:
            change = rel_change(rb, ra)
            fig.add_annotation(
                x=(order.index(a) + order.index(b)) / 2, y=max(ra, rb) + top * 0.13, showarrow=False,
                text=f"<b>{label}</b><br>{change:+.0%}", font=dict(size=FONT_SIZE + 1, color=pal["tailored"]),
            )
    base_layout(fig, height=440)
    fig.update_layout(title=dict(text=pretty_situation(situation), font=dict(size=FONT_SIZE + 2)), bargap=0.3,
                      showlegend=False)
    fig.update_yaxes(title="reads per strand needed (lower is better)", range=[0, top * 1.3])
    fig.update_xaxes(tickangle=0, tickfont=dict(size=FONT_SIZE - 3))
    return fig


# ---------------------------------------------------------------------------
# 4. What the encoder learned
# ---------------------------------------------------------------------------


def settings_table(runs: list[RunResult]) -> str:
    """Default codec next to each situation's tailored codec, changed rules highlighted."""
    default = runs[0].default_settings
    shown = [(r, tailored(r)) for r in runs]
    head = "<th>Encoder rule</th><th>Default (B)<br><small>same everywhere</small></th>" + "".join(
        f"<th>{html.escape(pretty_situation(r.situation))}<br><small>tailored</small></th>" for r, _ in shown
    )
    rows = []
    for key in setting_keys():
        label, hint = SETTING_LABELS.get(key, (key.replace("_", " "), ""))
        cells = [f'<td class="knob">{html.escape(label)}<small>{html.escape(hint)}</small></td>',
                 f"<td>{html.escape(fmt_setting(default, key))}</td>"]
        for run, best in shown:
            if best is None:
                cells.append('<td class="same">pending</td>')
                continue
            base = fmt_setting(run.default_settings, key)
            value = fmt_setting(best.settings, key)
            cls = "chg" if value != base else "same"
            cells.append(f'<td class="{cls}">{html.escape(value)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    note = ""
    if any(r.default_settings != default for r in runs):
        note = "<p class='muted'>Note: situations used different default settings; the Default column shows the first.</p>"
    return f'<table class="dash"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>{note}'


def risky_chart(run: RunResult, best: IterationResult) -> go.Figure:
    pal = palette()
    kmers = sorted(best.risky_kmers, key=lambda kr: kr[1], reverse=True)[:10][::-1]
    fig = go.Figure(go.Bar(
        x=[r for _, r in kmers], y=[k for k, _ in kmers], orientation="h",
        marker=dict(color=pal["tailored"], cornerradius=4),
        text=[f"{r:.2f}" for _, r in kmers], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: learned risk %{x:.2f}<extra></extra>",
    ))
    base_layout(fig, height=max(220, 48 * len(kmers) + 90))
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2)), bargap=0.35)
    fig.update_xaxes(range=[0, 1.12], title="learned risk of failing (0 to 1)")
    fig.update_yaxes(tickfont=dict(family="ui-monospace, Menlo, Consolas, monospace", size=FONT_SIZE + 2))
    return fig


def examples_table(summary: ExperimentSummary, situations: list[str]) -> str:
    pal = palette()
    sits = [s for s in situations if any(s in e.risk or s in e.accepted for e in summary.examples)]
    sits += sorted({s for e in summary.examples for s in e.risk} - set(sits), key=situation_rank)
    head = "<th>Candidate strand <small>(long letter runs highlighted)</small></th>" + "".join(
        f"<th>{html.escape(pretty_situation(s))}</th>" for s in sits)
    rows = []
    for ex in summary.examples:
        cells = [f'<td class="strand">{highlight_runs(ex.strand)}</td>']
        for s in sits:
            risk = ex.risk.get(s)
            ok = ex.accepted.get(s)
            badge = "" if ok is None else (
                f'<span class="badge" style="color:{pal["good"]}">✓ accepted</span>' if ok
                else f'<span class="badge" style="color:{pal["bad"]}">✗ rejected</span>')
            risk_txt = "" if risk is None else f"risk {risk:.2f}<br>"
            cells.append(f"<td>{risk_txt}{badge}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="dash"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


# ---------------------------------------------------------------------------
# 5. Details: firewall, coverage, per-position error, loop, raw numbers
# ---------------------------------------------------------------------------


def firewall_table(summary: ExperimentSummary) -> str:
    rows = []
    for e in sorted(summary.firewall, key=lambda e: (situation_rank(e.situation), list(FIREWALL_TESTS).index(e.test)
                                                      if e.test in FIREWALL_TESTS else 99)):
        value = f"{e.value:+.0%}" if e.metric == "recovery_rate_gain" else f"{e.value:.2f}" if e.metric == "risk_auc" \
            else f"{e.value:.1f}"
        rows.append(
            f"<tr><td>{html.escape(pretty_situation(e.situation))}</td>"
            f"<td>{html.escape(FIREWALL_TESTS.get(e.test, e.test))}</td>"
            f"<td>{html.escape(FIREWALL_METRICS.get(e.metric, e.metric))}</td><td><b>{value}</b></td>"
            f"<td class='muted'>{html.escape(e.note)}</td></tr>"
        )
    return ('<table class="dash"><thead><tr><th>Channel</th><th>Tested on</th><th>Measure</th><th>Value</th>'
            f'<th>Note</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def coverage_chart(run: RunResult, target: float) -> tuple[go.Figure | None, str | None]:
    pal = palette()
    decoders: dict[str, list[CoveragePoint]] = {}
    for p in run.coverage_curve:
        decoders.setdefault(p.decoder, []).append(p)
    if not decoders:
        return None, None
    extra = iter(pal["extra"])
    colors = {"baseline": pal["baseline"], "transformer": pal["default"], "tailored": pal["tailored"]}
    known = ["baseline", "transformer", "tailored"]
    order = sorted(decoders, key=lambda d: known.index(d) if d in known else 99)
    fig = go.Figure()
    # The dotted target line is explained in the sentence above the chart; no label, so nothing collides.
    fig.add_hline(y=target * 100, line=dict(color=pal["muted"], width=1.5, dash="dot"))
    fig.add_vline(x=run.profile.coverage_mean, line=dict(color=pal["muted"], width=1.5, dash="dash"),
                  annotation_text="read budget", annotation_position="bottom left",
                  annotation_font_size=FONT_SIZE - 1)
    needed = {}
    for name in order:
        pts = sorted(decoders[name], key=lambda p: p.coverage)
        color = colors.get(name) or next(extra, pal["muted"])
        label = DECODER_LABELS.get(name, name)
        fig.add_trace(go.Scatter(
            x=[p.coverage for p in pts], y=[p.strand_accuracy * 100 for p in pts],
            mode="lines+markers", name=label,
            line=dict(color=color, width=4 if name == "tailored" else 2.5), marker=dict(size=9),
            hovertemplate="%{x:g} reads: %{y:.1f}% exact<extra>" + label + "</extra>",
        ))
        need = coverage_needed(pts, target)
        needed[name] = need
        if need is not None:
            pos = {"tailored": "top left", "transformer": "bottom right"}.get(name)
            fig.add_trace(go.Scatter(
                x=[need], y=[target * 100], mode="markers+text" if pos else "markers", showlegend=False,
                marker=dict(size=14, color=color, line=dict(color="white", width=2)),
                text=[f"{need:.1f} reads"], textposition=pos or "top center",
                textfont=dict(size=FONT_SIZE, color=color),
                hovertemplate=f"{label} reaches {target:.0%} at %{{x:.1f}} reads<extra></extra>",
            ))
    base_layout(fig, height=440)
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2), y=0.99),
                      margin=dict(t=120))
    max_x = max([p.coverage for p in run.coverage_curve] + [run.profile.coverage_mean])
    fig.update_xaxes(title="reads per strand", range=[0, max_x * 1.04])
    fig.update_yaxes(title="strands read exactly (%)", range=[0, 102])

    # The comparison default is B (same decoder); fall back to the baseline if B is missing.
    reference = next((d for d in ("transformer", "baseline") if d in needed), None)
    msg = None
    if "tailored" in needed and reference:
        a, b = needed["tailored"], needed[reference]
        if a is not None and b is not None and b > 0:
            msg = (f"To read {target:.0%} of strands exactly, the tailored codec needs **{a:.1f} reads** "
                   f"instead of {b:.1f} with the same decoder: **{1 - a / b:.0%} fewer**.")
        elif a is not None:
            msg = f"Only the tailored codec reaches {target:.0%} ({a:.1f} reads)."
        else:
            msg = f"The tailored codec does not reach {target:.0%} in the measured range."
    return fig, msg


def position_chart(run: RunResult) -> go.Figure:
    pal = palette()
    rows = [("default", run.default_metrics.per_position_error)]
    rows += [(f"alternation {it.iteration + 1}", it.metrics.per_position_error) for it in run.iterations]
    width = max((len(r) for _, r in rows), default=0)
    z = [[v * 100 for v in r] + [None] * (width - len(r)) for _, r in rows]
    fig = go.Figure(go.Heatmap(
        z=z, x=list(range(1, width + 1)), y=[name for name, _ in rows],
        colorscale=pal["heat"], zmin=0, zmax=max([v for row in z for v in row if v is not None] + [1e-9]),
        colorbar=dict(title=dict(text="% wrong", side="right"), thickness=14),
        hovertemplate="%{y}, letter %{x}: %{z:.2f}% wrong<extra></extra>", ygap=2,
    ))
    base_layout(fig, height=max(240, 38 * len(rows) + 130))
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2)))
    fig.update_xaxes(title="position along the strand (letter number)", showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
    return fig


def loop_chart(runs: list[RunResult], metric: str) -> go.Figure:
    """Small multiples, one panel per channel: the metric per alternation vs the default."""
    pal = palette()

    def get(run: RunResult, it: IterationResult | None):
        if metric == "min_reads_at_target":
            return run.default_min_reads_at_target if it is None else it.min_reads_at_target
        m = run.default_metrics if it is None else it.metrics
        v = getattr(m, metric)
        return None if v is None else v * 100 if metric == "recovery_rate" else v

    fig = make_subplots(rows=1, cols=len(runs), horizontal_spacing=0.06,
                        subplot_titles=[pretty_situation(r.situation) for r in runs])
    for col, run in enumerate(runs, start=1):
        xs = [it.iteration + 1 for it in run.iterations]
        ys = [get(run, it) for it in run.iterations]
        dflt = get(run, None)
        first = col == 1
        if dflt is not None:
            fig.add_trace(go.Scatter(
                x=[0], y=[dflt], mode="markers", marker=dict(symbol="diamond", size=16, color=pal["default"]),
                name="Default (B)", legendgroup="d", showlegend=first, hovertemplate="Default: %{y:.2f}<extra></extra>",
            ), row=1, col=col)
        fig.add_trace(go.Scatter(
            x=([0] if dflt is not None else []) + xs, y=([dflt] if dflt is not None else []) + ys,
            mode="lines+markers", line=dict(color=pal["tailored"], width=3), marker=dict(size=10),
            name="Tailored, per alternation", legendgroup="t", showlegend=first,
            hovertemplate="Alternation %{x}: %{y:.2f}<extra></extra>",
        ), row=1, col=col)
        fig.update_xaxes(title_text="loop alternation (0 = default)", dtick=1, row=1, col=col)
    titles = {"min_reads_at_target": "reads needed", "bits_per_base": "bits per base",
              "recovery_rate": "file recovered at budget (%)"}
    base_layout(fig, height=380)
    fig.update_layout(margin=dict(t=110), legend=dict(y=1.16))
    fig.update_annotations(font_size=FONT_SIZE + 1)
    fig.update_yaxes(title_text=titles.get(metric, metric), row=1, col=1)
    return fig


def iteration_table(run: RunResult) -> pd.DataFrame:
    def row(label: str, s: EncoderSettings, m: Metrics, reads: float | None, notes: str = "") -> dict:
        return {
            "codec": label,
            "reads needed": reads,
            "bits/base": m.bits_per_base,
            "recovery rate": m.recovery_rate,
            "trials": m.n_trials,
            "strands exact": m.strand_accuracy,
            "reads/strand": m.reads_per_strand,
            "lost strands": m.dropout_rate,
            "write $/MB*": m.write_cost_usd_per_mb,
            "read $/MB*": m.read_cost_usd_per_mb,
            **{SETTING_LABELS.get(k, (k,))[0]: fmt_setting(s, k) for k in setting_keys()},
            "notes": notes,
        }

    rows = [row("default (B)", run.default_settings, run.default_metrics, run.default_min_reads_at_target)]
    rows += [row(f"alternation {it.iteration + 1}", it.settings, it.metrics, it.min_reads_at_target, it.notes)
             for it in run.iterations]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def render_body(run_id: str, focus: str, target: float) -> None:
    runs, summary, errors = load(run_id)
    for err in errors:
        st.error(f"Could not read results/{run_id}: {err}")
    if not runs and summary is None:
        st.warning(f"No result files in results/{run_id} yet.")
        return
    if focus != "All situations":
        runs = [r for r in runs if r.situation == focus] or runs
    if any(r.is_mock for r in runs) or (summary is not None and summary.is_mock):
        mock_banner()

    st.markdown('<div class="overline">Adaptive DNA codec · HackMIT 2026</div>', unsafe_allow_html=True)
    st.title(HEADLINE)
    st.markdown(
        '<p class="lede">DNA storage pipelines copy the same hand-written rules to every sequencing channel. '
        "We switch each rule on and off, measure what it costs and what it buys, and keep only what pays off. "
        "Then decoder failures teach the encoder what else to avoid. Same encoder, <b>same decoder</b>.</p>",
        unsafe_allow_html=True,
    )
    glossary()

    audit = list(getattr(summary, "rule_audit", None) or [])
    if focus != "All situations":
        audit = [e for e in audit if e.situation == focus]
    shown = [r.situation for r in runs] or sorted(
        {e.situation for e in audit} | {e.situation for e in getattr(summary, "ablation", [])}, key=situation_rank)
    audit_sits = [s for s in shown if any(e.situation == s for e in audit)] or sorted(
        {e.situation for e in audit}, key=situation_rank)

    if audit:
        st.header("1. Does each rule pay off on this channel?")
        st.caption("Each rule is measured on the default codec with the same decoder: switched off (or replaced by "
                   "a tuned value) with everything else unchanged. Reads needed = reads per strand to recover the "
                   "file every time.")
        contrast = audit_contrast(audit, audit_sits)
        if contrast:
            st.markdown(f'<div class="contrast">{html.escape(contrast)}</div>', unsafe_allow_html=True)
        st.markdown(rule_audit_table(audit, audit_sits), unsafe_allow_html=True)

    if runs:
        st.header("2. The tuned codec moves past the default")
        st.caption("Each panel is one channel. Gray diamond = default codec, blue dots = tuning steps. "
                   "Down and right is better: fewer reads to recover the file, more data per letter.")
        in_columns(runs, verdict)

    if summary is not None and summary.crossover:
        st.header("3. Each codec wins at home, not away")
        st.caption("Reads per strand each codec needs to recover the file, on each channel. Blue = fewer reads "
                   "than the default on that channel, red = more. Outlined cells: the codec on its own channel.")
        fig = crossover_chart(summary)
        cols = st.columns([3, 1]) if len(shown) <= 2 else [st.container()]
        with cols[0]:
            show(fig, key="crossover")

    if summary is not None and summary.ablation:
        st.header("4. Where the gain comes from")
        st.caption("A and B use the same fixed rules; B swaps in the AI decoder and is the default we compare "
                   "against. Tier 1 (B to C): rules audited and redundancy tuned, same decoder. Tier 2 (C to D): "
                   "a risk model learned from decoder failures picks the candidates. E re-tunes the decoder too.")
        sits = [s for s in shown if any(e.situation == s for e in summary.ablation)] or sorted(
            {e.situation for e in summary.ablation}, key=situation_rank)
        for start in range(0, len(sits), 3):
            chunk = sits[start:start + 3]
            for col, s in zip(st.columns(len(chunk), gap="large"), chunk):
                with col:
                    show(ablation_chart(summary, s), key=f"ablation-{s}")

    if runs:
        st.header("5. What the encoder learned")
        st.caption("Short DNA patterns each channel's risk model learned to fear. The encoder steers around them.")

        def render_risky(run: RunResult) -> None:
            best = tailored(run)
            if best is None or not best.risky_kmers:
                st.info(f"{pretty_situation(run.situation)}: no risky patterns logged yet.")
                return
            show(risky_chart(run, best), key=f"risky-{run.situation}")

        in_columns(runs, render_risky)
        if summary is not None and summary.examples:
            st.subheader("Same strand, different verdict per channel")
            st.markdown(examples_table(summary, shown), unsafe_allow_html=True)
        st.subheader("Encoder settings per channel")
        st.caption("Blue cells: rules the loop changed from the default for that channel.")
        st.markdown(settings_table(runs), unsafe_allow_html=True)

    st.header("6. Details")
    if summary is not None and summary.firewall:
        st.subheader("Does it hold outside our own simulator?")
        st.caption("Tuned on our simulator only. Checked on unseen seeds, a differently built simulator, and real reads.")
        st.markdown(firewall_table(summary), unsafe_allow_html=True)

    if runs:
        st.subheader("Strand accuracy vs reads per strand")

        def render_cov(run: RunResult) -> None:
            fig, msg = coverage_chart(run, target)
            if fig is None:
                st.info(f"{pretty_situation(run.situation)}: no coverage curve yet.")
                return
            if msg:
                st.markdown(msg)
            show(fig, key=f"cov-{run.situation}")

        in_columns(runs, render_cov)

        st.subheader("Where along the strand errors happen")
        st.caption("Darker = more letters decoded wrong at that position. Lower rows are later loop alternations.")

        def render_pos(run: RunResult) -> None:
            if not run.default_metrics.per_position_error and not run.iterations:
                st.info("No per-position data.")
                return
            show(position_chart(run), key=f"pos-{run.situation}")

        in_columns(runs, render_pos)

        if any(r.iterations for r in runs):
            st.subheader("The loop, alternation by alternation")
            show(loop_chart(runs, "min_reads_at_target"), key="loop-reads")
            show(loop_chart(runs, "bits_per_base"), key="loop-bits")

        with st.expander("Raw numbers per channel"):
            for run in runs:
                st.markdown(f"**{pretty_situation(run.situation)}** ({run.situation}): "
                            f"{html.escape(run.profile.description)}. Target: {target_text(run)}. "
                            f"Created {run.created_at}. Profile calibrated from: "
                            f"{run.profile.calibrated_from or 'not calibrated (guessed)'}.")
                st.dataframe(iteration_table(run), hide_index=True)
            st.caption("* cost columns use placeholder prices.")

    st.caption(f"Run `{run_id}` · {len(runs)} channel(s) · refreshed {datetime.now():%H:%M:%S}")


def main() -> None:
    st.set_page_config(page_title="Adaptive DNA codec", page_icon="🧬", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    run_ids = list_run_ids()
    with st.sidebar:
        st.header("Controls")
        if not run_ids:
            st.warning("No runs in results/. Generate mock data with "
                       "`uv run python -m scripts.make_mock_results`.")
            st.stop()
        # Prefer the newest real run, fall back to mock.
        default_idx = next((i for i, r in enumerate(run_ids) if r != "mock"), 0)
        run_id = st.selectbox("Run", run_ids, index=default_idx,
                              format_func=lambda r: f"{r}  (MOCK)" if r == "mock" else r)
        runs, _, _ = load(run_id)
        focus = st.selectbox("Channel", ["All situations", *[r.situation for r in runs]],
                             format_func=lambda s: "All channels" if s == "All situations" else pretty_situation(s))
        target = st.slider("Strand accuracy target (coverage chart)", 0.50, 0.99, 0.90, 0.01, format="%.2f")
        live = st.toggle("Live mode (auto-refresh)", value=False,
                         help="Re-reads the result files so the loop can be watched while it runs.")
        interval = st.select_slider("Refresh every (seconds)", [5, 10, 20, 30, 60], value=10, disabled=not live)
        st.divider()
        st.caption("Gray = the default codec (B: fixed rules, same decoder). Blue = the codec tailored to the channel.")

    if live:
        st.fragment(render_body, run_every=interval)(run_id, focus, target)
    else:
        render_body(run_id, focus, target)


if __name__ == "__main__":
    main()
