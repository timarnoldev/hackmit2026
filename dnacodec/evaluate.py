"""Evaluation harness. Owner: Agent C, reviewed by the ML verifier.

The only place Metrics are computed. Everything reported comes from here.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
from rapidfuzz.distance import Levenshtein

from .encoder import payload_bits_per_base
from .profiles import SituationProfile
from .types import Cluster, Decoder, EncoderSettings, FileMeta, Metrics, Scorer, Strand

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

    bits_per_base = None
    write_cost = None
    read_cost = None
    if meta is not None:
        bits_per_base = payload_bits_per_base(meta, n)
        if profile is not None and bits_per_base > 0:
            bases_per_mb = BITS_PER_MB / bits_per_base
            write_cost = bases_per_mb * profile.synthesis_usd_per_base
            strands_per_mb = bases_per_mb / meta.settings.strand_length
            read_cost = strands_per_mb * reads_per_strand * profile.sequencing_usd_per_read

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
    """
    raise NotImplementedError("Agent C")


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
    """
    raise NotImplementedError("Agent C")
