"""Live demo page: store a small file in DNA, read it back through a noisy channel, default vs tuned.

Run with the dashboard (Streamlit picks up pages/ automatically):
    uv run streamlit run dashboard/app.py
or on its own:
    uv run streamlit run dashboard/pages/2_Demo.py

Every run shows all fixed demo passes (the first five train seeds, dnacodec.demo.demo_seeds),
never a pass picked because the tuned codec won. The evidence numbers next to it come from the
loop's 300-trial held-out evaluation in results/<run_id>/<situation>.json.
"""

from __future__ import annotations

import hashlib
import html
from dataclasses import replace

import streamlit as st

from dnacodec import demo
from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.profiles import load_profile
from dnacodec.types import EncoderSettings

CHANNELS = {
    "nanopore_budget": "Nanopore, tight budget",
    "illumina_standard": "Illumina, standard lab",
}
B = demo.BRAND

CSS = f"""
<style>
.demo-lede {{ font-size: 1.15rem; line-height: 1.55; max-width: 60rem; opacity: 0.92; }}
.verdict {{ display: inline-block; padding: 0.35rem 0.8rem; border-radius: 8px; font-weight: 700;
  font-size: 1.15rem; color: #fff; margin: 0.2rem 0 0.6rem 0; }}
.verdict.ok {{ background: {B["gain"]}; }}
.verdict.lost {{ background: {B["cost"]}; }}
.lostbox {{ border: 2px dashed {B["cost"]}; border-radius: 10px; padding: 1.4rem 1rem; text-align: center;
  font-size: 1.1rem; font-weight: 600; margin-bottom: 0.6rem; }}
.outcome {{ font-size: 1.3rem; font-weight: 650; margin: 0.6rem 0 0.2rem 0; }}
.legend {{ display: flex; gap: 1.2rem; flex-wrap: wrap; font-size: 0.95rem; margin: 0.2rem 0 0.8rem 0; }}
.legend span.sw {{ display: inline-block; width: 0.95rem; height: 0.95rem; border-radius: 3px;
  vertical-align: -0.12rem; margin-right: 0.35rem; position: relative; }}
table.demo {{ width: 100%; border-collapse: collapse; font-size: 1.0rem; }}
table.demo td {{ padding: 0.3rem 0.4rem; border-top: 1px solid rgba(128,128,128,0.22);
  font-variant-numeric: tabular-nums; }}
table.demo td:last-child {{ text-align: right; font-weight: 600; }}
table.passes {{ border-collapse: collapse; font-size: 1.0rem; }}
table.passes th, table.passes td {{ padding: 0.3rem 0.8rem; border-top: 1px solid rgba(128,128,128,0.22);
  text-align: left; font-variant-numeric: tabular-nums; }}
.ok-t {{ color: {B["gain"]}; font-weight: 700; }}
.lost-t {{ color: {B["cost"]}; font-weight: 700; }}
@media (prefers-color-scheme: dark) {{ .ok-t {{ color: #4FD6B5; }} .lost-t {{ color: #FF86B0; }} }}
.mock-banner {{ background: #c62828; color: #fff; padding: 0.8rem 1rem; border-radius: 10px;
  font-size: 1.4rem; font-weight: 800; text-align: center; }}
</style>
"""

LEGEND = (
    '<div class="legend">'
    f'<span><span class="sw" style="background:{B["gain"]}"></span>correct</span>'
    f'<span><span class="sw" style="background:{B["cost"]}"></span>&#10005; wrong, caught by the checksum</span>'
    f'<span><span class="sw" style="background:{B["tbd_fill"]};border:1.5px dashed {B["tbd_text"]}"></span>lost, no reads</span>'
    "</div>"
)


def default_run(runs: list[str]) -> int:
    for preferred in ("run1",):
        if preferred in runs:
            return runs.index(preferred)
    return next((i for i, r in enumerate(runs) if r != "mock"), 0)


def get_input() -> tuple[bytes, str, str] | None:
    """(data, name, kind) from the chosen source, or None if the upload is missing or too big."""
    source = st.radio("What to store", ["Demo image", "Upload a small image", "Type a message"],
                      horizontal=True, key="source")
    if source == "Demo image":
        data = demo.demo_image_png()
        return data, "demo image (64 x 64 PNG, drawn in code)", "png"
    if source == "Type a message":
        text = st.text_area("Message", demo.DEFAULT_MESSAGE, max_chars=demo.MAX_DEMO_BYTES, key="message")
        data = text.encode("utf-8")
        if not data:
            st.info("Type a message to store.")
            return None
        return data, "your message", "text"
    up = st.file_uploader("Image, up to 5 KB (PNG, JPEG, GIF or WebP)", type=["png", "jpg", "jpeg", "gif", "webp"],
                          key="upload")
    if up is None:
        st.info("Choose a small image. Larger files work in the command line script.")
        return None
    data = up.getvalue()
    if len(data) > demo.MAX_DEMO_BYTES:
        st.warning(f"That file is {len(data) / 1024:.1f} KB. The live demo takes up to 5 KB so it runs in seconds.")
        return None
    return data, up.name, demo.sniff_kind(data)


