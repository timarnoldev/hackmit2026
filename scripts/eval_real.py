"""Baseline on the real Microsoft Nanopore held-out split, at several read budgets.

    uv run python scripts/eval_real.py

Each cluster is subsampled (without replacement, deterministic, seeds from heldout_seeds())
to at most k reads for k in 2, 4, 6, 10, 16. Clusters with fewer than k reads keep all of
them, empty clusters stay empty and count as wrong. The "full" row gives the decoder every
read; the decoder itself caps clusters at 16 reads (evenly spaced, deterministic).

Writes results/baseline_real_microsoft.json. These are the numbers any model must beat.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import realdata  # noqa: E402
from dnacodec.baseline import MAX_READS, MajorityVoteDecoder  # noqa: E402
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.results import RESULTS_DIR  # noqa: E402
from dnacodec.seeds import heldout_seeds  # noqa: E402

BUDGETS = [2, 4, 6, 10, 16]
STRAND_LENGTH = 110


def subsample_clusters(clusters: list[list[str]], k: int, seed: int) -> list[list[str]]:
    rng = np.random.default_rng(seed)
    out = []
    for reads in clusters:
        if len(reads) <= k:
            out.append(list(reads))
        else:
            idx = np.sort(rng.choice(len(reads), size=k, replace=False))
            out.append([reads[i] for i in idx])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "baseline_real_microsoft.json")
    args = parser.parse_args()

    data = realdata.load_microsoft("heldout")
    references = [c.reference for c in data]
    full = [c.reads for c in data]
    lengths = {len(r) for r in references}
    if lengths != {STRAND_LENGTH}:
        raise SystemExit(f"unexpected reference lengths {sorted(lengths)}")

    decoder = MajorityVoteDecoder()
    seeds = heldout_seeds(len(BUDGETS))
    settings: list[tuple[str, int | None, int | None]] = [
        (str(k), k, s) for k, s in zip(BUDGETS, seeds)
    ] + [("full", None, None)]

    rows = []
    for label, k, seed in settings:
        clusters = full if k is None else subsample_clusters(full, k, seed)
        start = time.perf_counter()
        decoded = decoder.decode(clusters, STRAND_LENGTH)
        seconds = time.perf_counter() - start
        m = evaluate(references, decoded, clusters)
        rows.append(
            {
                "max_reads": label,
                "subsample_seed": seed,
                "reads_per_strand": m.reads_per_strand,
                "strand_accuracy": m.strand_accuracy,
                "mean_edit_distance": m.mean_edit_distance,
                "dropout_rate": m.dropout_rate,
                "n_strands": m.n_strands,
                "decode_seconds": seconds,
                "per_position_error": m.per_position_error,
            }
        )

    print(f"Microsoft Nanopore, held-out split, {len(data)} clusters, decoder={decoder.name}")
    print(f"{'max reads':>9} {'reads/strand':>12} {'strand_acc':>10} {'mean_edit':>9} {'secs':>6}")
    for r in rows:
        print(
            f"{r['max_reads']:>9} {r['reads_per_strand']:>12.2f} {r['strand_accuracy']:>10.4f}"
            f" {r['mean_edit_distance']:>9.3f} {r['decode_seconds']:>6.1f}"
        )

    result = {
        "dataset": "microsoft_nanopore",
        "split": "heldout",
        "decoder": decoder.name,
        "decoder_max_reads": MAX_READS,
        "strand_length": STRAND_LENGTH,
        "n_clusters": len(data),
        "subsampling": "per cluster, without replacement, np.random.default_rng(seed); "
        "clusters with <= k reads keep all reads; 'full' passes all reads and the decoder "
        f"caps at {MAX_READS} evenly spaced reads",
        "is_mock": False,
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
