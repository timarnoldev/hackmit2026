"""Decoder accuracy on real held-out reads, averaged over many independent read draws.

A single subsample of reads moves the number by up to two points, which is why four tables
in this repo disagreed slightly. This averages over draws so the number can be quoted plainly.

    uv run --extra train python scripts/stable_decoder_numbers.py [--draws 20] [--workers 10]
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from dnacodec import realdata
from dnacodec.baseline import MajorityVoteDecoder
from dnacodec.results import RESULTS_DIR
from dnacodec.seeds import heldout_seeds

CHECKPOINT = "checkpoints/polish/polish.pt"
BEST_VARIANT = dict(
    rounds=2, drafts=3, select="confidence", mode="gain",
    thresholds={"low_max_reads": 3, "low": (0.5, -0.5), "high": (0.0, -1.0)},
)
_HELD: list = []


def _held():
    if not _HELD:
        _HELD.extend(realdata.load_microsoft("heldout"))
    return _HELD


def _build_decoder(spec):
    """spec = (name, kind, checkpoint, kwargs) -> a Decoder. kind is 'v1' or 'v2'."""
    _, kind, checkpoint, kwargs = spec
    if kind == "v2":
        from dnacodec.model.polish2 import Polish2Decoder

        return Polish2Decoder(checkpoint, **kwargs)
    from dnacodec.model.polish import PolishDecoder

    return PolishDecoder(checkpoint, workers=1, **kwargs)


def one_draw(args):
    k, seed, extra = args
    held = _held()
    refs = [c.reference for c in held]
    rng = np.random.default_rng(seed)
    clusters = [
        list(rng.choice(c.reads, min(k, len(c.reads)), replace=False)) if c.reads else []
        for c in held
    ]
    out = {"baseline": float(np.mean([o == r for o, r in zip(MajorityVoteDecoder().decode(clusters, 110), refs)]))}
    specs = [
        ("polish", "v1", CHECKPOINT, {}),
        ("polish_best", "v1", CHECKPOINT, BEST_VARIANT),
    ] + list(extra)
    try:
        for spec in specs:
            decoder = _build_decoder(spec)
            out[spec[0]] = float(
                np.mean([o == r for o, r in zip(decoder.decode(clusters, 110), refs)])
            )
            decoder.close()
    except Exception as exc:  # torch or checkpoint missing
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return k, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=20)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--reads", default="2,4,6,10,16")
    ap.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="NAME=KIND:CHECKPOINT[:JSON_KWARGS]",
        help="another decoder to measure in the same draws, e.g. "
        "'v2=v2:checkpoints/polish2/polish2.pt' or "
        "'v2_best=v2:ckpt.pt:{\"drafts\":3,\"rounds\":2,\"mode\":\"gain\"}'",
    )
    ap.add_argument("--out", default=None, help="result json (default results/decoder_real_heldout.json)")
    args = ap.parse_args()

    extra = []
    for item in args.extra:
        name, rest = item.split("=", 1)
        kind, rest = rest.split(":", 1)
        checkpoint, _, kwargs_json = rest.partition(":")
        extra.append((name, kind, checkpoint, json.loads(kwargs_json) if kwargs_json else {}))

    budgets = [int(x) for x in args.reads.split(",")]
    seeds = heldout_seeds(200)[50:]  # evaluation only, disjoint from the loop's blocks
    jobs = [(k, seeds[i], extra) for k in budgets for i in range(args.draws)]
    results: dict[int, list[dict]] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for k, out in pool.map(one_draw, jobs):
            results.setdefault(k, []).append(out)
    for k, rows in results.items():
        for row in rows:
            if "error" in row:
                raise SystemExit(f"decoder failed at {k} reads: {row['error']}")

    keys = [k for k in ("baseline", "polish", "polish_best", *[e[0] for e in extra])
            if k in results[budgets[0]][0]]
    print(f"{len(_held())} held-out clusters, {args.draws} independent read draws per point, mean +- sd")
    header = f"{'reads':>5}" + "".join(f"{k:>18}" for k in keys)
    print(header)
    table = {}
    for k in budgets:
        row = f"{k:>5}"
        table[k] = {}
        for key in keys:
            values = np.array([r[key] for r in results[k]])
            table[k][key] = {"mean": float(values.mean()), "sd": float(values.std())}
            row += f"   {values.mean():7.1%} +-{values.std():5.1%}"
        print(row)

    path = Path(args.out) if args.out else RESULTS_DIR / "decoder_real_heldout.json"
    path.write_text(json.dumps({"draws": args.draws, "clusters": len(_held()), "table": table}, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
