"""How fast is PolishDecoder.decode() with N feature workers?

Measures the CPU part of the polisher, which used to run on one core while the loop's own
worker pool was idle. Two measurements:

  decode: one trial's worth of clusters (default 1239, the number of strands of the default
          codec on the test file), simulated at the profile's coverage, decoded in one call
          like evaluation does. Reported as clusters per second and seconds per trial.
  e2e:    one recovery_trials call over 50 train seeds, the unit the loop spends its time in.

Run on the GPU machine:
    uv run --no-sync python -m scripts.bench_polish_workers --workers 1,4,8,16
    uv run --no-sync python -m scripts.bench_polish_workers --e2e --workers 1,8
"""

from __future__ import annotations

import argparse
import dataclasses
import time

import numpy as np

from dnacodec.model.polish import PolishDecoder
from dnacodec.profiles import load_profile
from dnacodec.simulator import simulate
from dnacodec.types import ALPHABET


def one_trial_clusters(n_strands: int, length: int, profile, seed: int) -> list[list[str]]:
    rng = np.random.default_rng(seed)
    strands = ["".join(ALPHABET[i] for i in rng.integers(0, 4, size=length)) for _ in range(n_strands)]
    return simulate(strands, profile, seed)


def bench_decode(args, profile) -> None:
    clusters = one_trial_clusters(args.clusters, args.length, profile, seed=12345)
    reads = sum(len(c) for c in clusters)
    print(f"{len(clusters)} clusters, {reads} reads, mean {reads / len(clusters):.1f} per cluster")
    print(f"{'workers':>8} {'s/trial':>9} {'clusters/s':>11} {'speedup':>8}")
    base = None
    reference = None
    for workers in args.workers:
        dec = PolishDecoder(args.checkpoint, device=args.device, workers=workers or None)
        try:
            out = dec.decode(clusters, args.length)  # warm up the pool and the GPU
            if reference is None:
                reference = out
            elif out != reference:
                raise SystemExit(f"workers={workers} decoded differently from workers={args.workers[0]}")
            times = []
            for _ in range(args.repeats):
                t0 = time.perf_counter()
                dec.decode(clusters, args.length)
                times.append(time.perf_counter() - t0)
            best = min(times)
        finally:
            dec.close()
        base = base or best
        print(f"{workers:>8} {best:>9.3f} {len(clusters) / best:>11.0f} {base / best:>7.2f}x")


def bench_e2e(args, profile) -> None:
    from dnacodec.encoder import encode
    from dnacodec.evaluate import recovery_trials
    from dnacodec.testfile import test_file
    from dnacodec.types import EncoderSettings

    data = test_file()
    settings = EncoderSettings()
    encoded = encode(data, settings, None)
    seeds = list(range(args.trials))
    print(f"recovery_trials: {len(encoded.strands)} strands, {len(seeds)} trials, "
          f"loop workers={args.loop_workers}")
    print(f"{'workers':>8} {'seconds':>9} {'trials/s':>9}")
    for workers in args.workers:
        dec = PolishDecoder(args.checkpoint, device=args.device, workers=workers or None)
        try:
            t0 = time.perf_counter()
            m = recovery_trials(data, settings, None, dec, profile, seeds,
                                workers=args.loop_workers, encoded=encoded)
            dt = time.perf_counter() - t0
        finally:
            dec.close()
        print(f"{workers:>8} {dt:>9.2f} {len(seeds) / dt:>9.2f}   "
              f"(recovery_rate={m.recovery_rate:.2f}, acc={m.strand_accuracy:.4f})")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", default="checkpoints/polish/polish.pt")
    p.add_argument("--device", default=None)
    p.add_argument("--profile", default="nanopore_budget")
    p.add_argument("--coverage", type=float, default=None)
    p.add_argument("--clusters", type=int, default=1239)
    p.add_argument("--length", type=int, default=110)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--workers", default="1,4,8,16",
                   help="comma separated, 1 means single process")
    p.add_argument("--e2e", action="store_true", help="time recovery_trials instead")
    p.add_argument("--trials", type=int, default=50)
    p.add_argument("--loop-workers", type=int, default=None, help="workers for recovery_trials")
    args = p.parse_args()
    args.workers = [int(w) for w in args.workers.split(",")]
    profile = load_profile(args.profile)
    if args.coverage is not None:
        profile = dataclasses.replace(profile, coverage_mean=args.coverage)
    (bench_e2e if args.e2e else bench_decode)(args, profile)


if __name__ == "__main__":
    main()
