"""Evaluation harness.

The only place Metrics are computed. Everything reported comes from here.
"""

from __future__ import annotations

import dataclasses
import math
import os
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Callable, Sequence

import numpy as np
from rapidfuzz.distance import Levenshtein

from .encoder import encode, payload_bits_per_base, recover
from .profiles import SituationProfile
from .simulator import simulate as default_simulate
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
GATHER_CHUNK_TRIALS = 50  # trials per decode() call for main_process_only decoders

# Signature of dnacodec.simulator.simulate: (strands, profile, seed) -> clusters
Simulator = Callable[[Sequence[Strand], SituationProfile, int], list[Cluster]]


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
    *,
    gather_chunk_trials: int = GATHER_CHUNK_TRIALS,
    simulator: Simulator | None = None,
    encoded: EncodedFile | None = None,
    per_trial: bool = False,
) -> Metrics:
    """File recovery over independent channel trials.

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
    - CPU decoders are pickled to the worker processes once and each trial runs fully in a
      worker. Decoders with main_process_only=True (GPU) stay in this process: trials go in
      chunks of gather_chunk_trials; each chunk is simulated in workers, its clusters
      concatenated and decoded with one decode() call here, then split per trial and
      recovered in workers. Memory: one chunk's clusters are held at a time.
      Results are identical to the per-trial path for any chunk size.
    - simulator (keyword): a function with the signature of dnacodec.simulator.simulate,
      e.g. dnacodec.simulator_b.simulate for the firewall. None = dnacodec.simulator.simulate.
      It is sent to the workers, so it must be picklable (module-level functions are).
    - encoded (keyword): a file already encoded from data with settings. Encoding is then
      skipped and scorer is ignored. Raises ValueError if encoded.meta.settings != settings
      or encoded.meta.n_bytes != len(data).
    - per_trial (keyword): also store per-trial results in extra, as lists in seed order
      (extra is typed dict[str, float]; these two entries are lists of floats instead):
        extra["trial_strand_accuracy"]: evaluate() on each trial alone, so
          mean(trial_strand_accuracy) == strand_accuracy up to float rounding;
        extra["trial_recovered"]: 1.0 if the trial recovered the file exactly, else 0.0, so
          mean(trial_recovered) == recovery_rate.
      No other field changes. recovery_trials never exits early, with or without per_trial.
    - Workers use the platform's default multiprocessing start method; with spawn (macOS)
      the calling script needs an `if __name__ == "__main__":` guard.
    """
    encoded = _encoded_file(data, settings, scorer, encoded)
    with _TrialRunner(
        encoded, data, decoder, workers, len(seeds), gather_chunk_trials, simulator
    ) as runner:
        return runner.run(profile, seeds, per_trial=per_trial)


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
    *,
    gather_chunk_trials: int = GATHER_CHUNK_TRIALS,
    simulator: Simulator | None = None,
    encoded: EncodedFile | None = None,
) -> float | None:
    """Fewest mean reads per strand at which recovery_rate >= target.

    Evaluates recovery_trials with dataclasses.replace(profile, coverage_mean=c) for c in
    ascending coverages and returns the first c meeting the target (may stop early, and may
    bisect between grid points). Returns None if no coverage meets it.

    Implementation notes:
    - Encodes once and reuses one worker pool for the whole grid. No bisection: the answer
      is always a grid value.
    - Per coverage, trials stop as soon as the failures (counted in seed order) make the
      target unreachable, so the answer is identical to running every trial, serial or
      parallel. A coverage that meets the target always ran all len(seeds) trials.
    - main_process_only decoders: trials are decoded in chunks of gather_chunk_trials and
      the early-exit check runs in seed order, so no further chunk is decoded once the target
      is unreachable. The answer matches the per-trial path for any chunk size.
    - simulator and encoded: as in recovery_trials.
    - coverage_mean is the mean reads per surviving strand (profile semantics); dropouts
      come on top of that.
    """
    if not 0.0 <= target <= 1.0:
        raise ValueError(f"target={target} must be in [0, 1]")
    if len(seeds) == 0:
        raise ValueError("no seeds")
    max_failures = math.floor((1.0 - target) * len(seeds) + 1e-9)
    encoded = _encoded_file(data, settings, scorer, encoded)
    with _TrialRunner(
        encoded, data, decoder, workers, len(seeds), gather_chunk_trials, simulator
    ) as runner:
        for c in sorted(coverages):
            m = runner.run(dataclasses.replace(profile, coverage_mean=float(c)), seeds, max_failures)
            failures = m.n_trials - round(m.recovery_rate * m.n_trials)
            if not m.extra.get("early_exit") and failures <= max_failures:
                return float(c)
    return None


