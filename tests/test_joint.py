"""Tests for dnacodec.joint (distilled risk labels). Small and CPU-only so they stay fast."""

from __future__ import annotations

import numpy as np
import pytest

from dnacodec.profiles import load_profile
from dnacodec.seeds import heldout_seeds, train_seed

torch = pytest.importorskip("torch")

from dnacodec import joint  # noqa: E402
from dnacodec.model.polish import PolishConfig, PolishNet, polish_clusters  # noqa: E402

TINY = PolishConfig(channels=16, blocks=(1, 2))
PROFILE = load_profile("nanopore_budget")


@pytest.fixture(scope="module")
def net() -> PolishNet:
    torch.manual_seed(0)
    return PolishNet(TINY).eval()


def strands(n: int, length: int = 60, seed: int = 7) -> list[str]:
    rng = np.random.default_rng(seed)
    return ["".join("ACGT"[c] for c in rng.integers(0, 4, length)) for _ in range(n)]


def clusters_for(seq: list[str], seed: int = 11):
    from dnacodec.simulator import simulate

    return simulate(seq, PROFILE, train_seed(seed))


# -- simulator selection -----------------------------------------------------------------


def test_get_simulate_picks_the_right_channel():
    from dnacodec import simulator, simulator_b

    assert joint.get_simulate("A") is simulator.simulate
    assert joint.get_simulate("b") is simulator_b.simulate
    with pytest.raises(ValueError):
        joint.get_simulate("C")


# -- polisher signals --------------------------------------------------------------------


def test_confidence_does_not_change_the_decoded_strand(net):
    seq = strands(24)
    cl = clusters_for(seq)
    control = polish_clusters(net, cl, 60)
    out = joint.polish_with_confidence(net, cl, 60, dense=True)
    assert out["strand"] == control  # labeling never perturbs the thing being labeled


def test_confidence_signals_are_probabilities(net):
    seq = strands(24)
    cl = clusters_for(seq)
    out = joint.polish_with_confidence(net, cl, 60, dense=True)
    for key in ("surr_pre", "surr_post"):
        v = out[key]
        seen = v[~np.isnan(v)]
        assert len(seen) > 0
        assert np.all((seen >= 0.0) & (seen <= 1.0))
    # a NaN exactly where the cluster was empty, never anywhere else
    empty = np.array([not any(c) for c in cl])
    assert np.array_equal(np.isnan(out["surr_post"]), empty)
    assert out["per_pos"].shape == (len(cl), 2, 60)
    dense = out["per_pos"][~empty]
    assert np.all((dense >= 0.0) & (dense <= 1.0))


def test_confidence_pool_matches_single_process(net):
    seq = strands(80)
    cl = clusters_for(seq)
    serial = joint.polish_with_confidence(net, cl, 60, rescore_pool=joint._RescorePool(1))
    pool = joint._RescorePool(2)
    try:
        parallel = joint.polish_with_confidence(net, cl, 60, rescore_pool=pool)
    finally:
        pool.close()
    assert serial["strand"] == parallel["strand"]
    np.testing.assert_allclose(serial["surr_post"], parallel["surr_post"], rtol=1e-5, atol=1e-6)


# -- labeling ----------------------------------------------------------------------------


def test_label_with_polisher_shapes_and_failure_agreement(net):
    seq = strands(12, 60, seed=3)
    batch = joint.label_with_polisher(
        seq, net, PROFILE, k=4, seed=train_seed(5), dense_k=2, workers=1
    )
    assert batch.fail.shape == (12, 4)
    assert batch.surr_post.shape == (12, 4)
    assert batch.per_pos.shape == (12, 2, 2, 60)
    ok = ~np.isnan(batch.fail)
    assert set(np.unique(batch.fail[ok]).tolist()) <= {0.0, 1.0}
    rate = batch.rate("fail")
    assert np.all((rate[~np.isnan(rate)] >= 0) & (rate[~np.isnan(rate)] <= 1))
    assert batch.dense_target(2).shape == (12, 2, 60)


def test_label_rate_slice_uses_disjoint_simulations(net):
    seq = strands(8, 60, seed=4)
    batch = joint.label_with_polisher(seq, net, PROFILE, k=4, seed=train_seed(6), workers=1)
    first = batch.rate("fail", 2)
    rest = batch.rate("fail", lo=2)
    both = batch.rate("fail")
    complete = ~np.isnan(batch.fail).any(axis=1)  # nanmean only averages cleanly without dropouts
    assert complete.any()
    np.testing.assert_allclose(both[complete], (first[complete] + rest[complete]) / 2, rtol=1e-5)


