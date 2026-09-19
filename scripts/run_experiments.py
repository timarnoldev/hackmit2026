"""Evidence experiments for one run id, after the loops of both core situations finished.

    uv run python -m scripts.run_experiments --run-id r1 [--quick] [--workers 20] [--mock all]

Reads results/<run_id>/<situation>.json and the frozen codecs in checkpoints/loop/<run_id>/,
and writes results/<run_id>/summary.json (ExperimentSummary):

- Rule audit per situation (tier 1): each hard rule toggled from the default codec with B's
  decoder, and default vs tuned redundancy, with a verdict by a rule fixed in code.
- Ablation ladder per situation: A default codec + baseline decoder, B default codec + the
  loop's adapted decoder, C audited rules + tuned redundancy with the rule scorer (tier 1),
  D C's grid plus the learned risk scorer, same decoder (tier 2), E the full loop.
- Crossover matrix: default, and each situation's tailored encoder (settings + risk model),
  evaluated on every situation's channel at that channel's budget. On channel X all codecs
  use X's final decoder, so only the encoder differs.
- Firewall: tailored vs default on Simulator A held-out seeds, the same comparison with
  Simulator B, and the risk model's ROC AUC on held-out real DNAformer clusters.
- Direct tier-2 measurement ("tier2_direct" firewall rows): rule vs learned scorer at C's
  settings, same decoder and held-out seeds, per-trial strand failure and recovery with paired
  bootstrap CIs, at the budget and at the default's min reads, 8 and 32 candidates per strand.
- Candidate examples: the same strands scored by each situation's risk model.

All evaluation uses held-out seeds (heldout_seeds) through recovery_trials and
min_reads_at_target; nothing here feeds back into a choice.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from dnacodec.encoder import encode, rule_scorer
from dnacodec.evaluate import evaluate
from dnacodec.loop import (
    Components,
    LoopConfig,
    _evaluate_heldout,
    bits_per_base,
    decoder_spec,
    describe,
    load_codecs,
    min_reads_refined,
)
from dnacodec.results import (
    AblationEntry,
    CandidateExample,
    CrossoverEntry,
    ExperimentSummary,
    FirewallEntry,
    RuleAuditEntry,
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


# ---------------------------------------------------------------- Simulator B


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


# ---------------------------------------------------------------- rule audit (tier 1)

# Fixed before any run: a rule's effect is "measurable" when the reads per strand it needs to
# meet the recovery target differ by at least this many steps of the coverage grid
# (LoopConfig.coverages). "Not reached within the grid" counts as one step past its end.
MEASURABLE_STEPS = 1


def ladder_indices(run: RunResult) -> tuple[int, int, int]:
    """RunResult.iterations indices of ablation systems C (tier1), D (alternation 0) and E (last)."""
    stages = [it.stage for it in run.iterations]
    c = stages.index("tier1") if "tier1" in stages else 0
    alts = [i for i, st in enumerate(stages) if st.startswith("alternation")]
    d = alts[0] if alts else c
    e = alts[-1] if alts else c
    return c, d, e


def grid_step(reads: float | None, coverages: Sequence[float]) -> int:
    """Index of the first grid coverage >= reads. A half-step refined value (e.g. 6.5) maps to
    the grid point above it, so verdicts keep counting whole grid steps as fixed up front."""
    grid = sorted(coverages)
    if reads is None:
        return len(grid)
    return next((i for i, g in enumerate(grid) if g >= reads - 1e-9), len(grid))


def rule_verdict(reads_on, reads_off, coverages) -> str:
    """Hard rules don't change bits per base, so the verdict is about reads per strand only."""
    diff = grid_step(reads_off, coverages) - grid_step(reads_on, coverages)
    if diff >= MEASURABLE_STEPS:
        return "pays off"
    if -diff >= MEASURABLE_STEPS:
        return "harmful"
    return "no measurable benefit"


def redundancy_verdict(budget, reads_default, bpb_default, reads_tuned, bpb_tuned, coverages) -> str:
    """Same objective as the search: meet the target at the budget, then maximize bits per base."""
    meets_d = reads_default is not None and reads_default <= budget
    meets_t = reads_tuned is not None and reads_tuned <= budget
    if meets_t and not meets_d:
        return "tuned is better"
    if meets_t and meets_d:
        return "tuned is better" if bpb_tuned > bpb_default + 1e-9 else "default is fine"
    if not meets_t and not meets_d:
        more = grid_step(reads_default, coverages) - grid_step(reads_tuned, coverages) >= MEASURABLE_STEPS
        return "tuned is better" if more else "default is fine"
    return "default is fine"


