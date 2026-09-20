#!/usr/bin/env python3
"""Quantize the learned polisher for the box, and check what quantization costs.

The network is a stem convolution, eight residual blocks of two dilated convolutions each,
128 channels, then two 1x1 heads. Every block is GroupNorm then GELU then a convolution, so
the normalisation statistics are computed per strand at run time and cannot be folded into
the weights. What can be quantized is the convolution weights, and that is where nearly all
of the 0.8M parameters are.

The scheme, which box_polish.cpp implements exactly:
  weights      int8, one scale per output channel, symmetric
  biases       float32, they are tiny and sensitive
  GroupNorm    float32 parameters, statistics computed at run time
  activations  int16, one scale per tensor, calibrated on real clusters
  accumulate   int32

This script is the reference: it runs the same arithmetic in numpy, compares against the
float torch model, and writes the C++ header. If the numbers here are bad there is no point
writing any firmware, which is why this runs first.

    uv run python hardware/esp32_ticker/tools/quantize_polish.py --clusters 200
    uv run python hardware/esp32_ticker/tools/quantize_polish.py --write ../src/box_polish_weights.h
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace as dc_replace
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import torch  # noqa: E402

from dnacodec import demo as demo_mod  # noqa: E402
from dnacodec.encoder import encode  # noqa: E402
from dnacodec.model.polish import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    apply_edits,
    draft_of,
    features_of,
    load_checkpoint,
)
from dnacodec.profiles import load_profile  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402
from dnacodec.simulator import simulate  # noqa: E402
from dnacodec.types import EncoderSettings  # noqa: E402

CKPT = REPO_ROOT / "checkpoints" / "polish" / "polish.pt"
GROUPS = 8


# ---------------------------------------------------------------- data


def clusters_for(n: int, channel: str, reads: float, strand_length: int):
    data = demo_mod.demo_image_png(64)
    settings = EncoderSettings(strand_length=strand_length, redundancy=0.8)
    profile = dc_replace(load_profile(channel), coverage_mean=reads)
    out, seed = [], 0
    while len(out) < n:
        enc = encode(data, settings, None)
        for ref, cluster in zip(enc.strands, simulate(enc.strands, profile, train_seed(seed))):
            live = [r for r in cluster if r]
            if live:
                out.append((live, ref))
            if len(out) >= n:
                break
        seed += 1
    return out


# ---------------------------------------------------------------- the quantized forward


def q_weight(w: np.ndarray):
    """int8 per output channel, symmetric. w is (out, in, k)."""
    scale = np.abs(w.reshape(w.shape[0], -1)).max(axis=1) / 127.0
    scale[scale == 0] = 1e-8
    q = np.clip(np.round(w / scale[:, None, None]), -127, 127).astype(np.int8)
    return q, scale.astype(np.float32)


def gelu(x):
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x**3)))


def group_norm(x, weight, bias, groups=GROUPS, eps=1e-5):
    """x is (C, L). Same as nn.GroupNorm over one sample."""
    c, _ = x.shape
    g = x.reshape(groups, c // groups, -1)
    mean = g.mean(axis=(1, 2), keepdims=True)
    var = g.var(axis=(1, 2), keepdims=True)
    g = (g - mean) / np.sqrt(var + eps)
    return g.reshape(c, -1) * weight[:, None] + bias[:, None]


def conv1d_q(x_q, x_scale, w_q, w_scale, bias, dilation, out_scale):
    """int16 activations times int8 weights into int32, then back to int16.

    x_q is (Cin, L) int16, w_q is (Cout, Cin, K) int8. Returns (Cout, L) int16 plus the
    float result, so the caller can compare.
    """
    cin, length = x_q.shape
    cout, _, k = w_q.shape
    pad = dilation * (k - 1) // 2
    acc = np.zeros((cout, length), dtype=np.int64)
    xi = x_q.astype(np.int64)
    for t in range(k):
        off = t * dilation - pad
        src = np.zeros((cin, length), dtype=np.int64)
        if off < 0:
            src[:, -off:] = xi[:, : length + off]
        elif off > 0:
            src[:, : length - off] = xi[:, off:]
        else:
            src = xi
        acc += w_q[:, :, t].astype(np.int64) @ src
    out_f = acc.astype(np.float64) * (x_scale * w_scale)[:, None] + bias[:, None]
    out_q = np.clip(np.round(out_f / out_scale), -32767, 32767).astype(np.int16)
    return out_q, out_f


class QuantNet:
    """The same arithmetic box_polish.cpp runs, in numpy."""

    def __init__(self, model, scales: dict | None = None):
        sd = {k: v.detach().cpu().numpy().astype(np.float64) for k, v in model.state_dict().items()}
        self.sd = sd
        self.qw = {}
        for name in self._conv_names(sd):
            self.qw[name] = q_weight(sd[name + ".weight"])
        self.scales = scales or {}

    @staticmethod
    def _conv_names(sd):
        names = ["stem"]
        for i in range(8):
            names += [f"blocks.{i}.conv1", f"blocks.{i}.conv2"]
        names += ["op_head", "ins_head"]
        return [n for n in names if n + ".weight" in sd]

    def act_scale(self, key, value):
        """Calibrated scale, or one derived from this tensor when calibrating."""
        if key in self.scales:
            return self.scales[key]
        s = float(np.abs(value).max()) / 32767.0
        return max(s, 1e-8)

    def forward(self, feat, collect=None):
        sd = self.sd
        # the input features are in [0, 1] by construction (vote fractions and a position
        # ramp), so one fixed scale is enough and costs nothing
        in_scale = 1.0 / 32767.0
        x_q = np.clip(np.round(feat / in_scale), -32767, 32767).astype(np.int16)

        s = self.act_scale("stem", None) if "stem" in self.scales else None
        w_q, w_s = self.qw["stem"]
        if s is None:
            _, probe = conv1d_q(x_q, in_scale, w_q, w_s, sd["stem.bias"], 1, 1.0)
            s = self.act_scale("stem", probe)
        if collect is not None:
            _, probe = conv1d_q(x_q, in_scale, w_q, w_s, sd["stem.bias"], 1, 1.0)
            collect.setdefault("stem", []).append(float(np.abs(probe).max()))
        h_q, _ = conv1d_q(x_q, in_scale, w_q, w_s, sd["stem.bias"], 1, s)
        h_scale = s

        dilations = (1, 2, 4, 8, 1, 2, 4, 8)
        for i, d in enumerate(dilations):
            res_q, res_scale = h_q, h_scale
            for step, conv in enumerate(("conv1", "conv2")):
                name = f"blocks.{i}.{conv}"
                nname = f"blocks.{i}.norm{step + 1}"
                # GroupNorm and GELU in float, on the dequantized tensor
                xf = h_q.astype(np.float64) * h_scale
                yf = gelu(group_norm(xf, sd[nname + ".weight"], sd[nname + ".bias"]))
                y_scale = self.act_scale(nname, yf) if nname not in self.scales else self.scales[nname]
                if collect is not None:
                    collect.setdefault(nname, []).append(float(np.abs(yf).max()))
                y_q = np.clip(np.round(yf / y_scale), -32767, 32767).astype(np.int16)

                w_q, w_s = self.qw[name]
                key = name
                if key in self.scales:
                    s = self.scales[key]
                else:
                    _, probe = conv1d_q(y_q, y_scale, w_q, w_s, sd[name + ".bias"], d, 1.0)
                    s = self.act_scale(key, probe)
                if collect is not None:
                    _, probe = conv1d_q(y_q, y_scale, w_q, w_s, sd[name + ".bias"], d, 1.0)
                    collect.setdefault(key, []).append(float(np.abs(probe).max()))
                h_q, _ = conv1d_q(y_q, y_scale, w_q, w_s, sd[name + ".bias"], d, s)
                h_scale = s

            # the residual add, in the residual branch's scale
            summed = res_q.astype(np.int64) * res_scale + h_q.astype(np.int64) * h_scale
            out_scale = self.scales.get(f"blocks.{i}.out")
            if out_scale is None:
                out_scale = max(float(np.abs(summed).max()) / 32767.0, 1e-8)
            if collect is not None:
                collect.setdefault(f"blocks.{i}.out", []).append(float(np.abs(summed).max()))
            h_q = np.clip(np.round(summed / out_scale), -32767, 32767).astype(np.int16)
            h_scale = out_scale

        xf = h_q.astype(np.float64) * h_scale
        yf = gelu(group_norm(xf, sd["norm.weight"], sd["norm.bias"]))
        y_scale = self.scales.get("norm") or max(float(np.abs(yf).max()) / 32767.0, 1e-8)
        if collect is not None:
            collect.setdefault("norm", []).append(float(np.abs(yf).max()))
        y_q = np.clip(np.round(yf / y_scale), -32767, 32767).astype(np.int16)

        w_q, w_s = self.qw["op_head"]
        _, op_f = conv1d_q(y_q, y_scale, w_q, w_s, sd["op_head.bias"], 1, 1.0)
        w_q, w_s = self.qw["ins_head"]
        _, ins_f = conv1d_q(y_q, y_scale, w_q, w_s, sd["ins_head.bias"], 1, 1.0)
        return op_f, ins_f


def softmax(x, axis=0):
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clusters", type=int, default=150)
    ap.add_argument("--calibrate", type=int, default=32, help="clusters used to fix the scales")
    ap.add_argument("--channel", default="nanopore_budget")
    ap.add_argument("--reads", type=float, default=10.0)
    ap.add_argument("--strand-length", type=int, default=110)
    ap.add_argument("--write", default=None, help="write the C++ weight header here")
    ap.add_argument("--skip-eval", action="store_true", help="calibrate and write, no comparison")
    args = ap.parse_args(argv)

    if not CKPT.exists():
        raise SystemExit(f"no checkpoint at {CKPT}")
    model, ckpt = load_checkpoint(CKPT, "cpu")
    model.eval()
    th = ckpt.get("thresholds") or DEFAULT_THRESHOLDS
    print(f"checkpoint {CKPT.name}, thresholds {th}")

    data = clusters_for(args.clusters, args.channel, args.reads, args.strand_length)

    # calibrate the activation scales on a handful of real clusters
    qn = QuantNet(model)
    collect: dict[str, list[float]] = {}
    for cluster, _ref in data[: args.calibrate]:
        draft, votes = draft_of(cluster, args.strand_length)
        if draft is None:
            continue
        qn.forward(features_of(draft, votes).astype(np.float64), collect=collect)
    # a high percentile, so one freak strand does not blunt every other one
    qn.scales = {k: max(float(np.percentile(v, 99.5)) / 32767.0, 1e-8) for k, v in collect.items()}
    print(f"calibrated {len(qn.scales)} activation scales on {args.calibrate} clusters")

    if args.skip_eval:
        write_header(Path(args.write), qn, model, th)
        return 0

    same = q_exact = f_exact = 0
    n = 0
    for cluster, ref in data:
        draft, votes = draft_of(cluster, args.strand_length)
        if draft is None:
            continue
        feat = features_of(draft, votes)
        size = votes[3]

        with torch.no_grad():
            op_l, ins_l = model(torch.from_numpy(feat[None]).float())
        op_f = op_l[0].softmax(0).numpy()
        ins_f = ins_l[0].softmax(0).numpy()

        op_q, ins_q = qn.forward(feat.astype(np.float64))
        op_q = softmax(op_q, axis=0)
        ins_q = softmax(ins_q, axis=0)

        sub_t, ins_t = th["low"] if size <= th["low_max_reads"] else th["high"]
        s_float = apply_edits(draft, op_f, ins_f, args.strand_length, sub_t, ins_t)
        s_quant = apply_edits(draft, op_q, ins_q, args.strand_length, sub_t, ins_t)

        n += 1
        same += int(s_float == s_quant)
        q_exact += int(s_quant == ref)
        f_exact += int(s_float == ref)

    print(f"\nclusters                         {n}")
    print(f"quantized == float32 strand      {same}/{n}  ({100.0 * same / n:.2f}%)")
    print(f"exact strands, quantized         {q_exact}/{n}  ({100.0 * q_exact / n:.2f}%)")
    print(f"exact strands, float32 polisher  {f_exact}/{n}  ({100.0 * f_exact / n:.2f}%)")

    if args.write:
        write_header(Path(args.write), qn, model, th)
    return 0


def write_header(path: Path, qn: QuantNet, model, thresholds: dict) -> None:
    sd = qn.sd
    out = []
    w = out.append
    w("// Generated by hardware/esp32_ticker/tools/quantize_polish.py. Do not edit by hand.")
    w("//")
    w("// The learned polisher, int8 weights with one scale per output channel, float32")
    w("// biases and GroupNorm parameters, and calibrated int16 activation scales. The")
    w("// arithmetic that uses these is box_polish.cpp, and the numpy reference that defines")
    w("// it is the generator itself.")
    w("")
    w("#pragma once")
    w("#include <stdint.h>")
    w("")
    w(f"#define POLISH_CHANNELS {sd['stem.weight'].shape[0]}")
    w(f"#define POLISH_FEATURES {sd['stem.weight'].shape[1]}")
    w(f"#define POLISH_STEM_K {sd['stem.weight'].shape[2]}")
    w("#define POLISH_BLOCKS 8")
    w("#define POLISH_OPS 6")
    w("#define POLISH_INS 5")
    w("#define POLISH_GROUPS 8")
    low = thresholds["low"]
    high = thresholds["high"]
    w(f"#define POLISH_LOW_MAX_READS {thresholds['low_max_reads']}")
    w(f"#define POLISH_LOW_SUB {low[0]}f")
    w(f"#define POLISH_LOW_INDEL {low[1]}f")
    w(f"#define POLISH_HIGH_SUB {high[0]}f")
    w(f"#define POLISH_HIGH_INDEL {high[1]}f")
    w("")

    def emit_i8(name, arr):
        flat = arr.reshape(-1)
        w(f"static const int8_t {name}[{flat.size}] = {{")
        for i in range(0, flat.size, 32):
            w("  " + ",".join(str(int(v)) for v in flat[i : i + 32]) + ",")
        w("};")

    def emit_f32(name, arr):
        flat = np.asarray(arr).reshape(-1).astype(np.float32)
        w(f"static const float {name}[{flat.size}] = {{")
        for i in range(0, flat.size, 8):
            w("  " + ",".join(f"{float(v):.8g}f" for v in flat[i : i + 8]) + ",")
        w("};")

    names = QuantNet._conv_names(sd)
    for name in names:
        cid = name.replace(".", "_")
        q, s = qn.qw[name]
        # stored as [out][tap][in] so the inner product over input channels is contiguous,
        # which is what makes the convolution fast on the chip
        emit_i8(f"kW_{cid}", np.transpose(q, (0, 2, 1)))
        emit_f32(f"kWS_{cid}", s)
        emit_f32(f"kB_{cid}", sd[name + ".bias"])
    for i in range(8):
        for j in (1, 2):
            nname = f"blocks.{i}.norm{j}"
            emit_f32(f"kGW_{nname.replace('.', '_')}", sd[nname + ".weight"])
            emit_f32(f"kGB_{nname.replace('.', '_')}", sd[nname + ".bias"])
    emit_f32("kGW_norm", sd["norm.weight"])
    emit_f32("kGB_norm", sd["norm.bias"])

    keys = sorted(qn.scales)
    w("")
    w("// calibrated activation scales, in the order box_polish.cpp asks for them")
    for k in keys:
        w(f"static const float kAS_{k.replace('.', '_')} = {qn.scales[k]:.8g}f;")
    path = Path(path)
    if not path.is_absolute():
        path = (HERE / path).resolve()
    path.write_text("\n".join(out) + "\n")
    print(f"\nwrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    raise SystemExit(main())
