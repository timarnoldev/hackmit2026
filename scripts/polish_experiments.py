"""Decoder experiments for the learned polisher: iterative rounds, several drafts, edit rules.

    # tune the "gain" margins on the validation carve of the TRAIN split
    uv run python scripts/polish_experiments.py tune --checkpoint checkpoints/polish/polish.pt

    # compare variants (train split for tuning decisions, heldout only for final numbers)
    uv run python scripts/polish_experiments.py eval --split train --variants polish,polish_r2
    uv run python scripts/polish_experiments.py eval --split heldout --variants all

Subsampling follows scripts/eval_real.py exactly: per cluster, without replacement, seeds
from heldout_seeds() for the held-out split (train uses train_seed() instead), clusters with
fewer reads keep all of them, empty clusters count as wrong. Accuracy always comes from
dnacodec.evaluate.evaluate, and every row reports the decode speed in clusters per second.
"""

from __future__ import annotations

import argparse
import itertools
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
from dnacodec.model.polish import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    apply_edits,
    load_checkpoint,
    polish_clusters_multi,
    predict_probs,
)
from dnacodec.results import RESULTS_DIR  # noqa: E402
from dnacodec.seeds import heldout_seeds, train_seed  # noqa: E402

BUDGETS = [2, 4, 6, 10, 16]
STRAND_LENGTH = 110

# name -> kwargs for polish_clusters_multi. "thresholds" overrides the checkpoint's.
VARIANTS: dict[str, dict] = {
    "baseline": {},  # the majority vote decoder, no model
    "polish": dict(rounds=1, drafts=1, mode="topk"),
    "polish_r2": dict(rounds=2, drafts=1, mode="topk"),
    "polish_r3": dict(rounds=3, drafts=1, mode="topk"),
    "polish_d3_conf": dict(rounds=1, drafts=3, select="confidence", mode="topk"),
    "polish_d3_agree": dict(rounds=1, drafts=3, select="agree", mode="topk"),
    "polish_d3_reads": dict(rounds=1, drafts=3, select="reads", mode="topk"),
    "polish_d5_conf": dict(rounds=1, drafts=5, select="confidence", mode="topk"),
    "polish_gain": dict(rounds=1, drafts=1, mode="gain"),
    "polish_gain_d3": dict(rounds=1, drafts=3, select="confidence", mode="gain"),
    "polish_gain_r2": dict(rounds=2, drafts=1, mode="gain"),
    "polish_d2_conf": dict(rounds=1, drafts=2, select="confidence", mode="topk"),
    "polish_gain_d2": dict(rounds=1, drafts=2, select="confidence", mode="gain"),
    "polish_gain_d5": dict(rounds=1, drafts=5, select="confidence", mode="gain"),
    "polish_gain_d3r2": dict(rounds=2, drafts=3, select="confidence", mode="gain"),
    "polish_gain_d3_agree": dict(rounds=1, drafts=3, select="agree", mode="gain"),
    "polish_gain_d3r3": dict(rounds=3, drafts=3, select="confidence", mode="gain"),
    "polish_gain_d5r2": dict(rounds=2, drafts=5, select="confidence", mode="gain"),
    "polish_gain_d8r2": dict(rounds=2, drafts=8, select="confidence", mode="gain"),
    "polish_gain_d3r2_agree": dict(rounds=2, drafts=3, select="agree", mode="gain"),
    "polish_gain_d3r2_reads": dict(rounds=2, drafts=3, select="reads", mode="gain"),
    # escalate: the extra drafts only for clusters the first one is unsure about
    "polish_gain_esc": dict(rounds=2, drafts=3, select="confidence", mode="gain",
                            escalate=-0.001),
    "polish_gain_esc5": dict(rounds=2, drafts=3, select="confidence", mode="gain",
                             escalate=-0.005),
    "polish_best": dict(rounds=2, drafts=3, select="confidence", mode="topk"),
}
# margins in nats for mode="gain", tuned by the `tune` subcommand on the train val carve
GAIN_THRESHOLDS = {"low_max_reads": 3, "low": (0.5, -0.5), "high": (0.0, -1.0)}


def subsample_clusters(clusters: list[list[str]], k: int, seed: int) -> list[list[str]]:
    """Identical to scripts/eval_real.py."""
    rng = np.random.default_rng(seed)
    out = []
    for reads in clusters:
        if len(reads) <= k:
            out.append(list(reads))
        else:
            idx = np.sort(rng.choice(len(reads), size=k, replace=False))
            out.append([reads[i] for i in idx])
    return out


