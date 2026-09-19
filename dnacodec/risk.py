"""Risk model. Owner: Agent F.

Learns which strands the decoder fails on in a given situation, and scores candidates
for the encoder. Fits the Scorer type in dnacodec.types.

Pipeline (PROJECT.md, "Risk labels are probabilities, not single failures"):

    generate_strands()      controlled training sequences (never Microsoft references)
    label_failure_rates()   simulate each strand K times, decode with a frozen decoder,
                            label = fraction of non-empty clusters decoded wrongly
    RiskModel.fit()         small 1D CNN regressing that failure rate
    RiskModel(strands)      risk in [0, 1], usable directly as an encoder Scorer

Seed discipline: every function that draws randomness takes a base seed and a `heldout`
flag. With heldout=False (the default, for training and tuning) the seed must pass
train_seed(); with heldout=True it must be one of heldout_seeds(), and that path is only
for evaluation. Per-chunk seeds are derived with numpy's SeedSequence from (base seed,
chunk index), so train and held-out streams never overlap and results do not depend on
the number of workers.

torch is imported lazily so that labeling workers (numpy only) start fast.
"""

from __future__ import annotations

import os
import pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .profiles import SituationProfile
from .seeds import is_heldout, train_seed
from .types import ALPHABET, Decoder, Strand

MAX_LENGTH = 140  # longest strand the CNN is built for (longer strands are still scored)
LABEL_CHUNK = 64  # strands per labeling task; fixed so results never depend on workers

_CODE = np.full(256, 0, dtype=np.int64)
for _i, _b in enumerate(ALPHABET):
    _CODE[ord(_b)] = _i
_ALPHA = np.frombuffer(ALPHABET.encode("ascii"), dtype=np.uint8)


# --------------------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------------------


def _check_seed(seed: int, heldout: bool) -> int:
    if heldout:
        if not is_heldout(seed):
            raise ValueError(f"heldout=True needs a seed from heldout_seeds(), got {seed}")
        return seed
    return train_seed(seed)


def _derived_seed(seed: int, *keys: int) -> int:
    """Independent 63-bit seed for (seed, keys). Never lands in the held-out range by
    construction of the check below (the range is tiny, but we make it impossible)."""
    value = int(np.random.SeedSequence([seed, *keys]).generate_state(2, np.uint64)[0] >> np.uint64(1))
    return value + 1_000_000 if is_heldout(value) else value


# --------------------------------------------------------------------------------------
# Sequence features (numpy)
# --------------------------------------------------------------------------------------


def _codes(strand: str) -> np.ndarray:
    return _CODE[np.frombuffer(strand.encode("ascii"), dtype=np.uint8)]


def _decode_codes(codes: np.ndarray) -> str:
    return _ALPHA[codes].tobytes().decode("ascii")


