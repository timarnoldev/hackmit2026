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


# ---- recovery_trials and min_reads_at_target ------------------------------------------

from dnacodec.baseline import MajorityVoteDecoder  # noqa: E402
from dnacodec.encoder import encode  # noqa: E402
from dnacodec.encoder import payload_bits_per_base as _bpb  # noqa: E402
from dnacodec.evaluate import _TrialRunner, min_reads_at_target, recovery_trials  # noqa: E402
from dnacodec.seeds import train_seed  # noqa: E402

DATA = bytes(range(256)) * 4  # 1 KB, about 60 strands: keeps trials fast
SETTINGS = EncoderSettings()


class FirstReadDecoder:
    """Takes the first read as is. Exact on a noise-free channel. Module level: picklable."""

    name = "first_read"

    def decode(self, clusters, strand_length):
        return [c[0] if c else None for c in clusters]


class NoneDecoder:
    name = "none"

    def decode(self, clusters, strand_length):
        return [None] * len(clusters)


def _clean(coverage=30.0):
    """Noise-free channel; strands are lost only when they get zero reads."""
    return dataclasses.replace(
        load_profile("illumina_standard"),
        sub_rate=0.0,
        ins_rate=0.0,
        del_rate=0.0,
        dropout_rate=0.0,
        decay_per_year=0.0,
        gc_dropout_factor=0.0,
        coverage_mean=coverage,
        coverage_dispersion=1000.0,  # nearly Poisson
    )


def _seeds(n, start=100):
    return [train_seed(start + i) for i in range(n)]


def test_recovery_clean_channel():
    encoded = encode(DATA, SETTINGS)
    m = recovery_trials(DATA, SETTINGS, None, FirstReadDecoder(), _clean(), _seeds(4), workers=1)
    assert m.recovery_rate == 1.0
    assert m.file_recovered is True
    assert m.n_trials == 4
    assert m.n_strands == len(encoded.strands)
    assert m.extra["n_strand_decodes"] == 4 * len(encoded.strands)
    assert m.strand_accuracy == pytest.approx(1.0 - m.dropout_rate)
    assert m.bits_per_base == pytest.approx(_bpb(encoded.meta, len(encoded.strands)))
    assert m.write_cost_usd_per_mb is not None and m.read_cost_usd_per_mb is not None
    assert m.reads_per_strand == pytest.approx(30, rel=0.1)
    assert "early_exit" not in m.extra


def test_recovery_hopeless_decoder():
    m = recovery_trials(DATA, SETTINGS, None, NoneDecoder(), _clean(), _seeds(3), workers=1)
    assert m.recovery_rate == 0.0
    assert m.file_recovered is False
    assert m.n_trials == 3
    assert m.strand_accuracy == 0.0


def test_recovery_parallel_equals_serial():
    profile = load_profile("nanopore_budget")  # real noise, mixed outcomes per strand
    args = (DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(6))
    serial = recovery_trials(*args, workers=1)
    parallel = recovery_trials(*args, workers=3)
    assert dataclasses.asdict(serial) == dataclasses.asdict(parallel)
    assert serial.n_trials == 6
    assert 0.0 < serial.strand_accuracy < 1.0


def test_recovery_is_deterministic_and_seed_dependent():
    profile = load_profile("nanopore_budget")
    a = recovery_trials(DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(2), workers=1)
    b = recovery_trials(DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(2), workers=1)
    c = recovery_trials(
        DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(2, start=500), workers=1
    )
    assert dataclasses.asdict(a) == dataclasses.asdict(b)
    assert a.per_position_error != c.per_position_error


def test_early_exit_is_reported_honestly():
    encoded = encode(DATA, SETTINGS)
    for workers in (1, 2):
        with _TrialRunner(encoded, DATA, NoneDecoder(), workers, 5) as runner:
            m = runner.run(_clean(), _seeds(5), max_failures=1)
        assert m.n_trials == 2  # second failure makes "at most 1 failure" unreachable
        assert m.recovery_rate == 0.0
        assert m.extra["early_exit"] == 1.0
        assert m.extra["n_trials_requested"] == 5.0
        assert m.file_recovered is False


