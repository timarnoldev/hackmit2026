"""Training data for the transformer decoder.

Three kinds of source, all producing (references, clusters) pairs:
- ClusterPool: fixed clusters, e.g. the real Microsoft or DNAformer train split, or the
  clusters the loop hands to finetune(). Only ever built from split="train" data.
- SimSource: random references pushed through dnacodec.simulator.simulate() with random
  profiles over a wide range of error rates. Every simulate() seed goes through train_seed().
- MixedSource: picks one of several sources per batch.

make_batch() then applies the coverage augmentation (random cluster size 1..16) and turns
everything into padded integer tensors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

import numpy as np
import torch

from ..profiles import SituationProfile
from ..seeds import HELDOUT_COUNT, HELDOUT_START, train_seed
from ..types import ALPHABET, Cluster, Strand

PAD = 4  # token id for padding (and for any non-ACGT character)
IGNORE = -100  # target id for positions beyond a strand's length
MAX_READS = 16
READ_LEN_FACTOR = 1.15

_LUT = np.full(256, PAD, dtype=np.int64)
for _i, _c in enumerate(ALPHABET):
    _LUT[ord(_c)] = _i
    _LUT[ord(_c.lower())] = _i

SimulateFn = Callable[[Sequence[Strand], SituationProfile, int], list[Cluster]]


def read_len_for(strand_length: int) -> int:
    """Reads are padded or truncated to this many bases."""
    return math.ceil(READ_LEN_FACTOR * strand_length)


def tokenize(s: str) -> np.ndarray:
    return _LUT[np.frombuffer(s.encode("ascii", "replace"), dtype=np.uint8)]


def select_reads(
    cluster: Cluster,
    strand_length: int,
    max_reads: int = MAX_READS,
    rng: np.random.Generator | None = None,
    k: int | None = None,
) -> list[str]:
    """Choose which reads go into the model.

    Training (rng given): k random reads (k defaults to max_reads), in random order.
    Inference (rng None): if the cluster is too large, keep the max_reads reads whose length
    is closest to strand_length (deterministic, stable order); otherwise all reads.
    """
    reads = [r for r in cluster if r]
    k = max_reads if k is None else min(k, max_reads)
    if rng is not None:
        n = min(k, len(reads))
        idx = rng.choice(len(reads), size=n, replace=False) if reads else []
        return [reads[i] for i in idx]
    if len(reads) <= k:
        return reads
    order = sorted(range(len(reads)), key=lambda i: (abs(len(reads[i]) - strand_length), i))
    return [reads[i] for i in sorted(order[:k])]


@dataclass
class Batch:
    reads: torch.Tensor  # (B, R, L) long, PAD where there is no base
    lengths: torch.Tensor  # (B,) long, strand length per example
    targets: torch.Tensor | None = None  # (B, S_max) long, IGNORE beyond the strand length

    def to(self, device: torch.device | str) -> Batch:
        return Batch(
            self.reads.to(device, non_blocking=True),
            self.lengths.to(device, non_blocking=True),
            None if self.targets is None else self.targets.to(device, non_blocking=True),
        )


def encode_batch(
    read_lists: Sequence[Sequence[str]],
    strand_lengths: Sequence[int],
    references: Sequence[Strand] | None = None,
) -> Batch:
    """Pad already selected reads (at most MAX_READS per cluster) into tensors."""
    b = len(read_lists)
    r = max(1, max((len(x) for x in read_lists), default=1))
    s_max = max(strand_lengths)
    L = read_len_for(s_max)
    reads = np.full((b, r, L), PAD, dtype=np.int64)
    for i, rl in enumerate(read_lists):
        for j, read in enumerate(rl):
            t = tokenize(read[:L])
            reads[i, j, : len(t)] = t
    targets = None
    if references is not None:
        targets = np.full((b, s_max), IGNORE, dtype=np.int64)
        for i, ref in enumerate(references):
            if len(ref) != strand_lengths[i]:
                raise ValueError(f"reference length {len(ref)} != strand length {strand_lengths[i]}")
            targets[i, : len(ref)] = tokenize(ref)
        targets = torch.from_numpy(targets)
    return Batch(torch.from_numpy(reads), torch.tensor(list(strand_lengths), dtype=torch.long), targets)


def make_batch(
    references: Sequence[Strand],
    clusters: Sequence[Cluster],
    rng: np.random.Generator,
    max_reads: int = MAX_READS,
    min_coverage: int = 1,
    max_coverage: int = MAX_READS,
    low_coverage_prob: float = 0.5,
    low_coverage_max: int = 6,
) -> Batch:
    """Training batch with coverage augmentation: each cluster keeps k random reads (capped by
    what it has). k is uniform in [min_coverage, low_coverage_max] with probability
    low_coverage_prob, else uniform in [min_coverage, max_coverage]. Low coverage (2 to 6
    reads) is where the majority vote baseline is weak, so it gets extra weight."""
    read_lists = []
    low_max = max(min_coverage, min(low_coverage_max, max_coverage))
    for ref, cluster in zip(references, clusters):
        hi = low_max if rng.random() < low_coverage_prob else max_coverage
        k = int(rng.integers(min_coverage, hi + 1))
        read_lists.append(select_reads(cluster, len(ref), max_reads, rng, k=k))
    return encode_batch(read_lists, [len(r) for r in references], references)


class Source(Protocol):
    def sample(self, n: int, rng: np.random.Generator) -> tuple[list[Strand], list[Cluster]]:
        """n non-empty clusters and their references."""
        ...


class ClusterPool:
    """A fixed set of (reference, cluster) pairs. Empty clusters are dropped."""

    def __init__(self, references: Sequence[Strand], clusters: Sequence[Cluster]):
        if len(references) != len(clusters):
            raise ValueError("references and clusters differ in length")
        keep = [i for i, c in enumerate(clusters) if any(c)]
        if not keep:
            raise ValueError("no non-empty clusters")
        self.references = [references[i] for i in keep]
        self.clusters = [list(clusters[i]) for i in keep]

    def __len__(self) -> int:
        return len(self.references)

    def sample(self, n: int, rng: np.random.Generator) -> tuple[list[Strand], list[Cluster]]:
        idx = rng.integers(0, len(self.references), size=n)
        return [self.references[i] for i in idx], [self.clusters[i] for i in idx]


def random_profile(rng: np.random.Generator, coverage_mean: float = 20.0) -> SituationProfile:
    """A random channel covering a wide range: total error rate log-uniform in 0.2%..15%,
    split randomly between substitutions, insertions and deletions. No dropouts (empty
    clusters teach nothing), high coverage (make_batch subsamples it anyway)."""
    total = math.exp(rng.uniform(math.log(0.002), math.log(0.15)))
    sub, ins, dele = rng.dirichlet([1.5, 1.5, 1.5]) * total
    return SituationProfile(
        name="random_train",
        description="random training channel",
        technology="nanopore" if rng.random() < 0.5 else "illumina",
        sub_rate=float(sub),
        ins_rate=float(ins),
        del_rate=float(dele),
        homopolymer_factor=float(rng.uniform(1.0, 1.6)),
        end_factor=float(rng.uniform(1.0, 2.0)),
        dropout_rate=0.0,
        gc_dropout_factor=0.0,
        coverage_mean=coverage_mean,
        coverage_dispersion=float(rng.uniform(2.0, 10.0)),
        storage_years=0.0,
        decay_per_year=0.0,
        synthesis_usd_per_base=0.0,
        sequencing_usd_per_read=0.0,
    )


def random_strands(n: int, length: int, rng: np.random.Generator) -> list[Strand]:
    letters = np.frombuffer(ALPHABET.encode(), dtype=np.uint8)
    arr = letters[rng.integers(0, 4, size=(n, length))]
    return [row.tobytes().decode() for row in arr]


def _sim_seed(rng: np.random.Generator) -> int:
    """A simulate() seed that is never in the held-out range, still checked by train_seed."""
    seed = int(rng.integers(HELDOUT_START + HELDOUT_COUNT, 2**31 - 1))
    return train_seed(seed)


class SimSource:
    """Random references through the channel simulator with a random profile per batch."""

    def __init__(
        self,
        min_length: int = 90,
        max_length: int = 140,
        simulate_fn: SimulateFn | None = None,
        profile_fn: Callable[[np.random.Generator], SituationProfile] = random_profile,
    ):
        if simulate_fn is None:
            from ..simulator import simulate as simulate_fn  # Agent A
        self.simulate = simulate_fn
        self.min_length, self.max_length = min_length, max_length
        self.profile_fn = profile_fn

    def sample(self, n: int, rng: np.random.Generator) -> tuple[list[Strand], list[Cluster]]:
        length = int(rng.integers(self.min_length, self.max_length + 1))
        profile = self.profile_fn(rng)
        refs_out: list[Strand] = []
        clusters_out: list[Cluster] = []
        for _ in range(10):  # top up if simulate dropped some strands
            refs = random_strands(n, length, rng)
            clusters = self.simulate(refs, profile, _sim_seed(rng))
            for ref, c in zip(refs, clusters):
                if any(c) and len(refs_out) < n:
                    refs_out.append(ref)
                    clusters_out.append(c)
            if len(refs_out) == n:
                break
        if not refs_out:
            raise RuntimeError("simulator returned only empty clusters")
        return refs_out, clusters_out


class MixedSource:
    """Picks one source per batch with the given probabilities."""

    def __init__(self, sources: Sequence[Source], weights: Sequence[float]):
        self.sources = list(sources)
        w = np.asarray(weights, dtype=float)
        self.weights = w / w.sum()

    def sample(self, n: int, rng: np.random.Generator) -> tuple[list[Strand], list[Cluster]]:
        i = int(rng.choice(len(self.sources), p=self.weights))
        return self.sources[i].sample(n, rng)


class BatchStream(torch.utils.data.IterableDataset):
    """Endless stream of training batches. Each DataLoader worker gets its own train seed."""

    def __init__(self, source: Source, batch_size: int, seed: int, **batch_kwargs):
        super().__init__()
        self.source, self.batch_size, self.seed = source, batch_size, seed
        self.batch_kwargs = batch_kwargs

    def __iter__(self):
        info = torch.utils.data.get_worker_info()
        worker = 0 if info is None else info.id + 1
        rng = np.random.default_rng(train_seed(self.seed * 1000 + worker))
        while True:
            refs, clusters = self.source.sample(self.batch_size, rng)
            yield make_batch(refs, clusters, rng, **self.batch_kwargs)


def batch_loader(source: Source, batch_size: int, seed: int, workers: int = 0, **batch_kwargs):
    stream = BatchStream(source, batch_size, seed, **batch_kwargs)
    if workers <= 0:
        return iter(stream)
    loader = torch.utils.data.DataLoader(
        stream, batch_size=None, num_workers=workers, persistent_workers=True, prefetch_factor=4
    )
    return iter(loader)


def split_train_val(
    references: Sequence[Strand], clusters: Sequence[Cluster], val_size: int, seed: int = 0
) -> tuple[ClusterPool, tuple[list[Strand], list[Cluster]]]:
    """Carve a validation subset out of TRAIN data (for checkpoint selection)."""
    rng = np.random.default_rng(train_seed(seed))
    perm = rng.permutation(len(references))
    val_idx, train_idx = perm[:val_size], perm[val_size:]
    pool = ClusterPool([references[i] for i in train_idx], [clusters[i] for i in train_idx])
    val = ([references[i] for i in val_idx], [list(clusters[i]) for i in val_idx])
    return pool, val
