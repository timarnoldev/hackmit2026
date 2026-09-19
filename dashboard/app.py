"""Adaptive DNA codec dashboard.

Run with:  uv run streamlit run dashboard/app.py

Reads only result files through dnacodec.results.load_runs. Never imports training code.
The one point it has to make obvious: every storage situation gets its own tailored
codec, and that codec beats the one-size-fits-all default.
"""

from __future__ import annotations

import dataclasses
import html
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dnacodec.results import RESULTS_DIR, CoveragePoint, IterationResult, RunResult, load_runs
from dnacodec.types import EncoderSettings, Metrics

# ---------------------------------------------------------------------------
# Palette. Gray = the old one-size-fits-all way, blue = our tailored codec.
# Steps come from a CVD-validated categorical palette, with separate light/dark steps.
# ---------------------------------------------------------------------------

PALETTES = {
    "light": {
        "tailored": "#2a78d6",
        "default": "#7a7974",
        "transformer": "#eb6834",
        "extra": ["#1baf7a", "#4a3aa7", "#e87ba4", "#008300"],
        "grid": "rgba(0,0,0,0.08)",
        "muted": "#52514e",
        "good": "#0f7a3d",
        "bad": "#c2362f",
        "heat": [[0.0, "#fcfcfb"], [0.2, "#cde2fb"], [0.5, "#6da7ec"], [0.8, "#256abf"], [1.0, "#0d366b"]],
    },
    "dark": {
        "tailored": "#3987e5",
        "default": "#a3a29b",
        "transformer": "#d95926",
        "extra": ["#199e70", "#9085e9", "#d55181", "#008300"],
        "grid": "rgba(255,255,255,0.10)",
        "muted": "#c3c2b7",
        "good": "#4cc38a",
        "bad": "#f07470",
        "heat": [[0.0, "#1a1a19"], [0.25, "#184f95"], [0.6, "#3987e5"], [1.0, "#b7d3f6"]],
    },
}

FONT_SIZE = 16

NICE_SITUATION_NAMES = {
    "nanopore_budget": "Nanopore, tight budget",
    "illumina_standard": "Illumina, standard lab",
    "illumina_archive_100y": "Illumina, 100-year archive",
}

DECODER_LABELS = {
    "baseline": "Baseline decoder (majority vote)",
    "transformer": "AI decoder, generic",
    "tailored": "Tailored codec",
}

# Plain-language names for encoder knobs. Unknown fields fall back to their raw name.
SETTING_LABELS = {
    "redundancy": ("Spare strands", "extra strands so the file survives lost DNA"),
    "max_homopolymer": ("Longest allowed letter run", "e.g. AAAA; long runs are hard to read"),
    "gc": ("Allowed G+C share", "strands too rich or poor in G/C get lost"),
    "strand_length": ("Letters per strand", "longer strands hold more data but break more"),
    "seed_bases": ("Index letters per strand", "address of the strand, carries no data"),
    "candidates_per_strand": ("Candidates tried per strand", "the risk model keeps the safest one"),
    "risk_threshold": ("Risk cut-off", "candidates rated riskier than this are rejected"),
}


def pretty_situation(name: str) -> str:
    return NICE_SITUATION_NAMES.get(name, name.replace("_", " ").capitalize())


def palette() -> dict:
    theme_type = None
    try:
        theme_type = st.context.theme.type
    except Exception:  # older Streamlit or no browser context (tests)
        pass
    return PALETTES["dark" if theme_type == "dark" else "light"]


# ---------------------------------------------------------------------------
# Data access. Only through load_runs.
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


def load(run_id: str) -> tuple[list[RunResult], str | None]:
    """Load a run. Retries briefly because the loop may be writing a file right now."""
    error = None
    for attempt in range(3):
        try:
            return load_runs(run_id), None
        except Exception as exc:  # partially written JSON, format drift
            error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.3 * (attempt + 1))
    return [], error


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
            return "no limit"
        lo = "0" if settings.gc_min is None else f"{settings.gc_min * 100:.0f}"
        hi = "100" if settings.gc_max is None else f"{settings.gc_max * 100:.0f}"
        return f"{lo} to {hi}%"
    value = getattr(settings, key)
    if value is None:
        return "no limit"
    if key == "redundancy":
        return f"{value * 100:.0f}%"
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


