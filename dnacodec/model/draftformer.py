"""DraftFormer: cross-read attention over the aligned reads, then an autoregressive decoder.

Why a third decoder. The v1 polisher (``dnacodec.model.polish``) sees 17 *aggregate* numbers
per draft position and predicts, independently per position, an edit to the draft. Two things
are thrown away by that:

1. **Which read said what.** A 2-2 split at 4 reads is a coin flip for a vote counter, but not
   for a model that can see that one of the two reads is out of register three bases earlier
   and the other matches the consensus everywhere else.
2. **Joint decisions along the strand.** An insertion and a deletion are not independent
   events: choosing to delete position 40 shifts everything after it. v1 approximates the
   coupling with a hand rule (pair the k best deletions with the k best insertions).
   A published 100M-parameter per-position transformer (DNAformer) scores the same as our
   0.8M CNN at 4 reads, while an autoregressive model (TReconLM) is 11 points better, which
   is evidence that the missing ingredient is joint decoding, not capacity.

So this module keeps the classic alignment as a *hint* (learning alignment from scratch is
what sank ``ConsensusNet``, see docs/MODELS.md section 3) and changes the two things above:

    reads ──▶ at every draft position, every read contributes the WINDOW letters around the
    │         one aligned to it, **in the read's own coordinates**, plus whether it has a base
    │         there at all and how far the whole read sits from the draft. A read base
    │         therefore carries its own local context, including bases the alignment
    │         mis-assigned, which the aligned-column view destroys.
    ▼
    cross-read attention: one query per draft position (built from the v1 17 features, the
    draft base, coverage and the position) attends over the reads at that position. Attention
    weights are the learned "how much do I trust read r here", which a vote count cannot do.
    ▼
    trunk: self-attention along the draft, memory M of length strand_length
    ▼
    autoregressive decoder: emits the strand left to right, cross-attending to M. Exactly
    strand_length tokens by construction, so no length repair is needed at all.

An auxiliary v1-style edit head sits on the trunk. It speeds up early training and gives a
non-autoregressive fallback (``mode="edit"``) for diagnosis.

Nothing here touches ``dnacodec.baseline``, ``dnacodec.model.polish`` or their checkpoints;
the draft and its vote features are imported from them so the three decoders are comparable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F
from rapidfuzz.distance import Levenshtein
from torch import nn

from ..baseline import MAX_READS, _pick_draft, _to_array, subsample
from ..types import ALPHABET, Cluster, Strand
from .polish import N_FEATURES, N_INS, N_OPS, apply_edits, features_of, labels_of, refine_draft

NO_ALIGN = -1  # gather index for "this read has no base at this draft position"
BOS = 4  # decoder start token
_LETTERS = np.frombuffer(ALPHABET.encode(), dtype=np.uint8)


# ---------------------------------------------------------------- alignment packing


def align_read(draft: str, read: str, arr: np.ndarray, n_pos: int):
    """Align one read to the draft and return it in both coordinate systems.

    Returns (gather, coord, is_ins):
      gather[i]  index into the read aligned to draft position i, or NO_ALIGN (int32, n_pos)
      coord[j]   draft position read base j belongs to, in 0..n_pos (int32, len(read))
      is_ins[j]  1 when read base j is an extra base not present in the draft (int8)

    The alignment is ``rapidfuzz`` Levenshtein opcodes, the same call and the same gap
    convention as ``dnacodec.baseline._votes``, so the draft and the columns agree with v1.
    """
    gather = np.full(n_pos, NO_ALIGN, dtype=np.int32)
    coord = np.zeros(len(read), dtype=np.int32)
    is_ins = np.ones(len(read), dtype=np.int8)
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(draft, read):
        if tag == "equal" or tag == "replace":
            m = min(i2 - i1, j2 - j1)
            gather[i1 : i1 + m] = np.arange(j1, j1 + m, dtype=np.int32)
            coord[j1 : j1 + m] = np.arange(i1, i1 + m, dtype=np.int32)
            is_ins[j1 : j1 + m] = 0
            if j2 - j1 > m:  # defensive: unequal replace block, the rest counts as inserted
                coord[j1 + m : j2] = i2
        elif tag == "insert":
            coord[j1:j2] = i1
        # "delete": those draft positions keep NO_ALIGN
    return gather, coord, is_ins


HALF_WINDOW = 3  # read bases kept on each side of the aligned one
WINDOW = 2 * HALF_WINDOW + 1
OUTSIDE = 4  # window value for "past the end of this read"


@dataclass
class Pack:
    """One cluster, ready for the model. All arrays are fixed size so they batch directly.

    The window is the point of this decoder. At draft position i, read r contributes not one
    voted letter but the ``WINDOW`` letters around the aligned one **in the read's own
    coordinates**, so a read that slipped out of register, or that carries an extra base
    three letters earlier, still shows what it actually said. When the read has no base at a
    draft position, the window is centred where that base would have been, so a deletion is
    visible in context instead of being one anonymous "missing" vote.
    """

    win: np.ndarray  # (R, L, WINDOW) int8, read bases around the aligned one, OUTSIDE = past the end
    aligned: np.ndarray  # (R, L) int8, 1 = this read has a base at this draft position
    rqual: np.ndarray  # (R,) float16, share of draft positions this read disagrees with
    feats: np.ndarray  # (N_FEATURES, L) float16, the v1 vote features of the same draft
    draft: np.ndarray  # (L,) int8
    n_reads: int


def read_window(draft: str, read: str, arr: np.ndarray, n_pos: int):
    """(window, aligned) for one read against the draft. See Pack."""
    gather, _, _ = align_read(draft, read, arr, n_pos)
    aligned = gather >= 0
    # where the read has no base, centre on the position the missing base would have had:
    # one past the last aligned base before it.
    prev = np.where(aligned, gather, -1)
    np.maximum.accumulate(prev, out=prev)
    centre = np.where(aligned, gather, prev + 1)
    idx = centre[:, None] + np.arange(-HALF_WINDOW, HALF_WINDOW + 1)[None, :]
    inside = (idx >= 0) & (idx < len(read))
    win = np.where(inside, arr[np.clip(idx, 0, max(len(read) - 1, 0))], OUTSIDE)
    return win.astype(np.int8), aligned.astype(np.int8)


def pack_cluster(cluster: Cluster, strand_length: int, max_reads: int = MAX_READS):
    """(draft string, Pack) for one cluster, or (None, None) when it has no read."""
    reads = subsample([r for r in cluster if r], max_reads)
    if not reads:
        return None, None
    arrays = [_to_array(r) for r in reads]
    draft, votes = refine_draft(_pick_draft(reads, strand_length), reads, arrays, strand_length)
    n = len(reads)
    win = np.full((max_reads, strand_length, WINDOW), OUTSIDE, dtype=np.int8)
    aligned = np.zeros((max_reads, strand_length), dtype=np.int8)
    rqual = np.zeros(max_reads, dtype=np.float16)
    draft_codes = _to_array(draft)
    for r, (read, arr) in enumerate(zip(reads, arrays)):
        win[r], aligned[r] = read_window(draft, read, arr, strand_length)
        same = (win[r, :, HALF_WINDOW] == draft_codes) & (aligned[r] == 1)
        rqual[r] = 1.0 - same.mean()
    pack = Pack(
        win=win,
        aligned=aligned,
        rqual=rqual,
        feats=features_of(draft, votes).astype(np.float16),
        draft=draft_codes.astype(np.int8),
        n_reads=n,
    )
    return draft, pack


def pack_example(cluster: Cluster, strand_length: int, truth: str):
    """A training example: the Pack plus the target strand and the auxiliary edit labels."""
    draft, pack = pack_cluster(cluster, strand_length)
    if pack is None:
        return None
    ops, ins = labels_of(draft, truth)
    return pack, _to_array(truth).astype(np.int8), ops.astype(np.int8), ins.astype(np.int8)


def collate(packs: Sequence[Pack], device: torch.device | str = "cpu") -> dict:
    """Stack packs into the batch dict the model's forward wants."""
    t = lambda a, dt: torch.from_numpy(np.ascontiguousarray(a)).to(device=device, dtype=dt)  # noqa: E731
    return {
        "win": t(np.stack([p.win for p in packs]), torch.long),
        "aligned": t(np.stack([p.aligned for p in packs]), torch.long),
        "rqual": t(np.stack([p.rqual for p in packs]), torch.float32),
        "feats": t(np.stack([p.feats for p in packs]), torch.float32),
        "draft": t(np.stack([p.draft for p in packs]), torch.long),
        "n_reads": t(np.array([p.n_reads for p in packs]), torch.long),
    }