def test_min_reads_at_target_clean_channel():
    grid = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    seeds = _seeds(8)
    c = min_reads_at_target(
        DATA, SETTINGS, None, FirstReadDecoder(), _clean(), seeds, coverages=grid, workers=1
    )
    assert c is not None and c in grid
    at_c = recovery_trials(
        DATA, SETTINGS, None, FirstReadDecoder(), _clean(c), seeds, workers=1
    )
    assert at_c.recovery_rate == 1.0
    below = grid[grid.index(c) - 1]
    assert grid.index(c) > 0, "grid starts too high to test the step below"
    at_below = recovery_trials(
        DATA, SETTINGS, None, FirstReadDecoder(), _clean(below), seeds, workers=1
    )
    assert at_below.recovery_rate < 1.0
    # a looser target can only need the same coverage or less
    loose = min_reads_at_target(
        DATA, SETTINGS, None, FirstReadDecoder(), _clean(), seeds, target=0.5, coverages=grid,
        workers=1,
    )
    assert loose is not None and loose <= c
    # grid order does not matter
    assert c == min_reads_at_target(
        DATA, SETTINGS, None, FirstReadDecoder(), _clean(), seeds, coverages=grid[::-1], workers=1
    )


def test_min_reads_at_target_none_when_unreachable():
    c = min_reads_at_target(
        DATA, SETTINGS, None, NoneDecoder(), _clean(), _seeds(4), coverages=(2, 4), workers=1
    )
    assert c is None


def test_min_reads_parallel_equals_serial():
    # real noise; low grid points fail (early exit), a high one passes
    profile = load_profile("nanopore_budget")
    settings = dataclasses.replace(SETTINGS, redundancy=1.0)
    args = (DATA, settings, None, MajorityVoteDecoder(), profile, _seeds(6))
    kw = dict(coverages=(3, 6, 10, 16, 24))
    serial = min_reads_at_target(*args, workers=1, **kw)
    assert serial is not None and serial > 3
    assert serial == min_reads_at_target(*args, workers=3, **kw)


class GatherBaseline(MajorityVoteDecoder):
    """The baseline flagged as a GPU-style decoder: must be decoded in the main process,
    all trials in one call. Deterministic per cluster, so results must equal the
    per-trial path. Refuses to be pickled, which proves it never reaches a worker."""

    name = "baseline_gather"
    main_process_only = True

    def __init__(self):
        super().__init__()
        self.calls = []

    def decode(self, clusters, strand_length):
        self.calls.append(len(clusters))
        return super().decode(clusters, strand_length)

    def __reduce__(self):
        raise TypeError("main_process_only decoder must not be pickled")


def test_main_process_only_matches_per_trial_path():
    profile = load_profile("nanopore_budget")
    seeds = _seeds(5)
    reference = recovery_trials(DATA, SETTINGS, None, MajorityVoteDecoder(), profile, seeds, workers=1)
    n = reference.n_strands
    # default chunk (50 >= 5 trials: one call), and chunk sizes that split unevenly
    for workers, chunk, expected_calls in [
        (1, None, [5 * n]),
        (3, None, [5 * n]),
        (1, 1, [n] * 5),
        (3, 2, [2 * n, 2 * n, n]),
        (2, 5, [5 * n]),
    ]:
        dec = GatherBaseline()
        kw = {} if chunk is None else {"gather_chunk_trials": chunk}
        m = recovery_trials(DATA, SETTINGS, None, dec, profile, seeds, workers=workers, **kw)
        assert dec.calls == expected_calls, (workers, chunk)
        assert dataclasses.asdict(m) == dataclasses.asdict(reference), (workers, chunk)


def test_gather_chunk_must_be_positive():
    with pytest.raises(ValueError):
        recovery_trials(
            DATA, SETTINGS, None, GatherBaseline(), _clean(), _seeds(2), workers=1,
            gather_chunk_trials=0,
        )


