"""Why the risk model scores AUC ~0.5 on real reads, and what it does once coverage is
held fixed. Firewall test 3: does the risk model's ranking transfer to real reads?

    uv run python scripts/risk_real_analysis.py \
        --dataset dnaformer:BinnedNanoporeSecondFlowcell_Random \
        --risk-model checkpoints/loop/run2/nanopore_budget/risk_it3.pkl \
        --decoder polish --coverages 4 6 8 --repeats 4 --out /tmp/risk_real_dnaformer.json

What it measures on the held-out real split:

1. How real decoder failure is distributed, and how much of it the read count alone
   explains (AUC of -read count vs failure). Real failure is dominated by coverage, so an
   unstratified AUC of the risk model mostly measures whether sequence correlates with how
   many reads a cluster happened to get, which it should not.
2. AUC **inside coverage strata**: every cluster in a stratum gets exactly the same number
   of reads (subsampled, `--repeats` times, label = fraction of subsamples decoded wrongly),
   so coverage cannot contribute anything. Reported for the risk model and for three simple
   baselines: longest homopolymer run, GC deviation from 0.5, and the 5-mer context risk
   read straight off the simulator's calibrated context table.
3. How much the references actually vary in those features. If a dataset's references are
   constrained (DNAformer's are), the honest statement is that the effect cannot be measured
   there, not that it is absent.

With --train-real the script additionally trains a risk model on **real train-split**
decoder failures at a fixed coverage and evaluates it on the held-out split with exactly the
same protocol. That model is kept apart from the simulator-trained one in every way: its own
output file, its own rows in the table, and it never touches scripts/train_risk.py.

Seed discipline: train split + train_seed() for anything fitted, held-out split +
heldout_seeds() for every reported number. Microsoft references are used for measurement
only, never for fitting (their README says they are not uniformly random).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import risk  # noqa: E402
from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.realdata import load_dnaformer, load_microsoft  # noqa: E402
from dnacodec.seeds import heldout_seeds, train_seed  # noqa: E402

MAX_READS = 16  # decoders take at most this many reads per cluster


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- data and scores


def load_clusters(dataset: str, split: str, limit: int | None):
    """dataset is "microsoft" or "dnaformer:<file stem>"."""
    if dataset == "microsoft":
        clusters = load_microsoft(split)  # type: ignore[arg-type]
    elif dataset.startswith("dnaformer:"):
        clusters = load_dnaformer(dataset.split(":", 1)[1], split)  # type: ignore[arg-type]
    else:
        raise ValueError(f"unknown dataset {dataset!r}")
    clusters = [c for c in clusters if c.reads]
    return clusters[:limit] if limit else clusters


def make_decoder(kind: str, checkpoint: Path | None):
    if kind == "baseline":
        return MajorityVoteDecoder()
    from dnacodec.model.polish import PolishDecoder

    return PolishDecoder(str(checkpoint or Path("checkpoints/polish/polish.pt")))


def load_scorer(path: Path, device: str | None):
    """A risk model saved by RiskModel.save, or pickled whole by the loop's manifest."""
    import pickle

    if path.suffix == ".pkl":
        with path.open("rb") as f:
            model = pickle.load(f)
        if model is None:
            raise ValueError(f"{path} holds no risk model (the loop saves None before the first fit)")
        return model
    return risk.load_risk_model(path, device)


def feature_scores(references: list[str], profile, models: dict) -> dict[str, np.ndarray]:
    """Every score we rank real failures with. Higher must mean riskier."""
    scores: dict[str, np.ndarray] = {
        "longest_run": np.array([risk.max_run_length(s) for s in references], dtype=float),
        "gc_deviation": np.array([abs(risk.gc_fraction(s) - 0.5) for s in references]),
        "context_worst_5mer": risk.context_risk(references, profile, "total", "max"),
        "context_top5_5mer": risk.context_risk(references, profile, "total", "top5"),
        "context_mean_5mer": risk.context_risk(references, profile, "total", "mean"),
        "context_mean_del": risk.context_risk(references, profile, "del", "mean"),
    }
    for name, model in models.items():
        scores[name] = np.asarray(model(references), dtype=np.float64)
    return scores


