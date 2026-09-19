"""Checks the shared foundation. Must stay green at every merge."""

import dataclasses

import pytest

from dnacodec import realdata
from dnacodec.encoder import max_run_length, payload_bits_per_base
from dnacodec.profiles import SituationProfile, list_profiles, load_profile
from dnacodec.results import load_runs, load_summary
from dnacodec.seeds import heldout_seeds, is_heldout, train_seed
from dnacodec.types import EncoderSettings, FileMeta


def test_all_profiles_load():
    names = list_profiles()
    assert len(names) >= 3
    for name in names:
        profile = load_profile(name)
        assert profile.name == name
        assert 0 <= profile.total_dropout < 1


def test_profile_rejects_bad_values():
    data = load_profile("nanopore_budget").to_dict()
    with pytest.raises(ValueError):
        SituationProfile.from_dict({**data, "sub_rate": 1.5})
    with pytest.raises(ValueError):
        SituationProfile.from_dict({**data, "typo_field": 1})


def test_seed_guard():
    assert train_seed(42) == 42
    with pytest.raises(ValueError):
        train_seed(heldout_seeds(1)[0])
    assert all(is_heldout(s) for s in heldout_seeds())


def test_helpers():
    assert max_run_length("ACGTTTTA") == 4
    assert max_run_length("") == 0
    meta = FileMeta(EncoderSettings(strand_length=100), n_bytes=100, n_chunks=4)
    assert payload_bits_per_base(meta, n_strands=4) == 2.0


def test_mock_results_roundtrip():
    from scripts.make_mock_results import main

    main()
    runs = load_runs("mock")
    assert {r.situation for r in runs} == {"nanopore_budget", "illumina_standard"}
    for run in runs:
        assert run.is_mock
        assert run.best.min_reads_at_target < run.default_min_reads_at_target
        assert dataclasses.asdict(run) == run.to_dict()
    summary = load_summary("mock")
    assert summary is not None and summary.is_mock
    assert {e.system for e in summary.ablation} == {"A", "B", "C", "D"}
    assert summary.to_dict() == type(summary).from_dict(summary.to_dict()).to_dict()


def test_profile_realism_fields_roundtrip():
    data = load_profile("nanopore_budget").to_dict()
    profile = SituationProfile.from_dict({**data, "homopolymer_run_factors": [1.0, 1.0, 1.1, 1.8]})
    assert profile.homopolymer_run_factors == (1.0, 1.0, 1.1, 1.8)
    assert SituationProfile.from_dict(profile.to_dict()) == profile


@pytest.mark.skipif(not realdata.MICROSOFT_DIR.exists(), reason="run scripts/download_data.sh")
def test_microsoft_dataset():
    everything = realdata.load_microsoft("all")
    assert len(everything) == 10_000
    assert sum(len(c.reads) for c in everything) == 269_709
    assert all(len(c.reference) == 110 for c in everything)
    train, heldout = realdata.load_microsoft("train"), realdata.load_microsoft("heldout")
    assert len(train) + len(heldout) == 10_000
    assert not {c.reference for c in train} & {c.reference for c in heldout}


@pytest.mark.skipif(
    not (realdata.DNAFORMER_DIR / "BinnedNanoporeSecondFlowcell_Random.txt").exists(),
    reason="run scripts/download_data.sh",
)
def test_dnaformer_dataset():
    clusters = realdata.load_dnaformer("BinnedNanoporeSecondFlowcell_Random", "all")
    assert len(clusters) > 1000
    assert all(set(c.reference) <= set("ACGT") for c in clusters)
    assert sum(len(c.reads) for c in clusters) > len(clusters)


def test_test_file_is_fixed():
    import hashlib

    from dnacodec.testfile import test_file

    data = test_file()
    assert len(data) == 20 * 1024
    assert data == test_file()
    assert hashlib.sha256(data).hexdigest()[:16] == "575e0ef9a36dfce0"  # must never change
