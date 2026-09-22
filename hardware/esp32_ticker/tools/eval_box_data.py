#!/usr/bin/env python3
"""Evaluate the box's own embedded dataset, the way the box itself counts it.

tools/verify_polish.py generates fresh clusters and skips the ones with no reads, because it
is comparing decoders. The box does neither: it plays the 300 records compiled into
box_data.h, and a strand that lost every read still counts as a strand that did not come
back. That difference alone moves the headline percentage, so this script measures the
dataset the device actually plays, with the device's own denominator, and prints both.

    uv run python hardware/esp32_ticker/tools/eval_box_data.py
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rapidfuzz.distance import Levenshtein  # noqa: E402

DATA = HERE.parent / "src" / "box_data.h"


def build(out: Path) -> Path:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if cxx is None:
        raise SystemExit("no C++ compiler found")
    src = HERE.parent / "src"
    subprocess.run([cxx, "-std=c++17", "-O2", "-I", str(src), "-o", str(out),
                    str(HERE / "verify_decoder.cpp"), str(src / "box_decoder.cpp"),
                    str(src / "box_polish.cpp"), str(src / "box_risk.cpp")], check=True)
    return out


def records(path: Path):
    """Parse box_data.h back into (reads, reference) per strand, in order."""
    text = path.read_text()
    strand_len = int(re.search(r"#define BOX_DATA_STRAND_LENGTH (\d+)", text).group(1))
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith('"i|'):
            continue
        body = line.strip(",").strip('"')
        reads, reference = [], ""
        for part in body.split("\\n"):
            if part.startswith("f|"):
                reads.append(part[2:])
            elif part.startswith("x|"):
                reference = part[2:]
        out.append((reads, reference))
    return out, strand_len


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DATA))
    args = ap.parse_args(argv)

    recs, strand_len = records(Path(args.data))
    print(f"{len(recs)} records, strand length {strand_len}")
    dropouts = sum(1 for reads, _ in recs if not reads)
    print(f"strands with no reads at all: {dropouts} ({100.0 * dropouts / len(recs):.1f}%)")

    ref_lens = {len(r) for _, r in recs if r}
    print(f"reference lengths present: {sorted(ref_lens)}")

    binary = build(Path("/tmp/erbgut_eval_box"))
    live = [(reads, ref) for reads, ref in recs if reads]
    payload = "".join(f"{strand_len}\t" + "\t".join(reads) + "\n" for reads, _ in live)
    proc = subprocess.run([str(binary)], input=payload, capture_output=True, text=True,
                          check=True)
    lines = [ln for ln in proc.stdout.split("\n") if ln != ""]
    if len(lines) != len(live):
        raise SystemExit(f"{len(lines)} results for {len(live)} clusters")

    exact_p = exact_c = 0
    edits_p = edits_c = 0
    fixes = 0
    fell_back = 0
    bases = 0
    for (reads, ref), line in zip(live, lines):
        parts = line.split("\t")
        classic, _draft, polished = parts[0], parts[1], parts[2]
        if not polished:
            fell_back += 1
            polished = classic
        exact_p += int(polished == ref)
        exact_c += int(classic == ref)
        edits_p += Levenshtein.distance(polished, ref)
        edits_c += Levenshtein.distance(classic, ref)
        fixes += Levenshtein.distance(classic, polished)
        bases += len(ref)

    n_live = len(live)
    n_all = len(recs)
    print(f"\npolisher declined and fell back to classic on {fell_back} strands")
    print(f"total model edits over the whole dataset: {fixes} "
          f"({fixes / n_live:.2f} per decoded strand)")

    print("\nover the strands that had reads, which is how verify_polish.py counts:")
    print(f"  exact, polisher   {exact_p}/{n_live}  ({100.0 * exact_p / n_live:.2f}%)")
    print(f"  exact, classic    {exact_c}/{n_live}  ({100.0 * exact_c / n_live:.2f}%)")

    print("\nover every record, which is how the box counts, dropouts included:")
    print(f"  exact, polisher   {exact_p}/{n_all}  ({100.0 * exact_p / n_all:.2f}%)")
    print(f"  exact, classic    {exact_c}/{n_all}  ({100.0 * exact_c / n_all:.2f}%)")

    print("\nper base, the number a storage system actually cares about:")
    print(f"  polisher          {100.0 * (1 - edits_p / bases):.2f}%")
    print(f"  classic           {100.0 * (1 - edits_c / bases):.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
