"""How often is a designed strand missing, and does yield depend on GC or homopolymers?

Owner: Agent A. Train splits only. Read-only: this script never writes a profile.

For every designed reference it counts the reads it got (0 if the strand never appears in
the dataset), then relates that count to the reference's GC content and longest homopolymer
run. That is the evidence for profile.dropout_rate and profile.gc_dropout_factor, which were
never calibrated.

What the "missing" number can and cannot separate:
    - it CAN measure: the share of designed strands for which the published dataset contains
      no read at all;
    - it CANNOT separate: never synthesized, lost in PCR or sample handling, not sequenced,
      sequenced but discarded by the authors' clustering or quality filters;
    - it is a LOWER BOUND for archival storage: these samples are fresh, and a dataset that
      was clustered against known references also loses strands whose reads exist but were
      not binned.

    uv run python scripts/analyze_dropout.py
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from dnacodec import realdata
from dnacodec.simulator import _encode, run_lengths

DESIGN_FILE = realdata.DNAFORMER_DIR / "sequences_random_file.txt"


def gc_and_run(refs: list[str]) -> tuple[np.ndarray, np.ndarray]:
    codes, lengths = _encode(refs)
    gc = ((codes == 1) | (codes == 2)).sum(axis=1) / np.maximum(lengths, 1)
    runs = np.where(codes == 255, 0, run_lengths(codes)).max(axis=1)
    return gc, runs


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Slope of y on x with a 95% interval from heteroscedasticity-robust (HC0) errors."""
    design = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    xtx_inv = np.linalg.inv(design.T @ design)
    cov = xtx_inv @ (design.T @ (design * resid[:, None] ** 2)) @ xtx_inv
    se = float(np.sqrt(cov[1, 1]))
    return float(beta[1]), float(beta[1] - 1.96 * se), float(beta[1] + 1.96 * se)


def wilson(x: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = x / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(centre - half, 0.0), centre + half)


def decile_table(values: np.ndarray, sizes: np.ndarray, label: str) -> str:
    order = np.argsort(values, kind="mergesort")
    groups = np.array_split(order, 10)
    lines = [f"  {label:>14} | {'mean cov':>8} | {'zero %':>7} | {'n':>6}"]
    for i, idx in enumerate(groups):
        lines.append(
            f"  decile {i + 1:>2} {values[idx].mean():5.3f} | {sizes[idx].mean():8.2f} | "
            f"{100 * np.mean(sizes[idx] == 0):7.3f} | {len(idx):6d}"
        )
    first, last = groups[0], groups[-1]
    ratio = sizes[first].mean() / max(sizes[last].mean(), 1e-9)
    lines.append(f"  lowest/highest decile mean coverage ratio: {ratio:.3f}")
    return "\n".join(lines)


def analyze(name: str, refs: list[str], counts: np.ndarray, note: str) -> None:
    gc, runs = gc_and_run(refs)
    n = len(refs)
    zero = int(np.sum(counts == 0))
    lo, hi = wilson(zero, n)
    print(f"\n=== {name}: {n} designed references (train split), {note}")
    print(f"  reads per reference: mean {counts.mean():.2f}  median {np.median(counts):.0f}")
    print(f"  no reads at all: {zero} = {zero / n:.4%}  [95% CI {lo:.4%}, {hi:.4%}]")
    print(f"  GC content: mean {gc.mean():.3f} sd {gc.std():.3f}; longest run: mean {runs.mean():.2f}")

    dev = np.abs(gc - 0.5)
    y = counts.astype(float)
    rel = y / max(y.mean(), 1e-9)
    for label, x in (("GC", gc), ("|GC - 0.5|", dev), ("longest run", runs.astype(float))):
        slope, slo, shi = ols(x, rel)
        unit = 0.1 if label != "longest run" else 1.0
        print(
            f"  coverage vs {label:<12}: {slope * unit:+.4f} of mean coverage per "
            f"{unit:g} [95% CI {slo * unit:+.4f}, {shi * unit:+.4f}]"
        )
        if zero:
            d_slope, d_lo, d_hi = ols(x, (counts == 0).astype(float))
            print(
                f"     dropout   vs {label:<12}: {d_slope * unit:+.5f} per {unit:g} "
                f"[95% CI {d_lo * unit:+.5f}, {d_hi * unit:+.5f}]"
            )
    print(decile_table(gc, y, "GC decile"))
    print(decile_table(dev, y, "|GC-.5| dec."))
    buckets = np.clip(runs, 0, 8)
    print(f"  {'longest run':>14} | {'mean cov':>8} | {'zero %':>7} | {'n':>6}")
    for r in sorted(set(buckets.tolist())):
        sel = buckets == r
        print(
            f"  run {r:>11} | {y[sel].mean():8.2f} | {100 * np.mean(y[sel] == 0):7.3f} | {int(sel.sum()):6d}"
        )


def microsoft() -> None:
    clusters = realdata.load_microsoft("train")
    refs = [c.reference for c in clusters]
    counts = np.array([len(c.reads) for c in clusters])
    analyze(
        "Microsoft Nanopore",
        refs,
        counts,
        "Centers.txt lists every designed strand, Clusters.txt has an (often empty) cluster for each",
    )


def dnaformer(stem: str) -> None:
    path = realdata.DNAFORMER_DIR / f"{stem}.txt"
    if not path.exists():
        print(f"\n=== {stem}: file missing, run scripts/download_data.sh --full")
        return
    if not DESIGN_FILE.exists():
        print(f"\n=== {stem}: {DESIGN_FILE.name} missing, cannot tell designed strands from sequenced ones")
        return
    design = [s for s in DESIGN_FILE.read_text().split() if not realdata.is_heldout_reference(s)]
    observed: Counter[str] = Counter()
    for cluster in realdata._iter_dnaformer(path):
        observed[cluster.reference] += len(cluster.reads)
    unknown = set(observed) - set(design) - {s for s in DESIGN_FILE.read_text().split()}
    counts = np.array([observed.get(s, 0) for s in design])
    note = (
        f"designed strands from {DESIGN_FILE.name}; {len(observed)} references appear in the file"
        + (f"; {len(unknown)} of them are not in the design file" if unknown else "")
    )
    analyze(f"DNAformer {stem}", design, counts, note)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-microsoft", action="store_true")
    parser.add_argument(
        "--dnaformer-file",
        action="append",
        default=["BinnedNanoporeSecondFlowcell_Random", "BinnedTestIllumina_Random"],
    )
    args = parser.parse_args(argv)
    if not args.skip_microsoft:
        microsoft()
    for stem in args.dnaformer_file:
        dnaformer(stem)
    print("\nAll numbers are lower bounds for archival dropout: these samples are fresh, and")
    print("strands whose reads exist but were never binned count as missing here.")


if __name__ == "__main__":
    main()