def reference_stats(references: list[str]) -> dict:
    runs = np.array([risk.max_run_length(s) for s in references])
    gc = np.array([risk.gc_fraction(s) for s in references])
    return {
        "n": len(references),
        "length": len(references[0]),
        "max_run_hist": {int(r): int(c) for r, c in zip(*np.unique(runs, return_counts=True))},
        "max_run_share_3_or_4": float(np.mean((runs >= 3) & (runs <= 4))),
        "gc_mean": float(gc.mean()),
        "gc_std": float(gc.std()),
        "gc_min": float(gc.min()),
        "gc_max": float(gc.max()),
        "gc_share_in_0.44_0.56": float(np.mean((gc >= 0.44) & (gc <= 0.56))),
    }


# ---------------------------------------------------------------- the two analyses


def natural_coverage(clusters, decoder, scores: dict[str, np.ndarray], seed: int, workers: int | None) -> dict:
    """Failure with the reads each cluster actually has (capped at MAX_READS).

    This is the setting the 0.516 number came from, and the one where coverage dominates.
    """
    refs = [c.reference for c in clusters]
    sizes = np.array([min(len(c.reads), MAX_READS) for c in clusters])
    rates = risk.real_failure_rates(
        refs, [c.reads for c in clusters], decoder, MAX_READS, repeats=1, seed=seed, workers=workers, heldout=True
    )
    failed = rates > 0.5
    out = {
        "n": len(refs),
        "failure_rate": float(failed.mean()),
        "reads_mean": float(sizes.mean()),
        "auc_read_count": risk.roc_auc(failed, -sizes.astype(float)),
        "failure_by_reads": [
            {"reads": int(k), "n": int((sizes == k).sum()), "failure_rate": float(failed[sizes == k].mean())}
            for k in sorted(set(sizes.tolist()))
        ],
        "auc": {},
        "auc_stratified_by_read_count": {},
    }
    for name, score in scores.items():
        out["auc"][name] = risk.roc_auc(failed, score)
        pooled, rows = risk.stratified_auc(failed, score, sizes)
        out["auc_stratified_by_read_count"][name] = {"pooled": pooled, "strata": rows}
    return out


def fixed_coverage(clusters, decoder, scores: dict[str, np.ndarray], coverage: int, repeats: int,
                   seed: int, workers: int | None, heldout: bool) -> dict:
    """Give every cluster exactly `coverage` reads, `repeats` times. Coverage is now constant,
    so any ranking signal left has to come from the sequence."""
    keep = [i for i, c in enumerate(clusters) if len(c.reads) >= coverage]
    refs = [clusters[i].reference for i in keep]
    rates = risk.real_failure_rates(
        refs, [clusters[i].reads for i in keep], decoder, coverage,
        repeats=repeats, seed=seed, workers=workers, heldout=heldout,
    )
    ok = ~np.isnan(rates)
    keep = [i for i, good in zip(keep, ok.tolist()) if good]
    refs = [r for r, good in zip(refs, ok.tolist()) if good]
    rates = rates[ok]
    out = {
        "coverage": coverage,
        "repeats": repeats,
        "n": len(refs),
        "mean_failure_rate": float(rates.mean()),
        "share_failed_majority": float((rates > 0.5).mean()),
        "share_failed_ever": float((rates > 0).mean()),
        "index": keep,
        "rates": rates.tolist(),
        "auc": {},
    }
    # Second stratification: inside this coverage stratum, also hold the longest homopolymer
    # run fixed. What survives is what the learned model knows beyond the hand rule.
    run_bin = np.array([min(risk.max_run_length(r), 6) for r in refs])
    for name, score in scores.items():
        s = score[np.asarray(keep)]
        pooled, rows = risk.stratified_auc(rates > 0.5, s, run_bin)
        out["auc"][name] = {
            "auc_gt_0.5": risk.roc_auc(rates > 0.5, s),
            "auc_gt_0": risk.roc_auc(rates > 0, s),
            "spearman": risk.spearman(rates, s),
            "auc_gt_0.5_within_run_length": pooled,
            "run_length_strata": rows,
        }
    return out