def _encoded_file(
    data: bytes, settings: EncoderSettings, scorer: Scorer | None, encoded: EncodedFile | None
) -> EncodedFile:
    """Encode, or check and reuse a pre-encoded file."""
    if encoded is None:
        return encode(data, settings, scorer)
    if encoded.meta.settings != settings:
        raise ValueError(
            f"encoded file was made with {encoded.meta.settings}, not with {settings}"
        )
    if encoded.meta.n_bytes != len(data):
        raise ValueError(
            f"encoded file holds {encoded.meta.n_bytes} bytes but data has {len(data)}"
        )
    return encoded


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

# (strands, meta, data, decoder, simulator) set once per worker process by _init_worker.
# decoder is None in workers when the decoder is main_process_only.
_State = tuple[list[Strand], FileMeta, bytes, "Decoder | None", Simulator]
_WORKER_STATE: _State | None = None


def _init_worker(
    strands: list[Strand],
    meta: FileMeta,
    data: bytes,
    decoder: Decoder | None,
    simulator: Simulator,
) -> None:
    global _WORKER_STATE
    _WORKER_STATE = (strands, meta, data, decoder, simulator)


def _is_main_process_only(decoder: Decoder) -> bool:
    return bool(getattr(decoder, "main_process_only", False))


def _one_trial(
    state: _State, profile: SituationProfile, seed: int
) -> tuple[list[Strand | None], list[int], bool]:
    """One channel pass. Depends only on (state, profile, seed), so it is deterministic."""
    strands, meta, data, decoder, simulate = state
    clusters = simulate(strands, profile, seed)
    decoded = list(decoder.decode(clusters, meta.settings.strand_length))
    if len(decoded) != len(strands):
        raise ValueError(f"decoder returned {len(decoded)} strands for {len(strands)} clusters")
    recovered = recover(decoded, meta) == data
    return decoded, [len(c) for c in clusters], recovered


def _worker_trial(profile: SituationProfile, seed: int):
    assert _WORKER_STATE is not None, "worker not initialized"
    return _one_trial(_WORKER_STATE, profile, seed)


def _worker_simulate(profile: SituationProfile, seed: int) -> list[Cluster]:
    assert _WORKER_STATE is not None, "worker not initialized"
    strands, _, _, _, simulate = _WORKER_STATE
    return simulate(strands, profile, seed)


def _worker_recover(decoded: list[Strand | None]) -> bool:
    assert _WORKER_STATE is not None, "worker not initialized"
    _, meta, data, _, _ = _WORKER_STATE
    return recover(decoded, meta) == data


