"""What screening seeds does to the structure of the fountain code, with an error bar.

docs/LEARNED_RULES.md reports mean droplet degree 9.83 with the rules against 11.97 without, and
worst-covered chunk in 3 droplets against 6. Those are two single draws of 1,239 seeds from a
robust soliton distribution whose tail reaches degree 953, so before reading anything into the
gap this script also measures how much those statistics move when nothing at all is different:
the sampling distribution of the same statistics over independent sets of 1,239 random seeds.

    uv run python scripts/seed_code_structure.py [--replicates 500]

Codecs compared: the standard one (rules on the whole strand, seed included), the payload-only
variant, the constraint-satisfying seed encoding, and the rules-off codec.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from dnacodec.encoder import _Layout, _neighbors, encode, seed_code_bits_lost, seed_code_size
from dnacodec.encoder import SeedCodedSettings
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import train_seed
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

VARIANTS = {
    "rules on, seed screened (standard)": EncoderSettings(),
    "rules on, payload only": EncoderSettings(constrain_seed=False),
    "rules on, constraint-satisfying seeds": SeedCodedSettings(),
    "rules off": EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None),
}


def _structure(seeds: list[int], n_chunks: int) -> dict[str, float]:
    coverage = np.zeros(n_chunks, dtype=np.int64)
    degrees = np.empty(len(seeds), dtype=np.int64)
    for i, seed in enumerate(seeds):
        nbrs = _neighbors(seed, n_chunks)
        degrees[i] = len(nbrs)
        coverage[nbrs] += 1
    return {
        "mean_degree": float(degrees.mean()),
        "min_coverage": int(coverage.min()),
        "mean_coverage": float(coverage.mean()),
        "degree_1_strands": int((degrees == 1).sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=500)
    args = ap.parse_args()

    data = test_file()
    rows: dict[str, dict] = {}
    n_chunks = n_strands = 0
    for name, settings in VARIANTS.items():
        enc = encode(data, settings)
        layout = _Layout(settings)
        seeds = [layout.parse(s)[0] for s in enc.strands]  # type: ignore[index]
        n_chunks, n_strands = enc.meta.n_chunks, len(enc.strands)
        rows[name] = _structure(seeds, n_chunks)
        rows[name]["seed_space"] = layout.seed_space

    # The null: draw the same number of seeds at random, nothing else different.
    rng = np.random.default_rng(train_seed(701))
    null = [
        _structure(rng.integers(0, 1 << 32, n_strands).tolist(), n_chunks)
        for _ in range(args.replicates)
    ]
    degrees = np.array([r["mean_degree"] for r in null])
    mincov = np.array([r["min_coverage"] for r in null])
    spread = {
        "replicates": args.replicates,
        "mean_degree_mean": float(degrees.mean()),
        "mean_degree_sd": float(degrees.std(ddof=1)),
        "mean_degree_p05": float(np.percentile(degrees, 5)),
        "mean_degree_p95": float(np.percentile(degrees, 95)),
        "min_coverage_mean": float(mincov.mean()),
        "min_coverage_sd": float(mincov.std(ddof=1)),
        "min_coverage_p05": float(np.percentile(mincov, 5)),
        "min_coverage_p95": float(np.percentile(mincov, 95)),
    }

    coded = SeedCodedSettings()
    seed_code = {
        "valid_blocks": seed_code_size(coded),
        "plain_blocks": 4**coded.seed_bases,
        "bits_lost": seed_code_bits_lost(coded),
    }

    print(f"{n_strands} droplets over {n_chunks} data chunks, 20 KB test file\n")
    print(f"{'codec':40s}{'mean degree':>13s}{'min coverage':>14s}{'seed space':>13s}")
    for name, r in rows.items():
        print(
            f"{name:40s}{r['mean_degree']:13.3f}{r['min_coverage']:14d}   "
            f"{r['seed_space']:13,d}"
        )
    print(
        f"\nnull, {args.replicates} independent sets of {n_strands} random seeds:\n"
        f"  mean degree  {spread['mean_degree_mean']:.3f} +- {spread['mean_degree_sd']:.3f} "
        f"(5-95%: {spread['mean_degree_p05']:.2f} to {spread['mean_degree_p95']:.2f})\n"
        f"  min coverage {spread['min_coverage_mean']:.2f} +- {spread['min_coverage_sd']:.2f} "
        f"(5-95%: {spread['min_coverage_p05']:.0f} to {spread['min_coverage_p95']:.0f})"
    )
    print(
        f"\nseed code: {seed_code['valid_blocks']:,} valid {coded.seed_bases}-mers of "
        f"{seed_code['plain_blocks']:,}, {seed_code['bits_lost']:.2f} bits of seed space given up"
    )

    path = RESULTS_DIR / "seed_code_structure.json"
    path.write_text(
        json.dumps(
            {
                "n_strands": n_strands,
                "n_chunks": n_chunks,
                "codecs": rows,
                "null": spread,
                "seed_code": seed_code,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