def test_labeling_reproduces_the_control_failure_rate(net):
    """Same seeds, same chunking as risk.label_failure_rates, so `fail` is the control label."""
    from dnacodec import risk

    class Wrap:
        def decode(self, clusters, strand_length):
            return polish_clusters(net, clusters, strand_length)

    seq = strands(16, 60, seed=9)
    mine = joint.label_with_polisher(seq, net, PROFILE, k=3, seed=train_seed(12), workers=1)
    theirs = risk.label_failure_rates(seq, Wrap(), PROFILE, k=3, seed=train_seed(12), workers=1)
    ok = ~np.isnan(theirs)
    np.testing.assert_allclose(mine.rate("fail")[ok], theirs[ok], rtol=1e-6)


def test_labeling_refuses_a_train_seed_for_heldout(net):
    with pytest.raises(ValueError):
        joint.label_with_polisher(strands(4), net, PROFILE, k=1, seed=5, heldout=True, workers=1)


# -- dense risk model --------------------------------------------------------------------


def test_dense_risk_model_fits_and_scores(tmp_path):
    seq = strands(120, 110, seed=21)
    rng = np.random.default_rng(0)
    y = np.clip(np.array([s.count("AAA") for s in seq], dtype=float) / 5 + rng.normal(0, 0.05, 120), 0, 1)
    dense = np.repeat(y[:, None, None], 2, axis=1) * np.ones((1, 1, 110))
    model = joint.DenseRiskModel(channels=8, n_blocks=1, epochs=3, seed=train_seed(1), device="cpu")
    model.fit_dense(seq, y, dense.astype(np.float32))
    pred = model(seq)
    assert pred.shape == (120,)
    assert np.all((pred >= 0) & (pred <= 1))
    path = model.save(tmp_path / "dense.pt")
    again = joint.DenseRiskModel.load(path, device="cpu")
    np.testing.assert_allclose(again(seq[:10]), pred[:10], rtol=1e-5, atol=1e-6)


def test_dense_risk_model_works_without_the_dense_target():
    seq = strands(60, 110, seed=22)
    y = np.linspace(0, 1, 60)
    model = joint.DenseRiskModel(channels=8, n_blocks=1, epochs=2, seed=train_seed(2), device="cpu")
    model.fit(seq, y)  # plain Scorer interface, no polisher signal at all
    assert model(seq).shape == (60,)


def test_dense_risk_model_is_a_scorer():
    from dnacodec.encoder import encode
    from dnacodec.types import EncoderSettings

    seq = strands(60, 110, seed=23)
    model = joint.DenseRiskModel(channels=8, n_blocks=1, epochs=2, seed=train_seed(3), device="cpu")
    model.fit(seq, np.linspace(0, 1, 60))
    settings = EncoderSettings(strand_length=110, redundancy=0.3)
    encoded = encode(b"hello world, twice over" * 4, settings, scorer=model)
    assert encoded.strands and all(len(s) == 110 for s in encoded.strands)


# -- selection regret --------------------------------------------------------------------


def test_selection_regret_picks_the_lowest_scored_candidate():
    scores = np.array([0.9, 0.1, 0.5, 0.2, 0.8, 0.7])
    truth = np.array([1.0, 0.0, 0.25, 0.75, 0.5, 0.5])
    out = joint.selection_regret(scores, truth, slots=2, candidates=3)
    assert out["picked"] == pytest.approx((0.0 + 0.75) / 2)  # argmin score per slot
    assert out["oracle"] == pytest.approx((0.0 + 0.5) / 2)
    assert out["first"] == pytest.approx((1.0 + 0.75) / 2)
    assert out["slots"] == 2


def test_selection_regret_drops_slots_with_unlabelled_candidates():
    scores = np.array([0.1, 0.2, 0.3, 0.4])
    truth = np.array([0.5, np.nan, 0.1, 0.2])
    out = joint.selection_regret(scores, truth, slots=2, candidates=2)
    assert out["slots"] == 1
    assert out["picked"] == pytest.approx(0.1)


def test_heldout_strands_are_not_train_strands():
    """The study's discipline in one assertion: the two sets never coincide."""
    from dnacodec import risk

    train = risk.generate_strands(200, 110, seed=train_seed(101))
    held = risk.generate_strands(200, 110, seed=heldout_seeds()[0], heldout=True)
    assert not (set(train) & set(held))
