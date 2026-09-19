"""The consensus transformer.

reads (B, R, L) -> per-read transformer encoder (token + position + read-index embeddings)
-> strand_length learned output queries (+ shared position embedding + strand length
embedding) run through transformer decoder layers: self-attention over output positions and
cross-attention to every read token of the cluster (Perceiver style)
-> logits (B, S, 4) over ACGT.

Positions are shared between read tokens and output queries, so query i starts out looking
near base i of each read; the layers learn to follow indel shifts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn

from .data import MAX_READS, PAD, read_len_for


@dataclass
class ModelConfig:
    d_model: int = 256
    n_heads: int = 8
    enc_layers: int = 4
    dec_layers: int = 6
    ff_mult: int = 4
    dropout: float = 0.1
    max_reads: int = MAX_READS
    max_strand_length: int = 160

    @property
    def max_read_len(self) -> int:
        return read_len_for(self.max_strand_length)

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS: dict[str, ModelConfig] = {
    "tiny": ModelConfig(d_model=64, n_heads=4, enc_layers=1, dec_layers=2, dropout=0.0),
    "small": ModelConfig(d_model=192, n_heads=6, enc_layers=3, dec_layers=4),
    "base": ModelConfig(d_model=256, n_heads=8, enc_layers=4, dec_layers=6),
    "large": ModelConfig(d_model=384, n_heads=8, enc_layers=4, dec_layers=6),
}


class ConsensusNet(nn.Module):
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg
        d = cfg.d_model
        self.tok_emb = nn.Embedding(PAD + 1, d, padding_idx=PAD)
        self.pos_emb = nn.Embedding(cfg.max_read_len, d)
        self.read_emb = nn.Embedding(cfg.max_reads, d)
        self.query_emb = nn.Embedding(cfg.max_strand_length, d)
        self.len_emb = nn.Embedding(cfg.max_strand_length + 1, d)
        self.null_mem = nn.Parameter(torch.zeros(1, 1, d))  # always attendable, avoids empty memory
        enc_layer = nn.TransformerEncoderLayer(
            d, cfg.n_heads, cfg.ff_mult * d, cfg.dropout, activation="gelu",
            batch_first=True, norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(enc_layer, cfg.enc_layers, enable_nested_tensor=False)
        dec_layer = nn.TransformerDecoderLayer(
            d, cfg.n_heads, cfg.ff_mult * d, cfg.dropout, activation="gelu",
            batch_first=True, norm_first=True,
        )
        self.decoder = nn.TransformerDecoder(dec_layer, cfg.dec_layers)
        self.mem_norm = nn.LayerNorm(d)
        self.out_norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, 4)
        nn.init.normal_(self.null_mem, std=0.02)

    def forward(self, reads: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        """reads (B, R, L) long, lengths (B,) long -> logits (B, max(lengths), 4)."""
        B, R, L = reads.shape
        if R > self.cfg.max_reads or L > self.cfg.max_read_len:
            raise ValueError(f"input {R}x{L} exceeds model limits {self.cfg.max_reads}x{self.cfg.max_read_len}")
        S = int(lengths.max())
        if S > self.cfg.max_strand_length:
            raise ValueError(f"strand length {S} > max_strand_length {self.cfg.max_strand_length}")
        dev = reads.device
        d = self.cfg.d_model

        pad = reads == PAD  # (B, R, L)
        x = self.tok_emb(reads) + self.pos_emb(torch.arange(L, device=dev)) \
            + self.read_emb(torch.arange(R, device=dev))[:, None, :]
        x = x.reshape(B * R, L, d)
        pad_flat = pad.reshape(B * R, L)
        nonempty = ~pad_flat.all(dim=1)
        h = x.new_zeros(B * R, L, d)
        if nonempty.any():
            h[nonempty] = self.encoder(x[nonempty], src_key_padding_mask=pad_flat[nonempty])
        mem = self.mem_norm(h).reshape(B, R * L, d)
        mem = torch.cat([self.null_mem.expand(B, 1, d).to(mem.dtype), mem], dim=1)
        mem_mask = torch.cat([torch.zeros(B, 1, dtype=torch.bool, device=dev), pad.reshape(B, R * L)], 1)

        pos = torch.arange(S, device=dev)
        q = (self.query_emb(pos) + self.pos_emb(pos))[None] + self.len_emb(lengths)[:, None, :]
        q_pad = pos[None, :] >= lengths[:, None]
        y = self.decoder(q, mem, tgt_key_padding_mask=q_pad, memory_key_padding_mask=mem_mask)
        return self.head(self.out_norm(y))


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())


def pick_device(preferred: str | None = None) -> torch.device:
    if preferred:
        return torch.device(preferred)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def save_checkpoint(path, model: ConsensusNet, **extra) -> None:
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    torch.save({"config": model.cfg.to_dict(), "model": model.state_dict(), **extra}, tmp)
    tmp.replace(path)  # atomic, a crash mid-save never corrupts the previous checkpoint


def load_checkpoint(path, device: torch.device | str = "cpu") -> tuple[ConsensusNet, dict]:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    model = ConsensusNet(ModelConfig(**ckpt["config"])).to(device)
    model.load_state_dict(ckpt["model"])
    return model, ckpt
