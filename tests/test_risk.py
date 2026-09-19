"""Tests for the risk model (Agent F). Small and CPU-only so they stay fast."""

from __future__ import annotations

import dataclasses
import pickle

import numpy as np
import pytest

from dnacodec import risk
from dnacodec.profiles import load_profile
from dnacodec.seeds import heldout_seeds, train_seed
from dnacodec.types import EncoderSettings

torch = pytest.importorskip("torch")


# -- generator ---------------------------------------------------------------------------


def test_generate_strands_deterministic_and_valid():
    a = risk.generate_strands(300, (100, 140), seed=train_seed(3))
    b = risk.generate_strands(300, (100, 140), seed=train_seed(3))
    c = risk.generate_strands(300, (100, 140), seed=train_seed(4))
    assert a == b and a != c
    assert all(set(s) <= set("ACGT") and 100 <= len(s) <= 140 for s in a)


def test_generate_strands_varies_risk_factors():
    s = risk.generate_strands(2000, 140, seed=train_seed(5))
    runs = np.array([risk.max_run_length(x) for x in s])
    gcs = np.array([risk.gc_fraction(x) for x in s])
    assert (runs >= 8).mean() > 0.05  # long homopolymers are well represented
    assert (runs <= 4).mean() > 0.2  # but so are ordinary strands
    assert (gcs < 0.3).mean() > 0.02 and (gcs > 0.7).mean() > 0.02
    uniform = risk.generate_strands(500, 140, seed=train_seed(5), mix={"uniform": 1.0})
    assert abs(np.mean([risk.gc_fraction(x) for x in uniform]) - 0.5) < 0.02


def test_seed_discipline():
    h = heldout_seeds(1)[0]
    with pytest.raises(ValueError):
        risk.generate_strands(10, seed=h)  # held-out seed on the training path
    with pytest.raises(ValueError):
        risk.generate_strands(10, seed=1, heldout=True)  # train seed on the held-out path
    assert len(risk.generate_strands(10, seed=h, heldout=True)) == 10
    for key in range(50):
        assert not 900_000 <= risk._derived_seed(1, key) < 901_000


# -- labeling ----------------------------------------------------------------------------


class FirstReadDecoder:
    name = "first_read"

    def decode(self, clusters, strand_length):
        return [c[0] if c else None for c in clusters]


class WrongDecoder:
    name = "wrong"

    def decode(self, clusters, strand_length):
        return ["A" * strand_length if c else None for c in clusters]


def _profile(**changes):
    return dataclasses.replace(load_profile("nanopore_budget"), **changes)


def test_labels_zero_on_noise_free_channel_and_one_for_wrong_decoder():
    clean = _profile(sub_rate=0.0, ins_rate=0.0, del_rate=0.0)
    strands = risk.generate_strands(40, (100, 140), seed=train_seed(6))
    y = risk.label_failure_rates(strands, FirstReadDecoder(), clean, k=4, seed=train_seed(1), workers=1)
    assert y.shape == (40,) and np.all(y == 0.0)
    strands = [s for s in strands if s != "A" * len(s)]
    y = risk.label_failure_rates(strands, WrongDecoder(), clean, k=4, seed=train_seed(1), workers=1)
    assert np.all(y == 1.0)


def test_dropouts_excluded_from_denominator():
    lossy = _profile(sub_rate=0.0, ins_rate=0.0, del_rate=0.0, dropout_rate=0.9)
    strands = risk.generate_strands(30, 110, seed=train_seed(7))
    fails, valid = risk.label_failure_counts(strands, FirstReadDecoder(), lossy, k=8, seed=train_seed(2), workers=1)
    assert valid.sum() < 30 * 8 * 0.5  # most clusters were dropped...
    assert fails.sum() == 0  # ...and none of them counted as failures
    y = risk.label_failure_rates(strands, FirstReadDecoder(), lossy, k=8, seed=train_seed(2), workers=1)
    assert np.all((y == 0) | np.isnan(y))
    assert np.array_equal(np.isnan(y), valid == 0)


def test_labels_deterministic_across_workers_and_seed_dependent():
    prof = _profile(sub_rate=0.05, ins_rate=0.05, del_rate=0.05)
    strands = risk.generate_strands(150, 110, seed=train_seed(8))  # 3 chunks
    y1 = risk.label_failure_rates(strands, FirstReadDecoder(), prof, k=3, seed=train_seed(3), workers=1)
    y2 = risk.label_failure_rates(strands, FirstReadDecoder(), prof, k=3, seed=train_seed(3), workers=2)
    y3 = risk.label_failure_rates(strands, FirstReadDecoder(), prof, k=3, seed=train_seed(4), workers=1)
    assert np.array_equal(y1, y2, equal_nan=True)
    assert not np.array_equal(y1, y3, equal_nan=True)
    assert 0.5 < np.nanmean(y1) <= 1.0  # first read of a noisy channel is usually wrong


def test_labels_with_baseline_rise_with_homopolymers():
    """End to end on the real simulator and baseline: long runs fail more on Nanopore."""
    from dnacodec.baseline import MajorityVoteDecoder

    prof = load_profile("nanopore_budget")
    rng = np.random.default_rng(train_seed(9))
    plain, runny = [], []
    for _ in range(60):
        s = rng.integers(0, 4, 110)
        plain.append(risk._decode_codes(s))
        s = s.copy()
        s[40:49] = 0  # a run of 9 As
        runny.append(risk._decode_codes(s))
    y = risk.label_failure_rates(plain + runny, MajorityVoteDecoder(), prof, k=8, seed=train_seed(5), workers=2)
    assert np.nanmean(y[60:]) > np.nanmean(y[:60]) + 0.05


