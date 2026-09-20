"""Train the v2 polisher (per-read set encoder), or the v1 architecture on the same data.

    uv run --extra train python scripts/train_polish2.py --arch v2 \
        --sim-clusters 1000000 --real-passes 48 --steps 40000 --run-name polish2

`--arch v1` builds the original dnacodec.model.polish.PolishNet from the *same* dataset,
the same schedule and the same number of steps, so "did the per-read features help, or was it
just more data and more capacity?" is answerable with one flag.

The dataset stores the raw alignment matrices (cols, insb) as int8 and derives both the 17 v1
vote features and the per-read tensor on the GPU, so one dataset feeds both architectures and
2 bytes per read-position is all the RAM it costs.

Train split only: simulated clusters with train_seed() seeds, plus passes over the real
Microsoft TRAIN clusters. A validation carve of the train split picks the checkpoint and tunes
the thresholds. The held-out split is never touched here.
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
from dnacodec.baseline import MAX_READS, MajorityVoteDecoder  # noqa: E402
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.model.data import random_profile, random_strands, split_train_val  # noqa: E402
from dnacodec.model.polish import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    PolishConfig,
    PolishNet,
    apply_edits,
)
from dnacodec.model.polish2 import (  # noqa: E402
    PolishConfig2,
    PolishNet2,
    count_parameters,
    expand_features,
    pack_cluster,
    polish2_clusters,
    save_checkpoint,
)
from dnacodec.seeds import HELDOUT_COUNT, HELDOUT_START, train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402

log = logging.getLogger("polish2")
EVAL_COVERAGES = (2, 4, 6, 10, 16)
_REAL: list = []
LOW_PROB, LOW_MAX = 0.5, 6


def setup_logging(log_file: Path) -> None:
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)):
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
    log.propagate = False


def _set_coverage(low_prob: float, low_max: int) -> None:
    global LOW_PROB, LOW_MAX
    LOW_PROB, LOW_MAX = low_prob, low_max


def sample_coverage(rng: np.random.Generator, n: int) -> np.ndarray:
    hi = np.where(rng.random(n) < LOW_PROB, LOW_MAX, MAX_READS)
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
    return (
        np.stack([e[0] for e in examples]),  # cols  (N, 16, L) int8
        np.stack([e[1] for e in examples]),  # insb  (N, 16, L) int8
        np.stack([e[2] for e in examples]),  # draft (N, L) int8
        np.array([e[3] for e in examples], dtype=np.int8),  # n reads
        np.stack([e[4] for e in examples]),  # op labels
        np.stack([e[5] for e in examples]),  # insert labels
    )


def sim_task(args):
    seed, n, length, low = args
    _set_coverage(*low)
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
            out.append(pack_cluster(reads, length, ref))
    packed = _stack(out)
    return (length, packed) if packed else None


def _init_real(indices: list[int]) -> None:
    data = realdata.load_microsoft("train")
    _REAL.clear()
    _REAL.extend((data[i].reference, data[i].reads) for i in indices)


def real_task(args):
    seed, start, end, low = args
    _set_coverage(*low)
    rng = np.random.default_rng(train_seed(seed))
    chunk = _REAL[start:end]
    ks = sample_coverage(rng, len(chunk))
    out, length = [], 110
    for (ref, reads), k in zip(chunk, ks):
        picked = subsample_reads(reads, int(k), rng)
        if picked:
            out.append(pack_cluster(picked, len(ref), ref))
            length = len(ref)
    packed = _stack(out)
    return (length, packed) if packed else None


def _train_indices(val_size: int):
    data = realdata.load_microsoft("train")
    refs = [c.reference for c in data]
    clusters = [c.reads for c in data]
    pool, val = split_train_val(refs, clusters, val_size, seed=0)
    val_refs = set(val[0])
    indices = [i for i, c in enumerate(data) if c.reference not in val_refs and any(c.reads)]
    return indices, val


def build_dataset(args):
    pool_indices, val = _train_indices(args.val_size)
    low = (args.low_prob, args.low_max)
    _set_coverage(*low)
    tasks_sim = [
        (train_seed(2_000_000 + i), args.chunk, 140 if (i % 5 == 4) else 110, low)
        for i in range(math.ceil(args.sim_clusters / args.chunk))
    ]
    n_real = len(pool_indices)
    tasks_real = [
        (train_seed(3_000_000 + p * 1000 + s), s, min(s + args.chunk, n_real), low)
        for p in range(args.real_passes)
        for s in range(0, n_real, args.chunk)
    ]
    log.info(f"generating {len(tasks_sim)} simulated and {len(tasks_real)} real chunks "
             f"on {args.workers} workers ({n_real} real train clusters)")
    parts: dict[int, list] = {}
    t0 = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_real,
                             initargs=(pool_indices,)) as pool:
        done = 0
        for item in pool.map(sim_task, tasks_sim, chunksize=1):
            done += 1
            if item is not None:
                parts.setdefault(item[0], []).append(item[1])
            if done % 50 == 0:
                log.info(f"  sim {done}/{len(tasks_sim)} chunks, {time.time() - t0:.0f}s")
        log.info(f"simulated data done in {time.time() - t0:.0f}s")
        done = 0
        for item in pool.map(real_task, tasks_real, chunksize=1):
            done += 1
            if item is not None:
                parts.setdefault(item[0], []).append(item[1])
            if done % 200 == 0:
                log.info(f"  real {done}/{len(tasks_real)} chunks, {time.time() - t0:.0f}s")
    data = {}
    for length, chunks in parts.items():
        data[length] = tuple(np.concatenate([c[i] for c in chunks]) for i in range(6))
    total = sum(v[0].shape[0] for v in data.values())
    gb = sum(sum(a.nbytes for a in v) for v in data.values()) / 1e9
    log.info(f"dataset: {total} examples, lengths {sorted(data)}, {gb:.1f} GB, "
             f"{time.time() - t0:.0f}s")
    for length, v in data.items():
        log.info(f"  L={length}: {v[0].shape[0]} examples, {(v[4] != 0).mean():.4f} op edits, "
                 f"{(v[5] != 0).mean():.4f} insertions per position")
    return data, val, total


# ---------------------------------------------------------------- validation


def _val_packed(val, coverages=EVAL_COVERAGES):
    """Packed examples per coverage for the validation carve, built once."""
    refs, clusters = val
    length = len(refs[0])
    out = []
    for k in coverages:
        cut = [[r for r in c if r][:k] for c in clusters]
        packed = [pack_cluster(c, length, None) for c in cut]
        out.append((k, cut, packed))
    return out


def _val_probs(model, arch, packed, device, batch_size=512):
    from dnacodec.model.polish2 import _forward_packed

    if arch == "v2":
        return _forward_packed(model, [p[:4] for p in packed], batch_size, device)
    probs = []
    amp = device.type == "cuda"
    with torch.no_grad():
        for start in range(0, len(packed), batch_size):
            chunk = packed[start : start + batch_size]
            cols, insb, draft, nreads = _to_tensors(chunk, device)
            _, x_pos, _ = expand_features(cols, insb, draft, nreads)
            with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
                op_logits, ins_logits = model(x_pos)
            probs.extend(zip(op_logits.float().softmax(1).cpu().numpy(),
                             ins_logits.float().softmax(1).cpu().numpy()))
    return probs


def _to_tensors(chunk, device):
    cols = torch.from_numpy(np.stack([c[0] for c in chunk])).to(device).long()
    insb = torch.from_numpy(np.stack([c[1] for c in chunk])).to(device).long()
    draft = torch.from_numpy(np.stack([c[2] for c in chunk])).to(device).long()
    nreads = torch.tensor([c[3] for c in chunk], device=device)
    return cols, insb, draft, nreads


def _drafts_of(packed, alphabet="ACGT"):
    return ["".join(alphabet[b] for b in p[2]) for p in packed]


def _apply(drafts, packed, probs, length, th, mode="topk"):
    out = []
    for draft, p, (op_p, ins_p) in zip(drafts, packed, probs):
        sub_t, ins_t = th["low"] if p[3] <= th["low_max_reads"] else th["high"]
        out.append(apply_edits(draft, op_p, ins_p, length, sub_t, ins_t, mode))
    return out


def evaluate_model(model, arch, val, cache, device, thresholds=None, baseline_cache={}) -> dict:
    refs = val[0]
    length = len(refs[0])
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    model.eval()
    result = {}
    for k, cut, packed in cache:
        probs = _val_probs(model, arch, packed, device)
        decoded = _apply(_drafts_of(packed), packed, probs, length, th)
        result[f"cov{k}"] = float(evaluate(refs, decoded, cut).strand_accuracy)
        if k not in baseline_cache:
            base = MajorityVoteDecoder().decode(cut, length)
            baseline_cache[k] = float(evaluate(refs, base, cut).strand_accuracy)
        result[f"base{k}"] = baseline_cache[k]
    result["mean"] = float(np.mean([result[f"cov{k}"] for k in EVAL_COVERAGES]))
    result["base_mean"] = float(np.mean([result[f"base{k}"] for k in EVAL_COVERAGES]))
    return result


def tune_thresholds(model, arch, val, cache, device,
                    grid=(0.0, 0.3, 0.5, 0.7, 0.9, 0.98)) -> dict:
    """Confidence thresholds, picked on the VALIDATION carve of the train split."""
    refs = val[0]
    length = len(refs[0])
    precomputed = [(k, cut, packed, _val_probs(model, arch, packed, device))
                   for k, cut, packed in cache]
    best = dict(DEFAULT_THRESHOLDS)
    for regime in ("low", "high"):
        scores = {}
        for sub_t in grid:
            for ins_t in grid:
                cand = {**best, regime: (sub_t, ins_t)}
                acc = [
                    evaluate(refs, _apply(_drafts_of(p), p, pr, length, cand), cut).strand_accuracy
                    for _, cut, p, pr in precomputed
                ]
                scores[(sub_t, ins_t)] = float(np.mean(acc))
        best[regime] = max(scores, key=scores.get)
        log.info(f"thresholds {regime}: {best[regime]} (val mean {scores[best[regime]]:.4f})")
    return best


def tune_gain_thresholds(model, arch, val, cache, device,
                         subs=(-1.0, 0.0, 0.5, 1.0, 2.0),
                         indels=(-2.0, -1.0, -0.5, 0.0, 1.0)) -> dict:
    """The same, for mode='gain' where the thresholds are log-odds margins in nats."""
    refs = val[0]
    length = len(refs[0])
    precomputed = [(k, cut, packed, _val_probs(model, arch, packed, device))
                   for k, cut, packed in cache]
    best = {"low_max_reads": 3, "low": (0.5, -0.5), "high": (0.0, -1.0)}
    for regime in ("low", "high"):
        scores = {}
        for sub_t in subs:
            for ins_t in indels:
                cand = {**best, regime: (sub_t, ins_t)}
                acc = [
                    evaluate(refs, _apply(_drafts_of(p), p, pr, length, cand, "gain"),
                             cut).strand_accuracy
                    for _, cut, p, pr in precomputed
                ]
                scores[(sub_t, ins_t)] = float(np.mean(acc))
        best[regime] = max(scores, key=scores.get)
        log.info(f"gain thresholds {regime}: {best[regime]} (val mean {scores[best[regime]]:.4f})")
    return best


# ---------------------------------------------------------------- training


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arch", choices=("v1", "v2"), default="v2")
    p.add_argument("--sim-clusters", type=int, default=1_000_000)
    p.add_argument("--real-passes", type=int, default=48)
    p.add_argument("--chunk", type=int, default=2_000)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4))
    p.add_argument("--steps", type=int, default=40_000)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--lr", type=float, default=2e-3)
    p.add_argument("--warmup", type=int, default=500)
    p.add_argument("--channels", type=int, default=192)
    p.add_argument("--blocks", default="1,2,4,8,16,1,2,4,8,16")
    p.add_argument("--read-channels", type=int, default=48)
    p.add_argument("--eval-every", type=int, default=2_000)
    p.add_argument("--val-size", type=int, default=500)
    p.add_argument("--low-prob", type=float, default=0.5)
    p.add_argument("--low-max", type=int, default=6)
    p.add_argument("--minutes", type=float, default=180.0)
    p.add_argument("--ema-decay", type=float, default=0.999)
    p.add_argument("--run-name", default="polish2")
    p.add_argument("--checkpoint-dir",
                   default=str(Path(__file__).resolve().parents[1] / "checkpoints"))
    p.add_argument("--device", default=None)
    p.add_argument("--dataset-cache", default=None,
                   help="npz path: load the dataset from here if it exists, else write it")
    args = p.parse_args(argv)

    run_dir = Path(args.checkpoint_dir) / args.run_name
    setup_logging(run_dir / "train.log")
    from dnacodec.model.net import pick_device

    device = pick_device(args.device)
    log.info(f"polish2 training on {device}, args {vars(args)}")
    if device.type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True

    cache_path = Path(args.dataset_cache) if args.dataset_cache else None
    n_examples = 0
    if cache_path is not None and cache_path.exists():
        log.info(f"loading dataset from {cache_path}")
        z = np.load(cache_path)
        lengths = sorted({int(k.split("_")[0]) for k in z.files})
        data = {L: tuple(z[f"{L}_{i}"] for i in range(6)) for L in lengths}
        n_examples = sum(v[0].shape[0] for v in data.values())
        _, val = _train_indices(args.val_size)
        log.info(f"dataset: {n_examples} examples, lengths {lengths}")
    else:
        data, val, n_examples = build_dataset(args)
        if cache_path is not None:
            log.info(f"writing dataset cache to {cache_path}")
            np.savez(cache_path, **{f"{L}_{i}": data[L][i] for L in data for i in range(6)})

    lengths = sorted(data)
    sizes = np.array([data[L][0].shape[0] for L in lengths], dtype=float)
    probs = sizes / sizes.sum()

    blocks = tuple(int(x) for x in args.blocks.split(","))
    if args.arch == "v2":
        cfg = PolishConfig2(channels=args.channels, blocks=blocks,
                            read_channels=args.read_channels)
        model = PolishNet2(cfg).to(device)
    else:
        cfg = PolishConfig(channels=args.channels, blocks=blocks)
        model = PolishNet(cfg).to(device)
    log.info(f"arch {args.arch}, config {cfg.to_dict()}, "
             f"{count_parameters(model) / 1e6:.2f}M parameters")

    ema_model = None
    if args.ema_decay > 0:
        ema_model = torch.optim.swa_utils.AveragedModel(
            model, avg_fn=lambda a, b, _n: args.ema_decay * a + (1 - args.ema_decay) * b
        )

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    rng = np.random.default_rng(train_seed(11))
    metrics_file = (run_dir / "metrics.jsonl").open("a")
    val_cache = _val_packed(val)
    best = -1.0
    t0 = time.time()
    ema_loss = None
    amp = device.type == "cuda"

    def run_eval(step: int, model_to_eval, tag: str) -> dict:
        rec = {"step": step, "loss": ema_loss, "minutes": (time.time() - t0) / 60, "tag": tag}
        rec.update(evaluate_model(model_to_eval, args.arch, val, val_cache, device))
        log.info("eval step {} [{}]: polish {} | baseline {}".format(
            step, tag,
            " ".join(f"{k}r={rec[f'cov{k}']:.3f}" for k in EVAL_COVERAGES),
            " ".join(f"{k}r={rec[f'base{k}']:.3f}" for k in EVAL_COVERAGES)))
        metrics_file.write(json.dumps(rec) + "\n")
        metrics_file.flush()
        return rec

    for step in range(1, args.steps + 1):
        if step < args.warmup:
            lr = args.lr * step / args.warmup
        else:
            t = (step - args.warmup) / max(1, args.steps - args.warmup)
            lr = args.lr * (0.5 * (1 + math.cos(math.pi * t)) * 0.99 + 0.01)
        for g in opt.param_groups:
            g["lr"] = lr
        L = lengths[int(rng.choice(len(lengths), p=probs))]
        cols_a, insb_a, draft_a, n_a, ops_a, ins_a = data[L]
        idx = rng.integers(0, cols_a.shape[0], size=args.batch_size)
        cols = torch.from_numpy(cols_a[idx]).to(device, non_blocking=True).long()
        insb = torch.from_numpy(insb_a[idx]).to(device, non_blocking=True).long()
        draft = torch.from_numpy(draft_a[idx]).to(device, non_blocking=True).long()
        nreads = torch.from_numpy(n_a[idx].astype(np.int64)).to(device, non_blocking=True)
        y_op = torch.from_numpy(ops_a[idx].astype(np.int64)).to(device, non_blocking=True)
        y_ins = torch.from_numpy(ins_a[idx].astype(np.int64)).to(device, non_blocking=True)
        x_read, x_pos, mask4 = expand_features(cols, insb, draft, nreads)
        model.train()
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            if args.arch == "v2":
                op_logits, ins_logits = model(x_read, x_pos, mask4)
            else:
                op_logits, ins_logits = model(x_pos)
            loss = (F.cross_entropy(op_logits.float(), y_op)
                    + F.cross_entropy(ins_logits.float(), y_ins))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if ema_model is not None and step > args.warmup:
            ema_model.update_parameters(model)
        value = float(loss.detach())
        ema_loss = value if ema_loss is None else 0.99 * ema_loss + 0.01 * value
        if step % 200 == 0:
            log.info(f"step {step}/{args.steps} loss {value:.4f} ema {ema_loss:.4f} "
                     f"lr {lr:.2e} {step / (time.time() - t0):.1f} it/s")
        over_budget = (time.time() - t0) / 60 > args.minutes
        if step % args.eval_every == 0 or step == args.steps or over_budget:
            rec = run_eval(step, model, "raw")
            if ema_model is not None and step > args.warmup * 2:
                rec_ema = run_eval(step, ema_model.module, "ema")
                if rec_ema["mean"] > rec["mean"]:
                    rec, model_best = rec_ema, ema_model.module
                else:
                    model_best = model
            else:
                model_best = model
            if rec["mean"] > best:
                best = rec["mean"]
                save_checkpoint(run_dir / "polish2.pt", model_best, step=step, metrics=rec,
                                source=f"polish2-{args.arch}", arch_kind=args.arch,
                                n_examples=n_examples, args=vars(args))
                log.info(f"new best validation mean {best:.4f} ({rec['tag']}) "
                         f"-> {run_dir / 'polish2.pt'}")
        if over_budget:
            log.info(f"time budget of {args.minutes} min reached, stopping at step {step}")
            break
    metrics_file.close()
    log.info(f"done in {(time.time() - t0) / 60:.1f} min, best validation mean {best:.4f}")

    # reload the best checkpoint and tune the thresholds on the validation carve
    ckpt = torch.load(run_dir / "polish2.pt", map_location=device, weights_only=False)
    if args.arch == "v2":
        best_model = PolishNet2(PolishConfig2(**{
            k: (tuple(v) if isinstance(v, list) else v) for k, v in ckpt["config"].items()
        })).to(device)
    else:
        best_model = PolishNet(PolishConfig(**{
            k: (tuple(v) if isinstance(v, list) else v) for k, v in ckpt["config"].items()
        })).to(device)
    best_model.load_state_dict(ckpt["model"])
    best_model.eval()
    th = tune_thresholds(best_model, args.arch, val, val_cache, device)
    rec = evaluate_model(best_model, args.arch, val, val_cache, device, thresholds=th)
    log.info("after tuning: polish {} | baseline {}".format(
        " ".join(f"{k}r={rec[f'cov{k}']:.3f}" for k in EVAL_COVERAGES),
        " ".join(f"{k}r={rec[f'base{k}']:.3f}" for k in EVAL_COVERAGES)))
    th_gain = tune_gain_thresholds(best_model, args.arch, val, val_cache, device)
    extra = {k: v for k, v in ckpt.items()
             if k not in ("config", "model", "thresholds", "arch")}
    save_checkpoint(run_dir / "polish2.pt", best_model, thresholds=th,
                    gain_thresholds=th_gain, tuned_metrics=rec, **extra)
    log.info(f"saved thresholds {th} and gain thresholds {th_gain}")

    if args.arch == "v2":  # sanity: the decoder path reproduces the tuned validation numbers
        refs, clusters = val
        cut = [[r for r in c if r][:6] for c in clusters]
        decoded = polish2_clusters(best_model, cut, len(refs[0]), device=device, thresholds=th)
        log.info(f"decoder path at 6 reads: {evaluate(refs, decoded, cut).strand_accuracy:.4f} "
                 f"(cached path said {rec['cov6']:.4f})")


if __name__ == "__main__":
    main()
