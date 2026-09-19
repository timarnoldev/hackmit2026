"""Training CLI for the transformer decoder.

    uv run python -m dnacodec.model.train --smoke
    uv run python -m dnacodec.model.train --source real --steps 30000 --batch-size 128
    uv run python -m dnacodec.model.train --source sim  --steps 100000 --batch-size 128 --workers 8
    uv run python -m dnacodec.model.train --source mixed --init checkpoints/sim/best.pt ...

Data discipline:
- Training uses the real TRAIN splits and simulate() seeds from train_seed() only.
- A validation subset is carved out of the real train split. It is excluded from training
  and is the only thing used to pick best.pt.
- The real held-out split is decoded at every eval and only logged, never used for selection.

Outputs in checkpoints/<run-name>/: train.log, metrics.jsonl, best.pt (best validation),
last.pt (every --ckpt-minutes and at the end, with optimizer state for --resume),
step<N>.pt snapshots every --ckpt-minutes.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np
import torch
import torch.nn.functional as F

from .. import realdata
from ..evaluate import evaluate
from ..seeds import train_seed
from ..types import Cluster, Strand
from .data import (
    IGNORE,
    MAX_READS,
    Batch,
    ClusterPool,
    MixedSource,
    SimSource,
    Source,
    batch_loader,
    split_train_val,
)
from .decoder import predict
from .net import PRESETS, ConsensusNet, ModelConfig, count_parameters, load_checkpoint, pick_device, save_checkpoint

CHECKPOINT_DIR = Path(__file__).resolve().parents[2] / "checkpoints"
EVAL_COVERAGES = (2, 4, 6, 10, 16)  # same budgets as scripts/eval_real.py

log = logging.getLogger("dnacodec.model")


def setup_logging(log_file: Path | None = None) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.propagate = False


# ---------------------------------------------------------------- metrics


def strand_accuracy(
    references: Sequence[Strand], decoded: Sequence[Strand | None], clusters: Sequence[Cluster]
) -> float:
    """Strand accuracy from dnacodec.evaluate, the only place metrics are computed."""
    return float(evaluate(references, decoded, clusters).strand_accuracy)


def coverage_accuracy(
    model: ConsensusNet,
    references: Sequence[Strand],
    clusters: Sequence[Cluster],
    coverages: Sequence[int] = EVAL_COVERAGES,
    batch_size: int = 256,
) -> dict[str, float]:
    """Strand accuracy when each cluster is cut to its first k reads (deterministic, no seed).
    Clusters with fewer than k reads keep all of them; empty clusters count as wrong."""
    result = {}
    lengths = sorted({len(r) for r in references})
    for k in coverages:
        cut = [[r for r in c if r][:k] for c in clusters]
        decoded: list[Strand | None] = [None] * len(references)
        for s in lengths:  # predict() takes one strand length per call
            idx = [i for i, r in enumerate(references) if len(r) == s]
            for i, d in zip(idx, predict(model, [cut[i] for i in idx], s, batch_size)):
                decoded[i] = d
        m = evaluate(references, decoded, cut)
        result[f"cov{k}"] = float(m.strand_accuracy)
        result[f"edit{k}"] = float(m.mean_edit_distance)
    result["mean"] = float(np.mean([result[f"cov{k}"] for k in coverages]))
    return result


# ---------------------------------------------------------------- training core


def loss_on(model: ConsensusNet, batch: Batch) -> torch.Tensor:
    logits = model(batch.reads, batch.lengths)
    return F.cross_entropy(logits.reshape(-1, 4).float(), batch.targets.reshape(-1), ignore_index=IGNORE)


def lr_at(step: int, total: int, warmup: int, base_lr: float, final_frac: float = 0.1) -> float:
    if step < warmup:
        return base_lr * (step + 1) / warmup
    t = min(1.0, (step - warmup) / max(1, total - warmup))
    return base_lr * (final_frac + (1 - final_frac) * 0.5 * (1 + math.cos(math.pi * t)))


def make_optimizer(model: ConsensusNet, lr: float, weight_decay: float = 0.01) -> torch.optim.Optimizer:
    decay = [p for n, p in model.named_parameters() if p.ndim >= 2 and "emb" not in n]
    no_decay = [p for n, p in model.named_parameters() if not (p.ndim >= 2 and "emb" not in n)]
    return torch.optim.AdamW(
        [{"params": decay, "weight_decay": weight_decay}, {"params": no_decay, "weight_decay": 0.0}],
        lr=lr, betas=(0.9, 0.98),
    )


def run_steps(
    model: ConsensusNet,
    batches: Iterator[Batch],
    optimizer: torch.optim.Optimizer,
    total_steps: int,
    lr: float,
    warmup: int,
    start_step: int = 0,
    grad_clip: float = 1.0,
    log_every: int = 50,
    on_step: Callable[[int, float], bool] | None = None,
) -> float:
    """Train from start_step to total_steps. on_step(step, loss) returning True stops early.
    Returns the exponential moving average of the training loss."""
    device = next(model.parameters()).device
    use_amp = device.type == "cuda"
    model.train()
    ema = None
    t0, last_log = time.time(), start_step
    for step in range(start_step, total_steps):
        for group in optimizer.param_groups:
            group["lr"] = lr_at(step, total_steps, warmup, lr)
        batch = next(batches).to(device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=use_amp):
            loss = loss_on(model, batch)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
        optimizer.step()
        value = float(loss.detach())
        ema = value if ema is None else 0.98 * ema + 0.02 * value
        done = step + 1
        if log_every and (done % log_every == 0 or done == total_steps):
            rate = (done - last_log) / max(1e-9, time.time() - t0)
            log.info(f"step {done}/{total_steps} loss {value:.4f} ema {ema:.4f} "
                     f"lr {optimizer.param_groups[0]['lr']:.2e} {rate:.2f} it/s")
            t0, last_log = time.time(), done
        if on_step is not None and on_step(done, ema):
            break
    return float(ema if ema is not None else float("nan"))


# ---------------------------------------------------------------- data setup


def load_real_train(sets: Sequence[str]) -> dict[str, tuple[list[Strand], list[Cluster]]]:
    """Real TRAIN splits only. 'microsoft' or 'dnaformer:<file stem>'."""
    out = {}
    for name in sets:
        if name == "microsoft":
            data = realdata.load_microsoft("train")
        elif name.startswith("dnaformer:"):
            data = realdata.load_dnaformer(name.split(":", 1)[1], "train")
        else:
            raise ValueError(f"unknown real set {name!r}")
        out[name] = ([c.reference for c in data], [c.reads for c in data])
    return out


def load_real_heldout(sets: Sequence[str], n: int) -> dict[str, tuple[list[Strand], list[Cluster]]]:
    """Held-out split, ONLY for logging. First n clusters of each set."""
    out = {}
    for name in sets:
        if name == "microsoft":
            data = realdata.load_microsoft("heldout")
        else:
            data = realdata.load_dnaformer(name.split(":", 1)[1], "heldout")
        data = data[:n]
        out[name] = ([c.reference for c in data], [c.reads for c in data])
    return out


# ---------------------------------------------------------------- main training


def train(args: argparse.Namespace) -> Path:
    run_dir = Path(args.checkpoint_dir) / args.run_name
    setup_logging(run_dir / "train.log")
    device = pick_device(args.device)
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
    log.info(f"run {args.run_name} on {device}, args {vars(args)}")

    # Data. Validation is carved from real TRAIN data and excluded from training.
    real_sets = [s for s in args.real_sets.split(",") if s]
    real = load_real_train(real_sets)
    pools: list[Source] = []
    val: dict[str, tuple[list[Strand], list[Cluster]]] = {}
    for name, (refs, clusters) in real.items():
        pool, val[name] = split_train_val(refs, clusters, args.val_size, seed=args.seed)
        if args.limit_clusters:
            pool = ClusterPool(pool.references[: args.limit_clusters], pool.clusters[: args.limit_clusters])
        log.info(f"real {name}: {len(pool)} train clusters, {len(val[name][0])} validation clusters")
        pools.append(pool)
    real_source: Source | None = None
    if pools:
        real_source = pools[0] if len(pools) == 1 else MixedSource(pools, [len(p) for p in pools])  # type: ignore[arg-type]
    if args.source == "real":
        if real_source is None:
            raise SystemExit("--source real needs --real-sets")
        source: Source = real_source
    elif args.source == "sim":
        source = SimSource(args.sim_min_length, args.sim_max_length)
    elif args.source == "mixed":
        if real_source is None:
            raise SystemExit("--source mixed needs --real-sets")
        source = MixedSource([SimSource(args.sim_min_length, args.sim_max_length), real_source],
                             [1 - args.p_real, args.p_real])
    else:
        raise SystemExit(f"unknown source {args.source}")
    heldout = load_real_heldout(real_sets or ["microsoft"], args.heldout_n) if args.heldout_n > 0 else {}

    # Model and optimizer.
    start_step, best = 0, -1.0
    opt_state = None
    if args.resume:
        model, ckpt = load_checkpoint(args.resume, device)
        start_step, best = ckpt.get("step", 0), ckpt.get("best_val", -1.0)
        opt_state = ckpt.get("optimizer")
        log.info(f"resumed from {args.resume} at step {start_step}")
    elif args.init:
        model, _ = load_checkpoint(args.init, device)
        log.info(f"initialized weights from {args.init}")
    else:
        cfg = ModelConfig(**{**PRESETS[args.model].to_dict(), **({"dropout": args.dropout} if args.dropout is not None else {})})
        model = ConsensusNet(cfg).to(device)
    log.info(f"model {model.cfg.to_dict()}, {count_parameters(model) / 1e6:.2f}M parameters")
    optimizer = make_optimizer(model, args.lr)
    if opt_state is not None:
        optimizer.load_state_dict(opt_state)

    batches = batch_loader(source, args.batch_size, train_seed(args.seed), workers=args.workers)
    metrics_file = (run_dir / "metrics.jsonl").open("a")
    t_start = time.time()
    last_ckpt = time.time()
    state = {"best": best, "step": start_step}

    def evaluate_now(step: int, train_loss: float) -> dict:
        t = time.time()
        rec: dict = {"step": step, "train_loss": train_loss, "elapsed_min": (time.time() - t_start) / 60}
        for name, (refs, clusters) in val.items():
            rec[f"val/{name}"] = coverage_accuracy(model, refs, clusters)
        for name, (refs, clusters) in heldout.items():
            rec[f"heldout/{name}"] = coverage_accuracy(model, refs, clusters)
        # Validation strand accuracy averaged over budgets; the small edit distance term only
        # breaks ties (e.g. early on, when every exact accuracy is still 0).
        if val:
            sel = float(np.mean([
                rec[f"val/{n}"]["mean"]
                - 1e-4 * np.mean([rec[f"val/{n}"][f"edit{c}"] for c in EVAL_COVERAGES])
                for n in val
            ]))
        else:
            sel = -train_loss
        rec["select"] = sel
        model.train()
        parts = [f"{name} acc(edit) " + " ".join(f"{c}r={d[f'cov{c}']:.3f}({d[f'edit{c}']:.2f})"
                                                   for c in EVAL_COVERAGES) + f" mean={d['mean']:.3f}"
                 for name, d in rec.items() if isinstance(d, dict)]
        log.info(f"eval step {step} ({time.time() - t:.0f}s): " + " | ".join(parts))
        metrics_file.write(json.dumps(rec) + "\n")
        metrics_file.flush()
        return rec

    def checkpoint(name: str, step: int, rec: dict | None, with_opt: bool) -> None:
        extra = {"step": step, "metrics": rec, "best_val": state["best"], "source": args.source,
                 "args": vars(args)}
        if with_opt:
            extra["optimizer"] = optimizer.state_dict()
        save_checkpoint(run_dir / name, model, **extra)

    def on_step(step: int, ema: float) -> bool:
        nonlocal last_ckpt
        state["step"] = step
        rec = None
        if step % args.eval_every == 0 or step == args.steps:
            rec = evaluate_now(step, ema)
            if rec["select"] > state["best"]:
                state["best"] = rec["select"]
                checkpoint("best.pt", step, rec, with_opt=False)
                log.info(f"new best validation {state['best']:.4f} at step {step} -> best.pt")
        if time.time() - last_ckpt >= args.ckpt_minutes * 60 or step == args.steps:
            checkpoint("last.pt", step, rec, with_opt=True)
            if step != args.steps:
                checkpoint(f"step{step}.pt", step, rec, with_opt=False)
            last_ckpt = time.time()
            log.info(f"checkpoint at step {step}")
        return False

    try:
        run_steps(model, batches, optimizer, args.steps, args.lr, args.warmup, start_step,
                  log_every=args.log_every, on_step=on_step)
    except KeyboardInterrupt:
        log.info("interrupted, saving last.pt")
        checkpoint("last.pt", state["step"], None, with_opt=True)
    metrics_file.close()
    log.info(f"done in {(time.time() - t_start) / 60:.1f} min, best validation {state['best']:.4f}")
    return run_dir / "best.pt"


# ---------------------------------------------------------------- smoke test


def smoke(args: argparse.Namespace) -> float:
    """Memorization check: tiny model, 200 real train clusters, full coverage (up to 16 reads).
    Train loss must go near zero; then the same clusters are decoded through the checkpoint."""
    run_dir = Path(args.checkpoint_dir) / "smoke"
    setup_logging(run_dir / "train.log")
    device = pick_device(args.device)
    data = realdata.load_microsoft("train")[:200]
    pool = ClusterPool([c.reference for c in data], [c.reads for c in data])
    model = ConsensusNet(PRESETS["tiny"]).to(device)
    log.info(f"smoke: {len(pool)} clusters, {count_parameters(model) / 1e3:.0f}K parameters, device {device}")
    optimizer = make_optimizer(model, 2e-3, weight_decay=0.0)
    batches = batch_loader(pool, 32, train_seed(args.seed), min_coverage=MAX_READS)
    t0 = time.time()
    steps = args.steps if args.steps_given else 3000
    ema = run_steps(model, batches, optimizer, steps, 2e-3, 100, log_every=100,
                    on_step=lambda step, ema: ema < 0.02)
    minutes = (time.time() - t0) / 60
    save_checkpoint(run_dir / "smoke.pt", model, step=-1, source="smoke")
    from .decoder import TransformerDecoder

    dec = TransformerDecoder(run_dir / "smoke.pt", device=str(device))
    decoded = dec.decode(pool.clusters, 110)
    acc = strand_accuracy(pool.references, decoded, pool.clusters)
    ok = ema < 0.05
    log.info(f"smoke {'PASSED' if ok else 'FAILED'}: train loss ema {ema:.4f} after {minutes:.1f} min, "
             f"decoded the 200 training clusters via TransformerDecoder, strand accuracy {acc:.3f}")
    return ema


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--smoke", action="store_true", help="tiny memorization check on 200 clusters")
    p.add_argument("--source", choices=["real", "sim", "mixed"], default="real")
    p.add_argument("--real-sets", default="microsoft",
                   help="comma list: microsoft, dnaformer:<file stem> (train split only)")
    p.add_argument("--p-real", type=float, default=0.5, help="share of real batches for --source mixed")
    p.add_argument("--sim-min-length", type=int, default=90)
    p.add_argument("--sim-max-length", type=int, default=140)
    p.add_argument("--model", choices=sorted(PRESETS), default="base")
    p.add_argument("--dropout", type=float, default=None)
    p.add_argument("--steps", type=int, default=50_000)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--lr", type=float, default=3e-4)
    p.add_argument("--warmup", type=int, default=1000)
    p.add_argument("--workers", type=int, default=0, help="DataLoader worker processes")
    p.add_argument("--eval-every", type=int, default=1000)
    p.add_argument("--log-every", type=int, default=50)
    p.add_argument("--val-size", type=int, default=500, help="validation clusters carved from each train set")
    p.add_argument("--heldout-n", type=int, default=2000, help="held-out clusters to log (0 = none)")
    p.add_argument("--limit-clusters", type=int, default=0, help="debug: cap training clusters")
    p.add_argument("--ckpt-minutes", type=float, default=30.0)
    p.add_argument("--checkpoint-dir", default=str(CHECKPOINT_DIR))
    p.add_argument("--run-name", default=None)
    p.add_argument("--resume", default=None, help="continue from a last.pt")
    p.add_argument("--init", default=None, help="start from these weights, fresh optimizer")
    p.add_argument("--device", default=None)
    p.add_argument("--seed", type=int, default=0)
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.steps_given = "--steps" in (argv if argv is not None else sys.argv)
    if args.smoke:
        smoke(args)
        return
    if args.run_name is None:
        args.run_name = f"{args.source}_{args.model}_{time.strftime('%m%d_%H%M')}"
    train(args)


if __name__ == "__main__":
    main()