# ---------------------------------------------------------------- blocks


class _Attn(nn.Module):
    """Multi-head attention on ``F.scaled_dot_product_attention``.

    ``nn.MultiheadAttention`` falls back to the math path as soon as a key padding mask is
    given in training mode, which materializes a (batch, heads, T, S) weight tensor. With
    one sequence per read (batch * 16) that tensor alone was gigabytes and made a step 100
    times slower than its FLOPs. SDPA keeps it fused, and a cache makes autoregressive
    decoding linear instead of quadratic in the strand length.
    """

    def __init__(self, d: int, heads: int, dropout: float, d_kv: int | None = None,
                 d_attn: int | None = None):
        super().__init__()
        d_attn = d_attn or d
        if d_attn % heads:
            raise ValueError(f"d_attn={d_attn} must be divisible by heads={heads}")
        self.heads, self.hd, self.dropout = heads, d_attn // heads, dropout
        self.q = nn.Linear(d, d_attn)
        self.k = nn.Linear(d_kv or d, d_attn)
        self.v = nn.Linear(d_kv or d, d_attn)
        self.o = nn.Linear(d_attn, d)

    def _split(self, x: torch.Tensor) -> torch.Tensor:
        b, t, _ = x.shape
        return x.view(b, t, self.heads, self.hd).transpose(1, 2)

    def forward(self, x, kv=None, key_padding_mask=None, causal=False, cache=None, key=None):
        """key_padding_mask: (B, S) bool, True = padding. cache: dict for incremental decoding."""
        q = self._split(self.q(x))
        if cache is not None and key is not None and kv is not None and f"{key}_k" in cache:
            k, v = cache[f"{key}_k"], cache[f"{key}_v"]  # cross-attention: memory never changes
        else:
            src = x if kv is None else kv
            k, v = self._split(self.k(src)), self._split(self.v(src))
            if cache is not None and key is not None:
                if kv is None and f"{key}_k" in cache:  # self-attention: append this step
                    k = torch.cat([cache[f"{key}_k"], k], dim=2)
                    v = torch.cat([cache[f"{key}_v"], v], dim=2)
                cache[f"{key}_k"], cache[f"{key}_v"] = k, v
        mask = None
        if key_padding_mask is not None:
            mask = ~key_padding_mask[:, None, None, :]  # True = take part in attention
        out = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, is_causal=causal and mask is None,
            dropout_p=self.dropout if self.training else 0.0,
        )
        b, _, t, _ = out.shape
        return self.o(out.transpose(1, 2).reshape(b, t, self.heads * self.hd))


