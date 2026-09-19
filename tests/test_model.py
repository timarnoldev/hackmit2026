"""Tests for the transformer decoder (dnacodec.model)."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from dnacodec import realdata  # noqa: E402
from dnacodec.model.data import (  # noqa: E402
    MAX_READS,
    PAD,
    ClusterPool,
    MixedSource,
    SimSource,
    batch_loader,
    encode_batch,
    make_batch,
    random_strands,
    select_reads,
    split_train_val,
)
from dnacodec.model.decoder import TransformerDecoder, predict  # noqa: E402
from dnacodec.model.finetune import finetune  # noqa: E402
from dnacodec.model.net import PRESETS, ConsensusNet, save_checkpoint  # noqa: E402
from dnacodec.model.train import make_optimizer, run_steps  # noqa: E402
from dnacodec.seeds import is_heldout  # noqa: E402

TINY = PRESETS["tiny"]


def noisy_copy(ref: str, rng: np.random.Generator, p: float) -> str:
    out = []
    for base in ref:
        u = rng.random()
        if u < p:  # substitution
            out.append("ACGT"[(("ACGT".index(base)) + rng.integers(1, 4)) % 4])
        elif u < 2 * p:  # deletion
            continue
        elif u < 3 * p:  # insertion
            out.append(base)
            out.append("ACGT"[rng.integers(4)])
        else:
            out.append(base)
    return "".join(out)


class MockSimulate:
    """Stands in for dnacodec.simulator.simulate until Agent A's lands. Records seeds."""

    def __init__(self):
        self.seeds: list[int] = []

    def __call__(self, strands, profile, seed):
        self.seeds.append(seed)
        rng = np.random.default_rng(seed)
        p = (profile.sub_rate + profile.ins_rate + profile.del_rate) / 3
        clusters = []
        for s in strands:
            n = 0 if rng.random() < 0.05 else int(rng.integers(1, 20))
            clusters.append([noisy_copy(s, rng, p) for _ in range(n)])
        return clusters


def test_select_reads_caps_and_is_deterministic():
    cluster = ["A" * (100 + i) for i in range(30)] + [""]
    picked = select_reads(cluster, 110)
    assert len(picked) == MAX_READS
    assert picked == select_reads(cluster, 110)
    assert "A" * 110 in picked and "" not in picked
    rng = np.random.default_rng(0)
    assert len(select_reads(cluster, 110, rng=rng, k=3)) == 3
    assert len(select_reads(["ACGT"], 110, rng=rng, k=5)) == 1


def test_encode_batch_mixed_lengths():
    batch = encode_batch([["ACGT", "AC"], ["GGG"]], [110, 140], ["A" * 110, "C" * 140])
    assert batch.reads.shape == (2, 2, 161)
    assert batch.reads[0, 0, :4].tolist() == [0, 1, 2, 3]
    assert batch.reads[1, 1].eq(PAD).all()
    assert batch.targets.shape == (2, 140)
    assert (batch.targets[0, 110:] == -100).all() and (batch.targets[1] == 1).all()


def test_forward_shapes_and_empty_reads():
    model = ConsensusNet(TINY).eval()
    batch = encode_batch([["ACGT" * 30], [], ["ACG" * 40, "T" * 130]], [110, 120, 140])
    logits = model(batch.reads, batch.lengths)
    assert logits.shape == (3, 140, 4)
    assert torch.isfinite(logits).all()


def test_sim_source_uses_train_seeds_and_mixes():
    sim = MockSimulate()
    source = SimSource(100, 140, simulate_fn=sim)
    rng = np.random.default_rng(1)
    refs, clusters = source.sample(32, rng)
    assert len(refs) == 32 and all(any(c) for c in clusters)
    assert 100 <= len(refs[0]) <= 140 and len({len(r) for r in refs}) == 1
    assert sim.seeds and not any(is_heldout(s) for s in sim.seeds)
    pool = ClusterPool(refs, clusters)
    mixed = MixedSource([source, pool], [0.5, 0.5])
    batch = make_batch(*mixed.sample(8, rng), rng)
    assert batch.reads.shape[1] <= MAX_READS


def test_sim_source_with_real_simulator():
    source = SimSource(110, 140)  # dnacodec.simulator.simulate
    rng = np.random.default_rng(2)
    batch = make_batch(*source.sample(16, rng), rng)
    assert batch.reads.shape[0] == 16 and batch.targets.shape[1] in range(110, 141)