def strictness(default: EncoderSettings, new: EncoderSettings) -> int:
    """Positive = the loop tightened the hand-written rules, negative = it relaxed them."""
    score = 0

    def run_limit(s: EncoderSettings) -> float:
        return float("inf") if s.max_homopolymer is None else s.max_homopolymer

    def gc_width(s: EncoderSettings) -> float:
        return (1.0 if s.gc_max is None else s.gc_max) - (0.0 if s.gc_min is None else s.gc_min)

    a, b = run_limit(default), run_limit(new)
    score += (a > b) - (a < b)
    a, b = round(gc_width(default), 6), round(gc_width(new), 6)
    score += (a > b) - (a < b)
    return score


def headline(run: RunResult, best: IterationResult) -> str:
    d, t = run.default_metrics, best.metrics
    wins = []
    acc_pts = (t.strand_accuracy - d.strand_accuracy) * 100
    if acc_pts >= 0.5:
        wins.append(f"{acc_pts:+.0f} pts more strands read back exactly")
    dens = rel_change(t.bits_per_base, d.bits_per_base)
    if dens is not None and dens >= 0.01:
        wins.append(f"{dens:+.0%} more data per letter")
    reads = rel_change(t.reads_per_strand, d.reads_per_strand)
    if reads is not None and reads <= -0.01:
        wins.append(f"{-reads:.0%} fewer reads")
    if not wins:
        return "No gain over the default yet."
    return "Beats the default: " + ", ".join(wins) + "."


def profile_summary(run: RunResult) -> str:
    p = run.profile
    err = p.sub_rate + p.ins_rate + p.del_rate
    return (
        f"{p.technology.capitalize()} · about {p.coverage_mean:g} reads per strand · "
        f"stored {p.storage_years:g} yr · {err:.1%} letter errors · {p.total_dropout:.0%} strands lost"
    )


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------

