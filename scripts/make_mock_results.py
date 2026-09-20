"""Writes synthetic results to results/mock/ for dashboard development and tests.

Everything is marked is_mock=True, and the dashboard shows a visible MOCK banner for it.
The shapes follow the evidence design in PROJECT.md: Pareto points per alternation, ablation
ladder A to D, crossover matrix, firewall tests, and candidate examples. Nothing here is a
measurement, and no number from results/mock/ is ever quoted.
"""

from __future__ import annotations

import shutil

import numpy as np

from dnacodec.profiles import load_profile
from dnacodec.results import (
    RESULTS_DIR,
    AblationEntry,
    RuleAuditEntry,
    Tier2Entry,
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
    accuracy = min(accuracy, 0.995)
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
                    stage=("tier1", "alternation 0", "alternation 1")[i],
                    default_min_reads_matched=round(start_reads - 0.1 * (i + 1), 2),
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
            ("C", 0.85, 1.45, 7.4),
            ("D", 0.89, 1.48, 6.5),
            ("E", 0.91, 1.49, 6.0),
        )
    ]
    rule_audit = [
        RuleAuditEntry("nanopore_budget", "max_homopolymer=3", 8.5, 12.0, 1.40, 1.40, "pays off"),
        RuleAuditEntry("nanopore_budget", "gc 0.4-0.6", 8.5, 8.6, 1.40, 1.40, "no measurable benefit"),
        RuleAuditEntry("nanopore_budget", "redundancy 0.3 vs tuned", 8.5, 7.4, 1.40, 1.45, "tuned is better"),
        RuleAuditEntry("illumina_standard", "max_homopolymer=3", 9.0, 9.0, 1.40, 1.40, "no measurable benefit"),
        RuleAuditEntry("illumina_standard", "gc 0.4-0.6", 9.0, 9.1, 1.40, 1.40, "no measurable benefit"),
        RuleAuditEntry("illumina_standard", "redundancy 0.3 vs tuned", 9.0, 8.0, 1.40, 1.52, "tuned is better"),
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
    tier2 = [
        Tier2Entry("nanopore_budget", 6.0, 8, 300, 0.0099, 0.0047, -0.0052, (-0.0073, -0.0033), 0.93, 0.97, 0.04, (-0.02, 0.10)),
        Tier2Entry("nanopore_budget", 6.0, 32, 300, 0.0099, 0.0045, -0.0054, (-0.0076, -0.0034), 0.93, 0.96, 0.03, (-0.02, 0.09)),
        Tier2Entry("nanopore_budget", 6.0, 8, 300, 0.0110, 0.0071, -0.0039, (-0.0062, -0.0015), 0.90, 0.93, 0.03, (-0.03, 0.09), simulator="B"),
        Tier2Entry("illumina_standard", 20.0, 8, 300, 0.0036, 0.0036, -0.0002, (-0.0009, 0.0005), 1.0, 1.0, 0.0, (0.0, 0.0)),
    ]
    summary = ExperimentSummary(ablation, rule_audit, crossover, firewall, examples, tier2, is_mock=True)
    print(save_summary(summary, "mock"))


if __name__ == "__main__":
    main()
