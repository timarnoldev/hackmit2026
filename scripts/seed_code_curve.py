"""Does encoding the seed so it satisfies the rules by construction buy anything?

The open thread in docs/LEARNED_RULES.md: screening droplet candidates against the sequence
rules also screens their seeds, which thins the erasure code. Two ways out were named there.
Applying the rules to the payload only was measured and is worse (0.713 against 0.807 at 12
reads). This script measures the other one: a rank/unrank over the seed_bases-mers that satisfy
the rules, so every seed index spells a legal seed block and no droplet is ever rejected for its
seed, while the payload keeps the full benefit of the rules.

Recovery curves, not thresholds (see docs/LEARNED_RULES.md for why):

    uv run python scripts/seed_code_curve.py --profile nanopore_budget --trials 300
"""

from __future__ import annotations

import argparse
import dataclasses
import json

from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.encoder import SeedCodedSettings
from dnacodec.evaluate import recovery_trials
from dnacodec.profiles import load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import heldout_seeds
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

VARIANTS = {
    "rules on, seed screened (standard)": EncoderSettings(),
    "rules on, payload only": EncoderSettings(constrain_seed=False),
    "rules on, constraint-satisfying seeds": SeedCodedSettings(),
    "rules off": EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--coverages", default="10,11,12,13,14,15,16,18,20")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    coverages = [float(c) for c in args.coverages.split(",")]
    seeds = heldout_seeds(args.trials)
    data, decoder = test_file(), MajorityVoteDecoder()

    print(f"{args.profile}, {args.trials} held-out trials per point, baseline decoder")
    print("recovery rate (share of trials where the 20 KB file came back exactly)\n")
    print(f"{'codec':38s}" + "".join(f"{c:>7.0f}" for c in coverages))
    table: dict[str, dict[float, float]] = {}
    for name, settings in VARIANTS.items():
        row, table[name] = f"{name:38s}", {}
        for coverage in coverages:
            channel = dataclasses.replace(profile, coverage_mean=coverage)
            metrics = recovery_trials(
                data, settings, None, decoder, channel, seeds, workers=args.workers
            )
            rate = float(metrics.recovery_rate or 0.0)
            table[name][coverage] = rate
            row += f"{rate:7.3f}"
        print(row, flush=True)

    path = RESULTS_DIR / f"seed_code_curve{args.tag}_{args.profile}.json"
    path.write_text(
        json.dumps(
            {"trials": args.trials, "profile": args.profile, "table": table}, indent=2
        )
        + "\n"
    )
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
