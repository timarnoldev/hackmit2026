# What an Encoder Should Avoid Depends on the Class of Decoder

**Exposé, second version.** The first ([`EXPOSE.md`](EXPOSE.md)) claimed the learned candidate
scorer as the contribution; a literature check found that mechanism already built. This version
claims the interaction instead. Every number is registered in
[`docs/NUMBERS.md`](../docs/NUMBERS.md) and reproducible from `results/`.

---

## 1. The claim

DNA storage encodes under sequence rules — no homopolymer longer than three, GC between 40 and 60
percent — written as constants and copied between papers, as if they were properties of the
chemistry.

They are not. **They are properties of the chemistry and of the decoder**, and not merely of how
good the decoder is, but of what kind it is:

- Under **algebraic error correction**, a strand is risky in proportion to *how many* errors it
  accumulates. Every wrong symbol costs the same.
- Under **learned reconstruction from multiple reads**, a strand is risky in proportion to how
  many of its errors are *shared across reads*. An error that one read makes is repaired by the
  others for free; an error that every read makes is unrecoverable at any coverage.

These are different quantities, so they imply different encoders. The field writes one rule set
for both.

## 2. The prediction this makes, and the measurement that confirms it

If the claim holds, a risk model trained against a reconstruction decoder should care about
**deletion contexts far more than substitution contexts of comparable error rate** — because
voting across reads repairs a substitution, while a deletion shifts every base after it. Under
algebraic correction the two would score alike.

That is exactly what our model learned, without being told:

| Context, from our real-read channel table | Its measured error multiplier | The model's risk |
|---|---|---|
| GAAAG, deletions | 71x | **0.934** |
| AAAAG, deletions | 13x | **0.973** |
| CCCGA, substitutions | 12.8x | 0.444 |
| TCGGG, substitutions | 8.2x | 0.465 |

Background for a random strand is 0.526. The model is alarmed by the deletion contexts and
indifferent to the substitution contexts, although both have strongly elevated error rates. It
was trained on nothing but decoder failures. **It did not learn where errors happen. It learned
where errors survive voting.**

It also rates G and C runs above A and T runs of equal length (GGGGG 0.707 against AAAAA 0.608), a
distinction no rule that counts identical letters can express.

## 3. Where the existing work sits

There is an unpublished implementation that predicts per-sequence failure from a frozen decoder's
simulated failures and reallocates parity accordingly
([DNA-Storage-Reliability](https://github.com/aakibinesar/DNA-Storage-Reliability), accessed
September 2026). Its labelling scheme is essentially ours: the fraction of ~30 simulated runs in
which decoding fails. Its `src/` contains `consensus_voter.py` and a Reed-Solomon decoder, and
**no learned reconstruction step**.

So it is not a competitor to this claim. It is one end of the axis: consensus vote plus algebraic
correction, the configuration this exposé predicts should produce a *different* notion of risk.
Our weakest decoder is close to theirs, and our strongest is two points off a published
transformer. We have the span; nobody has measured along it.

## 4. The second result we already hold

The value of learned candidate selection shrinks as the decoder gets stronger. Same codec, only
the scorer differs, paired held-out trials, strand failure rate at six reads on Nanopore:

| Decoder | Simulator A | Simulator B (firewall) |
|---|---|---|
| Classic majority vote | −3.95 pts (CI −4.18 to −3.72) | −4.16 pts (CI −4.37 to −3.96) |
| Learned polisher | −2.76 pts (CI −2.97 to −2.55) | −2.85 pts (CI −3.02 to −2.68) |

Two points on a curve nobody has drawn. A stronger decoder repairs more of what the encoder was
steering around, so the encoder should steer less, and differently.

## 5. And the unit error that comes with it

The natural tool for setting a constraint is a per-strand risk score. It is the wrong unit. Our
model calls a run of four nearly free per strand, 0.436 against 0.460. Measured end to end at 300
held-out trials per point:

| Longest run allowed | 12 reads | 14 reads | 16 reads |
|---|---|---|---|
| **3, the field's rule** | **0.807** | **0.993** | 1.000 |
| 4 | 0.327 | 0.990 | 1.000 |
| 5, where the model draws it | 0.030 | 0.940 | 1.000 |
| no limit | 0.000 | 0.287 | 0.990 |

A file is 1,239 strands and needs essentially all of them, so 0.024 per strand compounds into 48
points of file recovery. A per-sequence score is right for a per-sequence decision, such as how
much parity a strand gets. It is wrong for a global constraint, and we made that error ourselves
before the measurement caught us.

## 6. The experiment

One table: **the optimal constraint and the value of learned selection, as a function of decoder
class and strength.**

| Decoder arm | Status |
|---|---|
| Consensus vote + algebraic correction (their configuration) | **to build** — our codec is Fountain with a per-strand CRC, so an RS arm is real work, not a flag |
| Consensus vote alone | have it |
| Learned polisher, one draft | have it |
| Learned polisher, three drafts and two rounds | have it |
| Published transformer decoder | have it, running on our split |

For each arm, sweep the homopolymer limit over 3, 4, 5, 6 and none, measure recovery curves at 300
held-out trials per point, and measure what learned candidate selection is still worth. Widen from
two channels to four or six while the machine is warm.

**No wet lab.** This is GPU time. The honest cost is the algebraic arm, which has to be written.

## 7. Scope and what is explicitly not claimed

Workshop or methods track at an ML-for-science venue, or a methods note in the DNA storage
community.

**The polisher is not a contribution.** A CNN over alignment statistics predicting a corrected
consensus is the Medaka and DeepConsensus pattern. Here it is the instrument that makes the
phenomenon visible: without a learned reconstruction decoder there is no second point on the
curve and no shared-error regime to observe. Its only remarkable property is efficiency — 0.8M
parameters, two points behind a 38.5M published model at six reads, about 100x faster, small
enough to run on a microcontroller — and that belongs in a table, not an abstract.

Also not claimed: any result about physical DNA. Everything is simulated channels calibrated on
real published reads, plus real published reads for the decoder benchmarks. The risk model's
ranking transfers to real reads (AUC 0.69 with coverage held fixed on 1,996 held-out clusters,
0.61 with the longest run held fixed too, where the hand rule drops to chance). We have **not**
shown that selecting candidates by it lowers failures on synthesised DNA.

## 8. The honest risk

**The constraint may not move.** Three may win under every decoder class, in which case the result
is "the field's rule of thumb is more robust than anyone checked" — publishable, and dull.

That is the argument for this pivot over a wet-lab route: the decoder sweep falsifies it in about
a day of compute, before a word is written. Build the algebraic arm, run the sweep, and read the
table before committing to anything.
