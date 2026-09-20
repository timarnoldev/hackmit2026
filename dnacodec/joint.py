"""Letting the two learned models talk: risk labels distilled from the polisher.

This module is **additive**. It does not change `risk.py`, `polish.py` or `loop.py`, so the
sequential pipeline (train polisher -> freeze -> label with K=32 simulations -> train risk
model) stays available bit for bit as the control. Everything here is an alternative way to
produce the *labels* the risk model is fitted on, plus a risk model that gets a second,
dense teacher signal from the polisher.

The expensive step in the loop
------------------------------
`risk.label_failure_rates` simulates every strand K = 32 times, decodes each noisy cluster
with the frozen polisher, and labels the strand with the fraction of decodes that came out
wrong. The label is a mean of K Bernoulli draws, so its standard error is ~sqrt(p(1-p)/K):
about 0.09 at K = 32, about 0.35 at K = 2. That variance is the only reason K has to be
large. The decoder itself is not a black box though: its two heads are per-position
distributions over "what still has to be fixed". Running the polisher once more on its own
output gives, under a per-position independence assumption,

    P(nothing left to fix) = prod_i p_i(keep) * prod_i q_i(no insertion)

which is a *continuous* estimate of "this decode is wrong" from a **single** simulation.
`label_with_polisher` records both per simulation, so a K = 32 run yields the control labels
and the distilled labels on exactly the same simulated clusters, which makes the comparison
paired.

Three signals come out of one decode:

    fail       0/1, did the decoded strand differ from the truth (the control's signal)
    surr_post  1 - P(nothing left to fix), from a second polisher pass over its own output
    surr_pre   1 - P(the applied edit script was the right one), free, no second pass

and, for the first `dense_k` simulations, the per-position profile
`1 - p_i(keep)` and `1 - q_i(no insertion)`, which is the teacher signal for
`DenseRiskModel`: 2 * strand_length numbers per simulation instead of one bit.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from .profiles import SituationProfile
from .risk import MAX_LENGTH, _check_seed, _derived_seed, one_hot
from .seeds import train_seed
from .types import Cluster, Strand

_EPS = 1e-9
LABEL_CHUNK_STRANDS = 64  # strands per simulate() call; bounds memory at K = 32


# ------------------------------------------------------------------ simulators


def get_simulate(which: str = "A") -> Callable[[Sequence[Strand], SituationProfile, int], list[Cluster]]:
    """The channel to label against. "A" is `dnacodec.simulator`, "B" is `simulator_b`.

    Simulator B is evaluation only (the firewall). Never label training strands with it.
    """
    if which.upper() == "A":
        from .simulator import simulate
    elif which.upper() == "B":
        from .simulator_b import simulate
    else:
        raise ValueError(f"unknown simulator {which!r}, use 'A' or 'B'")
    return simulate


# ------------------------------------------------------------------ polisher signals


def _rescore_chunk(args: tuple) -> Any:
    """Worker: vote columns and features of already polished strands against their reads.

    Module level and torch free so ProcessPoolExecutor can pickle it by reference.
    """
    from .baseline import _to_array
    from .model.polish import features_of, votes_of

    polished, read_sets = args
    feats = [
        features_of(p, votes_of(p, reads, [_to_array(r) for r in reads]))
        for p, reads in zip(polished, read_sets)
    ]
    if feats and len({f.shape for f in feats}) == 1:
        return np.stack(feats)
    return feats


class _RescorePool:
    """Process pool for `_rescore_chunk`, kept alive across calls. None disables it."""

    def __init__(self, workers: int) -> None:
        self.workers = max(1, int(workers))
        self._pool: ProcessPoolExecutor | None = None

    def map(self, polished: list[str], read_sets: list[list[str]]):
        if self.workers <= 1 or len(polished) < 64:
            return list(_rescore_chunk((polished, read_sets)))
        import multiprocessing as mp

        if self._pool is None:
            from .model.polish import _default_start_method, _init_feature_worker

            ctx = mp.get_context(_default_start_method())
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=ctx, initializer=_init_feature_worker
            )
        size = max(32, len(polished) // (self.workers * 4) + 1)
        chunks = [
            (polished[a : a + size], read_sets[a : a + size]) for a in range(0, len(polished), size)
        ]
        out: list = []
        for part in self._pool.map(_rescore_chunk, chunks):
            out.extend(part)
        return out

    def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)


def polish_with_confidence(
    model,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 512,
    device=None,
    thresholds: dict | None = None,
    pool=None,
    rescore_pool: _RescorePool | None = None,
    dense: bool = True,
):
    """Decode with the polisher and report how sure it is, per strand and per position.

    Returns a dict with, for every input cluster (None / NaN for empty clusters):

        strand      the decoded strand, exactly as `polish_clusters` would return it
        surr_post   1 - exp(sum_i log p_i(keep) + sum_i log q_i(no insert)) measured by a
                    second polisher pass over its own output: the model's own probability
                    that its answer still needs an edit
        surr_pre    1 - exp(sum_i log max_c p_i(c) + sum_i log max_c q_i(c)) from the first
                    pass: the likelihood of the most likely edit script, free of charge
        conf_post   the raw log probability behind surr_post (mean per position)
        per_pos     (2, strand_length) float32, [1 - p_i(keep), 1 - q_i(no insert)], only
                    when dense is on

    The first pass is exactly `polish_clusters`: `strand` is bit for bit what the frozen
    decoder produces, so labeling never changes the thing being labeled.
    """
    import torch

    from .baseline import MAX_READS, subsample
    from .model.polish import KEEP, edits_from_probs, predict_probs

    n = len(clusters)
    probs = predict_probs(model, clusters, strand_length, batch_size, device, pool)
    strands = edits_from_probs(n, probs, strand_length, thresholds)

    out: dict[str, Any] = {
        "strand": strands,
        "surr_pre": np.full(n, np.nan),
        "surr_post": np.full(n, np.nan),
        "conf_post": np.full(n, np.nan),
        "per_pos": np.full((n, 2, strand_length), np.nan, dtype=np.float32) if dense else None,
    }

    idx: list[int] = []
    for i, draft, op_p, ins_p, _size in probs:
        idx.append(i)
        log_pre = float(np.log(op_p.max(0) + _EPS).sum() + np.log(ins_p.max(0) + _EPS).sum())
        out["surr_pre"][i] = 1.0 - np.exp(log_pre)
    if not idx:
        return out

    read_sets = [subsample([r for r in clusters[i] if r], MAX_READS) for i in idx]
    polished = [strands[i] for i in idx]
    feats = (rescore_pool or _RescorePool(1)).map(polished, read_sets)

    device_t = torch.device(device) if device is not None else next(model.parameters()).device
    amp = device_t.type == "cuda"
    pos = 0
    with torch.no_grad():
        for start in range(0, len(idx), batch_size):
            chunk = feats[start : start + batch_size]
            x = torch.from_numpy(np.stack(chunk)).to(device_t)
            with torch.autocast(device_t.type, dtype=torch.bfloat16, enabled=amp):
                op_logits, ins_logits = model(x)
            op_p = op_logits.float().softmax(1).cpu().numpy()
            ins_p = ins_logits.float().softmax(1).cpu().numpy()
            for j in range(len(chunk)):
                i = idx[start + j]
                keep = op_p[j][KEEP]
                none = ins_p[j][0]
                total = float(np.log(keep + _EPS).sum() + np.log(none + _EPS).sum())
                out["conf_post"][i] = total / max(1, 2 * len(keep))
                out["surr_post"][i] = 1.0 - np.exp(total)
                if dense:
                    width = min(strand_length, len(keep))
                    out["per_pos"][i, 0, :width] = 1.0 - keep[:width]
                    out["per_pos"][i, 1, :width] = 1.0 - none[:width]
            pos += len(chunk)
    return out


# ------------------------------------------------------------------ labeling


@dataclass
class LabelBatch:
    """Per-simulation outcomes for a set of strands. `fail` is the control's signal."""

    strands: list[Strand]
    fail: np.ndarray  # (n, K) 1/0, NaN where the cluster was empty (dropout)
    surr_post: np.ndarray  # (n, K) distilled signal, second polisher pass
    surr_pre: np.ndarray  # (n, K) distilled signal, first pass only (free)
    per_pos: np.ndarray  # (n, dense_k, 2, L) per-position edit probability, NaN padded
    seconds: dict[str, float] = field(default_factory=dict)

    def rate(self, key: str = "fail", k: int | None = None, lo: int = 0) -> np.ndarray:
        """Mean of simulations lo..k, NaN where no simulation produced a cluster.

        `lo` exists for the label-efficiency diagnostic: a cheap label built from the first
        j simulations has to be scored against a truth built from *other* simulations, or
        the shared clusters flatter it.
        """
        a = getattr(self, key)
        a = a[:, lo : (None if k is None else k)]
        with np.errstate(invalid="ignore"):
            return np.nanmean(a, axis=1)

    def dense_target(self, k: int | None = None) -> np.ndarray:
        """(n, 2, L) mean per-position edit probability over the first k simulations."""
        a = self.per_pos[:, :k] if k is not None else self.per_pos
        with np.errstate(invalid="ignore"):
            return np.nanmean(a, axis=1)