def max_run_length(strand: str) -> int:
    """Longest homopolymer run in the strand."""
    if not strand:
        return 0
    best = run = 1
    for a, b in zip(strand, strand[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return best


def gc_fraction(strand: str) -> float:
    return (strand.count("G") + strand.count("C")) / max(len(strand), 1)


# --------------------------------------------------------------------------------------
# 1. Controlled sequence generator
# --------------------------------------------------------------------------------------

# Default share of each strand family. Uniform random strands anchor the model on what
# the encoder actually produces; the others deliberately vary known risk factors.
DEFAULT_MIX: dict[str, float] = {
    "uniform": 0.30,
    "homopolymer": 0.25,  # 1 to 3 inserted runs of length 2 to 10
    "gc": 0.20,  # i.i.d. bases at a GC fraction drawn from [0.15, 0.85]
    "motif": 0.10,  # tandem repeats (di/tri/tetra units) and copied segments
    "mixed": 0.15,  # skewed GC plus inserted runs plus sometimes a motif
}


def _uniform(rng: np.random.Generator, length: int) -> np.ndarray:
    return rng.integers(0, 4, size=length)


def _biased(rng: np.random.Generator, length: int, gc: float) -> np.ndarray:
    # A, C, G, T with P(C) = P(G) = gc / 2 and P(A) = P(T) = (1 - gc) / 2
    p = np.array([(1 - gc) / 2, gc / 2, gc / 2, (1 - gc) / 2])
    return rng.choice(4, size=length, p=p)


def _insert_runs(rng: np.random.Generator, codes: np.ndarray, n_runs: int, max_run: int) -> np.ndarray:
    codes = codes.copy()
    length = len(codes)
    for _ in range(n_runs):
        run = int(rng.integers(2, max_run + 1))
        run = min(run, length)
        start = int(rng.integers(0, length - run + 1))
        codes[start : start + run] = rng.integers(0, 4)
    return codes


def _insert_motif(rng: np.random.Generator, codes: np.ndarray) -> np.ndarray:
    codes = codes.copy()
    length = len(codes)
    if rng.random() < 0.6:
        # tandem repeat of a short unit, e.g. ATATAT... or CAGCAGCAG...
        unit_len = int(rng.integers(2, 5))
        unit = rng.integers(0, 4, size=unit_len)
        span = min(int(rng.integers(8, 31)), length)
        rep = np.resize(unit, span)
        start = int(rng.integers(0, length - span + 1))
        codes[start : start + span] = rep
    else:
        # copy of a segment elsewhere in the strand (optionally reverse complement)
        span = min(int(rng.integers(8, 21)), length // 2)
        a = int(rng.integers(0, length - span + 1))
        b = int(rng.integers(0, length - span + 1))
        segment = codes[a : a + span].copy()
        if rng.random() < 0.5:
            segment = 3 - segment[::-1]  # A<->T, C<->G under ACGT order
        codes[b : b + span] = segment
    return codes


def generate_strands(
    n: int,
    length: int | tuple[int, int] = MAX_LENGTH,
    seed: int = 0,
    mix: dict[str, float] | None = None,
    max_run: int = 10,
    heldout: bool = False,
) -> list[Strand]:
    """Controlled strands: uniform random plus deliberately varied risk factors.

    length: fixed length or an inclusive (min, max) range drawn uniformly per strand.
    mix: share of each family in DEFAULT_MIX (normalized). Families are assigned in a
    shuffled order so every prefix of the list has roughly the right mix.
    Deterministic per seed. Never uses Microsoft references (their README says they are
    not uniformly random).
    """
    _check_seed(seed, heldout)
    mix = dict(DEFAULT_MIX if mix is None else mix)
    unknown = set(mix) - set(DEFAULT_MIX)
    if unknown:
        raise ValueError(f"unknown strand families {sorted(unknown)}")
    rng = np.random.default_rng(_derived_seed(seed, 1))
    names = list(mix)
    weights = np.array([mix[k] for k in names], dtype=np.float64)
    families = rng.choice(len(names), size=n, p=weights / weights.sum())
    lo, hi = (length, length) if isinstance(length, int) else length
    out: list[Strand] = []
    for fam in families.tolist():
        ln = int(rng.integers(lo, hi + 1))
        kind = names[fam]
        if kind == "uniform":
            codes = _uniform(rng, ln)
        elif kind == "homopolymer":
            codes = _insert_runs(rng, _uniform(rng, ln), int(rng.integers(1, 4)), max_run)
        elif kind == "gc":
            codes = _biased(rng, ln, float(rng.uniform(0.15, 0.85)))
        elif kind == "motif":
            codes = _insert_motif(rng, _uniform(rng, ln))
        else:  # mixed
            codes = _biased(rng, ln, float(rng.uniform(0.2, 0.8)))
            codes = _insert_runs(rng, codes, int(rng.integers(1, 3)), max_run)
            if rng.random() < 0.3:
                codes = _insert_motif(rng, codes)
        out.append(_decode_codes(codes))
    return out


# --------------------------------------------------------------------------------------
# 2. Failure-rate labeling
# --------------------------------------------------------------------------------------


def _label_chunk(args: tuple) -> tuple[np.ndarray, np.ndarray]:
    """Worker: K independent clusters per strand in one simulate() call, then decode."""
    from .simulator import simulate

    strands, decoder, profile, k, chunk_seed = args
    repeated = [s for s in strands for _ in range(k)]
    clusters = simulate(repeated, profile, chunk_seed)
    fails = np.zeros(len(strands), dtype=np.int64)
    valid = np.zeros(len(strands), dtype=np.int64)
    # Decode each strand length group separately (the Decoder protocol takes one length).
    by_len: dict[int, list[int]] = {}
    for i, s in enumerate(repeated):
        by_len.setdefault(len(s), []).append(i)
    for strand_length, idx in by_len.items():
        nonempty = [i for i in idx if clusters[i]]
        if not nonempty:
            continue
        decoded = decoder.decode([clusters[i] for i in nonempty], strand_length)
        for i, guess in zip(nonempty, decoded):
            owner = i // k
            valid[owner] += 1
            fails[owner] += guess != repeated[i]
    return fails, valid


def label_failure_counts(
    strands: Sequence[Strand],
    decoder: Decoder,
    profile: SituationProfile,
    k: int = 16,
    seed: int = 0,
    workers: int | None = None,
    heldout: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    """(failures, non-empty clusters) per strand over K simulated clusters each.

    workers=None uses all CPU cores; workers=1 runs in this process (use that for a GPU
    decoder). Results are identical for any number of workers.
    """
    _check_seed(seed, heldout)
    strands = list(strands)
    tasks = [
        (strands[a : a + LABEL_CHUNK], decoder, profile, k, _derived_seed(seed, 2, j))
        for j, a in enumerate(range(0, len(strands), LABEL_CHUNK))
    ]
    workers = (os.cpu_count() or 1) if workers is None else workers
    if workers <= 1 or len(tasks) <= 1:
        results = [_label_chunk(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
            results = list(pool.map(_label_chunk, tasks, chunksize=1))
    if not results:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    fails = np.concatenate([r[0] for r in results])
    valid = np.concatenate([r[1] for r in results])
    return fails, valid


def label_failure_rates(
    strands: Sequence[Strand],
    decoder: Decoder,
    profile: SituationProfile,
    k: int = 16,
    seed: int = 0,
    workers: int | None = None,
    heldout: bool = False,
) -> np.ndarray:
    """Failure rate per strand: fraction of its K simulated clusters decoded wrongly.

    Each strand is simulated K times at the profile's coverage (the whole batch goes
    through simulate() at once, each strand repeated K times), decoded with the frozen
    decoder, and compared to the strand exactly.

    Dropouts (empty clusters) are left out of the denominator: in our simulator dropout
    depends only on GC and the profile's loss rates, not on how hard the strand is to
    decode, and the encoder's fountain code handles lost strands as erasures anyway. So
    label = failures / non-empty clusters. A strand whose K clusters are all empty gets
    NaN; RiskModel.fit ignores NaN labels.
    """
    fails, valid = label_failure_counts(strands, decoder, profile, k, seed, workers, heldout)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(valid > 0, fails / np.maximum(valid, 1), np.nan)


def _decode_chunk(args: tuple) -> list[Strand | None]:
    decoder, clusters, strand_length = args
    return decoder.decode(clusters, strand_length)


def decode_parallel(
    decoder: Decoder, clusters: Sequence[list[str]], strand_length: int, workers: int | None = None
) -> list[Strand | None]:
    """decoder.decode over clusters, split across CPU cores (same output as one call for
    any deterministic decoder). Used to get per-cluster failures on real held-out data."""
    clusters = list(clusters)
    chunk = 256
    tasks = [(decoder, clusters[a : a + chunk], strand_length) for a in range(0, len(clusters), chunk)]
    workers = (os.cpu_count() or 1) if workers is None else workers
    if workers <= 1 or len(tasks) <= 1:
        parts = [_decode_chunk(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(tasks))) as pool:
            parts = list(pool.map(_decode_chunk, tasks, chunksize=1))
    return [s for part in parts for s in part]


# --------------------------------------------------------------------------------------
# Metrics used to report the risk model (ranking quality, not decoder accuracy)
# --------------------------------------------------------------------------------------


def _rankdata(x: np.ndarray) -> np.ndarray:
    """Average ranks (1-based), ties share their mean rank."""
    x = np.asarray(x, dtype=np.float64)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=np.float64)
    sorted_x = x[order]
    boundaries = np.flatnonzero(np.diff(sorted_x)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(x)]))
    for a, b in zip(starts.tolist(), ends.tolist()):
        ranks[order[a:b]] = (a + b + 1) / 2.0
    return ranks


def roc_auc(y_true: np.ndarray, score: np.ndarray) -> float:
    """ROC AUC via the Mann-Whitney statistic (ties count half). NaN if one class is empty."""
    y = np.asarray(y_true).astype(bool)
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = _rankdata(score)
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rankdata(a), _rankdata(b)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


# --------------------------------------------------------------------------------------
# 3. Models
# --------------------------------------------------------------------------------------


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def one_hot(strands: Sequence[Strand], width: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(B, 6, W) float32 input and (B, W) bool mask.

    Channels: one-hot A, C, G, T, the mask, and relative position (0 at the first base,
    1 at the last), so the CNN can learn position effects like the error ramp.
    """
    lengths = np.fromiter((len(s) for s in strands), dtype=np.int64, count=len(strands))
    w = int(width or max(int(lengths.max(initial=1)), 1))
    x = np.zeros((len(strands), 6, w), dtype=np.float32)
    mask = np.arange(w)[None, :] < lengths[:, None]
    if len(strands):
        flat = _CODE[np.frombuffer("".join(strands).encode("ascii"), dtype=np.uint8)]
        rows = np.repeat(np.arange(len(strands)), lengths)
        cols = np.concatenate([np.arange(n) for n in lengths.tolist()])
        keep = cols < w
        x[rows[keep], flat[keep], cols[keep]] = 1.0
        x[:, 4, :] = mask
        x[:, 5, :] = np.where(mask, np.arange(w)[None, :] / np.maximum(lengths[:, None] - 1, 1), 0.0)
    return x, mask


def _conv(conv, x):
    """conv(x), but on CPU as im2col + matmul: this torch build's CPU conv1d (no mkldnn on
    macOS arm64) is ~20x slower than BLAS. Same weights, same result."""
    import torch

    if not x.is_cpu:
        return conv(x)
    k = conv.kernel_size[0]
    pad = conv.padding[0]
    b, c, length = x.shape
    cols = torch.nn.functional.pad(x, (pad, pad)).unfold(2, k, 1)  # (B, C, L, k)
    cols = cols.permute(0, 2, 1, 3).reshape(b, length, c * k)
    y = torch.nn.functional.linear(cols, conv.weight.reshape(conv.out_channels, c * k), conv.bias)
    return y.transpose(1, 2)


def _build_cnn(channels: int, n_blocks: int, dropout: float):
    import torch
    from torch import nn

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

    class RiskCNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.stem = nn.Conv1d(6, channels, 9, padding=4)
            self.blocks = nn.ModuleList(Block() for _ in range(n_blocks))
            self.head = nn.Sequential(
                nn.Linear(2 * channels, channels), nn.GELU(), nn.Dropout(dropout), nn.Linear(channels, 1)
            )

        def forward(self, x, mask):
            m = mask[:, None, :].to(x.dtype)
            h = torch.nn.functional.gelu(_conv(self.stem, x)) * m
            for block in self.blocks:
                h = block(h, m)
            mean = h.sum(-1) / m.sum(-1).clamp(min=1.0)
            mx = h.masked_fill(m == 0, -1e4).amax(-1)
            return self.head(torch.cat([mean, mx], dim=1)).squeeze(-1)

    return RiskCNN()


class _TopKmersMixin:
    """k-mer attribution by scoring: insert each k-mer into random backgrounds."""

    _kmer_cache: dict

    def kmer_effects(self, k: int = 6, backgrounds: int | None = None, length: int = 110) -> dict[str, float]:
        """Mean predicted risk of random strands of `length` with the k-mer written at a
        random position, for every k-mer. Also stores the background mean risk in
        self.background_risk. Deterministic (fixed internal seed)."""
        cache = self.__dict__.setdefault("_kmer_cache", {})
        key = (k, backgrounds, length)
        if key in cache:
            return cache[key]
        n_kmers = 4**k
        backgrounds = backgrounds or max(8, min(64, 262_144 // n_kmers))
        rng = np.random.default_rng(_derived_seed(train_seed(0), 3, k))
        bg = rng.integers(0, 4, size=(backgrounds, length))
        pos = rng.integers(0, length - k + 1, size=backgrounds)
        kmers = (np.arange(n_kmers)[:, None] // (4 ** np.arange(k - 1, -1, -1))[None, :]) % 4
        seqs = np.repeat(bg[None, :, :], n_kmers, axis=0)  # (n_kmers, backgrounds, length)
        for j in range(backgrounds):
            seqs[:, j, pos[j] : pos[j] + k] = kmers
        flat = seqs.reshape(-1, length)
        strands = [_decode_codes(row) for row in flat]
        risk = self(strands).reshape(n_kmers, backgrounds).mean(axis=1)
        self.background_risk = float(np.mean(self([_decode_codes(r) for r in bg])))
        effects = {_decode_codes(kmers[i]): float(risk[i]) for i in range(n_kmers)}
        cache[key] = effects
        return effects

    def top_kmers(self, k: int = 6, n: int = 20) -> list[tuple[str, float]]:
        """Most risky k-mers with their risk, for the dashboard.

        Risk of a k-mer = mean predicted risk of random 110-base strands with that k-mer
        written in at a random position (compare to self.background_risk, the mean risk of
        the same backgrounds without it)."""
        effects = self.kmer_effects(k)
        return sorted(effects.items(), key=lambda kv: -kv[1])[:n]


class RiskModel(_TopKmersMixin):
    """Small CNN (or k-mer logistic regression as a fallback) over one-hot strands.

    Dilated residual 1D CNN over one-hot bases plus mask and relative position, masked
    mean and max pooling, one logit. Trained with BCE on soft targets (the failure rate).
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
        patience: int = 6,
        seed: int = 0,
        device: str | None = None,
        verbose: bool = False,
    ) -> None:
        self.config = dict(channels=channels, n_blocks=n_blocks, dropout=dropout)
        self.epochs, self.batch_size, self.lr = epochs, batch_size, lr
        self.weight_decay, self.val_fraction, self.patience = weight_decay, val_fraction, patience
        self.seed = train_seed(seed)
        self.device = device or pick_device()
        self.verbose = verbose
        self.net = None
        self.history: list[dict[str, float]] = []
        self.meta: dict[str, Any] = {}

    # -- training --------------------------------------------------------------------

    def fit(self, strands: Sequence[Strand], failed: np.ndarray) -> RiskModel:
        """failed[i] = 1.0 if the decoder did not reconstruct strands[i] exactly."""
        import torch

        strands = list(strands)
        y = np.asarray(failed, dtype=np.float32).reshape(-1)
        if len(y) != len(strands):
            raise ValueError(f"{len(strands)} strands but {len(y)} labels")
        keep = ~np.isnan(y)
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
        x_all_t = torch.from_numpy(x_all).to(dev)
        m_all_t = torch.from_numpy(m_all).to(dev)
        y_all_t = torch.from_numpy(y).to(dev)

        self.net = _build_cnn(**self.config).to(dev)
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        steps = max(1, self.epochs * int(np.ceil(len(tr_idx) / self.batch_size)))
        sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=self.lr, total_steps=steps)
        loss_fn = torch.nn.BCEWithLogitsLoss()

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
                logits = self.net(x_all_t[idx], m_all_t[idx])
                loss = loss_fn(logits, y_all_t[idx])
                opt.zero_grad()
                loss.backward()
                opt.step()
                sched.step()
                total += float(loss.detach()) * len(idx)
            record = {"epoch": epoch, "train_loss": total / max(len(tr_idx), 1)}
            if n_val:
                val = self._loss(x_all_t, m_all_t, y_all_t, val_idx)
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
        self.__dict__.pop("_kmer_cache", None)
        return self

    def _loss(self, x, m, y, idx) -> float:
        import torch

        self.net.eval()
        with torch.no_grad():
            total = 0.0
            for a in range(0, len(idx), 4096):
                sel = torch.as_tensor(idx[a : a + 4096], device=x.device)
                logits = self.net(x[sel], m[sel])
                total += float(torch.nn.functional.binary_cross_entropy_with_logits(logits, y[sel], reduction="sum"))
        return total / max(len(idx), 1)

    # -- scoring ---------------------------------------------------------------------

    def __call__(self, strands: Sequence[Strand]) -> np.ndarray:
        """Risk in [0, 1] per strand. Usable directly as an encoder Scorer."""
        import torch

        if self.net is None:
            raise RuntimeError("RiskModel is not fitted")
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

    # -- persistence -----------------------------------------------------------------

    def _payload(self) -> dict:
        return {
            "kind": "cnn",
            "config": self.config,
            "train": dict(
                epochs=self.epochs, batch_size=self.batch_size, lr=self.lr,
                weight_decay=self.weight_decay, val_fraction=self.val_fraction,
                patience=self.patience, seed=self.seed,
            ),
            "state": None if self.net is None else {k: v.cpu() for k, v in self.net.state_dict().items()},
            "history": self.history,
            "meta": self.meta,
        }

    @classmethod
    def _from_payload(cls, payload: dict, device: str | None = None) -> RiskModel:
        model = cls(**payload["config"], **payload["train"], device=device)
        model.history = payload.get("history", [])
        model.meta = payload.get("meta", {})
        if payload["state"] is not None:
            model.net = _build_cnn(**model.config)
            model.net.load_state_dict(payload["state"])
            model.net.to(model.device).eval()
        return model

    def save(self, path: str | Path) -> Path:
        import torch

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self._payload(), path)
        return path

    @classmethod
    def load(cls, path: str | Path, device: str | None = None) -> RiskModel:
        return load_risk_model(path, device)  # type: ignore[return-value]

    def __getstate__(self) -> dict:
        # Pickles (e.g. into worker processes) carry CPU weights; the device is re-picked.
        return self._payload()

    def __setstate__(self, payload: dict) -> None:
        self.__dict__.update(RiskModel._from_payload(payload).__dict__)


