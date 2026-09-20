"""Live demo round trip: one file through the channel, default codec vs tuned codec, side by side.

    uv run python scripts/demo_roundtrip.py --profile nanopore_budget --reads 6 --run-id run1
    uv run python scripts/demo_roundtrip.py --input photo.png --reads 6 --run-id run1 --seed 3
    uv run python scripts/demo_roundtrip.py --message "hello DNA" --reads 6

Encodes the file with the default codec (fixed hand rules, redundancy 0.3) and with the tuned
codec of a loop run, sends both through the simulated channel at the given reads per strand,
decodes both with the SAME decoder, and tries to recover the file. Prints the comparison and
writes, per seed and codec, a strand map (SVG and PNG: teal = correct, magenta with a cross =
wrong but caught by the checksum, amber with a dashed edge = lost with no reads) and, when the file came
back, the recovered file. Without --input or --message the built-in demo image is used.

Seeds, chosen by rule and not by outcome: without --seed the script runs the first five train
seeds (0, 1, 2, 3, 4; dnacodec.demo.demo_seeds) and reports every one of them. These were fixed
before any demo run. Never search for a seed where the tuned codec wins and show only that one:
a single pass of a small file is a showcase, the evidence is the 300-trial held-out evaluation
in results/<run_id>/<situation>.json, which this script prints next to the demo. --seed runs one
seed of your choice (it must be a train seed; held-out seeds are refused).

Tuned codec: the frozen codec in checkpoints/loop/<run_id>/<situation>/ with its learned risk
model if present; otherwise the settings from results/<run_id>/<situation>.json without the
risk model (the tier-1 codec, which needs none), and the script says so.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):  # allow `python scripts/demo_roundtrip.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import demo  # noqa: E402
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.results import RESULTS_DIR  # noqa: E402
from dnacodec.types import EncoderSettings  # noqa: E402


def _fmt_reads(v: float | None) -> str:
    return "not reached" if v is None else f"{v:g}"


def _row(name: str, r: demo.RoundTripResult) -> str:
    c = r.counts
    verdict = "RECOVERED" if r.ok else "LOST"
    return (f"  {name:<8} {verdict:<10} strands {r.n_strands:>4} (need >= {r.needed:>4})  "
            f"correct {c[demo.CORRECT]:>4}  caught {c[demo.CAUGHT]:>4}  lost {c[demo.LOST]:>3}"
            f"{f'  slipped {c[demo.SLIPPED]}' if c[demo.SLIPPED] else ''}  "
            f"acc {r.strand_accuracy:6.1%}  {r.bits_per_base:.3f} bits/base  "
            f"{r.reads_per_strand:4.2f} reads/strand  {r.total_seconds:5.2f} s")


def _write_outputs(out: Path, stem: str, r: demo.RoundTripResult, codec: str, kind: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"{stem}_seed{r.seed}_{codec}"
    written = []
    svg = base.with_name(base.name + "_strandmap.svg")
    svg.write_text(demo.strand_map_svg(r, title=f"{codec} codec, seed {r.seed}"))
    png = base.with_name(base.name + "_strandmap.png")
    png.write_bytes(demo.strand_map_png(r))
    written += [svg, png]
    if r.ok:
        rec = base.with_name(base.name + "_recovered" + demo.EXTENSION.get(kind, ".bin"))
        rec.write_bytes(r.recovered or b"")
        written.append(rec)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--input", type=Path, help="file to store (default: the built-in demo image)")
    src.add_argument("--message", help="text to store instead of a file")
    ap.add_argument("--profile", default="nanopore_budget")
    ap.add_argument("--reads", type=float, default=None, help="mean reads per strand (default: the profile's budget)")
    ap.add_argument("--run-id", default="run1")
    ap.add_argument("--iteration", type=int, default=None, help="loop iteration to use as the tuned codec")
    ap.add_argument("--seed", type=int, default=None,
                    help=f"one train seed; default: all {demo.DEMO_TRIALS} fixed demo seeds {demo.demo_seeds()}")
    ap.add_argument("--decoder", default="baseline", choices=("baseline", "transformer"))
    ap.add_argument("--checkpoint", default=None, help="transformer checkpoint (with --decoder transformer)")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "demo", help="output folder (gitignored by default)")
    args = ap.parse_args(argv)

    if args.message is not None:
        data, stem = args.message.encode("utf-8"), "message"
    elif args.input is not None:
        data, stem = args.input.read_bytes(), args.input.stem
    else:
        data, stem = demo.demo_image_png(), "demo_mark"
    kind = demo.sniff_kind(data)
    if len(data) > demo.MAX_DEMO_BYTES:
        print(f"note: {len(data):,} bytes is larger than the {demo.MAX_DEMO_BYTES:,} bytes the live demo is sized "
              "for; it will run, just slower.")

    profile = load_profile(args.profile)
    if args.reads is not None:
        profile = replace(profile, coverage_mean=float(args.reads))
    try:
        seeds = [demo.train_seed(args.seed)] if args.seed is not None else demo.demo_seeds()
    except ValueError as e:
        print(f"error: {e}. The demo only uses train seeds.")
        return 2

    from dnacodec.loop import make_decoder

    decoder = make_decoder(args.decoder, args.checkpoint)

    try:
        tuned = demo.load_tuned_codec(args.run_id, args.profile, args.iteration)
    except demo.TunedCodecUnavailable as e:
        tuned = None
        print(f"TUNED CODEC NOT AVAILABLE: {e}\nRunning the default codec only.\n")

    default_settings = tuned.default_settings if tuned and tuned.default_settings else EncoderSettings()
    print(f"File: {stem} ({kind}, {len(data):,} bytes)")
    print(f"Channel: {profile.name}, {profile.coverage_mean:g} mean reads per strand, decoder: {decoder.name} "
          "(the same for both codecs)")
    print(f"Default codec: {demo.describe_settings(default_settings)}, rule scorer")
    if tuned is not None:
        print(f"Tuned codec:   {demo.describe_settings(tuned.settings)}, {demo.scorer_name(tuned.scorer)}")
        print(f"  source: {tuned.note}")
        if tuned.is_mock:
            print("  WARNING: this run is MOCK data, not a real result.")
        if tuned.n_heldout_trials:
            print(f"  The evidence (loop evaluation, {tuned.n_heldout_trials} held-out trials of the 20 KB test file): "
                  f"fewest reads per strand that recover it in every trial: default "
                  f"{_fmt_reads(tuned.default_min_reads)}, tuned {_fmt_reads(tuned.tuned_min_reads)}.")
    print(f"Seeds: {seeds} ({'the fixed demo seeds, all reported' if args.seed is None else 'chosen on the command line'})")
    print()

    tally = {"default": 0, "tuned": 0}
    written: list[Path] = []
    for seed in seeds:
        d = demo.round_trip(data, default_settings, None, decoder, profile, seed, label="default")
        written += _write_outputs(args.out, stem, d, "default", kind)
        tally["default"] += d.ok
        print(f"seed {seed}:")
        print(_row("default", d))
        if tuned is not None:
            t = demo.round_trip(data, tuned.settings, tuned.scorer, decoder, profile, seed, label="tuned")
            written += _write_outputs(args.out, stem, t, "tuned", kind)
            tally["tuned"] += t.ok
            print(_row("tuned", t))
            print(f"  -> {demo.outcome(d, t)}")
        print()

    n = len(seeds)
    print(f"Recovered: default {tally['default']} of {n}" + (f", tuned {tally['tuned']} of {n}" if tuned else ""))
    if tuned is not None:
        print(f"Density: default {d.bits_per_base:.3f} bits per base, tuned {t.bits_per_base:.3f} bits per base.")
        note = demo.tradeoff_note(default_settings, tuned, d.bits_per_base, t.bits_per_base)
        if note:
            print(note)
    print("'need >=' is the number of data chunks: the file can only come back with at least that many correct strands.")
    print(f"Wrote {len(written)} files to {args.out}")
    return 0 if tuned is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
