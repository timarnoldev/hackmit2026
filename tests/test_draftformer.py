"""Tests for the DraftFormer decoder (Agent D)."""

from __future__ import annotations

import numpy as np
import pytest

from dnacodec.baseline import _DEL, _to_array, _votes
from dnacodec.model.data import random_profile, random_strands
from dnacodec.model.polish import draft_of, features_of
from dnacodec.simulator import simulate

torch = pytest.importorskip("torch")

from dnacodec.model.draftformer import (  # noqa: E402
    NO_ALIGN,
    DraftFormer,
    DraftFormerConfig,
    align_read,
    codes_to_strand,
    collate,
    count_parameters,
    decode_clusters,
    load_checkpoint,
    pack_cluster,
    pack_example,
    HALF_WINDOW,
    OUTSIDE,
    save_checkpoint,
)


def _clusters(n=8, length=110, coverage=8.0, seed=0):
    rng = np.random.default_rng(seed)
    profile = random_profile(rng, coverage_mean=coverage)
    refs = random_strands(n, length, rng)
    return refs, simulate(refs, profile, 4242)


def _tiny():
    return DraftFormer(DraftFormerConfig(d=32, d_read=16, heads=2, cross_layers=1, trunk_layers=1, dec_layers=1))


# ---------------------------------------------------------------- alignment


def test_align_read_round_trips_the_alignment():
    draft = "ACGTACGTAC"
    read = "ACTTACGGTAC"  # a deletion, a substitution and an insertion
    gather, coord, is_ins = align_read(draft, read, _to_array(read), len(draft))
    assert gather.shape == (len(draft),)
    assert coord.shape == is_ins.shape == (len(read),)
    for i, j in enumerate(gather.tolist()):
        if j != NO_ALIGN:
            assert 0 <= j < len(read)
            assert coord[j] == i
            assert is_ins[j] == 0
    # every read base is either aligned to a draft position or marked as an insertion
    aligned = set(int(j) for j in gather if j != NO_ALIGN)
    for j in range(len(read)):
        assert (j in aligned) != bool(is_ins[j])
    assert (coord >= 0).all() and (coord <= len(draft)).all()


def test_gathered_columns_reproduce_the_baseline_votes():
    """The per-read matrix must sum back to exactly what majority voting counted."""
    refs, clusters = _clusters(n=12, seed=1)
    for ref, cluster in zip(refs, clusters):
        reads = [r for r in cluster if r][:16]
        if not reads:
            continue
        draft, pack = pack_cluster(reads, len(ref))
        arrays = [_to_array(r) for r in reads]
        base_votes, _, _ = _votes(draft, reads, arrays)
        counts = np.zeros((len(draft), 5), dtype=np.int64)
        for r in range(pack.n_reads):
            for i in range(len(draft)):
                counts[i, int(pack.win[r, i, HALF_WINDOW]) if pack.aligned[r, i] else _DEL] += 1
        assert (counts == base_votes).all()


def test_pack_uses_the_same_draft_and_features_as_v1():
    refs, clusters = _clusters(n=6, seed=2)
    for ref, cluster in zip(refs, clusters):
        reads = [r for r in cluster if r][:16]
        if not reads:
            continue
        draft, pack = pack_cluster(reads, len(ref))
        v1_draft, v1_votes = draft_of(reads, len(ref))
        assert draft == v1_draft
        assert len(draft) == len(ref)
        assert np.allclose(pack.feats.astype(np.float32), features_of(v1_draft, v1_votes),
                           atol=1e-3)
        assert pack.win.shape == (16, len(ref), 2 * HALF_WINDOW + 1)
        assert pack.aligned.shape == (16, len(ref))
        assert pack.n_reads == len(reads)
        # unused read slots carry nothing
        assert (pack.win[pack.n_reads :] == OUTSIDE).all()
        assert (pack.aligned[pack.n_reads :] == 0).all()


def test_pack_cluster_handles_no_reads():
    assert pack_cluster([], 110) == (None, None)
    assert pack_cluster(["", ""], 110) == (None, None)


def test_pack_example_labels_match_the_draft():
    refs, clusters = _clusters(n=4, seed=3)
    for ref, cluster in zip(refs, clusters):
        reads = [r for r in cluster if r][:8]
        if not reads:
            continue
        pack, target, ops, ins = pack_example(reads, len(ref), ref)
        assert codes_to_strand(target) == ref
        assert ops.shape == ins.shape == (len(ref),)


