#!/usr/bin/env python3
"""Diff the on-device decoder against dnacodec.baseline on real clusters.

The box and the Mac must agree about what was decoded, or the demo quietly shows one thing
while the numbers describe another. Levenshtein alignment has ties, and two implementations
can pick different but equally optimal alignments, which changes the vote columns and so the
consensus. This is the check that says they do not.

Run:
    uv run python hardware/esp32_ticker/tools/verify_decoder.py
    uv run python hardware/esp32_ticker/tools/verify_decoder.py --clusters 500
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rapidfuzz.distance import Levenshtein  # noqa: E402
from dnacodec.baseline import MAX_READS, _pick_draft, reconstruct, subsample  # noqa: E402
from dnacodec.encoder import encode  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402
from dnacodec.types import EncoderSettings  # noqa: E402
from dnacodec import demo as demo_mod  # noqa: E402


def build(out: Path) -> Path:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if cxx is None:
        raise SystemExit("no C++ compiler found, cannot verify the on-device decoder")
    cmd = [cxx, "-std=c++17", "-O2", "-I", str(HERE.parent / "src"), "-o", str(out),
           str(HERE / "verify_decoder.cpp"), str(HERE.parent / "src" / "box_decoder.cpp"),
           str(HERE.parent / "src" / "box_polish.cpp"), str(HERE.parent / "src" / "box_risk.cpp")]
    subprocess.run(cmd, check=True)
    return out


def clusters_for(n: int, channel: str, reads: float, strand_length: int):
    """Real clusters with their true references, the same way the ticker makes them."""
    data = demo_mod.demo_image_png(64)
    settings = EncoderSettings(strand_length=strand_length, redundancy=0.8)
    profile = dc_replace(load_profile(channel), coverage_mean=reads)
    out = []
    seed = 0
    while len(out) < n:
        encoded = encode(data, settings, None)
        for ref, cluster in zip(encoded.strands, simulate(encoded.strands, profile,
                                                          train_seed(seed))):
            live = [r for r in cluster if r]
            if live:
                out.append((live, ref))
            if len(out) >= n:
                break
        seed += 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clusters", type=int, default=300)
    ap.add_argument("--channel", default="nanopore_budget")
    ap.add_argument("--reads", type=float, default=10.0)
    ap.add_argument("--strand-length", type=int, default=110)
    ap.add_argument("--show", type=int, default=3, help="how many disagreements to print")
    args = ap.parse_args(argv)

    binary = build(Path("/tmp/erbgut_verify_decoder"))
    clusters = clusters_for(args.clusters, args.channel, args.reads, args.strand_length)

    payload = "".join(f"{args.strand_length}\t" + "\t".join(c) + "\n" for c, _ref in clusters)
    proc = subprocess.run([str(binary)], input=payload, capture_output=True, text=True, check=True)
    lines = [ln for ln in proc.stdout.split("\n") if ln != ""]
    if len(lines) != len(clusters):
        raise SystemExit(f"device decoder returned {len(lines)} lines for {len(clusters)} clusters")

    same = same_draft = dev_exact = py_exact = 0
    dev_edits = py_edits = 0
    shown = 0
    for (cluster, ref), line in zip(clusters, lines):
        dev_cons, _, dev_draft = line.partition("\t")
        py_cons = reconstruct(cluster, args.strand_length)
        py_draft = _pick_draft(subsample(cluster, MAX_READS), args.strand_length)
        if dev_cons == py_cons:
            same += 1
        elif shown < args.show:
            shown += 1
            diff = sum(1 for a, b in zip(dev_cons, py_cons) if a != b)
            print(f"\ndisagreement, {len(cluster)} reads, {diff} positions differ")
            print(f"  device {dev_cons}")
            print(f"  python {py_cons}")
            print(f"  truth  {ref}")
        if dev_draft == py_draft:
            same_draft += 1
        # the number that actually matters: how often each one is exactly right
        dev_exact += int(dev_cons == ref)
        py_exact += int(py_cons == ref)
        dev_edits += Levenshtein.distance(dev_cons, ref)
        py_edits += Levenshtein.distance(py_cons, ref)

    n = len(clusters)
    print(f"\nclusters                      {n}")
    print(f"device and python agree       {same}/{n}  ({100.0 * same / n:.2f}%)")
    print(f"medoid draft agrees           {same_draft}/{n}  ({100.0 * same_draft / n:.2f}%)")
    print()
    print("exact strands against the true reference, which is what accuracy means here:")
    print(f"  on device                   {dev_exact}/{n}  ({100.0 * dev_exact / n:.2f}%)")
    print(f"  python dnacodec.baseline    {py_exact}/{n}  ({100.0 * py_exact / n:.2f}%)")
    print(f"mean edit distance to truth:  device {dev_edits / n:.3f}, python {py_edits / n:.3f}")
    return 0 if dev_exact >= py_exact else 1


if __name__ == "__main__":
    raise SystemExit(main())
