"""Tests for the majority vote baseline decoder."""

import numpy as np

from dnacodec.baseline import MAX_READS, MajorityVoteDecoder, reconstruct, subsample
from dnacodec.evaluate import evaluate
from dnacodec.seeds import train_seed
from dnacodec.types import ALPHABET


def _random_strand(rng, n):
    return "".join(rng.choice(list(ALPHABET), size=n))


def _noisy(rng, ref, p_sub, p_ins, p_del):
    out = []
    for b in ref:
        if rng.random() < p_ins:
            out.append(str(rng.choice(list(ALPHABET))))
        r = rng.random()
        if r < p_del:
            continue
        if r < p_del + p_sub:
            out.append(str(rng.choice([c for c in ALPHABET if c != b])))
        else:
            out.append(b)
    return "".join(out)


def test_empty_cluster_gives_none():
    assert MajorityVoteDecoder().decode([[], ["ACGT"]], 4) == [None, "ACGT"]


def test_identical_reads():
    ref = "ACGTTGCA" * 10
    assert MajorityVoteDecoder().decode([[ref] * 5], len(ref)) == [ref]


def test_single_read_forced_to_length():
    dec = MajorityVoteDecoder()
    assert dec.decode([["ACGTAC"]], 4)[0] is not None
    assert len(dec.decode([["ACGTAC"]], 4)[0]) == 4
    assert len(dec.decode([["AC"]], 5)[0]) == 5
    assert len(dec.decode([[""]], 3)[0]) == 3
    assert dec.decode([["ACGT"]], 4) == ["ACGT"]


def test_minority_indels_are_outvoted():
    rng = np.random.default_rng(train_seed(1))
    ref = _random_strand(rng, 110)
    del50 = ref[:50] + ref[51:]
    ins20 = ref[:20] + ("A" if ref[20] != "A" else "C") + ref[20:]
    sub80 = ref[:80] + ("G" if ref[80] != "G" else "T") + ref[81:]
    reads = [del50, ins20, ref, sub80, del50, ins20, sub80]
    assert reconstruct(reads, 110) == ref


def test_every_read_noisy_but_consensus_exact():
    """No read equals the reference, the errors are at different places."""
    rng = np.random.default_rng(train_seed(2))
    ref = _random_strand(rng, 110)
    reads = []
    for i in range(6):
        p = 10 + 15 * i
        variants = [
            ref[:p] + ref[p + 1 :],  # deletion
            ref[:p] + "T" + ref[p:],  # insertion
            ref[:p] + ("A" if ref[p] != "A" else "C") + ref[p + 1 :],  # substitution
        ]
        reads.append(variants[i % 3])
    assert all(r != ref for r in reads)
    assert reconstruct(reads, 110) == ref


def test_high_accuracy_on_moderate_noise():
    rng = np.random.default_rng(train_seed(3))
    refs = [_random_strand(rng, 110) for _ in range(60)]
    clusters = [[_noisy(rng, r, 0.02, 0.015, 0.02) for _ in range(10)] for r in refs]
    decoded = MajorityVoteDecoder().decode(clusters, 110)
    assert all(len(d) == 110 for d in decoded)
    m = evaluate(refs, decoded, clusters)
    assert m.strand_accuracy >= 0.9, m.strand_accuracy


def test_homopolymers():
    ref = "AAAACCCGGGGTTTACGTAAAAGGGCCCCTTTT" * 3
    rng = np.random.default_rng(train_seed(4))
    reads = [_noisy(rng, ref, 0.01, 0.01, 0.01) for _ in range(12)]
    out = reconstruct(reads, len(ref))
    assert len(out) == len(ref)
    assert out == ref


def test_subsample_is_deterministic_and_capped():
    reads = [f"R{i}" for i in range(100)]
    a = subsample(reads)
    assert a == subsample(reads)
    assert len(a) == MAX_READS
    assert len(set(a)) == MAX_READS
    assert a[0] == "R0" and a[-1] == "R99"
    assert subsample(reads[:5]) == reads[:5]


def test_large_cluster_decodes():
    rng = np.random.default_rng(train_seed(5))
    ref = _random_strand(rng, 140)
    reads = [_noisy(rng, ref, 0.02, 0.01, 0.01) for _ in range(80)]
    out = MajorityVoteDecoder().decode([reads], 140)
    assert out == [ref]
    assert out == MajorityVoteDecoder().decode([reads], 140)  # deterministic
