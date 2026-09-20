"""Tests for the v2 polisher (dnacodec.model.polish2)."""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from dnacodec.baseline import MAX_READS, _to_array  # noqa: E402
from dnacodec.model.polish import features_of, votes_of  # noqa: E402
from dnacodec.model.polish2 import (  # noqa: E402
    PAD_COL,
    PolishConfig2,
    PolishNet2,
    Polish2Decoder,
    count_parameters,
    expand_features,
    pack,
    pack_cluster,
    polish2_clusters,
    read_columns,
    save_checkpoint,
    load_checkpoint,
)


def _cluster(rng, length=40, n_reads=6, error=0.06):
    ref = "".join(rng.choice(list("ACGT"), size=length))
    reads = []
    for _ in range(n_reads):
        out = []
        for base in ref:
            roll = rng.random()
            if roll < error / 3:
                continue  # deletion
            if roll < 2 * error / 3:
                out.append(rng.choice(list("ACGT")))
                continue  # substitution
            if roll < error:
                out.append(rng.choice(list("ACGT")))
            out.append(base)
        reads.append("".join(out))
    return ref, reads


def test_read_columns_sum_to_the_baseline_votes():
    """Summing the per-read one-hots over reads must reproduce the v1 vote columns exactly."""
    rng = np.random.default_rng(0)
    for _ in range(20):
        ref, reads = _cluster(rng)
        draft = reads[0]
        arrays = [_to_array(r) for r in reads]
        cols, insb = read_columns(draft, reads, arrays)
        base_votes, ins_votes, ins_base, _ = votes_of(draft, reads, arrays)
        length = len(draft)
        for b in range(5):
            assert np.array_equal((cols == b).sum(0), base_votes[:, b])
        assert np.array_equal((insb > 0).sum(0), ins_votes[:length])
        for b in range(4):
            assert np.array_equal((insb == b + 1).sum(0), ins_base[:length, b])


def test_expand_features_matches_v1_features():
    """The 17 features recomputed on tensors are the ones features_of() builds."""
    rng = np.random.default_rng(1)
    packed, expected = [], []
    for _ in range(8):
        ref, reads = _cluster(rng, n_reads=int(rng.integers(1, MAX_READS + 1)))
        draft = reads[0]
        arrays = [_to_array(r) for r in reads]
        # all drafts must share a length for stacking, so pad the comparison per example
        packed.append(pack(draft, reads, arrays))
        expected.append(features_of(draft, votes_of(draft, reads, arrays)))
    for p, want in zip(packed, expected):
        cols = torch.from_numpy(p[0][None]).long()
        insb = torch.from_numpy(p[1][None]).long()
        draft_t = torch.from_numpy(p[2][None]).long()
        nreads = torch.tensor([p[3]])
        x_read, x_pos, mask4 = expand_features(cols, insb, draft_t, nreads)
        assert x_read.shape == (1, MAX_READS, 10, want.shape[1])
        assert float(mask4.sum()) == p[3]
        assert np.allclose(x_pos[0].numpy(), want, atol=1e-5)


def test_padded_read_slots_are_masked_out():
    rng = np.random.default_rng(2)
    ref, reads = _cluster(rng, n_reads=3)
    cols, insb, draft, n = pack(reads[0], reads, [_to_array(r) for r in reads])
    assert n == 3
    assert (cols[3:] == PAD_COL).all()
    x_read, _, mask4 = expand_features(
        torch.from_numpy(cols[None]).long(), torch.from_numpy(insb[None]).long(),
        torch.from_numpy(draft[None]).long(), torch.tensor([n])
    )
    assert float(x_read[0, 3:].abs().sum()) == 0.0


def test_net_runs_and_is_permutation_invariant():
    torch.manual_seed(0)
    rng = np.random.default_rng(3)
    ref, reads = _cluster(rng, n_reads=5)
    arrays = [_to_array(r) for r in reads]
    model = PolishNet2(PolishConfig2(channels=32, blocks=(1, 2), read_channels=16,
                                     read_blocks=(1,), read_blocks2=(1,), groups=4)).eval()
    def logits(order):
        r = [reads[i] for i in order]
        a = [arrays[i] for i in order]
        p = pack(reads[0], r, a)
        args = expand_features(torch.from_numpy(p[0][None]).long(),
                               torch.from_numpy(p[1][None]).long(),
                               torch.from_numpy(p[2][None]).long(), torch.tensor([p[3]]))
        with torch.no_grad():
            return model(*args)[0]
    a = logits([0, 1, 2, 3, 4])
    b = logits([4, 2, 0, 3, 1])
    assert torch.allclose(a, b, atol=1e-4)


def test_pack_cluster_labels_and_decoder_roundtrip(tmp_path):
    rng = np.random.default_rng(4)
    refs, clusters = [], []
    for _ in range(6):
        ref, reads = _cluster(rng, length=60, n_reads=5)
        refs.append(ref)
        clusters.append(reads)
    packed = pack_cluster(clusters[0], len(refs[0]), refs[0])
    assert packed[4].shape == packed[5].shape == (len(refs[0]),)
    model = PolishNet2(PolishConfig2(channels=32, blocks=(1, 2), read_channels=16,
                                     read_blocks=(1,), read_blocks2=(1,), groups=4)).eval()
    assert count_parameters(model) > 0
    out = polish2_clusters(model, clusters + [[]], len(refs[0]), device="cpu")
    assert out[-1] is None
    assert all(len(s) == len(refs[0]) for s in out[:-1])
    out_multi = polish2_clusters(model, clusters, len(refs[0]), device="cpu",
                                 rounds=2, drafts=3, mode="gain",
                                 thresholds={"low_max_reads": 3, "low": (0.5, -0.5),
                                             "high": (0.0, -1.0)})
    assert all(len(s) == len(refs[0]) for s in out_multi)

    path = tmp_path / "p2.pt"
    save_checkpoint(path, model, step=1)
    loaded, ckpt = load_checkpoint(path)
    assert ckpt["arch"] == "polish2"
    decoder = Polish2Decoder(path, device="cpu")
    assert decoder.name == "polish2"
    again = decoder.decode(clusters, len(refs[0]))
    assert again == polish2_clusters(loaded, clusters, len(refs[0]), device="cpu",
                                     thresholds=decoder.thresholds)
    decoder.close()