class _TrialRunner:
    """Runs trials for one encoded file, serially or on a process pool kept across calls."""

    def __init__(
        self,
        encoded: EncodedFile,
        data: bytes,
        decoder: Decoder,
        workers: int | None,
        n_tasks: int,
        gather_chunk_trials: int = GATHER_CHUNK_TRIALS,
        simulator: Simulator | None = None,
    ) -> None:
        if gather_chunk_trials < 1:
            raise ValueError(f"gather_chunk_trials={gather_chunk_trials} must be >= 1")
        self.chunk = gather_chunk_trials
        self.encoded = encoded
        simulator = default_simulate if simulator is None else simulator
        self.state: _State = (list(encoded.strands), encoded.meta, bytes(data), decoder, simulator)
        self.gather = _is_main_process_only(decoder)
        if workers is None:
            workers = os.cpu_count() or 1
        if workers < 1:
            raise ValueError(f"workers={workers} must be >= 1")
        self.workers = min(workers, max(1, n_tasks))
        self.pool: ProcessPoolExecutor | None = None
        if self.workers > 1:
            # a main_process_only decoder never leaves this process
            worker_state = self.state[:3] + ((None if self.gather else decoder), simulator)
            self.pool = ProcessPoolExecutor(
                max_workers=self.workers, initializer=_init_worker, initargs=worker_state
            )

    def __enter__(self) -> _TrialRunner:
        return self

    def __exit__(self, *exc) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)

    def _map(self, fn, args_list: list[tuple]) -> list:
        """fn(*args) for each args, in order; on the pool when there is one."""
        if self.pool is None:
            return [fn(*a) for a in args_list]
        futures = [self.pool.submit(fn, *a) for a in args_list]
        try:
            return [f.result() for f in futures]
        finally:
            for f in futures:
                f.cancel()

    def _gathered_results(self, profile: SituationProfile, seeds: Sequence[int]):
        """main_process_only decoders, lazily per chunk of trials: simulate the chunk in
        workers, decode its clusters with ONE decode() call here, split per trial, recover
        in workers. Same per-trial results as the per-trial path for a decoder that is
        deterministic per cluster. The next chunk is only computed when run() asks for it,
        so an early exit skips the remaining chunks."""
        seeds = list(seeds)
        for start in range(0, len(seeds), self.chunk):
            yield from self._gather_chunk(profile, seeds[start : start + self.chunk])

    def _gather_chunk(self, profile: SituationProfile, seeds: Sequence[int]) -> list:
        strands, meta, data, decoder, simulate = self.state
        if self.pool is None:
            per_trial = [simulate(strands, profile, s) for s in seeds]
        else:
            per_trial = self._map(_worker_simulate, [(profile, s) for s in seeds])
        n = len(strands)
        flat: list[Cluster] = [c for clusters in per_trial for c in clusters]
        sizes = [[len(c) for c in clusters] for clusters in per_trial]
        del per_trial
        decoded_flat = list(decoder.decode(flat, meta.settings.strand_length))
        del flat
        if len(decoded_flat) != n * len(seeds):
            raise ValueError(
                f"decoder returned {len(decoded_flat)} strands for {n * len(seeds)} clusters"
            )
        decoded = [decoded_flat[t * n : (t + 1) * n] for t in range(len(seeds))]
        if self.pool is None:
            recovered = [recover(d, meta) == data for d in decoded]
        else:
            recovered = self._map(_worker_recover, [(d,) for d in decoded])
        return list(zip(decoded, sizes, recovered))

    def _results(self, profile: SituationProfile, seeds: Sequence[int]):
        """Trial results in seed order, lazily, regardless of completion order."""
        if self.gather:
            yield from self._gathered_results(profile, seeds)
            return
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
        self,
        profile: SituationProfile,
        seeds: Sequence[int],
        max_failures: int | None = None,
        per_trial: bool = False,
    ) -> Metrics:
        """All trials, or (with max_failures) stop at the first trial in seed order that
        makes failures exceed max_failures. Deterministic either way.
        per_trial adds per-trial lists to extra and disables early exit."""
        if len(seeds) == 0:
            raise ValueError("no seeds")
        if per_trial:
            max_failures = None
        trial_accuracy: list[float] = []
        trial_recovered: list[float] = []
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
                if per_trial:
                    one = evaluate(strands, decoded, [[""] * k for k in sizes])
                    trial_accuracy.append(one.strand_accuracy)
                    trial_recovered.append(1.0 if recovered else 0.0)
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
        if per_trial:
            extra["trial_strand_accuracy"] = trial_accuracy  # type: ignore[assignment]
            extra["trial_recovered"] = trial_recovered  # type: ignore[assignment]
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
