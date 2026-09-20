"""Our decoder under TReconLM's protocol: exactly-N subclusters, their table's shape.

    # reproduce their published Table 7 on their own test split (harness check)
    uv run python scripts/eval_treconlm_protocol.py --split their_test \
        --treconlm-dir ~/TReconLM --model ~/treconlm_models/finetuned_microsoft_dna_len110.pt

    # our decoders under their protocol, on clusters we never trained on
    uv run python scripts/eval_treconlm_protocol.py --split our_heldout \
        --polish-checkpoint checkpoints/polish/polish.pt --skip-treconlm

scripts/eval_treconlm.py runs their model under our protocol. This script runs the other
direction: it builds test examples exactly as their paper does and reports failure rate per
subcluster size, the shape of their Table 7.

Their procedure (data/microsoft_data/microsoft_data.ipynb, section 3, and
src/utils/helper_functions.create_subclusters), which we import from their repo rather than
reimplement:

  - shuffle the reads of each cluster
  - a cluster of 2 to 10 reads becomes one example of that size
  - a cluster of more than 10 reads is cut into consecutive pieces of a size drawn uniformly
    from 2..10, stopping when fewer than two reads remain; the leftover 0 or 1 reads is
    discarded
  - empty clusters and single-read clusters produce no example at all
  - results are then binned by the number of reads in the example, so every column is an
    exactly-N column

--split their_test replays their global random stream (random.seed(42), then train, then
val, then test) so the examples are the ones their paper scored. Their fine-tuned model
never trained on these, but our polisher did train on about 80% of them, so we report only
their model there. --split our_heldout builds the same kind of examples from our held-out
clusters, which is the clean set for our decoders.

Their code is NOT vendored here; clone it and pass --treconlm-dir.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import realdata  # noqa: E402
from dnacodec.results import RESULTS_DIR  # noqa: E402

from scripts.eval_treconlm import (  # noqa: E402
    STRAND_LENGTH,
    TReconLMDecoder,
    our_decoders,
    treconlm_split_indices,
    wilson_interval,
)

THEIR_SEED = 42  # data/microsoft_data/microsoft_data.ipynb, section 3


def build_examples(split: str, treconlm_dir: Path, extra_paths: list[Path]):
    """(references, clusters) as TReconLM's create_subclusters produces them."""
    for p in [treconlm_dir, *extra_paths]:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from src.utils.helper_functions import create_subclusters

    data = realdata.load_microsoft("all")
    clusters = [c.reads for c in data]
    gts = [c.reference for c in data]

    random.seed(THEIR_SEED)
    if split == "their_test":
        # Replay their notebook's calls in order: the three share one RNG stream.
        from sklearn.model_selection import train_test_split

        indices = list(range(len(clusters)))
        train_i, temp_i = train_test_split(indices, test_size=0.2, random_state=42)
        val_i, test_i = train_test_split(temp_i, test_size=0.5, random_state=42)
        create_subclusters([clusters[i] for i in train_i], [gts[i] for i in train_i],
                           max_reads=None)
        create_subclusters([clusters[i] for i in val_i], [gts[i] for i in val_i])
        examples, sizes = create_subclusters([clusters[i] for i in test_i],
                                             [gts[i] for i in test_i])
    elif split == "our_heldout":
        held = realdata.load_microsoft("heldout")
        examples, sizes = create_subclusters([c.reads for c in held],
                                             [c.reference for c in held])
    else:
        raise SystemExit(f"unknown split {split}")

    refs, reads = [], []
    for example in examples:
        prefix, _, gt = example.rpartition(":")
        refs.append(gt)
        reads.append(prefix.split("|"))
    if [len(r) for r in reads] != list(sizes):
        raise SystemExit("subcluster sizes do not match the parsed examples")
    return refs, reads


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--split", choices=["their_test", "our_heldout"], required=True)
    p.add_argument("--treconlm-dir", type=Path, required=True)
    p.add_argument("--model", type=Path, default=None)
    p.add_argument("--model-label", default=None)
    p.add_argument("--extra-path", type=Path, action="append", default=[])
    p.add_argument("--skip-treconlm", action="store_true")
    p.add_argument("--polish-checkpoint", type=Path, default=None)
    p.add_argument("--variants", default="polish,polish_gain_d3r2")
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--polish-batch-size", type=int, default=256)
    p.add_argument("--device", default=None)
    p.add_argument("--sizes", default="2,4,6,10", help="columns to print")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    refs, clusters = build_examples(args.split, args.treconlm_dir, args.extra_path)
    counts = Counter(len(c) for c in clusters)
    print(f"split {args.split}: {len(clusters)} examples from TReconLM's create_subclusters")
    print("  examples per size: " + " ".join(f"{n}:{counts[n]}" for n in sorted(counts)))

    decoders: list[tuple[str, object]] = []
    if not args.skip_treconlm:
        if args.model is None:
            raise SystemExit("--model is required unless --skip-treconlm")
        tre = TReconLMDecoder(args.treconlm_dir, args.model, args.extra_path,
                              args.device, args.batch_size)
        label = args.model_label or f"treconlm_{args.model.stem}"
        print(f"{label}: {tre.n_params / 1e6:.1f}M parameters, device {tre.device}")
        decoders.append((label, lambda cs: tre.decode(cs, STRAND_LENGTH)))
    if args.polish_checkpoint or args.split == "our_heldout":
        decoders += our_decoders(args.polish_checkpoint, args.device, args.polish_batch_size,
                                 [v for v in args.variants.split(",") if v])

    columns = [int(s) for s in args.sizes.split(",")]
    rows: list[dict] = []
    for name, fn in decoders:
        start = time.perf_counter()
        decoded = list(fn(clusters))
        seconds = time.perf_counter() - start
        by_size: dict[int, list[bool]] = {}
        for ref, guess, cluster in zip(refs, decoded, clusters):
            by_size.setdefault(len(cluster), []).append(guess == ref)
        overall = [ok for hits in by_size.values() for ok in hits]
        for n, hits in sorted(by_size.items()):
            lo, hi = wilson_interval(sum(hits), len(hits))
            rows.append({"decoder": name, "cluster_size": n, "n_examples": len(hits),
                         "success_rate": sum(hits) / len(hits), "ci95_low": lo,
                         "ci95_high": hi, "decode_seconds": seconds})
        lo, hi = wilson_interval(sum(overall), len(overall))
        rows.append({"decoder": name, "cluster_size": "all", "n_examples": len(overall),
                     "success_rate": sum(overall) / len(overall), "ci95_low": lo,
                     "ci95_high": hi, "decode_seconds": seconds})
        print(f"  {name:>34} overall {100 * sum(overall) / len(overall):5.1f}%  {seconds:6.1f}s")

    print(f"\nsuccess rate by subcluster size, TReconLM protocol, split {args.split}")
    print(f"  {'decoder':>34} " + " ".join(f"{n:>8}" for n in columns) + f"{'all':>9}")
    print(f"  {'examples':>34} "
          + " ".join(f"{counts[n]:>8}" for n in columns) + f"{len(clusters):>9}")
    for name, _ in decoders:
        cells = []
        for n in list(columns) + ["all"]:
            r = next((r for r in rows if r["decoder"] == name and r["cluster_size"] == n), None)
            cells.append(f"{100 * r['success_rate']:8.1f}" if r else " " * 8)
        print(f"  {name:>34} " + " ".join(cells))

    out = {
        "dataset": "microsoft_nanopore",
        "split": args.split,
        "protocol": "TReconLM create_subclusters (their repo), random.seed(42); shuffled "
                    "reads, clusters of 2..10 kept whole, larger clusters cut into pieces of "
                    "a uniform size in 2..10 until fewer than two reads remain, leftovers "
                    "discarded; empty and single-read clusters produce no example; a strand "
                    "counts only if all 110 bases match",
        "n_examples": len(clusters),
        "examples_per_size": {str(k): v for k, v in sorted(counts.items())},
        "treconlm_model": str(args.model) if args.model else None,
        "polish_checkpoint": str(args.polish_checkpoint) if args.polish_checkpoint else None,
        "is_mock": False,
        "rows": rows,
    }
    path = args.out or RESULTS_DIR / f"treconlm_protocol_{args.split}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
