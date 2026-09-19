"""TransformerDecoder: the dnacodec.types.Decoder backed by a trained checkpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import torch

from ..types import ALPHABET, Cluster, Strand
from .data import encode_batch, select_reads
from .net import ConsensusNet, load_checkpoint, pick_device

_LETTERS = np.frombuffer(ALPHABET.encode(), dtype=np.uint8)


@torch.no_grad()
def predict(
    model: ConsensusNet,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 256,
    max_batch_reads: int = 1024,
) -> list[Strand | None]:
    """Decode clusters with a model. None for clusters without any read.

    Clusters larger than the model's max_reads keep the reads closest to strand_length.
    A chunk holds at most batch_size clusters and at most max_batch_reads reads in total
    (cross-attention memory grows with reads per chunk; 256 clusters x 16 reads on a laptop
    GPU was 50x slower than 1024 reads per chunk).
    """
    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    out: list[Strand | None] = [None] * len(clusters)
    todo = [i for i, c in enumerate(clusters) if any(c)]
    # Similar cluster sizes together keeps padding small.
    todo.sort(key=lambda i: min(len(clusters[i]), model.cfg.max_reads))
    start = 0
    while start < len(todo):
        width = min(len(clusters[todo[start]]), model.cfg.max_reads)
        n = max(1, min(batch_size, max_batch_reads // max(width, 1)))
        chunk = todo[start : start + n]
        chunk_width = min(len(clusters[chunk[-1]]), model.cfg.max_reads)
        while len(chunk) > 1 and len(chunk) * chunk_width > max_batch_reads:
            chunk = chunk[: max(1, max_batch_reads // chunk_width)]
            chunk_width = min(len(clusters[chunk[-1]]), model.cfg.max_reads)
        start += len(chunk)
        read_lists = [select_reads(clusters[i], strand_length, model.cfg.max_reads) for i in chunk]
        batch = encode_batch(read_lists, [strand_length] * len(chunk)).to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
            logits = model(batch.reads, batch.lengths)
        pred = logits.argmax(-1).cpu().numpy()
        for i, row in zip(chunk, pred):
            out[i] = _LETTERS[row[:strand_length]].tobytes().decode()
    model.train(was_training)
    return out


class TransformerDecoder:
    """Loads a checkpoint written by dnacodec.model.train or finetune."""

    name = "transformer"
    # Evaluation calls decode() once, in the main process, with all clusters of all trials.
    # predict() processes them in bounded chunks, so only one chunk is on the GPU at a time.
    main_process_only = True

    def __init__(
        self,
        checkpoint_path: str | Path,
        device: str | None = None,
        batch_size: int = 256,
        max_batch_reads: int = 1024,
    ):
        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.model.eval()
        self.batch_size = batch_size
        self.max_batch_reads = max_batch_reads
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics", "source")}

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return predict(self.model, clusters, strand_length, self.batch_size, self.max_batch_reads)
