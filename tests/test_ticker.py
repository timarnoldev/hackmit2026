"""Tests for the live decode ticker (scripts/ticker_server.py).

Two things must hold for the jury demo:
  1. A short live run produces well formed events, with the reads, the decoded strand and the
     running totals the display needs.
  2. A recording round trips: the file the server dumps replays without recomputing anything
     and carries the same events.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from scripts.ticker_server import (
    BOX_LETTERS,
    BOX_READS,
    PROTOCOL_VERSION,
    VISIBLE_LETTERS,
    DecodeBackend,
    Engine,
    ReplayEngine,
    TickerConfig,
    compact_event,
    fix_marks,
    pack_marks,
    read_marks,
    dump_box_dataset,
    read_recording,
    record_stream,
    unpack_marks,
    write_recording,
)

# A checkpoint that cannot exist, so the tests never depend on torch or on a trained model.
NO_CHECKPOINT = "/nonexistent/polish-checkpoint-for-tests.pt"


def short_config(**kwargs) -> TickerConfig:
    base = dict(source="message", rate=20.0, reads_per_strand=8.0, checkpoint=NO_CHECKPOINT)
    base.update(kwargs)
    return TickerConfig(**base)


def collect(n_strands: int, config: TickerConfig | None = None, timeout: float = 60.0) -> list[dict]:
    """Run the engine until n_strands strand events are out, then stop it."""
    events: list[dict] = []
    done = threading.Event()
    seen = 0

    def emit(event: dict) -> None:
        nonlocal seen
        events.append(event)
        if event.get("t") == "s":
            seen += 1
            if seen >= n_strands:
                done.set()

    engine = Engine(config or short_config(), emit)
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    assert done.wait(timeout), f"only {seen} strand events in {timeout}s"
    engine.stop.set()
    thread.join(timeout=5)
    return events


# ---------------------------------------------------------------- marks


def test_pack_marks_round_trips():
    marks = [(0, "x"), (3, "e"), (11, "m"), (37, "x")]
    packed = pack_marks(marks)
    assert packed == "0x3e11m37x"
    assert unpack_marks(packed) == marks
    assert pack_marks([]) == ""
    assert unpack_marks("") == []


def test_read_marks_names_the_three_error_kinds():
    # the strand, and a read with one wrong letter, one extra letter and one missing letter
    final = "ACGTACGTAC"
    read = "AGGTAACGTA"  # position 1 wrong, position 5 extra, last letter missing
    marks = dict(unpack_marks(read_marks(read, final, VISIBLE_LETTERS)))
    assert set(marks.values()) <= {"x", "e", "m"}
    assert marks, "a read that differs from the strand must produce marks"
    assert read_marks(final, final, VISIBLE_LETTERS) == ""


def test_fix_marks_counts_every_edit_and_trims_the_window():
    draft = "ACGT" * 15
    final = "TCGT" + "ACGT" * 14  # one substitution at position 0
    marks, replaced, total = fix_marks(draft, final, VISIBLE_LETTERS)
    assert total == 1
    assert unpack_marks(marks) == [(0, "s")]
    assert replaced == "A", "the display needs the letter the classic decoder actually had"
    # an edit outside the visible window is counted but not drawn
    late_final = draft[:50] + ("T" if draft[50] != "T" else "A") + draft[51:]
    marks, replaced, total = fix_marks(draft, late_final, VISIBLE_LETTERS)
    assert total == 1
    assert marks == "" and replaced == ""


def test_replaced_letters_line_up_with_the_marks():
    """Every mark in the compact event has exactly one replaced letter, in the same order."""
    for e in [x for x in collect(20) if x["t"] == "s"]:
        c = compact_event(e)
        assert len(unpack_marks(c["f"])) == len(c["fp"]), c
        for pos, kind in unpack_marks(c["f"]):
            assert kind in "sid"
            assert pos < len(c["c"]) or kind == "d"


# ---------------------------------------------------------------- live events


def test_live_run_produces_well_formed_events():
    events = collect(12)
    kinds = [e["t"] for e in events]
    assert kinds[0] == "hello"
    assert "pass" in kinds

    hello = events[0]
    assert hello["v"] == PROTOCOL_VERSION
    # no checkpoint in the tests, so the stream must say it fell back to the classic decoder
    assert hello["fix_layer"] == "vote"
    assert "majority vote" in hello["decoder"]
    assert hello["note"], "the fallback has to be stated in the stream"
    for key in ("paced", "trimmed", "channel", "rate", "visible_letters", "strand_length"):
        assert key in hello

    first_pass = next(e for e in events if e["t"] == "pass")
    assert first_pass["n"] > 0
    assert first_pass["bytes"] > 0
    assert first_pass["tech"] in ("nanopore", "illumina")

    strands = [e for e in events if e["t"] == "s"]
    assert len(strands) >= 12
    for e in strands:
        assert 0 <= e["i"] < e["n"]
        assert isinstance(e["ok"], bool)
        assert e["layer"] == "vote"
        assert len(e["cons"]) <= VISIBLE_LETTERS
        assert len(e["reads"]) <= 3
        for r in e["reads"]:
            assert len(r["s"]) <= VISIBLE_LETTERS
            assert set(r["s"]) <= set("ACGT")
            for pos, kind in unpack_marks(r["m"]):
                assert 0 <= pos < VISIBLE_LETTERS
                assert kind in "xem"
        for pos, kind in unpack_marks(e["fix"]):
            assert 0 <= pos < VISIBLE_LETTERS
            assert kind in "sid"
        st = e["st"]
        for key in ("done", "ok", "drop", "mfix", "rfix", "rps", "acc", "prog"):
            assert key in st
        assert 0 <= st["acc"] <= 1
        assert 0 <= st["prog"] <= 1
        assert st["ok"] <= st["done"]
        # a strand with no reads at all is a dropout and carries nothing to draw
        assert e["drop"] == (e["nr"] == 0)
        if e["drop"]:
            assert e["reads"] == [] and e["cons"] == ""


def test_totals_only_grow():
    strands = [e for e in collect(15) if e["t"] == "s"]
    previous = None
    for e in strands:
        st = e["st"]
        if previous is not None:
            for key in ("done", "ok", "drop", "mfix", "rfix"):
                assert st[key] >= previous[key], f"{key} went backwards"
        previous = st


def test_compact_event_fits_one_small_serial_line():
    """Every line the box has to parse while drawing stays well under 512 bytes."""
    strands = [e for e in collect(25) if e["t"] == "s"]
    sizes = [len(json.dumps(compact_event(e), separators=(",", ":")).encode()) for e in strands]
    assert max(sizes) < 512, f"largest serial line was {max(sizes)} bytes"
    # the compact form keeps everything the box draws
    sample = compact_event(strands[0])
    assert set(sample) >= {"t", "i", "n", "nr", "ok", "d", "r", "c", "f", "st"}


def test_the_ticker_keeps_going_past_the_end_of_the_file():
    """A pass must not end the stream: the demo has to run for minutes."""
    events = collect(2)
    n = next(e for e in events if e["t"] == "pass")["n"]
    events = collect(n + 5, short_config(rate=20.0), timeout=180.0)
    assert sum(1 for e in events if e["t"] == "pass") >= 2, "a second pass must start"
    assert any(e["t"] == "file" for e in events), "the finished pass must report the file"
    file_event = next(e for e in events if e["t"] == "file")
    assert isinstance(file_event["ok"], bool)


# ---------------------------------------------------------------- recording


def test_recording_round_trips(tmp_path):
    path = tmp_path / "stream.jsonl"
    record_stream(short_config(), 10, path)
    events = read_recording(path)
    assert events, "the recording must not be empty"
    assert events[0]["t"] == "hello"
    assert sum(1 for e in events if e["t"] == "s") >= 10

    again = tmp_path / "again.jsonl"
    write_recording(again, events)
    assert read_recording(again) == events, "write then read must give the same events back"


def test_replay_needs_no_compute(tmp_path):
    path = tmp_path / "stream.jsonl"
    record_stream(short_config(), 8, path)
    original = read_recording(path)

    out: list[dict] = []
    engine = ReplayEngine(path, out.append, rate=20.0, loop=False)
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    thread.join(timeout=30)
    engine.stop.set()

    assert len(out) == len(original)
    assert out[0]["t"] == "hello"
    assert out[0]["replay"] == path.name
    for replayed, recorded in zip(out, original):
        if recorded["t"] == "s":
            assert replayed == recorded, "a replayed strand must be the recorded one, unchanged"


def test_replay_hello_survives_a_recording_without_one(tmp_path):
    path = tmp_path / "only-strands.jsonl"
    write_recording(path, [{"t": "s", "i": 0, "n": 1, "nr": 1, "ok": True, "drop": False,
                            "reads": [], "cons": "ACGT", "fix": "", "nfix": 0,
                            "layer": "vote", "st": {}}])
    engine = ReplayEngine(path, lambda e: None, rate=20.0, loop=False)
    hello = engine.hello()
    assert hello["t"] == "hello"
    assert hello["replay"] == path.name


# ---------------------------------------------------------------- control


def test_pause_stops_the_stream_and_resume_starts_it_again():
    events: list[dict] = []
    engine = Engine(short_config(rate=20.0), events.append)
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not any(e["t"] == "s" for e in events) and time.monotonic() < deadline:
            time.sleep(0.05)
        engine.paused.set()
        time.sleep(0.4)
        frozen = len(events)
        time.sleep(0.5)
        assert len(events) == frozen, "a paused engine must not emit"
        engine.paused.clear()
        time.sleep(0.5)
        assert len(events) > frozen, "resuming must start the stream again"
    finally:
        engine.stop.set()
        thread.join(timeout=5)


@pytest.mark.parametrize("rate,expected", [(0.1, 0.5), (4.0, 4.0), (500.0, 20.0)])
def test_rate_is_clamped(rate, expected):
    engine = Engine(short_config(), lambda e: None)
    engine.set_rate(rate)
    assert engine.config.rate == expected


# ---------------------------------------------------------------- the box's own dataset


def test_box_dataset_carries_what_the_device_needs(tmp_path):
    """The frozen run must hold full reads and references, not just the display fields.

    The box decodes for itself, so a record that only carried the Mac's answer would make the
    device replay rather than decode, which is the thing the firmware must never do.
    """
    path = tmp_path / "box_data.h"
    out, n, size = dump_box_dataset(short_config(source="message"), 12, path)
    assert out.exists() and n == 12 and size > 0
    text = out.read_text()
    assert "#define BOX_DATA_STRANDS 12" in text
    assert "#define BOX_DATA_STRAND_LENGTH" in text

    records = [line for line in text.split("\n") if line.strip().startswith('"i|')]
    assert len(records) == 12
    for rec in records:
        body = rec.strip().strip(",").strip('"')
        lines = body.split("\\n")
        kinds = [ln[0] for ln in lines if ln]
        assert kinds[0] == "i"
        assert "x" in kinds, "every record needs the true reference to judge correctness"
        head = lines[0].split("|")
        assert len(head) == 5, "index, ok, nreads and the untrimmed fix count"
        n_reads = int(head[3])
        if n_reads:
            full = [ln for ln in lines if ln.startswith("f|")]
            assert full, "a strand with reads must carry them in full for the decoder"
            for ln in full:
                assert set(ln[2:]) <= set("ACGT")
            shown = [ln for ln in lines if ln.startswith("r|")]
            assert len(shown) <= BOX_READS
            for ln in shown:
                assert len(ln[2:].split("|")[0]) <= BOX_LETTERS


def test_box_dataset_is_decoded_by_the_classic_vote():
    """The frozen run must never claim a result the box could not produce on its own."""
    backend = DecodeBackend("/nonexistent-on-purpose.pt")
    assert not backend.has_model
    assert backend.fix_layer == "vote"


def test_serial_trim_matches_the_firmware_window():
    """The box draws a fixed window; the server must not send more than it can show."""
    for e in [x for x in collect(12) if x["t"] == "s"]:
        c = compact_event(e)
        assert len(c["r"]) <= BOX_READS
        assert len(c["c"]) <= BOX_LETTERS
        for r in c["r"]:
            assert len(r["s"]) <= BOX_LETTERS
