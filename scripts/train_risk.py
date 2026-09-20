"""Train and evaluate the risk model for one situation.

    uv run python scripts/train_risk.py --profile nanopore_budget --n-strands 12000 --k 16 \
        --out checkpoints/risk_nanopore_budget.pt

Steps:
1. Generate controlled strands (train seed) and label each with its failure rate over K
   simulated clusters, decoded with the baseline decoder (frozen).
2. Fit the risk model.
3. Evaluate on strands generated AND labeled with held-out seeds (never used for fitting):
   ROC AUC of predicted risk vs failure (label > 0.5 and label > 0), Spearman correlation
   with the failure rate, the same for two hand-written heuristics (longest run, GC
   deviation) as a baseline, and sanity tables by run length and GC.
4. Firewall test 3 preview (--real, default for nanopore profiles): decode the held-out
   DNAformer Nanopore clusters with the baseline and report ROC AUC of the risk score vs
   actual decoder failure. Evaluation only, never training.

Writes the model to --out and a JSON report next to it (<out>.json). Neither is committed.
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
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import heldout_seeds, train_seed  # noqa: E402

RUN_BINS = [(1, 3, "<=3"), (4, 4, "4"), (5, 5, "5"), (6, 6, "6"), (7, 7, "7"), (8, 99, ">=8")]
GC_BINS = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.01)]


def log(msg: str) -> None:
    print(msg, flush=True)


def ranking(y: np.ndarray, score: np.ndarray) -> dict[str, float]:
    ok = ~np.isnan(y)
    y, score = y[ok], score[ok]
    return {
        "auc_gt_0.5": risk.roc_auc(y > 0.5, score),
        "auc_gt_0": risk.roc_auc(y > 0, score),
        "spearman": risk.spearman(y, score),
        "positives_gt_0.5": float(np.mean(y > 0.5)),
        "n": int(len(y)),
    }


def heuristics(strands: list[str]) -> dict[str, np.ndarray]:
    return {
        "longest_run": np.array([risk.max_run_length(s) for s in strands], dtype=float),
        "gc_deviation": np.array([abs(risk.gc_fraction(s) - 0.5) for s in strands]),
    }


def evaluate_set(name: str, strands: list[str], y: np.ndarray, pred: np.ndarray) -> dict:
    out = {"model": ranking(y, pred)}
    for h, score in heuristics(strands).items():
        out[h] = ranking(y, score)
    m = out["model"]
    log(
        f"[{name}] n={m['n']} mean failure={np.nanmean(y):.3f}  "
        f"model AUC(>0.5)={m['auc_gt_0.5']:.3f} AUC(>0)={m['auc_gt_0']:.3f} "
        f"Spearman={m['spearman']:.3f}  |  longest-run AUC(>0.5)={out['longest_run']['auc_gt_0.5']:.3f} "
        f"Spearman={out['longest_run']['spearman']:.3f}  |  GC-dev Spearman={out['gc_deviation']['spearman']:.3f}"
    )
    return out


def tables(strands: list[str], y: np.ndarray, pred: np.ndarray) -> dict:
    runs = np.array([risk.max_run_length(s) for s in strands])
    gcs = np.array([risk.gc_fraction(s) for s in strands])
    by_run, by_gc = [], []
    log("  longest run | n     | mean predicted | mean actual")
    for lo, hi, label in RUN_BINS:
        sel = (runs >= lo) & (runs <= hi)
        if sel.any():
            row = {"bin": label, "n": int(sel.sum()), "pred": float(pred[sel].mean()), "actual": float(np.nanmean(y[sel]))}
            by_run.append(row)
            log(f"  {label:>11} | {row['n']:5d} | {row['pred']:.3f}          | {row['actual']:.3f}")
    log("  GC bin      | n     | mean predicted | mean actual")
    for lo, hi in GC_BINS:
        sel = (gcs >= lo) & (gcs < hi)
        if sel.any():
            label = f"{lo:.1f}-{min(hi, 1.0):.1f}"
            row = {"bin": label, "n": int(sel.sum()), "pred": float(pred[sel].mean()), "actual": float(np.nanmean(y[sel]))}
            by_gc.append(row)
            log(f"  {label:>11} | {row['n']:5d} | {row['pred']:.3f}          | {row['actual']:.3f}")
    return {"by_longest_run": by_run, "by_gc": by_gc}


def real_firewall(model, name: str, workers: int | None) -> dict:
    from dnacodec.realdata import load_dnaformer

    t = time.time()
    clusters = load_dnaformer(name, "heldout")
    refs = [c.reference for c in clusters]
    reads = [c.reads for c in clusters]
    length = len(refs[0])
    decoded = risk.decode_parallel(MajorityVoteDecoder(), reads, length, workers)
    metrics = evaluate(refs, decoded, reads)
    nonempty = np.array([len(r) > 0 for r in reads])
    failed = np.array([d != r for d, r in zip(decoded, refs)], dtype=float)
    pred = model(refs)
    sizes = np.array([len(r) for r in reads])
    out = {
        "dataset": f"{name} (heldout split)",
        "n_clusters": int(nonempty.sum()),
        "baseline_strand_accuracy": metrics.strand_accuracy,
        "model": ranking(failed[nonempty], pred[nonempty]),
        "seconds": time.time() - t,
    }
    for h, score in heuristics(refs).items():
        out[h] = ranking(failed[nonempty], score[nonempty])
    # Coverage is sequence-independent noise for this ranking; also rank within a band.
    band = nonempty & (sizes >= 5) & (sizes <= 10)
    out["model_coverage_5_to_10"] = ranking(failed[band], pred[band])
    # Coverage-stratified AUC: only compare clusters with similar read counts
    # (per-stratum AUCs weighted by their number of positive-negative pairs).
    num = den = 0.0
    for lo, hi in [(1, 2), (3, 4), (5, 6), (7, 9), (10, 14), (15, 10**9)]:
        sel = nonempty & (sizes >= lo) & (sizes <= hi)
        pos, neg = float((failed[sel] > 0.5).sum()), float((failed[sel] <= 0.5).sum())
        if pos and neg:
            num += risk.roc_auc(failed[sel] > 0.5, pred[sel]) * pos * neg
            den += pos * neg
    out["model_auc_coverage_stratified"] = num / den if den else float("nan")
    out["reference_stats"] = {
        "max_run_hist": {int(r): int(c) for r, c in zip(*np.unique([risk.max_run_length(s) for s in refs], return_counts=True))},
        "gc_min": float(min(risk.gc_fraction(s) for s in refs)),
        "gc_max": float(max(risk.gc_fraction(s) for s in refs)),
    }
    log(
        f"[real {name} heldout] clusters={out['n_clusters']} baseline strand accuracy={metrics.strand_accuracy:.3f} "
        f"model AUC={out['model']['auc_gt_0.5']:.3f} Spearman={out['model']['spearman']:.3f} "
        f"(5-10 reads: AUC={out['model_coverage_5_to_10']['auc_gt_0.5']:.3f}, n={out['model_coverage_5_to_10']['n']}; "
        f"coverage-stratified AUC={out['model_auc_coverage_stratified']:.3f})  "
        f"longest-run AUC={out['longest_run']['auc_gt_0.5']:.3f}  GC-dev AUC={out['gc_deviation']['auc_gt_0.5']:.3f}  "
        f"[{out['seconds']:.0f}s]"
    )
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", required=True)
    p.add_argument("--n-strands", type=int, default=12000)
    p.add_argument("--k", type=int, default=16)
    p.add_argument("--out", type=Path, default=None, help="default checkpoints/risk_<profile>.pt")
    p.add_argument("--heldout-strands", type=int, default=3000)
    p.add_argument("--min-length", type=int, default=100)
    p.add_argument("--max-length", type=int, default=140)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--model", choices=["cnn", "kmer"], default="cnn")
    p.add_argument("--device", default=None)
    p.add_argument("--load", type=Path, default=None, help="skip labeling and fitting, evaluate this model")
    p.add_argument("--real", default=None, help="DNAformer file stem for firewall test 3; 'none' to skip")
    args = p.parse_args()

    profile = load_profile(args.profile)
    out = args.out or Path(__file__).resolve().parents[1] / "checkpoints" / f"risk_{args.profile}.pt"
    real = args.real or ("BinnedNanoporeSecondFlowcell_Random" if profile.technology == "nanopore" else "none")
    decoder = MajorityVoteDecoder()
    seed = train_seed(args.seed)
    lengths = (args.min_length, args.max_length)
    report: dict = {"profile": args.profile, "decoder": decoder.name, "k": args.k, "n_strands": args.n_strands,
                    "lengths": lengths, "model_kind": args.model, "timings": {}}

    if args.load:
        model = risk.load_risk_model(args.load, args.device)
        log(f"loaded {args.load}")
    else:
        t = time.time()
        strands = risk.generate_strands(args.n_strands, lengths, seed=seed)
        y = risk.label_failure_rates(strands, decoder, profile, k=args.k, seed=seed, workers=args.workers)
        report["timings"]["label_train_s"] = time.time() - t
        log(f"labeled {len(strands)} strands x K={args.k} in {time.time() - t:.1f}s, mean failure {np.nanmean(y):.3f}")

        t = time.time()
        if args.model == "cnn":
            model = risk.RiskModel(epochs=args.epochs, seed=seed, device=args.device, verbose=True)
        else:
            model = risk.KmerRiskModel(seed=seed)
        model.fit(strands, y)
        report["timings"]["fit_s"] = time.time() - t
        log(f"fit {args.model} in {time.time() - t:.1f}s")

    # Held-out evaluation: strands and channel noise both from held-out seeds.
    hs = heldout_seeds(4)
    t = time.time()
    h_strands = risk.generate_strands(args.heldout_strands, lengths, seed=hs[0], heldout=True)
    h_y = risk.label_failure_rates(h_strands, decoder, profile, k=args.k, seed=hs[1], heldout=True, workers=args.workers)
    u_strands = risk.generate_strands(args.heldout_strands // 2, 140, seed=hs[2], mix={"uniform": 1.0}, heldout=True)
    u_y = risk.label_failure_rates(u_strands, decoder, profile, k=args.k, seed=hs[3], heldout=True, workers=args.workers)
    report["timings"]["label_heldout_s"] = time.time() - t

    t = time.time()
    h_pred = model(h_strands)
    report["timings"]["score_heldout_s"] = time.time() - t
    report["heldout_mixed"] = evaluate_set("held-out controlled mix", h_strands, h_y, h_pred)
    report["heldout_uniform140"] = evaluate_set("held-out uniform random, 140 nt", u_strands, u_y, model(u_strands))
    report["tables_heldout"] = tables(h_strands, h_y, h_pred)

    report["probe_homopolymer"] = risk.homopolymer_probe(model)
    report["probe_gc"] = risk.gc_probe(model)
    log("probe, one run of length r in a run-free random 140-mer: "
        + "  ".join(f"r={r}:{v:.3f}" for r, v in report["probe_homopolymer"].items()))
    log("probe, i.i.d. strands at GC (runs <= 3): "
        + "  ".join(f"{g:.1f}:{v:.3f}" for g, v in report["probe_gc"].items()))

    t = time.time()
    top = model.top_kmers(6, 15)
    report["top_kmers_6"] = top
    report["background_risk"] = model.background_risk
    report["timings"]["top_kmers_s"] = time.time() - t
    log(f"top 6-mers (background {model.background_risk:.3f}): " + ", ".join(f"{k}:{v:.3f}" for k, v in top[:10]))

    if real != "none":
        report["real_firewall"] = real_firewall(model, real, args.workers)

    if not args.load:
        model.meta = {"profile": args.profile, "decoder": decoder.name, "k": args.k, "n_strands": args.n_strands}
        model.save(out)
    out.with_suffix(".json").write_text(json.dumps(report, indent=2, default=float) + "\n")
    log(f"saved {out} and {out.with_suffix('.json')}")
    log("timings: " + ", ".join(f"{k}={v:.1f}" for k, v in report["timings"].items()))


if __name__ == "__main__":
    main()