# --------------------------------------------------------------------------------------
# Fallback: k-mer logistic regression with the same interface
# --------------------------------------------------------------------------------------


def kmer_features(strands: Sequence[Strand], max_k: int = 4, max_run: int = 10) -> np.ndarray:
    """k-mer frequencies for k = 1..max_k, one-hot max run length (1..max_run+), GC,
    GC squared, and length / MAX_LENGTH."""
    n_kmer = sum(4**k for k in range(1, max_k + 1))
    feats = np.zeros((len(strands), n_kmer + max_run + 3), dtype=np.float32)
    for i, s in enumerate(strands):
        c = _codes(s)
        off = 0
        for k in range(1, max_k + 1):
            if len(c) >= k:
                idx = np.zeros(len(c) - k + 1, dtype=np.int64)
                for j in range(k):
                    idx = idx * 4 + c[j : len(c) - k + 1 + j]
                feats[i, off : off + 4**k] = np.bincount(idx, minlength=4**k) / len(idx)
            off += 4**k
        feats[i, off + min(max_run_length(s), max_run) - 1] = 1.0
        gc = gc_fraction(s)
        feats[i, off + max_run : off + max_run + 3] = (gc, (gc - 0.5) ** 2 * 4, len(s) / MAX_LENGTH)
    return feats