def load_split(split: str, limit: int | None, val_size: int = 500):
    """"val" is the validation carve of the train split, the only data tuned on here."""
    data = realdata.load_microsoft("train" if split == "val" else split)
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    if split == "val":
        _, (refs, clusters) = split_train_val(refs, clusters, val_size, seed=0)
    if limit:
        refs, clusters = refs[:limit], clusters[:limit]
    return refs, clusters


def budget_seeds(split: str, rep: int = 0) -> list[int]:
    """The held-out seeds of scripts/eval_real.py; train and val get train seeds instead.

    rep > 0 draws further independent subsamples, for a tighter comparison on tuning data.
    """
    if split == "heldout":
        if rep:
            raise SystemExit("repeats are for tuning data, not for the held-out split")
        return list(heldout_seeds(len(BUDGETS)))
    return [train_seed(7_000 + 100 * rep + i) for i in range(len(BUDGETS))]


def run_variant(name: str, model, device, references, clusters_by_budget, thresholds, batch_size,
                gain_thresholds=GAIN_THRESHOLDS, escalate=None):
    rows = []
    kwargs = dict(VARIANTS[name])
    if escalate is not None and kwargs.get("drafts", 1) > 1:
        kwargs["escalate"] = escalate
    for budget, clusters in clusters_by_budget:
        start = time.perf_counter()
        if name == "baseline":
            decoded = MajorityVoteDecoder().decode(clusters, STRAND_LENGTH)
        else:
            th = thresholds if kwargs.get("mode") != "gain" else gain_thresholds
            decoded = polish_clusters_multi(
                model, clusters, STRAND_LENGTH, batch_size, device, th, **kwargs
            )
        seconds = time.perf_counter() - start
        m = evaluate(references, decoded, clusters)
        rows.append(
            {
                "variant": name,
                "max_reads": budget,
                "strand_accuracy": float(m.strand_accuracy),
                "mean_edit_distance": float(m.mean_edit_distance),
                "reads_per_strand": float(m.reads_per_strand),
                "decode_seconds": seconds,
                "clusters_per_second": len(clusters) / seconds,
            }
        )
        print(f"  {name:>16} {str(budget):>5} reads  acc {m.strand_accuracy:.4f}  "
              f"{len(clusters) / seconds:7.0f} clusters/s", flush=True)
    return rows


def cmd_eval(args) -> None:
    references, full = load_split(args.split, args.limit, args.val_size)
    lengths = {len(r) for r in references}
    if lengths != {STRAND_LENGTH}:
        raise SystemExit(f"unexpected reference lengths {sorted(lengths)}")
    budgets = [int(b) for b in args.budgets.split(",")] if args.budgets else BUDGETS
    clusters_by_budget = []
    for rep in range(args.repeats):
        seeds = budget_seeds(args.split, rep)
        for k, s in zip(BUDGETS, seeds):
            if k in budgets:
                clusters_by_budget.append((k, subsample_clusters(full, k, s)))
    if args.full:
        clusters_by_budget.append(("full", [list(c) for c in full]))

    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    model, ckpt = load_checkpoint(args.checkpoint, device)
    thresholds = {**DEFAULT_THRESHOLDS, **(ckpt.get("thresholds") or {})}
    names = list(VARIANTS) if args.variants == "all" else args.variants.split(",")
    gain_thresholds = dict(GAIN_THRESHOLDS)
    for key in ("low", "high"):
        value = getattr(args, f"gain_{key}")
        if value:
            gain_thresholds[key] = tuple(float(x) for x in value.split(","))
    print(f"{args.split} split, {len(references)} clusters, checkpoint {args.checkpoint}, "
          f"thresholds {thresholds}, gain {gain_thresholds}")
    rows: list[dict] = []
    for name in names:
        rows += run_variant(name, model, device, references, clusters_by_budget, thresholds,
                            args.batch_size, gain_thresholds, args.escalate)
    print()
    labels = list(dict.fromkeys(b for b, _ in clusters_by_budget))
    print(f"{'variant':>20} " + " ".join(f"{str(b):>7}" for b in labels) + "   cl/s")
    for name in names:
        mine = [r for r in rows if r["variant"] == name]
        accs = " ".join(
            f"{np.mean([r['strand_accuracy'] for r in mine if r['max_reads'] == b]):7.3f}"
            for b in labels
        )
        speed = np.mean([r["clusters_per_second"] for r in mine])
        print(f"{name:>20} {accs} {speed:7.0f}")
    out = {
        "dataset": "microsoft_nanopore",
        "split": args.split,
        "checkpoint": str(args.checkpoint),
        "checkpoint_info": {k: ckpt.get(k) for k in ("step",) if k in ckpt},
        "n_clusters": len(references),
        "thresholds": thresholds,
        "gain_thresholds": gain_thresholds,
        "variants": {k: v for k, v in VARIANTS.items() if k in names},
        "is_mock": False,
        "rows": rows,
    }
    path = Path(args.out or RESULTS_DIR / f"polish_variants_{args.split}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")