def rule_audit(situation: str, run: RunResult, manifest: dict, data: bytes, config: LoopConfig, comps: Components):
    """Toggle each hard rule from the default codec, same (B's) decoder, held-out seeds; and
    compare default vs tuned redundancy (the tier-1 search's redundancy, default strand length)."""
    default = run.default_settings
    decoder = manifest["decoder_b_obj"]
    base_reads = run.default_min_reads_at_target
    base_bpb = run.default_metrics.bits_per_base
    seeds = heldout_seeds(config.eval_trials)
    out: list[RuleAuditEntry] = []

    def measure(settings: EncoderSettings) -> tuple[float | None, float]:
        reads = min_reads_refined(data, settings, None, decoder, run.profile, seeds, config, comps,
                                  encoded=encode(data, settings))
        return reads, bits_per_base(data, settings)

    toggles = []
    if default.max_homopolymer is not None:
        toggles.append((f"max_homopolymer={default.max_homopolymer}", replace(default, max_homopolymer=None)))
    if default.gc_min is not None or default.gc_max is not None:
        toggles.append((f"gc {default.gc_min}-{default.gc_max}", replace(default, gc_min=None, gc_max=None)))
    for name, off in toggles:
        reads_off, bpb_off = measure(off)
        verdict = rule_verdict(base_reads, reads_off, config.coverages)
        out.append(RuleAuditEntry(situation, name, base_reads, reads_off, base_bpb, bpb_off, verdict,
                                  f"default codec with and without this rule, same decoder; "
                                  f"measurable = >= {MEASURABLE_STEPS} coverage grid step(s)"))
        log.info("[%s] rule audit %s: reads on %s / off %s -> %s", situation, name, base_reads, reads_off, verdict)

    c, _, e = ladder_indices(run)
    tuned_r = run.iterations[c].settings.redundancy
    tuned = replace(default, redundancy=tuned_r)
    reads_t, bpb_t = measure(tuned) if tuned != default else (base_reads, base_bpb)
    verdict = redundancy_verdict(run.profile.coverage_mean, base_reads, base_bpb, reads_t, bpb_t, config.coverages)
    out.append(RuleAuditEntry(situation, f"redundancy {default.redundancy:g} vs tuned", base_reads, reads_t,
                              base_bpb, bpb_t, verdict,
                              f"tuned redundancy {tuned_r:g} from the tier-1 search (rule scorer only); strand "
                              f"length kept at {default.strand_length}; budget {run.profile.coverage_mean:g} reads"))
    log.info("[%s] rule audit redundancy %g vs %g: reads %s / %s -> %s", situation, default.redundancy, tuned_r,
             base_reads, reads_t, verdict)

    # The key claim question: at the SAME bits per base, does the tuned codec need fewer reads
    # than the default rules? (Both numbers were measured by the loop on held-out seeds.)
    for label, idx in (("tier 1", c), ("full loop", e)):
        if label == "full loop" and idx == c:
            continue
        it = run.iterations[idx]
        default_matched, tuned_reads = it.default_min_reads_matched, it.min_reads_at_target
        bpb = it.metrics.bits_per_base
        v = matched_verdict(default_matched, tuned_reads, config.coverages)
        out.append(RuleAuditEntry(
            situation, f"tuned codec ({label}) vs default rules at the same bits per base",
            default_matched, tuned_reads, bpb, bpb, v,
            f"on = default rules and rule scorer at the tuned codec's redundancy and strand length "
            f"({it.settings.redundancy:g}, {it.settings.strand_length}); off = tuned codec ({it.stage}); "
            f"same decoder, held-out seeds; measurable = >= {MEASURABLE_STEPS} coverage grid step(s)"))
        log.info("[%s] MATCHED DENSITY (%s, %.3f bits/base): default rules need %s reads, tuned codec %s -> %s",
                 situation, label, bpb or 0.0, default_matched, tuned_reads, v)
    return out


