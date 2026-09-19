"""Live demo: one file through the channel, default codec vs tuned codec (PROJECT.md demo step 5).

This is a showcase, not an evaluation. The numbers that judge codecs come from
dnacodec.evaluate over 300 held-out trials (results/<run_id>/<situation>.json). A demo
round trip is one channel pass of a small file, so it can go either way on any single seed:
default lost and tuned recovered, both recovered, or both lost. All three are shown as they are.

Seed rule (fixed before looking at any outcome): the demo uses the first DEMO_TRIALS train
seeds, DEMO_SEED_START, DEMO_SEED_START + 1, ... (see demo_seeds()). They go through
train_seed(), so a held-out seed can never be used here. Nobody searches for a seed where the
tuned codec wins; tools that run one trial show all of these seeds, not a chosen one.

Everything here goes through the existing APIs: encoder.encode / encoder.recover,
simulator.simulate, a Decoder, profiles.load_profile, and evaluate.evaluate for strand
accuracy and bits per base.
"""

from __future__ import annotations

import html
import json
import math
import pickle
import struct
import time
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

import numpy as np

from . import results as results_mod
from .encoder import encode, recover
from .evaluate import evaluate
from .profiles import SituationProfile
from .seeds import train_seed
from .simulator import simulate
from .types import Decoder, EncoderSettings, Metrics, Scorer

# ---------------------------------------------------------------- seeds

DEMO_SEED_START = 0  # the first train seeds, fixed by rule before any demo was run
DEMO_TRIALS = 5
MAX_DEMO_BYTES = 5 * 1024  # keeps a round trip to a few seconds on a laptop


def demo_seeds(n: int = DEMO_TRIALS) -> list[int]:
    """The fixed demo seeds: the first n train seeds. Never chosen by looking at outcomes."""
    return [train_seed(DEMO_SEED_START + i) for i in range(n)]


# ---------------------------------------------------------------- strand status

CORRECT = "correct"  # decoded exactly
CAUGHT = "caught"  # decoded wrong, the per-strand checksum caught it, so it counts as missing
LOST = "lost"  # no reads at all (dropout)
SLIPPED = "slipped"  # decoded wrong and passed its checksum (about 1 in 65,536 wrong strands)
STATUSES = (CORRECT, CAUGHT, LOST, SLIPPED)

STATUS_TEXT = {
    CORRECT: "correct",
    CAUGHT: "wrong, caught by the checksum",
    LOST: "lost, no reads",
    SLIPPED: "wrong, passed the checksum",
}

# Erbgut palette (marketing/BRAND.md): G gain teal, C cost magenta, T amber, A audit indigo.
BRAND = {
    "audit": "#3A3FC2",
    "cost": "#B02A63",
    "gain": "#0B7465",
    "tbd_fill": "#F4C45A",
    "tbd_text": "#8A5A00",
    "paper": "#F2F5F4",
    "surface": "#FFFFFF",
    "ink": "#131B20",
    "muted": "#4B5A60",
    "rule": "#CBD4D2",
}
STATUS_COLOR = {CORRECT: BRAND["gain"], CAUGHT: BRAND["cost"], LOST: BRAND["tbd_fill"], SLIPPED: BRAND["ink"]}


def _checksum_ok(strand: str, settings: EncoderSettings) -> bool:
    """Does a decoded strand pass its per-strand CRC? Uses the encoder's own layout parser."""
    try:
        from .encoder import _Layout  # read-only use of the encoder's parser

        return _Layout(settings).parse(strand) is not None
    except Exception:  # noqa: BLE001  if the parser is unavailable, treat wrong strands as caught
        return False


def strand_statuses(references: Sequence[str], decoded: Sequence[str | None], clusters: Sequence[list[str]],
                    settings: EncoderSettings) -> list[str]:
    out = []
    for ref, guess, cluster in zip(references, decoded, clusters):
        if len(cluster) == 0 or guess is None:
            out.append(LOST)
        elif guess == ref:
            out.append(CORRECT)
        elif _checksum_ok(guess, settings):
            out.append(SLIPPED)
        else:
            out.append(CAUGHT)
    return out


