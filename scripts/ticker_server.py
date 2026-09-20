#!/usr/bin/env python3
"""Erbgut live decode ticker: server, event stream, USB serial sender, recorder and replay.

What this is
------------
A real decode, streamed as events for a display. The server encodes a file with
dnacodec.encoder, pushes the strands through dnacodec.simulator (the calibrated channel
simulator), then decodes cluster by cluster and emits one event per strand. Every letter,
every error and every correction shown downstream comes out of that run. Nothing here is a
scripted animation.

Two things are done for the eye, and both are stated in the stream and in the UI:
  1. Pacing. The decode is far faster than a person can read, so events are released at a
     fixed rate (default 4 strands per second). The pacing never changes what was decoded.
  2. Trimming. Each event carries at most 3 reads of a cluster and the first 38 letters of
     each line, because that is what fits a 320 x 240 screen. The full strand is still
     decoded and still judged correct or wrong over its full length.

Decoder
-------
PolishDecoder (classic draft plus a CNN that corrects it) is used when torch and a
checkpoint are available. Otherwise the server falls back to the classic majority vote and
says so in the `hello` event and in every strand event (`fix_layer`). With the fallback
there are no model corrections to show, so the visible fixes are the read errors that the
vote resolved, which is the same picture as slide 3 of the deck.

Endpoints
---------
  GET  /                     the browser view (marketing/ticker/index.html)
  GET  /events               server sent events, one JSON event per message
  GET  /api/state            current configuration and running totals
  POST /api/control          {"action": "pause" | "resume" | "restart" | "speed" | "config",
                              ...}  see _control()

Running
-------
  python scripts/ticker_server.py                       serve + browser view + USB box
  python scripts/ticker_server.py --no-serial           browser view only
  python scripts/ticker_server.py --record out.jsonl    also write the stream to a file
  python scripts/ticker_server.py --replay out.jsonl    replay a recording, no compute

New file, owned by the ticker. It only imports from dnacodec, it changes nothing there.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, replace as dc_replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rapidfuzz.distance import Levenshtein  # noqa: E402

from dnacodec import demo as demo_mod  # noqa: E402
from dnacodec.baseline import MAX_READS, subsample  # noqa: E402
from dnacodec.encoder import encode, recover  # noqa: E402
from dnacodec.profiles import list_profiles, load_profile  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402
from dnacodec.types import Cluster, EncoderSettings, Strand  # noqa: E402

TICKER_DIR = REPO_ROOT / "marketing" / "ticker"
DECK_FONT_DIR = REPO_ROOT / "marketing" / "deck" / "fonts"
DEFAULT_CHECKPOINTS = (
    REPO_ROOT / "checkpoints" / "polish" / "polish.pt",
    Path.home() / "hackmit2026" / "checkpoints" / "polish" / "polish.pt",
)

# The browser view's window: 38 letters and 3 reads per strand.
VISIBLE_LETTERS = 38
VISIBLE_READS = 3

# The box's window is narrower. Its panel is 320 x 240 with a 14 px margin on every side and
# the decoded strand drawn at 12 px per letter, so 22 letters and 2 reads is what fits while
# the letters stay readable from two metres. The trim happens in compact_event(), so the
# browser keeps the wider view. Must match VISIBLE_LETTERS and MAX_READS in
# hardware/esp32_ticker/src/board_config.h.
BOX_LETTERS = 22
BOX_READS = 2

PROTOCOL_VERSION = 1


# ---------------------------------------------------------------- configuration


@dataclass
class TickerConfig:
    channel: str = "nanopore_budget"
    reads_per_strand: float = 10.0  # profile.coverage_mean for this run
    rate: float = 4.0  # strands per second released to the stream
    source: str = "image"  # "message", "image", or a path to a file
    strand_length: int = 110
    # 0.8 spare strands per data strand: on nanopore_budget at 10 reads the classic decoder
    # gets about 70% of strands exactly right, and the fountain code still returns the file.
    redundancy: float = 0.8
    max_bytes: int = 4096  # a pass stays a few seconds of compute
    visible_letters: int = VISIBLE_LETTERS
    visible_reads: int = VISIBLE_READS
    checkpoint: str | None = None

    def clamp(self) -> "TickerConfig":
        self.rate = float(min(20.0, max(0.5, self.rate)))
        self.reads_per_strand = float(min(30.0, max(1.0, self.reads_per_strand)))
        self.visible_letters = int(min(80, max(10, self.visible_letters)))
        self.visible_reads = int(min(5, max(1, self.visible_reads)))
        return self


def load_source(source: str, max_bytes: int) -> tuple[bytes, str]:
    """Bytes to encode plus a short label. 'message' and 'image' are the built-in demo files."""
    if source == "message":
        return demo_mod.DEFAULT_MESSAGE.encode("utf-8"), "hackmit.txt"
    if source == "image":
        return demo_mod.demo_image_png(64), "erbgut-mark.png"
    path = Path(source).expanduser()
    data = path.read_bytes()
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data, path.name


# ---------------------------------------------------------------- decoder


# The accurate PolishDecoder variant (see docs/MODELS.md and scripts/polish_experiments.py).
# Accuracy and visible fixes matter more than throughput here: the ticker decodes a handful
# of clusters per second, not a benchmark.
POLISH_KWARGS = dict(
    rounds=2,
    drafts=3,
    select="confidence",
    mode="gain",
    thresholds={"low_max_reads": 3, "low": (0.5, -0.5), "high": (0.0, -1.0)},
    escalate=-0.001,
)


class DecodeBackend:
    """Classic majority vote, plus the learned polisher on top of it when it is available.

    Both answers are kept per cluster, so the stream can show exactly what the model changed
    on top of the classic decoder. Without a checkpoint the classic answer is the answer and
    `has_model` is False.
    """

    def __init__(self, checkpoint: str | None = None) -> None:
        self.has_model = False
        self.name = "majority vote"
        self.note = ""
        self._decoder = None
        paths = [Path(checkpoint)] if checkpoint else list(DEFAULT_CHECKPOINTS)
        found = next((p for p in paths if p.exists()), None)
        if found is None:
            where = ", ".join(str(p) for p in paths)
            self.note = (f"No polish checkpoint found ({where}), so the classic majority vote "
                         "decodes and the fixes shown are the read errors the vote resolved.")
            return
        try:
            from dnacodec.model.polish import PolishDecoder

            self._decoder = PolishDecoder(found, **POLISH_KWARGS)
            self.has_model = True
            self.name = "polish (majority vote plus CNN)"
            self.note = (f"Polish checkpoint loaded from {found.name}. The fixes shown are the "
                         "edits the model made to the classic majority vote answer.")
        except Exception as exc:  # noqa: BLE001  torch missing, bad checkpoint, anything
            self.note = (f"Polish checkpoint at {found} could not be loaded "
                         f"({type(exc).__name__}: {exc}), so the classic majority vote decodes "
                         "and the fixes shown are the read errors the vote resolved.")

    @property
    def fix_layer(self) -> str:
        return "model" if self.has_model else "vote"

    def decode_one(self, cluster: Cluster, strand_length: int) -> tuple[str | None, str | None]:
        """(classic majority vote answer, final strand). Both None for an empty cluster.

        The classic answer is what dnacodec.baseline.MajorityVoteDecoder returns for this
        cluster, so the difference between the two is exactly what the model contributed.
        """
        if not any(cluster):
            return None, None
        draft = _classic_draft(cluster, strand_length)
        if draft is None:
            return None, None
        if not self.has_model:
            return draft, draft
        final = self._decoder.decode([cluster], strand_length)[0]
        return draft, (final if final is not None else draft)


def _classic_draft(cluster: Cluster, strand_length: int) -> str | None:
    """The majority vote reconstruction, the same call dnacodec.baseline makes.

    Imported lazily and locally so the ticker never needs torch for the classic path.
    """
    from dnacodec.baseline import reconstruct

    reads = [r for r in cluster if r]
    if not reads:
        return None
    return reconstruct(reads, strand_length)


# ---------------------------------------------------------------- diffs


def pack_marks(marks: Sequence[tuple[int, str]]) -> str:
    """Marks as one short string, position then kind, for example "3x11e25m".

    Deliberately compact: a serial line to the box has to stay small, and a C parser for
    this is a loop over digits. An empty string means no marks.
    """
    return "".join(f"{pos}{kind}" for pos, kind in sorted(marks))


def unpack_marks(packed: str) -> list[tuple[int, str]]:
    """Inverse of pack_marks. Used by the tests and mirrored in JavaScript and C."""
    out: list[tuple[int, str]] = []
    digits = ""
    for ch in packed:
        if ch.isdigit():
            digits += ch
        elif digits:
            out.append((int(digits), ch))
            digits = ""
    return out


def read_marks(read: str, final: str, visible: int) -> str:
    """Where a single noisy read disagrees with the decoded strand, in read coordinates.

    Kinds match slide 3 of the deck:
      "x" the read has the wrong letter here
      "e" the read has an extra letter here that the strand does not have
      "m" the read is missing a letter in front of this position
    Only marks inside the visible window are returned.
    """
    out: list[tuple[int, str]] = []
    seen_missing: set[int] = set()
    for op in Levenshtein.editops(read, final):
        if op.src_pos >= visible:
            continue
        if op.tag == "replace":
            out.append((op.src_pos, "x"))
        elif op.tag == "delete":
            out.append((op.src_pos, "e"))
        elif op.tag == "insert" and op.src_pos not in seen_missing:
            seen_missing.add(op.src_pos)
            out.append((op.src_pos, "m"))
    return pack_marks(out)


def fix_marks(draft: str, final: str, visible: int) -> tuple[str, str, int]:
    """What the model changed, in final-strand coordinates, plus the total count.

    Returns (marks, replaced, total). Kinds: "s" substituted, "i" inserted, "d" deleted a
    letter in front of this position. `replaced` holds, in the same order as the marks, the
    letter the classic decoder had there, or "-" where it had none. The display needs it to
    show a corrected position actually changing from the wrong letter to the right one, and
    it is the real draft letter, not a guess. The count is over the whole strand; the strings
    are trimmed to the visible window.
    """
    marks: list[tuple[int, str]] = []
    was: dict[tuple[int, str], str] = {}
    total = 0
    seen_del: set[int] = set()
    for op in Levenshtein.editops(draft, final):
        total += 1
        pos = op.dest_pos
        if pos >= visible:
            continue
        if op.tag == "replace":
            marks.append((pos, "s"))
            was[(pos, "s")] = draft[op.src_pos]
        elif op.tag == "insert":
            marks.append((pos, "i"))
            was[(pos, "i")] = "-"  # the draft had nothing here
        elif op.tag == "delete" and pos not in seen_del:
            seen_del.add(pos)
            marks.append((pos, "d"))
            was[(pos, "d")] = draft[op.src_pos]
    ordered = sorted(marks)
    return pack_marks(ordered), "".join(was[m] for m in ordered), total


# ---------------------------------------------------------------- totals


@dataclass
class Totals:
    strands: int = 0
    correct: int = 0
    dropouts: int = 0
    model_fixes: int = 0
    read_errors: int = 0  # read letters the consensus disagreed with, over the shown reads
    reads: int = 0
    passes: int = 0
    files_recovered: int = 0

    def as_event(self, index: int, n_strands: int) -> dict[str, Any]:
        return {
            "done": self.strands,
            "ok": self.correct,
            "drop": self.dropouts,
            "mfix": self.model_fixes,
            "rfix": self.read_errors,
            "rps": round(self.reads / self.strands, 2) if self.strands else 0.0,
            "acc": round(self.correct / self.strands, 4) if self.strands else 0.0,
            "pass": self.passes,
            "rec": self.files_recovered,
            "prog": round((index + 1) / n_strands, 4) if n_strands else 0.0,
        }


# ---------------------------------------------------------------- the engine


class Engine:
    """Produces the event stream from a real encode, simulate, decode round trip.

    One pass encodes the file and decodes every strand of it. When a pass ends the engine
    starts the next one with the next channel seed, so the same file keeps producing fresh
    noise and the ticker can run for as long as the jury is watching.
    """

    def __init__(self, config: TickerConfig, emit: Callable[[dict], None]) -> None:
        self.config = config.clamp()
        self.emit = emit
        self.totals = Totals()
        self.backend = DecodeBackend(self.config.checkpoint)
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.restart = threading.Event()
        self._lock = threading.Lock()
        self._pass_seed = 0
        self.last_stats: dict[str, Any] = {}

    # -------------------------------------------------- control

    def set_config(self, **kwargs: Any) -> None:
        with self._lock:
            self.config = dc_replace(self.config, **kwargs).clamp()
        self.restart.set()

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self.config.rate = float(min(20.0, max(0.5, rate)))

    def hello(self) -> dict[str, Any]:
        c = self.config
        return {
            "t": "hello",
            "v": PROTOCOL_VERSION,
            "decoder": self.backend.name,
            "fix_layer": self.backend.fix_layer,
            "note": self.backend.note,
            "channel": c.channel,
            "channels": list_profiles(),
            "reads_per_strand": c.reads_per_strand,
            "rate": c.rate,
            "source": c.source,
            "visible_letters": c.visible_letters,
            "visible_reads": c.visible_reads,
            "strand_length": c.strand_length,
            "paced": (f"Events are released at {c.rate:g} strands per second so the decode is "
                      "readable. The decode itself is real and runs at full speed."),
            "trimmed": (f"Each strand shows {c.visible_reads} of its reads and the first "
                        f"{c.visible_letters} of {c.strand_length} letters. Correctness is "
                        "judged over the whole strand."),
        }

    # -------------------------------------------------- the loop

    def run(self) -> None:
        self.emit(self.hello())
        while not self.stop.is_set():
            try:
                self._one_pass()
            except Exception as exc:  # noqa: BLE001  a bad pass must not kill the demo
                self.emit({"t": "error", "msg": f"{type(exc).__name__}: {exc}"})
                time.sleep(1.0)
            self._pass_seed += 1

    def _one_pass(self) -> None:
        with self._lock:
            c = dc_replace(self.config)
        self.restart.clear()
        data, label = load_source(c.source, c.max_bytes)
        settings = EncoderSettings(strand_length=c.strand_length, redundancy=c.redundancy)
        profile = dc_replace(load_profile(c.channel), coverage_mean=c.reads_per_strand)
        seed = train_seed(self._pass_seed)

        t0 = time.perf_counter()
        encoded = encode(data, settings, None)
        clusters = simulate(encoded.strands, profile, seed)
        setup_seconds = time.perf_counter() - t0

        n = len(encoded.strands)
        self.emit({
            "t": "pass",
            "n": n,
            "bytes": len(data),
            "file": label,
            "channel": profile.name,
            "tech": profile.technology,
            "seed": seed,
            "reads_per_strand": c.reads_per_strand,
            "setup_ms": round(setup_seconds * 1000),
            "pass": self.totals.passes + 1,
        })

        decoded: list[Strand | None] = [None] * n
        for i, cluster in enumerate(clusters):
            if self.stop.is_set() or self.restart.is_set():
                return
            tick = time.perf_counter()
            while self.paused.is_set() and not self.stop.is_set() and not self.restart.is_set():
                time.sleep(0.05)
            event, final = self._strand_event(i, n, cluster, encoded.strands[i], c)
            decoded[i] = final
            self.emit(event)
            with self._lock:
                rate = self.config.rate
            delay = (1.0 / rate) - (time.perf_counter() - tick)
            if delay > 0:
                time.sleep(delay)

        recovered = recover(decoded, encoded.meta)
        ok = recovered is not None and recovered == data
        self.totals.passes += 1
        self.totals.files_recovered += int(ok)
        self.emit({
            "t": "file",
            "ok": bool(ok),
            "bytes": len(data),
            "file": label,
            "n": n,
            "chunks": encoded.meta.n_chunks,
            "pass": self.totals.passes,
            "st": self.last_stats,
        })

    def _strand_event(self, i: int, n: int, cluster: Cluster, reference: Strand,
                      c: TickerConfig) -> tuple[dict[str, Any], Strand | None]:
        vis = c.visible_letters
        reads = [r for r in cluster if r]
        self.totals.strands += 1
        self.totals.reads += len(reads)

        if not reads:
            self.totals.dropouts += 1
            self.last_stats = self.totals.as_event(i, n)
            return {
                "t": "s", "i": i, "n": n, "nr": 0, "ok": False, "drop": True,
                "reads": [], "cons": "", "fix": "", "fixfrom": "", "nfix": 0,
                "layer": self.backend.fix_layer, "st": self.last_stats,
            }, None

        draft, final = self.backend.decode_one(cluster, c.strand_length)
        final = final or draft or ""
        correct = final == reference
        self.totals.correct += int(correct)

        marks, replaced, n_fix = fix_marks(draft or final, final, vis)
        self.totals.model_fixes += n_fix

        # The first few reads of the deterministic subsample, so the picture is reproducible.
        shown = subsample(reads, MAX_READS)[: c.visible_reads]
        read_events = []
        for r in shown:
            m = read_marks(r, final, vis)
            self.totals.read_errors += sum(1 for ch in m if ch.isalpha())
            read_events.append({"s": r[:vis], "m": m})

        self.last_stats = self.totals.as_event(i, n)
        return {
            "t": "s",
            "i": i,
            "n": n,
            "nr": len(reads),
            "ok": bool(correct),
            "drop": False,
            "reads": read_events,
            "cons": final[:vis],
            "fix": marks,
            "fixfrom": replaced,
            "nfix": n_fix,
            "layer": self.backend.fix_layer,
            "st": self.last_stats,
        }, final


# ---------------------------------------------------------------- replay engine


class ReplayEngine:
    """Replays a recorded stream without recomputing anything.

    The events are exactly the ones a live run produced, so the picture is the same decode.
    Only the clock is ours.
    """

    def __init__(self, path: Path, emit: Callable[[dict], None], rate: float | None = None,
                 loop: bool = True) -> None:
        self.path = Path(path)
        self.emit = emit
        self.rate = rate
        self.loop = loop
        self.paused = threading.Event()
        self.stop = threading.Event()
        self.restart = threading.Event()
        self.events = read_recording(self.path)
        self.config = TickerConfig(rate=rate or 4.0)
        self.last_stats: dict[str, Any] = {}

    def set_rate(self, rate: float) -> None:
        self.rate = float(min(20.0, max(0.5, rate)))

    def set_config(self, **kwargs: Any) -> None:
        if "rate" in kwargs:
            self.set_rate(float(kwargs["rate"]))

    def hello(self) -> dict[str, Any]:
        """The recorded hello, with the pacing text corrected to this replay's own clock."""
        out = next((dict(e) for e in self.events if e.get("t") == "hello"),
                   {"t": "hello", "v": PROTOCOL_VERSION})
        rate = self.rate or 4.0
        out["replay"] = str(self.path.name)
        out["rate"] = rate
        out["paced"] = (f"Replaying a recorded decode at {rate:g} strands per second. The events "
                        "are exactly the ones a live run produced; only the clock is new.")
        return out

    def run(self) -> None:
        while not self.stop.is_set():
            for event in self.events:
                if self.stop.is_set():
                    return
                while self.paused.is_set() and not self.stop.is_set():
                    time.sleep(0.05)
                out = dict(event)
                out.pop("dt", None)
                if out.get("t") == "hello":
                    out = self.hello()
                if "st" in out:
                    self.last_stats = out["st"]
                self.emit(out)
                if out.get("t") == "s":
                    time.sleep(1.0 / (self.rate or 4.0))
                else:
                    time.sleep(min(0.4, 1.0 / (self.rate or 4.0)))
            if not self.loop:
                return