# -- metrics -----------------------------------------------------------------------------


def test_roc_auc_and_spearman():
    assert risk.roc_auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert risk.roc_auc(np.array([0, 0, 1, 1]), np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert risk.roc_auc(np.array([0, 1]), np.array([0.5, 0.5])) == 0.5
    assert np.isnan(risk.roc_auc(np.array([1, 1]), np.array([0.1, 0.2])))
    assert risk.spearman(np.arange(10), np.arange(10) ** 3) == pytest.approx(1.0)
    rng = np.random.default_rng(train_seed(0))
    y, s = rng.random(400) > 0.5, rng.random(400)
    ranks = risk._rankdata(s)
    brute = np.mean([(a > b) + 0.5 * (a == b) for a in s[y] for b in s[~y]])
    assert risk.roc_auc(y, s) == pytest.approx(brute)
    assert ranks.min() == 1 and ranks.max() == 400


# -- models ------------------------------------------------------------------------------


def _toy_data(n: int, seed: int):
    strands = risk.generate_strands(n, (100, 140), seed=train_seed(seed))
    y = np.array([min(1.0, max(0.0, (risk.max_run_length(s) - 2) / 8)) for s in strands])
    return strands, y


@pytest.fixture(scope="module")
def fitted():
    strands, y = _toy_data(800, 10)
    model = risk.RiskModel(channels=16, n_blocks=2, epochs=6, batch_size=64, lr=5e-3, device="cpu", seed=1)
    return model.fit(strands, y)


def test_cnn_learns_toy_signal_and_is_a_scorer(fitted):
    strands, y = _toy_data(300, 11)
    pred = fitted(strands)
    assert pred.shape == (300,) and pred.dtype == np.float64
    assert np.all((pred >= 0) & (pred <= 1))
    assert risk.spearman(y, pred) > 0.5
    assert fitted([]).shape == (0,)
    with pytest.raises(ValueError):
        risk.RiskModel(device="cpu").fit(strands[:3], y[:2])


def test_fit_ignores_nan_labels():
    strands, y = _toy_data(120, 12)
    y[::3] = np.nan
    model = risk.RiskModel(channels=8, n_blocks=1, epochs=1, device="cpu").fit(strands, y)
    assert np.all(np.isfinite(model(strands[:5])))


def test_save_load_and_pickle_roundtrip(fitted, tmp_path):
    strands, _ = _toy_data(50, 13)
    path = fitted.save(tmp_path / "risk.pt")
    loaded = risk.RiskModel.load(path, device="cpu")
    assert np.allclose(loaded(strands), fitted(strands), atol=1e-6)
    clone = pickle.loads(pickle.dumps(fitted))
    assert np.allclose(clone(strands), fitted(strands), atol=1e-5)


def test_cpu_im2col_conv_matches_torch_conv():
    conv = torch.nn.Conv1d(6, 8, 9, padding=4)
    x = torch.randn(3, 6, 40)
    assert torch.allclose(risk._conv(conv, x), conv(x), atol=1e-5)


def test_top_kmers_prefers_homopolymers(fitted):
    top = fitted.top_kmers(k=4, n=10)
    assert len(top) == 10 and all(len(k) == 4 for k, _ in top)
    assert [v for _, v in top] == sorted((v for _, v in top), reverse=True)
    assert top[0][0] in {"AAAA", "CCCC", "GGGG", "TTTT"}
    assert top[0][1] > fitted.background_risk


def test_kmer_fallback_same_interface(tmp_path):
    strands, y = _toy_data(400, 14)
    model = risk.KmerRiskModel(max_k=3).fit(strands, y)
    test, ty = _toy_data(200, 15)
    pred = model(test)
    assert pred.shape == (200,) and np.all((pred >= 0) & (pred <= 1))
    assert risk.spearman(ty, pred) > 0.5
    loaded = risk.load_risk_model(model.save(tmp_path / "kmer.pkl"))
    assert np.allclose(loaded(test), pred)
    assert len(model.top_kmers(k=3, n=5)) == 5


def test_probes_run(fitted):
    hp = risk.homopolymer_probe(fitted, run_lengths=(1, 4, 8), n=64)
    assert hp[8] > hp[1]
    gc = risk.gc_probe(fitted, gc_values=(0.3, 0.5), n=32)
    assert set(gc) == {0.3, 0.5}


def test_risk_model_plugs_into_encoder(fitted):
    from dnacodec.encoder import encode, recover

    data = np.random.default_rng(train_seed(2)).integers(0, 256, 2000, dtype=np.uint8).tobytes()
    settings = EncoderSettings(max_homopolymer=None, gc_min=None, gc_max=None)
    enc = encode(data, settings, scorer=fitted)
    assert recover(enc.strands, enc.meta) == data
    default = encode(data, settings)
    # the risk-scored encoder picks strands the model rates safer than first-passing ones
    assert fitted(enc.strands).mean() < fitted(default.strands).mean()
