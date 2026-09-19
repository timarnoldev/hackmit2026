"""Evaluation harness. Owner: Agent C, reviewed by the ML verifier.

The only place Metrics are computed. Everything reported comes from here.
"""

from __future__ import annotations

import dataclasses
import math
import os
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Sequence

import numpy as np
from rapidfuzz.distance import Levenshtein

from .encoder import encode, payload_bits_per_base, recover
from .profiles import SituationProfile
from .simulator import simulate
from .types import (
    Cluster,
    Decoder,
    EncodedFile,
    EncoderSettings,
    FileMeta,
    Metrics,
    Scorer,
    Strand,
)

BITS_PER_MB = 8e6  # 1 MB = 10^6 bytes


def evaluate(
    references: Sequence[Strand],
    decoded: Sequence[Strand | None],
    clusters: Sequence[Cluster],
    profile: SituationProfile | None = None,
    meta: FileMeta | None = None,
    file_recovered: bool | None = None,
) -> Metrics:
    """Compare decoded strands to references.

    - strand_accuracy: exact matches / all strands (None and dropouts count as wrong)
    - mean_edit_distance: over strands with a non-None decode (use rapidfuzz Levenshtein)
    - per_position_error: from edit operations, attributed to reference positions
    - bits_per_base and costs: only when meta and profile are given.
      read cost uses the actual mean reads per strand, write cost uses strand_length

    Details the numbers depend on:

    - An empty cluster counts as wrong even if the decoder returned something for it.
    - per_position_error[i] = number of edit operations attributed to reference position i,
      divided by the number of strands with a non-None decode. Substitutions and deletions
      sit at the reference base they touch. An insertion sits at the reference base it is
      inserted in front of; insertions after the last base count at the last base.
      Levenshtein alignments are not unique, so the attribution follows rapidfuzz's editops.
      By construction sum(per_position_error) == mean_edit_distance.
      Its length is the longest reference length; a strand's errors can only land on
      positions of its own reference.
    - Costs per MB of payload: bases_per_mb = 8e6 / bits_per_base,
      write = bases_per_mb * synthesis_usd_per_base,
      read = (bases_per_mb / strand_length) * reads_per_strand * sequencing_usd_per_read.
    """
    n = len(references)
    if len(decoded) != n or len(clusters) != n:
        raise ValueError(
            f"length mismatch: {n} references, {len(decoded)} decoded, {len(clusters)} clusters"
        )
    if n == 0:
        raise ValueError("nothing to evaluate")

    max_len = max(len(r) for r in references)
    position_errors = np.zeros(max_len, dtype=np.float64)
    exact = 0
    n_decoded = 0
    total_distance = 0

    for reference, guess, cluster in zip(references, decoded, clusters):
        if guess is None:
            continue
        n_decoded += 1
        if guess == reference and len(cluster) > 0:
            exact += 1
        ops = Levenshtein.editops(reference, guess)
        total_distance += len(ops)
        last = len(reference) - 1
        for op in ops:
            # insert src_pos can equal len(reference) (insertion after the last base)
            pos = min(op.src_pos, last) if last >= 0 else 0
            position_errors[pos] += 1

    cluster_sizes = np.array([len(c) for c in clusters], dtype=np.float64)
    dropout_rate = float(np.mean(cluster_sizes == 0))
    reads_per_strand = float(np.mean(cluster_sizes))

    if n_decoded:
        mean_edit_distance = total_distance / n_decoded
        per_position_error = (position_errors / n_decoded).tolist()
    else:
        mean_edit_distance = float("nan")
        per_position_error = [float("nan")] * max_len

    bits_per_base, write_cost, read_cost = _density_and_costs(meta, n, profile, reads_per_strand)

    return Metrics(
        n_strands=n,
        strand_accuracy=exact / n,
        mean_edit_distance=float(mean_edit_distance),
        dropout_rate=dropout_rate,
        reads_per_strand=reads_per_strand,
        per_position_error=per_position_error,
        file_recovered=file_recovered,
        bits_per_base=bits_per_base,
        write_cost_usd_per_mb=write_cost,
        read_cost_usd_per_mb=read_cost,
        extra={"decoded_fraction": n_decoded / n},
    )


