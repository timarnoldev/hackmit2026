"""Does learned candidate selection help at file level? Measured as a curve.

The ablation ladder answers this with one threshold, "fewest reads at which all trials recover",
and that metric is knife-edge near the top of the curve: run 2 puts the risk model at half a read
of benefit, run 6 at a read of cost. Neither number has an error bar.

The ladder's C and D rungs are also not a clean isolation. The settings search is free to move
redundancy between them, and in run 6 it does (1.6 against 1.3), so the rung compares two codecs
that differ in more than the scorer.

This script addresses both. It holds every setting identical, changes only the scorer, and
measures the whole recovery curve at 300 held-out trials per point.

    uv run --extra train python scripts/tier2_recovery_curve.py --run-id run2_strict

"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pickle
from pathlib import Path

from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.encoder import rule_scorer
from dnacodec.evaluate import recovery_trials
from dnacodec.profiles import load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import heldout_seeds
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

CHECKPOINTS = Path(__file__).resolve().parents[1] / "checkpoints" / "loop"


def load_risk(path: Path):
    from dnacodec.risk import load_risk_model

    try:
        return load_risk_model(path)
    except AttributeError:  # the loop pickles the model object itself
        return pickle.loads(path.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run2_strict")
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--coverages", default="4,4.5,5,5.5,6,7,8,10")
    ap.add_argument("--candidates", type=int, default=32)
    ap.add_argument("--iteration", type=int, default=1, help="which iteration's settings and risk model")
    args = ap.parse_args()

    run_file = RESULTS_DIR / args.run_id / f"{args.profile}.json"
    run = json.loads(run_file.read_text())
    it = run["iterations"][args.iteration]
    base = EncoderSettings(**{k: v for k, v in it["settings"].items() if k in
                              {f.name for f in dataclasses.fields(EncoderSettings)}})

    # The only difference between the two codecs is which scorer ranks the candidates. Same
    # redundancy, same strand length, same hard rules, same number of candidates.
    rules_only = dataclasses.replace(base, candidates_per_strand=args.candidates, risk_threshold=None)
    with_risk = dataclasses.replace(base, candidates_per_strand=args.candidates)

    risk_path = sorted((CHECKPOINTS / args.run_id.replace("_strict", "") / args.profile).glob("risk_it*.pkl"))
    if not risk_path:
        raise SystemExit(f"no risk model under {CHECKPOINTS / args.run_id / args.profile}")
    model = load_risk(risk_path[min(args.iteration, len(risk_path) - 1)])

    variants = {
        "hand rules only (rule scorer)": (rules_only, rule_scorer(rules_only)),
        "learned selection (risk model)": (with_risk, model),
    }

    profile = load_profile(args.profile)
    coverages = [float(c) for c in args.coverages.split(",")]
    seeds = heldout_seeds(args.trials)
    data, decoder = test_file(), MajorityVoteDecoder()

    print(f"{args.profile}, {args.run_id} iteration {args.iteration}, {args.trials} held-out trials per point")
    print(f"identical settings, only the scorer differs: redundancy {base.redundancy}, "
          f"strand_length {base.strand_length}, {args.candidates} candidates, "
          f"risk_threshold {with_risk.risk_threshold}\n")
    print(f"{'codec':32s}" + "".join(f"{c:>7.1f}" for c in coverages))
    table: dict[str, dict[float, float]] = {}
    for name, (settings, scorer) in variants.items():
        row, table[name] = f"{name:32s}", {}
        for coverage in coverages:
            channel = dataclasses.replace(profile, coverage_mean=coverage)
            metrics = recovery_trials(data, settings, scorer, decoder, channel, seeds, workers=args.workers)
            rate = float(metrics.recovery_rate or 0.0)
            table[name][coverage] = rate
            row += f"{rate:7.3f}"
        print(row, flush=True)

    path = RESULTS_DIR / f"tier2_recovery_curve_{args.run_id}_{args.profile}.json"
    path.write_text(json.dumps(
        {"trials": args.trials, "run_id": args.run_id, "profile": args.profile,
         "iteration": args.iteration, "candidates": args.candidates, "table": table}, indent=2) + "\n")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
