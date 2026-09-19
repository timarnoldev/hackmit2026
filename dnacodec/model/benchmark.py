"""Final benchmark of a checkpoint on the real Microsoft held-out split, same protocol as the
baseline in scripts/eval_real.py (same heldout_seeds() subsampling, same budgets, metrics
from dnacodec.evaluate), printed next to the baseline.

    uv run python -m dnacodec.model.benchmark checkpoints/<run>/best.pt

This is a final benchmark: never use its output to choose checkpoints or settings.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .. import realdata
from ..evaluate import evaluate
from ..results import RESULTS_DIR
from ..seeds import heldout_seeds
from .decoder import TransformerDecoder

BUDGETS = [2, 4, 6, 10, 16]  # as scripts/eval_real.py
STRAND_LENGTH = 110
BASELINE_FILE = RESULTS_DIR / "baseline_real_microsoft.json"


def main(argv: list[str] | None = None) -> None:
    from scripts.eval_real import subsample_clusters  # the baseline's exact subsampling

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("checkpoint")
    p.add_argument("--device", default=None)
    p.add_argument("--out", type=Path, default=None, help="JSON output (default: next to checkpoint)")
    args = p.parse_args(argv)

    data = realdata.load_microsoft("heldout")
    references = [c.reference for c in data]
    full = [c.reads for c in data]
    decoder = TransformerDecoder(args.checkpoint, device=args.device)
    baseline = {}
    if BASELINE_FILE.exists():
        baseline = {r["max_reads"]: r for r in json.loads(BASELINE_FILE.read_text())["rows"]}

    rows = []
    settings = [(str(k), k, s) for k, s in zip(BUDGETS, heldout_seeds(len(BUDGETS)))] + [("full", None, None)]
    print(f"Microsoft held-out, {len(data)} clusters, checkpoint {args.checkpoint}")
    print(f"{'max reads':>9} {'transformer':>11} {'baseline':>9} {'tf edit':>8} {'bl edit':>8} {'secs':>5}")
    for label, k, seed in settings:
        clusters = full if k is None else subsample_clusters(full, k, seed)
        t = time.perf_counter()
        decoded = decoder.decode(clusters, STRAND_LENGTH)
        secs = time.perf_counter() - t
        m = evaluate(references, decoded, clusters)
        b = baseline.get(label, {})
        rows.append({"max_reads": label, "subsample_seed": seed, "strand_accuracy": m.strand_accuracy,
                     "mean_edit_distance": m.mean_edit_distance, "reads_per_strand": m.reads_per_strand,
                     "baseline_strand_accuracy": b.get("strand_accuracy"), "decode_seconds": secs,
                     "per_position_error": m.per_position_error})
        bl = f"{b['strand_accuracy']:.4f}" if b else "n/a"
        be = f"{b['mean_edit_distance']:.3f}" if b else "n/a"
        print(f"{label:>9} {m.strand_accuracy:>11.4f} {bl:>9} {m.mean_edit_distance:>8.3f} {be:>8} {secs:>5.1f}")
    if not baseline:
        print(f"(no {BASELINE_FILE}, run scripts/eval_real.py for the baseline column)")

    out = args.out or Path(args.checkpoint).with_suffix(".heldout.json")
    out.write_text(json.dumps({"dataset": "microsoft_nanopore", "split": "heldout",
                               "decoder": decoder.name, "checkpoint": str(args.checkpoint),
                               "checkpoint_info": {k: v for k, v in decoder.checkpoint_info.items()
                                                   if k != "metrics"},
                               "is_mock": False, "rows": rows}, indent=2) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
