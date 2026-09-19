"""Tests for the learned polisher (dnacodec.model.polish)."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.model.polish import (  # noqa: E402
    DELETE,
    N_FEATURES,
    N_INS,
    N_OPS,
    PolishConfig,
    PolishDecoder,
    PolishNet,
    apply_edits,
    count_parameters,
    draft_of,
    example_of,
    features_of,
    labels_of,
    polish_clusters,
    save_checkpoint,
)
from dnacodec.types import ALPHABET  # noqa: E402

TINY = PolishConfig(channels=16, blocks=(1, 2))


def noisy_copy(ref: str, rng: np.random.Generator, p: float = 0.03) -> str:
    out = []
    for base in ref:
        u = rng.random()
        if u < p:
            out.append(ALPHABET[(ALPHABET.index(base) + rng.integers(1, 4)) % 4])
        elif u < 2 * p:
            continue
        elif u < 3 * p:
            out.append(base)
            out.append(ALPHABET[rng.integers(4)])
        else:
            out.append(base)
    return "".join(out)


def random_ref(n: int, rng: np.random.Generator) -> str:
    return "".join(ALPHABET[i] for i in rng.integers(0, 4, size=n))


def test_draft_matches_baseline_and_features_are_sane():
    rng = np.random.default_rng(0)
    ref = random_ref(110, rng)
    cluster = [noisy_copy(ref, rng) for _ in range(6)]
    draft, votes = draft_of(cluster, 110)
    assert len(draft) == 110
    assert draft == MajorityVoteDecoder().decode([cluster], 110)[0]
    f = features_of(draft, votes)
    assert f.shape == (N_FEATURES, 110)
    assert np.isfinite(f).all() and f.min() >= 0.0 and f.max() <= 1.01


def test_labels_describe_the_edit_from_draft_to_truth():
    ops, ins = labels_of("ACGTACGT", "ACTTACGT")  # one substitution at position 2
    assert ops.shape == (8,) and ins.shape == (8,)
    assert ops[2] == 1 + ALPHABET.index("T")
    assert (ins == 0).all() and (np.delete(ops, 2) == 0).all()

    ops, _ = labels_of("ACGGT", "ACGT")  # one base too many in the draft
    assert DELETE in ops.tolist()

    _, ins = labels_of("ACGT", "ACGGT")  # one base missing in the draft
    assert (ins == 1 + ALPHABET.index("G")).sum() == 1


def test_applying_true_labels_recovers_the_truth():
    rng = np.random.default_rng(3)
    for _ in range(20):
        ref = random_ref(60, rng)
        cluster = [noisy_copy(ref, rng, 0.05) for _ in range(3)]
        draft, votes = draft_of(cluster, 60)
        ops, ins = labels_of(draft, ref)
        # one-hot "probabilities" from the true labels
        op_p = np.eye(N_OPS, dtype=np.float32)[ops].T
        ins_p = np.eye(N_INS, dtype=np.float32)[ins].T
        out = apply_edits(draft, op_p, ins_p, 60)
        assert len(out) == 60
        if (ops == DELETE).sum() == (ins != 0).sum():  # pairing keeps everything
            assert out == ref


def test_apply_edits_always_returns_strand_length():
    rng = np.random.default_rng(5)
    draft = random_ref(110, rng)
    for _ in range(10):
        op_p = rng.random((N_OPS, 110)).astype(np.float32)
        ins_p = rng.random((N_INS, 110)).astype(np.float32)
        assert len(apply_edits(draft, op_p, ins_p, 110)) == 110


def test_model_shapes_and_size():
    model = PolishNet()
    x = torch.zeros(2, N_FEATURES, 140)
    op_logits, ins_logits = model(x)
    assert op_logits.shape == (2, N_OPS, 140) and ins_logits.shape == (2, N_INS, 140)
    assert 0.3e6 < count_parameters(model) < 2e6


def test_decoder_protocol_and_lengths(tmp_path):
    assert PolishDecoder.main_process_only is True
    save_checkpoint(tmp_path / "p.pt", PolishNet(TINY))
    dec = PolishDecoder(tmp_path / "p.pt", device="cpu", batch_size=8)
    assert dec.name == "polish"
    rng = np.random.default_rng(11)
    refs = [random_ref(110, rng) for _ in range(20)]
    clusters = [[noisy_copy(r, rng) for _ in range(int(rng.integers(1, 20)))] for r in refs] + [[]]
    out = dec.decode(clusters, 110)
    assert len(out) == 21 and out[-1] is None
    assert all(len(o) == 110 and set(o) <= set(ALPHABET) for o in out[:-1])
    for length in (60, 140):
        refs = [random_ref(length, rng) for _ in range(3)]
        cl = [[noisy_copy(r, rng) for _ in range(4)] for r in refs]
        assert all(len(o) == length for o in dec.decode(cl, length))


def test_untrained_polisher_keeps_baseline_quality():
    """An untrained net should mostly predict nothing, so the draft survives."""
    torch.manual_seed(0)
    model = PolishNet(TINY).eval()
    rng = np.random.default_rng(13)
    refs = [random_ref(110, rng) for _ in range(30)]
    clusters = [[noisy_copy(r, rng) for _ in range(8)] for r in refs]
    drafts = MajorityVoteDecoder().decode(clusters, 110)
    out = polish_clusters(model, clusters, 110, device="cpu")
    changed = sum(a != b for a, b in zip(drafts, out))
    assert changed <= len(refs)  # it may edit, but it never breaks the length
    assert all(len(o) == 110 for o in out)


def test_thresholds_gate_the_edits():
    """With a high threshold nothing is edited; with a low one the argmax decides."""
    rng = np.random.default_rng(23)
    draft = random_ref(40, rng)
    op_p = np.full((N_OPS, 40), 0.1, dtype=np.float32)
    op_p[1] = 0.6  # substitute to A everywhere, 60% sure
    ins_p = np.zeros((N_INS, 40), dtype=np.float32)
    ins_p[0] = 1.0
    assert apply_edits(draft, op_p, ins_p, 40, sub_threshold=0.9, indel_threshold=0.9) == draft
    assert apply_edits(draft, op_p, ins_p, 40, sub_threshold=0.5, indel_threshold=0.5) == "A" * 40


def test_decoder_uses_thresholds_from_the_checkpoint(tmp_path):
    from dnacodec.model.polish import load_checkpoint

    th = {"low_max_reads": 4, "low": (0.9, 0.9), "high": (0.2, 0.3)}
    save_checkpoint(tmp_path / "t.pt", PolishNet(TINY), thresholds=th)
    _, ckpt = load_checkpoint(tmp_path / "t.pt")
    assert ckpt["thresholds"] == th
    dec = PolishDecoder(tmp_path / "t.pt", device="cpu")
    assert dec.thresholds == th


def test_example_of_returns_features_and_labels():
    rng = np.random.default_rng(17)
    ref = random_ref(110, rng)
    cluster = [noisy_copy(ref, rng) for _ in range(4)]
    f, ops, ins = example_of(cluster, 110, ref)
    assert f.shape == (N_FEATURES, 110) and ops.shape == (110,) and ins.shape == (110,)
    assert example_of([], 110, ref) is None
