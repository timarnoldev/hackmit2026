<p><img src="logo.svg" alt="Erbgut" width="240" height="210"></p>

# Erbgut

**Measure the rule. Keep what pays.**

We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly. HackMIT 2026.

---

## The problem

DNA stores data at extreme density for centuries, but writing and reading it is noisy: letters get swapped, added or dropped, and whole strands go missing. Storage pipelines cope with hand-written rules:

- no run of the same letter longer than 3,
- GC content between 40 and 60%,
- a fixed amount of redundancy, often 0.3 (30% extra strands).

Every rule costs density or reads. The rules were chosen once, copied between papers, and applied to every sequencing channel, even though Nanopore and Illumina fail in very different ways. Nobody measures whether they pay off.

## What Erbgut does

*Erbgut is German for the genetic material an organism inherits and passes on.*

For one channel (sequencing technology, read budget, recovery target):

| | Tier 1: rule audit | Tier 2: learned selection |
|---|---|---|
| **Question** | Does each hand rule, and each extra strand of redundancy, actually reduce failures here? | What else should the encoder avoid on this channel? |
| **Method** | Switch each rule off, measure reads per strand and bits per base at a fixed recovery target, tune redundancy the same way | A small CNN learns from real decoder failures which strands are risky; the Fountain encoder keeps the safest of several candidates, at no cost in density |
| **Output** | A verdict per rule and channel: pays off, or doesn't | A risk model and a list of the patterns it learned to avoid |

Same encoder, same decoder throughout. Only the rules, the redundancy and the choice of candidate strands change.

## Why you can trust the numbers

- **Fixed objective, set before any run:** a fixed 20 KB file must come back exactly in all 300 held-out trials. Codecs are judged on the Pareto front of bits per base against reads per strand.
- **Calibrated simulator:** within about 3 points of real Nanopore reads at every read count, on held-out Microsoft data that no calibration step touched.
- **Something real to learn:** errors shared by all reads of a strand are 45 to 66% predictable from the local 5-letter context on real Nanopore reads (held-apart data, two independent datasets), with hot-spot AUC 0.80 to 0.90. The hand rules don't cover these contexts.
- **Evidence designed to catch us out:** ablation ladder A to E, crossover matrix (each tuned codec on its own and the other channel), and a sim-to-real firewall with a structurally different Simulator B plus checks on real held-out reads.

## Results

All from held-out trials. Provenance for every figure: `docs/NUMBERS.md`.

| Question | Answer |
|---|---|
| Does the "max 3 identical letters" rule pay off? | **Nanopore: yes.** 19.5 reads per strand with it, 24.5 without. **Illumina: no measurable benefit** (3.0 against 2.5) |
| Does the "GC 40 to 60%" rule pay off? | **Nanopore: no measurable benefit** (19.5 against 20.0). **Illumina: yes**, 3.0 reads with it against 4.0 without |
| What does tuning the redundancy buy? | Nanopore 19.5 reads down to 5.5. Illumina 1.20 up to 1.50 bits per base at the same target |
| Does learned selection add anything on top of the audit? | Yes. Holding every setting fixed and changing only which scorer ranks the 32 candidates: at 4.5 reads per strand, 27.0% of files recovered against 98.3%, a 71 point difference at a standard error of 2.7 |
| Does it survive Simulator B and real reads? | Yes. Per-strand failures drop 3.95 points on Simulator A and 4.16 on the firewall simulator. On real Nanopore reads at fixed coverage the risk model ranks failures at AUC 0.69, against 0.66 for the homopolymer rule alone |

Each of the two standard rules pays off on exactly one of the two channels and does nothing on the other. Nobody measures that today.

**What we have not shown:** that *selecting* candidates by the risk model reduces failures on real DNA. That needs strands synthesized and sequenced, and we have no wet lab.

## What's under the hood

- **Channel simulator** calibrated on real Nanopore and Illumina reads, including a per 5-letter context error table
- **Fountain (LT) encoder** with a CRC-16 per strand, so a wrong strand becomes a missing one
- **Learned decoder**: the classic decoder aligns and votes, a 0.8M-parameter CNN polishes the result. 88.1% exact strands at 6 reads on real held-out Nanopore data, against 67.2% for the classic method. The baseline is reported next to every model result
- **1D CNN risk model** trained on failure rates over 32 simulations per strand
- **Streamlit dashboard:** rule audit, Pareto plot, crossover matrix, ablation ladder
- About 11,000 lines of Python, about 2,500 of them tests, 260 tests green, running on an ASUS Ascent GX10 (NVIDIA GB10)

## What we don't claim

A new encoder (ours is a standard Fountain code on purpose), a state-of-the-art decoder, or any wet lab work. On raw accuracy we are level with a fine-tuned DNAformer and clearly behind TReconLM at low coverage; `docs/COMPARISON.md` has the full table. Our opponent is the hand-tuned default, with the same decoder on both sides.

---

**Code:** github.com/timarnoldev/hackmit2026 | **Team:** [TEAM: names and contact]
