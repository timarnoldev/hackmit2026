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
import zlib

import numpy as np

from dnacodec.encoder import (
    FILE_CRC_BYTES,
    SeedCodedSettings,
    _chunks_from_bytes,
    _homopolymer_re,
    _Layout,
    _neighbors,
    _passes,
    encode,
    seed_code_bits_lost,
    seed_code_size,
)
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


def _acceptance_by_degree(data: bytes, n_candidates: int) -> dict:
    """Share of candidate droplets that pass the standard rules, bucketed by droplet degree.

    Walks the encoder's own seed stream and builds the real strand for each seed, so this is
    exactly the screening encode() does, just without stopping at the first acceptance.
    """
    settings = EncoderSettings()
    layout = _Layout(settings)
    n_chunks = layout.n_chunks(len(data))
    blob = zlib.crc32(data).to_bytes(FILE_CRC_BYTES, "big") + data
    chunks = _chunks_from_bytes(blob, layout, n_chunks)
    homo = _homopolymer_re(settings)
    seen: dict[int, list[int]] = {}
    for counter in range(n_candidates):
        seed = layout.seed_from_counter(counter)
        nbrs = _neighbors(seed, n_chunks)
        value = 0
        for i in nbrs:
            value ^= chunks[i]
        row = seen.setdefault(len(nbrs), [0, 0])
        row[0] += 1
        row[1] += _passes(layout.to_strand(seed, value), settings, homo)
    buckets = {
        "1": [0, 0], "2-3": [0, 0], "4-8": [0, 0], "9-20": [0, 0], "21+": [0, 0],
    }
    for degree, (n, ok) in seen.items():
        key = (
            "1" if degree == 1
            else "2-3" if degree <= 3
            else "4-8" if degree <= 8
            else "9-20" if degree <= 20
            else "21+"
        )
        buckets[key][0] += n
        buckets[key][1] += ok
    total = sum(n for n, _ in buckets.values()), sum(ok for _, ok in buckets.values())
    return {
        "n_candidates": n_candidates,
        "overall": total[1] / total[0],
        "by_degree": {k: {"n": n, "accepted": ok, "rate": ok / n} for k, (n, ok) in buckets.items() if n},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=500)
    ap.add_argument("--candidates", type=int, default=200_000)
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

    # The mechanism: does screening a candidate reject some droplet degrees more than others?
    # If acceptance is flat in degree, screening cannot bias the degree distribution at all, and
    # any gap between the codecs above is the sampling noise measured in the null.
    acceptance = _acceptance_by_degree(data, args.candidates)

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
        f"\nhow often a candidate droplet passes the standard rules, by degree "
        f"({acceptance['n_candidates']:,} candidates, overall {acceptance['overall']:.4f}):"
    )
    for key, row in acceptance["by_degree"].items():
        print(f"  degree {key:>5s}  n={row['n']:7,d}  accepted {row['rate']:.4f}")
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
                "acceptance_by_degree": acceptance,
                "seed_code": seed_code,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