def _ffn(d: int, hidden: int) -> nn.Module:
    return nn.Sequential(nn.Linear(d, hidden), nn.GELU(), nn.Linear(hidden, d))


class _SelfBlock(nn.Module):
    """Pre-norm self-attention plus a feed-forward, the standard pair."""

    def __init__(self, d: int, heads: int, ffn: int, dropout: float):
        super().__init__()
        self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.attn = _Attn(d, heads, dropout)
        self.ff = _ffn(d, ffn)
        self.drop = nn.Dropout(dropout)

    def forward(self, x, key_padding_mask=None, causal=False, cache=None, key=None):
        x = x + self.drop(self.attn(self.n1(x), key_padding_mask=key_padding_mask,
                                    causal=causal, cache=cache, key=key))
        return x + self.drop(self.ff(self.n2(x)))


class _CrossBlock(nn.Module):
    """Pre-norm cross-attention into a memory, plus a feed-forward."""

    def __init__(self, d: int, heads: int, ffn: int, dropout: float, d_mem: int | None = None,
                 d_attn: int | None = None):
        super().__init__()
        self.n1, self.nm, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d_mem or d), nn.LayerNorm(d)
        self.attn = _Attn(d, heads, dropout, d_kv=d_mem, d_attn=d_attn)
        self.ff = _ffn(d, ffn)
        self.drop = nn.Dropout(dropout)

    def forward(self, x, mem, mem_key_padding_mask=None, cache=None, key=None):
        x = x + self.drop(self.attn(self.n1(x), kv=self.nm(mem),
                                    key_padding_mask=mem_key_padding_mask, cache=cache, key=key))
        return x + self.drop(self.ff(self.n2(x)))


