# Devpost submission: StrandAudit

**Project name:** StrandAudit

**Tagline (Devpost "elevator pitch", max 200 characters):**
We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

**Thumbnail:** `logo-mark.svg` exported to PNG at 512 px, or a dashboard screenshot of the rule audit view.

> Before submitting: replace every `[RESULT: ...]` with a number from held-out trials, or delete the sentence. Search for `[RESULT` and `[TEAM` before you press submit.

---

## Inspiration

DNA can hold data at a density no hard drive comes close to, and it lasts for centuries. But it's a noisy medium. Writing it (synthesis) and reading it (sequencing) swap, add and drop letters, and entire strands go missing.

Reading the DNA storage literature, we kept seeing the same few rules: never more than three of the same letter in a row, keep GC content between 40 and 60%, add a fixed amount of redundancy. Every rule costs storage density or sequencing reads. Yet they are chosen once, copied from paper to paper, and applied to every sequencing channel, even though Nanopore and Illumina fail in completely different ways. Nanopore struggles with runs of identical letters. Illumina barely cares.

We wanted to answer a simple engineering question that no tool answers today: *given my sequencing technology and my read budget, which of these rules are actually worth paying for?*

## What it does

StrandAudit tunes a DNA storage codec for one channel (sequencing technology, read budget, recovery target), in two tiers.

**Tier 1, rule audit.** It switches each hand rule off, one at a time, and measures what it costs and what it buys. It tunes the redundancy the same way. Every verdict comes from recovery trials on held-out seeds: a fixed 20 KB file has to come back exactly in all 300 trials, and we count the reads per strand and bits per base it took. The encoder and decoder stay the same throughout.

**Tier 2, learned selection.** A Fountain encoder can write the same data as many different candidate strands at no cost in density. A small CNN risk model learns, from where our decoder actually fails on this channel, which candidates are risky, and the encoder keeps the safest one. This lets the codec avoid patterns the hand rules don't cover.

The result is shown in a Streamlit dashboard: the rule audit (a verdict per rule and channel), a Pareto plot of bits per base against reads per strand with the default codec and each tuning round, a crossover matrix, and an ablation ladder. The demo ends with an image encoded into DNA, read back through the Nanopore channel at 6 reads per strand, and decoded with both the default and the tuned codec.

What we found: [RESULT: one or two sentences on the rule audit verdicts per channel, and the reads per strand or bits per base the tuned codec needs vs the default at matched density]. If the audit shows the hand rules are already near optimal on a channel, we report that. It's a useful answer, and now it's a measured one.

## How we built it

**Channel simulator.** We can't run a wet lab at a hackathon, so the simulator plays the lab. It models substitutions, insertions, deletions, run-length effects, a position ramp, per-read quality, shared per-position errors, dropouts and uneven coverage. We calibrated it on real reads: the Microsoft clustered Nanopore reads dataset and the DNAformer Nanopore and Illumina reads from Technion. The acceptance test was that our baseline decoder must score about the same on simulated reads as on real ones. On the Microsoft held-out split, which no calibration step touched, it lands within about 3 points at every read count from 2 to 16, and where it's off it's slightly harder.