# ---------------------------------------------------------------- round trip


@dataclass
class RoundTripResult:
    label: str
    settings: EncoderSettings
    scorer_name: str
    decoder_name: str
    profile_name: str
    reads_budget: float  # profile.coverage_mean used for this pass
    seed: int
    data: bytes
    n_chunks: int
    recovered: bytes | None  # the exact file, or None when it could not be recovered
    statuses: list[str]
    cluster_sizes: list[int]
    metrics: Metrics  # from dnacodec.evaluate.evaluate
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.recovered is not None and self.recovered == self.data

    @property
    def n_strands(self) -> int:
        return len(self.statuses)

    @property
    def counts(self) -> dict[str, int]:
        return {s: self.statuses.count(s) for s in STATUSES}

    @property
    def strand_accuracy(self) -> float:
        return self.metrics.strand_accuracy

    @property
    def bits_per_base(self) -> float:
        return float(self.metrics.bits_per_base or 0.0)

    @property
    def reads_per_strand(self) -> float:
        return self.metrics.reads_per_strand

    @property
    def usable(self) -> int:
        """Strands the Fountain code can use: decoded correctly (slipped ones poison, not help)."""
        return self.counts[CORRECT]

    @property
    def needed(self) -> int:
        """Lower bound: the Fountain code needs at least one usable strand per source chunk."""
        return self.n_chunks

    @property
    def total_seconds(self) -> float:
        return sum(self.timings.values())


def scorer_name(scorer: Scorer | None) -> str:
    if scorer is None or getattr(scorer, "is_rule_scorer", False):
        return "rule scorer"
    return f"learned risk model ({type(scorer).__name__})"


