"""Run the alternating loop for one situation.

    uv run python -m scripts.run_loop --profile nanopore_budget --run-id r1 [--quick] [--workers 20]
    uv run python -m scripts.run_loop --profile nanopore_budget --run-id r1 \\
        --decoder transformer --checkpoint checkpoints/mixed_ft/best.pt

Recommended for run2 (nanopore_budget): the calibrated Nanopore channel decodes only about half
the strands at 6 reads, so redundancies below ~0.8 can't meet the target there and only cost
screening time. Restrict its grid:

    uv run python -m scripts.run_loop --profile nanopore_budget --run-id run2 --workers 15 \\
        --redundancy 0.8,1.0,1.3,1.6,2.0

If nothing in the grid (plus the fallback redundancies) meets the target on train seeds, the
loop records the best-effort setting with "TARGET NOT MET ON TRAIN SEEDS" in its notes and
still evaluates it and computes min reads; it never crashes on this.

Progress goes to results/<run_id>/log.txt and stdout. Results: results/<run_id>/<profile>.json
(after every alternation). Frozen codecs for run_experiments: checkpoints/loop/<run_id>/.

--mock NAMES replaces components that are not on main yet with simple stand-ins
(trials, risk, all). Anything mocked marks the RunResult is_mock=True, so the dashboard
shows its MOCK banner. The mocks live here, not in the library, and are also used by tests.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from typing import Sequence

import numpy as np

from dnacodec.encoder import encode, max_run_length, payload_bits_per_base, recover
from dnacodec.loop import Components, LoopConfig, make_decoder, run_loop
from dnacodec.profiles import SituationProfile, load_profile
from dnacodec.results import RESULTS_DIR
from dnacodec.types import ALPHABET, Decoder, EncoderSettings, Metrics, Strand

# ---------------------------------------------------------------- mocks
#
# Mock channel + decoder: a strand fails with a probability that grows with the profile's
# error rate, its longest homopolymer (via homopolymer_factor) and GC skew, and falls with
# the number of reads it got. Encoding and recovery are the real Fountain code, so
# redundancy, strand length and the risk scorer act on the file exactly as they would.

MOCK_DECODER_SCALE = 1.0  # roughly: nanopore_budget at 6 reads gives ~55-60% strand accuracy


def _gc(s: str) -> float:
    return (s.count("G") + s.count("C")) / max(1, len(s))


def _max_runs(strands: Sequence[Strand]) -> np.ndarray:
    """Longest homopolymer per strand, vectorized for equal-length batches."""
    if not strands:
        return np.zeros(0)
    length = len(strands[0])
    if length == 0 or any(len(s) != length for s in strands):
        return np.array([max_run_length(s) for s in strands], dtype=np.float64)
    arr = np.frombuffer("".join(strands).encode(), dtype=np.uint8).reshape(len(strands), length)
    run = np.ones(len(strands))
    best = run.copy()
    for j in range(1, length):
        run = np.where(arr[:, j] == arr[:, j - 1], run + 1, 1.0)
        np.maximum(best, run, out=best)
    return best


def _gc_dev(strands: Sequence[Strand]) -> np.ndarray:
    return np.array([abs(_gc(s) - 0.5) for s in strands])


def mock_strand_difficulty(strands: Sequence[Strand], profile: SituationProfile) -> np.ndarray:
    e = profile.sub_rate + profile.ins_rate + profile.del_rate
    runs = _max_runs(strands)
    gc_dev = _gc_dev(strands)
    lengths = np.array([len(s) for s in strands], dtype=np.float64)
    hp = profile.homopolymer_factor ** np.maximum(runs - 2, 0) ** 1.5
    return MOCK_DECODER_SCALE * e * lengths * hp * (1 + 2 * gc_dev)


def mock_fail_probability(
    strands: Sequence[Strand], profile: SituationProfile, reads: np.ndarray, difficulty: np.ndarray | None = None
) -> np.ndarray:
    d = mock_strand_difficulty(strands, profile) if difficulty is None else difficulty
    return np.where(reads > 0, np.exp(-reads / np.maximum(d, 1e-9)), 1.0)


def _mock_reads(rng: np.random.Generator, n: int, profile: SituationProfile) -> np.ndarray:
    survive = rng.random(n) >= profile.total_dropout
    return np.where(survive, rng.poisson(profile.coverage_mean, n), 0)


class MockTrialRunner:
    """Stand-in for dnacodec.evaluate.recovery_trials (same signature)."""

    def __init__(self) -> None:
        self._cache: dict = {}
        self.calls: list[tuple[EncoderSettings, list[int], str]] = []

    def __call__(self, data, settings, scorer, decoder, profile, seeds, workers=None, *,
                 simulator=None, encoded=None, per_trial=False) -> Metrics:
        """simulator= is ignored: the mock channel has nothing to swap."""
        seeds = list(seeds)
        self.calls.append((settings, seeds, profile.name))
        if encoded is not None:
            if encoded.meta.settings != settings or encoded.meta.n_bytes != len(data):
                raise ValueError("encoded file does not match settings or data")  # same contract as evaluate
            enc = encoded
        else:
            key = (bytes(data[:32]), len(data), settings, id(scorer))
            if key not in self._cache:
                self._cache[key] = (encode(data, settings, scorer), scorer)
            enc = self._cache[key][0]
        n = len(enc.strands)
        difficulty = mock_strand_difficulty(enc.strands, profile)
        recovered = 0
        correct = 0
        reads_total = 0
        dropped = 0
        trial_acc: list[float] = []
        trial_rec: list[bool] = []
        for seed in seeds:
            rng = np.random.default_rng(seed)
            reads = _mock_reads(rng, n, profile)
            ok = rng.random(n) >= mock_fail_probability(enc.strands, profile, reads, difficulty)
            decoded = [s if k else None for s, k in zip(enc.strands, ok)]
            ok_file = recover(decoded, enc.meta) == data
            recovered += ok_file
            trial_rec.append(bool(ok_file))
            trial_acc.append(float(ok.mean()))
            correct += int(ok.sum())
            reads_total += int(reads.sum())
            dropped += int((reads == 0).sum())
        t = len(seeds)
        rate = recovered / t
        acc = correct / (n * t)
        return Metrics(
            n_strands=n * t,
            strand_accuracy=acc,
            mean_edit_distance=(1 - acc) * 3,
            dropout_rate=dropped / (n * t),
            reads_per_strand=reads_total / (n * t),
            per_position_error=[(1 - acc) * 0.03] * settings.strand_length,
            file_recovered=rate == 1.0,
            bits_per_base=payload_bits_per_base(enc.meta, n),
            recovery_rate=rate,
            n_trials=t,
            extra={"mock": 1.0, **({"trial_strand_accuracy": trial_acc, "trial_recovered": trial_rec}
                                   if per_trial else {})},
        )


def mock_min_reads(runner):
    """Stand-in for dnacodec.evaluate.min_reads_at_target built on a trial runner."""

    def min_reads(data, settings, scorer, decoder, profile, seeds, target, coverages, workers=None, **kw):
        for c in sorted(coverages):
            m = runner(data, settings, scorer, decoder, replace(profile, coverage_mean=float(c)), seeds, workers, **kw)
            if (m.recovery_rate or 0.0) >= target:
                return float(c)
        return None

    return min_reads


def mock_generate_strands(n: int, length: int, seed: int) -> list[Strand]:
    """Uniform strands plus strands with planted homopolymers and GC skew."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        kind = i % 3
        if kind == 2:  # GC skew
            p_gc = rng.uniform(0.2, 0.8)
            p = np.array([1 - p_gc, p_gc, p_gc, 1 - p_gc]) / 2
            s = "".join(rng.choice(list(ALPHABET), length, p=p))
        else:
            s = "".join(rng.choice(list(ALPHABET), length))
            if kind == 1:  # planted run
                run = int(rng.integers(3, 9))
                pos = int(rng.integers(0, length - run))
                s = s[:pos] + ALPHABET[int(rng.integers(4))] * run + s[pos + run :]
        out.append(s)
    return out