def run_all(data: bytes, situation: str, reads: float, run_id: str):
    """All fixed demo passes, default vs tuned. Cached in the session per input."""
    key = (hashlib.sha256(data).hexdigest(), situation, float(reads), run_id)
    cache = st.session_state.setdefault("demo_cache", {})
    if key in cache:
        return cache[key]
    profile = replace(load_profile(situation), coverage_mean=float(reads))
    decoder = MajorityVoteDecoder()
    try:
        tuned = demo.load_tuned_codec(run_id, situation)
        missing = None
    except demo.TunedCodecUnavailable as e:
        tuned, missing = None, str(e)
    default_settings = tuned.default_settings if tuned and tuned.default_settings else EncoderSettings()
    passes = []
    for seed in demo.demo_seeds():
        d = demo.round_trip(data, default_settings, None, decoder, profile, seed, label="default")
        t = (demo.round_trip(data, tuned.settings, tuned.scorer, decoder, profile, seed, label="tuned")
             if tuned is not None else None)
        passes.append((d, t))
    cache.clear()  # keep one result set in memory
    cache[key] = (tuned, missing, default_settings, passes)
    return cache[key]


def show_file(r: demo.RoundTripResult, kind: str) -> None:
    if r.ok:
        st.markdown('<div class="verdict ok">&#10003; File recovered exactly</div>', unsafe_allow_html=True)
        if kind in demo.IMAGE_KINDS:
            st.image(r.recovered, width=192)
        else:
            st.code((r.recovered or b"").decode("utf-8", errors="replace"), language=None, wrap_lines=True)
    else:
        st.markdown('<div class="verdict lost">&#10005; File lost</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="lostbox">Too few correct strands: {r.counts[demo.CORRECT]} came back correct, '
                    f'the file needs at least {r.needed} and usually a few more. Nothing is returned, '
                    "never a corrupted file.</div>",
                    unsafe_allow_html=True)


def numbers_table(r: demo.RoundTripResult) -> str:
    c = r.counts
    rows = [
        ("Strands written", f"{r.n_strands}"),
        ("Correct strands needed, at least", f"{r.needed}"),
        ("Correct", f"{c[demo.CORRECT]}"),
        ("Wrong, caught by the checksum", f"{c[demo.CAUGHT]}"),
        ("Lost, no reads", f"{c[demo.LOST]}"),
    ]
    if c[demo.SLIPPED]:
        rows.append(("Wrong, passed the checksum", f"{c[demo.SLIPPED]}"))
    rows += [
        ("Strand accuracy", f"{r.strand_accuracy:.1%}"),
        ("Bits per DNA letter (density)", f"{r.bits_per_base:.2f}"),
        ("Reads per strand, this pass", f"{r.reads_per_strand:.2f}"),
        ("Time (encode, sequence, decode)", f"{r.total_seconds:.2f} s"),
    ]
    body = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in rows)
    return f'<table class="demo">{body}</table>'


def column(title: str, subtitle: str, r: demo.RoundTripResult, kind: str) -> None:
    st.subheader(title)
    st.caption(subtitle)
    show_file(r, kind)
    st.markdown(demo.strand_map_svg(r, title=f"{title}: strand map"), unsafe_allow_html=True)
    st.markdown(numbers_table(r), unsafe_allow_html=True)


def passes_table(passes, selected: int) -> str:
    def cell(r):
        if r is None:
            return "<td>n/a</td>"
        return ('<td class="ok-t">&#10003; recovered</td>' if r.ok else '<td class="lost-t">&#10005; lost</td>')

    rows = []
    for i, (d, t) in enumerate(passes):
        mark = " (shown above)" if i == selected else ""
        rows.append(f"<tr><td>Pass {i + 1}, seed {d.seed}{mark}</td>{cell(d)}{cell(t)}</tr>")
    n = len(passes)
    nd = sum(d.ok for d, _ in passes)
    nt = sum(t.ok for _, t in passes if t is not None)
    rows.append(f"<tr><td><b>Recovered</b></td><td><b>{nd} of {n}</b></td>"
                f"<td><b>{nt if passes[0][1] is not None else 'n/a'} of {n}</b></td></tr>")
    return ('<table class="passes"><tr><th>Channel pass</th><th>Default codec</th><th>Tuned codec</th></tr>'
            + "".join(rows) + "</table>")