def recovery_trials(
    data: bytes,
    settings: EncoderSettings,
    scorer: Scorer | None,
    decoder: Decoder,
    profile: SituationProfile,
    seeds: Sequence[int],
    workers: int | None = None,
) -> Metrics:
    """File recovery over independent channel trials. Owner: Agent C.

    Encode data once with settings and scorer (encoding is deterministic). Then for each seed:
    simulate(strands, profile, seed), decoder.decode, encoder.recover, compare to data exactly.
    Returns Metrics with recovery_rate = fraction of trials recovered, n_trials = len(seeds),
    file_recovered = (recovery_rate == 1.0), strand metrics pooled over all trials via evaluate(),
    bits_per_base and costs from the encoded file.

    Trials run in parallel across CPU cores (workers=None uses all cores); results must be
    identical to a serial run. The caller decides train vs held-out seeds.

    Implementation notes:
    - Metrics.n_strands is the number of strands of the encoded file (one trial), not the
      pooled count; extra["n_strand_decodes"] holds the pooled count.
    - Pooled strand metrics: every (trial, strand) pair is one row in evaluate().
    - The decoder is pickled to the worker processes once. A decoder that cannot be pickled
      or must stay in this process (e.g. one holding a GPU) needs workers=1.
    - Workers use the platform's default multiprocessing start method; with spawn (macOS)
      the calling script needs an `if __name__ == "__main__":` guard.
    """
    encoded = encode(data, settings, scorer)
    with _TrialRunner(encoded, data, decoder, workers, len(seeds)) as runner:
        return runner.run(profile, seeds)


def min_reads_at_target(
    data: bytes,
    settings: EncoderSettings,
    scorer: Scorer | None,
    decoder: Decoder,
    profile: SituationProfile,
    seeds: Sequence[int],
    target: float = 1.0,
    coverages: Sequence[float] = (2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16),
    workers: int | None = None,
) -> float | None:
    """Fewest mean reads per strand at which recovery_rate >= target. Owner: Agent C.

    Evaluates recovery_trials with dataclasses.replace(profile, coverage_mean=c) for c in
    ascending coverages and returns the first c meeting the target (may stop early, and may
    bisect between grid points). Returns None if no coverage meets it.

    Implementation notes:
    - Encodes once and reuses one worker pool for the whole grid. No bisection: the answer
      is always a grid value.
    - Per coverage, trials stop as soon as the failures (counted in seed order) make the
      target unreachable, so the answer is identical to running every trial, serial or
      parallel. A coverage that meets the target always ran all len(seeds) trials.
    - coverage_mean is the mean reads per surviving strand (profile semantics); dropouts
      come on top of that.
    """
    if not 0.0 <= target <= 1.0:
        raise ValueError(f"target={target} must be in [0, 1]")
    if len(seeds) == 0:
        raise ValueError("no seeds")
    max_failures = math.floor((1.0 - target) * len(seeds) + 1e-9)
    encoded = encode(data, settings, scorer)
    with _TrialRunner(encoded, data, decoder, workers, len(seeds)) as runner:
        for c in sorted(coverages):
            m = runner.run(dataclasses.replace(profile, coverage_mean=float(c)), seeds, max_failures)
            failures = m.n_trials - round(m.recovery_rate * m.n_trials)
            if not m.extra.get("early_exit") and failures <= max_failures:
                return float(c)
    return None


def _density_and_costs(
    meta: FileMeta | None,
    n_strands: int,
    profile: SituationProfile | None,
    reads_per_strand: float,
) -> tuple[float | None, float | None, float | None]:
    """bits_per_base, write cost per MB, read cost per MB (see evaluate)."""
    if meta is None:
        return None, None, None
    bits_per_base = payload_bits_per_base(meta, n_strands)
    if profile is None or bits_per_base <= 0:
        return bits_per_base, None, None
    bases_per_mb = BITS_PER_MB / bits_per_base
    write_cost = bases_per_mb * profile.synthesis_usd_per_base
    strands_per_mb = bases_per_mb / meta.settings.strand_length
    read_cost = strands_per_mb * reads_per_strand * profile.sequencing_usd_per_read
    return bits_per_base, write_cost, read_cost


# ---- trial machinery -------------------------------------------------------------------

# (strands, meta, data, decoder) set once per worker process by _init_worker.
_WORKER_STATE: tuple[list[Strand], FileMeta, bytes, Decoder] | None = None


