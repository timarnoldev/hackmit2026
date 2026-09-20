"""What does the polisher's self-confidence cost, next to a plain decode?

    uv run python scripts/time_confidence.py --polish checkpoints/polish/polish.pt

The whole distillation question turns on this ratio. A distilled label carries more
information per simulation than a 0/1 failure, but the second polisher pass it needs is not
free, and the comparison that matters is at equal wall clock, not at equal K. Both paths run
here in one process, alternating, so they see the same machine load.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import joint, risk  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", default="nanopore_budget")
    p.add_argument("--polish", default="checkpoints/polish/polish.pt")
    p.add_argument("--strands", type=int, default=2000)
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--workers", type=int, default=7)
    p.add_argument("--length", type=int, default=110)
    p.add_argument("--device", default=None)
    args = p.parse_args()

    from dnacodec.model.net import pick_device
    from dnacodec.model.polish import _FeaturePool, load_checkpoint, polish_clusters

    device = pick_device(args.device)
    model, ckpt = load_checkpoint(args.polish, device)
    thresholds = ckpt.get("thresholds")
    profile = load_profile(args.profile)

    strands = risk.generate_strands(args.strands, args.length, seed=train_seed(555))
    simulate = joint.get_simulate("A")
    repeated = [s for s in strands for _ in range(args.k)]
    t = time.time()
    clusters = simulate(repeated, profile, train_seed(556))
    sim_seconds = time.time() - t
    n = len(clusters)

    pool = _FeaturePool(args.workers)
    rescore = joint._RescorePool(args.workers)
    plain: list[float] = []
    conf: list[float] = []
    try:
        for r in range(args.repeats + 1):  # first round warms the pools up and is dropped
            t = time.time()
            polish_clusters(model, clusters, args.length, 512, device, thresholds, pool)
            a = time.time() - t
            t = time.time()
            joint.polish_with_confidence(
                model, clusters, args.length, 512, device, thresholds, pool, rescore, dense=True
            )
            b = time.time() - t
            if r:
                plain.append(a)
                conf.append(b)
            print(f"round {r}: plain {a:.2f}s, with confidence {b:.2f}s", flush=True)
    finally:
        rescore.close()
        pool.close()

    pa, pb = float(np.mean(plain)), float(np.mean(conf))
    print(f"\n{n} clusters, {args.workers} feature workers, {device}")
    print(f"simulate            {sim_seconds:6.2f}s  ({n / sim_seconds:8.0f} clusters/s)")
    print(f"plain decode        {pa:6.2f}s  ({n / pa:8.0f} clusters/s)")
    print(f"decode + confidence {pb:6.2f}s  ({n / pb:8.0f} clusters/s)")
    print(f"confidence overhead x{pb / pa:.2f} on the decode, "
          f"x{(sim_seconds + pb) / (sim_seconds + pa):.2f} on simulate + decode")


if __name__ == "__main__":
    main()