def main() -> None:
    st.set_page_config(page_title="Live demo", page_icon="🧬", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("Live demo: store a file in DNA, read it back")
    st.markdown(
        '<p class="demo-lede">We write a small file into DNA strands, send them through a simulated sequencer '
        "that is calibrated on real reads, and try to get the file back. Both codecs use the same encoder and the "
        "same decoder. Only the settings differ: the default uses the hand rules and redundancy everyone copies, "
        "the tuned codec uses what our tool measured for this channel. Each small square below is one DNA strand.</p>",
        unsafe_allow_html=True,
    )

    runs = demo.list_result_runs()
    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        situation = st.selectbox("Channel", list(CHANNELS), format_func=CHANNELS.get, key="channel")
    budget = load_profile(situation).coverage_mean
    with c2:
        reads = st.slider("Reads per strand (mean)", 2, 20, int(round(budget)), 1, key="reads",
                          help=f"The budget this channel was tuned for is {budget:g} reads per strand.")
    with c3:
        if runs:
            run_id = st.selectbox("Tuned codec from run", runs, index=default_run(runs), key="run",
                                  format_func=lambda r: f"{r} (MOCK)" if r == "mock" else r)
        else:
            run_id = "run1"
            st.caption("No loop results found in results/.")

    picked = get_input()
    if picked is None:
        st.stop()
    data, name, kind = picked
    st.caption(f"Storing {html.escape(name)}: {len(data):,} bytes.")

    with st.spinner("Encoding, sequencing and decoding..."):
        tuned, missing, default_settings, passes = run_all(data, situation, reads, run_id)

    if tuned is not None and tuned.is_mock:
        st.markdown('<div class="mock-banner">MOCK DATA: NOT REAL RESULTS</div>', unsafe_allow_html=True)
    if missing:
        st.warning(f"Tuned codec not available: {missing} Showing the default codec only.")

    labels = [f"Pass {i + 1} (seed {d.seed})" for i, (d, _) in enumerate(passes)]
    sel = st.radio("Channel pass", range(len(passes)), format_func=lambda i: labels[i], horizontal=True, key="pass",
                   help="Each pass is an independent run of the sequencer. The five passes are fixed in advance "
                        "(the first five training seeds), so nobody picks a lucky one.")
    d, t = passes[sel]
    if t is not None:
        st.markdown(f'<div class="outcome">{html.escape(demo.outcome(d, t))}</div>', unsafe_allow_html=True)
    st.markdown(LEGEND, unsafe_allow_html=True)

    left, right = st.columns(2, gap="large")
    with left:
        column("Default codec", f"{demo.describe_settings(default_settings)}. Rule scorer.", d, kind)
    with right:
        if t is not None and tuned is not None:
            column(f"Tuned codec ({tuned.run_id})",
                   f"{demo.describe_settings(tuned.settings)}. {demo.scorer_name(tuned.scorer).capitalize()}.", t, kind)
        else:
            st.subheader("Tuned codec")
            st.info("Not available on this machine.")

    st.subheader("All five fixed passes")
    st.markdown(passes_table(passes, sel), unsafe_allow_html=True)

    if tuned is not None and t is not None:
        st.subheader("What the tuned codec pays, and the real evidence")
        note = demo.tradeoff_note(default_settings, tuned, d.bits_per_base, t.bits_per_base)
        if note:
            st.markdown(note)
        if tuned.n_heldout_trials:
            def fmt(v):
                return "not reached in the tested range" if v is None else f"{v:g}"
            st.markdown(
                f"This page is a showcase: one small file, five passes. The measurement behind it is the loop's "
                f"evaluation on the 20 KB test file over {tuned.n_heldout_trials} unseen passes: to recover it every "
                f"time, the default codec needs **{fmt(tuned.default_min_reads)}** reads per strand, the tuned codec "
                f"**{fmt(tuned.tuned_min_reads)}**.")
        st.caption(tuned.note)
    st.caption("Why a wrong strand can't corrupt the file: every strand carries a checksum. A strand the decoder gets "
               "wrong is thrown away like a lost one, and the Fountain code rebuilds the file from any large enough "
               "set of correct strands. A final file checksum means you get the exact file or nothing.")


main()
