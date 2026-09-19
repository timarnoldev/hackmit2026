"""Fine-tuning for the loop: adapt a trained checkpoint to one channel.

The loop simulates its current encoding with train seeds and hands the clusters here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..seeds import train_seed
from ..types import Cluster, Strand
from .data import ClusterPool, batch_loader
from .net import load_checkpoint, pick_device, save_checkpoint


def finetune(
    checkpoint_path: str | Path,
    clusters: Sequence[Cluster],
    references: Sequence[Strand],
    steps: int,
    out_path: str | Path,
    *,
    batch_size: int = 64,
    lr: float = 1e-4,
    seed: int = 0,
    device: str | None = None,
    log_every: int = 0,
) -> Path:
    """Fine-tune the model in checkpoint_path on (clusters, references) and save to out_path.

    clusters must come from TRAINING data only (simulate() with train_seed() seeds, or a real
    train split). Coverage augmentation (1 to 16 reads) is applied as in pretraining, so the
    model keeps working at every read budget. Empty clusters are ignored. References may have
    different lengths, up to the model's max_strand_length.
    Returns out_path.
    """
    from .train import make_optimizer, run_steps  # train imports decoder, keep this module light

    dev = pick_device(device)
    model, ckpt = load_checkpoint(checkpoint_path, dev)
    too_long = max(len(r) for r in references)
    if too_long > model.cfg.max_strand_length:
        raise ValueError(f"strand length {too_long} > model max {model.cfg.max_strand_length}")
    pool = ClusterPool(references, clusters)
    batches = batch_loader(pool, batch_size, train_seed(seed))
    optimizer = make_optimizer(model, lr)
    warmup = max(1, min(100, steps // 10))
    loss = run_steps(model, batches, optimizer, steps, lr, warmup, log_every=log_every)
    out_path = Path(out_path)
    save_checkpoint(
        out_path, model, step=steps, source="finetune", parent=str(checkpoint_path),
        metrics={"finetune_train_loss": loss, "n_clusters": len(pool)},
    )
    return out_path