CSS = """
<style>
html { font-size: 18px; }
.block-container { padding-top: 2.2rem; max-width: 1700px; }
h1 { font-size: 2.6rem !important; letter-spacing: -0.01em; }
h2 { font-size: 1.9rem !important; margin-top: 1.4rem !important; }
h3 { font-size: 1.35rem !important; }
[data-testid="stMetricValue"] { font-size: 2.2rem; font-weight: 650; }
[data-testid="stMetricLabel"] p { font-size: 1.0rem; font-weight: 600; }
[data-testid="stMetricDelta"] { font-size: 1.0rem; }
.mock-banner {
  background: #c62828; color: #ffffff; padding: 0.9rem 1.2rem; border-radius: 10px;
  font-size: 1.6rem; font-weight: 800; text-align: center; letter-spacing: 0.04em;
  margin: 0.4rem 0 1rem 0; border: 3px solid #8e0000;
}
.mock-banner small { display: block; font-size: 0.95rem; font-weight: 500; letter-spacing: 0; }
.lede { font-size: 1.3rem; opacity: 0.85; margin-top: -0.6rem; }
.gloss { font-size: 1.0rem; line-height: 1.45; opacity: 0.9; }
.gloss b { font-size: 1.05rem; }
.headline { font-size: 1.15rem; font-weight: 650; margin: 0.2rem 0 0.6rem 0; }
.muted { opacity: 0.7; font-size: 0.95rem; }
table.settings { width: 100%; border-collapse: collapse; font-size: 1.1rem; }
table.settings th, table.settings td {
  padding: 0.55rem 0.8rem; border-bottom: 1px solid rgba(128,128,128,0.25); text-align: left;
  vertical-align: top;
}
table.settings th { font-size: 1.1rem; }
table.settings td.knob small { display: block; opacity: 0.65; font-size: 0.85rem; }
table.settings td.chg { background: rgba(57,135,229,0.20); font-weight: 700; }
table.settings td.same { opacity: 0.55; }
table.settings tr.result td { font-weight: 650; border-top: 2px solid rgba(128,128,128,0.45); }
table.compare { width: 100%; border-collapse: collapse; font-size: 1.05rem; margin: 0.4rem 0 0.3rem 0; }
table.compare th { text-align: right; font-size: 0.9rem; opacity: 0.7; font-weight: 600; padding: 0.2rem 0.4rem; }
table.compare td { text-align: right; padding: 0.35rem 0.4rem; border-top: 1px solid rgba(128,128,128,0.22);
  white-space: nowrap; font-variant-numeric: tabular-nums; }
table.compare td:first-child, table.compare th:first-child { text-align: left; white-space: normal; }
table.compare td:nth-child(4) { font-weight: 700; }
table.compare .flat { opacity: 0.6; font-weight: 500; }
.tag { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px; font-size: 0.9rem;
  font-weight: 650; border: 1.5px solid currentColor; }
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
    fig.update_xaxes(gridcolor=pal["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=pal["grid"], zeroline=False)
    return fig


def show(fig: go.Figure, key: str) -> None:
    st.plotly_chart(fig, key=key, config={"displayModeBar": False})


def mock_banner() -> None:
    st.markdown(
        '<div class="mock-banner">MOCK DATA: NOT REAL RESULTS'
        "<small>These numbers come from scripts/make_mock_results.py and exist only to build the dashboard.</small></div>",
        unsafe_allow_html=True,
    )


def glossary() -> None:
    cols = st.columns(4)
    items = [
        ("Strand", "one short piece of DNA, about 110 letters (A, C, G, T). A file is split over thousands."),
        ("Coverage", "how many noisy copies (reads) of each strand we read back. More reads cost more."),
        ("Density", "bits of data stored per DNA letter. The maximum is 2. Higher is better."),
        ("Codec", "the rules for writing data into DNA plus the decoder that reads it back."),
    ]
    for col, (term, text) in zip(cols, items):
        col.markdown(f'<div class="gloss"><b>{term}</b>: {text}</div>', unsafe_allow_html=True)


def verdict_card(run: RunResult) -> None:
    best = tailored(run)
    with st.container(border=True):
        st.subheader(pretty_situation(run.situation))
        st.markdown(f'<div class="muted">{html.escape(profile_summary(run))}</div>', unsafe_allow_html=True)
        if best is None:
            st.info("The loop has not finished its first iteration yet.")
            return
        st.markdown(f'<div class="headline">{html.escape(headline(run, best))}</div>', unsafe_allow_html=True)
        d, t = run.default_metrics, best.metrics
        spark = [it.metrics.strand_accuracy * 100 for it in run.iterations]

        acc_delta = (t.strand_accuracy - d.strand_accuracy) * 100
        st.metric(
            "Strands read back exactly",
            f"{t.strand_accuracy:.1%}",
            f"{acc_delta:+.1f} pts",
            delta_color="normal" if abs(acc_delta) >= 0.05 else "off",
            delta_description=f"vs default {d.strand_accuracy:.1%}",
            chart_data=spark if len(spark) > 1 else None,
            help="Share of strands the decoder reconstructs letter-perfect. Lost strands count as wrong.",
            border=True,
        )
        st.markdown(compare_table(d, t), unsafe_allow_html=True)
        recovered = {True: "yes", False: "no", None: "not tested"}[t.file_recovered]
        st.caption(f"Tailored = best loop iteration ({best.iteration}). Whole test file recovered: {recovered}. "
                   "* placeholder prices.")


# (label, Metrics field, format, higher is better)
COMPARE_ROWS = [
    ("Bits per letter", "bits_per_base", "{:.2f}", True),
    ("Reads per strand", "reads_per_strand", "{:.1f}", False),
    ("Write cost per MB*", "write_cost_usd_per_mb", "${:,.0f}", False),
    ("Read cost per MB*", "read_cost_usd_per_mb", "${:,.2f}", False),
]


def compare_table(default: Metrics, new: Metrics) -> str:
    """Default vs tailored for the secondary metrics, with a colored relative change."""
    pal = palette()
    rows = []
    for label, key, fmt, higher_better in COMPARE_ROWS:
        old, cur = getattr(default, key), getattr(new, key)
        show_old = "n/a" if old is None else fmt.format(old)
        show_cur = "n/a" if cur is None else fmt.format(cur)
        change = rel_change(cur, old)
        if change is None:
            delta = ""
        elif abs(change) < 0.005:
            delta = '<span class="flat">same</span>'
        else:
            good = (change > 0) == higher_better
            color = pal["good"] if good else pal["bad"]
            delta = f'<span style="color:{color}">{"▲" if change > 0 else "▼"} {abs(change):.0%}</span>'
        rows.append(f"<tr><td>{label}</td><td>{show_old}</td><td><b>{show_cur}</b></td><td>{delta}</td></tr>")
    return ('<table class="compare"><thead><tr><th></th><th>Default</th><th>Tailored</th><th>Change</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table>')


def settings_table(runs: list[RunResult]) -> str:
    """The key view: default codec next to each situation's tailored codec, changes highlighted."""
    default = runs[0].default_settings
    shown = [(r, tailored(r)) for r in runs]
    head = "<th>Encoder rule</th><th>Default<br><small>one size fits all</small></th>" + "".join(
        f"<th>{html.escape(pretty_situation(r.situation))}<br><small>tailored</small></th>" for r, _ in shown
    )
    rows = []
    for key in setting_keys():
        label, hint = SETTING_LABELS.get(key, (key.replace("_", " "), ""))
        cells = [f'<td class="knob">{html.escape(label)}<small>{html.escape(hint)}</small></td>',
                 f"<td>{fmt_setting(default, key)}</td>"]
        for run, best in shown:
            if best is None:
                cells.append('<td class="same">pending</td>')
                continue
            base = fmt_setting(run.default_settings, key)
            value = fmt_setting(best.settings, key)
            cls = "chg" if value != base else "same"
            cells.append(f'<td class="{cls}">{value}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    def result_row(label: str, getter) -> str:
        cells = [f'<td class="knob">{label}</td>', f"<td>{html.escape(getter(runs[0].default_metrics, runs[0]))}</td>"]
        for run, best in shown:
            cells.append("<td>pending</td>" if best is None else f"<td>{html.escape(getter(best.metrics, run))}</td>")
        return '<tr class="result">' + "".join(cells) + "</tr>"

    direction = ['<td class="knob">What the loop did</td>', "<td></td>"]
    for run, best in shown:
        if best is None:
            direction.append("<td></td>")
            continue
        s = strictness(run.default_settings, best.settings)
        tag = "stricter rules" if s > 0 else "looser rules" if s < 0 else "same rules"
        direction.append(f'<td><span class="tag">{tag}</span></td>')
    rows.append('<tr class="result">' + "".join(direction) + "</tr>")
    rows.append(result_row("Strands read back exactly", lambda m, r: f"{m.strand_accuracy:.1%}"))
    rows.append(result_row(
        "Density (bits per letter)", lambda m, r: "n/a" if m.bits_per_base is None else f"{m.bits_per_base:.2f}"))
    note = ""
    if any(r.default_settings != default for r in runs):
        note = "<p class='muted'>Note: situations used different default settings; the Default column shows the first.</p>"
    return f'<table class="settings"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>{note}'


def risky_chart(run: RunResult, best: IterationResult) -> go.Figure:
    pal = palette()
    kmers = sorted(best.risky_kmers, key=lambda kr: kr[1], reverse=True)[:10][::-1]
    fig = go.Figure(go.Bar(
        x=[r for _, r in kmers], y=[k for k, _ in kmers], orientation="h",
        marker=dict(color=pal["tailored"], cornerradius=4),
        text=[f"{r:.2f}" for _, r in kmers], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: risk %{x:.2f}<extra></extra>",
    ))
    base_layout(fig, height=max(220, 48 * len(kmers) + 70))
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2)), bargap=0.35)
    fig.update_xaxes(range=[0, 1.12], title="learned risk of failing (0 to 1)")
    fig.update_yaxes(tickfont=dict(family="ui-monospace, Menlo, Consolas, monospace", size=FONT_SIZE + 2))
    return fig


