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

VARIANT_SETS = {
    # Does each hand rule pay off at all?
    "rules": {
        "both rules (standard)": EncoderSettings(),
        "homopolymer rule off": EncoderSettings(max_homopolymer=None),
        "GC rule off": EncoderSettings(gc_min=None, gc_max=None),
        "both rules off": EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None),
        "both rules, payload only": EncoderSettings(constrain_seed=False),
    },
    # Where should the homopolymer line be drawn? The field says 3, the risk model says the
    # damage starts at 5. Between those two sits a claim we make on a slide, so measure it.
    "threshold": {
        "max run 3 (the field's rule)": EncoderSettings(max_homopolymer=3),
        "max run 4": EncoderSettings(max_homopolymer=4),
        "max run 5 (what the model says)": EncoderSettings(max_homopolymer=5),
        "max run 6": EncoderSettings(max_homopolymer=6),
        "no limit": EncoderSettings(max_homopolymer=None),
    },
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--coverages", default="10,12,14,16,18,20,22,24,26")
    ap.add_argument("--set", dest="variant_set", default="rules", choices=sorted(VARIANT_SETS))
    args = ap.parse_args()

    profile = load_profile(args.profile)
    coverages = [float(c) for c in args.coverages.split(",")]
    seeds = heldout_seeds(args.trials)
    data, decoder = test_file(), MajorityVoteDecoder()

    print(f"{args.profile}, {args.trials} held-out trials per point, baseline decoder")
    print("recovery rate (share of trials where the file came back exactly)\n")
    header = f"{'codec':30s}" + "".join(f"{c:>7.0f}" for c in coverages)
    print(header)
    table: dict[str, dict[float, float]] = {}
    for name, settings in VARIANT_SETS[args.variant_set].items():
        row, table[name] = f"{name:30s}", {}
        for coverage in coverages:
            channel = dataclasses.replace(profile, coverage_mean=coverage)
            metrics = recovery_trials(data, settings, None, decoder, channel, seeds, workers=args.workers)
            rate = float(metrics.recovery_rate or 0.0)
            table[name][coverage] = rate
            row += f"{rate:7.3f}"
        print(row, flush=True)

    path = RESULTS_DIR / f"rule_recovery_curve_{args.variant_set}_{args.profile}.json"
    path.write_text(json.dumps({"trials": args.trials, "profile": args.profile, "set": args.variant_set, "table": table}, indent=2) + "\n")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
