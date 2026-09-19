"""The alternating loop. Owner: Agent G.

For one situation profile at its read budget B = profile.coverage_mean (PROJECT.md,
"The loop alternates, it doesn't co-train"):

  1. Adapt the decoder to this channel (on strands of the default codec), then freeze it.
     The default codec (system B) is evaluated with exactly this decoder.
  2. Label strands: generate controlled strands, simulate each K times at coverage B,
     label = fraction decoded wrongly by the frozen decoder (train seeds).
  3. Train the risk model on those labels.
  4. Grid search encoder settings with the risk-scored encoder (train seeds only):
     50 screening trials per candidate, 300 re-check trials before a candidate is chosen.
     Chosen = highest bits per base that meets the recovery target at coverage B.
  5. Re-adapt the decoder to the strands the new encoder produces.

At most three alternations. After every alternation the chosen codec is evaluated once on
held-out seeds and a RunResult is saved, so the dashboard shows progress live. Held-out
results never feed back into any choice: the search only ever sees train seeds, and the
stopping rule only looks at the chosen settings.

Components (trial runner, labeler, risk trainer, decoder adaptation) are injected through
`Components`, with defaults that import the real implementations lazily, so tests run on
mocks and real modules drop in without changes.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import pickle
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from .encoder import encode, payload_bits_per_base, rule_scorer
from .profiles import SituationProfile
from .results import CoveragePoint, IterationResult, RunResult, save_run
from .seeds import heldout_seeds, is_heldout, train_seed
from .testfile import test_file
from .types import ALPHABET, Decoder, EncoderSettings, Metrics, Scorer, Strand

log = logging.getLogger("dnacodec.loop")

ROOT = Path(__file__).resolve().parents[1]
# Frozen risk models and adapted decoder checkpoints per run. Gitignored, not read by the dashboard.
CODEC_DIR = ROOT / "checkpoints" / "loop"

MAX_ALTERNATIONS = 3

# Train seed ranges, all far away from the held-out block (900_000 .. 900_999).
SEED_TIMING = 1_000_000
SEED_ADAPT = 2_000_000  # + 100_000 * alternation
SEED_LABEL = 3_000_000  # + 1_000_000 * alternation
SEED_THRESHOLD = 7_000_000  # + 10_000 * alternation
SEED_SCREEN = 10_000_000  # + 10_000 * alternation
SEED_RECHECK = 11_000_000  # + 10_000 * alternation


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class SearchGrid:
    """The settings the loop may turn. Every combination is one candidate."""

    redundancy: tuple[float, ...] = (0.05, 0.1, 0.15, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0)
    strand_length: tuple[int, ...] = (110, 140)
    max_homopolymer: tuple[int | None, ...] = (3, None)  # None = homopolymer rule off
    gc_rule: tuple[bool, ...] = (True, False)  # True = default GC window on, False = off
    # Risk threshold as a quantile of candidate risk: 0.75 rejects the riskiest 25% of the
    # candidates that pass the hard constraints. None = no threshold (reject 0%). Turned into
    # an absolute threshold per alternation, strand length and hard-constraint combination,
    # because risk scales differ a lot between profiles and between retrained models.
    risk_quantile: tuple[float | None, ...] = (None, 0.9, 0.75, 0.5)
    # Redundancies tried only if nothing in the grid meets the target.
    fallback_redundancy: tuple[float, ...] = (1.3, 1.6, 2.0)


@dataclass(frozen=True)
class LoopConfig:
    target: float = 1.0  # recovery target: fraction of trials recovered exactly
    screen_trials: int = 50  # train-seed trials per candidate (PROJECT.md trial budget)
    recheck_trials: int = 300  # train-seed re-check before a candidate may be chosen
    eval_trials: int = 300  # held-out trials per final codec
    first_screen_batch: int = 10  # screening runs in batches and stops at the first miss
    max_rechecks_per_level: int = 4  # re-checks per bits-per-base level before moving on
    label_strands: int = 2000  # labeled strands per alternation (split over strand lengths)
    label_k: int = 20  # simulations per labeled strand
    threshold_strands: int = 2000  # random strands used to turn risk quantiles into thresholds
    adapt_passes: int = 8  # train-seed channel passes over the encoded file for decoder adaptation
    adapt_steps: int = 2000  # fine-tuning steps (transformer only)
    # Coverages for min_reads_at_target. Must reach above the largest budget (illumina: 20).
    coverages: tuple[float, ...] = (2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 25, 30)
    curve_coverages: tuple[float, ...] = (2, 4, 6, 8, 10, 15, 20)
    curve_trials: int = 50  # held-out trials per coverage-curve point
    stop_when_converged: bool = True  # stop when decoder unchanged and settings repeat
    workers: int | None = None
    grid: SearchGrid = field(default_factory=SearchGrid)
    quick: bool = False

    @classmethod
    def quick_mode(cls, workers: int | None = None) -> LoopConfig:
        """Tiny budgets for tests and demos. Numbers from quick runs are not the objective."""
        return cls(
            screen_trials=6,
            recheck_trials=12,
            eval_trials=12,
            first_screen_batch=3,
            max_rechecks_per_level=2,
            label_strands=200,
            label_k=4,
            threshold_strands=300,
            adapt_passes=2,
            adapt_steps=50,
            coverages=(2, 4, 6, 8, 10, 14, 20, 30),
            curve_coverages=(4, 6, 10, 20),
            curve_trials=4,
            workers=workers,
            grid=SearchGrid(
                redundancy=(0.1, 0.3, 0.6, 1.0),
                strand_length=(110, 140),
                max_homopolymer=(3, None),
                gc_rule=(True,),
                risk_quantile=(None, 0.75),
                fallback_redundancy=(1.6,),
            ),
            quick=True,
        )


# ---------------------------------------------------------------- components


def _call_flexible(fn: Callable, **available: Any) -> Any:
    """Call fn with the subset of `available` its signature accepts (aliases resolved by name).

    Used for the functions of other agents whose exact signature is not fixed yet
    (generate_strands, label_failure_rates, finetune). Raises TypeError naming what's missing.
    """
    params = inspect.signature(fn).parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return fn(**available)
    kwargs = {k: v for k, v in available.items() if k in params}
    missing = [
        n for n, p in params.items()
        if n not in kwargs and p.default is p.empty and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    if missing:
        raise TypeError(f"{fn.__module__}.{fn.__qualname__} needs {missing}; loop can pass {sorted(available)}")
    return fn(**kwargs)


def _real_recovery_trials(data, settings, scorer, decoder, profile, seeds, workers=None) -> Metrics:
    from .evaluate import recovery_trials

    return recovery_trials(data, settings, scorer, decoder, profile, seeds, workers=workers)


def _real_min_reads(data, settings, scorer, decoder, profile, seeds, target, coverages, workers=None):
    from .evaluate import min_reads_at_target

    return min_reads_at_target(
        data, settings, scorer, decoder, profile, seeds, target=target, coverages=coverages, workers=workers
    )


def _real_generate_strands(n: int, length: int, seed: int) -> list[Strand]:
    from .risk import generate_strands

    return list(
        _call_flexible(
            generate_strands, n=n, n_strands=n, count=n, length=length, strand_length=length,
            seed=seed, rng=np.random.default_rng(seed),
        )
    )


def _real_label(strands, profile, decoder, k, seed, workers=None) -> np.ndarray:
    from .risk import label_failure_rates

    return np.asarray(
        _call_flexible(
            label_failure_rates, strands=strands, profile=profile, decoder=decoder,
            k=k, K=k, n_sims=k, n_simulations=k, seed=seed, base_seed=seed, workers=workers,
        ),
        dtype=np.float64,
    )


def _real_train_risk(strands: Sequence[Strand], labels: np.ndarray, seed: int) -> Scorer:
    from .risk import RiskModel

    model = RiskModel(seed=train_seed(seed))
    return model.fit(strands, labels) or model


def _baseline_decoder() -> Decoder:
    from .baseline import MajorityVoteDecoder

    return MajorityVoteDecoder()


def make_decoder(kind: str = "baseline", checkpoint: str | Path | None = None) -> Decoder:
    """'baseline' = MajorityVoteDecoder, 'transformer' = TransformerDecoder(checkpoint)."""
    if kind == "baseline":
        return _baseline_decoder()
    if kind == "transformer":
        if checkpoint is None:
            raise ValueError("transformer decoder needs a checkpoint")
        from .model.decoder import TransformerDecoder

        return TransformerDecoder(checkpoint)
    raise ValueError(f"unknown decoder kind {kind!r}")


def decoder_spec(decoder: Decoder) -> dict:
    path = getattr(decoder, "checkpoint_path", None)
    return {"kind": decoder.name, "checkpoint": str(path) if path is not None else None}


def default_adapt(
    decoder: Decoder,
    profile: SituationProfile,
    strands: Sequence[Strand],
    seeds: Sequence[int],
    out_path: Path,
    steps: int,
) -> Decoder:
    """Fine-tune a checkpointed decoder on this channel; decoders without a checkpoint
    (the majority vote baseline) are returned unchanged, i.e. adaptation is a no-op."""
    checkpoint = getattr(decoder, "checkpoint_path", None)
    if checkpoint is None:
        return decoder
    from .simulator import simulate

    try:
        from .model.finetune import finetune
    except ImportError:  # pragma: no cover - layout fallback
        from .model import finetune  # type: ignore[attr-defined,no-redef]

    clusters, references = [], []
    for s in seeds:
        clusters.extend(simulate(strands, profile, train_seed(s)))
        references.extend(strands)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _call_flexible(
        finetune, checkpoint_path=checkpoint, clusters=clusters, references=references,
        steps=steps, out_path=out_path, seed=train_seed(seeds[0]) if seeds else 0,
    )
    return make_decoder("transformer", out_path)


@dataclass
class Components:
    """Everything the loop calls that another agent owns. None = the real implementation."""

    # (data, settings, scorer, decoder, profile, seeds, workers) -> Metrics
    recovery_trials: Callable | None = None
    # (data, settings, scorer, decoder, profile, seeds, target, coverages, workers) -> float | None
    min_reads_at_target: Callable | None = None
    # (n, length, seed) -> list[Strand]
    generate_strands: Callable | None = None
    # (strands, profile, decoder, k, seed, workers) -> np.ndarray of failure rates
    label_failure_rates: Callable | None = None
    # (strands, labels, seed) -> risk model (a Scorer, ideally with top_kmers)
    train_risk: Callable | None = None
    # (decoder, profile, strands, seeds, out_path, steps) -> Decoder
    adapt: Callable | None = None
    # () -> Decoder, the majority vote baseline (system A)
    baseline_decoder: Callable | None = None
    mocked: tuple[str, ...] = ()  # names of mocked components, for notes and is_mock

    def resolved(self) -> Components:
        return Components(
            recovery_trials=self.recovery_trials or _real_recovery_trials,
            min_reads_at_target=self.min_reads_at_target or _real_min_reads,
            generate_strands=self.generate_strands or _real_generate_strands,
            label_failure_rates=self.label_failure_rates or _real_label,
            train_risk=self.train_risk or _real_train_risk,
            adapt=self.adapt or default_adapt,
            baseline_decoder=self.baseline_decoder or _baseline_decoder,
            mocked=self.mocked,
        )


# ---------------------------------------------------------------- helpers


def assert_train_seeds(seeds: Sequence[int]) -> list[int]:
    """Every seed that may influence a choice passes through here."""
    bad = [s for s in seeds if is_heldout(s)]
    if bad:
        raise AssertionError(f"held-out seeds reached training or search: {bad[:5]}")
    return [train_seed(s) for s in seeds]


def train_seeds(start: int, n: int) -> list[int]:
    return assert_train_seeds(range(start, start + n))


_BPB_CACHE: dict[tuple[int, int, int], tuple[Any, int]] = {}


def bits_per_base(data: bytes, settings: EncoderSettings) -> float:
    """Net bits per synthesized base of a setting, without running any trial.

    The strand count is ceil(n_chunks * (1 + redundancy)) and n_chunks depends only on the
    strand layout and the file size, so one unconstrained encode per layout is enough.
    """
    key = (settings.strand_length, settings.seed_bases, len(data))
    if key not in _BPB_CACHE:
        probe = replace(settings, redundancy=0.0, max_homopolymer=None, gc_min=None, gc_max=None, risk_threshold=None)
        meta = encode(data, probe).meta
        _BPB_CACHE[key] = (meta, meta.n_chunks)
    meta, n_chunks = _BPB_CACHE[key]
    n_strands = math.ceil(n_chunks * (1 + settings.redundancy))
    return payload_bits_per_base(replace(meta, settings=settings), n_strands)


def describe(s: EncoderSettings) -> str:
    homo = f"hp<={s.max_homopolymer}" if s.max_homopolymer is not None else "hp off"
    gc = f"gc {s.gc_min}-{s.gc_max}" if s.gc_min is not None or s.gc_max is not None else "gc off"
    thr = f"thr {s.risk_threshold:.4g}" if s.risk_threshold is not None else "thr off"
    return f"len {s.strand_length} red {s.redundancy:g} {homo} {gc} {thr}"


def _pool(parts: list[Metrics]) -> Metrics:
    """Combine batch metrics of the same codec, weighted by trials (screening only)."""
    if len(parts) == 1:
        return parts[0]
    w = np.array([p.n_trials or 1 for p in parts], dtype=np.float64)
    first = parts[0]
    avg = lambda xs: float(np.average(np.asarray(xs, dtype=np.float64), weights=w))  # noqa: E731
    rate = avg([p.recovery_rate or 0.0 for p in parts])
    return replace(
        first,
        strand_accuracy=avg([p.strand_accuracy for p in parts]),
        mean_edit_distance=avg([p.mean_edit_distance for p in parts]),
        dropout_rate=avg([p.dropout_rate for p in parts]),
        reads_per_strand=avg([p.reads_per_strand for p in parts]),
        recovery_rate=rate,
        n_trials=int(w.sum()),
        file_recovered=rate == 1.0,
    )


ThresholdKey = tuple[int, "int | None", bool, "float | None"]  # (length, max_homopolymer, gc rule on, quantile)


def risk_thresholds(
    scorer: Scorer, grid: SearchGrid, base: EncoderSettings, seed: int, n: int
) -> dict[ThresholdKey, float | None]:
    """Absolute risk thresholds from quantiles of candidate risk.

    Candidates are uniform random strands (encoder strands are whitened, so close to
    uniform) that pass the hard constraints of each (length, homopolymer rule, GC rule)
    combination; the quantile is taken over those, so "reject the riskiest 25%" means the
    same thing whatever the scale of the risk model and whichever rules are on.
    """
    out: dict[ThresholdKey, float | None] = {}
    rng = np.random.default_rng(train_seed(seed))
    for length in grid.strand_length:
        strands = ["".join(ALPHABET[i] for i in row) for row in rng.integers(0, 4, size=(n, length))]
        risk = np.asarray(scorer(strands), dtype=np.float64)
        for hp in grid.max_homopolymer:
            for gc_on in grid.gc_rule:
                rules = _rules(base, length, hp, gc_on)
                ok = rule_scorer(rules)(strands) == 0.0
                pool = risk[ok] if ok.sum() >= 20 else risk
                for q in grid.risk_quantile:
                    out[(length, hp, gc_on, q)] = None if q is None else float(np.quantile(pool, q))
    return out


def _rules(base: EncoderSettings, length: int, hp: int | None, gc_on: bool) -> EncoderSettings:
    return replace(base, strand_length=int(length), max_homopolymer=hp,
                   gc_min=base.gc_min if gc_on else None, gc_max=base.gc_max if gc_on else None)


def grid_candidates(
    grid: SearchGrid,
    base: EncoderSettings,
    thresholds: dict[ThresholdKey, float | None],
    redundancies: Sequence[float] | None = None,
) -> list[EncoderSettings]:
    """Every grid combination. Quantiles without a threshold entry are skipped."""
    out: list[EncoderSettings] = []
    for r in redundancies if redundancies is not None else grid.redundancy:
        for length in grid.strand_length:
            for hp in grid.max_homopolymer:
                for gc_on in grid.gc_rule:
                    for q in grid.risk_quantile:
                        if q is not None and thresholds.get((length, hp, gc_on, q)) is None:
                            continue
                        threshold = None if q is None else thresholds[(length, hp, gc_on, q)]
                        s = replace(_rules(base, length, hp, gc_on), redundancy=float(r), risk_threshold=threshold)
                        if s not in out:
                            out.append(s)
    return out


# ---------------------------------------------------------------- settings search


@dataclass
class Candidate:
    settings: EncoderSettings
    bits_per_base: float
    screen: Metrics | None = None
    screen_trials: int = 0
    passed_screen: bool = False
    recheck: Metrics | None = None
    error: str | None = None

    @property
    def passed_recheck(self) -> bool:
        return self.recheck is not None and bool(self.recheck.file_recovered)


@dataclass
class SearchResult:
    chosen: EncoderSettings
    target_met: bool  # True only if the chosen setting passed screen and re-check on train seeds
    chosen_candidate: Candidate
    candidates: list[Candidate]
    trials_run: int
    seconds: float

    def summary(self) -> str:
        c = self.chosen_candidate
        rc = c.recheck
        return (
            f"{describe(self.chosen)} | {self.chosen_candidate.bits_per_base:.3f} bits/base | "
            f"re-check {rc.recovery_rate:.3f} over {rc.n_trials} train trials" if rc is not None else
            f"{describe(self.chosen)} | {c.bits_per_base:.3f} bits/base | no re-check"
        )


def _meets(metrics: Metrics, target: float) -> bool:
    return (metrics.recovery_rate or 0.0) >= target - 1e-12


def search_settings(
    data: bytes,
    profile: SituationProfile,
    scorer: Scorer | None,
    decoder: Decoder,
    candidates: Sequence[EncoderSettings],
    config: LoopConfig,
    runner: Callable,
    screen_seeds: Sequence[int],
    recheck_seeds: Sequence[int],
    fallback: Sequence[EncoderSettings] = (),
) -> SearchResult:
    """Pick the cheapest setting that meets the recovery target on train seeds.

    Candidates are grouped by bits per base and visited from the densest level down. In a
    level, every candidate is screened on screen_seeds (in batches, stopping at the first
    miss, which gives the same pass/fail as running all of them); passers are re-checked on
    recheck_seeds in order of pooled strand accuracy, and the first one that passes is
    chosen. So the result is the maximum bits per base meeting the target at coverage B,
    with ties broken by strand accuracy on train seeds. Held-out seeds are rejected.
    """
    screen_seeds = assert_train_seeds(screen_seeds)
    recheck_seeds = assert_train_seeds(recheck_seeds)
    target = config.target
    t0 = time.time()
    trials = 0

    def run(settings: EncoderSettings, seeds: list[int]) -> Metrics:
        nonlocal trials
        assert_train_seeds(seeds)
        trials += len(seeds)
        return runner(data, settings, scorer, decoder, profile, seeds, config.workers)

    allowed_misses = math.floor((1 - target) * len(screen_seeds) + 1e-9)
    first = min(len(screen_seeds), max(1, config.first_screen_batch, config.workers or 0))
    batches = [screen_seeds[:first]]
    rest = screen_seeds[first:]
    if rest:
        batches.append(rest)

    def screen(c: Candidate) -> None:
        parts: list[Metrics] = []
        misses = 0
        for batch in batches:
            try:
                m = run(c.settings, batch)
            except ValueError as e:  # constraints too strict to encode
                c.error = str(e)
                return
            parts.append(m)
            misses += round((1 - (m.recovery_rate or 0.0)) * len(batch))
            if misses > allowed_misses:
                break
        c.screen = _pool(parts)
        c.screen_trials = c.screen.n_trials or 0
        c.passed_screen = c.screen_trials == len(screen_seeds) and misses <= allowed_misses

    def visit(pool: list[Candidate]) -> Candidate | None:
        levels: dict[float, list[Candidate]] = {}
        for c in pool:
            levels.setdefault(round(c.bits_per_base, 9), []).append(c)
        for bpb in sorted(levels, reverse=True):
            level = levels[bpb]
            for c in level:
                screen(c)
            passers = sorted((c for c in level if c.passed_screen), key=lambda c: -c.screen.strand_accuracy)
            log.info(
                "[%s] level %.3f bits/base: %d candidates, %d pass %d-trial screen (%.0fs, %d trials so far)",
                profile.name, bpb, len(level), len(passers), len(screen_seeds), time.time() - t0, trials,
            )
            for c in passers[: config.max_rechecks_per_level]:
                c.recheck = run(c.settings, list(recheck_seeds))
                log.info("[%s]   re-check %s: %.3f", profile.name, describe(c.settings), c.recheck.recovery_rate)
                if _meets(c.recheck, target):
                    return c
        return None

    all_cands = [Candidate(s, bits_per_base(data, s)) for s in candidates]
    chosen = visit(all_cands)
    if chosen is None and fallback:
        log.warning("[%s] nothing in the grid meets the target, trying higher redundancy", profile.name)
        extra = [Candidate(s, bits_per_base(data, s)) for s in fallback if s not in candidates]
        all_cands += extra
        chosen = visit(extra)
    if chosen is not None:
        return SearchResult(chosen.settings, True, chosen, all_cands, trials, time.time() - t0)

    # Nothing meets the target on train seeds: report the most robust candidate, flagged.
    tried = [c for c in all_cands if c.screen is not None]
    if not tried:
        raise RuntimeError("no candidate could be encoded")
    best = max(tried, key=lambda c: (c.screen.recovery_rate or 0.0, c.screen.strand_accuracy, c.bits_per_base))
    log.warning("[%s] no setting meets the target on train seeds; best effort: %s", profile.name, describe(best.settings))
    return SearchResult(best.settings, False, best, all_cands, trials, time.time() - t0)


# ---------------------------------------------------------------- codec store


class CodecStore:
    """Frozen codecs of one run (risk model pickles, decoder specs), for run_experiments."""

    def __init__(self, run_id: str, situation: str, root: Path | None = None):
        self.dir = (root or CODEC_DIR) / run_id / situation
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.dir / "manifest.json"
        self.manifest: dict = {"situation": situation, "iterations": []}

    def decoder_path(self, tag: str) -> Path:
        return self.dir / f"decoder_{tag}.pt"

    def write(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2) + "\n")

    def save_iteration(self, it: int, settings: EncoderSettings, risk: Scorer, decoder: Decoder, extra: dict) -> None:
        risk_path = self.dir / f"risk_it{it}.pkl"
        with risk_path.open("wb") as f:
            pickle.dump(risk, f)
        self.manifest["iterations"].append(
            {"iteration": it, "settings": asdict(settings), "risk_path": str(risk_path),
             "decoder": decoder_spec(decoder), **extra}
        )
        self.write()


def load_codecs(run_id: str, situation: str, root: Path | None = None) -> dict:
    """Manifest of a finished loop with risk models unpickled and decoders re-created."""
    path = (root or CODEC_DIR) / run_id / situation / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["decoder_b_obj"] = _load_decoder(manifest["decoder_b"])
    for it in manifest["iterations"]:
        it["settings_obj"] = EncoderSettings(**it["settings"])
        with open(it["risk_path"], "rb") as f:
            it["risk"] = pickle.load(f)
        it["decoder_obj"] = _load_decoder(it["decoder"])
    return manifest


def _load_decoder(spec: dict) -> Decoder:
    if spec["kind"] == "transformer":
        return make_decoder("transformer", spec["checkpoint"])
    if spec["kind"] == "baseline":
        return make_decoder("baseline")
    raise ValueError(f"cannot re-create decoder {spec}")


# ---------------------------------------------------------------- runtime estimate


def estimate_runtime(
    data: bytes,
    profile: SituationProfile,
    decoder: Decoder,
    default_settings: EncoderSettings,
    config: LoopConfig,
    comps: Components,
    n_alternations: int,
) -> dict[str, float]:
    """Time a few trial batches on train seeds and extrapolate the full loop."""
    workers = config.workers or os.cpu_count() or 1
    n_cal = max(4, min(workers, 20))
    seeds = train_seeds(SEED_TIMING, n_cal)
    t = time.time()
    m = comps.recovery_trials(data, default_settings, None, decoder, profile, seeds, config.workers)
    per_trial = (time.time() - t) / n_cal  # wall seconds per trial at this parallelism
    t = time.time()
    encode(data, replace(default_settings, max_homopolymer=None, gc_min=None, gc_max=None),
           lambda s: np.zeros(len(s)))
    per_encode = time.time() - t

    g = config.grid
    n_cand = len(g.redundancy) * len(g.strand_length) * len(g.max_homopolymer) * len(g.gc_rule) * len(g.risk_quantile)
    strands_per_trial = max(1, len(encode(data, default_settings).strands))
    # Evaluations: held-out trials at B plus min reads (scan until the target, assume half the grid).
    min_reads = config.eval_trials * max(1, len(config.coverages) // 2)
    per_eval = config.eval_trials + min_reads
    label = config.label_strands * config.label_k / strands_per_trial
    screen_typ = n_cand * (config.first_screen_batch + 0.5 * config.screen_trials) * 0.6
    screen_worst = n_cand * config.screen_trials
    recheck = 2 * config.recheck_trials
    curves = 3 * len(config.curve_coverages) * config.curve_trials
    typ = per_eval + n_alternations * (label + screen_typ + recheck + per_eval) + curves
    worst = per_eval + n_alternations * (label + screen_worst + 3 * recheck + per_eval) + curves
    encodes = n_alternations * n_cand * 2  # recovery_trials encodes once per call
    est = {
        "seconds_per_trial": per_trial,
        "seconds_per_scored_encode": per_encode,
        "typical_minutes": (typ * per_trial + 0.6 * encodes * per_encode) / 60,
        "worst_minutes": (worst * per_trial + encodes * per_encode) / 60,
        "candidates": float(n_cand),
    }
    log.info(
        "[%s] runtime estimate: %.2fs/trial wall (%d trials, workers=%s), %.1fs/scored encode, "
        "%d candidates x %d alternations -> ~%.0f min typical, %.0f min worst case "
        "(adapting a transformer adds fine-tuning time on top)",
        profile.name, per_trial, n_cal, config.workers, per_encode, n_cand, n_alternations,
        est["typical_minutes"], est["worst_minutes"],
    )
    return est


# ---------------------------------------------------------------- the loop


def _evaluate_heldout(
    data: bytes,
    settings: EncoderSettings,
    scorer: Scorer | None,
    decoder: Decoder,
    profile: SituationProfile,
    config: LoopConfig,
    comps: Components,
) -> tuple[Metrics, float | None]:
    """The one held-out evaluation of a codec: recovery at budget B plus min reads at target."""
    seeds = heldout_seeds(config.eval_trials)
    metrics = comps.recovery_trials(data, settings, scorer, decoder, profile, seeds, config.workers)
    min_reads = comps.min_reads_at_target(
        data, settings, scorer, decoder, profile, seeds, config.target, config.coverages, config.workers
    )
    return metrics, min_reads


def matched_default(default_settings: EncoderSettings, chosen: EncoderSettings) -> EncoderSettings:
    """The default codec (its hard rules, rule scorer) at the chosen codec's bits per base."""
    return replace(default_settings, redundancy=chosen.redundancy, strand_length=chosen.strand_length)