def loop_chart(runs: list[RunResult], metric: str) -> go.Figure:
    pal = palette()
    fig = make_subplots(
        rows=1, cols=len(runs), shared_yaxes=True, horizontal_spacing=0.04,
        subplot_titles=[pretty_situation(r.situation) for r in runs],
    )
    pct = metric == "strand_accuracy"
    for col, run in enumerate(runs, start=1):
        xs = [it.iteration for it in run.iterations]
        ys = [getattr(it.metrics, metric) for it in run.iterations]
        ys = [None if y is None else (y * 100 if pct else y) for y in ys]
        dflt = getattr(run.default_metrics, metric)
        dflt = None if dflt is None else (dflt * 100 if pct else dflt)
        first = col == 1
        if dflt is not None and xs:
            fig.add_trace(go.Scatter(
                x=[min(xs), max(xs)] if len(xs) > 1 else [xs[0] - 0.5, xs[0] + 0.5], y=[dflt, dflt],
                mode="lines", line=dict(color=pal["default"], width=2, dash="dash"),
                name="Default codec", legendgroup="d", showlegend=first,
                hovertemplate="Default: %{y:.2f}<extra></extra>",
            ), row=1, col=col)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers", line=dict(color=pal["tailored"], width=3),
            marker=dict(size=10), name="Tailored codec, per loop iteration", legendgroup="t", showlegend=first,
            hovertemplate="Iteration %{x}: %{y:.2f}<extra></extra>",
        ), row=1, col=col)
        best = tailored(run)
        if best is not None and getattr(best.metrics, metric) is not None:
            by = getattr(best.metrics, metric) * (100 if pct else 1)
            fig.add_trace(go.Scatter(
                x=[best.iteration], y=[by], mode="markers",
                marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(color=pal["tailored"], width=3)),
                name="Best iteration (the tailored codec)", legendgroup="b", showlegend=first,
                hoverinfo="skip",
            ), row=1, col=col)
        fig.update_xaxes(title_text="loop iteration", dtick=1, row=1, col=col)
    base_layout(fig, height=400)
    fig.update_layout(margin=dict(t=110), legend=dict(y=1.14))
    fig.update_annotations(font_size=FONT_SIZE + 1)
    fig.update_yaxes(title_text="strands exact (%)" if pct else "bits per letter", row=1, col=1)
    return fig


