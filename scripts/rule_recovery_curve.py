"""Do the hand rules help or hurt? Measured as a curve, not as a knife edge.

The rule audit reports "fewest reads at which all trials recover". Around the threshold that
metric is brittle: one failed trial out of 300 moves the answer by several reads. This script
measures the whole recovery curve instead, so the comparison does not depend on where a single
trial lands.

    uv run python scripts/rule_recovery_curve.py [--trials 300] [--workers 16]
"""

from __future__ import annotations

import argparse
import dataclasses
import json

from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.evaluate import recovery_trials
from dnacodec.profiles import load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import heldout_seeds
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

VARIANTS = {
    "both rules (standard)": EncoderSettings(),
    "homopolymer rule off": EncoderSettings(max_homopolymer=None),
    "GC rule off": EncoderSettings(gc_min=None, gc_max=None),
    "both rules off": EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None),
    "both rules, payload only": EncoderSettings(constrain_seed=False),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--coverages", default="10,12,14,16,18,20,22,24,26")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    coverages = [float(c) for c in args.coverages.split(",")]
    seeds = heldout_seeds(args.trials)
    data, decoder = test_file(), MajorityVoteDecoder()

    print(f"{args.profile}, {args.trials} held-out trials per point, baseline decoder")
    print("recovery rate (share of trials where the file came back exactly)\n")
    header = f"{'codec':28s}" + "".join(f"{c:>7.0f}" for c in coverages)
    print(header)
    table: dict[str, dict[float, float]] = {}
    for name, settings in VARIANTS.items():
        row, table[name] = f"{name:28s}", {}
        for coverage in coverages:
            channel = dataclasses.replace(profile, coverage_mean=coverage)
            metrics = recovery_trials(data, settings, None, decoder, channel, seeds, workers=args.workers)
            rate = float(metrics.recovery_rate or 0.0)
            table[name][coverage] = rate
            row += f"{rate:7.3f}"
        print(row, flush=True)

    path = RESULTS_DIR / f"rule_recovery_curve_{args.profile}.json"
    path.write_text(json.dumps({"trials": args.trials, "profile": args.profile, "table": table}, indent=2) + "\n")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