def matched_verdict(default_reads, tuned_reads, coverages) -> str:
    """Same bits per base, so only reads count: fewer reads for the tuned codec = better."""
    diff = grid_step(default_reads, coverages) - grid_step(tuned_reads, coverages)
    if diff >= MEASURABLE_STEPS:
        return "tuned is better"
    if -diff >= MEASURABLE_STEPS:
        return "harmful"
    return "no measurable benefit"


# ---------------------------------------------------------------- direct tier-2 measurement

TIER2_CANDIDATES = (8, 32)
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0  # resampling of already measured trials, not a channel seed

_TIER2_STATE: tuple | None = None


def _tier2_init(data: bytes, encoded, decoder: Decoder) -> None:
    global _TIER2_STATE
    _TIER2_STATE = (data, encoded, decoder)


def _tier2_trial(profile, seed: int) -> Metrics:
    from dnacodec.evaluate import recovery_trials

    data, encoded, decoder = _TIER2_STATE
    return recovery_trials(data, encoded.meta.settings, None, decoder, profile, [seed], workers=1, encoded=encoded)


def per_trial_metrics(data, encoded, decoder, profile, seeds, config: LoopConfig, comps: Components) -> list[Metrics]:
    """One Metrics per trial (from recovery_trials, so evaluate() computes everything), in seed
    order. Parallel over trials for CPU decoders; in process for GPU decoders and mocks."""
    workers = config.workers or os.cpu_count() or 1
    if "trials" in comps.mocked or getattr(decoder, "main_process_only", False) or workers <= 1 or len(seeds) <= 1:
        return [comps.recovery_trials(data, encoded.meta.settings, None, decoder, profile, [s], 1, encoded=encoded)
                for s in seeds]
    with ProcessPoolExecutor(min(workers, len(seeds)), initializer=_tier2_init,
                             initargs=(data, encoded, decoder)) as pool:
        return list(pool.map(_tier2_trial, [profile] * len(seeds), list(seeds), chunksize=4))