# ---------------------------------------------------------------- the model


@dataclass
class DraftFormerConfig:
    d_read: int = 48
    d: int = 160
    heads: int = 4
    cross_dim: int = 64  # width of the attention over reads: the read axis carries 16x the tokens
    cross_layers: int = 2
    trunk_layers: int = 4
    dec_layers: int = 4
    ffn_mult: int = 3
    dropout: float = 0.0
    max_len: int = 200  # positional tables cover strand lengths and read lengths up to this
    max_reads: int = MAX_READS

    def to_dict(self) -> dict:
        return asdict(self)


class DraftFormer(nn.Module):
    def __init__(self, cfg: DraftFormerConfig | None = None):
        super().__init__()
        self.cfg = cfg = cfg or DraftFormerConfig()
        dr, d, ffn = cfg.d_read, cfg.d, cfg.d * cfg.ffn_mult
        # --- per-read encoder: the read-coordinate window at each draft position
        self.win_proj = nn.Linear(WINDOW * 5 + 2, dr)  # one-hots plus "aligned" and read quality
        self.read_mlp = nn.Sequential(nn.LayerNorm(dr), nn.Linear(dr, dr), nn.GELU(),
                                      nn.Linear(dr, dr))
        self.read_norm = nn.LayerNorm(dr)
        # --- draft-position query
        self.feat_proj = nn.Linear(N_FEATURES, d)
        self.draft_emb = nn.Embedding(4, d)
        self.pos_emb = nn.Embedding(cfg.max_len, d)
        self.cov_emb = nn.Embedding(cfg.max_reads + 1, d)
        self.cross_blocks = nn.ModuleList(
            [_CrossBlock(d, cfg.heads, ffn, cfg.dropout, d_mem=dr, d_attn=cfg.cross_dim)
             for _ in range(cfg.cross_layers)]
        )
        self.trunk_blocks = nn.ModuleList(
            [_SelfBlock(d, cfg.heads, ffn, cfg.dropout) for _ in range(cfg.trunk_layers)]
        )
        self.mem_norm = nn.LayerNorm(d)
        # --- auxiliary v1-style edit heads (non-autoregressive fallback, faster convergence)
        self.op_head = nn.Linear(d, N_OPS)
        self.ins_head = nn.Linear(d, N_INS)
        # --- autoregressive decoder
        self.tok_emb = nn.Embedding(5, d)  # A C G T and BOS
        self.dpos_emb = nn.Embedding(cfg.max_len, d)
        self.mem_gate = nn.Linear(d, d)
        self.dec_self = nn.ModuleList(
            [_SelfBlock(d, cfg.heads, ffn, cfg.dropout) for _ in range(cfg.dec_layers)]
        )
        self.dec_cross = nn.ModuleList(
            [_CrossBlock(d, cfg.heads, ffn, cfg.dropout) for _ in range(cfg.dec_layers)]
        )
        self.out_norm = nn.LayerNorm(d)
        self.out_head = nn.Linear(d, 4)

    # ------------------------------------------------------------ encoder

    def encode(self, batch: dict) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Batch dict -> memory (B, L, d) and the auxiliary op/insert logits."""
        win, aligned, rqual = batch["win"], batch["aligned"], batch["rqual"]
        feats, draft, n_reads = batch["feats"], batch["draft"], batch["n_reads"]
        b, r, length, _ = win.shape
        cfg = self.cfg
        dtype = self.win_proj.weight.dtype

        x = F.one_hot(win, 5).to(dtype).reshape(b, r, length, WINDOW * 5)
        x = torch.cat(
            [x, aligned[..., None].to(dtype), rqual[:, :, None, None].expand(b, r, length, 1)],
            dim=-1,
        )
        g = self.win_proj(x)
        g = self.read_norm(g + self.read_mlp(g))  # (B, R, L, d_read)
        g = g.permute(0, 2, 1, 3).reshape(b * length, r, cfg.d_read)  # (B*L, R, d_read)

        present = torch.arange(r, device=win.device)[None, :] >= n_reads[:, None]  # True = pad
        kpm = present[:, None, :].expand(b, length, r).reshape(b * length, r)

        pos = torch.arange(length, device=win.device)
        q = (
            self.feat_proj(feats.transpose(1, 2))
            + self.draft_emb(draft)
            + self.pos_emb(pos.clamp(max=cfg.max_len - 1))[None]
            + self.cov_emb(n_reads.clamp(max=cfg.max_reads))[:, None, :]
        )
        q = q.reshape(b * length, 1, cfg.d)
        for block in self.cross_blocks:
            q = block(q, g, mem_key_padding_mask=kpm)
        m = q.reshape(b, length, cfg.d)
        for block in self.trunk_blocks:
            m = block(m)
        m = self.mem_norm(m)
        return m, self.op_head(m).transpose(1, 2), self.ins_head(m).transpose(1, 2)

    # ------------------------------------------------------------ decoder

    def decode_teacher(
        self, mem: torch.Tensor, target: torch.Tensor, noise: float = 0.0
    ) -> torch.Tensor:
        """Teacher forcing. target (B, L) of base codes -> logits (B, L, 4).

        `noise` corrupts that share of the fed-back tokens with a random base. At inference
        the decoder conditions on its own output, so a model trained on a perfect history
        learns to copy the history instead of reading the memory; a little noise in training
        is the cheap guard against that (scheduled sampling without the second forward pass).
        """
        b, length, _ = mem.shape
        inp = torch.cat([torch.full((b, 1), BOS, device=target.device, dtype=target.dtype),
                         target[:, :-1]], dim=1)
        if noise > 0:
            flip = torch.rand(inp.shape, device=inp.device) < noise
            flip[:, 0] = False  # never touch BOS
            rand = torch.randint(0, 4, inp.shape, device=inp.device, dtype=inp.dtype)
            inp = torch.where(flip, rand, inp)
        return self._dec_forward(mem, inp, offset=0, causal=True)

    def _dec_forward(self, mem, inp, offset: int = 0, causal: bool = True, cache=None):
        """Decoder over `inp` (B, T). offset is the position of its first token."""
        t = inp.shape[1]
        pos = torch.arange(offset, offset + t, device=inp.device).clamp(max=self.cfg.max_len - 1)
        x = self.tok_emb(inp) + self.dpos_emb(pos)[None] + self.mem_gate(mem[:, offset : offset + t])
        for i, (self_block, cross_block) in enumerate(zip(self.dec_self, self.dec_cross)):
            x = self_block(x, causal=causal, cache=cache, key=f"s{i}")
            x = cross_block(x, mem, cache=cache, key=f"c{i}")
        return self.out_head(self.out_norm(x))

    @torch.no_grad()
    def generate(self, mem: torch.Tensor, length: int) -> torch.Tensor:
        """Greedy decoding, exactly `length` tokens. Returns (B, L) base codes."""
        return self.beam(mem, length, beams=1)

    @torch.no_grad()
    def beam(self, mem: torch.Tensor, length: int, beams: int = 4) -> torch.Tensor:
        """Beam search over the whole strand, with a key/value cache.

        Returns (B, L) base codes of the best beam. beams=1 is greedy decoding. The output
        has exactly `length` tokens: there is no end symbol and no length repair, which is
        the structural advantage of generating a strand of known length.
        """
        b, _, d = mem.shape
        mem_b = mem if beams == 1 else (
            mem[:, None].expand(b, beams, mem.shape[1], d).reshape(b * beams, -1, d)
        )
        cache: dict = {}
        inp = torch.full((b * beams, 1), BOS, dtype=torch.long, device=mem.device)
        tokens = torch.zeros((b * beams, length), dtype=torch.long, device=mem.device)
        scores = torch.full((b, beams), float("-inf"), device=mem.device)
        scores[:, 0] = 0.0
        rows = torch.arange(b, device=mem.device)[:, None] * beams
        for t in range(length):
            logits = self._dec_forward(mem_b, inp, offset=t, causal=False, cache=cache)[:, -1]
            if beams == 1:
                nxt = logits.argmax(-1)
                tokens[:, t] = nxt
                inp = nxt[:, None]
                continue
            logp = logits.float().log_softmax(-1)
            cand = (scores.reshape(b * beams, 1) + logp).reshape(b, beams * 4)
            scores, flat = cand.topk(beams, dim=1)
            order = (rows + flat // 4).reshape(-1)
            token = (flat % 4).reshape(-1)
            tokens = tokens[order]
            tokens[:, t] = token
            for k in cache:  # follow the beams that survived
                cache[k] = cache[k][order]
            inp = token[:, None]
        if beams == 1:
            return tokens
        best = (rows[:, 0] + scores.argmax(dim=1))
        return tokens[best]

    def forward(self, batch: dict, target: torch.Tensor, noise: float = 0.0):
        mem, op_logits, ins_logits = self.encode(batch)
        return self.decode_teacher(mem, target, noise), op_logits, ins_logits


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def save_checkpoint(path, model: DraftFormer, **extra) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save({"config": model.cfg.to_dict(), "model": model.state_dict(), **extra}, tmp)
    tmp.replace(path)


def load_checkpoint(path, device: torch.device | str = "cpu") -> tuple[DraftFormer, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model = DraftFormer(DraftFormerConfig(**dict(ckpt["config"]))).to(device)
    model.load_state_dict(ckpt["model"])
    model.eval()
    return model, ckpt


# ---------------------------------------------------------------- inference


def codes_to_strand(codes: np.ndarray) -> str:
    return _LETTERS[np.asarray(codes, dtype=np.int64)].tobytes().decode()


@torch.no_grad()
def decode_clusters(
    model: DraftFormer,
    clusters: Sequence[Cluster],
    strand_length: int,
    batch_size: int = 256,
    device: torch.device | str | None = None,
    mode: str = "beam",
    beams: int = 4,
    thresholds: dict | None = None,
) -> list[Strand | None]:
    """One strand per cluster, None for an empty cluster.

    mode="beam"/"greedy" run the autoregressive decoder; mode="edit" uses the auxiliary
    per-position heads and v1's apply_edits instead, which is the non-autoregressive
    ablation of exactly the same trained encoder.
    """
    device = torch.device(device) if device is not None else next(model.parameters()).device
    out: list[Strand | None] = [None] * len(clusters)
    todo, drafts, packs = [], [], []
    for i, cluster in enumerate(clusters):
        draft, pack = pack_cluster(cluster, strand_length)
        if pack is not None:
            todo.append(i)
            drafts.append(draft)
            packs.append(pack)
    if not todo:
        return out
    amp = device.type == "cuda"
    model.eval()
    for start in range(0, len(packs), batch_size):
        chunk = packs[start : start + batch_size]
        batch = collate(chunk, device)
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            mem, op_logits, ins_logits = model.encode(batch)
        if mode == "edit":
            op_p = op_logits.float().softmax(1).cpu().numpy()
            ins_p = ins_logits.float().softmax(1).cpu().numpy()
            th = thresholds or {"low_max_reads": 3, "low": (0.5, 0.5), "high": (0.5, 0.5)}
            for j, pack in enumerate(chunk):
                sub_t, ins_t = th["low"] if pack.n_reads <= th["low_max_reads"] else th["high"]
                out[todo[start + j]] = apply_edits(
                    drafts[start + j], op_p[j], ins_p[j], strand_length, sub_t, ins_t
                )
            continue
        with torch.autocast(device.type, dtype=torch.bfloat16, enabled=amp):
            codes = model.beam(mem.float(), strand_length, beams if mode == "beam" else 1)
        codes = codes.cpu().numpy()
        for j in range(len(chunk)):
            out[todo[start + j]] = codes_to_strand(codes[j])
    return out


class DraftFormerDecoder:
    """Implements dnacodec.types.Decoder. Autoregressive, so the length is exact by design."""

    name = "draftformer"
    main_process_only = True

    def __init__(
        self,
        checkpoint_path: str | Path,
        device: str | None = None,
        batch_size: int = 256,
        mode: str = "beam",
        beams: int = 4,
    ):
        from .net import pick_device

        self.device = pick_device(device)
        self.model, ckpt = load_checkpoint(checkpoint_path, self.device)
        self.batch_size = batch_size
        self.mode = mode
        self.beams = beams
        self.thresholds = ckpt.get("thresholds")
        self.checkpoint_info = {k: v for k, v in ckpt.items() if k in ("step", "metrics")}

    def decode(self, clusters: Sequence[Cluster], strand_length: int) -> list[Strand | None]:
        return decode_clusters(
            self.model, clusters, strand_length, self.batch_size, self.device,
            self.mode, self.beams, self.thresholds,
        )

    def close(self) -> None:  # symmetry with PolishDecoder, nothing to release
        pass