def read_recording(path: Path) -> list[dict[str, Any]]:
    events = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def write_recording(path: Path, events: Sequence[dict[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    return path


def record_stream(config: TickerConfig, n_strands: int, path: Path) -> Path:
    """Run the engine until n_strands strand events are out, writing every event to path.

    Used by --dump and by the tests. The recording replays without any compute.
    """
    events: list[dict[str, Any]] = []
    done = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("t") == "s" and sum(1 for e in events if e.get("t") == "s") >= n_strands:
            done.set()

    engine = Engine(config, emit)
    thread = threading.Thread(target=engine.run, daemon=True)
    thread.start()
    done.wait(timeout=max(60.0, n_strands / max(0.5, config.rate) * 3 + 60))
    engine.stop.set()
    thread.join(timeout=5)
    return write_recording(path, events)


# ---------------------------------------------------------------- broadcast hub


class Hub:
    """Fan-out of events to the SSE clients and the serial sender.

    Every subscriber has a bounded queue. A slow subscriber drops events rather than
    blocking the decode, so an unplugged box or a stalled browser tab never stops the run.
    """

    def __init__(self, backlog: int = 400) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._backlog = backlog
        self.engine: Any = None  # set once the engine exists, for late subscribers

    def engine_hello(self) -> dict:
        return self.engine.hello() if self.engine is not None else {"t": "hello", "v": PROTOCOL_VERSION}

    def subscribe(self, maxsize: int = 200) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            if event.get("t") == "pass":  # hello is rebuilt live for every new subscriber
                self._history = [e for e in self._history if e.get("t") != event.get("t")]
                self._history.append(event)
                self._history = self._history[-8:]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()  # drop the oldest, keep the newest
                    q.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)


# ---------------------------------------------------------------- USB serial sender


def find_serial_port(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    candidates: list[str] = []
    for pattern in ("cu.usbmodem*", "cu.SLAB*", "cu.usbserial*", "cu.wchusbserial*"):
        candidates.extend(sorted(str(p) for p in Path("/dev").glob(pattern)))
    return candidates[0] if candidates else None


class SerialSender:
    """Line delimited JSON over the box's USB serial link, with reconnect.

    The ESP32-S3-BOX-3 is on its native USB serial (a CDC endpoint), where the baud rate is
    cosmetic; it is still set so a UART bridge would work too. The port is opened raw, one
    compact JSON object is written per line, and every failure closes the port and retries.
    The decode never waits for the box: if the write would block, the event is dropped.

    The box talks back in lines, not loose bytes. A line holding exactly one character is a
    command: 'p' pause, 'r' resume, '+' faster, '-' slower, '?' resend the hello, '.' the
    once a second heartbeat. Every other line is diagnostic text from the firmware and is
    kept for /api/state but never acted on. The framing matters: the box also prints things
    like "fps=28.4 heap=213456", and a bare byte scan would read the 'p' in that as a pause.
    """

    RECONNECT_SECONDS = 1.5

    def __init__(self, hub: Hub, control: Callable[[str], None], port: str | None = None,
                 baud: int = 921600, visible: int = BOX_LETTERS, reads: int = BOX_READS) -> None:
        self.hub = hub
        self.control = control
        self.requested_port = port
        self.baud = baud
        self.visible = visible
        self.reads = reads
        self.stop = threading.Event()
        self.fd: int | None = None
        self.port: str | None = None
        self.connected = False
        self.last_error = ""
        self.last_rx: float = 0.0  # when the box last said anything
        self.beats = 0
        self.log: list[str] = []  # the last few diagnostic lines the firmware printed
        self._rx = b""

    # -------------------------------------------------- port handling

    def _open(self) -> bool:
        path = find_serial_port(self.requested_port)
        if path is None:
            self.last_error = "no serial port found"
            return False
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            self.last_error = f"{path}: {exc.strerror}"
            return False
        try:
            self._configure(fd)
        except Exception as exc:  # noqa: BLE001  a CDC port may not support termios
            self.last_error = f"{path}: {type(exc).__name__}: {exc}"
        self.fd, self.port, self.connected, self.last_error = fd, path, True, ""
        return True

    def _configure(self, fd: int) -> None:
        import termios

        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        iflag &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR | termios.IGNCR)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
        cflag |= termios.CLOCAL | termios.CREAD
        cflag &= ~termios.CRTSCTS if hasattr(termios, "CRTSCTS") else cflag
        speed = getattr(termios, f"B{self.baud}", None)
        if speed is not None:
            ispeed = ospeed = speed
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, ispeed, ospeed, cc])

    def _close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd, self.connected = None, False

    # -------------------------------------------------- the loop

    def run(self) -> None:
        q = self.hub.subscribe(maxsize=120)
        try:
            while not self.stop.is_set():
                if not self.connected and not self._open():
                    self._drain(q)
                    time.sleep(self.RECONNECT_SECONDS)
                    continue
                self._handshake()
                while self.connected and not self.stop.is_set():
                    self._read_back()
                    try:
                        event = q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    self._write_line(compact_event(event, self.visible, self.reads))
        finally:
            self.hub.unsubscribe(q)
            self._close()

    @staticmethod
    def _drain(q: queue.Queue) -> None:
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                return

    def _handshake(self) -> None:
        """Tell the box a stream is starting, then send it what it needs to draw a header."""
        self._write_line({"t": "hi", "v": PROTOCOL_VERSION})
        self._resend()

    def _resend(self) -> None:
        """The hello and the current pass again, for a box that just asked with '?'.

        Deliberately without the "hi" line: the box answers "hi" with '?', so replying to a
        '?' with another "hi" would bounce the two forever.
        """
        self._write_line(compact_event(self.hub.engine_hello(), self.visible, self.reads))
        for event in self.hub.history():
            self._write_line(compact_event(event, self.visible, self.reads))

    def _write_line(self, payload: dict) -> None:
        if self.fd is None:
            return
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            written = 0
            deadline = time.monotonic() + 0.25
            while written < len(line):
                try:
                    written += os.write(self.fd, line[written:])
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        return  # the box is not reading, drop this event and move on
                    time.sleep(0.002)
        except OSError as exc:
            self.last_error = f"write failed: {exc.strerror}"
            self._close()

    def _read_back(self) -> None:
        if self.fd is None:
            return
        try:
            chunk = os.read(self.fd, 256)
        except BlockingIOError:
            return
        except OSError as exc:
            self.last_error = f"read failed: {exc.strerror}"
            self._close()
            return
        if not chunk:
            return
        self.last_rx = time.monotonic()
        self._rx += chunk
        while b"\n" in self._rx:
            raw, self._rx = self._rx.split(b"\n", 1)
            self._handle_line(raw.decode("utf-8", "replace").strip())
        if len(self._rx) > 512:  # a line that long is noise, not a message
            self._rx = b""

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        if len(line) == 1:
            if line == ".":
                self.beats += 1
                return
            if line in "pr+-?":
                try:
                    self.control(line)
                except Exception:  # noqa: BLE001  a bad key must never kill the sender
                    pass
                return
        self.log.append(line)
        del self.log[:-8]