**Context errors from real reads.** We found that on real Nanopore reads, errors shared by all reads of a strand (the ones more reads can't average away) are 30 to 66% predictable from the local 5-letter context, on held-apart data, with the same context families leading in two independent datasets and hot-spot AUC 0.80 to 0.90. We fit a per 5-letter context error table and put it into the simulator, so the risk model has something real to learn.

**Encoder.** A DNA Fountain style LT code with a robust soliton distribution, several candidate seeds per strand, configurable hard rules and a pluggable scorer. A CRC-16 in every strand turns a wrongly decoded strand into a missing one, so a confident mistake can't corrupt the file.

**Decoders.** A majority vote baseline with iterative alignment (90.6% exact strands at 16 reads on real held-out Nanopore data), always reported next to any model result. A transformer decoder that reconstructs a strand from up to 16 noisy reads: each read is encoded separately, then one learned query per output position cross-attends to all read tokens.

**Risk model.** A 1D CNN over one-hot bases. Labels are failure rates, not single failures: each training strand is simulated 32 times and decoded, and the label is the fraction decoded wrongly. The training strands are generated by us, with controlled runs, GC skew and motifs.

**The loop.** It alternates and freezes: adapt the decoder, label strands, train the risk model, grid search the settings, re-adapt the decoder. At most three rounds. The settings search is a plain grid on purpose.

**Evidence design, fixed before any run.** A rule audit table. An ablation ladder A to E where the default is always system B (same decoder), so B to C isolates tier 1 and C to D isolates tier 2. A crossover matrix running each tuned codec on its own and the other channel. A sim-to-real firewall: a structurally different Simulator B (bursty errors, a different position curve, a context table from the other dataset), never used for optimization, plus the risk model's ranking checked on real held-out reads.

**Compute.** All GPU training and the CPU heavy recovery trials run on an ASUS Ascent GX10 with an NVIDIA GB10 and 128 GB of unified memory.

**Process.** Much of the code was written by coding agents working in parallel on separate branches. We owned the shared interfaces, the held-out data rules, the verification of every number and the story. The result is about 10,900 lines of Python with about 2,400 lines of tests.

## Challenges we ran into

- **Our first simulator was far too easy.** It matched the average error rates of real reads, yet the baseline decoder scored far higher on simulated reads than on real ones from 4 reads per strand up (see the calibration tables in `docs/ERRORS.md`). Matching averages isn't enough; the structure of the errors matters. Adding per-read quality spread, shared per-position errors and the context table closed the gap to within about 3 points.
- **Noisy labels.** A strand can fail once by bad luck, and training on single outcomes would teach the risk model noise. So every label is a failure rate over 32 simulations.
- **Moving targets.** Training the decoder and the risk model at the same time gives the risk model stale labels. We alternate and freeze instead.
- **Not fooling ourselves.** The loop could learn a quirk of our own simulator. That's why Simulator B and the real-read check exist, and why a gain that doesn't survive them gets reported as such.
- **Biased data.** The Microsoft dataset's references are known not to be uniformly random, so we never learn risky motifs from them.
- **ARM64.** The GX10 is aarch64, not x86, so every dependency had to work on linux-aarch64 and GPU support in PyTorch had to be checked before anything else. [RESULT: say what actually happened here, or cut this bullet.]

## Accomplishments that we're proud of

- A simulator that matches real Nanopore reads within about 3 points at every read count on held-out data, checked on data no fit touched.
- Finding that shared Nanopore errors are 30 to 66% predictable from the 5-letter context in two independent datasets, patterns the standard rules don't cover.
- An evidence design written down before the first run, with a fixed objective, so no one could pick a metric after seeing results.
- A clean separation of claims: the ablation ladder shows exactly where any gain comes from.
- [RESULT: the headline result we're proudest of, once it holds under the firewall].

## What we learned

- A rule that is right on one channel can be dead weight on another, and the only way to know is to measure at a fixed target. [RESULT: which rules turned out channel dependent, if any].
- Calibrating a simulator to averages gives false confidence. Calibrate to the thing you care about, here decoder accuracy against coverage.
- Being narrow about claims makes a project stronger. "Same encoder, same decoder, fixed target" is a sentence judges and researchers both trust.
- With coding agents, writing code gets fast. The bottlenecks move to GPU time, integration and checking every number.

## What's next for StrandAudit

- Run the audit on more channels and read budgets, including long-term archival storage, where most loss is whole strands.
- A conditional risk model that takes the channel description as input, so it can handle operating points it never trained on.
- Let users calibrate a channel profile from a sample of their own reads.
- Real cost figures in place of our placeholder prices.
- Validating a tuned codec with a wet lab partner, synthesizing and sequencing strands it chose.

## Built with

python, pytorch, numpy, rapidfuzz, streamlit, plotly, pandas, pytest, uv, nvidia-gb10, asus-ascent-gx10, cuda

**Data:** Microsoft clustered Nanopore reads dataset (MIT license), DNAformer binned reads from Technion (CC BY 4.0).

## Try it out

- Code: https://github.com/timarnoldev/hackmit2026
- Landing page: `marketing/index.html` in the repo

## Team

[TEAM: names, roles and links]