def mock_label_failure_rates(strands, profile, decoder, k, seed, workers=None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = len(strands)
    fails = np.zeros(n)
    difficulty = mock_strand_difficulty(strands, profile)
    for _ in range(k):
        reads = _mock_reads(rng, n, profile)
        fails += rng.random(n) < mock_fail_probability(strands, profile, reads, difficulty)
    return fails / k


def _features(strands: Sequence[Strand]) -> np.ndarray:
    runs = _max_runs(strands)
    gc = _gc_dev(strands)
    return np.stack([np.ones(len(strands)), runs, np.maximum(runs - 3, 0) ** 1.5, gc, gc**2], axis=1)


class MockRiskModel:
    """Least squares on (max run, run excess, GC deviation). Stand-in for dnacodec.risk.RiskModel."""

    def __init__(self) -> None:
        self.w: np.ndarray | None = None

    def fit(self, strands: Sequence[Strand], failed: np.ndarray) -> MockRiskModel:
        self.w, *_ = np.linalg.lstsq(_features(strands), np.asarray(failed, dtype=np.float64), rcond=None)
        return self

    def __call__(self, strands: Sequence[Strand]) -> np.ndarray:
        assert self.w is not None, "fit first"
        return np.clip(_features(strands) @ self.w, 0.0, 1.0)

    def top_kmers(self, k: int = 6, n: int = 20) -> list[tuple[str, float]]:
        background = "ACGT" * 40
        kmers = [b * k for b in ALPHABET] + ["GC" * (k // 2), "AT" * (k // 2), "GGCGGC"[:k], "AATTAA"[:k]]
        scored = [(km, float(self([background[:50] + km + background[: 60 - k]])[0])) for km in kmers]
        return sorted(scored, key=lambda x: -x[1])[:n]


def mock_train_risk(strands, labels, seed) -> MockRiskModel:
    return MockRiskModel().fit(strands, labels)


def mock_components(which: Sequence[str] = ("all",)) -> Components:
    which = set(which)
    if "all" in which:
        which = {"trials", "risk"}
    unknown = which - {"trials", "risk"}
    if unknown:
        raise ValueError(f"unknown mock components {sorted(unknown)}")
    comps = Components(mocked=tuple(sorted(which)))
    if "trials" in which:
        runner = MockTrialRunner()
        comps.recovery_trials = runner
        comps.min_reads_at_target = mock_min_reads(runner)
    if "risk" in which:
        comps.generate_strands = mock_generate_strands
        comps.train_risk = mock_train_risk
        # With the real channel, label with the real simulator and decoder; else the mock channel.
        comps.label_failure_rates = mock_label_failure_rates if "trials" in which else real_channel_labels
    return comps


def real_channel_labels(strands, profile, decoder: Decoder, k, seed, workers=None) -> np.ndarray:
    """Failure rates from the real simulator and decoder (used when only the risk model is mocked)."""
    from dnacodec.evaluate import evaluate
    from dnacodec.simulator import simulate

    by_len: dict[int, list[int]] = {}
    for i, s in enumerate(strands):
        by_len.setdefault(len(s), []).append(i)
    fails = np.zeros(len(strands))
    for j in range(k):
        clusters = simulate(strands, profile, seed + j)
        for length, idx in by_len.items():
            dec = decoder.decode([clusters[i] for i in idx], length)
            for i, d in zip(idx, dec):
                m = evaluate([strands[i]], [d], [clusters[i]])
                fails[i] += m.strand_accuracy < 1.0
    return fails / k


# ---------------------------------------------------------------- CLI


def setup_logging(run_id: str) -> None:
    path = RESULTS_DIR / run_id / "log.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.FileHandler(path, mode="a"), logging.StreamHandler(sys.stdout)):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def parse_mock(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--decoder", choices=("baseline", "transformer"), default="baseline")
    ap.add_argument("--checkpoint", help="transformer checkpoint (with --decoder transformer)")
    ap.add_argument("--quick", action="store_true", help="tiny trial budgets for tests and demos")
    ap.add_argument("--workers", type=int, default=None, help="CPU workers for trials (default: all cores)")
    ap.add_argument("--alternations", type=int, default=3, help="at most 3")
    ap.add_argument("--mock", default=None, help="comma list of components to mock: trials, risk, all")
    ap.add_argument("--eval-trials", type=int, default=None, help="override held-out trials per evaluation")
    ap.add_argument("--no-refine", action="store_true", help="don't test c - 0.5 after the coverage grid")
    ap.add_argument("--redundancy", default=None,
                    help="redundancy grid override; for nanopore_budget use 0.8,1.0,1.3,1.6,2.0 (see module doc)")
    ap.add_argument("--lengths", default=None, help="strand length grid override, e.g. 110,140")
    args = ap.parse_args(argv)

    setup_logging(args.run_id)
    profile = load_profile(args.profile)
    config = LoopConfig.quick_mode(args.workers) if args.quick else LoopConfig(workers=args.workers)
    if args.eval_trials:
        config = replace(config, eval_trials=args.eval_trials)
    if args.no_refine:
        config = replace(config, refine_half_step=False)
    if args.redundancy:
        config = replace(config, grid=replace(config.grid, redundancy=tuple(float(x) for x in args.redundancy.split(","))))
    if args.lengths:
        config = replace(config, grid=replace(config.grid, strand_length=tuple(int(x) for x in args.lengths.split(","))))
    mocks = parse_mock(args.mock)
    # Import through the package so pickled mock risk models load from scripts.run_loop, not __main__.
    from scripts.run_loop import mock_components as package_mocks

    comps = package_mocks(mocks) if mocks else Components()
    decoder = make_decoder(args.decoder, args.checkpoint)
    run = run_loop(profile, args.run_id, EncoderSettings(), args.alternations,
                   config=config, components=comps, decoder=decoder)
    d, best = run.default_metrics, run.best
    print(
        f"{profile.name}: default {d.bits_per_base:.3f} bits/base, recovery {d.recovery_rate}, "
        f"min reads {run.default_min_reads_at_target} | tailored {best.metrics.bits_per_base:.3f} bits/base, "
        f"recovery {best.metrics.recovery_rate}, min reads {best.min_reads_at_target} | {best.settings}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
