"""Evidence experiments for one run id, after the loops of both core situations finished.

    uv run python -m scripts.run_experiments --run-id r1 [--quick] [--workers 20] [--mock all]

Reads results/<run_id>/<situation>.json and the frozen codecs in checkpoints/loop/<run_id>/,
and writes results/<run_id>/summary.json (ExperimentSummary):

- Ablation ladder per situation: A default codec + baseline decoder, B default codec + the
  loop's adapted decoder, C risk scorer + settings search with B's frozen decoder (first
  alternation), D after the final alternation.
- Crossover matrix: default, and each situation's tailored encoder (settings + risk model),
  evaluated on every situation's channel at that channel's budget. On channel X all codecs
  use X's final decoder, so only the encoder differs.
- Firewall: tailored vs default on Simulator A held-out seeds, the same comparison with
  Simulator B, and the risk model's ROC AUC on held-out real DNAformer clusters.
- Candidate examples: the same strands scored by each situation's risk model.

All evaluation uses held-out seeds (heldout_seeds) through recovery_trials and
min_reads_at_target; nothing here feeds back into a choice.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import multiprocessing
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np

from dnacodec.encoder import rule_scorer
from dnacodec.evaluate import evaluate
from dnacodec.loop import Components, LoopConfig, _evaluate_heldout, decoder_spec, load_codecs
from dnacodec.results import (
    AblationEntry,
    CandidateExample,
    CrossoverEntry,
    ExperimentSummary,
    FirewallEntry,
    RunResult,
    load_runs,
    save_summary,
)
from dnacodec.seeds import heldout_seeds
from dnacodec.testfile import test_file
from dnacodec.types import ALPHABET, Decoder, EncoderSettings, Metrics, Scorer

log = logging.getLogger("dnacodec.experiments")

CORE = ("nanopore_budget", "illumina_standard")
REAL_DATASETS = {
    "nanopore": "BinnedNanoporeSecondFlowcell_Random",
    "illumina": "BinnedTestIllumina_Random",
}
NAN = float("nan")


class Codec:
    """An encoder (settings + scorer) with a label; None scorer = rule scorer (default codec)."""

    def __init__(self, name: str, settings: EncoderSettings, scorer: Scorer | None):
        self.name, self.settings, self.scorer = name, settings, scorer


# ---------------------------------------------------------------- Simulator B routing


@contextmanager
def use_simulator(sim_fn: Callable) -> Iterator[Callable[[], bool]]:
    """Route recovery_trials through another simulator by patching the module-level simulate.

    recovery_trials has no simulator parameter (interface gap, reported to the architect), so
    this patches dnacodec.simulator.simulate and dnacodec.evaluate.simulate. Every call of the
    patched function touches a sentinel file, and the yielded check tells whether the trials
    really went through sim_fn (they don't if workers were spawned fresh or a mock ran).
    """
    import dnacodec.evaluate as evaluate_mod
    import dnacodec.simulator as simulator_mod

    sentinel = Path(tempfile.mkdtemp(prefix="simb_"))

    def patched(strands, profile, seed):
        (sentinel / str(os.getpid())).touch()
        return sim_fn(strands, profile, seed)

    saved = {m: m.__dict__.get("simulate") for m in (simulator_mod, evaluate_mod)}
    for mod in saved:
        if "simulate" in mod.__dict__:
            mod.simulate = patched
    try:
        yield lambda: any(sentinel.iterdir())
    finally:
        for mod, fn in saved.items():
            if fn is not None:
                mod.simulate = fn
        shutil.rmtree(sentinel, ignore_errors=True)


def load_simulator_b() -> Callable | None:
    if importlib.util.find_spec("dnacodec.simulator_b") is None:
        return None
    mod = importlib.import_module("dnacodec.simulator_b")
    for name in ("simulate", "simulate_b"):
        if callable(getattr(mod, name, None)):
            return getattr(mod, name)
    return None


# ---------------------------------------------------------------- real-data risk ranking


def roc_auc(scores: np.ndarray, positive: np.ndarray) -> float:
    """Mann-Whitney ROC AUC with average ranks for ties. NaN if a class is empty."""
    scores = np.asarray(scores, dtype=np.float64)
    positive = np.asarray(positive, dtype=bool)
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    if n_pos == 0 or n_neg == 0:
        return NAN
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores))
    sorted_scores = scores[order]
    i = 0
    while i < len(scores):
        j = i
        while j + 1 < len(scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def real_risk_auc(
    risk: Scorer, decoder: Decoder, technology: str, coverage: float, max_clusters: int
) -> tuple[float, str]:
    """Risk ranking on held-out real clusters: AUC of predicted risk vs actual decoder failure."""
    from dnacodec.realdata import DNAFORMER_DIR, load_dnaformer

    name = REAL_DATASETS.get(technology)
    if name is None or not (DNAFORMER_DIR / f"{name}.txt").exists():
        return NAN, f"real dataset for {technology} not found"
    clusters = [c for c in load_dnaformer(name, split="heldout") if c.reads][:max_clusters]
    rng = np.random.default_rng(heldout_seeds(1)[0])
    k = max(1, round(coverage))
    reads = [
        c.reads if len(c.reads) <= k else [c.reads[i] for i in sorted(rng.choice(len(c.reads), k, replace=False))]
        for c in clusters
    ]
    failed = np.zeros(len(clusters), dtype=bool)
    by_len: dict[int, list[int]] = {}
    for i, c in enumerate(clusters):
        by_len.setdefault(len(c.reference), []).append(i)
    for length, idx in by_len.items():
        decoded = decoder.decode([reads[i] for i in idx], length)
        for i, d in zip(idx, decoded):
            failed[i] = evaluate([clusters[i].reference], [d], [reads[i]]).strand_accuracy < 1.0
    scores = np.asarray(risk([c.reference for c in clusters]), dtype=np.float64)
    auc = roc_auc(scores, failed)
    note = f"{name} held-out, {len(clusters)} clusters at <= {k} reads, {failed.mean():.1%} decoder failures"
    return auc, note


# ---------------------------------------------------------------- candidate examples


def accepted_by(settings: EncoderSettings, risk: Scorer, strands: Sequence[str], reference_risk: np.ndarray) -> np.ndarray:
    """Would this situation's encoder accept the strand? Hard constraints plus the risk
    threshold; with no threshold, the encoder keeps the safest of several candidates, so a
    strand above the median risk of random strands counts as rejected."""
    passes = rule_scorer(settings)(strands) == 0.0
    r = np.asarray(risk(strands), dtype=np.float64)
    limit = settings.risk_threshold if settings.risk_threshold is not None else float(np.median(reference_risk))
    return passes & (r <= limit)


def candidate_examples(codecs: dict[str, Codec], n: int = 3000, length: int = 110, seed: int = 12345, keep: int = 8):
    names = list(codecs)
    rng = np.random.default_rng(seed)
    pool = ["".join(ALPHABET[i] for i in row) for row in rng.integers(0, 4, size=(n, length))]
    risks = {s: np.asarray(codecs[s].scorer(pool), dtype=np.float64) for s in names}
    acc = {s: accepted_by(codecs[s].settings, codecs[s].scorer, pool, risks[s]) for s in names}
    pct = {s: np.argsort(np.argsort(risks[s])) / max(1, n - 1) for s in names}
    disagree = np.zeros(n, dtype=bool)
    for a in names:
        for b in names:
            disagree |= acc[a] != acc[b]
    spread = np.max([pct[s] for s in names], axis=0) - np.min([pct[s] for s in names], axis=0)
    order = sorted(range(n), key=lambda i: (not disagree[i], -spread[i]))[:keep]
    return [
        CandidateExample(
            strand=pool[i],
            risk={s: float(risks[s][i]) for s in names},
            accepted={s: bool(acc[s][i]) for s in names},
        )
        for i in order
    ]


# ---------------------------------------------------------------- the experiments


def build_summary(
    run_id: str,
    situations: Sequence[str] = CORE,
    config: LoopConfig | None = None,
    components: Components | None = None,
    store_root: Path | None = None,
    real_clusters: int = 2000,
    data: bytes | None = None,
) -> ExperimentSummary:
    config = config or LoopConfig()
    comps = (components or Components()).resolved()
    data = test_file() if data is None else data
    runs: dict[str, RunResult] = {r.situation: r for r in load_runs(run_id)}
    missing = [s for s in situations if s not in runs]
    if missing:
        raise FileNotFoundError(f"no loop results for {missing} in run {run_id}")
    codecs = {s: load_codecs(run_id, s, store_root) for s in situations}
    is_mock = bool(comps.mocked) or any(runs[s].is_mock for s in situations)
    summary = ExperimentSummary(is_mock=is_mock)

    def heldout(settings, scorer, decoder, profile) -> tuple[Metrics, float | None]:
        return _evaluate_heldout(data, settings, scorer, decoder, profile, config, comps)

    final_decoder = {s: codecs[s]["iterations"][-1]["decoder_obj"] for s in situations}
    tailored = {
        s: Codec(s, codecs[s]["iterations"][-1]["settings_obj"], codecs[s]["iterations"][-1]["risk"])
        for s in situations
    }

    # 1. Ablation ladder.
    for s in situations:
        run, manifest = runs[s], codecs[s]
        if manifest["decoder_b"]["kind"] == "baseline":
            a_metrics, a_reads = run.default_metrics, run.default_min_reads_at_target  # identical codec and decoder
        else:
            a_metrics, a_reads = heldout(run.default_settings, None, comps.baseline_decoder(), run.profile)
        summary.ablation += [
            AblationEntry(s, "A", a_metrics, a_reads),
            AblationEntry(s, "B", run.default_metrics, run.default_min_reads_at_target),
            AblationEntry(s, "C", run.iterations[0].metrics, run.iterations[0].min_reads_at_target),
            AblationEntry(s, "D", run.iterations[-1].metrics, run.iterations[-1].min_reads_at_target),
        ]
        log.info("[%s] ablation: A %.3f  B %.3f  C %.3f  D %.3f (held-out recovery)", s,
                 *(e.metrics.recovery_rate or 0.0 for e in summary.ablation[-4:]))

    # 2. Crossover matrix: on channel X every codec uses X's final decoder.
    home: dict[tuple[str, str], tuple[Metrics, float | None]] = {}
    for x in situations:
        run, manifest = runs[x], codecs[x]
        dec = final_decoder[x]
        same_as_b = decoder_spec(dec) == manifest["decoder_b"]
        for codec in ("default", *situations):
            if codec == "default" and same_as_b:
                result = (run.default_metrics, run.default_min_reads_at_target)
            elif codec == x:
                result = (run.iterations[-1].metrics, run.iterations[-1].min_reads_at_target)
            elif codec == "default":
                result = heldout(run.default_settings, None, dec, run.profile)
            else:
                result = heldout(tailored[codec].settings, tailored[codec].scorer, dec, run.profile)
            home[(codec, x)] = result
            summary.crossover.append(CrossoverEntry(codec, x, result[0], result[1]))
            log.info("crossover %s on %s: recovery %.3f, min reads %s", codec, x, result[0].recovery_rate or 0.0, result[1])

    # 3. Firewall.
    sim_b = load_simulator_b()
    for s in situations:
        run = runs[s]
        d_m, d_r = home[("default", s)]
        t_m, t_r = home[(s, s)]
        summary.firewall += _comparison(s, "sim_a_heldout", d_m, d_r, t_m, t_r, "Simulator A, held-out seeds")
        if sim_b is None:
            summary.firewall.append(FirewallEntry(s, "sim_b", "not_run", 0.0, "dnacodec.simulator_b not available yet"))
        else:
            summary.firewall += _sim_b_comparison(s, sim_b, run, tailored[s], final_decoder[s], data, config, comps)
        if real_clusters <= 0:
            summary.firewall.append(FirewallEntry(s, "real", "not_run", 0.0, "skipped (real_clusters=0)"))
            continue
        try:
            auc, note = real_risk_auc(tailored[s].scorer, final_decoder[s], run.profile.technology,
                                      run.profile.coverage_mean, real_clusters)
        except Exception as e:  # real data problems must not kill the summary
            auc, note = NAN, f"failed: {e}"
        summary.firewall.append(FirewallEntry(s, "real", "risk_auc", auc, note))
        log.info("[%s] real risk AUC %.3f (%s)", s, auc, note)

    # 4. Candidate examples.
    summary.examples = candidate_examples(tailored)
    return summary


def _comparison(s, test, d_m, d_r, t_m, t_r, note) -> list[FirewallEntry]:
    def reads(v):
        return (float(v), note) if v is not None else (NAN, note + "; target not reached at any tested coverage")

    return [
        FirewallEntry(s, test, "recovery_rate_gain", (t_m.recovery_rate or 0.0) - (d_m.recovery_rate or 0.0), note),
        FirewallEntry(s, test, "min_reads_default", *reads(d_r)),
        FirewallEntry(s, test, "min_reads_tailored", *reads(t_r)),
    ]


def _sim_b_comparison(s, sim_b, run, codec, decoder, data, config, comps) -> list[FirewallEntry]:
    workers = config.workers if multiprocessing.get_start_method() == "fork" else 1
    cfg = replace(config, workers=workers)
    with use_simulator(sim_b) as used:
        d_m, d_r = _evaluate_heldout(data, run.default_settings, None, decoder, run.profile, cfg, comps)
        t_m, t_r = _evaluate_heldout(data, codec.settings, codec.scorer, decoder, run.profile, cfg, comps)
        routed = used()
    if not routed:
        return [FirewallEntry(s, "sim_b", "not_run", 0.0,
                              "trials did not go through Simulator B (mock trial runner or spawned workers)")]
    return _comparison(s, "sim_b", d_m, d_r, t_m, t_r, "Simulator B, held-out seeds")


def main(argv: Sequence[str] | None = None) -> int:
    from scripts.run_loop import mock_components, parse_mock, setup_logging

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--situations", default=",".join(CORE))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--real-clusters", type=int, default=2000)
    ap.add_argument("--mock", default=None, help="comma list of components to mock: trials, risk, all")
    args = ap.parse_args(argv)

    setup_logging(args.run_id)
    config = LoopConfig.quick_mode(args.workers) if args.quick else LoopConfig(workers=args.workers)
    mocks = parse_mock(args.mock)
    comps = mock_components(mocks) if mocks else Components()
    situations = [s.strip() for s in args.situations.split(",") if s.strip()]
    summary = build_summary(args.run_id, situations, config, comps,
                            real_clusters=200 if args.quick else args.real_clusters)
    path = save_summary(summary, args.run_id)
    log.info("summary written to %s (is_mock=%s)", path, summary.is_mock)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