def coverage_chart(run: RunResult, target: float) -> tuple[go.Figure | None, str | None]:
    pal = palette()
    decoders: dict[str, list[CoveragePoint]] = {}
    for p in run.coverage_curve:
        decoders.setdefault(p.decoder, []).append(p)
    if not decoders:
        return None, None
    extra = iter(pal["extra"])
    colors = {"baseline": pal["default"], "transformer": pal["transformer"], "tailored": pal["tailored"]}
    order = sorted(decoders, key=lambda d: ["baseline", "transformer", "tailored"].index(d)
                   if d in colors else 99)
    fig = go.Figure()
    fig.add_hline(y=target * 100, line=dict(color=pal["muted"], width=1.5, dash="dot"),
                  annotation_text=f"target {target:.0%}", annotation_position="top left",
                  annotation_font_size=FONT_SIZE - 1)
    fig.add_vline(x=run.profile.coverage_mean, line=dict(color=pal["muted"], width=1.5, dash="dash"),
                  annotation_text="reading budget", annotation_position="bottom left",
                  annotation_font_size=FONT_SIZE - 1)
    needed = {}
    for name in order:
        pts = sorted(decoders[name], key=lambda p: p.coverage)
        color = colors.get(name) or next(extra, pal["muted"])
        width = 4 if name == "tailored" else 2.5
        fig.add_trace(go.Scatter(
            x=[p.coverage for p in pts], y=[p.strand_accuracy * 100 for p in pts],
            mode="lines+markers", name=DECODER_LABELS.get(name, name),
            line=dict(color=color, width=width), marker=dict(size=9),
            hovertemplate="%{x:g} reads: %{y:.1f}% exact<extra>" + DECODER_LABELS.get(name, name) + "</extra>",
        ))
        need = coverage_needed(pts, target)
        needed[name] = need
        if need is not None:
            # Label only the two ends of the story (tailored vs baseline) so labels never collide.
            label = {"tailored": "top left", "baseline": "bottom right"}.get(name)
            fig.add_trace(go.Scatter(
                x=[need], y=[target * 100], mode="markers+text" if label else "markers", showlegend=False,
                marker=dict(size=14, color=color, line=dict(color="white", width=2)),
                text=[f"{need:.1f} reads"], textposition=label or "top center",
                textfont=dict(size=FONT_SIZE, color=color),
                hovertemplate=f"{DECODER_LABELS.get(name, name)} reaches {target:.0%} at %{{x:.1f}} reads<extra></extra>",
            ))
    base_layout(fig, height=420)
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2), y=0.99),
                      margin=dict(t=110))
    max_x = max([p.coverage for p in run.coverage_curve] + [run.profile.coverage_mean])
    fig.update_xaxes(title="reads per strand (coverage)", range=[0, max_x * 1.04])
    fig.update_yaxes(title="strands exact (%)", range=[0, 102])

    reference = "baseline" if "baseline" in needed else order[0]
    msg = None
    if "tailored" in needed and reference != "tailored":
        a, b = needed["tailored"], needed[reference]
        if a is not None and b is not None and b > 0:
            msg = (f"To read {target:.0%} of strands exactly, the tailored codec needs **{a:.1f} reads** "
                   f"instead of {b:.1f}: **{1 - a / b:.0%} fewer reads**.")
        elif a is not None:
            msg = f"Only the tailored codec reaches {target:.0%} ({a:.1f} reads)."
        else:
            msg = f"No decoder reaches {target:.0%} in the measured range."
    return fig, msg


