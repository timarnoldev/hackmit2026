#!/usr/bin/env python3
"""Diff the on-device learned polisher against the Python one, on real clusters.

Three numbers matter, and they separate two different sources of disagreement:

  quantization only   the device's int8 model against the Python float model, both fed the
                      device's own draft and vote columns, so the alignment cannot differ
  end to end          the device's whole pipeline against Python's, where the classic
                      decoder's tied alignments also contribute
  accuracy            exact strands against the true reference, for each of them, which is
                      the only number that says whether any of this is worth running

The device runs the single draft path, the original polish_clusters(). The Mac in host mode
runs the multi draft variant, which is more accurate; that difference is reported too so
nobody reads the box's number as the project's number.

    uv run python hardware/esp32_ticker/tools/verify_polish.py --clusters 300
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402

from dnacodec import demo as demo_mod  # noqa: E402
from dnacodec.baseline import reconstruct  # noqa: E402
from dnacodec.encoder import encode  # noqa: E402
from dnacodec.model.polish import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    apply_edits,
    draft_of,
    features_of,
    load_checkpoint,
)
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402
from dnacodec.types import EncoderSettings  # noqa: E402

CKPT = REPO_ROOT / "checkpoints" / "polish" / "polish.pt"


def build(out: Path) -> Path:
    cxx = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if cxx is None:
        raise SystemExit("no C++ compiler found")
    src = HERE.parent / "src"
    cmd = [cxx, "-std=c++17", "-O2", "-I", str(src), "-o", str(out),
           str(HERE / "verify_decoder.cpp"), str(src / "box_decoder.cpp"),
           str(src / "box_polish.cpp"), str(src / "box_risk.cpp")]
    subprocess.run(cmd, check=True)
    return out


def clusters_for(n: int, channel: str, reads: float, strand_length: int):
    data = demo_mod.demo_image_png(64)
    settings = EncoderSettings(strand_length=strand_length, redundancy=0.8)
    profile = dc_replace(load_profile(channel), coverage_mean=reads)
    out, seed = [], 0
    while len(out) < n:
        enc = encode(data, settings, None)
        for ref, cluster in zip(enc.strands, simulate(enc.strands, profile, train_seed(seed))):
            live = [r for r in cluster if r]
            if live:
                out.append((live, ref))
            if len(out) >= n:
                break
        seed += 1
    return out


def python_polish(model, th, draft, votes, size, strand_length):
    """The Python polisher, given an explicit draft and its votes."""
    feat = features_of(draft, votes)
    with torch.no_grad():
        op_l, ins_l = model(torch.from_numpy(feat[None]).float())
    op_p = op_l[0].softmax(0).numpy()
    ins_p = ins_l[0].softmax(0).numpy()
    sub_t, ins_t = th["low"] if size <= th["low_max_reads"] else th["high"]
    return apply_edits(draft, op_p, ins_p, strand_length, sub_t, ins_t)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clusters", type=int, default=300)
    ap.add_argument("--channel", default="nanopore_budget")
    ap.add_argument("--reads", type=float, default=10.0)
    ap.add_argument("--strand-length", type=int, default=110)
    args = ap.parse_args(argv)

    if not CKPT.exists():
        raise SystemExit(f"no checkpoint at {CKPT}")
    model, ckpt = load_checkpoint(CKPT, "cpu")
    model.eval()
    th = ckpt.get("thresholds") or DEFAULT_THRESHOLDS

    binary = build(Path("/tmp/erbgut_verify_polish"))
    data = clusters_for(args.clusters, args.channel, args.reads, args.strand_length)
    payload = "".join(f"{args.strand_length}\t" + "\t".join(c) + "\n" for c, _ in data)
    proc = subprocess.run([str(binary)], input=payload, capture_output=True, text=True, check=True)
    lines = [ln for ln in proc.stdout.split("\n") if ln != ""]

    n = 0
    q_same_draft = 0        # same draft: isolates quantization from alignment
    same_end = 0            # whole pipeline against whole pipeline
    dev_exact = py_exact = classic_exact = 0
    for (cluster, ref), line in zip(data, lines):
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2]:
            continue
        dev_cons, dev_draft_read, dev_polished = parts[0], parts[1], parts[2]

        py_draft, py_votes = draft_of(cluster, args.strand_length)
        if py_draft is None:
            continue
        py_polished = python_polish(model, th, py_draft, py_votes, py_votes[3],
                                    args.strand_length)

        # feed Python the DEVICE's draft, so only quantization can differ
        from dnacodec.baseline import MAX_READS, _to_array, subsample
        from dnacodec.model.polish import votes_of
        reads = subsample([r for r in cluster if r], MAX_READS)
        arrays = [_to_array(r) for r in reads]
        dev_votes = votes_of(dev_cons, reads, arrays)
        py_on_dev_draft = python_polish(model, th, dev_cons, dev_votes, len(reads),
                                        args.strand_length)

        n += 1
        q_same_draft += int(dev_polished == py_on_dev_draft)
        same_end += int(dev_polished == py_polished)
        dev_exact += int(dev_polished == ref)
        py_exact += int(py_polished == ref)
        classic_exact += int(reconstruct(cluster, args.strand_length) == ref)

    print(f"\nclusters                                  {n}")
    print("same draft and votes, so only quantization can differ:")
    print(f"  device int8 == python float32           {q_same_draft}/{n}  "
          f"({100.0 * q_same_draft / n:.2f}%)")
    print("whole pipeline, where tied alignments also differ:")
    print(f"  device == python                        {same_end}/{n}  "
          f"({100.0 * same_end / n:.2f}%)")
    print("\nexact strands against the true reference:")
    print(f"  polisher on device (int8, one draft)    {dev_exact}/{n}  "
          f"({100.0 * dev_exact / n:.2f}%)")
    print(f"  polisher in python (float, one draft)   {py_exact}/{n}  "
          f"({100.0 * py_exact / n:.2f}%)")
    print(f"  classic majority vote                   {classic_exact}/{n}  "
          f"({100.0 * classic_exact / n:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
