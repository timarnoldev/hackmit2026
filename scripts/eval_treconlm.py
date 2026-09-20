"""TReconLM's released model on OUR held-out split, under OUR protocol.

    uv run python scripts/eval_treconlm.py \
        --treconlm-dir ~/TReconLM \
        --model ~/treconlm_models/finetuned_microsoft_dna_len110.pt \
        --polish-checkpoint checkpoints/polish/polish.pt

Why this script exists. docs/COMPARISON.md used to put our number next to TReconLM's
published Table 7. Those are two different experiments: they score 5,109 subclusters of
exactly N reads drawn from their own 10% test split, we score 2,000 whole clusters at
at-most-k reads from our own 20% split. This script removes that gap by running their
released checkpoint on our clusters, with our subsampling seeds and our metric, next to our
own decoders on exactly the same subsampled clusters.

What it does NOT remove. TReconLM's Microsoft fine-tune was trained on a random 80% of the
10,000 clusters (sklearn train_test_split, random_state=42, see their
data/microsoft_data/microsoft_data.ipynb). Our held-out split is every 5th cluster. The two
overlap: 1,585 of our 2,000 held-out clusters sat in their training set. So this script
reports three cluster sets:

  all         all 2,000 of our held-out clusters. Clean for us, 79% contaminated for their
              fine-tuned model. Read it as an upper bound on their fine-tuned score.
  their_test  our held-out clusters that are also in their 10% test split (203 clusters).
              No model here trained or tuned on these. This is the apples-to-apples row.
  their_eval  our held-out clusters in their validation OR test split (415 clusters). Their
              model took no gradient step on these, but their early stopping saw the
              validation half.

Their pretrained-only checkpoint (model_seq_len_110.pt) never saw this dataset at all, so
for that model the "all" row is clean and carries the full 2,000 clusters.

Protocol, identical to scripts/eval_real.py: per cluster, subsample without replacement with
np.random.default_rng(seed) and the seeds of heldout_seeds(), clusters with <= k reads keep
all of them, empty clusters stay empty and count as wrong, a strand counts only if all 110
bases match. Budgets stop at 10 because TReconLM's context was trained for 2 to 10 reads.

Their code is NOT vendored here. Clone it yourself and pass --treconlm-dir. See
docs/COMPARISON.md for the commit and licence we used.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dnacodec import realdata  # noqa: E402
from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.evaluate import evaluate  # noqa: E402
from dnacodec.results import RESULTS_DIR  # noqa: E402
from dnacodec.seeds import heldout_seeds  # noqa: E402

BUDGETS = [2, 4, 6, 10, 16]  # the seed schedule of eval_real.py; we run the first four
TRECONLM_BUDGETS = [2, 4, 6, 10]
STRAND_LENGTH = 110
HELDOUT_EVERY = realdata.HELDOUT_EVERY
N_CLUSTERS_TOTAL = 10_000


def subsample_clusters(clusters: list[list[str]], k: int, seed: int) -> list[list[str]]:
    """Identical to scripts/eval_real.py."""
    rng = np.random.default_rng(seed)
    out = []
    for reads in clusters:
        if len(reads) <= k:
            out.append(list(reads))
        else:
            idx = np.sort(rng.choice(len(reads), size=k, replace=False))
            out.append([reads[i] for i in idx])
    return out


def treconlm_split_indices(n_total: int) -> dict[str, set[int]]:
    """Their 80/10/10 split of the Microsoft clusters, reproduced exactly.

    From TReconLM data/microsoft_data/microsoft_data.ipynb, section 2:

        indices = list(range(len(clusters)))
        train_indices, temp_indices = train_test_split(indices, test_size=0.2, random_state=42)
        val_indices, test_indices = train_test_split(temp_indices, test_size=0.5, random_state=42)

    Indices are positions in Centers.txt / Clusters.txt, the same order we read them in.
    """
    from sklearn.model_selection import train_test_split

    indices = list(range(n_total))
    train, temp = train_test_split(indices, test_size=0.2, random_state=42)
    val, test = train_test_split(temp, test_size=0.5, random_state=42)
    return {"train": set(train), "val": set(val), "test": set(test)}


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval. The small subsets below need one."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---- TReconLM ---------------------------------------------------------------------------


class TReconLMDecoder:
    """Their released checkpoint, driven the way tutorial/custom_data.ipynb drives it.

    The model only ever sees the text before the ':' (GPT_Inference.inference does
    ex.split(':')[0]), so we pass an empty ground truth and no reference can leak in.
    """

    def __init__(self, treconlm_dir: Path, model_path: Path, extra_paths: list[Path],
                 device_name: str | None = None, batch_size: int = 200) -> None:
        import torch

        for p in [treconlm_dir, *extra_paths]:
            sys.path.insert(0, str(p))
        from src.eval_pkg.GPT_Inference import GPT_Inference  # noqa: E402
        from src.gpt_pkg.model import GPT, GPTConfig  # noqa: E402

        self._inference_cls = GPT_Inference
        self.batch_size = batch_size
        self.model_path = model_path

        with (treconlm_dir / "src" / "data_pkg" / "meta_nuc.pkl").open("rb") as f:
            meta = pickle.load(f)
        self.stoi, self.itos = meta["stoi"], meta["itos"]

        if device_name is None:
            device_name = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device_name)

        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.model_args = dict(ckpt["model_args"])
        config_args = {k: v for k, v in self.model_args.items() if k != "model_type"}
        state = {k.replace("_orig_mod.", ""): v for k, v in ckpt["model"].items()}
        model = GPT(GPTConfig(**config_args))
        model.load_state_dict(state, strict=True)
        self.model = (
            model.half().to(self.device).eval()
            if self.device.type == "cuda"
            else model.to(self.device).eval()
        )
        self.n_params = sum(p.numel() for p in self.model.parameters())
        self.ckpt_meta = {
            "iter_num": ckpt.get("iter_num"),
            "best_val_loss": float(ckpt["best_val_loss"]) if "best_val_loss" in ckpt else None,
            "model_args": self.model_args,
        }
        del ckpt, state

        if self.device.type == "cuda":
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
            self.ctx = torch.amp.autocast("cuda", dtype=dtype)
        else:
            self.ctx = torch.inference_mode()

        self.params = {
            "model": self.model,
            "ctx": self.ctx,
            "device": self.device,
            "stoi": self.stoi,
            "itos": self.itos,
            "encode": lambda s: [self.stoi[c] for c in s],
            "decode": lambda t: "".join(self.itos[i] for i in t),
            "temperature": 1.0,
            "greedy": True,
            "ground_truth_length": STRAND_LENGTH,
            "block_size": self.model_args["block_size"],
            "target_type": "CPRED",
            "constrained_generation": True,
        }

    name = "treconlm"

    def decode(self, clusters: list[list[str]], strand_length: int) -> list[str | None]:
        import torch

        decoded: list[str | None] = [None] * len(clusters)
        # Empty clusters never reach the model; evaluate() counts them wrong anyway.
        jobs = [(i, c) for i, c in enumerate(clusters) if c]
        jobs.sort(key=lambda j: sum(len(r) for r in j[1]) + len(j[1]))
        with torch.inference_mode(), self.ctx:
            for start in range(0, len(jobs), self.batch_size):
                batch = jobs[start : start + self.batch_size]
                inputs = ["|".join(reads) + ":" for _, reads in batch]
                sizes = [len(reads) for _, reads in batch]
                out = self._inference_cls(self.params).inference(inputs, alignment_size=sizes)
                for (i, _), pred in zip(batch, out["candidate_sequences"]):
                    decoded[i] = "".join(c for c in pred if c in "ACTG")[:strand_length]
        return decoded


# ---- our decoders -----------------------------------------------------------------------


def our_decoders(checkpoint: Path | None, device_name: str | None, batch_size: int,
                 variants: list[str]):
    """(name, callable(clusters) -> decoded) for the rows we put next to theirs."""
    out: list[tuple[str, object]] = [
        ("ours_majority_vote", lambda cs: MajorityVoteDecoder().decode(cs, STRAND_LENGTH))
    ]
    if checkpoint is None:
        return out
    from dnacodec.model.net import pick_device
    from dnacodec.model.polish import DEFAULT_THRESHOLDS, load_checkpoint, polish_clusters_multi

    from scripts.polish_experiments import GAIN_THRESHOLDS, VARIANTS

    device = pick_device(device_name)
    model, ckpt = load_checkpoint(checkpoint, device)
    thresholds = {**DEFAULT_THRESHOLDS, **(ckpt.get("thresholds") or {})}
    for variant in variants:
        kwargs = dict(VARIANTS[variant])
        th = GAIN_THRESHOLDS if kwargs.get("mode") == "gain" else thresholds

        def run(cs, kwargs=kwargs, th=th):
            return polish_clusters_multi(
                model, cs, STRAND_LENGTH, batch_size, device, th, **kwargs
            )

        out.append((f"ours_{variant}", run))
    return out


# ---- scoring ----------------------------------------------------------------------------


def score(references, decoded, clusters, keep: list[int]) -> dict:
    refs = [references[i] for i in keep]
    dec = [decoded[i] for i in keep]
    cls = [clusters[i] for i in keep]
    m = evaluate(refs, dec, cls)
    successes = round(m.strand_accuracy * len(keep))
    lo, hi = wilson_interval(successes, len(keep))
    return {
        "n_clusters": len(keep),
        "strand_accuracy": float(m.strand_accuracy),
        "ci95_low": lo,
        "ci95_high": hi,
        "mean_edit_distance": float(m.mean_edit_distance),
        "reads_per_strand": float(m.reads_per_strand),
        "dropout_rate": float(m.dropout_rate),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--treconlm-dir", type=Path, required=True,
                   help="clone of github.com/MLI-lab/TReconLM, OUTSIDE this repo")
    p.add_argument("--model", type=Path, required=True, help="their .pt checkpoint")
    p.add_argument("--model-label", default=None, help="name for this model in the output")
    p.add_argument("--extra-path", type=Path, action="append", default=[],
                   help="extra sys.path entries for their imports (stubs, deps)")
    p.add_argument("--polish-checkpoint", type=Path, default=None)
    p.add_argument("--variants", default="polish,polish_gain_d3r2")
    p.add_argument("--budgets", default=",".join(str(b) for b in TRECONLM_BUDGETS))
    p.add_argument("--batch-size", type=int, default=200)
    p.add_argument("--polish-batch-size", type=int, default=256)
    p.add_argument("--device", default=None)
    p.add_argument("--limit", type=int, default=None, help="smoke test on the first N clusters")
    p.add_argument("--skip-ours", action="store_true")
    p.add_argument("--out", type=Path, default=RESULTS_DIR / "treconlm_on_heldout.json")
    args = p.parse_args()

    budgets = [int(b) for b in args.budgets.split(",")]
    if any(b not in BUDGETS for b in budgets):
        raise SystemExit(f"budgets must come from the eval_real.py schedule {BUDGETS}")

    data = realdata.load_microsoft("heldout")
    all_data = realdata.load_microsoft("all")
    if len(all_data) != N_CLUSTERS_TOTAL:
        raise SystemExit(f"expected {N_CLUSTERS_TOTAL} clusters, found {len(all_data)}")
    references = [c.reference for c in data]
    full = [c.reads for c in data]
    if {len(r) for r in references} != {STRAND_LENGTH}:
        raise SystemExit("unexpected reference lengths")

    # held-out position j is global cluster index j * HELDOUT_EVERY
    global_index = [j * HELDOUT_EVERY for j in range(len(data))]
    if [all_data[g].reference for g in global_index] != references:
        raise SystemExit("held-out positions do not line up with the full cluster list")

    splits = treconlm_split_indices(len(all_data))
    subsets = {
        "all": list(range(len(data))),
        "their_test": [j for j, g in enumerate(global_index) if g in splits["test"]],
        "their_eval": [j for j, g in enumerate(global_index)
                       if g in splits["test"] or g in splits["val"]],
    }
    leaked = [j for j, g in enumerate(global_index) if g in splits["train"]]
    if args.limit:
        keep = set(range(args.limit))
        references, full = references[: args.limit], full[: args.limit]
        subsets = {k: [j for j in v if j in keep] for k, v in subsets.items()}
        leaked = [j for j in leaked if j in keep]

    print(f"held-out clusters: {len(references)}")
    print(f"  in TReconLM's Microsoft fine-tune TRAIN split: {len(leaked)} "
          f"({100 * len(leaked) / len(references):.1f}%)")
    for name, idx in subsets.items():
        print(f"  subset {name}: {len(idx)} clusters")

    decoders: list[tuple[str, object]] = []
    label = args.model_label or f"treconlm_{args.model.stem}"
    tre = TReconLMDecoder(args.treconlm_dir, args.model, args.extra_path,
                          args.device, args.batch_size)
    print(f"{label}: {tre.n_params / 1e6:.1f}M parameters, device {tre.device}, "
          f"ckpt iter {tre.ckpt_meta['iter_num']}")
    decoders.append((label, lambda cs: tre.decode(cs, STRAND_LENGTH)))
    if not args.skip_ours:
        decoders += [
            (n, f) for n, f in our_decoders(
                args.polish_checkpoint, args.device, args.polish_batch_size,
                [v for v in args.variants.split(",") if v]
            )
        ]

    seeds = dict(zip(BUDGETS, heldout_seeds(len(BUDGETS))))
    rows: list[dict] = []
    for k in budgets:
        clusters = subsample_clusters(full, k, seeds[k])
        sizes = np.array([len(c) for c in clusters])
        print(f"\nbudget {k}: mean {sizes.mean():.2f} reads, "
              f"{int((sizes == 0).sum())} empty, {int((sizes == 1).sum())} single-read")
        for name, fn in decoders:
            start = time.perf_counter()
            decoded = list(fn(clusters))
            seconds = time.perf_counter() - start
            for subset, idx in subsets.items():
                if not idx:
                    continue
                row = {"decoder": name, "max_reads": k, "subset": subset,
                       "subsample_seed": seeds[k], "decode_seconds": seconds,
                       **score(references, decoded, clusters, idx)}
                rows.append(row)
            acc = next(r for r in rows[::-1] if r["decoder"] == name and r["subset"] == "all")
            print(f"  {name:>34} acc(all) {acc['strand_accuracy']:.4f}  {seconds:6.1f}s")

    print("\nexact strand accuracy, our protocol, our held-out clusters")
    for subset, idx in subsets.items():
        if not idx:
            continue
        print(f"\n  subset {subset} ({len(idx)} clusters)")
        header = " ".join(f"{b:>15}" for b in budgets)
        print(f"  {'decoder':>34} {header}")
        for name, _ in decoders:
            cells = []
            for b in budgets:
                r = next((r for r in rows if r["decoder"] == name and r["max_reads"] == b
                          and r["subset"] == subset), None)
                cells.append(
                    f"{100 * r['strand_accuracy']:6.1f} "
                    f"[{100 * r['ci95_low']:4.1f},{100 * r['ci95_high']:4.1f}]" if r else " " * 15
                )
            print(f"  {name:>34} " + " ".join(cells))

    out = {
        "dataset": "microsoft_nanopore",
        "split": "heldout",
        "protocol": "scripts/eval_real.py: per cluster, without replacement, "
                    "np.random.default_rng(seed) with seeds from heldout_seeds(); clusters "
                    "with <= k reads keep all reads; empty clusters count as wrong; a strand "
                    "counts only if all 110 bases match",
        "treconlm_model": str(args.model),
        "treconlm_checkpoint": tre.ckpt_meta,
        "treconlm_params": tre.n_params,
        "polish_checkpoint": str(args.polish_checkpoint) if args.polish_checkpoint else None,
        "n_heldout_clusters": len(references),
        "n_heldout_in_treconlm_train": len(leaked),
        "subset_sizes": {k: len(v) for k, v in subsets.items()},
        "subset_definitions": {
            "all": "every cluster of our held-out split (every 5th cluster of the dataset)",
            "their_test": "our held-out clusters that are also in TReconLM's 10% test split",
            "their_eval": "our held-out clusters in TReconLM's validation or test split",
        },
        "is_mock": False,
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