def position_chart(run: RunResult) -> go.Figure:
    pal = palette()
    rows = [("default", run.default_metrics.per_position_error)]
    rows += [(f"iter {it.iteration}", it.metrics.per_position_error) for it in run.iterations]
    width = max((len(r) for _, r in rows), default=0)
    z = [[v * 100 for v in r] + [None] * (width - len(r)) for _, r in rows]
    fig = go.Figure(go.Heatmap(
        z=z, x=list(range(1, width + 1)), y=[name for name, _ in rows],
        colorscale=pal["heat"], zmin=0, colorbar=dict(title=dict(text="% wrong", side="right"), thickness=14),
        hovertemplate="%{y}, letter %{x}: %{z:.2f}% wrong<extra></extra>", xgap=0, ygap=2,
    ))
    base_layout(fig, height=max(240, 34 * len(rows) + 120))
    fig.update_layout(title=dict(text=pretty_situation(run.situation), font=dict(size=FONT_SIZE + 2)))
    fig.update_xaxes(title="position along the strand (letter number)", showgrid=False)
    fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
    return fig


def iteration_table(run: RunResult) -> pd.DataFrame:
    def row(label: str, s: EncoderSettings, m: Metrics, notes: str = "") -> dict:
        return {
            "codec": label,
            "strands exact": m.strand_accuracy,
            "bits/letter": m.bits_per_base,
            "reads/strand": m.reads_per_strand,
            "lost strands": m.dropout_rate,
            "edit distance": m.mean_edit_distance,
            "file recovered": m.file_recovered,
            "write $/MB": m.write_cost_usd_per_mb,
            "read $/MB": m.read_cost_usd_per_mb,
            **{SETTING_LABELS.get(k, (k,))[0]: fmt_setting(s, k) for k in setting_keys()},
            "notes": notes,
        }

    rows = [row("default", run.default_settings, run.default_metrics)]
    rows += [row(f"iteration {it.iteration}", it.settings, it.metrics, it.notes) for it in run.iterations]
    return pd.DataFrame(rows)


