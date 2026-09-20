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

    reads ──▶ per-read transformer **in read coordinates**, positional embedding = the draft
    │         position each read base aligns to, plus a flag for "this base is an insertion".
    │         A read base therefore carries its own local context, including bases the
    │         alignment mis-assigned, which the aligned-column view destroys.
    ▼
    gather at the aligned index ──▶ (draft position, read) grid of read states
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

BASE_PAD = 4  # read base token for padding
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


@dataclass
class Pack:
    """One cluster, ready for the model. All arrays are fixed size so they batch directly."""

    rbase: np.ndarray  # (R, Lr) int8, read bases, BASE_PAD outside the read
    rcoord: np.ndarray  # (R, Lr) int16, draft position of each read base
    rins: np.ndarray  # (R, Lr) int8, 1 = inserted base
    gather: np.ndarray  # (R, L) int16, read index per draft position, NO_ALIGN if missing
    feats: np.ndarray  # (N_FEATURES, L) float16, the v1 vote features of the same draft
    draft: np.ndarray  # (L,) int8
    n_reads: int


def read_width(strand_length: int) -> int:
    """Padded read length. Nanopore reads run a little longer or shorter than the strand."""
    return strand_length + 32


def pack_cluster(cluster: Cluster, strand_length: int, max_reads: int = MAX_READS):
    """(draft string, Pack) for one cluster, or (None, None) when it has no read."""
    reads = subsample([r for r in cluster if r], max_reads)
    if not reads:
        return None, None
    arrays = [_to_array(r) for r in reads]
    draft, votes = refine_draft(_pick_draft(reads, strand_length), reads, arrays, strand_length)
    width = read_width(strand_length)
    n = len(reads)
    rbase = np.full((max_reads, width), BASE_PAD, dtype=np.int8)
    rcoord = np.zeros((max_reads, width), dtype=np.int16)
    rins = np.zeros((max_reads, width), dtype=np.int8)
    gather = np.full((max_reads, strand_length), NO_ALIGN, dtype=np.int16)
    for r, (read, arr) in enumerate(zip(reads, arrays)):
        g, coord, ins = align_read(draft, read, arr, strand_length)
        keep = min(len(read), width)
        rbase[r, :keep] = arr[:keep]
        rcoord[r, :keep] = coord[:keep]
        rins[r, :keep] = ins[:keep]
        gather[r] = np.where(g < keep, g, NO_ALIGN)  # a truncated tail counts as unaligned
    pack = Pack(
        rbase=rbase,
        rcoord=rcoord,
        rins=rins,
        gather=gather,
        feats=features_of(draft, votes).astype(np.float16),
        draft=_to_array(draft).astype(np.int8),
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
        "rbase": t(np.stack([p.rbase for p in packs]), torch.long),
        "rcoord": t(np.stack([p.rcoord for p in packs]), torch.long),
        "rins": t(np.stack([p.rins for p in packs]), torch.long),
        "gather": t(np.stack([p.gather for p in packs]), torch.long),
        "feats": t(np.stack([p.feats for p in packs]), torch.float32),
        "draft": t(np.stack([p.draft for p in packs]), torch.long),
        "n_reads": t(np.array([p.n_reads for p in packs]), torch.long),
    }


# ---------------------------------------------------------------- blocks


class _SelfBlock(nn.Module):
    """Pre-norm self-attention plus a feed-forward, the standard pair."""

    def __init__(self, d: int, heads: int, ffn: int, dropout: float):
        super().__init__()
        self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(d, heads, dropout=dropout, batch_first=True)
        self.ff = nn.Sequential(nn.Linear(d, ffn), nn.GELU(), nn.Linear(ffn, d))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, key_padding_mask=None, attn_mask=None, is_causal=False):
        h = self.n1(x)
        a, _ = self.attn(
            h, h, h,
            key_padding_mask=key_padding_mask,
            attn_mask=attn_mask,
            is_causal=is_causal,
            need_weights=False,
        )
        x = x + self.drop(a)
        return x + self.drop(self.ff(self.n2(x)))


class _CrossBlock(nn.Module):
    """Pre-norm cross-attention into a memory, plus a feed-forward."""

    def __init__(self, d: int, heads: int, ffn: int, dropout: float, d_mem: int | None = None):
        super().__init__()
        d_mem = d_mem or d
        self.n1, self.nm, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d_mem), nn.LayerNorm(d)
        self.attn = nn.MultiheadAttention(
            d, heads, dropout=dropout, batch_first=True, kdim=d_mem, vdim=d_mem
        )
        self.ff = nn.Sequential(nn.Linear(d, ffn), nn.GELU(), nn.Linear(ffn, d))
        self.drop = nn.Dropout(dropout)

    def forward(self, x, mem, mem_key_padding_mask=None):
        m = self.nm(mem)
        a, _ = self.attn(
            self.n1(x), m, m, key_padding_mask=mem_key_padding_mask, need_weights=False
        )
        x = x + self.drop(a)
        return x + self.drop(self.ff(self.n2(x)))


# ---------------------------------------------------------------- the model


