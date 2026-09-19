"""Writes fake results to results/mock/ so the dashboard can be built before the loop exists.

Everything is marked is_mock=True. The dashboard must show a visible MOCK banner for these.
The shapes follow PROJECT.md: Pareto points per alternation, ablation ladder A to D,
crossover matrix, firewall tests, and candidate examples.
"""

from __future__ import annotations

import shutil

import numpy as np

from dnacodec.profiles import load_profile
from dnacodec.results import (
    RESULTS_DIR,
    AblationEntry,
    CandidateExample,
    CoveragePoint,
    CrossoverEntry,
    ExperimentSummary,
    FirewallEntry,
    IterationResult,
    RunResult,
    save_run,
    save_summary,
)
from dnacodec.types import EncoderSettings, Metrics

CORE = ("nanopore_budget", "illumina_standard")
N_TRIALS = 300


def fake_metrics(rng: np.random.Generator, accuracy: float, bits: float, reads: float) -> Metrics:
    length = 110
    ramp = np.linspace(1.0, 2.0, length)
    recovery = float(np.clip((accuracy - 0.6) / 0.35, 0, 1))
    return Metrics(
        n_strands=10_000,
        strand_accuracy=accuracy,
        mean_edit_distance=float((1 - accuracy) * 3),
        dropout_rate=0.03,
        reads_per_strand=reads,
        per_position_error=[float(x) for x in (1 - accuracy) * 0.02 * ramp * rng.uniform(0.8, 1.2, length)],
        file_recovered=recovery >= 1.0,
        bits_per_base=bits,
        write_cost_usd_per_mb=float(8e6 / bits * 1e-4),
        read_cost_usd_per_mb=float(8e6 / bits / 110 * reads * 1e-5),
        recovery_rate=recovery,
        n_trials=N_TRIALS,
    )


def main() -> None:
    shutil.rmtree(RESULTS_DIR / "mock", ignore_errors=True)  # stale files from older formats break loading
    rng = np.random.default_rng(0)
    default = EncoderSettings()
    for name in CORE:
        profile = load_profile(name)
        nanopore = profile.technology == "nanopore"
        start_acc, start_bits = (0.80, 1.40) if nanopore else (0.94, 1.40)
        start_reads = 8.5 if nanopore else 9.0
        iterations = []
        for i in range(3):
            gain = 1 - 0.5 ** (i + 1)
            settings = EncoderSettings(
                redundancy=round(0.3 - 0.08 * gain, 3),
                max_homopolymer=3 if nanopore else None,
                gc_min=0.4,
                gc_max=0.6,
                risk_threshold=0.5 if nanopore else 0.7,
            )
            iterations.append(
                IterationResult(
                    iteration=i,
                    settings=settings,
                    metrics=fake_metrics(rng, start_acc + 0.12 * gain, start_bits + 0.12 * gain, profile.coverage_mean),
                    min_reads_at_target=round(start_reads - (2.5 if nanopore else 1.5) * gain, 2),
                    risky_kmers=(
                        [("AAAA", 0.81), ("TTTT", 0.77), ("GGGG", 0.70), ("CCCC", 0.66), ("GCGCG", 0.41)]
                        if nanopore
                        else [("GGCGG", 0.34), ("CCGCC", 0.31), ("AAAA", 0.12), ("TTTT", 0.11)]
                    ),
                    notes="mock",
                )
            )
        curve = [
            CoveragePoint(decoder=dec, coverage=c, strand_accuracy=float(1 - np.exp(-c / scale)))
            for dec, scale in (("baseline", 6.0), ("transformer", 3.5), ("tailored", 2.5))
            for c in (1, 2, 4, 6, 8, 10, 15, 20)
        ]
        run = RunResult(
            situation=name,
            profile=profile,
            recovery_target=1.0,
            n_trials=N_TRIALS,
            default_settings=default,
            default_metrics=fake_metrics(rng, start_acc, start_bits, profile.coverage_mean),
            iterations=iterations,
            default_min_reads_at_target=start_reads,
            coverage_curve=curve,
            is_mock=True,
        )
        print(save_run(run, "mock"))

    ablation = [
        AblationEntry(situation=s, system=sys, metrics=fake_metrics(rng, acc, bits, 6.0), min_reads_at_target=reads)
        for s in CORE
        for sys, acc, bits, reads in (
            ("A", 0.68, 1.40, 11.0),
            ("B", 0.80, 1.40, 8.5),
            ("C", 0.88, 1.47, 6.8),
            ("D", 0.91, 1.49, 6.0),
        )
    ]
    crossover = [
        CrossoverEntry(codec=codec, channel=channel, metrics=fake_metrics(rng, acc, 1.45, 6.0), min_reads_at_target=reads)
        for codec, channel, acc, reads in (
            ("default", "nanopore_budget", 0.80, 8.5),
            ("default", "illumina_standard", 0.94, 9.0),
            ("nanopore_budget", "nanopore_budget", 0.91, 6.0),
            ("nanopore_budget", "illumina_standard", 0.93, 9.2),
            ("illumina_standard", "nanopore_budget", 0.76, 9.4),
            ("illumina_standard", "illumina_standard", 0.97, 7.5),
        )
    ]
    firewall = [
        FirewallEntry("nanopore_budget", "sim_a_heldout", "min_reads_tailored", 6.0),
        FirewallEntry("nanopore_budget", "sim_b", "min_reads_tailored", 6.6, "gain shrinks but survives"),
        FirewallEntry("nanopore_budget", "real", "risk_auc", 0.68),
        FirewallEntry("illumina_standard", "sim_b", "min_reads_tailored", 7.9),
        FirewallEntry("illumina_standard", "real", "risk_auc", 0.61),
    ]
    examples = [
        CandidateExample(
            strand="".join(rng.choice(list("ACGT"), 110)),
            risk={"nanopore_budget": r_n, "illumina_standard": r_i},
            accepted={"nanopore_budget": r_n < 0.5, "illumina_standard": r_i < 0.7},
        )
        for r_n, r_i in ((0.82, 0.15), (0.12, 0.10), (0.64, 0.22), (0.30, 0.74))
    ]
    print(save_summary(ExperimentSummary(ablation, crossover, firewall, examples, is_mock=True), "mock"))


if __name__ == "__main__":
    main()