class KmerRiskModel(_TopKmersMixin):
    """Logistic regression on k-mer frequencies and run/GC features. Same interface as
    RiskModel; cheap and CPU only. Soft-target BCE with L2, full-batch L-BFGS."""

    def __init__(self, max_k: int = 4, l2: float = 1e-3, seed: int = 0) -> None:
        self.max_k, self.l2, self.seed = max_k, l2, train_seed(seed)
        self.w: np.ndarray | None = None
        self.mu: np.ndarray | None = None
        self.sd: np.ndarray | None = None
        self.meta: dict[str, Any] = {}

    def fit(self, strands: Sequence[Strand], failed: np.ndarray) -> KmerRiskModel:
        import torch

        y = np.asarray(failed, dtype=np.float64).reshape(-1)
        keep = ~np.isnan(y)
        strands = [s for s, ok in zip(strands, keep) if ok]
        y = np.clip(y[keep], 0, 1)
        x = kmer_features(strands, self.max_k).astype(np.float64)
        self.mu, self.sd = x.mean(0), x.std(0) + 1e-6
        xt = torch.from_numpy((x - self.mu) / self.sd)
        xt = torch.cat([xt, torch.ones(len(xt), 1, dtype=xt.dtype)], dim=1)
        yt = torch.from_numpy(y)
        w = torch.zeros(xt.shape[1], dtype=xt.dtype, requires_grad=True)
        opt = torch.optim.LBFGS([w], max_iter=200, line_search_fn="strong_wolfe")

        def closure():
            opt.zero_grad()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(xt @ w, yt) + self.l2 * (w[:-1] ** 2).sum()
            loss.backward()
            return loss

        opt.step(closure)
        self.w = w.detach().numpy()
        self.__dict__.pop("_kmer_cache", None)
        return self

    def __call__(self, strands: Sequence[Strand]) -> np.ndarray:
        if self.w is None:
            raise RuntimeError("KmerRiskModel is not fitted")
        strands = list(strands)
        if not strands:
            return np.zeros(0)
        x = (kmer_features(strands, self.max_k) - self.mu) / self.sd
        z = x @ self.w[:-1] + self.w[-1]
        return 1.0 / (1.0 + np.exp(-z))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({"kind": "kmer", "max_k": self.max_k, "l2": self.l2, "seed": self.seed,
                         "w": self.w, "mu": self.mu, "sd": self.sd, "meta": self.meta}, f)
        return path


