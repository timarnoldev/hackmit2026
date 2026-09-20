#!/usr/bin/env python3
"""Find a real slot where the hand rules and the learned risk model disagree, both ways.

    uv run --extra train python scripts/rule_vs_model_case.py \
        --model checkpoints/risk/risk_nanopore_budget.pkl

The encoder builds several candidate strands for every slot of the file and keeps one. The hand
rules (max homopolymer 3, GC 0.4 to 0.6) decide which candidates are even allowed; under the rule
scorer the encoder then takes the first survivor. This script replays that candidate search on the
real test file with the real settings, scores every candidate with the trained risk model, and
looks for a window of consecutive candidates where the two disagree in both directions at once:

  * the candidate the rules hand the encoder is one the model rates dangerous, and
  * the candidate the model rates safest of all is one the rules threw away.

Nothing here is written by hand. The strands come out of the encoder's own seed schedule and the
scores out of the model. Results go to results/rule_vs_model_case.json, which is what the deck
quotes.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec.encoder import (  # the encoder's own machinery, so these are real candidates
    FILE_CRC_BYTES,
    _chunks_from_bytes,
    _homopolymer_re,
    _Layout,
    _neighbors,
    _passes,
    _scramble,
)
from dnacodec.risk import gc_fraction, load_risk_model, max_run_length
from dnacodec.testfile import test_file
from dnacodec.types import EncoderSettings

OUT = Path("results/rule_vs_model_case.json")


def load_risk(path: Path):
    """The loop's checkpoints hold a pickled RiskModel; load_risk_model reads the dict form."""
    try:
        return load_risk_model(path)
    except (AttributeError, KeyError):
        import pickle

        with open(path, "rb") as fh:
            return pickle.load(fh)


def violation(strand: str, settings: EncoderSettings) -> str:
    run = max_run_length(strand)
    gc = gc_fraction(strand)
    if settings.max_homopolymer is not None and run > settings.max_homopolymer:
        return f"run of {run}"
    if settings.gc_min is not None and gc < settings.gc_min:
        return f"GC {gc * 100:.0f}%"
    if settings.gc_max is not None and gc > settings.gc_max:
        return f"GC {gc * 100:.0f}%"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, default=Path("checkpoints/risk/risk_nanopore_budget.pkl"))
    ap.add_argument("--scan", type=int, default=20000, help="candidates to replay from the seed schedule")
    ap.add_argument("--window", type=int, default=6, help="candidates shown for the slot")
    ap.add_argument("--excerpt", type=int, default=40, help="payload bases the deck can show")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    # The settings the runs use for this channel, from results/run2_strict/nanopore_budget.json.
    settings = EncoderSettings(
        strand_length=110, seed_bases=16, redundancy=0.3, candidates_per_strand=8,
        max_homopolymer=3, gc_min=0.4, gc_max=0.6,
    )
    layout = _Layout(settings)
    data = test_file()
    n_chunks = layout.n_chunks(len(data))
    blob = zlib.crc32(data).to_bytes(FILE_CRC_BYTES, "big") + data
    chunks = _chunks_from_bytes(blob, layout, n_chunks)

    def build(counter: int) -> str:
        seed = _scramble(counter, layout.seed_bits)
        value = 0
        for i in _neighbors(seed, n_chunks):
            value ^= chunks[i]
        return layout.to_strand(seed, value)

    homo_re = _homopolymer_re(settings)
    strands = [build(c) for c in range(args.scan)]
    passes = [bool(_passes(s, settings, homo_re)) for s in strands]

    risk = load_risk(args.model)
    scores = np.asarray(risk(strands), dtype=float).reshape(-1)
    print(f"scanned {len(strands)} candidates, {sum(passes)} pass the hand rules, "
          f"mean risk {scores.mean():.3f}")

    # Walk the seed schedule in windows of `window` consecutive candidates: that is what the
    # encoder actually tries for one slot before it has its keeper.
    seed_b = settings.seed_bases
    best = None
    for a in range(0, len(strands) - args.window):
        w = range(a, a + args.window)
        kept = [i for i in w if passes[i]]
        cut = [i for i in w if not passes[i]]
        if len(kept) < 2 or len(cut) < 2:
            continue
        rules_pick = kept[0]                      # the encoder takes the first survivor
        model_pick = min(w, key=lambda i: scores[i])
        if passes[model_pick]:
            continue                              # no disagreement worth showing
        if scores[model_pick] >= min(scores[i] for i in kept):
            continue
        # the story is only honest if the crossed-out favourite is a near miss on the rule,
        # and if everything the deck shows happens inside the excerpt it can print
        if max_run_length(strands[model_pick]) > settings.max_homopolymer + 1:
            continue
        def visible(i: int) -> bool:
            """A cut candidate only belongs on the slide if the deck can show why it was cut:
            a GC violation goes on a chip, a homopolymer has to fall inside the excerpt."""
            if passes[i]:
                return True
            if violation(strands[i], settings).startswith("GC"):
                return True
            # the whole run has to sit inside the excerpt, or the letters on the slide would
            # show a shorter run than the chip next to them claims
            return max_run_length(strands[i][seed_b:seed_b + args.excerpt]) == max_run_length(strands[i])

        if not all(visible(i) for i in w):
            continue
        gap = float(scores[rules_pick] - scores[model_pick])
        if best is None or gap > best[0]:
            best = (gap, a, rules_pick, model_pick)

    if best is None:
        sys.exit("no window met the conditions; widen --scan or relax the window size")

    gap, a, rules_pick, model_pick = best
    rows = []
    for i in range(a, a + args.window):
        s = strands[i]
        rows.append({
            "n": i - a + 1,
            "strand": s,
            "excerpt": s[seed_b:seed_b + args.excerpt],
            "excerpt_from": seed_b + 1,
            "excerpt_to": seed_b + args.excerpt,
            "risk": round(float(scores[i]), 3),
            "passes": passes[i],
            "violation": violation(s, settings),
            "run": max_run_length(s),
            "gc": round(gc_fraction(s), 3),
            "rules_pick": i == rules_pick,
            "model_pick": i == model_pick,
        })

    print(f"\nslot candidates {a} to {a + args.window - 1} of the seed schedule")
    for r in rows:
        mark = "rules keep" if r["passes"] else f"rules cut, {r['violation']}"
        tail = " <- the rules hand this one over" if r["rules_pick"] else (
            " <- the model would keep this one" if r["model_pick"] else "")
        print(f"  {r['n']}  risk {r['risk']:.3f}  {mark:<20}{tail}")
        print(f"     {r['strand']}")
    print(f"\nrisk of the rules' pick minus risk of the model's pick: {gap:.3f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "model": str(args.model),
        "settings": {
            "strand_length": settings.strand_length, "seed_bases": settings.seed_bases,
            "max_homopolymer": settings.max_homopolymer,
            "gc_min": settings.gc_min, "gc_max": settings.gc_max,
        },
        "scanned": len(strands),
        "mean_risk": round(float(scores.mean()), 3),
        "window_start": a,
        "gap": round(gap, 3),
        "candidates": rows,
    }, indent=2) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