def round_trip(
    data: bytes,
    settings: EncoderSettings,
    scorer: Scorer | None,
    decoder: Decoder,
    profile: SituationProfile,
    seed: int,
    label: str = "",
) -> RoundTripResult:
    """Encode, simulate one channel pass, decode, recover. One seed, one trial.

    profile.coverage_mean is the read budget (mean reads per surviving strand); pass a
    replaced profile to change it. seed goes through train_seed(): the demo never touches
    held-out seeds.
    """
    seed = train_seed(int(seed))
    data = bytes(data)
    t = {}
    t0 = time.perf_counter()
    encoded = encode(data, settings, scorer)
    t["encode"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    clusters = simulate(encoded.strands, profile, seed)
    t["simulate"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    decoded = list(decoder.decode(clusters, settings.strand_length))
    t["decode"] = time.perf_counter() - t0
    if len(decoded) != len(encoded.strands):
        raise ValueError(f"decoder returned {len(decoded)} strands for {len(encoded.strands)} clusters")

    t0 = time.perf_counter()
    recovered = recover(decoded, encoded.meta)
    t["recover"] = time.perf_counter() - t0
    if recovered is not None and recovered != data:  # recover() checks a CRC-32; never show a wrong file
        recovered = None

    statuses = strand_statuses(encoded.strands, decoded, clusters, settings)
    metrics = evaluate(encoded.strands, decoded, clusters, profile, encoded.meta,
                       file_recovered=recovered is not None)
    return RoundTripResult(
        label=label, settings=settings, scorer_name=scorer_name(scorer),
        decoder_name=getattr(decoder, "name", type(decoder).__name__), profile_name=profile.name,
        reads_budget=float(profile.coverage_mean), seed=seed, data=data, n_chunks=encoded.meta.n_chunks,
        recovered=recovered, statuses=statuses, cluster_sizes=[len(c) for c in clusters],
        metrics=metrics, timings=t,
    )


def outcome(default: RoundTripResult, tuned: RoundTripResult) -> str:
    """Plain-language outcome of one side-by-side pass. Every combination is a valid result."""
    if default.ok and tuned.ok:
        return "Both codecs recovered the file."
    if tuned.ok:
        return "The default codec lost the file. The tuned codec recovered it exactly."
    if default.ok:
        return "The default codec recovered the file. The tuned codec lost it."
    return "Both codecs lost the file on this pass."


# ---------------------------------------------------------------- the tuned codec


class TunedCodecUnavailable(RuntimeError):
    """Neither the frozen codec nor the loop's result file exists locally for this run."""


@dataclass
class TunedCodec:
    settings: EncoderSettings
    scorer: Scorer | None  # None = rule scorer
    run_id: str
    situation: str
    source: str  # "checkpoint" (frozen codec with its risk model) or "results" (settings only)
    iteration: int | None
    stage: str
    note: str  # plain-language description of what was loaded and what is missing
    exact: bool  # True if this is exactly the codec the loop evaluated
    is_mock: bool = False
    # Reference numbers from the loop's held-out evaluation (300 trials), when available.
    default_min_reads: float | None = None
    tuned_min_reads: float | None = None
    default_heldout_recovery: float | None = None
    tuned_heldout_recovery: float | None = None
    matched_min_reads: float | None = None  # default rules at the tuned codec's bits per base
    n_heldout_trials: int | None = None
    default_settings: EncoderSettings | None = None


def _codec_root(codec_root: Path | None) -> Path:
    if codec_root is not None:
        return Path(codec_root)
    from .loop import CODEC_DIR

    return CODEC_DIR


def _load_run(run_id: str, situation: str, results_root: Path | None):
    root = Path(results_root) if results_root is not None else results_mod.RESULTS_DIR
    path = root / run_id / f"{situation}.json"
    if not path.exists():
        return None
    return results_mod.RunResult.from_dict(json.loads(path.read_text()))


def _resolve(path_str: str, manifest_dir: Path) -> Path:
    """Manifests store absolute paths from the machine that ran the loop; fall back to the
    file of the same name next to the manifest."""
    p = Path(path_str)
    return p if p.exists() else manifest_dir / p.name


def _pick(n: int, iteration: int | None) -> int:
    if iteration is None:
        return n - 1
    idx = iteration if iteration >= 0 else n + iteration
    if not 0 <= idx < n:
        raise TunedCodecUnavailable(f"iteration {iteration} does not exist (the run has {n})")
    return idx


def load_tuned_codec(
    run_id: str,
    situation: str,
    iteration: int | None = None,
    *,
    codec_root: Path | None = None,
    results_root: Path | None = None,
) -> TunedCodec:
    """The tuned codec of a loop run for one situation.

    1. Frozen codec: checkpoints/loop/<run_id>/<situation>/manifest.json (written by
       loop.CodecStore). Takes the chosen iteration (default: the last one, the full loop,
       ablation E) with its pickled risk model, like scripts/run_experiments.py does.
    2. Otherwise the loop's result file results/<run_id>/<situation>.json, settings only.
       Without the risk model the codec that was evaluated can only be rebuilt exactly for
       the tier-1 iteration (rule scorer). By default that one is used; asking for another
       iteration drops its risk threshold and says it's an approximation.
    Raises TunedCodecUnavailable with a clear message when neither exists.
    """
    run = _load_run(run_id, situation, results_root)
    ref = {}
    if run is not None:
        ref = dict(
            is_mock=run.is_mock, default_min_reads=run.default_min_reads_at_target,
            default_heldout_recovery=run.default_metrics.recovery_rate,
            n_heldout_trials=run.n_trials, default_settings=run.default_settings,
        )

    def with_ref(codec: TunedCodec) -> TunedCodec:
        if run is not None and codec.iteration is not None and codec.iteration < len(run.iterations):
            it = run.iterations[codec.iteration]
            codec.tuned_min_reads = it.min_reads_at_target
            codec.tuned_heldout_recovery = it.metrics.recovery_rate
            codec.matched_min_reads = it.default_min_reads_matched
        return codec

    # 1. Frozen codec from the loop.
    manifest_path = _codec_root(codec_root) / run_id / situation / "manifest.json"
    why_not_checkpoint = f"no frozen codec at {manifest_path}"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            its = manifest["iterations"]
            if not its:
                raise TunedCodecUnavailable("the manifest has no iterations yet")
            entry = its[_pick(len(its), iteration)]
            with _resolve(entry["risk_path"], manifest_path.parent).open("rb") as f:
                risk = pickle.load(f)
            idx = int(entry.get("iteration", its.index(entry)))
            stage = str(entry.get("stage_name") or entry.get("stage") or "")
            dec = entry.get("decoder", {}).get("kind", "?")
            note = (f"Frozen codec from run {run_id}, iteration {idx} ({stage}), "
                    f"{'with its learned risk model' if risk is not None else 'rule scorer'}. "
                    f"The loop tuned it with the {dec} decoder.")
            if manifest.get("mocked"):
                ref["is_mock"] = True
            if run is None:
                ref.setdefault("default_settings", EncoderSettings(**manifest.get("default_settings", {})))
            return with_ref(TunedCodec(
                settings=EncoderSettings(**entry["settings"]), scorer=risk, run_id=run_id, situation=situation,
                source="checkpoint", iteration=idx, stage=stage, note=note, exact=True, **ref,
            ))
        except TunedCodecUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  e.g. torch missing to unpickle the risk model
            why_not_checkpoint = f"frozen codec at {manifest_path} could not be loaded ({type(e).__name__}: {e})"

    # 2. Settings from the loop's result file.
    if run is None:
        raise TunedCodecUnavailable(
            f"No tuned codec for run '{run_id}', situation '{situation}' on this machine: {why_not_checkpoint}, "
            f"and no result file at results/{run_id}/{situation}.json. Sync the run's results "
            "(and checkpoints/loop/ for the risk model) from the GX10, or choose another run."
        )
    if not run.iterations:
        raise TunedCodecUnavailable(f"run '{run_id}' has no tuned iteration for '{situation}' yet")
    tier1 = [i for i, it in enumerate(run.iterations) if it.stage == "tier1"]
    if iteration is None and tier1:
        idx = tier1[-1]
    else:
        idx = _pick(len(run.iterations), iteration)
    it = run.iterations[idx]
    exact = it.stage == "tier1"
    settings = it.settings if exact else replace(it.settings, risk_threshold=None)
    later = len(run.iterations) - 1 - idx
    if exact:
        note = (f"Tier-1 codec from run {run_id} (audited rules and tuned redundancy, rule scorer), rebuilt "
                "exactly from the result file. " + (f"The {later} later loop iteration(s) need their learned risk "
                f"model, which is not on this machine ({why_not_checkpoint}), so they are not used here."
                if later else ""))
    else:
        note = (f"APPROXIMATION: settings of iteration {idx} ({it.stage}) from run {run_id} without its learned "
                f"risk model ({why_not_checkpoint}). The rule scorer picks candidates instead and the risk "
                "threshold is dropped, so this is not exactly the codec the loop evaluated.")
    return with_ref(TunedCodec(
        settings=settings, scorer=None, run_id=run_id, situation=situation, source="results",
        iteration=idx, stage=it.stage, note=note.strip(), exact=exact, **ref,
    ))


def tradeoff_note(default: EncoderSettings, tuned: TunedCodec, default_bpb: float, tuned_bpb: float) -> str:
    """Plain-language statement of what the tuned codec pays, so a win is never oversold."""
    parts = []
    if tuned_bpb < default_bpb - 1e-9:
        parts.append(
            f"The tuned codec stores {tuned_bpb:.2f} bits per DNA letter instead of {default_bpb:.2f}: it writes more "
            "spare strands. At this read budget the loop found that to be the cheapest setting that brings the "
            "file back reliably, so it trades more synthesis for cheaper reading.")
    elif tuned_bpb > default_bpb + 1e-9:
        parts.append(f"The tuned codec is also denser: {tuned_bpb:.2f} bits per DNA letter instead of {default_bpb:.2f}.")
    if tuned.matched_min_reads is not None and tuned.tuned_min_reads is not None:
        if tuned.matched_min_reads <= tuned.tuned_min_reads:
            parts.append(
                f"At the same density the default rules also reach the target at {tuned.matched_min_reads:g} reads, "
                "so this gain comes from the tuned redundancy and strand length, not from changing the rules.")
        else:
            parts.append(
                f"At the same density the default rules need {tuned.matched_min_reads:g} reads per strand versus "
                f"{tuned.tuned_min_reads:g} for the tuned codec.")
    return " ".join(parts)


def list_result_runs(results_root: Path | None = None) -> list[str]:
    root = Path(results_root) if results_root is not None else results_mod.RESULTS_DIR
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and any(p.glob("*.json")))


def describe_settings(s: EncoderSettings) -> str:
    hp = f"runs of at most {s.max_homopolymer}" if s.max_homopolymer is not None else "run rule off"
    gc = (f"GC {s.gc_min:g} to {s.gc_max:g}" if s.gc_min is not None and s.gc_max is not None
          else "GC rule off")
    thr = f", risk threshold {s.risk_threshold:.3g}" if s.risk_threshold is not None else ""
    return f"{s.strand_length} letters per strand, {s.redundancy:g} spare strands per data strand, {hp}, {gc}{thr}"


# ---------------------------------------------------------------- file helpers


def sniff_kind(data: bytes) -> str:
    """'png', 'jpeg', 'gif', 'webp', 'text' or 'binary'."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text" if all(c.isprintable() or c in "\n\r\t" for c in text) else "binary"


IMAGE_KINDS = ("png", "jpeg", "gif", "webp")
EXTENSION = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp", "text": ".txt", "binary": ".bin"}


def png_bytes(pixels: np.ndarray) -> bytes:
    """Minimal PNG writer for an H x W x 3 uint8 array (no imaging library needed)."""
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    h, w, _ = pixels.shape
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(h))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _rgb(hex_color: str) -> np.ndarray:
    h = hex_color.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.int16)


def demo_image_png(size: int = 64) -> bytes:
    """The default demo image, drawn in code: the Erbgut mark (bars A, C, G, G on a strand,
    a dimension line over the run of two G bars) on paper with a light grain. The grain is
    fixed (not a channel seed) and gives the file a realistic size of a few KB."""
    s = size / 64
    img = np.empty((size, size, 3), dtype=np.int16)
    img[:] = _rgb(BRAND["paper"])
    grain = np.random.default_rng(64).integers(-7, 8, size=(size, size, 1))
    img = img + grain
    ink = _rgb(BRAND["ink"])

    def rect(x0: float, y0: float, x1: float, y1: float, color: np.ndarray) -> None:
        img[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)] = color

    bars = [BRAND["audit"], BRAND["cost"], BRAND["gain"], BRAND["gain"]]
    for i, color in enumerate(bars):
        x = 8 + i * 13
        rect(x, 20, x + 9, 48, _rgb(color))
    rect(5, 48, 59, 51, ink)  # the strand
    rect(34, 12, 56, 13, ink)  # dimension line over the run
    rect(34, 9, 35, 16, ink)
    rect(55, 9, 56, 16, ink)
    return png_bytes(np.clip(img, 0, 255).astype(np.uint8))


DEFAULT_MESSAGE = (
    "Hello from HackMIT 2026. This message was written into DNA strands, read back through a "
    "simulated Nanopore sequencer with only a few noisy reads per strand, and decoded again. "
    "Erbgut measures whether each DNA coding rule, and each extra strand of redundancy, "
    "pays off on your channel, and tunes the codec to keep only what pays. "
    "Measure the rule. Keep what pays."
)


# ---------------------------------------------------------------- strand map


def _grid_shape(n: int, columns: int | None) -> tuple[int, int]:
    cols = columns or max(10, min(40, math.ceil(math.sqrt(n * 2))))
    return cols, max(1, math.ceil(n / cols))


def strand_map_svg(result: RoundTripResult, columns: int | None = None, cell: int = 12, title: str | None = None) -> str:
    """Grid of strands in encoding order: teal = correct, magenta with a cross = wrong but caught by
    the checksum, amber with a dashed edge = lost with no reads. Each cell has a tooltip."""
    n = result.n_strands
    cols, rows = _grid_shape(n, columns)
    gap = 2
    pad = 8
    w = pad * 2 + cols * (cell + gap) - gap
    h = pad * 2 + rows * (cell + gap) - gap
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
             f'style="max-width:{w * 2}px" role="img" aria-label="{html.escape(title or "strand map")}">',
             f'<rect width="{w}" height="{h}" rx="6" fill="{BRAND["surface"]}" stroke="{BRAND["rule"]}"/>']
    if title:
        parts.append(f"<title>{html.escape(title)}</title>")
    for i, (status, reads) in enumerate(zip(result.statuses, result.cluster_sizes)):
        x = pad + (i % cols) * (cell + gap)
        y = pad + (i // cols) * (cell + gap)
        tip = f"<title>strand {i + 1}: {STATUS_TEXT[status]}, {reads} read{'s' if reads != 1 else ''}</title>"
        color = STATUS_COLOR[status]
        if status == LOST:
            parts.append(f'<g><rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" height="{cell - 2}" rx="2" '
                         f'fill="{BRAND["tbd_fill"]}" stroke="{BRAND["tbd_text"]}" stroke-width="1.2" stroke-dasharray="2 1.5"/>{tip}</g>')
        elif status == CAUGHT:
            parts.append(f'<g><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>'
                         f'<path d="M{x + 3} {y + 3}L{x + cell - 3} {y + cell - 3}M{x + cell - 3} {y + 3}L{x + 3} {y + cell - 3}" '
                         f'stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>{tip}</g>')
        elif status == SLIPPED:
            parts.append(f'<g><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>'
                         f'<circle cx="{x + cell / 2}" cy="{y + cell / 2}" r="2" fill="#fff"/>{tip}</g>')
        else:
            parts.append(f'<g><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>{tip}</g>')
    parts.append("</svg>")
    return "".join(parts)


def strand_map_png(result: RoundTripResult, columns: int | None = None, cell: int = 12) -> bytes:
    """Same picture as strand_map_svg, as a PNG (for slides and the CLI)."""
    n = result.n_strands
    cols, rows = _grid_shape(n, columns)
    gap, pad = 2, 8
    w = pad * 2 + cols * (cell + gap) - gap
    h = pad * 2 + rows * (cell + gap) - gap
    img = np.empty((h, w, 3), dtype=np.int16)
    img[:] = _rgb(BRAND["surface"])
    white = _rgb("#FFFFFF")
    for i, status in enumerate(result.statuses):
        x = pad + (i % cols) * (cell + gap)
        y = pad + (i // cols) * (cell + gap)
        block = img[y:y + cell, x:x + cell]
        if status == LOST:
            edge = _rgb(BRAND["tbd_text"])
            block[:] = _rgb(BRAND["tbd_fill"])
            block[0, :] = edge
            block[-1, :] = edge
            block[:, 0] = edge
            block[:, -1] = edge
            continue
        block[:] = _rgb(STATUS_COLOR[status])
        if status == CAUGHT:
            for k in range(3, cell - 3):
                block[k, k] = white
                block[k, cell - 1 - k] = white
        elif status == SLIPPED:
            c = cell // 2
            block[c - 1:c + 1, c - 1:c + 1] = white
    return png_bytes(np.clip(img, 0, 255).astype(np.uint8))