def in_columns(runs: list[RunResult], render) -> None:
    """Render one block per situation side by side (at most 3 per row)."""
    for start in range(0, len(runs), 3):
        chunk = runs[start:start + 3]
        cols = st.columns(len(chunk), gap="medium")
        for col, run in zip(cols, chunk):
            with col:
                render(run)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def render_body(run_id: str, focus: str, target: float) -> None:
    runs, error = load(run_id)
    if error:
        st.error(f"Could not read results/{run_id}: {error}")
    if not runs:
        st.warning(f"No result files in results/{run_id} yet.")
        return
    if focus != "All situations":
        runs = [r for r in runs if r.situation == focus] or runs
    if any(r.is_mock for r in runs):
        mock_banner()

    st.title("Adaptive DNA codec")
    st.markdown(
        '<p class="lede">Every storage situation gets its own tailored codec, '
        "and it beats the one-size-fits-all default.</p>",
        unsafe_allow_html=True,
    )
    glossary()

    st.header("1. The result: tailored codec vs default")
    in_columns(runs, verdict_card)
    st.caption("Cost numbers use placeholder prices and are only useful for comparing codecs, not as quotes.")

    st.header("2. Each situation ends up with a different codec")
    st.caption("Blue cells are rules the loop changed from the default for that situation.")
    st.markdown(settings_table(runs), unsafe_allow_html=True)

    st.subheader("Risky patterns the loop learned to avoid")
    st.caption("Short DNA snippets the risk model found likely to be misread in this situation. "
               "The encoder steers around them.")

    def render_risky(run: RunResult) -> None:
        best = tailored(run)
        if best is None or not best.risky_kmers:
            st.info(f"{pretty_situation(run.situation)}: no risky patterns logged yet.")
            return
        show(risky_chart(run, best), key=f"risky-{run.situation}")

    in_columns(runs, render_risky)

    st.header("3. The loop converging")
    st.caption("Each dot is one loop round: encode, simulate the DNA channel, decode, learn from failures, "
               "re-encode. The dashed line is the default codec.")
    if any(r.iterations for r in runs):
        show(loop_chart(runs, "strand_accuracy"), key="loop-acc")
        if any(it.metrics.bits_per_base is not None for r in runs for it in r.iterations):
            show(loop_chart(runs, "bits_per_base"), key="loop-dens")
    else:
        st.info("No loop iterations yet.")

    st.header("4. Same accuracy with fewer reads")
    st.caption("Reading more copies of each strand costs money. Lines further left reach the target with fewer reads.")

    def render_cov(run: RunResult) -> None:
        fig, msg = coverage_chart(run, target)
        if fig is None:
            st.info(f"{pretty_situation(run.situation)}: no coverage curve yet.")
            return
        if msg:
            st.markdown(msg)
        show(fig, key=f"cov-{run.situation}")

    in_columns(runs, render_cov)

    st.header("5. Where along the strand errors happen")
    st.caption("Darker = more letters decoded wrong at that position. Rows further down are later loop rounds.")

    def render_pos(run: RunResult) -> None:
        if not run.default_metrics.per_position_error and not run.iterations:
            st.info("No per-position data.")
            return
        show(position_chart(run), key=f"pos-{run.situation}")

    in_columns(runs, render_pos)

    with st.expander("Raw numbers per situation"):
        for run in runs:
            st.markdown(f"**{pretty_situation(run.situation)}** ({run.situation}): "
                        f"{html.escape(run.profile.description)}. Created {run.created_at}. "
                        f"Profile calibrated from: {run.profile.calibrated_from or 'not calibrated (guessed)'}.")
            st.dataframe(iteration_table(run), hide_index=True)

    st.caption(f"Run `{run_id}` · {len(runs)} situation(s) · refreshed {datetime.now():%H:%M:%S}")


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
        runs, _ = load(run_id)
        situations = [r.situation for r in runs]
        focus = st.selectbox("Situation", ["All situations", *situations],
                             format_func=lambda s: s if s == "All situations" else pretty_situation(s))
        target = st.slider("Target accuracy for the coverage chart", 0.50, 0.99, 0.90, 0.01, format="%.2f")
        live = st.toggle("Live mode (auto-refresh)", value=False,
                         help="Re-reads the result files so the loop can be watched while it runs.")
        interval = st.select_slider("Refresh every (seconds)", [5, 10, 20, 30, 60], value=10, disabled=not live)
        st.divider()
        st.caption("Gray = the one-size-fits-all default codec. Blue = the codec tailored to this situation.")

    if live:
        st.fragment(render_body, run_every=interval)(run_id, focus, target)
    else:
        render_body(run_id, focus, target)


if __name__ == "__main__":
    main()
