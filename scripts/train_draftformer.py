"""Train the DraftFormer (dnacodec.model.draftformer).

    uv run --extra train python scripts/train_draftformer.py --steps 40000 --workers 6

Data is generated **on the fly**, not once up front: a pool of worker processes keeps a
queue of freshly simulated batches filled, so the model never sees the same example twice.
The v1 polisher was trained on 577k fixed examples; at 25k steps of batch 192 this recipe
draws about 5M distinct ones for the same wall clock, which is the cheapest lever we have.

Coverage curriculum: every example is subsampled to a random number of reads, weighted hard
toward the low end (that is where we lose), and the weighting can be annealed.

Discipline: only the real TRAIN split and train_seed()s are touched. The checkpoint is
selected on a validation carve of the train split, never on held-out data.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import realdata  # noqa: E402
from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.model.data import random_profile, random_strands, split_train_val  # noqa: E402
from dnacodec.model.draftformer import (  # noqa: E402
    DraftFormer,
    DraftFormerConfig,
    collate,
    count_parameters,
    decode_clusters,
    load_checkpoint,
    pack_example,
    save_checkpoint,
)
from dnacodec.seeds import HELDOUT_COUNT, HELDOUT_START, train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402

log = logging.getLogger("draftformer")
EVAL_COVERAGES = (2, 4, 6, 10, 16)
_REAL: list = []  # per worker: (reference, reads) of the real train clusters minus the carve


def setup_logging(log_file: Path) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)):
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
    log.propagate = False


# ---------------------------------------------------------------- data workers


def _init_worker(indices: list[int]) -> None:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    try:
        torch.set_num_threads(1)
    except Exception:  # pragma: no cover
        pass
    data = realdata.load_microsoft("train")
    _REAL.clear()
    _REAL.extend((data[i].reference, data[i].reads) for i in indices)


def sample_coverage(rng: np.random.Generator, n: int, low_prob: float, low_max: int) -> np.ndarray:
    hi = np.where(rng.random(n) < low_prob, low_max, 16)
    return rng.integers(1, hi + 1)


def _subsample(reads, k: int, rng: np.random.Generator) -> list[str]:
    reads = [r for r in reads if r]
    if len(reads) <= k:
        return reads
    return [reads[i] for i in np.sort(rng.choice(len(reads), size=k, replace=False))]


def make_batch(args):
    """One fresh batch. (seed, size, length, real_share, low_prob, low_max) -> stacked arrays."""
    seed, size, length, real_share, low_prob, low_max = args
    rng = np.random.default_rng(train_seed(seed))
    n_real = int(round(size * real_share)) if (_REAL and length == 110) else 0
    n_sim = size - n_real
    examples = []
    if n_sim:
        profile = random_profile(rng, coverage_mean=float(rng.uniform(4, 25)))
        refs = random_strands(n_sim, length, rng)
        sim_seed = train_seed(int(rng.integers(HELDOUT_START + HELDOUT_COUNT, 2**31 - 1)))
        clusters = simulate(refs, profile, sim_seed)
        ks = sample_coverage(rng, n_sim, low_prob, low_max)
        for ref, cluster, k in zip(refs, clusters, ks):
            reads = _subsample(cluster, int(k), rng)
            if reads:
                ex = pack_example(reads, length, ref)
                if ex is not None:
                    examples.append(ex)
    if n_real:
        pick = rng.integers(0, len(_REAL), size=n_real)
        ks = sample_coverage(rng, n_real, low_prob, low_max)
        for i, k in zip(pick.tolist(), ks):
            ref, reads = _REAL[i]
            picked = _subsample(reads, int(k), rng)
            if picked and len(ref) == length:
                ex = pack_example(picked, length, ref)
                if ex is not None:
                    examples.append(ex)
    if not examples:
        return None
    packs = [e[0] for e in examples]
    stacked = {
        "win": np.stack([p.win for p in packs]),
        "aligned": np.stack([p.aligned for p in packs]),
        "rqual": np.stack([p.rqual for p in packs]),
        "feats": np.stack([p.feats for p in packs]),
        "draft": np.stack([p.draft for p in packs]),
        "n_reads": np.array([p.n_reads for p in packs], dtype=np.int16),
    }
    target = np.stack([e[1] for e in examples])
    ops = np.stack([e[2] for e in examples])
    ins = np.stack([e[3] for e in examples])
    return stacked, target, ops, ins


def to_device(stacked, device):
    kinds = {
        "win": torch.long, "aligned": torch.long, "rqual": torch.float32,
        "feats": torch.float32, "draft": torch.long, "n_reads": torch.long,
    }
    return {
        k: torch.from_numpy(np.ascontiguousarray(v)).to(device=device, dtype=kinds[k])
        for k, v in stacked.items()
    }


class BatchStream:
    """Keeps `depth` batches in flight on a process pool, yields them as they finish."""

    def __init__(self, pool: ProcessPoolExecutor, args, depth: int, seed0: int = 10_000):
        self.pool, self.args, self.depth = pool, args, depth
        self.seed = seed0
        self.futures = [self._submit() for _ in range(depth)]
        self.cursor = 0

    def _submit(self):
        a = self.args
        self.seed += 1
        length = 140 if (self.seed % 5 == 4) else 110
        return self.pool.submit(
            make_batch,
            (train_seed(self.seed), a["size"], length, a["real_share"], a["low_prob"], a["low_max"]),
        )

    def next(self):
        while True:
            fut = self.futures[self.cursor]
            self.futures[self.cursor] = self._submit()
            self.cursor = (self.cursor + 1) % self.depth
            out = fut.result()
            if out is not None:
                return out


# ---------------------------------------------------------------- validation


def _train_indices(val_size: int):
    data = realdata.load_microsoft("train")
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    _, val = split_train_val(refs, clusters, val_size, seed=0)
    val_refs = set(val[0])
    indices = [i for i, c in enumerate(data) if c.reference not in val_refs and any(c.reads)]
    return indices, val


def evaluate_model(model, val, device, coverages=EVAL_COVERAGES, mode="greedy", beams=1,
                   batch_size=512, baseline_cache={}) -> dict:
    refs, clusters = val
    length = len(refs[0])
    result = {}
    for k in coverages:
        cut = [[r for r in c if r][:k] for c in clusters]
        decoded = decode_clusters(model, cut, length, batch_size, device, mode=mode, beams=beams)
        result[f"cov{k}"] = float(evaluate(refs, decoded, cut).strand_accuracy)
        if k not in baseline_cache:
            base = MajorityVoteDecoder().decode(cut, length)
            baseline_cache[k] = float(evaluate(refs, base, cut).strand_accuracy)
        result[f"base{k}"] = baseline_cache[k]
    result["mean"] = float(np.mean([result[f"cov{k}"] for k in coverages]))
    result["base_mean"] = float(np.mean([result[f"base{k}"] for k in coverages]))
    return result


# ---------------------------------------------------------------- training


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--steps", type=int, default=30_000)
    p.add_argument("--batch-size", type=int, default=192)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--depth", type=int, default=12, help="batches kept in flight")
    p.add_argument("--lr", type=float, default=1.2e-3)
    p.add_argument("--warmup", type=int, default=800)
    p.add_argument("--weight-decay", type=float, default=0.02)
    p.add_argument("--real-share", type=float, default=0.25)
    p.add_argument("--low-prob", type=float, default=0.65)
    p.add_argument("--low-max", type=int, default=6)
    p.add_argument("--aux-weight", type=float, default=0.3)
    p.add_argument("--teacher-noise", type=float, default=0.05)
    p.add_argument("--d", type=int, default=192)
    p.add_argument("--d-read", type=int, default=80)
    p.add_argument("--trunk-layers", type=int, default=4)
    p.add_argument("--dec-layers", type=int, default=4)
    p.add_argument("--cross-layers", type=int, default=2)
    p.add_argument("--dropout", type=float, default=0.0)
    p.add_argument("--eval-every", type=int, default=2_000)
    p.add_argument("--val-size", type=int, default=1_000)
    p.add_argument("--minutes", type=float, default=240.0)
    p.add_argument("--run-name", default="draftformer")
    p.add_argument("--checkpoint-dir",
                   default=str(Path(__file__).resolve().parents[1] / "checkpoints"))
    p.add_argument("--device", default=None)
    p.add_argument("--resume", default=None)
    p.add_argument("--compile", action="store_true")
    args = p.parse_args(argv)

    run_dir = Path(args.checkpoint_dir) / args.run_name
    setup_logging(run_dir / "train.log")
    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    log.info(f"draftformer training on {device}, args {vars(args)}")
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    cfg = DraftFormerConfig(
        d=args.d, d_read=args.d_read, trunk_layers=args.trunk_layers,
        dec_layers=args.dec_layers, cross_layers=args.cross_layers,
        dropout=args.dropout,
    )
    model = DraftFormer(cfg).to(device)
    start_step = 0
    if args.resume:
        model, ckpt = load_checkpoint(args.resume, device)
        start_step = int(ckpt.get("step", 0))
        log.info(f"resumed {args.resume} at step {start_step}")
    log.info(f"model {cfg.to_dict()}, {count_parameters(model) / 1e6:.2f}M parameters")

    indices, val = _train_indices(args.val_size)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay,
                            betas=(0.9, 0.95))
    metrics_file = (run_dir / "metrics.jsonl").open("a")
    stream_args = dict(size=args.batch_size, real_share=args.real_share,
                       low_prob=args.low_prob, low_max=args.low_max)
    best = -1.0
    ema = None
    seen = 0
    t0 = time.time()
    amp = device.type == "cuda"

    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(indices,)) as pool:
        stream = BatchStream(pool, stream_args, args.depth, seed0=20_000 + start_step)
        log.info(f"data stream up: {args.workers} workers, {args.depth} batches in flight, "
                 f"{len(indices)} real train clusters")
        for step in range(start_step + 1, args.steps + 1):
            frac = step / max(1, args.steps)
            lr = args.lr * (
                step / args.warmup if step < args.warmup
                else 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * (frac - args.warmup / args.steps)
                                                     / max(1e-6, 1 - args.warmup / args.steps)))
            )
            for g in opt.param_groups:
                g["lr"] = lr
            stacked, target, ops, ins = stream.next()
            batch = to_device(stacked, device)
            y = torch.from_numpy(target.astype(np.int64)).to(device)
            y_op = torch.from_numpy(ops.astype(np.int64)).to(device)
            y_ins = torch.from_numpy(ins.astype(np.int64)).to(device)
            seen += y.shape[0]
            model.train()
            with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
                logits, op_logits, ins_logits = model(batch, y, args.teacher_noise)
                loss_seq = F.cross_entropy(logits.float().transpose(1, 2), y)
                loss_aux = (F.cross_entropy(op_logits.float(), y_op)
                            + F.cross_entropy(ins_logits.float(), y_ins))
                loss = loss_seq + args.aux_weight * loss_aux
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            value = float(loss_seq.detach())
            ema = value if ema is None else 0.98 * ema + 0.02 * value
            if step % 100 == 0:
                log.info(f"step {step}/{args.steps} seq {value:.4f} ema {ema:.4f} lr {lr:.2e} "
                         f"{(step - start_step) / (time.time() - t0):.2f} it/s "
                         f"{seen / 1e6:.2f}M examples")
            over = (time.time() - t0) / 60 > args.minutes
            if step % args.eval_every == 0 or step == args.steps or over:
                rec = {"step": step, "loss": ema, "minutes": (time.time() - t0) / 60,
                       "examples": seen}
                rec.update(evaluate_model(model, val, device))
                log.info("eval step {}: draftformer {} | baseline {}".format(
                    step,
                    " ".join(f"{k}r={rec[f'cov{k}']:.3f}" for k in EVAL_COVERAGES),
                    " ".join(f"{k}r={rec[f'base{k}']:.3f}" for k in EVAL_COVERAGES)))
                metrics_file.write(json.dumps(rec) + "\n")
                metrics_file.flush()
                if rec["mean"] > best:
                    best = rec["mean"]
                    save_checkpoint(run_dir / "draftformer.pt", model, step=step, metrics=rec,
                                    args=vars(args))
                    log.info(f"new best validation mean {best:.4f}")
            if over:
                log.info(f"time budget {args.minutes} min reached at step {step}")
                break
    metrics_file.close()
    log.info(f"done in {(time.time() - t0) / 60:.1f} min, {seen / 1e6:.2f}M examples, "
             f"best validation mean {best:.4f}")
    model, _ = load_checkpoint(run_dir / "draftformer.pt", device)
    rec = evaluate_model(model, val, device, mode="beam", beams=4)
    log.info("beam 4: draftformer {} | baseline {}".format(
        " ".join(f"{k}r={rec[f'cov{k}']:.3f}" for k in EVAL_COVERAGES),
        " ".join(f"{k}r={rec[f'base{k}']:.3f}" for k in EVAL_COVERAGES)))


if __name__ == "__main__":
    main()
