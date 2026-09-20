"""Calibrate the channel simulator on real reads (train splits only).

Two stages:

1. Error statistics. Align real reads to their references with Levenshtein editops and
   measure substitution, insertion and deletion rates, deletions by homopolymer run length,
   the position ramp, the share of malformed reads (reads more than OUTLIER_FRACTION of the
   length away from their reference) and the coverage distribution. Then adjust the profile
   until the SAME measurement on simulated reads (same references, same alignment)
   reproduces the real numbers.
2. Decoding difficulty. Per-base statistics don't pin down how hard a cluster is to decode:
   real reads vary much more in quality than i.i.d. errors suggest, and some errors are
   shared by most reads of a strand. So read_quality_spread and position_rate_spread are
   chosen by coordinate search so that baseline decoder accuracy vs reads per cluster on
   simulated reads matches the real curve (error rates are refit for every candidate).

The final comparison runs on train clusters that were not used for the fit.

    uv run python scripts/calibrate.py                    # Nanopore from Microsoft train
    uv run python scripts/calibrate.py --dry-run          # measure and compare, write nothing
    uv run python scripts/calibrate.py --skip-microsoft --dnaformer-file BinnedTestIllumina_Random

Only split="train" is ever loaded here. Every seed goes through train_seed().
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein

from dnacodec import realdata
from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.evaluate import evaluate
from dnacodec.profiles import PROFILES_DIR, SituationProfile, load_profile, save_profile
from dnacodec.seeds import train_seed
from dnacodec.simulator import _encode as encode_strands
from dnacodec.simulator import kmer_ids, load_context_table, run_lengths, sample_coverage, simulate

# Reads further than this fraction of the reference length from it are counted as malformed
# (misclustered or chimeric), not as channel noise. Applied identically to real and simulated.
OUTLIER_FRACTION = 0.3
MAX_RUN = 7  # run lengths >= MAX_RUN share the last homopolymer bucket
MIN_RUN_EVENTS = 30  # fewer real deletions than this in a run bucket: too noisy to fit
SIM_READS_PER_REF = 12  # minimum simulated reads per reference when fitting error stats
SIM_MAX_READS_PER_REF = 60
SIM_TARGET_EVENTS = 150_000
CONTEXT_K = 5  # centered k-mer for the context table
CONTEXT_PRIOR = 2000.0  # pseudo base-reads at the global rate when estimating k-mer rates
HOT_THRESHOLD = 0.2  # a (strand, position) is a hot spot if this share of reads errs there
HOT_MIN_READS = 10
FIT_ITERATIONS = 5
CONTEXT_FIT_ITERATIONS = 8
CONTEXT_RUN_MIN = 4  # with a context table, run factors only for runs >= this (the 5-mer sees shorter ones)
CONTEXT_DAMPING = 0.5  # exponent on multiplicative updates of the table and run factors
BUDGETS = [2, 4, 6, 10, 16]  # reads per cluster for the accuracy curve (as in eval_real.py)
READ_GRID = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
POS_GRID = [0.0, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6]
STATS_CLUSTERS = 6000  # clusters used to fit error statistics (and the context table)
CURVE_CLUSTERS = 2000  # the first of these fit the accuracy curve
VAL_CLUSTERS = 2000  # clusters after STATS_CLUSTERS, used for nothing but validation
# The archive profile keeps extra substitutions for storage damage (deamination) on top
# of the measured Illumina rate. Extrapolated, there is no real aged-DNA data here.
ARCHIVE_SUB_BOOST = 1.5

TAGS = {"replace": 0, "insert": 1, "delete": 2}
TYPE_NAMES = ("sub", "ins", "del")


_TABLE_DIR = Path(tempfile.mkdtemp(prefix="dnacodec_ctx_"))
_TABLE_COUNTER = itertools.count()


def write_context_table(table: np.ndarray, path: Path | None = None, fitted_on: str = "") -> str:
    path = path or _TABLE_DIR / f"ctx_{next(_TABLE_COUNTER)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"k": CONTEXT_K, "fitted_on": fitted_on}
    for name, row in zip(TYPE_NAMES, table):
        data[name] = [round(float(v), 4) for v in row]
    path.write_text(json.dumps(data) + "\n")
    return str(path)


@dataclass
class ErrorStats:
    n_reads: int
    n_outliers: int
    length: int
    sub: float
    ins: float
    dele: float
    end_ratio: float  # fitted linear error rate at the last base / at the first base
    hp_ratio: float  # fitted per-extra-run-base multiplier of the total error rate
    position: np.ndarray  # total error events per read per position, len == length
    by_run: np.ndarray  # (3, MAX_RUN) sub/ins/del rate per base by run length 1..MAX_RUN
    run_events: np.ndarray  # (3, MAX_RUN) the event counts behind by_run
    read_error_sd: float  # spread of per-read error fraction (heterogeneity between reads)
    read_error_quantiles: np.ndarray  # per-read error fraction at 5, 25, 50, 75, 95 %
    ctx_events: np.ndarray  # (3, 4**CONTEXT_K) sub/ins/del events by centered k-mer
    ctx_bases: np.ndarray  # (4**CONTEXT_K,) base-reads by centered k-mer

    def ctx_relative(self) -> np.ndarray:
        """(3, 4**k) k-mer error rate relative to the global rate, shrunk toward 1."""
        glob = self.ctx_events.sum(axis=1, keepdims=True) / max(self.ctx_bases.sum(), 1)
        rate = (self.ctx_events + CONTEXT_PRIOR * glob) / (self.ctx_bases[None, :] + CONTEXT_PRIOR)
        return rate / np.maximum(glob, 1e-12)

    @property
    def total(self) -> float:
        return self.sub + self.ins + self.dele

    @property
    def outlier_fraction(self) -> float:
        return self.n_outliers / max(self.n_reads + self.n_outliers, 1)

    @property
    def total_by_run(self) -> np.ndarray:
        return self.by_run.sum(axis=0)


def error_stats(refs: list[str], clusters: list[list[str]]) -> ErrorStats:
    lengths = {len(r) for r in refs}
    if len(lengths) != 1:
        raise ValueError(f"expected one reference length, got {sorted(lengths)}")
    length = lengths.pop()
    codes, _ = encode_strands(refs)
    runs = np.minimum(run_lengths(codes), MAX_RUN)  # (n_refs, length)
    kids = kmer_ids(codes, CONTEXT_K)
    n_ctx = 4**CONTEXT_K
    ctx_events = np.zeros((3, n_ctx), dtype=np.int64)
    ctx_bases = np.zeros(n_ctx, dtype=np.int64)

    counts = np.zeros((3, length), dtype=np.int64)
    run_events = np.zeros((3, MAX_RUN + 1), dtype=np.int64)
    run_bases = np.zeros(MAX_RUN + 1, dtype=np.int64)
    per_read = []
    n_reads = n_outliers = 0
    cutoff = int(OUTLIER_FRACTION * length)
    for i, (ref, reads) in enumerate(zip(refs, clusters)):
        used = 0
        ref_runs = runs[i]
        ref_kids = kids[i]
        for read in reads:
            if Levenshtein.distance(ref, read, score_cutoff=cutoff) > cutoff:
                n_outliers += 1
                continue
            ops = Levenshtein.editops(ref, read).as_list()
            used += 1
            per_read.append(len(ops) / length)
            if not ops:
                continue
            tag = np.fromiter((TAGS[o[0]] for o in ops), dtype=np.int64, count=len(ops))
            pos = np.fromiter((o[1] for o in ops), dtype=np.int64, count=len(ops))
            pos = np.minimum(pos, length - 1)  # insertion after the last base
            np.add.at(counts, (tag, pos), 1)
            np.add.at(run_events, (tag, ref_runs[pos]), 1)
            k = ref_kids[pos]
            inside = k >= 0
            np.add.at(ctx_events, (tag[inside], k[inside]), 1)
        n_reads += used
        if used:
            run_bases += used * np.bincount(ref_runs, minlength=MAX_RUN + 1)
            ctx_bases += used * np.bincount(ref_kids[ref_kids >= 0], minlength=n_ctx)

    bases = max(n_reads * length, 1)
    sub, ins, dele = (counts.sum(axis=1) / bases).tolist()
    position = counts.sum(axis=0) / max(n_reads, 1)

    x = np.arange(length)
    slope, intercept = np.polyfit(x, position, 1)
    end_ratio = float((intercept + slope * (length - 1)) / intercept)

    by_run = run_events[:, 1:] / np.maximum(run_bases[1:], 1)
    total = by_run.sum(axis=0)
    r = np.arange(1, MAX_RUN + 1)
    ok = (run_bases[1:] > 0) & (total > 0)
    weights = np.sqrt(run_events[:, 1:].sum(axis=0)[ok])
    log_ratio = np.log(total[ok] / total[0])
    # weighted least squares through the origin: log(rate_r / rate_1) = (r - 1) log f
    hp_log = np.sum(weights * (r[ok] - 1) * log_ratio) / max(np.sum(weights * (r[ok] - 1) ** 2), 1e-12)
    per_read_arr = np.array(per_read) if per_read else np.zeros(1)
    return ErrorStats(
        n_reads=n_reads,
        n_outliers=n_outliers,
        length=length,
        sub=sub,
        ins=ins,
        dele=dele,
        end_ratio=end_ratio,
        hp_ratio=float(np.exp(hp_log)),
        position=position,
        by_run=by_run,
        run_events=run_events[:, 1:],
        read_error_sd=float(np.std(per_read_arr)),
        read_error_quantiles=np.percentile(per_read_arr, [5, 25, 50, 75, 95]),
        ctx_events=ctx_events,
        ctx_bases=ctx_bases,
    )


def hotspot_shares(refs: list[str], clusters: list[list[str]]) -> np.ndarray:
    """(3,) share of (strand, position) pairs where >= HOT_THRESHOLD of reads have a
    sub / ins / del, over clusters with >= HOT_MIN_READS non-outlier reads."""
    length = len(refs[0])
    cutoff = int(OUTLIER_FRACTION * length)
    hot = np.zeros(3)
    total = 0
    for ref, reads in zip(refs, clusters):
        hits = np.zeros((3, length))
        used = 0
        for read in reads:
            if Levenshtein.distance(ref, read, score_cutoff=cutoff) > cutoff:
                continue
            used += 1
            seen = np.zeros((3, length), dtype=bool)
            for t, src, _ in Levenshtein.editops(ref, read).as_list():
                seen[TAGS[t], min(src, length - 1)] = True
            hits += seen
        if used >= HOT_MIN_READS:
            hot += (hits / used >= HOT_THRESHOLD).sum(axis=1)
            total += length
    return hot / max(total, 1)


def nb_shape_mle(sizes: np.ndarray) -> float:
    """Maximum likelihood negative binomial shape with the mean fixed to the sample mean."""
    mean = float(sizes.mean())
    values, counts = np.unique(sizes.astype(np.int64), return_counts=True)
    lgamma = np.vectorize(math.lgamma)

    def loglik(k: float) -> float:
        terms = lgamma(values + k) - lgamma(k) + k * np.log(k / (k + mean)) + values * np.log(mean / (k + mean))
        return float(np.sum(counts * terms))

    grid = np.exp(np.linspace(np.log(0.05), np.log(1000.0), 400))
    best = int(np.argmax([loglik(k) for k in grid]))
    lo, hi = grid[max(best - 1, 0)], grid[min(best + 1, len(grid) - 1)]
    fine = np.linspace(lo, hi, 200)
    return float(fine[int(np.argmax([loglik(k) for k in fine]))])


def coverage_fit(clusters: list[list[str]]) -> tuple[float, float, float, float]:
    """Mean, variance, method-of-moments and maximum likelihood NB shape of cluster sizes.

    The MLE shape is what goes into the profile: the real distribution is not exactly
    negative binomial, and matching the variance (moments) puts about 3x too many
    clusters at <= 3 reads, which is exactly the regime that decides decoding failures.
    """
    sizes = np.array([len(c) for c in clusters], dtype=float)
    mean, var = sizes.mean(), sizes.var()
    moments = mean**2 / (var - mean) if var > mean else 1e3
    return float(mean), float(var), float(moments), nb_shape_mle(sizes)


def simulate_sized(refs: list[str], sizes: list[int], profile: SituationProfile, seed: int) -> list[list[str]]:
    """Simulate with no dropout and exactly sizes[i] reads for refs[i] (errors only)."""
    need = max(sizes) if sizes else 1
    sim_profile = replace(
        profile,
        dropout_rate=0.0,
        decay_per_year=0.0,
        gc_dropout_factor=0.0,
        coverage_mean=float(2 * need + 20),
        coverage_dispersion=1e6,  # Poisson, comfortably above every requested size
    )
    clusters = simulate(refs, sim_profile, train_seed(seed))
    out = [c[:n] for c, n in zip(clusters, sizes)]
    if any(len(c) < n for c, n in zip(out, sizes)):
        raise RuntimeError("simulated cluster smaller than requested; raise coverage_mean")
    return out


def sim_reads_per_ref(refs: list[str], profile: SituationProfile) -> int:
    """Enough simulated reads for ~SIM_TARGET_EVENTS error events (low-error Illumina needs more)."""
    rate = profile.sub_rate + profile.ins_rate + profile.del_rate
    need = SIM_TARGET_EVENTS / max(len(refs) * len(refs[0]) * rate, 1e-9)
    return int(np.clip(np.ceil(need), SIM_READS_PER_REF, SIM_MAX_READS_PER_REF))


def simulated_stats(refs: list[str], profile: SituationProfile, seed: int) -> ErrorStats:
    n = sim_reads_per_ref(refs, profile)
    return error_stats(refs, simulate_sized(refs, [n] * len(refs), profile, seed))


def _forward_fill(values: np.ndarray, reliable: np.ndarray) -> np.ndarray:
    """Buckets with too few real events copy the value of the previous bucket."""
    out = values.copy()
    for i in range(1, len(out)):
        if not reliable[i]:
            out[i] = out[i - 1]
    return out


def fit_error_model(
    refs: list[str],
    real: ErrorStats,
    start: SituationProfile,
    seed: int,
    verbose: bool = True,
    fit_context: bool = False,
    iterations: int = FIT_ITERATIONS,
    warm: bool = False,
) -> tuple[SituationProfile, ErrorStats]:
    """Adjust rates, homopolymer run factors, end_factor and (optionally) the context table
    until simulated error statistics match the real ones.

    Deletions get a separate factor per run length (homopolymer_run_factors, first entry 1),
    substitutions and insertions a single rate each. Run lengths with fewer than
    MIN_RUN_EVENTS real deletions (e.g. long runs on Illumina) reuse the previous factor
    instead of being fit to noise. The context table holds mean-1 multipliers per centered
    k-mer and error type (mean weighted by how often each k-mer occurs in the real data).
    warm=True keeps the start's parameters instead of initializing from the real stats.

    With fit_context the deletion model is del_rate x table[5-mer] x run factor, which is
    only identifiable with constraints: run factors are fixed to 1 below CONTEXT_RUN_MIN
    (the centered 5-mer already sees those runs), del_rate follows the overall deletion
    rate, and table and run factors take damped steps.
    """
    if fit_context:
        return _fit_error_model_context(refs, real, start, seed, verbose, iterations, warm)
    reliable = real.run_events[2] >= MIN_RUN_EVENTS
    reliable[0] = True
    del_run = _forward_fill(np.maximum(real.by_run[2], 1e-6), reliable)
    weights = real.ctx_bases / max(real.ctx_bases.sum(), 1)
    real_ctx = real.ctx_relative()

    def normalized(table: np.ndarray) -> np.ndarray:
        return table / np.maximum((table * weights).sum(axis=1, keepdims=True), 1e-12)

    profile = start
    if not warm:
        profile = replace(
            start,
            sub_rate=real.sub,
            ins_rate=real.ins,
            del_rate=float(del_run[0]),
            homopolymer_run_factors=tuple((del_run / del_run[0]).tolist()),
            end_factor=max(real.end_ratio, 0.1),
            malformed_read_rate=real.outlier_fraction,
            context_table=write_context_table(normalized(real_ctx)) if fit_context else None,
        )
    for it in range(iterations):
        sim = simulated_stats(refs, profile, seed + it)
        if verbose:
            ctx_err = ""
            if fit_context:
                ctx_err = f" ctx log-ratio sd {np.round(np.std(np.log(sim.ctx_relative() / real_ctx), axis=1), 3).tolist()}"
            print(
                f"  fit {it}: sim sub {sim.sub:.4f} ins {sim.ins:.4f} del {sim.dele:.4f} "
                f"end {sim.end_ratio:.3f} del-by-run {np.round(sim.by_run[2], 3).tolist()}{ctx_err}"
            )
        del_now = profile.del_rate * np.asarray(profile.homopolymer_run_factors)
        ratio = np.where(reliable, del_run / np.maximum(sim.by_run[2], 1e-6), 1.0)
        del_new = _forward_fill(del_now * ratio, reliable)
        updates = dict(
            sub_rate=min(profile.sub_rate * real.sub / max(sim.sub, 1e-9), 0.3),
            ins_rate=min(profile.ins_rate * real.ins / max(sim.ins, 1e-9), 0.3),
            del_rate=min(float(del_new[0]), 0.3),
            homopolymer_run_factors=tuple((del_new / del_new[0]).tolist()),
            end_factor=max(profile.end_factor + real.end_ratio - sim.end_ratio, 0.1),
        )
        if fit_context:
            _, table = load_context_table(profile.context_table)
            # Damped: run factors correct deletions in runs at the same time.
            step = (real_ctx / np.maximum(sim.ctx_relative(), 1e-6)) ** 0.7
            updates["context_table"] = write_context_table(normalized(table * step))
        profile = replace(profile, **updates)
    final = simulated_stats(refs, profile, seed + 1000)
    return profile, final


def _fit_error_model_context(
    refs: list[str],
    real: ErrorStats,
    start: SituationProfile,
    seed: int,
    verbose: bool,
    iterations: int,
    warm: bool,
) -> tuple[SituationProfile, ErrorStats]:
    iterations = max(iterations, CONTEXT_FIT_ITERATIONS) if not warm else iterations
    reliable = real.run_events[2] >= MIN_RUN_EVENTS
    fit_run = reliable & (np.arange(1, MAX_RUN + 1) >= CONTEXT_RUN_MIN)
    weights = real.ctx_bases / max(real.ctx_bases.sum(), 1)
    real_ctx = real.ctx_relative()

    def normalized(table: np.ndarray) -> np.ndarray:
        return table / np.maximum((table * weights).sum(axis=1, keepdims=True), 1e-12)

    profile = start
    if not warm:
        profile = replace(
            start,
            sub_rate=real.sub,
            ins_rate=real.ins,
            del_rate=real.dele,
            homopolymer_run_factors=tuple([1.0] * MAX_RUN),
            end_factor=max(real.end_ratio, 0.1),
            malformed_read_rate=real.outlier_fraction,
            context_table=write_context_table(normalized(real_ctx)),
        )
    for it in range(iterations):
        sim = simulated_stats(refs, profile, seed + it)
        if verbose:
            sd = np.round(np.std(np.log(sim.ctx_relative() / real_ctx), axis=1), 3).tolist()
            print(
                f"  fit {it}: sim sub {sim.sub:.4f} ins {sim.ins:.4f} del {sim.dele:.4f} "
                f"end {sim.end_ratio:.3f} del-by-run {np.round(sim.by_run[2], 3).tolist()} ctx log-ratio sd {sd}"
            )
        factors = np.asarray(profile.homopolymer_run_factors, dtype=np.float64)
        run_step = np.where(fit_run, real.by_run[2] / np.maximum(sim.by_run[2], 1e-6), 1.0)
        factors = _forward_fill(factors * run_step**CONTEXT_DAMPING, fit_run | (np.arange(MAX_RUN) == 0))
        factors[: CONTEXT_RUN_MIN - 1] = 1.0
        _, table = load_context_table(profile.context_table)
        step = (real_ctx / np.maximum(sim.ctx_relative(), 1e-6)) ** CONTEXT_DAMPING
        profile = replace(
            profile,
            sub_rate=min(profile.sub_rate * real.sub / max(sim.sub, 1e-9), 0.3),
            ins_rate=min(profile.ins_rate * real.ins / max(sim.ins, 1e-9), 0.3),
            del_rate=min(profile.del_rate * real.dele / max(sim.dele, 1e-9), 0.3),
            homopolymer_run_factors=tuple(factors.tolist()),
            end_factor=max(profile.end_factor + real.end_ratio - sim.end_ratio, 0.1),
            context_table=write_context_table(normalized(table * step)),
        )
    final = simulated_stats(refs, profile, seed + 1000)
    return profile, final


def subsample_clusters(clusters: list[list[str]], k: int, seed: int) -> list[list[str]]:
    """At most k reads per cluster, without replacement (same protocol as eval_real.py)."""
    rng = np.random.default_rng(train_seed(seed))
    out = []
    for reads in clusters:
        if len(reads) <= k:
            out.append(list(reads))
        else:
            idx = np.sort(rng.choice(len(reads), size=k, replace=False))
            out.append([reads[i] for i in idx])
    return out


def accuracy_curve(refs: list[str], clusters: list[list[str]], seed: int) -> np.ndarray:
    """Baseline strand accuracy (from evaluate) after subsampling to each budget."""
    decoder = MajorityVoteDecoder()
    length = len(refs[0])
    out = []
    for i, k in enumerate(BUDGETS):
        sub = subsample_clusters(clusters, k, seed + i)
        out.append(evaluate(refs, decoder.decode(sub, length), sub).strand_accuracy)
    return np.array(out)


def curve_for_profile(refs: list[str], real_clusters: list[list[str]], profile: SituationProfile, seed: int):
    """Simulated curve with each cluster sized like the real one (capped at max budget)."""
    sizes = [min(len(c), max(BUDGETS)) for c in real_clusters]
    return accuracy_curve(refs, simulate_sized(refs, sizes, profile, seed), seed)


def fit_spreads(
    stats_refs: list[str],
    real_stats: ErrorStats,
    curve_refs: list[str],
    curve_clusters: list[list[str]],
    real_curve: np.ndarray,
    start: SituationProfile,
    seed: int,
    fit_context: bool = False,
) -> SituationProfile:
    """Choose read_quality_spread and position_rate_spread by coordinate search.

    Every candidate gets its error rates refit, then is scored by the squared distance of its
    simulated accuracy curve to the real one.
    """
    cache: dict[tuple[float, float], tuple[float, SituationProfile]] = {}
    read_sigma, pos_sigma = 0.5, 1.0
    base, _ = fit_error_model(
        stats_refs,
        real_stats,
        replace(start, read_quality_spread=read_sigma, position_rate_spread=pos_sigma),
        seed,
        fit_context=fit_context,
    )

    def score(read_sigma: float, pos_sigma: float) -> float:
        key = (read_sigma, pos_sigma)
        if key not in cache:
            candidate = replace(base, read_quality_spread=read_sigma, position_rate_spread=pos_sigma)
            candidate, _ = fit_error_model(
                stats_refs, real_stats, candidate, seed, verbose=False,
                fit_context=fit_context, iterations=2, warm=True,
            )
            curve = curve_for_profile(curve_refs, curve_clusters, candidate, seed + 7)
            loss = float(np.sum((curve - real_curve) ** 2))
            print(f"  read {read_sigma:.2f} pos {pos_sigma:.2f}: sim curve {np.round(curve, 3).tolist()} loss {loss:.4f}")
            cache[key] = (loss, candidate)
        return cache[key][0]

    for _ in range(2):
        pos_sigma = min(POS_GRID, key=lambda p: score(read_sigma, p))
        read_sigma = min(READ_GRID, key=lambda r: score(r, pos_sigma))
    # The simplest candidate (smallest spreads) whose loss is within sampling noise of the
    # best: 2 x sum_k p_k (1 - p_k) / n is the expected loss of a perfect model.
    n = len(curve_refs)
    tolerance = 2.0 * float(np.sum(real_curve * (1 - real_curve))) / n
    best = min(v[0] for v in cache.values())
    ok = [(r + p, key) for key, (loss, _) in cache.items() if loss <= best + tolerance for r, p in [key]]
    chosen = min(ok)[1]
    print(f"  chosen read {chosen[0]:.2f} pos {chosen[1]:.2f} (best loss {best:.4f}, noise tolerance {tolerance:.4f})")
    return cache[chosen][1]


def rounded(profile: SituationProfile) -> SituationProfile:
    factors = profile.homopolymer_run_factors
    return replace(
        profile,
        sub_rate=round(profile.sub_rate, 5),
        ins_rate=round(profile.ins_rate, 5),
        del_rate=round(profile.del_rate, 5),
        homopolymer_factor=round(profile.homopolymer_factor, 3),
        homopolymer_run_factors=tuple(round(f, 3) for f in factors) if factors else None,
        end_factor=round(profile.end_factor, 3),
        coverage_dispersion=round(profile.coverage_dispersion, 3),
        read_quality_spread=round(profile.read_quality_spread, 3),
        position_rate_spread=round(profile.position_rate_spread, 3),
        malformed_read_rate=round(profile.malformed_read_rate, 5),
    )


def describe(name: str, stats: ErrorStats) -> str:
    return (
        f"{name:<5} sub {stats.sub:.4f}  ins {stats.ins:.4f}  del {stats.dele:.4f}  "
        f"total {stats.total:.4f}  end {stats.end_ratio:.3f}  hp {stats.hp_ratio:.3f}  "
        f"malformed {stats.outlier_fraction:.4f}  per-read err q5..q95 "
        f"{np.round(stats.read_error_quantiles, 3).tolist()}"
    )


def coverage_rows(sizes: np.ndarray, shape: float, seed: int) -> list[tuple[str, float, float]]:
    """Real cluster sizes vs negative binomial draws with the real mean and fitted shape."""
    rng = np.random.default_rng(train_seed(seed))
    sim = sample_coverage(rng, float(sizes.mean()), shape, 200_000)
    return [
        ("coverage variance", float(sizes.var()), float(sim.var())),
        ("coverage median", float(np.median(sizes)), float(np.median(sim))),
        ("frac clusters <= 3", float(np.mean(sizes <= 3)), float(np.mean(sim <= 3))),
    ]


def comparison_table(real: ErrorStats, sim: ErrorStats, extra: list[tuple[str, float, float]] = ()) -> str:
    rows = [
        ("substitution rate", real.sub, sim.sub),
        ("insertion rate", real.ins, sim.ins),
        ("deletion rate", real.dele, sim.dele),
        ("total error rate", real.total, sim.total),
        ("end/start error ratio", real.end_ratio, sim.end_ratio),
        ("homopolymer factor", real.hp_ratio, sim.hp_ratio),
        ("per-read err sd", real.read_error_sd, sim.read_error_sd),
        ("malformed read share", real.outlier_fraction, sim.outlier_fraction),
        *extra,
    ]
    names = ("sub", "ins", "del")
    for t in range(3):
        for r in range(MAX_RUN):
            label = f"{names[t]}/base run={r + 1}{'+' if r + 1 == MAX_RUN else ''}"
            rows.append((label, real.by_run[t, r], sim.by_run[t, r]))
    for idx in np.array_split(np.arange(real.length), 5):
        rows.append((f"err/base pos {idx[0]}-{idx[-1]}", real.position[idx].mean(), sim.position[idx].mean()))
    lines = [
        f"| {'statistic':<22} | {'real':>8} | {'sim':>8} | {'rel diff':>8} |",
        f"|{'-' * 24}|{'-' * 10}|{'-' * 10}|{'-' * 10}|",
    ]
    for label, a, b in rows:
        rel = f"{(b - a) / a:+8.1%}" if a else f"{'n/a':>8}"
        lines.append(f"| {label:<22} | {a:8.4f} | {b:8.4f} | {rel} |")
    return "\n".join(lines)


def curve_table(rows: list[tuple[str, np.ndarray]]) -> str:
    head = "| reads per cluster | " + " | ".join(f"{k:>6}" for k in BUDGETS) + " |"
    lines = [head, "|" + "---|" * (len(BUDGETS) + 1)]
    for label, curve in rows:
        lines.append(f"| {label:<17} | " + " | ".join(f"{a:6.1%}" for a in curve) + " |")
    return "\n".join(lines)


def load_dnaformer_train(name: str) -> list[realdata.RealCluster]:
    path = realdata.DNAFORMER_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing, run scripts/download_data.sh --full")
    clusters = realdata.load_dnaformer(name, split="train")
    with path.open("rb") as f:
        f.seek(max(path.stat().st_size - 2, 0))
        clean_end = f.read() == b"\n\n"
    if not clean_end and clusters:
        # Partially downloaded file: the last cluster may be cut off mid-read.
        print(f"  {name}: file does not end cleanly, dropping the last cluster")
        clusters = clusters[:-1]
    return clusters


def calibrate(
    clusters: list[realdata.RealCluster],
    profile_names: list[str],
    source: str,
    seed: int,
    fit_dispersion: bool,
    dry_run: bool,
    fit_spread: bool = True,
    fit_context: bool = False,
    table_out: str | None = None,
) -> SituationProfile:
    """Fit one dataset. table_out: write the context table under this name in profiles/
    even on a dry run (used for Simulator B's table)."""
    # Most common reference length only (DNAformer files may contain a few odd ones).
    lengths = np.array([len(c.reference) for c in clusters])
    length = int(np.bincount(lengths).argmax())
    clusters = [c for c in clusters if len(c.reference) == length]
    rng = np.random.default_rng(train_seed(seed))
    order = rng.permutation(len(clusters))  # shuffled, so fit and validation are comparable
    clusters = [clusters[i] for i in order]
    refs = [c.reference for c in clusters]
    reads = [c.reads for c in clusters]

    # Validation clusters are taken first so they never overlap the fit, whatever the size.
    val_refs, val_reads = refs[:VAL_CLUSTERS], reads[:VAL_CLUSTERS]
    stats_refs = refs[VAL_CLUSTERS : VAL_CLUSTERS + STATS_CLUSTERS]
    stats_reads = reads[VAL_CLUSTERS : VAL_CLUSTERS + STATS_CLUSTERS]
    fit_refs, fit_reads = stats_refs[:CURVE_CLUSTERS], stats_reads[:CURVE_CLUSTERS]

    real = error_stats(stats_refs, stats_reads)
    mean, var, moments_shape, shape = coverage_fit(reads)
    print(f"\n== {source}: {len(clusters)} train clusters of length {length}")
    print(f"  coverage mean {mean:.2f} var {var:.2f} -> NB shape MLE {shape:.3f} (moments {moments_shape:.3f})")
    print("  " + describe("real", real))

    base = load_profile(profile_names[0])
    if fit_dispersion:
        base = replace(base, coverage_dispersion=shape)
    if fit_spread:
        real_curve = accuracy_curve(fit_refs, fit_reads, seed)
        print(f"  real accuracy curve (fit clusters) {np.round(real_curve, 3).tolist()}")
        fitted = fit_spreads(stats_refs, real, fit_refs, fit_reads, real_curve, base, seed, fit_context)
    else:
        fitted = base
    fitted, _ = fit_error_model(stats_refs, real, fitted, seed, fit_context=fit_context, warm=fit_spread)
    fitted = rounded(fitted)
    table_name = None
    if fit_context:
        _, table = load_context_table(fitted.context_table)
        table_name = table_out or f"context/{profile_names[0]}.json"
        table_path = PROFILES_DIR / table_name if (table_out or not dry_run) else _TABLE_DIR / table_name
        fitted = replace(fitted, context_table=write_context_table(table, table_path, f"{source} (train split)"))
        print(f"  context table -> {table_path}")

    # Validation on clusters not used for any fit.
    real_val = error_stats(val_refs, val_reads)
    sim_val = simulated_stats(val_refs, fitted, seed + 500)
    print("  " + describe("real", real_val))
    print("  " + describe("sim", sim_val))
    sizes = np.array([len(r) for r in reads], dtype=float)
    extra = coverage_rows(sizes, fitted.coverage_dispersion, seed)
    # Hot spots and per-k-mer rates need the real cluster sizes.
    sim_full = simulate_sized(val_refs, [len(c) for c in val_reads], fitted, seed + 600)
    hot_real, hot_sim = hotspot_shares(val_refs, val_reads), hotspot_shares(val_refs, sim_full)
    sim_ctx = error_stats(val_refs, sim_full)
    rel_real, rel_sim = real_val.ctx_relative(), sim_ctx.ctx_relative()
    for t, name in enumerate(TYPE_NAMES):
        extra.append((f"hot-spot share {name}", float(hot_real[t]), float(hot_sim[t])))
    for t, name in enumerate(TYPE_NAMES):
        corr = float(np.corrcoef(np.log(rel_real[t]), np.log(rel_sim[t]))[0, 1])
        extra.append((f"5-mer log-rate corr {name}", 1.0, corr))
    table = comparison_table(real_val, sim_val, extra)
    print(table)
    print("  (5-mer log-rate corr: correlation of per-5-mer relative rates, real vs sim, on the")
    print("   validation clusters; the 'real' column is 1 by definition)")

    if fit_spread:
        real_curve = accuracy_curve(val_refs, val_reads, seed + 100)
        matched = curve_for_profile(val_refs, val_reads, fitted, seed + 200)
        nb_profile = replace(
            fitted, dropout_rate=0.0, decay_per_year=0.0, gc_dropout_factor=0.0, coverage_mean=mean
        )
        nb = accuracy_curve(val_refs, simulate(val_refs, nb_profile, train_seed(seed + 300)), seed + 300)
        old = replace(
            fitted,
            homopolymer_run_factors=None,
            read_quality_spread=0.0,
            malformed_read_rate=0.0,
            homopolymer_factor=real_val.hp_ratio,
            position_rate_spread=0.0,
            context_table=None,
        )
        old, _ = fit_error_model_legacy(val_refs, real_val, old, seed + 400)
        old_curve = curve_for_profile(val_refs, val_reads, old, seed + 200)
        print(f"\n  baseline strand accuracy, {len(val_refs)} held-apart train clusters of {source}")
        print(
            curve_table(
                [
                    ("real", real_curve),
                    ("sim, new model", matched),
                    ("sim, NB coverage", nb),
                    ("sim, old model", old_curve),
                ]
            )
        )
        print("  (sim rows except 'NB coverage' use the real cluster sizes; 'NB coverage' draws")
        print(f"   sizes from the fitted negative binomial with mean {mean:.1f}; old model = no spread,")
        print("   single homopolymer_factor for all error types, no spreads, no malformed reads)")

    for name in profile_names:
        current = load_profile(name)
        updated = replace(
            current,
            sub_rate=fitted.sub_rate * (ARCHIVE_SUB_BOOST if "archive" in name else 1.0),
            ins_rate=fitted.ins_rate,
            del_rate=fitted.del_rate,
            homopolymer_factor=round(real.hp_ratio, 3),  # informational, overridden by run factors
            homopolymer_run_factors=fitted.homopolymer_run_factors,
            end_factor=fitted.end_factor,
            read_quality_spread=fitted.read_quality_spread,
            position_rate_spread=fitted.position_rate_spread,
            malformed_read_rate=fitted.malformed_read_rate,
            coverage_dispersion=fitted.coverage_dispersion if fit_dispersion else current.coverage_dispersion,
            calibrated_from=f"{source} (train split)",
        )
        updated = replace(updated, context_table=table_name)
        if dry_run:
            print(f"  [dry run] {name}: {updated}")
        else:
            print(f"  wrote {save_profile(updated)}")
    return fitted


def fit_error_model_legacy(
    refs: list[str], real: ErrorStats, start: SituationProfile, seed: int
) -> tuple[SituationProfile, ErrorStats]:
    """The first-version model (single homopolymer_factor), kept only for the comparison row."""
    profile = replace(start, sub_rate=real.sub, ins_rate=real.ins, del_rate=real.dele)
    for it in range(FIT_ITERATIONS):
        sim = simulated_stats(refs, profile, seed + it)
        profile = replace(
            profile,
            sub_rate=min(profile.sub_rate * real.sub / max(sim.sub, 1e-9), 0.3),
            ins_rate=min(profile.ins_rate * real.ins / max(sim.ins, 1e-9), 0.3),
            del_rate=min(profile.del_rate * real.dele / max(sim.dele, 1e-9), 0.3),
            homopolymer_factor=max(profile.homopolymer_factor * real.hp_ratio / sim.hp_ratio, 0.5),
            end_factor=max(profile.end_factor + real.end_ratio - sim.end_ratio, 0.1),
        )
    return profile, simulated_stats(refs, profile, seed + 1000)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dnaformer-file",
        action="append",
        default=[],
        help="DNAformer file stem; Illumina files calibrate illumina_standard",
    )
    parser.add_argument("--skip-microsoft", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="measure and compare, write nothing")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--no-context", action="store_true", help="don't fit a 5-mer context table")
    args = parser.parse_args(argv)

    if not args.skip_microsoft:
        calibrate(
            realdata.load_microsoft("train"),
            ["nanopore_budget"],
            "microsoft_nanopore",
            args.seed,
            fit_dispersion=True,
            dry_run=args.dry_run,
            fit_context=not args.no_context,
        )

    for name in args.dnaformer_file:
        clusters = load_dnaformer_train(name)
        if "illumina" in name.lower():
            profiles, fit_dispersion, dry = ["illumina_standard"], True, args.dry_run
        else:
            # DNAformer Nanopore doesn't change nanopore_budget (calibrated on Microsoft, the
            # dataset we benchmark on); its context table becomes Simulator B's table.
            profiles, fit_dispersion, dry = ["nanopore_budget"], False, True
        # Illumina has ~1 error per 1000 bases: too few events per 5-mer for a context table.
        fit_context = "illumina" not in name.lower() and not args.no_context
        table_out = None if "illumina" in name.lower() else "context/nanopore_budget_b.json"
        calibrate(
            clusters, profiles, f"dnaformer_{name}", args.seed, fit_dispersion, dry,
            fit_context=fit_context, table_out=table_out,
        )


if __name__ == "__main__":
    main()
