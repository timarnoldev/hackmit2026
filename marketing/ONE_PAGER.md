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

Tuning runs are starting now. Placeholders are marked and will be filled from held-out trials only.

| Question | Answer |
|---|---|
| Which hand rules pay off on Nanopore and on Illumina? | [RESULT: rule audit verdicts per channel] |
| Reads per strand to hit the target on Nanopore, default vs tuned, at matched density | [RESULT: default N, tuned M] |
| Bits per base at the Illumina read budget, default vs tuned | [RESULT: default vs tuned bits per base] |
| Does learned selection add anything on top of the audit? (C to D) | [RESULT: reads per strand, C vs D] |
| Does it survive Simulator B and real reads? | [RESULT: Simulator B outcome, real-read risk AUC] |

If the audit finds that the hand rules are already near optimal on a channel, that is the answer, and now it's measured instead of assumed.

## What's under the hood

- **Channel simulator** calibrated on real Nanopore and Illumina reads, including a per 5-letter context error table
- **Fountain (LT) encoder** with a CRC-16 per strand, so a wrong strand becomes a missing one
- **Transformer decoder** reconstructing a strand from up to 16 noisy reads, with a majority vote baseline always reported next to it (baseline: 90.6% exact strands at 16 reads on real held-out data)
- **1D CNN risk model** trained on failure rates over 32 simulations per strand
- **Streamlit dashboard:** rule audit, Pareto plot, crossover matrix, ablation ladder
- About 11,000 lines of Python, about 2,500 of them tests, running on an ASUS Ascent GX10 (NVIDIA GB10)

## What we don't claim

A new encoder (ours is a standard Fountain code on purpose), a decoder that beats DNAformer, or any wet lab work. Our opponent is the hand-tuned default, with the same decoder.

---

**Code:** github.com/timarnoldev/hackmit2026 | **Team:** [TEAM: names and contact]
