# Your Homopolymer Limit Depends on Your Decoder

**Exposé, second version.** The first version ([`EXPOSE.md`](EXPOSE.md)) claimed the learned
candidate scorer as the contribution. A literature check found that mechanism already exists, so
this version moves the claim up a level. Every number is registered in
[`docs/NUMBERS.md`](../docs/NUMBERS.md) and reproducible from `results/`.

---

## 1. The claim

DNA storage encodes under sequence rules — no homopolymer longer than three, GC content between
40 and 60 percent — written as constants and copied between papers. They are treated as properties
of the chemistry.

**They are properties of the chemistry *and of the decoder*.** The right constraint depends on
what reconstructs the strand afterwards, and nobody measures how it moves when that changes. We
show it moves, and we show that the natural tool for setting it — a per-strand risk model — is
systematically the wrong unit.

## 2. Why this is not the prior work

There is existing work that predicts sequence-level failure from a frozen decoder's simulated
failures and reallocates parity accordingly (see
[DNA-Storage-Reliability](https://github.com/aakibinesar/DNA-Storage-Reliability), whose labelling
scheme — the fraction of ~30 simulated runs in which decoding fails — is essentially ours). There
is also work that learns a DNA encoder with biological constraints folded into the loss
([DJSCC-DNA](https://ieeexplore.ieee.org/document/10314138/)).

Both hold the decoder fixed and neither asks what the constraint should be. This exposé starts
where they stop: **the constraint is the dependent variable, and the decoder is an input.**

## 3. The two results we already have

### 3.1 The gain from learned selection shrinks as the decoder improves

Same codec, only the scorer ranking candidates differs, paired held-out trials, strand failure
rate at six reads on Nanopore:

| Decoder | Simulator A | Simulator B (firewall) |
|---|---|---|
| Classic majority vote | −3.95 pts (CI −4.18 to −3.72) | −4.16 pts (CI −4.37 to −3.96) |
| Learned polisher | −2.76 pts (CI −2.97 to −2.55) | −2.85 pts (CI −3.02 to −2.68) |

A better decoder repairs more of what the encoder was steering around. **The encoder's optimal
behaviour is a function of the decoder**, and this is the first point on that curve.

### 3.2 Per-strand risk is the wrong unit for setting a constraint

Our risk model, probed after training, says a run of four is nearly free: 0.436 against 0.460 for
a run of three. Measured end to end at 300 held-out trials per point, recovery rate against mean
reads per strand:

| Longest run allowed | 12 reads | 14 reads | 16 reads |
|---|---|---|---|
| **3, the field's rule** | **0.807** | **0.993** | 1.000 |
| 4 | 0.327 | 0.990 | 1.000 |
| 5, where the model draws it | 0.030 | 0.940 | 1.000 |
| 6 | 0.003 | 0.587 | 0.997 |
| no limit | 0.000 | 0.287 | 0.990 |

Monotone, and the field's three wins. A file is 1,239 strands and needs essentially all of them,
so a per-strand difference of 0.024 compounds into 48 points of file recovery.

**This is a direct caution for the prior work.** A per-sequence failure score is the right tool
for a per-sequence decision, such as how much parity that strand gets. Using it to set a global
constraint is a unit error, and we made it ourselves before the measurement caught us.

## 4. What is missing, and it is only compute

| | Status |
|---|---|
| Decoder dependence, 2 decoders | have it |
| Per-strand versus file level | have it, monotone |
| Rules disagree across channels | have it, but n = 2 |
| Decoder dependence across 4–5 decoders | **missing** |
| Channels widened to 4–6 | **missing** |

Both gaps are GPU time, **not a wet lab**. The decoder sweep is cheap because every decoder
already exists in the repository: majority vote, the polisher at one draft, the polisher at three
drafts and two rounds, TReconLM pretrained, TReconLM fine-tuned. For each, measure where the
homopolymer limit belongs and what learned candidate selection is still worth.

The experiment is one table: **optimal constraint as a function of decoder strength.**

## 5. Supporting material, already measured

- **The model learned where errors are fatal, not where they happen.** It rates deletion contexts
  far above substitution contexts of comparable error rate (GAAAG 0.934 against CCCGA 0.444), and
  G/C runs above A/T runs of equal length. Voting across reads repairs a substitution; a deletion
  shifts everything after it. No rule that counts identical letters can express either.
- **The ranking transfers to real DNA.** AUC 0.69 on 1,996 held-out real Nanopore clusters with
  coverage held fixed; 0.61 with the longest run held fixed as well, where the hand rule itself
  drops to 0.50.
- **A benchmark caution worth its own paragraph.** 1,585 of our 2,000 held-out clusters (79.2%)
  lie inside the published TReconLM checkpoint's own fine-tuning split. Anyone benchmarking a
  decoder against that checkpoint on the Microsoft dataset must check the overlap first.
- **A metric caution.** The all-or-nothing recovery threshold is knife-edge near the top of the
  curve and inverted one of our own rankings on a single trial in 300. Close codecs must be
  compared on curves.

## 6. Scope

Workshop or methods track at an ML-for-science venue, or a methods note in the DNA storage
community. The decoder we built is explicitly **not** a contribution: a CNN over alignment
statistics predicting a corrected consensus is the Medaka and DeepConsensus pattern, and it is
used here as a fixed instrument so both sides of every comparison share it.

## 7. The honest risk

**The limit may not move.** Three may win under every decoder, in which case the result is "the
field's rule of thumb is more robust than anyone checked", which is publishable and dull.

That is the reason to prefer this pivot over the wet-lab route: **it is falsifiable in about a day
of compute, before a word is written.** Run the decoder sweep first. If the optimum moves, there
is a paper. If it does not, we have saved ourselves months.
