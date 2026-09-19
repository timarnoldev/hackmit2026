"""Loaders for public real sequencing datasets, plus the fixed held-out split.

Microsoft clustered Nanopore reads:
    https://github.com/microsoft/clustered-nanopore-reads-dataset
    Centers.txt has one reference per line. Clusters.txt has a line of "=" before each
    cluster, clusters in the same order as Centers.txt.

DNAformer binned reads (Technion, CC BY 4.0):
    https://zenodo.org/records/17473983
    Each cluster is: reference line, a line of "*", the reads, then two blank lines.

Run scripts/download_data.sh first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from .types import Cluster, Strand

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
MICROSOFT_DIR = DATA_DIR / "microsoft"
DNAFORMER_DIR = DATA_DIR / "dnaformer"

# Every 5th cluster is held out. Never train or tune on these.
HELDOUT_EVERY = 5

Split = Literal["train", "heldout", "all"]


@dataclass
class RealCluster:
    reference: Strand
    reads: Cluster


def _select(clusters: list[RealCluster], split: Split) -> list[RealCluster]:
    if split == "all":
        return clusters
    heldout = split == "heldout"
    return [c for i, c in enumerate(clusters) if (i % HELDOUT_EVERY == 0) == heldout]


def load_microsoft(split: Split = "train", root: Path = MICROSOFT_DIR) -> list[RealCluster]:
    references = (root / "Centers.txt").read_text().split()
    clusters: list[Cluster] = []
    for line in (root / "Clusters.txt").read_text().splitlines():
        line = line.strip()
        if line.startswith("="):
            clusters.append([])
        elif line:
            clusters[-1].append(line)
    if len(clusters) != len(references):
        raise ValueError(f"{len(clusters)} clusters but {len(references)} references")
    return _select([RealCluster(r, c) for r, c in zip(references, clusters)], split)


def _iter_dnaformer(path: Path) -> Iterator[RealCluster]:
    reference: str | None = None
    reads: Cluster = []
    in_reads = False
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line:
                if reference is not None and in_reads:
                    yield RealCluster(reference, reads)
                    reference, reads, in_reads = None, [], False
            elif line.startswith("*"):
                in_reads = True
            elif reference is None:
                reference = line
            else:
                reads.append(line)
    if reference is not None:
        yield RealCluster(reference, reads)


def load_dnaformer(
    name: str = "BinnedNanoporeSecondFlowcell_Random",
    split: Split = "train",
    root: Path = DNAFORMER_DIR,
) -> list[RealCluster]:
    """name is a file stem from the Zenodo record, e.g. BinnedTestIllumina_Random."""
    return _select(list(_iter_dnaformer(root / f"{name}.txt")), split)