def _trim_marks(packed: str, visible: int) -> str:
    return pack_marks([(p, k) for p, k in unpack_marks(packed) if p < visible])


def _trim_replaced(packed: str, replaced: str, visible: int) -> str:
    """The replaced letters that survive trimming the marks to the visible window."""
    out = []
    for i, (pos, _kind) in enumerate(unpack_marks(packed)):
        if pos < visible and i < len(replaced):
            out.append(replaced[i])
    return "".join(out)


def compact_event(event: dict, visible: int = BOX_LETTERS, reads: int = BOX_READS) -> dict:
    """The same event, trimmed for the serial link so a line stays small.

    Nothing is invented here; fields the box does not draw are dropped and the strings are
    cut to the visible window.
    """
    t = event.get("t")
    if t == "s":
        st = event.get("st", {})
        return {
            "t": "s",
            "i": event["i"],
            "n": event["n"],
            "nr": event["nr"],
            "ok": event["ok"],
            "d": event.get("drop", False),
            "r": [{"s": r["s"][:visible], "m": _trim_marks(r["m"], visible)}
                  for r in event.get("reads", [])[:reads]],
            "c": event.get("cons", "")[:visible],
            "f": _trim_marks(event.get("fix", ""), visible),
            # the letters the classic decoder had where the model changed something, so the
            # box can show a position flip from the wrong letter to the corrected one
            "fp": _trim_replaced(event.get("fix", ""), event.get("fixfrom", ""), visible),
            # only the numbers the box's header draws. Keep this list in step with the
             # header: leaving "mfix" out once made the box show 0 fixes for a whole run
             # while the server was counting them correctly.
            "st": {k: st[k] for k in ("done", "ok", "mfix", "rfix", "rps", "prog") if k in st},
        }
    if t == "hello":
        return {"t": "hello", "v": event.get("v", PROTOCOL_VERSION), "dec": event.get("decoder", ""),
                # "model" or "vote": the box shows the fallback in the header, in cost
                # magenta, so a silent drop to the classic decoder is never invisible
                "layer": event.get("fix_layer", ""), "ch": event.get("channel", ""),
                "rps": event.get("reads_per_strand", 0), "rate": event.get("rate", 0)}
    if t == "pass":
        return {"t": "pass", "n": event.get("n", 0), "file": event.get("file", ""),
                "ch": event.get("channel", ""), "pass": event.get("pass", 0),
                "bytes": event.get("bytes", 0)}
    if t == "file":
        return {"t": "file", "ok": event.get("ok", False), "file": event.get("file", ""),
                "pass": event.get("pass", 0)}
    if t == "state":
        return {"t": "state", "paused": event.get("paused", False), "rate": event.get("rate", 0)}
    return {"t": str(t)}


