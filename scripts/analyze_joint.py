"""Read a run_joint.py report (and its cached labels) and print the tables for the writeup.

    uv run python scripts/analyze_joint.py --report results/joint/nanopore_k2.json

Two things this adds on top of the report:

1. the arm table as markdown, control first, so the comparison is unavoidable;
2. the decode-time confidence stratified by how many reads the cluster had. Pooled over all
   coverages, "the decoder knows it is wrong" is partly just "the cluster was thin", which is
   the same confound the real-data firewall test found. Inside a read-count bin the number
   means what it says.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import joint, risk  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import heldout_seeds  # noqa: E402

BINS = [(1, 2), (3, 4), (5, 6), (7, 9), (10, 16)]

ROWS = [
    ("control_k32", "control: K=32 failure counts (today's pipeline)"),
    ("control_k16", "control: K=16 failure counts"),
    ("control_k8", "control: K=8 failure counts"),
    ("control_k4", "control: K=4 failure counts"),
    ("control_k2", "control: K=2 failure counts"),
    ("control_k1", "control: K=1 failure count"),
    ("distill_post_k32", "distilled confidence, K=32"),
    ("distill_post_rank_k16", "distilled confidence (rank), K=16"),
    ("distill_post_rank_k8", "distilled confidence (rank), K=8"),
    ("distill_post_rank_k4", "distilled confidence (rank), K=4"),
    ("distill_post_rank_k2", "distilled confidence (rank), K=2"),
    ("distill_post_k2", "distilled confidence (raw), K=2"),
    ("distill_post_k1", "distilled confidence (raw), K=1"),
    ("distill_pre_k2", "distilled confidence, no second pass, K=2"),
    ("distill_pre_k1", "distilled confidence, no second pass, K=1"),
    ("hybrid_k2", "half failure count, half distilled, K=2"),
    ("dense_control_k2", "K=2 counts + per-position teacher"),
    ("dense_distill_k2", "K=2 distilled + per-position teacher"),
]


def fmt(v, n=3):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{n}f}"


def arm_table(report: dict) -> str:
    arms = report["arms"]
    sims = [s for s in ("A", "B") if f"uniform_simA" in next(iter(arms.values()))] or ["A"]
    sims = ["A", "B"] if "uniform_simB" in next(iter(arms.values())) else ["A"]
    head = "| label source | sims/strand | " + " | ".join(
        f"sim{s} AUC | sim{s} rho | sim{s} picked" for s in sims
    ) + " |"
    sep = "|---" * (2 + 3 * len(sims)) + "|"
    lines = [head, sep]
    for key, label in ROWS:
        if key not in arms:
            continue
        a = arms[key]
        cells = [label, str(a["k_simulations"])]
        for s in sims:
            u = a[f"uniform_sim{s}"]
            sel = a[f"selection_sim{s}"]
            cells += [
                f"{fmt(u['auc_gt_0.5'])} ±{fmt(u['auc_gt_0.5_sd'])}",
                fmt(u["spearman"]),
                fmt(sel["picked"], 4),
            ]
        lines.append("| " + " | ".join(cells) + " |")
    ref = next(iter(arms.values()))
    lines.append("")
    for s in sims:
        sel = ref[f"selection_sim{s}"]
        lines.append(
            f"sim{s} reference points: no selection at all {sel['mean_all']:.4f}, "
            f"an oracle that knows the true failure rate {sel['oracle']:.4f}."
        )
    return "\n".join(lines)


def stratified(report: dict, which: str, sim: str) -> str:
    cfg = report["config"]
    profile = load_profile(cfg["profile"])
    cache = Path(cfg["cache"]) / cfg["profile"]
    ho = heldout_seeds()
    if which == "mixed":
        strands = risk.generate_strands(cfg["eval_strands"], cfg["length"], seed=ho[0], heldout=True)
        seed = ho[2]
    else:
        strands = risk.generate_strands(
            cfg["slots"] * cfg["candidates"], cfg["length"], seed=ho[1], mix={"uniform": 1.0}, heldout=True
        )
        seed = ho[3]
    path = cache / f"labels_{which}_{sim}_{len(strands)}_{cfg['k']}_{cfg['length']}.npz"
    if not path.exists():
        return f"(no cached labels at {path})"
    z = np.load(path, allow_pickle=True)
    fail, score = z["fail"].reshape(-1), z["surr_post"].reshape(-1)
    sizes = joint.cluster_sizes(strands, profile, cfg["k"], seed, sim, heldout=True).reshape(-1)
    ok = ~np.isnan(fail) & ~np.isnan(score)
    fail, score, sizes = fail[ok], score[ok], sizes[ok]
    lines = [
        f"### decode-time confidence, {which} strands, simulator {sim}",
        "",
        "| reads in the cluster | clusters | actual failure rate | mean predicted | AUC |",
        "|---|---|---|---|---|",
    ]
    for lo, hi in BINS:
        sel = (sizes >= lo) & (sizes <= hi)
        if sel.sum() < 50:
            continue
        lines.append(
            f"| {lo}-{hi} | {int(sel.sum())} | {fail[sel].mean():.3f} | {score[sel].mean():.3f} | "
            f"{fmt(risk.roc_auc(fail[sel] > 0.5, score[sel]))} |"
        )
    pooled, _ = risk.stratified_auc(fail > 0.5, score, np.clip(sizes, 0, 17))
    lines.append(
        f"| pooled | {len(fail)} | {fail.mean():.3f} | {score.mean():.3f} | "
        f"{fmt(risk.roc_auc(fail > 0.5, score))} (coverage-stratified {fmt(pooled)}) |"
    )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--report", default="results/joint/nanopore_k2.json")
    p.add_argument("--stratify", default="uniform,mixed")
    args = p.parse_args()
    report = json.loads(Path(args.report).read_text())

    print("## Label efficiency: what one simulation is worth\n")
    print("| simulations | binary failure rho | distilled rho | binary AUC | distilled AUC |")
    print("|---|---|---|---|---|")
    for budget, row in report["label_efficiency"].items():
        print(
            f"| {budget.split('=')[1]} | {fmt(row['fail']['spearman'])} | {fmt(row['surr_post']['spearman'])} "
            f"| {fmt(row['fail']['auc_gt_0.5'])} | {fmt(row['surr_post']['auc_gt_0.5'])} |"
        )
    print("\n(scored against the simulations the cheap label did not see)\n")

    print("## Risk models trained on each label source\n")
    print(arm_table(report))
    print()
    for which in args.stratify.split(","):
        for sim in ("A", "B"):
            print()
            print(stratified(report, which.strip(), sim))
    print()
    cost = report["labeling_cost_seconds"]
    print(
        f"\nLabeling cost for the control, {report['config']['train_strands']} strands x K={cost['k']:.0f}: "
        f"{cost['total']:.0f}s ({cost['simulate']:.0f}s simulate, {cost['decode']:.0f}s decode + confidence)."
    )


if __name__ == "__main__":
    main()
