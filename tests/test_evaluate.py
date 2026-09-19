"""Hand-built cases where the right metrics are known exactly."""

import dataclasses
import math

import pytest

from dnacodec.evaluate import evaluate
from dnacodec.profiles import load_profile
from dnacodec.types import EncoderSettings, FileMeta


def test_perfect_decode():
    refs = ["ACGT", "TTGA"]
    m = evaluate(refs, list(refs), [["ACGT"], ["TTGA", "TTG"]])
    assert m.n_strands == 2
    assert m.strand_accuracy == 1.0
    assert m.mean_edit_distance == 0.0
    assert m.dropout_rate == 0.0
    assert m.reads_per_strand == 1.5
    assert m.per_position_error == [0.0, 0.0, 0.0, 0.0]
    assert m.bits_per_base is None
    assert m.write_cost_usd_per_mb is None
    assert m.read_cost_usd_per_mb is None
    assert m.file_recovered is None


def test_none_and_dropout_count_as_wrong():
    refs = ["ACGT", "ACGT", "ACGT", "ACGT"]
    decoded = ["ACGT", None, "ACGT", "ACGA"]
    # third strand decodes to the right answer but its cluster is empty: still wrong
    clusters = [["ACGT"], [], [], ["ACGA", "ACGA"]]
    m = evaluate(refs, decoded, clusters)
    assert m.strand_accuracy == 0.25
    assert m.dropout_rate == 0.5
    assert m.reads_per_strand == 3 / 4
    # edit distance over the 3 non-None decodes: 0, 0, 1
    assert m.mean_edit_distance == pytest.approx(1 / 3)
    assert m.extra["decoded_fraction"] == 0.75


def test_per_position_error_attribution():
    refs = ["AAAAA", "CCCCC", "GGGGG", "TTTTT"]
    decoded = [
        "AAGAA",  # substitution at position 2
        "CCCC",  # deletion of one C (any position; rapidfuzz picks one)
        "GGGGGA",  # insertion after the last base, counts at the last position
        None,  # excluded from the denominator
    ]
    m = evaluate(refs, decoded, [["x"]] * 4)
    assert len(m.per_position_error) == 5
    assert m.mean_edit_distance == 1.0
    assert sum(m.per_position_error) == pytest.approx(m.mean_edit_distance)
    assert m.per_position_error[2] >= 1 / 3
    assert m.per_position_error[4] >= 1 / 3
    assert m.strand_accuracy == 0.0


def test_insertion_attributed_before_reference_base():
    # "ACGT" -> "ACTGT": T inserted in front of reference position 2
    m = evaluate(["ACGT"], ["ACTGT"], [["ACTGT"]])
    assert m.mean_edit_distance == 1.0
    assert m.per_position_error == [0.0, 0.0, 1.0, 0.0]


def test_substitution_positions_exact():
    refs = ["ACGTACGT", "ACGTACGT"]
    decoded = ["TCGTACGT", "ACGTACGA"]  # first and last base wrong
    m = evaluate(refs, decoded, [["r"], ["r"]])
    assert m.per_position_error == [0.5, 0, 0, 0, 0, 0, 0, 0.5]


def test_per_position_length_is_max_reference_length():
    m = evaluate(["ACG", "ACGTAC"], ["ACG", "ACGTAG"], [["r"], ["r"]])
    assert len(m.per_position_error) == 6
    assert m.per_position_error[5] == 0.5


def test_all_none():
    m = evaluate(["ACGT"], [None], [[]])
    assert m.strand_accuracy == 0.0
    assert math.isnan(m.mean_edit_distance)
    assert m.dropout_rate == 1.0


def test_length_mismatch_rejected():
    with pytest.raises(ValueError):
        evaluate(["ACGT"], ["ACGT", "ACGT"], [["ACGT"]])


def test_density_and_costs():
    settings = EncoderSettings(strand_length=100)
    # 10 strands of 100 bases = 1000 bases, 250 bytes = 2000 bits -> 2.0 bits per base
    meta = FileMeta(settings=settings, n_bytes=250, n_chunks=10)
    refs = ["A" * 100] * 10
    clusters = [["A" * 100] * 4] * 10  # 4 reads per strand
    profile = dataclasses.replace(
        load_profile("nanopore_budget"),
        synthesis_usd_per_base=0.001,
        sequencing_usd_per_read=0.01,
    )
    m = evaluate(refs, list(refs), clusters, profile=profile, meta=meta, file_recovered=True)
    assert m.bits_per_base == pytest.approx(2.0)
    bases_per_mb = 8e6 / 2.0  # 4e6 bases
    assert m.write_cost_usd_per_mb == pytest.approx(bases_per_mb * 0.001)  # 4000 USD
    strands_per_mb = bases_per_mb / 100  # 40,000 strands
    assert m.read_cost_usd_per_mb == pytest.approx(strands_per_mb * 4 * 0.01)  # 1600 USD
    assert m.file_recovered is True

    # meta without profile: density but no costs
    m2 = evaluate(refs, list(refs), clusters, meta=meta)
    assert m2.bits_per_base == pytest.approx(2.0)
    assert m2.write_cost_usd_per_mb is None
