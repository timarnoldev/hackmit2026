"""What did the risk model learn? Read the rules back out of a trained model.

    uv run --extra train python scripts/what_the_model_learned.py --run-id run5

For each situation it prints the patterns the model considers dangerous, its response to
homopolymer length and to GC content, and how its ranking compares with the error context
table the simulator was calibrated with. The last part is the interesting one: the model never
saw that table, it only saw which strands the decoder failed on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from dnacodec.profiles import PROFILES_DIR, load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.risk import gc_probe, homopolymer_probe, load_risk_model

CHECKPOINTS = Path(__file__).resolve().parents[1] / "checkpoints" / "loop"


def context_table_top(profile_name: str, kind: str = "del", n: int = 8) -> list[tuple[str, float]]:
    """The k-mers the simulator's calibrated table says are worst, for comparison."""
    profile = load_profile(profile_name)
    if not profile.context_table:
        return []
    table = json.loads((PROFILES_DIR / profile.context_table).read_text())
    k, values = table["k"], np.asarray(table[kind], dtype=float)
    letters = "ACGT"
    order = np.argsort(values)[::-1][:n]
    out = []
    for idx in order:
        kmer = ""
        rest = int(idx)
        for _ in range(k):
            kmer = letters[rest % 4] + kmer
            rest //= 4
        out.append((kmer, float(values[idx])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default="run5")
    ap.add_argument("--iteration", type=int, default=None, help="default: the last one present")
    ap.add_argument("--kmer", type=int, default=5)
    args = ap.parse_args()

    for situation_dir in sorted((CHECKPOINTS / args.run_id).iterdir()):
        models = sorted(situation_dir.glob("risk_it*.pkl"))
        if not models:
            continue
        path = models[args.iteration] if args.iteration is not None else models[-1]
        model = load_risk_model(path)
        name = situation_dir.name
        print(f"\n{'=' * 78}\n{name}   ({path.name})\n{'=' * 78}")

        print(f"\nPatterns it rates most dangerous ({args.kmer}-mers, inserted into random backgrounds):")
        for kmer, risk in model.top_kmers(k=args.kmer, n=10):
            print(f"   {kmer}   risk {risk:.3f}")
        background = getattr(model, "background_risk", None)
        if background is not None:
            print(f"   (a random background strand sits at {background:.3f})")

        print("\nResponse to a single homopolymer run of length r:")
        for r, risk in sorted(homopolymer_probe(model).items()):
            print(f"   run of {r:2d}   risk {risk:.3f}")

        print("\nResponse to GC content (runs capped at 3, so this is GC alone):")
        for gc, risk in sorted(gc_probe(model).items()):
            print(f"   GC {gc:.2f}   risk {risk:.3f}")

        for kind in ("del", "sub"):
            top = context_table_top(name, kind)
            if not top:
                continue
            print(f"\nWhat the calibrated channel says is worst for {kind} (the model never saw this):")
            print("   " + "  ".join(f"{k} x{v:.1f}" for k, v in top[:6]))
            scored = model([k * 22 for k, _ in top[:6]])
            print("   the model's risk for those same patterns: " + "  ".join(f"{s:.3f}" for s in scored))

    out = RESULTS_DIR / f"{args.run_id}_learned_rules.txt"
    print(f"\n(tip: rerun with a tee into {out} if you want this on disk)")


if __name__ == "__main__":
    main()