def load_risk_model(path: str | Path, device: str | None = None) -> RiskModel | KmerRiskModel:
    """Load a model saved by RiskModel.save or KmerRiskModel.save."""
    path = Path(path)
    try:
        import torch

        payload = torch.load(path, map_location="cpu", weights_only=False)
    except Exception:
        with path.open("rb") as f:
            payload = pickle.load(f)
    if payload.get("kind") == "kmer":
        model = KmerRiskModel(payload["max_k"], payload["l2"], payload["seed"])
        model.w, model.mu, model.sd, model.meta = payload["w"], payload["mu"], payload["sd"], payload["meta"]
        return model
    return RiskModel._from_payload(payload, device)


# --------------------------------------------------------------------------------------
# Probes for the sanity tables and the dashboard
# --------------------------------------------------------------------------------------


def homopolymer_probe(
    model, run_lengths: Sequence[int] = (1, 2, 3, 4, 5, 6, 7, 8, 10), n: int = 512, length: int = 140
) -> dict[int, float]:
    """Mean predicted risk of random strands whose longest run is capped at 2, with one
    run of exactly r bases written at a random position (r=1: no run inserted)."""
    rng = np.random.default_rng(_derived_seed(train_seed(0), 5))
    base = _no_runs(rng, n, length)
    out = {}
    for r in run_lengths:
        seqs = base.copy()
        if r > 1:
            for i in range(n):
                p = int(rng.integers(1, length - r))
                letter = int(rng.integers(0, 4))
                seqs[i, p : p + r] = letter
                # keep the run exactly r long: neighbors must differ from the run letter
                if seqs[i, p - 1] == letter:
                    seqs[i, p - 1] = (letter + 1) % 4
                if p + r < length and seqs[i, p + r] == letter:
                    seqs[i, p + r] = (letter + 2) % 4
        out[int(r)] = float(np.mean(model([_decode_codes(s) for s in seqs])))
    return out


def gc_probe(
    model, gc_values: Sequence[float] = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8), n: int = 512, length: int = 140
) -> dict[float, float]:
    """Mean predicted risk of i.i.d. strands at a GC fraction, with runs capped at 3 so
    the GC effect is not just a homopolymer effect in disguise."""
    rng = np.random.default_rng(_derived_seed(train_seed(0), 6))
    out = {}
    for gc in gc_values:
        seqs = []
        while len(seqs) < n:
            s = _decode_codes(_biased(rng, length, gc))
            if max_run_length(s) <= 3:
                seqs.append(s)
        out[float(gc)] = float(np.mean(model(seqs)))
    return out


def _no_runs(rng: np.random.Generator, n: int, length: int) -> np.ndarray:
    """Random strands with no homopolymer longer than 2."""
    seqs = rng.integers(0, 4, size=(n, length))
    for j in range(2, length):
        bad = (seqs[:, j] == seqs[:, j - 1]) & (seqs[:, j - 1] == seqs[:, j - 2])
        seqs[bad, j] = (seqs[bad, j] + rng.integers(1, 4, size=int(bad.sum()))) % 4
    return seqs
