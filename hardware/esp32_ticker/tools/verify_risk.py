#!/usr/bin/env python3
"""Diff the on-device risk model against dnacodec.risk on real strands.

The risk model outputs a probability, not a string, so the test is different from the
decoder's: what matters is that the number on the chip is the same number Python gives, and
that the ranking is unchanged, because ranking is what the encoder actually uses when it
chooses between candidate strands.

    uv run python hardware/esp32_ticker/tools/verify_risk.py --strands 400
"""

from __future__ import annotations

import argparse
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dnacodec.encoder import encode  # noqa: E402
from dnacodec.risk import spearman  # noqa: E402
from dnacodec.types import EncoderSettings  # noqa: E402
from dnacodec import demo as demo_mod  # noqa: E402

CKPT = REPO_ROOT / "checkpoints" / "risk" / "risk_nanopore_budget.pkl"


def build(out: Path) -> Path:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if cxx is None:
        raise SystemExit("no C++ compiler found")
    src = HERE.parent / "src"
    subprocess.run([cxx, "-std=c++17", "-O2", "-I", str(src), "-o", str(out),
                    str(HERE / "risk_main.cpp"), str(src / "box_risk.cpp")], check=True)
    return out


def strands_for(n: int, strand_length: int) -> list[str]:
    """Real encoder output, plus deliberately awkward strands so the range is covered."""
    data = demo_mod.demo_image_png(64)
    enc = encode(data, EncoderSettings(strand_length=strand_length, redundancy=0.8), None)
    out = list(enc.strands[:n])
    rng = np.random.default_rng(0)
    while len(out) < n:  # homopolymers and skewed GC, where the model should be confident
        kind = rng.integers(0, 3)
        if kind == 0:
            s = "".join(rng.choice(list("ACGT"), strand_length))
        elif kind == 1:
            s = ("A" * int(rng.integers(4, 12))).ljust(strand_length, "C")[:strand_length]
        else:
            s = "".join(rng.choice(list("GC"), strand_length))
        out.append(s)
    return out[:n]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strands", type=int, default=400)
    ap.add_argument("--strand-length", type=int, default=110)
    ap.add_argument("--checkpoint", default=str(CKPT))
    args = ap.parse_args(argv)

    with Path(args.checkpoint).open("rb") as f:
        model = pickle.load(f)

    strands = strands_for(args.strands, args.strand_length)
    binary = build(Path("/tmp/erbgut_verify_risk"))
    payload = "".join(s + "\n" for s in strands)
    proc = subprocess.run([str(binary)], input=payload, capture_output=True, text=True,
                          check=True)
    dev = np.array([float(x) for x in proc.stdout.split()], dtype=np.float64)
    py = np.asarray(model(strands), dtype=np.float64)
    if len(dev) != len(py):
        raise SystemExit(f"device returned {len(dev)} scores for {len(py)} strands")

    diff = np.abs(dev - py)
    order_dev = np.argsort(-dev)
    order_py = np.argsort(-py)
    top = max(1, len(py) // 10)
    overlap = len(set(order_dev[:top].tolist()) & set(order_py[:top].tolist())) / top

    print(f"\nstrands                        {len(py)}")
    print(f"mean absolute difference       {diff.mean():.3e}")
    print(f"largest difference             {diff.max():.3e}")
    print(f"within 0.01                    {(diff < 0.01).mean() * 100:.2f}%")
    print(f"spearman rank correlation      {spearman(dev, py):.6f}")
    print(f"riskiest 10%, same strands     {overlap * 100:.1f}%")
    print(f"\nrisk range on device           {dev.min():.3f} to {dev.max():.3f}")
    print(f"risk range in python           {py.min():.3f} to {py.max():.3f}")
    return 0 if diff.max() < 0.02 else 1


if __name__ == "__main__":
    raise SystemExit(main())