def paired_bootstrap(a: np.ndarray, b: np.ndarray, n: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict:
    """Means of a, b and b - a with percentile 95% CIs, resampling trials (paired: same indices)."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    idx = np.random.default_rng(seed).integers(0, len(a), size=(n, len(a)))
    ma, mb = a[idx].mean(axis=1), b[idx].mean(axis=1)
    out = {}
    for name, point, boot in (("a", a.mean(), ma), ("b", b.mean(), mb), ("diff", b.mean() - a.mean(), mb - ma)):
        lo, hi = np.percentile(boot, [2.5, 97.5])
        out[name] = (float(point), float(lo), float(hi))
    return out


def tier2_direct(situation: str, run: RunResult, manifest: dict, data: bytes, config: LoopConfig,
                 comps: Components) -> list[FirewallEntry]:
    """Rule scorer vs learned risk scorer at C's settings (no threshold), B's frozen decoder,
    the same held-out seeds, paired per trial. At the budget and at the default's min reads,
    with candidates_per_strand 8 and 32. diff = risk - rule, negative = learned selection better."""
    c, d, _ = ladder_indices(run)
    base = replace(run.iterations[c].settings, risk_threshold=None)
    risk = manifest["iterations"][d]["risk"]
    if risk is None:
        return [FirewallEntry(situation, "tier2_direct", "not_run", 0.0, "no learned risk model in this run")]
    decoder = manifest["decoder_b_obj"]
    seeds = heldout_seeds(config.eval_trials)
    coverages = [("budget", run.profile.coverage_mean)]
    if run.default_min_reads_at_target is not None and run.default_min_reads_at_target != run.profile.coverage_mean:
        coverages.append(("default min reads", run.default_min_reads_at_target))
    cache: dict[tuple, list[Metrics]] = {}

    def trials(settings, scorer, coverage) -> list[Metrics]:
        enc = encode(data, settings, scorer)
        key = (hash(tuple(enc.strands)), coverage)  # the rule scorer ignores candidates_per_strand
        if key not in cache:
            cache[key] = per_trial_metrics(data, enc, decoder, replace(run.profile, coverage_mean=float(coverage)),
                                           seeds, config, comps)
        return cache[key]

    out: list[FirewallEntry] = []
    for cov_name, coverage in coverages:
        for k in TIER2_CANDIDATES:
            settings = replace(base, candidates_per_strand=k)
            rule = trials(settings, None, coverage)
            learned = trials(settings, risk, coverage)
            context = (f"coverage {coverage:g} ({cov_name}), {k} candidates per strand, {len(seeds)} held-out trials, "
                       f"C settings {describe(settings)}, B's decoder; diff = risk - rule, 95% bootstrap CI over trials")
            for metric, per in (("strand_fail", lambda m: 1.0 - m.strand_accuracy),
                                ("recovery", lambda m: m.recovery_rate or 0.0)):
                stats = paired_bootstrap([per(m) for m in rule], [per(m) for m in learned])
                for label, key in (("rule", "a"), ("risk", "b"), ("diff", "diff")):
                    point, lo, hi = stats[key]
                    out += [
                        FirewallEntry(situation, "tier2_direct", f"{metric}_{label}", point, context),
                        FirewallEntry(situation, "tier2_direct", f"{metric}_{label}_ci_low", lo, context),
                        FirewallEntry(situation, "tier2_direct", f"{metric}_{label}_ci_high", hi, context),
                    ]
                if metric == "strand_fail":
                    log.info("[%s] TIER2 DIRECT cov %g, %d cand: strand fail rule %.4f, risk %.4f, "
                             "diff %+.4f [%+.4f, %+.4f]", situation, coverage, k, stats["a"][0], stats["b"][0],
                             *stats["diff"])
                else:
                    log.info("[%s] TIER2 DIRECT cov %g, %d cand: recovery rule %.3f, risk %.3f, "
                             "diff %+.3f [%+.3f, %+.3f]", situation, coverage, k, stats["a"][0], stats["b"][0],
                             *stats["diff"])
    return out


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
    # The tailored codec of a situation is the final one (system E).
    tailored = {
        s: Codec(s, codecs[s]["iterations"][-1]["settings_obj"], codecs[s]["iterations"][-1]["risk"])
        for s in situations
    }

    # 1. Rule audit (tier 1) and ablation ladder A to E.
    for s in situations:
        run, manifest = runs[s], codecs[s]
        summary.rule_audit += rule_audit(s, run, manifest, data, config, comps)
        if manifest["decoder_b"]["kind"] == "baseline":
            a_metrics, a_reads = run.default_metrics, run.default_min_reads_at_target  # identical codec and decoder
        else:
            a_metrics, a_reads = heldout(run.default_settings, None, comps.baseline_decoder(), run.profile)
        c, d, e = ladder_indices(run)
        summary.ablation += [
            AblationEntry(s, "A", a_metrics, a_reads),
            AblationEntry(s, "B", run.default_metrics, run.default_min_reads_at_target),
            AblationEntry(s, "C", run.iterations[c].metrics, run.iterations[c].min_reads_at_target),
            AblationEntry(s, "D", run.iterations[d].metrics, run.iterations[d].min_reads_at_target),
            AblationEntry(s, "E", run.iterations[e].metrics, run.iterations[e].min_reads_at_target),
        ]
        log.info("[%s] ablation (held-out recovery / min reads): %s", s, "  ".join(
            f"{x.system} {x.metrics.recovery_rate or 0.0:.3f}/{x.min_reads_at_target}" for x in summary.ablation[-5:]))

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
        final = run.iterations[-1]
        v = final.default_min_reads_matched
        summary.firewall.append(FirewallEntry(
            s, "sim_a_heldout", "min_reads_default_matched", NAN if v is None else float(v),
            f"default rules at the tailored codec's bits per base (redundancy {final.settings.redundancy:g}, "
            f"length {final.settings.strand_length}), same decoder"))
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

    # 4. Direct tier-2 measurement: learned vs rule scorer at C's settings, paired per trial.
    for s in situations:
        summary.firewall += tier2_direct(s, runs[s], codecs[s], data, config, comps)

    # 5. Candidate examples.
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
    if "trials" in comps.mocked:
        return [FirewallEntry(s, "sim_b", "not_run", 0.0, "mock trial runner has no channel to swap")]
    d_m, d_r = _evaluate_heldout(data, run.default_settings, None, decoder, run.profile, config, comps, sim_b)
    t_m, t_r = _evaluate_heldout(data, codec.settings, codec.scorer, decoder, run.profile, config, comps, sim_b)
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
