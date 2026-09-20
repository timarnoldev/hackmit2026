"""How much of a gap between two codecs at one coverage is just which seeds they drew?

The seed-screening question is argued in droplet degrees, and mean droplet degree over 1,239
droplets is a noisy statistic (see scripts/seed_code_structure.py). This script asks the same
question in the only currency that counts: recovery rate. It encodes several different random
files of the same size with each codec, which gives each codec several independent draws of the
seed set, and measures recovery at one coverage for every draw.

The spread across files is the error bar that belongs on any single-file comparison of two
codecs, on top of the binomial error bar of the trials themselves.

    uv run python scripts/seed_code_spread.py --files 10 --coverage 12 --trials 300

Files are random bytes from train seeds (never the held-out ones); channel trials use held-out
seeds, the same set for every file and codec.
"""

from __future__ import annotations

import argparse
import dataclasses
import json

import numpy as np

from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.encoder import SeedCodedSettings
from dnacodec.evaluate import recovery_trials
from dnacodec.profiles import load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import heldout_seeds, train_seed
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

VARIANTS = {
    "rules on, seed screened (standard)": EncoderSettings(),
    "rules on, payload only": EncoderSettings(constrain_seed=False),
    "rules on, constraint-satisfying seeds": SeedCodedSettings(),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=10)
    ap.add_argument("--start", type=int, default=0, help="index of the first file, to extend a run")
    ap.add_argument(
        "--codecs", default="", help="comma separated substrings; empty means all three"
    )
    ap.add_argument("--coverage", type=float, default=12.0)
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--profile", default="nanopore_budget")
    args = ap.parse_args()

    n_bytes = len(test_file())
    index = list(range(args.start, args.start + args.files))
    files = [
        np.random.default_rng(train_seed(800 + i)).integers(0, 256, n_bytes, dtype=np.uint8).tobytes()
        for i in index
    ]
    wanted = [w.strip() for w in args.codecs.split(",") if w.strip()]
    variants = {
        name: settings
        for name, settings in VARIANTS.items()
        if not wanted or any(w in name for w in wanted)
    }
    channel = dataclasses.replace(load_profile(args.profile), coverage_mean=args.coverage)
    seeds = heldout_seeds(args.trials)
    decoder = MajorityVoteDecoder()

    print(
        f"{args.profile} at {args.coverage:g} reads per strand, {args.trials} held-out trials per "
        f"point, {args.files} different {n_bytes // 1024} KB files (index {index[0]}..{index[-1]})\n"
    )
    print(f"{'codec':38s}" + "".join(f"{i:>7d}" for i in index) + "    mean     sd")
    table: dict[str, list[float]] = {}
    for name, settings in variants.items():
        rates = []
        for data in files:
            metrics = recovery_trials(
                data, settings, None, decoder, channel, seeds, workers=args.workers
            )
            rates.append(float(metrics.recovery_rate or 0.0))
        table[name] = rates
        arr = np.array(rates)
        print(
            f"{name:38s}"
            + "".join(f"{r:7.3f}" for r in rates)
            + f"  {arr.mean():6.3f} {arr.std(ddof=1):6.3f}",
            flush=True,
        )

    tag = "" if args.start == 0 else f"_from{args.start}"
    path = RESULTS_DIR / f"seed_code_spread{tag}_{args.profile}.json"
    path.write_text(
        json.dumps(
            {
                "profile": args.profile,
                "coverage": args.coverage,
                "trials": args.trials,
                "files": args.files,
                "file_index": index,
                "n_bytes": n_bytes,
                "recovery_by_file": table,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
