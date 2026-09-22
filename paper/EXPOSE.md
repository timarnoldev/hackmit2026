# Learning What to Avoid: Decoder Failures as a Training Signal for DNA Storage Encoders

**Exposé.** Written 2026-09-22, after HackMIT 2026. Every number here is registered with its
measurement in [`docs/NUMBERS.md`](../docs/NUMBERS.md) and reproducible from `results/`.

---

## 1. The claim

DNA storage pipelines decide *what to write* using hand-written sequence rules — no homopolymer
longer than three, GC content between 40 and 60 percent — chosen once and copied between papers.
These rules are proxies for a question they never ask: **which candidate strand is this decoder
least likely to lose?**

We answer that question directly. A small model is trained on nothing but the failures of a frozen
decoder, and the encoder uses it to rank candidate strands. The claim is narrow and testable:
**at a fixed recovery target, ranking candidates by a learned failure model beats ranking them by
the standard rules, with every other part of the codec held identical.**

## 2. The core result

Same redundancy, same strand length, same hard rules, same 32 candidates per slot, same decoder,
same seeds, same 20 KB file. **Only the scorer that ranks the candidates differs.** 300 held-out
trials per point, simulated Nanopore channel calibrated on real reads.

| Mean reads per strand | 4.0 | **4.5** | 5.0 | 5.5 |
|---|---|---|---|---|
| Candidates ranked by the hand rules | 0.000 | **0.270** | 1.000 | 1.000 |
| Candidates ranked by the learned model | 0.100 | **0.983** | 0.997 | 1.000 |

At 4.5 reads per strand: **27% of files recovered against 98.3%**, a gap of 71 points at a
standard error of 2.7. Choosing among candidates costs no density, so this is free in bits per
base and worth roughly half a read per strand at the operating point.

Reproduce: `scripts/tier2_recovery_curve.py`, raw in
`results/tier2_recovery_curve_run2_strict_nanopore_budget.json`.

## 3. What supports it

**The same effect per strand, with confidence intervals, on two structurally different
simulators.** Strand failure rate at six reads, paired trials: 51.96% → 48.01% on the calibrated
simulator (−3.95, CI −4.18 to −3.72) and 50.92% → 46.76% on a simulator built from different
mechanisms and a context table fit on a different dataset, never used for any tuning (−4.16, CI
−4.37 to −3.96). Repeated with a stronger learned decoder in place, the gain shrinks as expected
but holds: −2.76 and −2.85.

**The model's ranking transfers to real DNA.** On 1,996 held-out real Nanopore clusters with
coverage held fixed, it ranks decoder failures at AUC 0.69. Holding the longest run fixed as well
— neutralising the hand rule's own feature — it still reaches 0.61 while the hand rule itself
drops to 0.50, chance.

**What it learned is interpretable and not what a rule encodes.** Probed after training, it rates
G and C runs above A and T runs of equal length, a distinction no standard rule makes. Asked about
the contexts our channel model says are worst, it is alarmed by deletion contexts and relaxed
about substitution contexts of comparable error rate. That is the correct call for a system that
votes across reads: voting repairs a substitution, a deletion shifts everything after it. **The
model did not learn where errors happen. It learned where errors are fatal** — precisely what a
rule that counts identical letters cannot express.

## 4. What is not yet shown

Three gaps, stated plainly because they define the work.

1. **No wet lab.** We show the ranking transfers to real reads. We have **not** shown that
   selecting candidates by it lowers failures on real synthesised DNA. This is the load-bearing
   gap and a reviewer will open here.
2. **Two channels.** The per-channel framing rests on n = 2. Two channels illustrate; they do not
   establish.
3. **Metric fragility.** An all-or-nothing recovery threshold is knife-edge near the top of the
   curve and can invert a ranking on one trial in 300. We found this the hard way and switched to
   recovery curves. A paper must handle it openly rather than let a reviewer find it.

## 5. Proposed work

- **Breadth instead of a wet lab, as the realistic path.** Four to six channels spanning Nanopore
  and Illumina error regimes, plus a second real dataset for the transfer measurement. The claim
  becomes "the learned scorer wins wherever the channel has sequence structure to exploit, and
  ties where it does not" — which is already what the Illumina result says, where the gain is zero
  because the channel is clean.
- **An ablation of the signal itself**: how many simulated decodes per strand the labels need, and
  whether the failure probability can be distilled from the decoder's own confidence instead. A
  preliminary run reaches AUC 0.83 on the firewall simulator that way, which would remove the most
  expensive step in the loop.
- **Sensitivity to the decoder.** The gain shrinks as the decoder improves. Characterising that
  curve is the honest boundary of the method.
- **Wet-lab validation** as the stretch goal, and the only thing that converts this from a method
  paper into a result about DNA.

## 6. Scope and venue

Workshop or methods track at an ML-for-science venue, or a methods note in the DNA storage
community. A preprint costs nothing and should go up early.

The decoder we built is **not** part of the contribution. A CNN over alignment statistics that
predicts a corrected consensus is an established pattern — Medaka and DeepConsensus are the same
idea — and it is used here as a fixed instrument so that both sides of every comparison share it.
Its only remarkable property is efficiency: 0.8M parameters, two points behind a 38.5M published
model at six reads, about 100x faster, small enough to run on a microcontroller. That is an
engineering result, not a research contribution, and it belongs in a table rather than an abstract.

## 7. Principal risk

That the effect is a property of our simulator rather than of DNA. The firewall simulator and the
real-read transfer measurement are designed against exactly that, and both survive — but neither
is synthesis. Until someone sequences strands chosen by this model, the honest framing is **a
method and strong simulated evidence**, not a demonstrated improvement to a physical pipeline.
