"""Does screening the seed against the sequence rules cost reads? Measured, not argued.

    uv run python scripts/seed_constraint_test.py [--trials 300]

Three codecs, same file, same channel, same decoder: the standard one (rules applied to the
whole strand including the seed), the same rules applied to the payload only, and no rules.
"""
from __future__ import annotations

import argparse

from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.evaluate import min_reads_at_target
from dnacodec.profiles import load_profile
from dnacodec.seeds import heldout_seeds
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

VARIANTS = {
    "rules on, seed screened too (standard)": EncoderSettings(),
    "rules on, payload only": EncoderSettings(constrain_seed=False),
    "rules off": EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    profile = load_profile(args.profile)
    seeds = heldout_seeds(args.trials)
    data, decoder = test_file(), MajorityVoteDecoder()
    print(f"{args.profile}, {args.trials} held-out trials, baseline decoder")
    for name, settings in VARIANTS.items():
        reads = min_reads_at_target(data, settings, None, decoder, profile, seeds, workers=args.workers)
        print(f"  {name:40s} {reads if reads is not None else 'target never met'} reads per strand")


if __name__ == "__main__":
    main()
