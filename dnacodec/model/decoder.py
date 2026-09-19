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
) -> list[Strand | None]:
    """Decode clusters with a model. None for clusters without any read.

    Clusters larger than the model's max_reads keep the reads closest to strand_length.
    """
    was_training = model.training
    model.eval()
    device = next(model.parameters()).device
    out: list[Strand | None] = [None] * len(clusters)
    todo = [i for i, c in enumerate(clusters) if any(c)]
    # Similar cluster sizes together keeps padding small.
    todo.sort(key=lambda i: min(len(clusters[i]), model.cfg.max_reads))
    for start in range(0, len(todo), batch_size):
        chunk = todo[start : start + batch_size]
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
    # predict() processes them in chunks of batch_size, so only one chunk is on the GPU.
    main_process_only = True

    def __init__(self, checkpoint_path: str | Path, device: str | None = None, batch_size: int = 256):
        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.model.eval()
        self.batch_size = batch_size
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics", "source")}

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return predict(self.model, clusters, strand_length, self.batch_size)
