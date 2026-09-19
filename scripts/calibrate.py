"""Calibrate the channel simulator on real reads (train splits only). Owner: Agent A.

Measures substitution, insertion and deletion rates, the position ramp, the homopolymer
effect and coverage dispersion by aligning real reads to their references with
Levenshtein editops. Then fits simulator parameters so that the SAME measurement applied
to simulated reads (same references, same alignment) reproduces the real statistics,
and writes the fitted numbers into the profiles.

    uv run python scripts/calibrate.py                    # Nanopore from Microsoft train
    uv run python scripts/calibrate.py --dry-run          # measure and compare, write nothing
    uv run python scripts/calibrate.py --dnaformer-file BinnedTestIllumina_Random

Only split="train" is ever loaded here. Simulation seeds go through train_seed().
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace

import numpy as np
from rapidfuzz.distance import Levenshtein

from dnacodec import realdata
from dnacodec.profiles import SituationProfile, load_profile, save_profile
from dnacodec.seeds import train_seed
from dnacodec.simulator import run_lengths, sample_coverage, simulate
from dnacodec.simulator import _encode as encode_strands

# Reads further than this fraction of the reference length from it are treated as
# misbinned or chimeric, not as channel noise. Applied identically to real and simulated.
OUTLIER_FRACTION = 0.3
MAX_RUN = 6  # run lengths >= MAX_RUN are pooled in the homopolymer statistic
SIM_READS_PER_REF = 8
FIT_ITERATIONS = 6
# The archive profile keeps extra substitutions for storage damage (deamination) on top
# of the measured Illumina rate. Extrapolated, there is no real aged-DNA data here.
ARCHIVE_SUB_BOOST = 1.5

TAGS = {"replace": 0, "insert": 1, "delete": 2}


@dataclass
class ErrorStats:
    n_reads: int
    n_outliers: int
    length: int
    sub: float
    ins: float
    dele: float
    end_ratio: float  # fitted linear error rate at the last base / at the first base
    hp_ratio: float  # fitted per-extra-run-base multiplier of the error rate
    position: np.ndarray  # total error events per read per position, len == length
    by_run: np.ndarray  # error rate per base by run length 1..MAX_RUN
    read_error_sd: float  # spread of per-read error fraction (heterogeneity between reads)

    @property
    def total(self) -> float:
        return self.sub + self.ins + self.dele


def error_stats(refs: list[str], clusters: list[list[str]]) -> ErrorStats:
    lengths = {len(r) for r in refs}
    if len(lengths) != 1:
        raise ValueError(f"expected one reference length, got {sorted(lengths)}")
    length = lengths.pop()
    codes, _ = encode_strands(refs)
    runs = np.minimum(run_lengths(codes), MAX_RUN)  # (n_refs, length)

    counts = np.zeros((3, length), dtype=np.int64)
    run_events = np.zeros(MAX_RUN + 1, dtype=np.int64)
    run_bases = np.zeros(MAX_RUN + 1, dtype=np.int64)
    per_read = []
    n_reads = n_outliers = 0
    cutoff = int(OUTLIER_FRACTION * length)
    for i, (ref, reads) in enumerate(zip(refs, clusters)):
        used = 0
        ref_runs = runs[i]
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
            np.add.at(run_events, ref_runs[pos], 1)
        n_reads += used
        if used:
            run_bases += used * np.bincount(ref_runs, minlength=MAX_RUN + 1)

    bases = max(n_reads * length, 1)
    sub, ins, dele = (counts.sum(axis=1) / bases).tolist()
    position = counts.sum(axis=0) / max(n_reads, 1)

    x = np.arange(length)
    slope, intercept = np.polyfit(x, position, 1)
    end_ratio = float((intercept + slope * (length - 1)) / intercept)

    by_run = run_events[1:] / np.maximum(run_bases[1:], 1)
    r = np.arange(1, MAX_RUN + 1)
    ok = (run_bases[1:] > 0) & (by_run > 0)
    weights = np.sqrt(run_events[1:][ok])
    log_ratio = np.log(by_run[ok] / by_run[0])
    # weighted least squares through the origin: log(rate_r / rate_1) = (r - 1) log f
    hp_log = np.sum(weights * (r[ok] - 1) * log_ratio) / np.sum(weights * (r[ok] - 1) ** 2)
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
        read_error_sd=float(np.std(per_read)) if per_read else 0.0,
    )


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


def simulated_stats(refs: list[str], profile: SituationProfile, seed: int) -> ErrorStats:
    sim_profile = replace(
        profile,
        dropout_rate=0.0,
        decay_per_year=0.0,
        gc_dropout_factor=0.0,
        coverage_mean=float(SIM_READS_PER_REF),
        coverage_dispersion=1e4,  # nearly constant coverage, only errors matter here
    )
    return error_stats(refs, simulate(refs, sim_profile, train_seed(seed)))


def fit_error_model(
    refs: list[str], real: ErrorStats, start: SituationProfile, seed: int
) -> tuple[SituationProfile, ErrorStats]:
    """Adjust rates, homopolymer_factor and end_factor until simulated stats match real."""
    profile = replace(
        start,
        sub_rate=real.sub,
        ins_rate=real.ins,
        del_rate=real.dele,
        homopolymer_factor=real.hp_ratio,
        end_factor=max(real.end_ratio, 0.1),
    )
    for it in range(FIT_ITERATIONS):
        sim = simulated_stats(refs, profile, seed + it)
        print(
            f"  fit {it}: sim sub {sim.sub:.4f} ins {sim.ins:.4f} del {sim.dele:.4f} "
            f"end {sim.end_ratio:.3f} hp {sim.hp_ratio:.3f}"
        )
        profile = replace(
            profile,
            sub_rate=min(profile.sub_rate * real.sub / max(sim.sub, 1e-9), 0.3),
            ins_rate=min(profile.ins_rate * real.ins / max(sim.ins, 1e-9), 0.3),
            del_rate=min(profile.del_rate * real.dele / max(sim.dele, 1e-9), 0.3),
            homopolymer_factor=max(profile.homopolymer_factor * real.hp_ratio / sim.hp_ratio, 0.5),
            end_factor=max(profile.end_factor + real.end_ratio - sim.end_ratio, 0.1),
        )
    final = simulated_stats(refs, profile, seed + 1000)
    return profile, final


def rounded(profile: SituationProfile) -> SituationProfile:
    return replace(
        profile,
        sub_rate=round(profile.sub_rate, 5),
        ins_rate=round(profile.ins_rate, 5),
        del_rate=round(profile.del_rate, 5),
        homopolymer_factor=round(profile.homopolymer_factor, 3),
        end_factor=round(profile.end_factor, 3),
        coverage_dispersion=round(profile.coverage_dispersion, 3),
    )


def describe(name: str, stats: ErrorStats) -> str:
    return (
        f"{name:<10} sub {stats.sub:.4f}  ins {stats.ins:.4f}  del {stats.dele:.4f}  "
        f"total {stats.total:.4f}  end {stats.end_ratio:.3f}  hp {stats.hp_ratio:.3f}  "
        f"read-sd {stats.read_error_sd:.4f}"
    )


def coverage_rows(sizes: np.ndarray, shape: float, seed: int) -> list[tuple[str, float, float]]:
    """Real cluster sizes vs negative binomial draws with the real mean and fitted shape."""
    rng = np.random.default_rng(train_seed(seed))
    sim = sample_coverage(rng, float(sizes.mean()), shape, 200_000)
    return [
        ("coverage variance", float(sizes.var()), float(sim.var())),
        ("coverage median", float(np.median(sizes)), float(np.median(sim))),
        ("coverage 90th pct", float(np.percentile(sizes, 90)), float(np.percentile(sim, 90))),
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
        *extra,
    ]
    lines = [
        f"| {'statistic':<22} | {'real':>8} | {'sim':>8} | {'rel diff':>8} |",
        f"|{'-' * 24}|{'-' * 10}|{'-' * 10}|{'-' * 10}|",
    ]
    for label, a, b in rows:
        lines.append(f"| {label:<22} | {a:8.4f} | {b:8.4f} | {(b - a) / a:+8.1%} |")
    for r in range(MAX_RUN):
        label = f"err/base run={r + 1}{'+' if r + 1 == MAX_RUN else ''}"
        a, b = real.by_run[r], sim.by_run[r]
        lines.append(f"| {label:<22} | {a:8.4f} | {b:8.4f} | {(b - a) / a:+8.1%} |")
    bins = np.array_split(np.arange(real.length), 5)
    for idx in bins:
        a, b = real.position[idx].mean(), sim.position[idx].mean()
        label = f"err/base pos {idx[0]}-{idx[-1]}"
        lines.append(f"| {label:<22} | {a:8.4f} | {b:8.4f} | {(b - a) / a:+8.1%} |")
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
    max_clusters: int | None,
) -> str:
    if max_clusters:
        clusters = clusters[:max_clusters]
    refs = [c.reference for c in clusters]
    reads = [c.reads for c in clusters]
    real = error_stats(refs, reads)
    mean, var, moments_shape, shape = coverage_fit(reads)
    outlier_share = real.n_outliers / max(real.n_reads + real.n_outliers, 1)
    print(
        f"\n== {source}: {len(clusters)} train clusters, {real.n_reads} reads used, "
        f"{real.n_outliers} outlier reads dropped ({outlier_share:.2%})"
    )
    print(f"  coverage mean {mean:.2f} var {var:.2f} -> NB shape MLE {shape:.3f} (moments {moments_shape:.3f})")
    print("  " + describe("real", real))

    base = load_profile(profile_names[0])
    fitted, sim = fit_error_model(refs, real, base, seed)
    print("  " + describe("sim", sim))
    sizes = np.array([len(r) for r in reads], dtype=float)
    table = comparison_table(real, sim, coverage_rows(sizes, shape, seed))
    print(table)

    for name in profile_names:
        current = load_profile(name)
        updated = replace(
            current,
            sub_rate=fitted.sub_rate * (ARCHIVE_SUB_BOOST if "archive" in name else 1.0),
            ins_rate=fitted.ins_rate,
            del_rate=fitted.del_rate,
            homopolymer_factor=fitted.homopolymer_factor,
            end_factor=fitted.end_factor,
            coverage_dispersion=shape if fit_dispersion else current.coverage_dispersion,
            calibrated_from=f"{source} (train split)",
        )
        updated = rounded(updated)
        if dry_run:
            print(f"  [dry run] {name}: {updated}")
        else:
            print(f"  wrote {save_profile(updated)}")
    return table


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dnaformer-file",
        action="append",
        default=[],
        help="DNAformer file stem; Illumina files calibrate the Illumina profiles",
    )
    parser.add_argument("--skip-microsoft", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="measure and compare, write nothing")
    parser.add_argument("--max-clusters", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args(argv)

    if not args.skip_microsoft:
        calibrate(
            realdata.load_microsoft("train"),
            ["nanopore_budget"],
            "microsoft_nanopore",
            args.seed,
            fit_dispersion=True,
            dry_run=args.dry_run,
            max_clusters=args.max_clusters,
        )

    for name in args.dnaformer_file:
        clusters = load_dnaformer_train(name)
        if "illumina" in name.lower():
            profiles, fit_dispersion, dry = ["illumina_standard", "illumina_archive_100y"], True, args.dry_run
        else:
            # Extra Nanopore files are measured for comparison only; nanopore_budget stays
            # calibrated on Microsoft, the dataset we benchmark on.
            profiles, fit_dispersion, dry = ["nanopore_budget"], False, True
        calibrate(clusters, profiles, f"dnaformer_{name}", args.seed, fit_dispersion, dry, args.max_clusters)


if __name__ == "__main__":
    main()