def train_on_real(dataset: str, decoder, coverage: int, repeats: int, limit: int | None,
                  workers: int | None, epochs: int, device: str | None) -> tuple[object, dict]:
    """Fit a RiskModel on real *train split* decoder failures at a fixed coverage."""
    seed = train_seed(7)
    clusters = load_clusters(dataset, "train", limit)
    clusters = [c for c in clusters if len(c.reads) >= coverage]
    refs = [c.reference for c in clusters]
    t = time.time()
    rates = risk.real_failure_rates(
        refs, [c.reads for c in clusters], decoder, coverage, repeats=repeats, seed=seed, workers=workers
    )
    label_s = time.time() - t
    ok = ~np.isnan(rates)
    log(f"[train-real] {int(ok.sum())} train clusters at {coverage} reads x {repeats}, "
        f"mean failure {np.nanmean(rates):.3f}, labeled in {label_s:.0f}s")
    model = risk.RiskModel(epochs=epochs, seed=seed, device=device, verbose=False)
    t = time.time()
    model.fit([r for r, good in zip(refs, ok.tolist()) if good], rates[ok])
    info = {
        "dataset": dataset,
        "split": "train",
        "coverage": coverage,
        "repeats": repeats,
        "n": int(ok.sum()),
        "mean_failure_rate": float(np.nanmean(rates)),
        "label_seconds": label_s,
        "fit_seconds": time.time() - t,
    }
    log(f"[train-real] fit in {info['fit_seconds']:.0f}s")
    return model, info


# ---------------------------------------------------------------- reporting


