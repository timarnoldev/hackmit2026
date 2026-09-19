"""Writes fake RunResults to results/mock/ so the dashboard can be built before the loop exists.

Every file is marked is_mock=True. The dashboard must show a visible MOCK banner for these.
"""

from __future__ import annotations

import numpy as np

from dnacodec.profiles import list_profiles, load_profile
from dnacodec.results import CoveragePoint, IterationResult, RunResult, save_run
from dnacodec.types import EncoderSettings, Metrics


def fake_metrics(rng: np.random.Generator, accuracy: float, bits: float, reads: float) -> Metrics:
    length = 110
    ramp = np.linspace(1.0, 2.0, length)
    return Metrics(
        n_strands=10_000,
        strand_accuracy=accuracy,
        mean_edit_distance=float((1 - accuracy) * 3),
        dropout_rate=0.03,
        reads_per_strand=reads,
        per_position_error=[float(x) for x in (1 - accuracy) * 0.02 * ramp * rng.uniform(0.8, 1.2, length)],
        file_recovered=accuracy > 0.9,
        bits_per_base=bits,
        write_cost_usd_per_mb=float(8e6 / bits * 1e-4),
        read_cost_usd_per_mb=float(8e6 / bits / 110 * reads * 1e-5),
    )


def main() -> None:
    rng = np.random.default_rng(0)
    default = EncoderSettings()
    for name in list_profiles():
        profile = load_profile(name)
        hard = profile.technology == "nanopore"
        start_acc = 0.78 if hard else 0.93
        iterations = []
        for i in range(5):
            gain = 1 - 0.5**i
            settings = EncoderSettings(
                redundancy=round(0.3 + (0.1 if hard else -0.1) * gain, 3),
                max_homopolymer=2 if hard else (4 if i >= 2 else 3),
                gc_min=0.4 if hard else (0.3 if i >= 2 else 0.4),
                gc_max=0.6 if hard else (0.7 if i >= 2 else 0.6),
            )
            iterations.append(
                IterationResult(
                    iteration=i,
                    settings=settings,
                    metrics=fake_metrics(rng, start_acc + (0.97 - start_acc) * gain, 1.45 + 0.15 * gain, profile.coverage_mean),
                    risky_kmers=[("AAAAG", 0.81), ("GGGCC", 0.74), ("TTTTT", 0.69), ("CGCGC", 0.52)],
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
            default_settings=default,
            default_metrics=fake_metrics(rng, start_acc, 1.45, profile.coverage_mean),
            iterations=iterations,
            coverage_curve=curve,
            is_mock=True,
        )
        print(save_run(run, "mock"))


if __name__ == "__main__":
    main()