@dataclass
class DraftFormerConfig:
    d_read: int = 96
    d: int = 192
    heads: int = 6
    read_heads: int = 4
    read_layers: int = 2
    cross_layers: int = 2
    trunk_layers: int = 4
    dec_layers: int = 4
    ffn_mult: int = 4
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
        # --- per-read encoder, in read coordinates
        self.base_emb = nn.Embedding(5, dr)
        self.coord_emb = nn.Embedding(cfg.max_len + 1, dr)
        self.ins_emb = nn.Embedding(2, dr)
        self.read_blocks = nn.ModuleList(
            [_SelfBlock(dr, cfg.read_heads, dr * 2, cfg.dropout) for _ in range(cfg.read_layers)]
        )
        self.read_norm = nn.LayerNorm(dr)
        self.no_align = nn.Parameter(torch.zeros(dr))
        self.read_proj = nn.Linear(dr, d)
        self.read_id = nn.Embedding(cfg.max_reads, d)  # a read slot identity, so reads differ
        # --- draft-position query
        self.feat_proj = nn.Linear(N_FEATURES, d)
        self.draft_emb = nn.Embedding(4, d)
        self.pos_emb = nn.Embedding(cfg.max_len, d)
        self.cov_emb = nn.Embedding(cfg.max_reads + 1, d)
        self.cross_blocks = nn.ModuleList(
            [_CrossBlock(d, cfg.heads, ffn, cfg.dropout) for _ in range(cfg.cross_layers)]
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
        rbase, rcoord, rins = batch["rbase"], batch["rcoord"], batch["rins"]
        gather, feats, draft = batch["gather"], batch["feats"], batch["draft"]
        n_reads = batch["n_reads"]
        b, r, width = rbase.shape
        length = draft.shape[1]
        cfg = self.cfg

        pad = rbase == BASE_PAD
        h = (
            self.base_emb(rbase)
            + self.coord_emb(rcoord.clamp(0, cfg.max_len))
            + self.ins_emb(rins)
        )
        h = h.reshape(b * r, width, cfg.d_read)
        mask = pad.reshape(b * r, width)
        # a fully padded read slot would make softmax nan: give it one visible token
        mask = mask & ~(mask.all(dim=1, keepdim=True))
        for block in self.read_blocks:
            h = block(h, key_padding_mask=mask)
        h = self.read_norm(h).reshape(b, r, width, cfg.d_read)

        idx = gather.clamp(min=0).unsqueeze(-1).expand(-1, -1, -1, cfg.d_read)
        g = torch.gather(h, 2, idx)  # (B, R, L, d_read)
        g = torch.where((gather >= 0).unsqueeze(-1), g, self.no_align.to(g.dtype))
        g = self.read_proj(g) + self.read_id.weight[:r][None, :, None, :]
        g = g.permute(0, 2, 1, 3).reshape(b * length, r, cfg.d)  # (B*L, R, d)

        present = torch.arange(r, device=rbase.device)[None, :] >= n_reads[:, None]  # True = pad
        kpm = present[:, None, :].expand(b, length, r).reshape(b * length, r)

        pos = torch.arange(length, device=rbase.device)
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
        causal = torch.triu(
            torch.full((length, length), float("-inf"), device=mem.device), diagonal=1
        )
        return self._dec_forward(mem, inp, causal)

    def _dec_forward(self, mem, inp, causal):
        length = inp.shape[1]
        pos = torch.arange(length, device=inp.device).clamp(max=self.cfg.max_len - 1)
        x = self.tok_emb(inp) + self.dpos_emb(pos)[None] + self.mem_gate(mem[:, :length])
        for self_block, cross_block in zip(self.dec_self, self.dec_cross):
            x = self_block(x, attn_mask=causal)
            x = cross_block(x, mem)
        return self.out_head(self.out_norm(x))

    @torch.no_grad()
    def generate(self, mem: torch.Tensor, length: int) -> torch.Tensor:
        """Greedy decoding, exactly `length` tokens. Returns (B, L) base codes.

        No KV cache: the prefix is re-run each step, which is O(L^2) attention but on
        L <= 140 tokens and a 192-wide model that is a few milliseconds per batch.
        """
        b = mem.shape[0]
        out = torch.zeros((b, length), dtype=torch.long, device=mem.device)
        inp = torch.full((b, 1), BOS, dtype=torch.long, device=mem.device)
        for t in range(length):
            causal = torch.triu(
                torch.full((t + 1, t + 1), float("-inf"), device=mem.device), diagonal=1
            )
            logits = self._dec_forward(mem, inp, causal)[:, -1]
            nxt = logits.argmax(-1)
            out[:, t] = nxt
            inp = torch.cat([inp, nxt[:, None]], dim=1)
        return out

    @torch.no_grad()
    def beam(self, mem: torch.Tensor, length: int, beams: int = 4) -> torch.Tensor:
        """Beam search over the whole strand. Returns (B, L) base codes of the best beam."""
        if beams <= 1:
            return self.generate(mem, length)
        b, _, d = mem.shape
        mem_b = mem[:, None].expand(b, beams, mem.shape[1], d).reshape(b * beams, -1, d)
        inp = torch.full((b * beams, 1), BOS, dtype=torch.long, device=mem.device)
        scores = torch.full((b, beams), float("-inf"), device=mem.device)
        scores[:, 0] = 0.0
        for t in range(length):
            causal = torch.triu(
                torch.full((t + 1, t + 1), float("-inf"), device=mem.device), diagonal=1
            )
            logp = self._dec_forward(mem_b, inp, causal)[:, -1].float().log_softmax(-1)
            cand = scores.reshape(b * beams, 1) + logp  # (B*beams, 4)
            cand = cand.reshape(b, beams * 4)
            scores, flat = cand.topk(beams, dim=1)
            which = flat // 4
            token = flat % 4
            base = (torch.arange(b, device=mem.device) * beams)[:, None]
            inp = inp[(base + which).reshape(-1)]
            inp = torch.cat([inp, token.reshape(-1, 1)], dim=1)
        best = scores.argmax(dim=1)
        rows = torch.arange(b, device=mem.device) * beams + best
        return inp[rows, 1:]

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
