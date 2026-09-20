"""Does distilling the polisher's own confidence replace K = 32 simulations?

    uv run python scripts/run_joint.py --profile nanopore_budget \
        --polish checkpoints/polish/polish.pt --out results/joint/nanopore.json

The control is the sequential pipeline exactly as it is today: freeze the polisher, label
every training strand with the fraction of K = 32 simulated decodes that came out wrong, fit
`risk.RiskModel` on those labels. Every other arm changes only the *labels* (and, for the
dense arms, adds an auxiliary per-position target), never the architecture, the strands, the
seeds or the simulated clusters: `joint.label_with_polisher` records all signals from one
K = 32 pass, so arm k = 2 sees the first two of the very same clusters the control saw.

Measured, per arm, on strands and clusters that never touched training:
  - ranking of the true failure rate (AUC, Spearman) on a mixed held-out set,
  - the same on uniform random strands, which is what the encoder actually chooses between,
  - selection regret: group the uniform strands into slots of C candidates, let the model
    pick one per slot, report the true failure rate of what it picked,
  - all of the above again on Simulator B, which nothing here is ever optimized against.

Held-out seeds and Simulator B are used for evaluation only. Training uses train seeds and
Simulator A.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import joint, risk  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import heldout_seeds, train_seed  # noqa: E402

T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------- metrics


def ranking(y: np.ndarray, score: np.ndarray) -> dict:
    ok = ~np.isnan(y) & ~np.isnan(score)
    y, score = y[ok], score[ok]
    return {
        "auc_gt_0.5": risk.roc_auc(y > 0.5, score),
        "auc_gt_0": risk.roc_auc(y > 0, score),
        "spearman": risk.spearman(y, score),
        "positives_gt_0.5": float(np.mean(y > 0.5)) if len(y) else float("nan"),
        "n": int(len(y)),
    }


def quantiles(a: np.ndarray) -> dict:
    a = a[~np.isnan(a)]
    if not len(a):
        return {}
    q = np.quantile(a, [0.0, 0.1, 0.5, 0.9, 1.0])
    return {"mean": float(a.mean()), "sd": float(a.std()), "q": [round(float(v), 4) for v in q]}


def rank_uniform(a: np.ndarray) -> np.ndarray:
    """Values replaced by their rank scaled into (0, 1). Keeps the order, kills saturation."""
    out = np.full(len(a), np.nan)
    ok = ~np.isnan(a)
    r = risk._rankdata(a[ok])
    out[ok] = (r - 0.5) / max(len(r), 1)
    return out


# ---------------------------------------------------------------- arms


def arms_for(batch: joint.LabelBatch, k_cheap: int, k_full: int) -> dict:
    """label vector (and optional dense target) per arm, all from the one K = k_full pass."""
    fail_cheap = batch.rate("fail", k_cheap)
    post_cheap = batch.rate("surr_post", k_cheap)
    out = {
        f"control_k{k_full}": (batch.rate("fail"), None, k_full),
        f"control_k{k_cheap}": (fail_cheap, None, k_cheap),
        "control_k1": (batch.rate("fail", 1), None, 1),
        f"distill_post_k{k_cheap}": (post_cheap, None, k_cheap),
        "distill_post_k1": (batch.rate("surr_post", 1), None, 1),
        f"distill_post_rank_k{k_cheap}": (rank_uniform(post_cheap), None, k_cheap),
        f"distill_pre_k{k_cheap}": (batch.rate("surr_pre", k_cheap), None, k_cheap),
        "distill_pre_k1": (batch.rate("surr_pre", 1), None, 1),
        f"distill_post_k{k_full}": (batch.rate("surr_post"), None, k_full),
        # the cheap binary label and the distilled one are two noisy views of the same thing
        f"hybrid_k{k_cheap}": (0.5 * fail_cheap + 0.5 * rank_uniform(post_cheap), None, k_cheap),
    }
    dense = batch.dense_target(k_cheap)
    out[f"dense_control_k{k_cheap}"] = (fail_cheap, dense, k_cheap)
    out[f"dense_distill_k{k_cheap}"] = (post_cheap, dense, k_cheap)
    return out


def label_efficiency(batch: joint.LabelBatch, k_full: int, budgets=(1, 2, 4)) -> dict:
    """How much of the truth does a label built from j simulations carry, per signal?

    The truth is the mean failure rate over the simulations the cheap label did NOT see
    (index j onwards), so a cheap binary label is never flattered by sharing clusters with
    its own target. This is the whole distillation question in one table: if the polisher's
    confidence is a lower-variance read of the same quantity, its curve should sit above the
    binary one at the same j.
    """
    out: dict = {}
    for j in budgets:
        if j >= k_full:
            continue
        truth = batch.rate("fail", lo=j)
        ok = ~np.isnan(truth)
        row = {}
        for key in ("fail", "surr_post", "surr_pre"):
            cheap = batch.rate(key, j)
            good = ok & ~np.isnan(cheap)
            row[key] = {
                "spearman": risk.spearman(cheap[good], truth[good]),
                "auc_gt_0.5": risk.roc_auc(truth[good] > 0.5, cheap[good]),
            }
        out[f"k={j}"] = row
    return out


def fit_arm(name, strands, y, dense, seed, device, epochs, verbose=False):
    if dense is None:
        model = risk.RiskModel(seed=seed, device=device, epochs=epochs, verbose=verbose)
        model.fit(strands, y)
    else:
        model = joint.DenseRiskModel(seed=seed, device=device, epochs=epochs, verbose=verbose)
        model.fit_dense(strands, y, dense)
    return model


# ---------------------------------------------------------------- main


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default="nanopore_budget")
    p.add_argument("--polish", default="checkpoints/polish/polish.pt")
    p.add_argument("--out", default="results/joint/report.json")
    p.add_argument("--length", type=int, default=110)
    p.add_argument("--train-strands", type=int, default=6000)
    p.add_argument("--eval-strands", type=int, default=2000)
    p.add_argument("--slots", type=int, default=500)
    p.add_argument("--candidates", type=int, default=8)
    p.add_argument("--k", type=int, default=32, help="simulations per strand for the control")
    p.add_argument("--k-cheap", type=int, default=2, help="simulations the distilled arms may use")
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--seeds", type=int, default=3, help="risk-model training seeds per arm")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--device", default=None)
    p.add_argument("--simulators", default="A,B")
    p.add_argument("--cache", default="checkpoints/joint")
    args = p.parse_args()

    profile = load_profile(args.profile)
    cache = Path(args.cache) / args.profile
    cache.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from dnacodec.model.polish import load_checkpoint
    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    model, ckpt = load_checkpoint(args.polish, device)
    thresholds = ckpt.get("thresholds")
    log(f"polisher {args.polish} on {device}, profile {args.profile}, coverage {profile.coverage_mean}")

    # ---------------- strand sets -------------------------------------------------
    train_strands = risk.generate_strands(args.train_strands, args.length, seed=train_seed(101))
    ho = heldout_seeds()
    eval_mixed = risk.generate_strands(args.eval_strands, args.length, seed=ho[0], heldout=True)
    eval_uniform = risk.generate_strands(
        args.slots * args.candidates, args.length, seed=ho[1], mix={"uniform": 1.0}, heldout=True
    )
    log(f"strands: train {len(train_strands)}, mixed heldout {len(eval_mixed)}, uniform heldout {len(eval_uniform)}")

    def labeled(tag: str, strands, seed: int, sim: str, heldout: bool) -> joint.LabelBatch:
        path = cache / f"labels_{tag}_{sim}_{len(strands)}_{args.k}_{args.length}.npz"
        if path.exists():
            z = np.load(path, allow_pickle=True)
            log(f"cached labels {path.name}")
            return joint.LabelBatch(
                strands=list(z["strands"]),
                fail=z["fail"],
                surr_post=z["surr_post"],
                surr_pre=z["surr_pre"],
                per_pos=z["per_pos"],
                seconds=json.loads(str(z["seconds"])),
            )
        log(f"labeling {tag} on simulator {sim}: {len(strands)} strands x {args.k}")
        batch = joint.label_with_polisher(
            strands, model, profile, k=args.k, seed=seed, sim=sim, thresholds=thresholds,
            device=device, dense_k=max(args.k_cheap, 4), workers=args.workers, heldout=heldout, log=log,
        )
        np.savez_compressed(
            path, strands=np.array(batch.strands), fail=batch.fail, surr_post=batch.surr_post,
            surr_pre=batch.surr_pre, per_pos=batch.per_pos, seconds=json.dumps(batch.seconds),
        )
        log(f"  {batch.seconds['total']:.0f}s total ({batch.seconds['simulate']:.0f}s simulate, "
            f"{batch.seconds['decode']:.0f}s decode+confidence)")
        return batch

    sims = [s.strip().upper() for s in args.simulators.split(",") if s.strip()]

    # Training labels: train seeds, Simulator A only.
    train_batch = labeled("train", train_strands, train_seed(202), "A", heldout=False)
    # Evaluation labels: held-out seeds, both simulators.
    eval_batches = {}
    for sim in sims:
        eval_batches[("mixed", sim)] = labeled("mixed", eval_mixed, ho[2], sim, heldout=True)
        eval_batches[("uniform", sim)] = labeled("uniform", eval_uniform, ho[3], sim, heldout=True)

    # ---------------- label diagnostics -------------------------------------------
    report: dict = {
        "profile": args.profile,
        "polisher": str(args.polish),
        "config": vars(args),
        "label_distributions": {
            f"fail_k{args.k}": quantiles(train_batch.rate("fail")),
            f"fail_k{args.k_cheap}": quantiles(train_batch.rate("fail", args.k_cheap)),
            f"surr_post_k{args.k_cheap}": quantiles(train_batch.rate("surr_post", args.k_cheap)),
            f"surr_pre_k{args.k_cheap}": quantiles(train_batch.rate("surr_pre", args.k_cheap)),
        },
        "label_efficiency": label_efficiency(train_batch, args.k),
        "labeling_cost_seconds": train_batch.seconds,
        "arms": {},
        "heuristics": {},
    }
    for k, v in report["label_distributions"].items():
        log(f"  labels {k}: {v}")
    log("label efficiency (cheap label vs the simulations it did not see):")
    for budget, row in report["label_efficiency"].items():
        log("  " + budget + "  " + "  ".join(
            f"{key}: rho={v['spearman']:.3f} AUC={v['auc_gt_0.5']:.3f}" for key, v in row.items()
        ))

    # ---------------- heuristic baselines (no learning) ---------------------------
    for set_name in ("mixed", "uniform"):
        for sim in sims:
            batch = eval_batches[(set_name, sim)]
            truth = batch.rate("fail")
            entry = {}
            runs = np.array([risk.max_run_length(s) for s in batch.strands], dtype=float)
            entry["longest_run"] = ranking(truth, runs)
            entry["gc_deviation"] = ranking(
                truth, np.array([abs(risk.gc_fraction(s) - 0.5) for s in batch.strands])
            )
            try:
                entry["context_table"] = ranking(truth, risk.context_risk(batch.strands, profile))
            except Exception as exc:  # pragma: no cover - only if a profile has no table
                entry["context_table"] = {"error": str(exc)}
            report["heuristics"][f"{set_name}_sim{sim}"] = entry
            log(f"heuristics {set_name}/sim{sim}: longest_run AUC(>0.5)={entry['longest_run']['auc_gt_0.5']:.3f}")

    # ---------------- arms ---------------------------------------------------------
    arms = arms_for(train_batch, args.k_cheap, args.k)
    for name, (y, dense, k_used) in arms.items():
        log(f"--- arm {name} (K={k_used} simulations per strand) ---")
        per_seed: list[dict] = []
        for s in range(args.seeds):
            t = time.time()
            m = fit_arm(name, train_strands, y, dense, seed=train_seed(300 + s), device=str(device), epochs=args.epochs)
            entry: dict = {"fit_seconds": time.time() - t}
            for set_name in ("mixed", "uniform"):
                for sim in sims:
                    batch = eval_batches[(set_name, sim)]
                    truth = batch.rate("fail")
                    pred = m(batch.strands)
                    entry[f"{set_name}_sim{sim}"] = ranking(truth, pred)
                    if set_name == "uniform":
                        entry[f"selection_sim{sim}"] = joint.selection_regret(
                            pred, truth, args.slots, args.candidates
                        )
            per_seed.append(entry)
        # aggregate over training seeds
        agg: dict = {"k_simulations": k_used, "seeds": args.seeds}
        keys = [k for k in per_seed[0] if k != "fit_seconds"]
        for key in keys:
            agg[key] = {}
            for metric in per_seed[0][key]:
                vals = np.array([e[key][metric] for e in per_seed], dtype=float)
                agg[key][metric] = float(np.nanmean(vals))
                agg[key][metric + "_sd"] = float(np.nanstd(vals))
        agg["fit_seconds"] = float(np.mean([e["fit_seconds"] for e in per_seed]))
        report["arms"][name] = agg
        for sim in sims:
            r = agg[f"uniform_sim{sim}"]
            sel = agg[f"selection_sim{sim}"]
            log(f"  sim{sim} uniform: AUC(>0.5)={r['auc_gt_0.5']:.3f}+-{r['auc_gt_0.5_sd']:.3f} "
                f"Spearman={r['spearman']:.3f}  selection picked={sel['picked']:.4f} "
                f"(oracle {sel['oracle']:.4f}, no selection {sel['mean_all']:.4f})")
            m = agg[f"mixed_sim{sim}"]
            log(f"  sim{sim} mixed:   AUC(>0.5)={m['auc_gt_0.5']:.3f} Spearman={m['spearman']:.3f}")

    out_path.write_text(json.dumps(report, indent=2, default=str))
    log(f"wrote {out_path}")


if __name__ == "__main__":
    main()