# ---------------------------------------------------------------- model


def test_forward_shapes_and_parameter_count():
    refs, clusters = _clusters(n=5, seed=4)
    items = [pack_example([r for r in c if r][:4], 110, ref)
             for ref, c in zip(refs, clusters) if any(c)]
    items = [i for i in items if i is not None]
    batch = collate([i[0] for i in items])
    y = torch.from_numpy(np.stack([i[1] for i in items]).astype(np.int64))
    model = _tiny()
    logits, op_logits, ins_logits = model(batch, y)
    assert logits.shape == (len(items), 110, 4)
    assert op_logits.shape == (len(items), 6, 110)
    assert ins_logits.shape == (len(items), 5, 110)
    assert count_parameters(DraftFormer()) < 8_000_000  # small by construction


def test_generate_and_beam_produce_exactly_strand_length():
    refs, clusters = _clusters(n=5, seed=5)
    items = [pack_example([r for r in c if r][:4], 110, ref)
             for ref, c in zip(refs, clusters) if any(c)]
    items = [i for i in items if i is not None]
    batch = collate([i[0] for i in items])
    model = _tiny()
    mem, _, _ = model.encode(batch)
    greedy = model.generate(mem, 110)
    assert greedy.shape == (len(items), 110)
    assert int(greedy.max()) < 4 and int(greedy.min()) >= 0
    assert torch.equal(model.beam(mem, 110, 1), greedy)  # beam 1 is greedy
    assert model.beam(mem, 110, 3).shape == (len(items), 110)


def test_decode_clusters_respects_the_decoder_contract():
    refs, clusters = _clusters(n=6, seed=6)
    cut = [[r for r in c if r][:4] for c in clusters]
    cut[0] = []  # a dropout
    model = _tiny()
    for mode in ("greedy", "beam", "edit"):
        out = decode_clusters(model, cut, 110, mode=mode, beams=2)
        assert len(out) == len(cut)
        assert out[0] is None
        for strand, cluster in zip(out, cut):
            if cluster:
                assert strand is not None and len(strand) == 110
                assert set(strand) <= set("ACGT")


def test_decode_is_deterministic():
    refs, clusters = _clusters(n=4, seed=7)
    cut = [[r for r in c if r][:5] for c in clusters]
    model = _tiny()
    assert decode_clusters(model, cut, 110, mode="greedy") == \
        decode_clusters(model, cut, 110, mode="greedy")


def test_length_140_works():
    refs, clusters = _clusters(n=3, length=140, seed=8)
    cut = [[r for r in c if r][:6] for c in clusters]
    out = decode_clusters(_tiny(), cut, 140, mode="greedy")
    assert all(o is None or len(o) == 140 for o in out)


def test_checkpoint_round_trip(tmp_path):
    model = _tiny()
    path = tmp_path / "df.pt"
    save_checkpoint(path, model, step=3)
    loaded, ckpt = load_checkpoint(path)
    assert ckpt["step"] == 3
    assert loaded.cfg.d == model.cfg.d
    refs, clusters = _clusters(n=3, seed=9)
    cut = [[r for r in c if r][:4] for c in clusters]
    assert decode_clusters(loaded, cut, 110, mode="greedy") == \
        decode_clusters(model.eval(), cut, 110, mode="greedy")


def test_single_read_cluster():
    """One read is the hardest case: the draft is the read and there is nothing to vote on."""
    refs, clusters = _clusters(n=4, seed=10)
    cut = [[r for r in c if r][:1] for c in clusters]
    out = decode_clusters(_tiny(), cut, 110, mode="greedy")
    assert all(o is None or len(o) == 110 for o in out)


def test_cached_generation_matches_teacher_forcing():
    """The key/value cache must produce exactly what a full re-run of the prefix produces."""
    refs, clusters = _clusters(n=4, seed=11)
    items = [pack_example([r for r in c if r][:6], 110, ref)
             for ref, c in zip(refs, clusters) if any(c)]
    items = [i for i in items if i is not None]
    batch = collate([i[0] for i in items])
    model = _tiny().eval()
    with torch.no_grad():
        mem, _, _ = model.encode(batch)
        greedy = model.generate(mem, 110)
        # feeding the generated strand back in as the teacher must reproduce it greedily
        logits = model.decode_teacher(mem, greedy)
    assert torch.equal(logits.argmax(-1), greedy)