def test_main_process_only_early_exit_matches_and_saves_decoding():
    encoded = encode(DATA, SETTINGS)
    n = len(encoded.strands)
    profile = load_profile("nanopore_budget")  # every trial fails at this size and coverage
    seeds = _seeds(5)
    with _TrialRunner(encoded, DATA, MajorityVoteDecoder(), 1, 5) as runner:
        reference = runner.run(profile, seeds, max_failures=1)
    assert reference.extra["early_exit"] == 1.0 and reference.n_trials == 2
    # the second failure ends it: later chunks are never decoded
    for workers, chunk, expected_calls in [
        (1, 50, [5 * n]),
        (2, 50, [5 * n]),
        (1, 1, [n, n]),
        (2, 1, [n, n]),
        (2, 2, [2 * n]),
        (1, 3, [3 * n]),
    ]:
        dec = GatherBaseline()
        with _TrialRunner(encoded, DATA, dec, workers, 5, chunk) as runner:
            m = runner.run(profile, seeds, max_failures=1)
        assert dec.calls == expected_calls, (workers, chunk)
        assert dataclasses.asdict(m) == dataclasses.asdict(reference), (workers, chunk)


def test_main_process_only_min_reads_matches():
    profile = load_profile("nanopore_budget")
    settings = dataclasses.replace(SETTINGS, redundancy=1.0)
    kw = dict(coverages=(3, 6, 10, 16, 24))
    seeds = _seeds(6)
    expected = min_reads_at_target(
        DATA, settings, None, MajorityVoteDecoder(), profile, seeds, workers=1, **kw
    )
    assert expected is not None
    dec = GatherBaseline()
    got = min_reads_at_target(DATA, settings, None, dec, profile, seeds, workers=2, **kw)
    assert got == expected
    assert len(dec.calls) == kw["coverages"].index(got) + 1  # one decode per coverage tried
    for chunk in (1, 4):
        dec = GatherBaseline()
        got = min_reads_at_target(
            DATA, settings, None, dec, profile, seeds, workers=2, gather_chunk_trials=chunk, **kw
        )
        assert got == expected, chunk
        # failing coverages stop after their first failing chunk: fewer trials decoded
        # than running every trial at every coverage tried
        n = len(encode(DATA, settings).strands)
        tried = kw["coverages"].index(got) + 1
        assert sum(dec.calls) // n < len(seeds) * tried, chunk


# ---- simulator= and encoded= keyword options -------------------------------------------

from dnacodec import simulator as simulator_a  # noqa: E402
from dnacodec import simulator_b  # noqa: E402


def test_default_simulator_is_simulator_a():
    profile = load_profile("nanopore_budget")
    args = (DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(3))
    default = recovery_trials(*args, workers=1)
    explicit = recovery_trials(*args, workers=2, simulator=simulator_a.simulate)
    assert dataclasses.asdict(default) == dataclasses.asdict(explicit)


def test_simulator_b_changes_trials_and_stays_deterministic():
    profile = load_profile("nanopore_budget")
    seeds = _seeds(4)
    args = (DATA, SETTINGS, None, MajorityVoteDecoder(), profile, seeds)
    a = recovery_trials(*args, workers=1)
    b_serial = recovery_trials(*args, workers=1, simulator=simulator_b.simulate)
    assert b_serial.strand_accuracy != a.strand_accuracy
    assert b_serial.per_position_error != a.per_position_error
    # parallel workers and the gather path use simulator B too
    b_parallel = recovery_trials(*args, workers=2, simulator=simulator_b.simulate)
    assert dataclasses.asdict(b_parallel) == dataclasses.asdict(b_serial)
    for workers in (1, 2):
        dec = GatherBaseline()
        b_gather = recovery_trials(
            DATA, SETTINGS, None, dec, profile, seeds, workers=workers,
            simulator=simulator_b.simulate, gather_chunk_trials=3,
        )
        assert dataclasses.asdict(b_gather) == dataclasses.asdict(b_serial)


def test_min_reads_with_simulator_b_parallel_equals_serial():
    profile = load_profile("nanopore_budget")
    settings = dataclasses.replace(SETTINGS, redundancy=1.0)
    args = (DATA, settings, None, MajorityVoteDecoder(), profile, _seeds(4))
    kw = dict(coverages=(3, 6, 10, 16, 24, 32), simulator=simulator_b.simulate)
    assert min_reads_at_target(*args, workers=1, **kw) == min_reads_at_target(
        *args, workers=2, **kw
    )