def test_coverage_augmentation_hits_every_budget():
    rng = np.random.default_rng(0)
    refs = random_strands(400, 110, rng)
    clusters = [[r] * 16 for r in refs]
    batch = make_batch(refs, clusters, rng)
    sizes = (batch.reads[:, :, 0] != PAD).sum(1)
    assert sizes.min() == 1 and sizes.max() == 16
    assert (sizes <= 6).float().mean() > 0.5  # extra weight on low coverage


def test_split_train_val_is_disjoint():
    rng = np.random.default_rng(0)
    refs = random_strands(50, 20, rng)
    pool, (val_refs, _) = split_train_val(refs, [[r] for r in refs], 10)
    assert len(val_refs) == 10 and len(pool) == 40
    assert not set(val_refs) & set(pool.references)


def _trained_tiny(tmp_path, steps=300):
    """Tiny model trained briefly on mock-simulated data, saved as a checkpoint."""
    torch.manual_seed(0)
    source = SimSource(30, 40, simulate_fn=MockSimulate(), profile_fn=_low_noise_profile)
    model = ConsensusNet(PRESETS["tiny"])
    opt = make_optimizer(model, 3e-3)
    first = []
    run_steps(model, batch_loader(source, 32, seed=3), opt, steps, 3e-3, 10, log_every=0,
              on_step=lambda s, ema: first.append(ema) and False)
    path = tmp_path / "tiny.pt"
    save_checkpoint(path, model, step=steps)
    return path, first


def _low_noise_profile(rng):
    from dnacodec.model.data import random_profile

    p = random_profile(rng)
    return p.__class__(**{**p.to_dict(), "sub_rate": 0.01, "ins_rate": 0.01, "del_rate": 0.01})


def test_training_learns_and_decoder_roundtrip(tmp_path):
    path, losses = _trained_tiny(tmp_path)
    assert losses[-1] < 0.7 * losses[0], f"loss did not drop: {losses[0]:.3f} -> {losses[-1]:.3f}"

    dec = TransformerDecoder(path, device="cpu")
    rng = np.random.default_rng(7)
    refs = random_strands(20, 35, rng)
    clusters = [[noisy_copy(r, rng, 0.01) for _ in range(5)] for r in refs] + [[]]
    out = dec.decode(clusters, 35)
    assert len(out) == 21 and out[-1] is None
    assert all(isinstance(s, str) and len(s) == 35 and set(s) <= set("ACGT") for s in out[:-1])
    # Every strand length the loop may ask for, up to 140, gives exactly that many bases.
    for length in (110, 140):
        assert all(len(s) == length for s in dec.decode([["ACGT" * 40]] * 3, length))
    with pytest.raises(ValueError):
        dec.decode([["ACGT"]], 1000)


def test_finetune_writes_loadable_checkpoint(tmp_path):
    path, _ = _trained_tiny(tmp_path, steps=5)
    rng = np.random.default_rng(11)
    refs = random_strands(40, 36, rng)
    clusters = [[noisy_copy(r, rng, 0.02) for _ in range(8)] for r in refs]
    out = finetune(path, clusters, refs, steps=5, out_path=tmp_path / "ft.pt", batch_size=8, device="cpu")
    assert out.exists()
    dec = TransformerDecoder(out, device="cpu")
    assert dec.checkpoint_info["source"] == "finetune"
    assert all(len(s) == 36 for s in dec.decode(clusters[:4], 36))


def test_predict_restores_training_mode():
    model = ConsensusNet(TINY).train()
    predict(model, [["ACGT"]], 10)
    assert model.training


@pytest.mark.skipif(not realdata.MICROSOFT_DIR.exists(), reason="run scripts/download_data.sh")
def test_train_cli_end_to_end(tmp_path):
    from dnacodec.model.train import main

    main(["--source", "real", "--model", "tiny", "--steps", "4", "--batch-size", "4",
          "--eval-every", "2", "--val-size", "8", "--heldout-n", "8", "--limit-clusters", "50",
          "--checkpoint-dir", str(tmp_path), "--run-name", "t", "--device", "cpu", "--warmup", "2"])
    run = tmp_path / "t"
    assert (run / "best.pt").exists() and (run / "last.pt").exists()
    lines = (run / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == 2 and "heldout/microsoft" in lines[-1] and "cov2" in lines[-1]
    dec = TransformerDecoder(run / "best.pt", device="cpu")
    assert len(dec.decode([["ACGT" * 28]], 110)[0]) == 110