def label_with_polisher(
    strands: Sequence[Strand],
    model,
    profile: SituationProfile,
    k: int = 32,
    seed: int = 0,
    sim: str = "A",
    thresholds: dict | None = None,
    device=None,
    batch_size: int = 512,
    dense_k: int = 4,
    workers: int | None = None,
    heldout: bool = False,
    log: Callable[[str], None] | None = None,
) -> LabelBatch:
    """Simulate each strand K times, decode with `model`, record every signal per simulation.

    Same simulation protocol and the same seed derivation as `risk.label_failure_counts`
    (`_derived_seed(seed, 2, chunk_index)`, chunks of 64 strands), so `fail.mean(axis=1)` is
    the control label. The distilled labels come off the same clusters, which makes every
    comparison in `scripts/run_joint.py` paired.
    """
    _check_seed(seed, heldout)
    from .model.polish import _FeaturePool, _default_workers

    simulate = get_simulate(sim)
    strands = list(strands)
    n = len(strands)
    dense_k = min(dense_k, k)
    max_len = max((len(s) for s in strands), default=1)

    fail = np.full((n, k), np.nan, dtype=np.float32)
    surr_post = np.full((n, k), np.nan, dtype=np.float32)
    surr_pre = np.full((n, k), np.nan, dtype=np.float32)
    per_pos = np.full((n, dense_k, 2, max_len), np.nan, dtype=np.float32)

    workers = _default_workers() if workers is None else max(1, int(workers))
    feature_pool = _FeaturePool(workers) if workers > 1 else None
    rescore_pool = _RescorePool(workers)
    t_sim = t_dec = 0.0
    t0 = time.time()
    try:
        for a in range(0, n, LABEL_CHUNK_STRANDS):
            block = list(range(a, min(a + LABEL_CHUNK_STRANDS, n)))
            chunk_seed = _derived_seed(seed, 2, a // LABEL_CHUNK_STRANDS)
            repeated = [strands[i] for i in block for _ in range(k)]
            ts = time.time()
            clusters = simulate(repeated, profile, chunk_seed)
            t_sim += time.time() - ts
            by_len: dict[int, list[int]] = {}
            for j, s in enumerate(repeated):
                by_len.setdefault(len(s), []).append(j)
            ts = time.time()
            for strand_length, flat in by_len.items():
                res = polish_with_confidence(
                    model,
                    [clusters[j] for j in flat],
                    strand_length,
                    batch_size=batch_size,
                    device=device,
                    thresholds=thresholds,
                    pool=feature_pool,
                    rescore_pool=rescore_pool,
                    dense=True,
                )
                for slot, j in enumerate(flat):
                    guess = res["strand"][slot]
                    if guess is None:
                        continue  # empty cluster: dropout is not the decoder's fault
                    row, col = block[j // k], j % k
                    fail[row, col] = float(guess != repeated[j])
                    surr_post[row, col] = res["surr_post"][slot]
                    surr_pre[row, col] = res["surr_pre"][slot]
                    if col < dense_k:
                        width = min(strand_length, max_len)
                        per_pos[row, col, :, :width] = res["per_pos"][slot][:, :width]
            t_dec += time.time() - ts
            if log and (a // LABEL_CHUNK_STRANDS) % 10 == 0:
                done = min(a + LABEL_CHUNK_STRANDS, n)
                log(f"  labeled {done}/{n} strands ({time.time() - t0:.0f}s)")
    finally:
        rescore_pool.close()
        if feature_pool is not None:
            feature_pool.close()
    return LabelBatch(
        strands=strands,
        fail=fail,
        surr_post=surr_post,
        surr_pre=surr_pre,
        per_pos=per_pos,
        seconds={"total": time.time() - t0, "simulate": t_sim, "decode": t_dec, "k": float(k)},
    )


def cluster_sizes(
    strands: Sequence[Strand],
    profile: SituationProfile,
    k: int,
    seed: int,
    sim: str = "A",
    heldout: bool = False,
) -> np.ndarray:
    """(n, k) number of reads in every cluster `label_with_polisher` saw, re-simulated.

    Same chunking and the same derived seeds, so cluster (i, j) here is cluster (i, j) there.
    Simulating is cheap (about 10% of a labeling run); this exists so a saved LabelBatch can
    be stratified by coverage after the fact, because "the decoder knows it is wrong" is
    mostly worthless if all it knows is that the cluster was thin.
    """
    _check_seed(seed, heldout)
    simulate = get_simulate(sim)
    strands = list(strands)
    out = np.zeros((len(strands), k), dtype=np.int16)
    for a in range(0, len(strands), LABEL_CHUNK_STRANDS):
        block = list(range(a, min(a + LABEL_CHUNK_STRANDS, len(strands))))
        chunk_seed = _derived_seed(seed, 2, a // LABEL_CHUNK_STRANDS)
        repeated = [strands[i] for i in block for _ in range(k)]
        for j, cluster in enumerate(simulate(repeated, profile, chunk_seed)):
            out[block[j // k], j % k] = len([r for r in cluster if r])
    return out


# ------------------------------------------------------------------ dense risk model


def _build_dense_cnn(channels: int, n_blocks: int, dropout: float, dense_out: int = 2):
    """`risk._build_cnn` with a second, per-position head.

    The trunk is identical to the risk model's, so the scalar path has the same capacity as
    the control. The extra head predicts the polisher's per-position edit probability, which
    only shapes the trunk during training and is thrown away at scoring time. Nothing the
    encoder calls needs a cluster: the risk model still sees only the strand's letters.
    """
    import torch
    from torch import nn

    from .risk import _conv

    class Block(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.conv1 = nn.Conv1d(channels, channels, 5, padding=2)
            self.conv2 = nn.Conv1d(channels, channels, 1)
            self.norm = nn.BatchNorm1d(channels)
            self.drop = nn.Dropout(dropout)

        def forward(self, h, m):
            y = _conv(self.conv2, torch.nn.functional.gelu(self.norm(_conv(self.conv1, h))))
            return (h + self.drop(y)) * m

    class DenseRiskCNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.stem = nn.Conv1d(6, channels, 9, padding=4)
            self.blocks = nn.ModuleList(Block() for _ in range(n_blocks))
            self.head = nn.Sequential(
                nn.Linear(2 * channels, channels), nn.GELU(), nn.Dropout(dropout), nn.Linear(channels, 1)
            )
            self.pos_head = nn.Conv1d(channels, dense_out, 1)

        def trunk(self, x, mask):
            m = mask[:, None, :].to(x.dtype)
            h = torch.nn.functional.gelu(_conv(self.stem, x)) * m
            for block in self.blocks:
                h = block(h, m)
            return h, m

        def forward(self, x, mask, with_dense: bool = False):
            h, m = self.trunk(x, mask)
            mean = h.sum(-1) / m.sum(-1).clamp(min=1.0)
            mx = h.masked_fill(m == 0, -1e4).amax(-1)
            logit = self.head(torch.cat([mean, mx], dim=1)).squeeze(-1)
            if not with_dense:
                return logit
            return logit, _conv(self.pos_head, h)

    return DenseRiskCNN()


class DenseRiskModel:
    """A risk model that is also taught *where* the decoder expects trouble.

    Same interface as `risk.RiskModel`: `fit(strands, failed)`, `model(strands) -> [0, 1]`,
    usable directly as the encoder's `Scorer`. `fit_dense(strands, failed, per_pos)` adds the
    auxiliary target: the polisher's per-position edit probability, averaged over a handful of
    simulations. That is 2 * L teacher numbers per strand instead of one, which is the whole
    point: the expensive part of the control is beating the variance out of a single bit.

    At scoring time the auxiliary head is not used, so the encoder's interface is unchanged.
    """

    def __init__(
        self,
        channels: int = 48,
        n_blocks: int = 4,
        dropout: float = 0.1,
        epochs: int = 30,
        batch_size: int = 256,
        lr: float = 2e-3,
        weight_decay: float = 1e-4,
        val_fraction: float = 0.1,
        patience: int = 10,
        dense_weight: float = 1.0,
        seed: int = 0,
        device: str | None = None,
        verbose: bool = False,
    ) -> None:
        from .risk import pick_device

        self.config = dict(channels=channels, n_blocks=n_blocks, dropout=dropout)
        self.epochs, self.batch_size, self.lr = epochs, batch_size, lr
        self.weight_decay, self.val_fraction, self.patience = weight_decay, val_fraction, patience
        self.dense_weight = dense_weight
        self.seed = train_seed(seed)
        self.device = device or pick_device()
        self.verbose = verbose
        self.net = None
        self.history: list[dict[str, float]] = []
        self.meta: dict[str, Any] = {}

    def fit(self, strands: Sequence[Strand], failed: np.ndarray) -> DenseRiskModel:
        return self.fit_dense(strands, failed, None)

    def fit_dense(
        self, strands: Sequence[Strand], failed: np.ndarray, per_pos: np.ndarray | None
    ) -> DenseRiskModel:
        import torch

        strands = list(strands)
        y = np.asarray(failed, dtype=np.float32).reshape(-1)
        if len(y) != len(strands):
            raise ValueError(f"{len(strands)} strands but {len(y)} labels")
        keep = ~np.isnan(y)
        if per_pos is not None:
            per_pos = np.asarray(per_pos, dtype=np.float32)[keep]
        strands = [s for s, ok in zip(strands, keep) if ok]
        y = np.clip(y[keep], 0.0, 1.0)
        if not strands:
            raise ValueError("no labeled strands to fit")

        torch.manual_seed(self.seed)
        rng = np.random.default_rng(_derived_seed(self.seed, 4))
        order = rng.permutation(len(strands))
        n_val = int(len(strands) * self.val_fraction) if len(strands) >= 50 else 0
        val_idx, tr_idx = order[:n_val], order[n_val:]

        width = max(MAX_LENGTH, max(len(s) for s in strands))
        x_all, m_all = one_hot(strands, width)
        dev = torch.device(self.device)
        x_t = torch.from_numpy(x_all).to(dev)
        m_t = torch.from_numpy(m_all).to(dev)
        y_t = torch.from_numpy(y).to(dev)
        if per_pos is not None:
            padded = np.zeros((len(strands), per_pos.shape[1], width), dtype=np.float32)
            valid = np.zeros((len(strands), 1, width), dtype=np.float32)
            w = min(width, per_pos.shape[2])
            block = np.nan_to_num(per_pos[:, :, :w], nan=0.0)
            padded[:, :, :w] = block
            valid[:, 0, :w] = (~np.isnan(per_pos[:, 0, :w])).astype(np.float32)
            d_t = torch.from_numpy(padded).to(dev)
            v_t = torch.from_numpy(valid).to(dev)
        else:
            d_t = v_t = None

        self.net = _build_dense_cnn(**self.config).to(dev)
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        steps = max(1, self.epochs * int(np.ceil(len(tr_idx) / self.batch_size)))
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=self.lr, total_steps=steps)
        bce = torch.nn.BCEWithLogitsLoss()

        best_state, best_val, bad = None, float("inf"), 0
        self.history = []
        for epoch in range(self.epochs):
            self.net.train()
            perm = torch.from_numpy(rng.permutation(tr_idx)).to(dev)
            total = 0.0
            for a in range(0, len(perm), self.batch_size):
                idx = perm[a : a + self.batch_size]
                if len(idx) < 2:
                    continue
                if d_t is None or self.dense_weight <= 0:
                    logits = self.net(x_t[idx], m_t[idx])
                    loss = bce(logits, y_t[idx])
                else:
                    logits, dense = self.net(x_t[idx], m_t[idx], with_dense=True)
                    dl = torch.nn.functional.binary_cross_entropy_with_logits(
                        dense, d_t[idx], reduction="none"
                    )
                    mask = v_t[idx]
                    dloss = (dl * mask).sum() / mask.sum().clamp(min=1.0) / dense.shape[1]
                    loss = bce(logits, y_t[idx]) + self.dense_weight * dloss
                opt.zero_grad()
                loss.backward()
                opt.step()
                sched.step()
                total += float(loss.detach()) * len(idx)
            record = {"epoch": epoch, "train_loss": total / max(len(tr_idx), 1)}
            if n_val:
                val = self._loss(x_t, m_t, y_t, val_idx)
                record["val_loss"] = val
                if val < best_val - 1e-5:
                    best_val, bad = val, 0
                    best_state = {k: v.detach().clone() for k, v in self.net.state_dict().items()}
                else:
                    bad += 1
            self.history.append(record)
            if self.verbose:
                print(" ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in record.items()))
            if n_val and bad >= self.patience:
                break
        if best_state is not None:
            self.net.load_state_dict(best_state)
        self.net.eval()
        return self

    def _loss(self, x, m, y, idx) -> float:
        import torch

        self.net.eval()
        with torch.no_grad():
            total = 0.0
            for a in range(0, len(idx), 4096):
                sel = torch.as_tensor(idx[a : a + 4096], device=x.device)
                logits = self.net(x[sel], m[sel])
                total += float(
                    torch.nn.functional.binary_cross_entropy_with_logits(logits, y[sel], reduction="sum")
                )
        return total / max(len(idx), 1)

    def __call__(self, strands: Sequence[Strand]) -> np.ndarray:
        import torch

        if self.net is None:
            raise RuntimeError("DenseRiskModel is not fitted")
        strands = list(strands)
        if not strands:
            return np.zeros(0, dtype=np.float64)
        out = np.empty(len(strands), dtype=np.float64)
        dev = torch.device(self.device)
        self.net.eval()
        with torch.no_grad():
            for a in range(0, len(strands), 8192):
                batch = strands[a : a + 8192]
                x, m = one_hot(batch, max(MAX_LENGTH, max(len(s) for s in batch)))
                logits = self.net(torch.from_numpy(x).to(dev), torch.from_numpy(m).to(dev))
                out[a : a + len(batch)] = torch.sigmoid(logits.float()).cpu().numpy()
        return out

    def save(self, path: str | Path) -> Path:
        import torch

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "kind": "dense",
                "config": self.config,
                "dense_weight": self.dense_weight,
                "state": None if self.net is None else {k: v.cpu() for k, v in self.net.state_dict().items()},
                "history": self.history,
                "meta": self.meta,
            },
            path,
        )
        return path

    @classmethod
    def load(cls, path: str | Path, device: str | None = None) -> DenseRiskModel:
        import torch

        payload = torch.load(Path(path), map_location="cpu", weights_only=False)
        model = cls(**payload["config"], dense_weight=payload.get("dense_weight", 1.0), device=device)
        model.history = payload.get("history", [])
        model.meta = payload.get("meta", {})
        if payload["state"] is not None:
            model.net = _build_dense_cnn(**model.config)
            model.net.load_state_dict(payload["state"])
            model.net.to(model.device).eval()
        return model


# ------------------------------------------------------------------ selection regret


def selection_regret(
    scores: np.ndarray, truth: np.ndarray, slots: int, candidates: int
) -> dict[str, float]:
    """What the encoder actually cares about: pick one candidate per slot, how bad is it?

    `scores` and `truth` are (slots * candidates,) in slot-major order. Returns the mean true
    failure rate of the picked strands, next to the oracle (lowest true failure in the slot)
    and the first candidate (no selection at all).
    """
    s = np.asarray(scores, dtype=np.float64).reshape(slots, candidates)
    t = np.asarray(truth, dtype=np.float64).reshape(slots, candidates)
    ok = ~np.isnan(t).any(axis=1)
    s, t = s[ok], t[ok]
    if not len(t):
        return {"picked": float("nan"), "oracle": float("nan"), "first": float("nan"), "slots": 0}
    picked = t[np.arange(len(t)), s.argmin(axis=1)]
    return {
        "picked": float(picked.mean()),
        "oracle": float(t.min(axis=1).mean()),
        "first": float(t[:, 0].mean()),
        "mean_all": float(t.mean()),
        "slots": int(len(t)),
    }