def test_pre_encoded_file_gives_identical_metrics():
    profile = load_profile("nanopore_budget")
    encoded = encode(DATA, SETTINGS)
    args = (DATA, SETTINGS, None, MajorityVoteDecoder(), profile, _seeds(3))
    inside = recovery_trials(*args, workers=1)
    outside = recovery_trials(*args, workers=2, encoded=encoded)
    assert dataclasses.asdict(inside) == dataclasses.asdict(outside)

    settings = dataclasses.replace(SETTINGS, redundancy=1.0)
    encoded_r1 = encode(DATA, settings)
    kw = dict(coverages=(3, 6, 10, 16, 24))
    margs = (DATA, settings, None, MajorityVoteDecoder(), profile, _seeds(4))
    expected = min_reads_at_target(*margs, workers=1, **kw)
    assert expected is not None
    assert expected == min_reads_at_target(*margs, workers=2, encoded=encoded_r1, **kw)


def test_pre_encoded_file_must_match():
    encoded = encode(DATA, SETTINGS)
    other = dataclasses.replace(SETTINGS, redundancy=0.5)
    with pytest.raises(ValueError):
        recovery_trials(
            DATA, other, None, FirstReadDecoder(), _clean(), _seeds(1), workers=1, encoded=encoded
        )
    with pytest.raises(ValueError):
        min_reads_at_target(
            DATA, other, None, FirstReadDecoder(), _clean(), _seeds(1), workers=1, encoded=encoded
        )
    with pytest.raises(ValueError):  # a different file
        recovery_trials(
            DATA[:500], SETTINGS, None, FirstReadDecoder(), _clean(), _seeds(1), workers=1,
            encoded=encoded,
        )


# ---- per_trial= ---------------------------------------------------------------------

def test_per_trial_aggregates_to_pooled_and_is_path_independent():
    profile = dataclasses.replace(load_profile("nanopore_budget"), coverage_mean=7.0)
    settings = dataclasses.replace(SETTINGS, redundancy=1.0)
    seeds = _seeds(6)
    args = (DATA, settings, None, MajorityVoteDecoder(), profile, seeds)
    plain = recovery_trials(*args, workers=1)
    m = recovery_trials(*args, workers=1, per_trial=True)

    acc, rec = m.extra["trial_strand_accuracy"], m.extra["trial_recovered"]
    assert len(acc) == len(rec) == len(seeds)
    assert set(rec) <= {0.0, 1.0}
    assert 0.0 < m.recovery_rate < 1.0, "want mixed outcomes for a meaningful test"
    # aggregates exactly (in counts) to the pooled metrics
    n = m.n_strands
    assert sum(round(a * n) for a in acc) == round(m.strand_accuracy * n * len(seeds))
    assert sum(rec) / len(seeds) == m.recovery_rate
    assert sum(acc) / len(acc) == pytest.approx(m.strand_accuracy, abs=1e-12)
    # nothing else changes
    without = dict(dataclasses.asdict(m))
    extra = dict(without.pop("extra"))
    del extra["trial_strand_accuracy"], extra["trial_recovered"]
    plain_d = dataclasses.asdict(plain)
    assert extra == plain_d.pop("extra")
    assert without == plain_d

    # identical on the parallel and the chunked gather paths
    parallel = recovery_trials(*args, workers=3, per_trial=True)
    assert dataclasses.asdict(parallel) == dataclasses.asdict(m)
    for workers, chunk in ((1, 4), (2, 1)):
        dec = GatherBaseline()
        g = recovery_trials(
            DATA, settings, None, dec, profile, seeds, workers=workers, per_trial=True,
            gather_chunk_trials=chunk,
        )
        assert dataclasses.asdict(g) == dataclasses.asdict(m), (workers, chunk)


def test_per_trial_disables_early_exit():
    encoded = encode(DATA, SETTINGS)
    profile = load_profile("nanopore_budget")  # every trial fails here
    with _TrialRunner(encoded, DATA, MajorityVoteDecoder(), 1, 5) as runner:
        m = runner.run(profile, _seeds(5), max_failures=0, per_trial=True)
    assert m.n_trials == 5 and "early_exit" not in m.extra
    assert m.extra["trial_recovered"] == [0.0] * 5