def cmd_tune(args) -> None:
    """Sweep the gain-mode margins on the validation carve of the TRAIN split."""
    data = realdata.load_microsoft("train")
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    _, val = split_train_val(refs, clusters, args.val_size, seed=0)
    val_refs, val_clusters = val

    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    model, ckpt = load_checkpoint(args.checkpoint, device)
    cached = []
    for k in BUDGETS:
        cut = [[r for r in c if r][:k] for c in val_clusters]
        cached.append((cut, predict_probs(model, cut, STRAND_LENGTH, device=device)))

    def score(low: tuple, high: tuple, low_max: int = 3) -> tuple[float, list[float]]:
        accs = []
        for cut, probs in cached:
            decoded: list[str | None] = [None] * len(val_refs)
            for i, draft, op_p, ins_p, size in probs:
                s, t = low if size <= low_max else high
                decoded[i] = apply_edits(draft, op_p, ins_p, STRAND_LENGTH, s, t, "gain")
            accs.append(float(evaluate(val_refs, decoded, cut).strand_accuracy))
        return float(np.mean(accs)), accs

    grid = [float(x) for x in args.grid.split(",")]
    base_th = {**DEFAULT_THRESHOLDS, **(ckpt.get("thresholds") or {})}
    ref_accs = []
    for cut, probs in cached:
        decoded = [None] * len(val_refs)
        for i, draft, op_p, ins_p, size in probs:
            s, t = base_th["low"] if size <= base_th["low_max_reads"] else base_th["high"]
            decoded[i] = apply_edits(draft, op_p, ins_p, STRAND_LENGTH, s, t, "topk")
        ref_accs.append(float(evaluate(val_refs, decoded, cut).strand_accuracy))
    print(f"topk reference (thresholds {base_th}): mean {np.mean(ref_accs):.4f} "
          + " ".join(f"{k}r={a:.3f}" for k, a in zip(BUDGETS, ref_accs)))

    best = {"low": (0.0, 0.0), "high": (0.0, 0.0)}
    for regime in ("low", "high"):
        results = {}
        for s, t in itertools.product(grid, grid):
            cand = {**best, regime: (s, t)}
            results[(s, t)], _ = score(cand["low"], cand["high"])
        top = max(results, key=results.get)
        best[regime] = top
        rank = sorted(results.items(), key=lambda kv: -kv[1])[:5]
        print(f"gain {regime}: best {top} mean {results[top]:.4f}; top5 "
              + ", ".join(f"{k}={v:.4f}" for k, v in rank))
    mean, accs = score(best["low"], best["high"])
    print(f"gain margins {best}: mean {mean:.4f} "
          + " ".join(f"{k}r={a:.3f}" for k, a in zip(BUDGETS, accs)))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--checkpoint", default="checkpoints/polish/polish.pt")
    p.add_argument("--device", default=None)
    p.add_argument("--batch-size", type=int, default=512)
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eval", help="accuracy and speed of the variants on one split")
    e.add_argument("--split", default="val", choices=["val", "train", "heldout"])
    e.add_argument("--variants", default="baseline,polish")
    e.add_argument("--limit", type=int, default=None, help="use only the first N clusters")
    e.add_argument("--val-size", type=int, default=500)
    e.add_argument("--repeats", type=int, default=1, help="independent subsamples (tuning data)")
    e.add_argument("--budgets", default=None, help="comma separated subset of the read budgets")
    e.add_argument("--full", action="store_true", help="also run the unlimited-reads row")
    e.add_argument("--escalate", type=float, default=None,
                   help="override: only try the other drafts below this confidence")
    e.add_argument("--gain-low", default=None, help="sub,indel margins for clusters <= 3 reads")
    e.add_argument("--gain-high", default=None, help="sub,indel margins for larger clusters")
    e.add_argument("--out", default=None)
    e.set_defaults(func=cmd_eval)

    t = sub.add_parser("tune", help="sweep the gain-mode margins on the train validation carve")
    t.add_argument("--val-size", type=int, default=500)
    t.add_argument("--grid", default="-1.0,-0.5,0.0,0.5,1.0,2.0")
    t.set_defaults(func=cmd_tune)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