def _init_worker(strands: list[Strand], meta: FileMeta, data: bytes, decoder: Decoder) -> None:
    global _WORKER_STATE
    _WORKER_STATE = (strands, meta, data, decoder)


def _one_trial(
    state: tuple[list[Strand], FileMeta, bytes, Decoder], profile: SituationProfile, seed: int
) -> tuple[list[Strand | None], list[int], bool]:
    """One channel pass. Depends only on (state, profile, seed), so it is deterministic."""
    strands, meta, data, decoder = state
    clusters = simulate(strands, profile, seed)
    decoded = list(decoder.decode(clusters, meta.settings.strand_length))
    if len(decoded) != len(strands):
        raise ValueError(f"decoder returned {len(decoded)} strands for {len(strands)} clusters")
    recovered = recover(decoded, meta) == data
    return decoded, [len(c) for c in clusters], recovered


def _worker_trial(profile: SituationProfile, seed: int):
    assert _WORKER_STATE is not None, "worker not initialized"
    return _one_trial(_WORKER_STATE, profile, seed)


class _TrialRunner:
    """Runs trials for one encoded file, serially or on a process pool kept across calls."""

    def __init__(
        self, encoded: EncodedFile, data: bytes, decoder: Decoder, workers: int | None, n_tasks: int
    ) -> None:
        self.encoded = encoded
        self.state = (list(encoded.strands), encoded.meta, bytes(data), decoder)
        if workers is None:
            workers = os.cpu_count() or 1
        if workers < 1:
            raise ValueError(f"workers={workers} must be >= 1")
        self.workers = min(workers, max(1, n_tasks))
        self.pool: ProcessPoolExecutor | None = None
        if self.workers > 1:
            self.pool = ProcessPoolExecutor(
                max_workers=self.workers, initializer=_init_worker, initargs=self.state
            )

    def __enter__(self) -> _TrialRunner:
        return self

    def __exit__(self, *exc) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)

    def _results(self, profile: SituationProfile, seeds: Sequence[int]):
        """Trial results in seed order, lazily, regardless of completion order."""
        if self.pool is None:
            for seed in seeds:
                yield _one_trial(self.state, profile, seed)
            return
        futures: list[Future] = [self.pool.submit(_worker_trial, profile, s) for s in seeds]
        try:
            for f in futures:
                yield f.result()
        finally:
            for f in futures:
                f.cancel()  # no-op for finished or running tasks

    def run(
        self, profile: SituationProfile, seeds: Sequence[int], max_failures: int | None = None
    ) -> Metrics:
        """All trials, or (with max_failures) stop at the first trial in seed order that
        makes failures exceed max_failures. Deterministic either way."""
        if len(seeds) == 0:
            raise ValueError("no seeds")
        strands = self.state[0]
        decoded_all: list[Strand | None] = []
        clusters_all: list[Cluster] = []  # size stand-ins: evaluate() only uses len(cluster)
        n_run = 0
        n_recovered = 0
        early_exit = False
        results = self._results(profile, seeds)
        try:
            for decoded, sizes, recovered in results:
                n_run += 1
                n_recovered += recovered
                decoded_all.extend(decoded)
                clusters_all.extend([""] * k for k in sizes)
                if max_failures is not None and n_run - n_recovered > max_failures:
                    early_exit = n_run < len(seeds)
                    break
        finally:
            results.close()  # cancels queued trials after an early exit

        pooled = evaluate(strands * n_run, decoded_all, clusters_all)
        meta = self.encoded.meta
        bits, write_cost, read_cost = _density_and_costs(
            meta, len(strands), profile, pooled.reads_per_strand
        )
        rate = n_recovered / n_run
        extra = dict(pooled.extra)
        extra["n_strand_decodes"] = float(len(decoded_all))
        extra["coverage_mean"] = float(profile.coverage_mean)
        if early_exit:
            extra["early_exit"] = 1.0
            extra["n_trials_requested"] = float(len(seeds))
        return dataclasses.replace(
            pooled,
            n_strands=len(strands),
            file_recovered=rate == 1.0,
            bits_per_base=bits,
            write_cost_usd_per_mb=write_cost,
            read_cost_usd_per_mb=read_cost,
            recovery_rate=rate,
            n_trials=n_run,
            extra=extra,
        )
