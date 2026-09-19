"""Train the learned polisher (dnacodec.model.polish) on baseline drafts.

    uv run python scripts/train_polish.py --sim-clusters 200000 --real-passes 12 --steps 6000

Data: simulated clusters (dnacodec.simulator.simulate, random profiles over a wide error
range, seeds through train_seed()) and real Microsoft TRAIN clusters, each subsampled to a
random coverage of 1..16 reads with extra weight on 1..6. Feature building runs in worker
processes. A validation subset carved from the real train split (never the held-out split)
picks the checkpoint; held-out is only touched by the final benchmark.

Writes checkpoints/<run>/polish.pt, train.log and metrics.jsonl.
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
from dnacodec.model.polish import (  # noqa: E402
    N_FEATURES,
    PolishConfig,
    PolishNet,
    count_parameters,
    example_of,
    polish_clusters,
    save_checkpoint,
)
from dnacodec.seeds import HELDOUT_COUNT, HELDOUT_START, train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402

log = logging.getLogger("polish")
EVAL_COVERAGES = (2, 4, 6, 10, 16)
_REAL: list = []  # per worker: the real train clusters


def setup_logging(log_file: Path) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)):
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
    log.propagate = False


def sample_coverage(rng: np.random.Generator, n: int) -> np.ndarray:
    hi = np.where(rng.random(n) < 0.5, 6, 16)
    return rng.integers(1, hi + 1)


def subsample_reads(reads, k: int, rng: np.random.Generator) -> list[str]:
    reads = [r for r in reads if r]
    if len(reads) <= k:
        return reads
    return [reads[i] for i in np.sort(rng.choice(len(reads), size=k, replace=False))]


def _stack(examples):
    examples = [e for e in examples if e is not None]
    if not examples:
        return None
    feats = np.stack([e[0] for e in examples]).astype(np.float16)
    ops = np.stack([e[1] for e in examples]).astype(np.int8)
    ins = np.stack([e[2] for e in examples]).astype(np.int8)
    return feats, ops, ins


def sim_task(args) -> tuple[int, tuple] | None:
    """One batch of simulated clusters: random channel, random references, random coverage."""
    seed, n, length = args
    rng = np.random.default_rng(train_seed(seed))
    profile = random_profile(rng, coverage_mean=float(rng.uniform(4, 25)))
    refs = random_strands(n, length, rng)
    sim_seed = train_seed(int(rng.integers(HELDOUT_START + HELDOUT_COUNT, 2**31 - 1)))
    clusters = simulate(refs, profile, sim_seed)
    ks = sample_coverage(rng, n)
    out = []
    for ref, cluster, k in zip(refs, clusters, ks):
        reads = subsample_reads(cluster, int(k), rng)
        if reads:
            out.append(example_of(reads, length, ref))
    packed = _stack(out)
    return (length, packed) if packed else None


def _init_real(indices: list[int]) -> None:
    data = realdata.load_microsoft("train")
    _REAL.clear()
    _REAL.extend((data[i].reference, data[i].reads) for i in indices)


def real_task(args) -> tuple[int, tuple] | None:
    """One pass over a slice of the real train clusters at a random coverage each."""
    seed, start, end = args
    rng = np.random.default_rng(train_seed(seed))
    chunk = _REAL[start:end]
    ks = sample_coverage(rng, len(chunk))
    out = []
    length = 110
    for (ref, reads), k in zip(chunk, ks):
        picked = subsample_reads(reads, int(k), rng)
        if picked:
            out.append(example_of(picked, len(ref), ref))
            length = len(ref)
    packed = _stack(out)
    return (length, packed) if packed else None


def build_dataset(args) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    pool_indices, val = _train_indices(args.val_size)
    tasks_sim = [
        (train_seed(1_000 + i), args.chunk, 140 if (i % 5 == 4) else 110)
        for i in range(math.ceil(args.sim_clusters / args.chunk))
    ]
    n_real = len(pool_indices)
    tasks_real = [
        (train_seed(500_000 + p * 1000 + s), s, min(s + args.chunk, n_real))
        for p in range(args.real_passes)
        for s in range(0, n_real, args.chunk)
    ]
    log.info(f"generating {len(tasks_sim)} simulated and {len(tasks_real)} real chunks "
             f"on {args.workers} workers ({n_real} real train clusters)")
    parts: dict[int, list] = {}
    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_real, initargs=(pool_indices,)) as pool:
        results = list(pool.map(sim_task, tasks_sim, chunksize=1))
        log.info(f"simulated data done in {time.time() - t0:.0f}s")
        results += list(pool.map(real_task, tasks_real, chunksize=1))
    for item in results:
        if item is None:
            continue
        length, packed = item
        parts.setdefault(length, []).append(packed)
    data = {}
    for length, chunks in parts.items():
        data[length] = tuple(np.concatenate([c[i] for c in chunks]) for i in range(3))
    total = sum(v[0].shape[0] for v in data.values())
    mb = sum(v[0].nbytes for v in data.values()) / 1e6
    log.info(f"dataset: {total} examples, lengths {sorted(data)}, {mb:.0f} MB, "
             f"{time.time() - t0:.0f}s")
    for length, (f, ops, ins) in data.items():
        log.info(f"  L={length}: {f.shape[0]} examples, {(ops != 0).mean():.4f} op edits, "
                 f"{(ins != 0).mean():.4f} insertions per position")
    return data, val


def _train_indices(val_size: int):
    """Real train clusters minus the validation carve (same split as dnacodec.model.train)."""
    data = realdata.load_microsoft("train")
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    pool, val = split_train_val(refs, clusters, val_size, seed=0)
    val_refs = set(val[0])
    indices = [i for i, c in enumerate(data) if c.reference not in val_refs and any(c.reads)]
    return indices, val


def evaluate_polish(model, val, device, coverages=EVAL_COVERAGES, baseline_cache={}) -> dict:
    refs, clusters = val
    result = {}
    for k in coverages:
        cut = [[r for r in c if r][:k] for c in clusters]
        decoded = polish_clusters(model, cut, len(refs[0]), device=device)
        result[f"cov{k}"] = float(evaluate(refs, decoded, cut).strand_accuracy)
        if k not in baseline_cache:
            base = MajorityVoteDecoder().decode(cut, len(refs[0]))
            baseline_cache[k] = float(evaluate(refs, base, cut).strand_accuracy)
        result[f"base{k}"] = baseline_cache[k]
    result["mean"] = float(np.mean([result[f"cov{k}"] for k in coverages]))
    result["base_mean"] = float(np.mean([result[f"base{k}"] for k in coverages]))
    return result


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sim-clusters", type=int, default=200_000)
    p.add_argument("--real-passes", type=int, default=12)
    p.add_argument("--chunk", type=int, default=2_000)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 8))
    p.add_argument("--steps", type=int, default=6_000)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=2e-3)
    p.add_argument("--warmup", type=int, default=200)
    p.add_argument("--channels", type=int, default=128)
    p.add_argument("--eval-every", type=int, default=1_000)
    p.add_argument("--val-size", type=int, default=500)
    p.add_argument("--minutes", type=float, default=45.0, help="stop training after this long")
    p.add_argument("--run-name", default="polish")
    p.add_argument("--checkpoint-dir", default=str(Path(__file__).resolve().parents[1] / "checkpoints"))
    p.add_argument("--device", default=None)
    args = p.parse_args(argv)

    run_dir = Path(args.checkpoint_dir) / args.run_name
    setup_logging(run_dir / "train.log")
    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    log.info(f"polish training on {device}, args {vars(args)}")
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

    data, val = build_dataset(args)
    lengths = sorted(data)
    sizes = np.array([data[L][0].shape[0] for L in lengths], dtype=float)
    probs = sizes / sizes.sum()

    model = PolishNet(PolishConfig(channels=args.channels)).to(device)
    log.info(f"model {model.cfg.to_dict()}, {count_parameters(model) / 1e6:.2f}M parameters")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    rng = np.random.default_rng(train_seed(7))
    metrics_file = (run_dir / "metrics.jsonl").open("a")
    best = -1.0
    t0 = time.time()
    ema = None
    amp = device.type == "cuda"
    for step in range(1, args.steps + 1):
        lr = args.lr * (step / args.warmup if step < args.warmup
                        else 0.5 * (1 + math.cos(math.pi * (step - args.warmup) / max(1, args.steps - args.warmup))) * 0.9 + 0.1)
        for g in opt.param_groups:
            g["lr"] = lr
        L = lengths[int(rng.choice(len(lengths), p=probs))]
        feats, ops, ins = data[L]
        idx = rng.integers(0, feats.shape[0], size=args.batch_size)
        x = torch.from_numpy(feats[idx].astype(np.float32)).to(device, non_blocking=True)
        y_op = torch.from_numpy(ops[idx].astype(np.int64)).to(device, non_blocking=True)
        y_ins = torch.from_numpy(ins[idx].astype(np.int64)).to(device, non_blocking=True)
        model.train()
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            op_logits, ins_logits = model(x)
            loss = F.cross_entropy(op_logits.float(), y_op) + F.cross_entropy(ins_logits.float(), y_ins)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        value = float(loss.detach())
        ema = value if ema is None else 0.98 * ema + 0.02 * value
        if step % 100 == 0:
            log.info(f"step {step}/{args.steps} loss {value:.4f} ema {ema:.4f} lr {lr:.2e} "
                     f"{step / (time.time() - t0):.1f} it/s")
        over_budget = (time.time() - t0) / 60 > args.minutes
        if step % args.eval_every == 0 or step == args.steps or over_budget:
            rec = {"step": step, "loss": ema, "minutes": (time.time() - t0) / 60}
            rec.update(evaluate_polish(model, val, device))
            log.info("eval step {}: polish {} | baseline {}".format(
                step,
                " ".join(f"{k}r={rec[f'cov{k}']:.3f}" for k in EVAL_COVERAGES),
                " ".join(f"{k}r={rec[f'base{k}']:.3f}" for k in EVAL_COVERAGES)))
            metrics_file.write(json.dumps(rec) + "\n")
            metrics_file.flush()
            if rec["mean"] > best:
                best = rec["mean"]
                save_checkpoint(run_dir / "polish.pt", model, step=step, metrics=rec,
                                source="polish", args=vars(args))
                log.info(f"new best validation mean {best:.4f} -> {run_dir / 'polish.pt'}")
        if over_budget:
            log.info(f"time budget of {args.minutes} min reached, stopping at step {step}")
            break
    metrics_file.close()
    log.info(f"done in {(time.time() - t0) / 60:.1f} min, best validation mean {best:.4f}")


if __name__ == "__main__":
    main()