def print_table(report: dict) -> None:
    names = list(report["scores"])
    log("")
    log(f"=== {report['dataset']} held-out, decoder {report['decoder']} ===")
    nat = report["natural_coverage"]
    log(f"natural coverage: n={nat['n']}, {nat['failure_rate']:.1%} failures, "
        f"mean {nat['reads_mean']:.1f} reads, AUC of read count alone = {nat['auc_read_count']:.3f}")
    for metric, share_key, title in [
        ("auc_gt_0.5", "share_failed_majority", "AUC, strand fails in most subsamples"),
        ("auc_gt_0", "share_failed_ever", "AUC, strand fails in at least one subsample"),
        ("spearman", "share_failed_majority", "Spearman with the failure rate"),
        ("auc_gt_0.5_within_run_length", "share_failed_majority",
         "AUC, coverage AND longest run held fixed (beyond the hand rule)"),
    ]:
        log("")
        log(f"  {title}")
        header = "  score".ljust(26) + "unstrat".rjust(9) + "by-reads".rjust(10)
        for cov in report["coverages"]:
            header += f"c={cov}".rjust(9)
        log(header + "pooled".rjust(9))
        for name in names:
            row = f"  {name}".ljust(26)
            if metric == "auc_gt_0.5":
                row += f"{nat['auc'][name]:.3f}".rjust(9)
                row += f"{nat['auc_stratified_by_read_count'][name]['pooled']:.3f}".rjust(10)
            else:
                row += "-".rjust(9) + "-".rjust(10)
            values, weights = [], []
            for cov in report["coverages"]:
                block = report["fixed_coverage"][str(cov)]
                a = block["auc"][name][metric]
                row += f"{a:.3f}".rjust(9)
                if not np.isnan(a):
                    share = block[share_key]
                    values.append(a)
                    weights.append(block["n"] * max(share * (1 - share), 1e-6))
            pooled = float(np.average(values, weights=weights)) if values else float("nan")
            log(row + f"{pooled:.3f}".rjust(9))
    log("")
    for cov in report["coverages"]:
        b = report["fixed_coverage"][str(cov)]
        log(f"  [coverage {cov}] n={b['n']}, {b['share_failed_majority']:.1%} fail in most subsamples, "
            f"{b['share_failed_ever']:.1%} fail at least once")
    stats = report["reference_stats"]
    log(f"  references: length {stats['length']}, longest run 3 or 4 for {stats['max_run_share_3_or_4']:.1%}, "
        f"GC {stats['gc_min']:.2f} to {stats['gc_max']:.2f} (sd {stats['gc_std']:.3f}, "
        f"{stats['gc_share_in_0.44_0.56']:.1%} within 0.44 to 0.56)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default="dnaformer:BinnedNanoporeSecondFlowcell_Random",
                   help='"microsoft" or "dnaformer:<file stem>"')
    p.add_argument("--profile", default="nanopore_budget", help="only for the context-table baseline")
    p.add_argument("--risk-model", type=Path, default=None, help="a risk model to test (any origin)")
    p.add_argument("--risk-model-name", default="risk_model_sim", help="its row label in the table")
    p.add_argument("--decoder", choices=["baseline", "polish"], default="polish")
    p.add_argument("--polish-checkpoint", type=Path, default=None)
    p.add_argument("--coverages", type=int, nargs="+", default=[4, 6, 8])
    p.add_argument("--repeats", type=int, default=4)
    p.add_argument("--max-clusters", type=int, default=0, help="0 = all held-out clusters")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--train-real", action="store_true", help="also fit a risk model on real train failures")
    p.add_argument("--train-coverage", type=int, default=6)
    p.add_argument("--train-clusters", type=int, default=12000)
    p.add_argument("--train-repeats", type=int, default=4)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--device", default=None)
    p.add_argument("--save-real-model", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    profile = load_profile(args.profile)
    decoder = make_decoder(args.decoder, args.polish_checkpoint)
    models: dict = {}
    if args.risk_model:
        models[args.risk_model_name] = load_scorer(args.risk_model, args.device)
        log(f"loaded simulator-trained risk model {args.risk_model}")

    real_info = None
    if args.train_real:
        real_model, real_info = train_on_real(
            args.dataset, decoder, args.train_coverage, args.train_repeats,
            args.train_clusters or None, args.workers, args.epochs, args.device,
        )
        models["risk_model_real"] = real_model
        if args.save_real_model:
            real_model.meta = {"trained_on": "real", **real_info}
            real_model.save(args.save_real_model)
            log(f"saved real-trained risk model to {args.save_real_model}")

    clusters = load_clusters(args.dataset, "heldout", args.max_clusters or None)
    refs = [c.reference for c in clusters]
    log(f"{len(clusters)} held-out clusters from {args.dataset}")
    scores = feature_scores(refs, profile, models)
    hs = heldout_seeds(10)

    report: dict = {
        "dataset": args.dataset,
        "split": "heldout",
        "decoder": decoder.name,
        "profile": args.profile,
        "risk_model": str(args.risk_model) if args.risk_model else None,
        "scores": list(scores),
        "coverages": args.coverages,
        "reference_stats": reference_stats(refs),
        "trained_on_real": real_info,
    }
    t = time.time()
    report["natural_coverage"] = natural_coverage(clusters, decoder, scores, hs[0], args.workers)
    log(f"natural coverage done in {time.time() - t:.0f}s")
    report["fixed_coverage"] = {}
    for i, cov in enumerate(args.coverages):
        t = time.time()
        block = fixed_coverage(clusters, decoder, scores, cov, args.repeats, hs[1 + i], args.workers, True)
        block.pop("index")
        block.pop("rates")
        report["fixed_coverage"][str(cov)] = block
        log(f"coverage {cov}: n={block['n']} done in {time.time() - t:.0f}s")

    print_table(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=float) + "\n")
        log(f"wrote {args.out}")


if __name__ == "__main__":
    main()