def coverage_curve(
    data: bytes,
    codecs: Sequence[tuple[str, EncoderSettings, Scorer | None, Decoder]],
    profile: SituationProfile,
    config: LoopConfig,
    comps: Components,
) -> list[CoveragePoint]:
    seeds = heldout_seeds(config.curve_trials)
    out = []
    for name, settings, scorer, decoder in codecs:
        for c in config.curve_coverages:
            m = comps.recovery_trials(
                data, settings, scorer, decoder, replace(profile, coverage_mean=float(c)), seeds, config.workers
            )
            out.append(CoveragePoint(name, float(c), m.strand_accuracy, m.recovery_rate))
    return out


def run_loop(
    profile: SituationProfile,
    run_id: str,
    default_settings: EncoderSettings = EncoderSettings(),
    max_iterations: int = MAX_ALTERNATIONS,
    *,
    config: LoopConfig | None = None,
    components: Components | None = None,
    decoder: Decoder | None = None,
    data: bytes | None = None,
    store_root: Path | None = None,
) -> RunResult:
    """Run the alternating loop for one situation and save a RunResult after every alternation.

    decoder: the starting decoder (default: the majority vote baseline). The default codec
    (system B) is default_settings with the rule scorer and the decoder after step 1.
    """
    config = config or LoopConfig()
    comps = (components or Components()).resolved()
    if max_iterations > MAX_ALTERNATIONS:
        log.warning("at most %d alternations (PROJECT.md), clamping %d", MAX_ALTERNATIONS, max_iterations)
    n_alt = max(1, min(max_iterations, MAX_ALTERNATIONS))
    data = test_file() if data is None else data
    decoder = decoder or comps.baseline_decoder()
    store = CodecStore(run_id, profile.name, store_root)
    is_mock = bool(comps.mocked)
    mock_note = f"MOCK components: {', '.join(comps.mocked)}. " if is_mock else ""
    quick_note = "QUICK mode (reduced trial budgets). " if config.quick else ""
    t_start = time.time()

    log.info("[%s] run %s: budget B=%g reads/strand, target %.3f, decoder %s, %d alternations",
             profile.name, run_id, profile.coverage_mean, config.target, decoder.name, n_alt)
    estimate = estimate_runtime(data, profile, decoder, default_settings, config, comps, n_alt)

    # Step 1: adapt the decoder to this channel on the default codec's strands, then freeze.
    default_strands = encode(data, default_settings).strands
    decoder_b = comps.adapt(decoder, profile, default_strands, train_seeds(SEED_ADAPT, config.adapt_passes),
                            store.decoder_path("b"), config.adapt_steps)
    store.manifest.update({
        "run_id": run_id, "profile": profile.to_dict(), "default_settings": asdict(default_settings),
        "decoder_start": decoder_spec(decoder), "decoder_b": decoder_spec(decoder_b),
        "mocked": list(comps.mocked), "quick": config.quick, "estimate": estimate,
    })
    store.write()
    log.info("[%s] step 1: decoder %s -> %s (frozen)", profile.name, decoder_spec(decoder), decoder_spec(decoder_b))

    # System B: default codec, same decoder. Held-out, once.
    default_metrics, default_min_reads = _evaluate_heldout(data, default_settings, None, decoder_b, profile, config, comps)
    log.info("[%s] default codec (system B): held-out recovery %.3f, strand acc %.3f, %.3f bits/base, min reads %s",
             profile.name, default_metrics.recovery_rate or 0.0, default_metrics.strand_accuracy,
             default_metrics.bits_per_base or 0.0, default_min_reads)
    run = RunResult(
        situation=profile.name, profile=profile, recovery_target=config.target, n_trials=config.eval_trials,
        default_settings=default_settings, default_metrics=default_metrics, iterations=[],
        default_min_reads_at_target=default_min_reads, is_mock=is_mock,
    )
    save_run(run, run_id)

    current = decoder_b
    prev_settings: EncoderSettings | None = None
    risk: Scorer | None = None
    for it in range(n_alt):
        t_it = time.time()
        # Step 2: label strands with the frozen decoder.
        per_len = max(1, config.label_strands // len(config.grid.strand_length))
        label_seed = train_seed(SEED_LABEL + it * 1_000_000)
        strands: list[Strand] = []
        for j, length in enumerate(config.grid.strand_length):
            strands += comps.generate_strands(per_len, length, train_seed(label_seed + j))
        labels = np.asarray(comps.label_failure_rates(
            strands, profile, current, config.label_k, train_seed(label_seed + 100), config.workers), dtype=np.float64)
        label_mean = float(np.nanmean(labels)) if np.isfinite(labels).any() else float("nan")
        log.info("[%s] alt %d step 2: %d strands x K=%d, mean failure rate %.3f (%d unlabeled)",
                 profile.name, it, len(strands), config.label_k, label_mean, int(np.isnan(labels).sum()))

        # Step 3: train the risk model.
        risk = comps.train_risk(strands, labels, train_seed(label_seed + 200))

        # Step 4: grid search on train seeds.
        thresholds = risk_thresholds(risk, config.grid, default_settings, SEED_THRESHOLD + it * 10_000,
                                     config.threshold_strands)
        cands = grid_candidates(config.grid, default_settings, thresholds)
        fallback = grid_candidates(config.grid, default_settings, thresholds, config.grid.fallback_redundancy)
        search = search_settings(
            data, profile, risk, current, cands, config, comps.recovery_trials,
            train_seeds(SEED_SCREEN + it * 10_000, config.screen_trials),
            train_seeds(SEED_RECHECK + it * 10_000, config.recheck_trials),
            fallback=fallback,
        )
        chosen = search.chosen
        log.info("[%s] alt %d step 4: chose %s (target met on train: %s; %d train trials, %.0fs)",
                 profile.name, it, search.summary(), search.target_met, search.trials_run, search.seconds)

        # Held-out evaluation of the chosen codec, with the decoder the search used.
        metrics, min_reads = _evaluate_heldout(data, chosen, risk, current, profile, config, comps)
        # PROJECT.md reading metric is "at matched bits per base": the default rules and scorer
        # at the chosen redundancy and strand length, same decoder, same held-out seeds.
        matched = matched_default(default_settings, chosen)
        matched_reads = comps.min_reads_at_target(
            data, matched, None, current, profile, heldout_seeds(config.eval_trials),
            config.target, config.coverages, config.workers,
        )
        kmers = [(str(k), float(r)) for k, r in risk.top_kmers()] if hasattr(risk, "top_kmers") else []
        screened = sum(c.screen is not None for c in search.candidates)
        notes = (
            f"{mock_note}{quick_note}decoder {decoder_spec(current)['kind']}"
            f"{' (re-adapted)' if it > 0 and current is not decoder_b else ''}; "
            f"{screened} candidates screened, {search.trials_run} train trials; "
            f"train re-check {search.chosen_candidate.recheck.recovery_rate if search.chosen_candidate.recheck else 'n/a'}"
            f"{'' if search.target_met else '; TARGET NOT MET ON TRAIN SEEDS (best effort)'}; "
            f"label mean failure {label_mean:.3f}; "
            f"default rules at matched bits/base ({describe(matched)}): min reads {matched_reads}"
        )
        run.iterations.append(IterationResult(it, chosen, metrics, min_reads, kmers, notes))
        store.save_iteration(it, chosen, risk, current, {
            "target_met_train": search.target_met,
            "matched_default_settings": asdict(matched),
            "matched_default_min_reads": matched_reads,
            "train_recheck_rate": search.chosen_candidate.recheck.recovery_rate if search.chosen_candidate.recheck else None,
            "thresholds": {f"len{k[0]} hp{k[1]} gc{'on' if k[2] else 'off'} q{k[3]}": v for k, v in thresholds.items()},
            "search": [
                {"settings": describe(c.settings), "bits_per_base": c.bits_per_base,
                 "screen_rate": c.screen.recovery_rate if c.screen else None,
                 "screen_trials": c.screen_trials,
                 "screen_strand_acc": c.screen.strand_accuracy if c.screen else None,
                 "recheck_rate": c.recheck.recovery_rate if c.recheck else None, "error": c.error}
                for c in search.candidates
            ],
        })
        save_run(run, run_id)
        log.info("[%s] alt %d: held-out recovery %.3f, strand acc %.3f, %.3f bits/base, min reads %s (%.0fs)",
                 profile.name, it, metrics.recovery_rate or 0.0, metrics.strand_accuracy,
                 metrics.bits_per_base or 0.0, min_reads, time.time() - t_it)

        if it == n_alt - 1:
            break
        # Step 5: re-adapt the decoder to the strands the new encoder produces.
        new_strands = encode(data, chosen, risk).strands
        adapted = comps.adapt(current, profile, new_strands, train_seeds(SEED_ADAPT + (it + 1) * 100_000, config.adapt_passes),
                              store.decoder_path(f"it{it + 1}"), config.adapt_steps)
        unchanged = adapted is current
        if config.stop_when_converged and unchanged and chosen == prev_settings:
            log.info("[%s] converged: decoder unchanged and settings repeated, stopping after %d alternations",
                     profile.name, it + 1)
            break
        prev_settings = chosen
        current = adapted

    # Coverage curves on held-out seeds: A (baseline decoder), B (only if it's the transformer), tailored.
    final = run.iterations[-1]
    baseline = decoder_b if decoder_b.name == "baseline" else comps.baseline_decoder()
    codecs = [("baseline", default_settings, None, baseline)]
    if decoder_b.name == "transformer":
        codecs.append(("transformer", default_settings, None, decoder_b))
    else:
        final.notes += "; coverage curve 'transformer' skipped: system B uses the baseline decoder"
    codecs.append(("tailored", final.settings, risk, current))
    run.coverage_curve = coverage_curve(data, codecs, profile, config, comps)
    save_run(run, run_id)
    store.manifest["seconds"] = time.time() - t_start
    store.write()
    log.info("[%s] done in %.1f min", profile.name, (time.time() - t_start) / 60)
    return run
