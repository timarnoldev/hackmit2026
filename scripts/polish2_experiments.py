"""Pick the v2 polisher's inference variant on the validation carve of the TRAIN split.

    uv run --extra train python scripts/polish2_experiments.py \
        --checkpoint checkpoints/polish2/polish2.pt --repeats 3

Same idea as scripts/polish_experiments.py, for dnacodec.model.polish2. Every decision here is
taken on `val`, the 500-cluster carve of the real train split that training also used for the
checkpoint and the thresholds. The held-out split is never read by this script; the winner is
measured on it afterwards by scripts/stable_decoder_numbers.py.
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
from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.model.data import split_train_val  # noqa: E402
from dnacodec.model.polish import DEFAULT_THRESHOLDS  # noqa: E402
from dnacodec.model.polish2 import load_checkpoint, polish2_clusters  # noqa: E402
from dnacodec.results import RESULTS_DIR  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402

BUDGETS = [2, 4, 6, 10, 16]
STRAND_LENGTH = 110

VARIANTS: dict[str, dict] = {
    "baseline": {},
    "v2": dict(rounds=1, drafts=1, mode="topk"),
    "v2_r2": dict(rounds=2, drafts=1, mode="topk"),
    "v2_gain": dict(rounds=1, drafts=1, mode="gain"),
    "v2_gain_r2": dict(rounds=2, drafts=1, mode="gain"),
    "v2_d3": dict(rounds=1, drafts=3, select="confidence", mode="topk"),
    "v2_d3_r2": dict(rounds=2, drafts=3, select="confidence", mode="topk"),
    "v2_gain_d3": dict(rounds=1, drafts=3, select="confidence", mode="gain"),
    "v2_gain_d3r2": dict(rounds=2, drafts=3, select="confidence", mode="gain"),
    "v2_gain_d3r3": dict(rounds=3, drafts=3, select="confidence", mode="gain"),
    "v2_gain_d5r2": dict(rounds=2, drafts=5, select="confidence", mode="gain"),
    "v2_gain_d8r2": dict(rounds=2, drafts=8, select="confidence", mode="gain"),
    "v2_gain_d3r2_agree": dict(rounds=2, drafts=3, select="agree", mode="gain"),
    "v2_gain_d3r2_reads": dict(rounds=2, drafts=3, select="reads", mode="gain"),
}


def subsample_clusters(clusters, k: int, seed: int):
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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--variants", default="all")
    ap.add_argument("--budgets", default="4,6,10")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--val-size", type=int, default=500)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = realdata.load_microsoft("train")
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    _, (refs, clusters) = split_train_val(refs, clusters, args.val_size, seed=0)

    budgets = [int(b) for b in args.budgets.split(",")]
    jobs = []
    for rep in range(args.repeats):
        for i, k in enumerate(BUDGETS):
            if k in budgets:
                jobs.append((k, subsample_clusters(clusters, k, train_seed(9_000 + 100 * rep + i))))

    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    model, ckpt = load_checkpoint(args.checkpoint, device)
    topk_th = {**DEFAULT_THRESHOLDS, **(ckpt.get("thresholds") or {})}
    gain_th = ckpt.get("gain_thresholds") or {"low_max_reads": 3, "low": (0.5, -0.5),
                                              "high": (0.0, -1.0)}
    print(f"val carve, {len(refs)} clusters, {args.checkpoint}")
    print(f"topk thresholds {topk_th} | gain thresholds {gain_th}")

    names = list(VARIANTS) if args.variants == "all" else args.variants.split(",")
    rows = []
    for name in names:
        kwargs = dict(VARIANTS[name])
        for k, cut in jobs:
            start = time.perf_counter()
            if name == "baseline":
                decoded = MajorityVoteDecoder().decode(cut, STRAND_LENGTH)
            else:
                th = gain_th if kwargs.get("mode") == "gain" else topk_th
                decoded = polish2_clusters(model, cut, STRAND_LENGTH, args.batch_size, device,
                                           th, **kwargs)
            seconds = time.perf_counter() - start
            m = evaluate(refs, decoded, cut)
            rows.append({"variant": name, "reads": k, "acc": float(m.strand_accuracy),
                         "clusters_per_second": len(cut) / seconds})
        print(f"  {name:>20} done", flush=True)

    print()
    print(f"{'variant':>22} " + " ".join(f"{b:>7}" for b in budgets) + "    mean    cl/s")
    summary = {}
    for name in names:
        mine = [r for r in rows if r["variant"] == name]
        per = [float(np.mean([r["acc"] for r in mine if r["reads"] == b])) for b in budgets]
        speed = float(np.mean([r["clusters_per_second"] for r in mine]))
        summary[name] = {"per_budget": dict(zip(budgets, per)), "mean": float(np.mean(per)),
                         "clusters_per_second": speed}
        print(f"{name:>22} " + " ".join(f"{p:7.4f}" for p in per)
              + f" {np.mean(per):7.4f} {speed:7.0f}")
    best = max((n for n in names if n != "baseline"), key=lambda n: summary[n]["mean"])
    print(f"\nbest on the train val carve: {best}")
    path = Path(args.out) if args.out else RESULTS_DIR / "polish2_variants_val.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"split": "train_val_carve", "checkpoint": args.checkpoint,
                                "repeats": args.repeats, "summary": summary, "best": best,
                                "rows": rows}, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