# ---------------------------------------------------------------- HTTP server


class TickerHandler(BaseHTTPRequestHandler):
    server_version = "ErbgutTicker/1.0"
    app: Any = None  # set on the server instance

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        if self.app and self.app.verbose:
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _static(self, rel: str) -> None:
        types = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
                 ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png",
                 ".json": "application/json", ".txt": "text/plain; charset=utf-8"}
        if rel.startswith("fonts/"):
            path = DECK_FONT_DIR / rel[len("fonts/"):]
        elif rel.startswith("logo"):
            path = REPO_ROOT / "marketing" / rel
        else:
            path = TICKER_DIR / rel
        path = path.resolve()
        allowed = (TICKER_DIR.resolve(), DECK_FONT_DIR.resolve(), (REPO_ROOT / "marketing").resolve())
        if not any(str(path).startswith(str(a)) for a in allowed) or not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, path.read_bytes(), types.get(path.suffix, "application/octet-stream"))

    # -------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path == "/events":
            self._events()
        elif path == "/api/state":
            self._json(self.app.state())
        elif path == "/api/health":
            self._json({"ok": True})
        else:
            self._static(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/control":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        try:
            self._json(self.app.control(payload))
        except Exception as exc:  # noqa: BLE001  a bad control must not kill the server
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain",
                   {"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"})

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = self.app.hub.subscribe(maxsize=300)
        try:
            for event in [self.app.engine.hello()] + self.app.hub.history():
                self._sse(event)
            last_ping = time.monotonic()
            while True:
                try:
                    self._sse(q.get(timeout=1.0))
                except queue.Empty:
                    if time.monotonic() - last_ping > 10:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.hub.unsubscribe(q)

    def _sse(self, event: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n")
        self.wfile.flush()


# ---------------------------------------------------------------- application


class App:
    def __init__(self, engine: Any, hub: Hub, sender: SerialSender | None, verbose: bool = False,
                 recorder: Any = None) -> None:
        self.engine = engine
        self.hub = hub
        self.sender = sender
        self.verbose = verbose
        self.recorder = recorder
        self.controls: list[str] = []  # what paused or resumed the stream, and who asked

    def state(self) -> dict:
        c = self.engine.config
        return {
            "paused": self.engine.paused.is_set(),
            "rate": c.rate,
            "channel": getattr(c, "channel", ""),
            "channels": list_profiles(),
            "reads_per_strand": getattr(c, "reads_per_strand", 0),
            "source": getattr(c, "source", ""),
            "totals": getattr(self.engine, "last_stats", {}),
            "controls": self.controls,  # the last few pause, resume and speed changes, with their source
            "serial": {
                "enabled": self.sender is not None,
                "connected": bool(self.sender and self.sender.connected),
                "port": (self.sender.port if self.sender else None),
                "error": (self.sender.last_error if self.sender else ""),
                # the box heartbeats once a second; silence for three means it is not running
                "box_alive": bool(self.sender and self.sender.last_rx
                                  and time.monotonic() - self.sender.last_rx < 3.0),
                "beats": (self.sender.beats if self.sender else 0),
                "box_log": (self.sender.log if self.sender else []),
            },
        }

    def control(self, payload: dict, source: str = "http") -> dict:
        """Apply a control and record where it came from.

        Every pause on stage has to be explainable, so the source travels with the action and
        lands in the log, on the console and in /api/state. Nothing in the server ever pauses
        on its own: a missed heartbeat, an unplugged box or a dropped browser tab only affect
        what gets drawn, never whether the decode runs.
        """
        action = str(payload.get("action", "")).lower()
        e = self.engine
        if action == "pause":
            e.paused.set()
        elif action == "resume":
            e.paused.clear()
        elif action == "toggle":
            e.paused.clear() if e.paused.is_set() else e.paused.set()
        elif action == "speed":
            e.set_rate(float(payload.get("rate", 4.0)))
        elif action == "restart":
            e.restart.set()
        elif action == "config":
            fields = {}
            for key in ("channel", "reads_per_strand", "source", "rate", "strand_length",
                        "redundancy", "visible_letters", "visible_reads"):
                if key in payload:
                    fields[key] = payload[key]
            if fields:
                e.set_config(**fields)
        elif action:
            raise ValueError(f"unknown action {action!r}")
        out = self.state()
        if action:
            entry = (f"{time.strftime('%H:%M:%S')} {action} from {source}"
                     f" -> paused={out['paused']} rate={out['rate']:g}")
            self.controls.append(entry)
            del self.controls[:-12]
            print(entry, flush=True)
        self.hub.publish({"t": "state", "paused": out["paused"], "rate": out["rate"]})
        return out

    def key(self, ch: str) -> None:
        """One character on its own line from the box. Anything unknown is ignored."""
        if ch == "p":
            self.control({"action": "pause"}, source="box touch")
        elif ch == "r":
            self.control({"action": "resume"}, source="box touch")
        elif ch == "+":
            self.control({"action": "speed", "rate": self.engine.config.rate * 1.5}, source="box touch")
        elif ch == "-":
            self.control({"action": "speed", "rate": self.engine.config.rate / 1.5}, source="box touch")
        elif ch == "?" and self.sender is not None:
            self.sender._resend()


# ---------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--channel", default="nanopore_budget", help=f"one of {list_profiles()}")
    p.add_argument("--reads", type=float, default=TickerConfig.reads_per_strand, help="reads per strand (profile coverage_mean)")
    p.add_argument("--rate", type=float, default=TickerConfig.rate, help="strands per second released to the stream")
    p.add_argument("--file", default=TickerConfig.source, help="'message', 'image', or a path to a file")
    p.add_argument("--max-bytes", type=int, default=4096)
    p.add_argument("--checkpoint", default=None, help="polish checkpoint; default looks in checkpoints/polish/")
    p.add_argument("--serial-port", default=None, help="default: the first /dev/cu.usbmodem*")
    p.add_argument("--baud", type=int, default=921600)
    p.add_argument("--no-serial", action="store_true", help="browser view only, do not touch the box")
    p.add_argument("--record", default=None, help="write every event to this jsonl file while serving")
    p.add_argument("--replay", default=None, help="replay a recording instead of decoding")
    p.add_argument("--dump", default=None, help="write a recording and exit, no server")
    p.add_argument("--dump-strands", type=int, default=120, help="strand events for --dump")
    p.add_argument("--open", action="store_true", help="open the browser view")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = TickerConfig(channel=args.channel, reads_per_strand=args.reads, rate=args.rate,
                          source=args.file, max_bytes=args.max_bytes,
                          checkpoint=args.checkpoint).clamp()

    if args.dump:
        path = record_stream(config, args.dump_strands, Path(args.dump))
        print(f"wrote {len(read_recording(path))} events to {path}")
        return 0

    hub = Hub()
    record_file = None
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        record_file = Path(args.record).open("w", encoding="utf-8")
    lock = threading.Lock()

    def emit(event: dict) -> None:
        if record_file is not None:
            with lock:
                record_file.write(json.dumps(event, separators=(",", ":")) + "\n")
                record_file.flush()
        hub.publish(event)

    engine: Any
    if args.replay:
        engine = ReplayEngine(Path(args.replay), emit, rate=args.rate)
        print(f"replaying {args.replay} ({len(engine.events)} events), no decode is running")
    else:
        engine = Engine(config, emit)
        if engine.backend.has_model:
            print(f"decoder: {engine.backend.name}")
            print(f"  {engine.backend.note}")
        else:
            bar = "!" * 78
            print(f"\n{bar}\nWARNING: the learned polisher is NOT running, so the ticker's "
                  f"'fixes' counter will stay at 0.\n{engine.backend.note}\n"
                  "Install torch and put a checkpoint at checkpoints/polish/polish.pt, or pass "
                  "--checkpoint.\n"
                  f"{bar}\n")

    hub.engine = engine
    app = App(engine, hub, None, verbose=args.verbose)
    sender = None
    if not args.no_serial:
        sender = SerialSender(hub, app.key, port=args.serial_port, baud=args.baud)
        app.sender = sender
        threading.Thread(target=sender.run, daemon=True).start()
        found = find_serial_port(args.serial_port)
        print(f"usb serial: {found or 'no port yet, will keep looking'}")

    handler = type("Handler", (TickerHandler,), {"app": app})
    httpd = ThreadingHTTPServer((args.host, args.port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=engine.run, daemon=True).start()

    url = f"http://{args.host}:{args.port}/"
    print(f"browser view: {url}")
    print("press ctrl-c to stop")
    if args.open:
        import webbrowser

        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        engine.stop.set()
        if sender is not None:
            sender.stop.set()
        httpd.shutdown()
        if record_file is not None:
            record_file.close()
            print(f"recording written to {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
