# Adaptive DNA Codec

**One line:** We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

**Thesis:** A sequence constraint, and every extra strand of redundancy, should only be paid for when it measurably reduces decoding failure on the target channel. Today these rules are folklore: chosen once, copied between papers, applied to every channel. We replace them with measurements.

**What the tool does:** For a given channel (sequencing technology, read budget, recovery target), it

1. **audits every coding rule and the redundancy level**: switches each rule on and off, measures what it costs and what it buys in reads per strand and bits per base, and keeps only what pays off, and
2. **learns what to avoid from decoder failures**: a risk model trained on where the decoder actually fails picks the safest candidate strands, going beyond the hand-written rules where the channel has patterns they don't cover.

**What we show:** At a fixed file recovery target, with the *same* encoder and the *same* decoder, the tuned codec needs fewer reads per strand or carries less redundancy than the hand-tuned default. A crossover experiment shows the tuning is channel-specific: the Illumina-tailored codec cannot recover the file on the Nanopore channel at any coverage we tested.

This document is the design and the evidence plan. The measured results live in [docs/NUMBERS.md](docs/NUMBERS.md), which is the single registry of every number we quote.

## The problem

DNA stores data at extreme density for centuries, but it's a very noisy medium. Writing (synthesis) and reading (sequencing) introduce substitutions, insertions and deletions, and whole strands get lost.

Which errors dominate depends heavily on the channel:

| Channel | Dominant problem |
|---|---|
| Nanopore sequencing | Many insertions and deletions, especially in runs of the same letter |
| Illumina sequencing | Few errors, mostly single wrong letters |
| Tight reading budget | Few noisy copies per strand to reconstruct from |
| Long storage time | DNA degrades, more strands get lost entirely |

Practical pipelines usually fix their sequence constraints ("no runs longer than 3", "GC between 40% and 60%") and their redundancy by hand, and they train decoders once. Adaptive constrained coding, learned decoders (DNAformer, Bar-Lev et al. 2025) and end-to-end learned codes all exist, but they optimize the rules, the redundancy or the decoder **in isolation**. Nobody closes the loop so that the decoder's actual failures on a target channel decide what the encoder avoids and how much redundancy the codec carries.

## Our claims, stated narrowly

We compete at the **system level**: how an existing codec should be configured for one explicit operating point (channel, read budget, recovery target). Our opponent is the hand-tuned default practitioners use, not other papers' components. The claim has two tiers:

| Tier | Claim | Status |
|---|---|---|
| **1. Rule audit** | Measured per channel, some standard rules and redundancy levels don't pay off, and tuning them reaches the recovery target more cheaply than the default | Holds regardless of what the risk model learns. Directly tests the thesis |
| **2. Learned selection** | Decoder failures teach the encoder to avoid patterns the hand rules don't cover, improving further on tier 1 | Depends on whether the channel has learnable sequence-dependent errors beyond homopolymer runs |

We do **not** claim that adaptive DNA coding is new, that our encoder or decoder beats DNA Fountain or DNAformer (the encoder is a standard Fountain code on purpose, the decoder only has to be solid), or that the encoder is a neural network.

**Why tier 2 has something to learn.** Errors that all reads of a strand share at the same position, the ones more reads can't average away, are largely predictable from the local sequence. On held-apart halves of the train data, the centered 5-mer explains about 45% of the between-position error variance on Microsoft Nanopore and 66% on DNAformer Nanopore, and predicts error hot spots with AUC 0.80 to 0.90. The same context families lead in both datasets, and tables fit on one predict the other's hot spots with AUC 0.71 to 0.79. The hand rules (homopolymer length, GC window) don't cover these contexts. The simulator models them with a per-5-mer error table fit on real train reads, so the risk model has something real to learn. Provenance for all of these: [docs/NUMBERS.md](docs/NUMBERS.md) section 3.

If tier 2 doesn't hold, the honest result is still strong: the tool shows with measurements where the hand rules are close to optimal and where they cost without helping.

## Where the gain actually comes from

This matters because it decides which claims are honest.

1. **Candidate selection is free.** A Fountain encoder tries many seeds per strand anyway. Picking the lowest risk candidate among 8 costs zero density; it only costs compute.
2. **Lower strand failure is the direct effect.** If the risk model picks strands the decoder reconstructs more reliably, the per-strand failure rate drops at a given coverage.
3. **That converts into the two numbers people pay for:**
   - **Fewer reads per strand** to hit the recovery target (cheaper reading), or
   - **Less redundancy** at the same coverage, meaning more bits per synthesized base (cheaper writing).
4. **Dropping useless constraints widens the pool.** On a channel where homopolymers barely matter (Illumina), the loop may disable the hard homopolymer rule. That doesn't save density directly, but it lets the risk model choose among more candidates and spend its selection on what actually fails there.

So the headline is about **reads per strand and redundancy**. We don't claim large density gains from "dropping constraints" on their own, because in a Fountain code they are nearly free.

## Objective, fixed before any run

For each situation with read budget B:

- **Test file:** a fixed 20 KB file of random bytes (compressed data looks random), generated from a fixed seed in `dnacodec/testfile.py`, with a test that fails if it ever changes. That's roughly 1,000 to 1,300 strands depending on settings. Recovery probability depends strongly on file size, so the size never changes between codecs. The demo image is separate and only used for the demo.
- **Recovery target:** the file is recovered exactly in all 300 held-out trials (95% upper bound on the failure rate is about 1%). One trial = one independent pass through the channel with its own held-out seed.
- **Trial budget:** the settings search uses 50 trials per candidate setting on train seeds. The full 300 held-out trials are run only once per final codec. Candidates that pass all 50 search trials are re-checked with 300 train-seed trials before being chosen, so a lucky setting can't slip through.
- **Primary metric, reading:** the minimum mean reads per strand at which the codec meets the recovery target, at matched bits per base.
- **Primary metric, writing:** the maximum bits per base at which the codec meets the recovery target, at coverage B.
- **Summary:** the Pareto front of (bits per base, reads per strand) at the recovery target. A codec is better only if it moves this front, not if it buys accuracy with extra redundancy.

Cost per MB is shown in the dashboard, but cost numbers are placeholders and are never the headline.

## How it works

### Components

| Component | Role | Is it AI? |
|---|---|---|
| **Channel simulator** | Turns strands into clusters of noisy reads for a situation. Calibrated on real reads, including run-length effects and a per-5-mer error table | No |
| **Encoder** | Fountain (LT) code. Generates candidates per strand, applies the hard constraints that are enabled for this situation, keeps the one the scorer rates safest. A checksum per strand turns decoder mistakes into erasures, so a confident wrong strand can never corrupt the file | No, but its choices are steered by the risk model |
| **Decoder** | The classic decoder aligns the reads and votes; a small CNN polisher corrects what is left. The alignment plus majority vote baseline is always reported next to it | **Yes**, the polisher |
| **Risk model** | Small CNN predicting P(decode failure \| sequence, situation). Works on top of the audited hand rules and catches the patterns they don't cover | **Yes** |
| **Settings search** | Grid search over redundancy, strand length, which hard constraints are on, and the risk threshold | No, deliberately |

The settings search is a plain grid on purpose. The interesting learning happens in the decoder and the risk model; a third "AI optimizer" would add risk and weaken the story. How both models are built and trained: [docs/MODELS.md](docs/MODELS.md).

### Risk labels are probabilities, not single failures

A strand failing once may be bad luck: a cluster with 2 reads, a burst of deletions, an undertrained decoder. Learning "failed means bad" teaches the CNN noise. Instead, for every labeled strand:

```
sequence x  ──▶  simulate K times at this situation's coverage  ──▶  decoder
            ──▶  label = fraction of the K clusters decoded wrongly
```

The risk model regresses on that failure rate. **Training sequences** are generated under control, not taken from Microsoft's data: uniform random strands plus strands with deliberately varied homopolymer length, GC content and motifs. That lets us check whether the model rediscovers known effects on Nanopore, and whether it correctly learns that they don't matter much on Illumina.

### The loop alternates, it doesn't co-train

Fine-tuning the decoder while the risk model learns from it gives the risk model a moving target: labels from decoder A go stale once it becomes decoder B. So we alternate and freeze.

```
               Situation profile + read budget B + recovery target
                                      │
                                      ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 1. Adapt the decoder to this channel, then freeze it               │
  │ 2. Label strands: simulate each K times, measure failure rate      │
  │ 3. Train the risk model on those labels                            │
  │ 4. Grid search settings with the risk-scored encoder; keep the     │
  │    cheapest point that meets the recovery target (train seeds)     │
  │ 5. Re-adapt the decoder to the strands the new encoder produces    │
  └────────────────────────────────────────────────────────────────────┘
                 steps 2 to 5 run at most twice more
                                      │
                                      ▼
        Tailored codec = risk model + settings + decoder weights
        evaluated once on held-out seeds, reported on the Pareto plot
```

Each alternation is one point on the Pareto plot, so the dashboard shows the codec moving toward the front.

## The evidence

These are core deliverables, not stretch goals. Without them a judge can't tell where an improvement comes from. Measured values for all five: [docs/NUMBERS.md](docs/NUMBERS.md).

### 1. Rule audit (the tool's main output)

For each channel and each rule, starting from the default codec with the same decoder: reads per strand needed with the rule on and off, bits per base with it on and off, and a verdict. Every verdict comes from held-out recovery trials. This table is the direct answer to "does this rule pay off on my channel".

### 2. Ablation ladder (same channel, same held-out seeds)

| System | Encoder | Decoder |
|---|---|---|
| A | Fixed rules, fixed redundancy | Majority vote baseline |
| B | Fixed rules, fixed redundancy | Learned decoder, adapted to the channel |
| C | Audited rules + tuned redundancy, rule scorer only | Same decoder as B, frozen |
| D | C + learned risk scorer | Same decoder as B, frozen |
| E | Full alternating loop | Re-adapted decoder |

A to B is the decoder's contribution, which DNAformer already showed. **B to C is tier 1 (rule audit). C to D is tier 2 (learned selection).** D to E is the value of co-adaptation. The "default" we compare against is always B, never A.

The ladder's C to D rung is not a clean isolation of tier 2, because the settings search is free to move redundancy between the rungs. The isolated measurement holds every setting fixed and changes only which scorer ranks the candidates; that is the version we quote, and it is in [docs/NUMBERS.md](docs/NUMBERS.md) section 5.

### 3. Crossover matrix

Each tailored codec run on its own channel and on the other, against the default on both. A codec that wins at home and not away is tailored, not just better overall. Because the tailored codecs sit at different densities, this matrix is read on the Pareto front of bits per base against reads per strand, never on reads alone; [docs/NUMBERS.md](docs/NUMBERS.md) section 5 states what the measured matrix does and does not show.

### 4. Sim-to-real firewall

The loop could learn a quirk of our own simulator instead of a property of DNA. To catch that:

| Stage | Channel used |
|---|---|
| Optimize | Simulator A (calibrated profile, train seeds) |
| Test 1 | Simulator A, held-out seeds |
| Test 2 | Simulator B: rates perturbed by ±30%, a different homopolymer and position error model, a context table fit on DNAformer instead of Microsoft, held-out seeds |
| Test 3 | Real reads: decoder accuracy on held-out real clusters, and risk model ranking (ROC AUC of predicted risk against actual decoder failure on held-out real clusters) |

A gain that holds under Simulator B and a risk model whose ranking holds on real reads are the strongest evidence possible without a wet lab. A gain that disappears under B is reported as simulator overfitting, not hidden.

### 5. What the encoder learned

Per channel: the most penalized patterns (homopolymers, GC runs, motifs) with their learned risk, side by side, plus the same candidate strands accepted on one channel and rejected on the other. This is the visual proof that the same encoder behaves differently per situation. Read out of the trained model in [docs/LEARNED_RULES.md](docs/LEARNED_RULES.md).

## Scope

**Core**

- Channel simulator with two calibrated situations: `nanopore_budget` and `illumina_standard`
- Simulator B (perturbed variant) for the firewall test
- Fountain encoder with per-strand checksum, configurable hard constraints and a pluggable scorer
- Baseline decoder and a learned decoder (strands up to 140 letters, clusters up to 16 reads)
- Risk model trained on failure rates from controlled sequences
- Alternating loop with grid settings search
- Evaluation harness, ablation ladder, crossover matrix, firewall tests
- Dashboard

**The decoder is replaceable by design.** Our question is not "can a neural network reconstruct DNA" (known) but "can decoder failures steer the encoder". The B to C comparison works with any fixed decoder, which is why a from-scratch transformer that lost to the classic baseline could be dropped for a CNN polisher without touching the claim. See [docs/MODELS.md](docs/MODELS.md) section 3.

**Stretch**

- Third situation `illumina_archive_100y`. Decay is mostly strand loss, an erasure problem, so tailoring there is mainly redundancy tuning. Its profile is extrapolated because no real data exists, and we say so.
- Conditional risk model: one network taking the sequence plus a situation vector (error rates, dropout, coverage mean and spread, storage age, technology). Could interpolate to operating points it was never trained on.
- Experimental end-to-end learned encoder as a research comparison, not the product.

**Out of scope, and we say so openly**

- Wet lab work, so no newly designed strand is ever physically sequenced
- Clustering reads by origin strand (standard assumption; the simulator gives true clusters)
- Basecalling from raw Nanopore signal

## Data: real reads and simulator

Real data alone can't drive the loop: every alternation produces new strands, and only a simulator can "read" them overnight. Real data keeps the simulator and the models honest.

| Dataset | Platform | Content | Link |
|---|---|---|---|
| Microsoft clustered Nanopore reads | Nanopore (MinION) | 10,000 references of length 110, 269,709 reads, already clustered. Error rates roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions | [GitHub](https://github.com/microsoft/clustered-nanopore-reads-dataset) |
| DNAformer binned reads (Technion) | Nanopore (2 flowcells) and Illumina | References of length 140, clusters labeled with their reference, random and semantic files, about 1.2 GB, CC BY 4.0 | [Zenodo](https://zenodo.org/records/17473983) |

**Caveat on the Microsoft set.** The dataset README (note added 8/12/2024) says the references are *not* uniformly random: a generation bug gave them long-range dependencies, and some clusters may be malformed as a result. We therefore never train the risk model on its references. We use it as a reconstruction benchmark, for calibration and for comparison with published results (Trellis BMA, BBS, DNAformer).

Calibration does include a per-5-mer error table fit on Microsoft train reads. That is a different thing: it measures the error rate *given* a context, which the skewed reference composition mostly makes noisier for rare contexts. Malformed clusters could still bias some contexts. Two checks keep it honest: the same context families lead on DNAformer Nanopore, and Simulator B uses a table fit on DNAformer instead. A pattern the risk model learned only from a Microsoft quirk would lose its advantage in firewall test 2.

**How we use real data**

1. **Calibrate the simulator:** error rates, position dependence, run-length effects, per-5-mer context errors, read quality spread and coverage distribution, fit to real train clusters against their references.
2. **Fine-tune the decoder** on real train clusters after pretraining on simulated data.
3. **Validate the risk model:** its predicted risk must rank held-out real failing strands above succeeding ones.
4. **Benchmark honestly** on held-out real clusters that no training touches.

**How we use the simulator**

- Pretraining the decoder on unlimited data
- Risk labels (K simulations per strand)
- Every loop alternation
- Simulator B, only for the firewall test and never for optimization

## Risks and how the design handles them

| Risk | Mitigation |
|---|---|
| **Improvement is really just the decoder** | The default is always B, with the same decoder. The ablation ladder separates the contributions |
| **Loop optimizes against our own simulator** | Simulator B test and real-data risk ranking. Gains that don't survive are reported as such |
| **Metric gaming** (accuracy bought with redundancy) | Objective and recovery target fixed before any run; results judged on the Pareto front |
| **Noisy risk labels** | Failure rate over K simulations instead of binary single outcomes |
| **Moving target between decoder and risk model** | Alternate and freeze, at most three alternations |
| **A decoder outputs a confident wrong strand** | Per-strand CRC-16 turns it into an erasure the Fountain code handles |
| **The learned decoder underperforms** | Fall back to the baseline; the core claim (B to C) holds with any fixed decoder |
| **Winner's curse in the settings search** | Best-of-hundreds selection on the same trials inflates results. Selection runs on train seeds only, with a margin check at 10% fewer reads and three independent evaluation blocks |
| **Biased real data** | Microsoft references are known to be non-random. The risk model never trains on them; the context error table fit on Microsoft reads is cross-checked on DNAformer, and Simulator B uses the DNAformer table |
| **Gains are small** | Selection is free, so any gain is a pure win; the ablation shows exactly how large it is. A small, clean, verified effect beats a large unverified one |
| **Learned patterns just rediscover known rules** | That validates the method on Nanopore. The informative part is that Illumina learns different, weaker rules, visible in the crossover matrix |

## Glossary

- **Strand:** one short piece of DNA, about 100 to 200 letters (A, C, G, T)
- **Synthesis:** chemically writing DNA; the expensive step
- **Sequencing:** reading DNA; produces many noisy copies (reads)
- **Coverage:** reads per original strand; reading cost scales with it
- **Codec:** encoder plus decoder, plus their settings
- **Density:** stored bits per synthesized letter (max 2)
- **Redundancy:** extra strands beyond the minimum needed, so the file survives lost strands
- **Fountain code:** turns data into many strands, any large enough subset of which recovers the file
- **Checksum:** a few bits per strand that detect a wrongly decoded strand so it's treated as lost
- **Homopolymer:** a run of the same letter, like AAAAAA; error-prone on Nanopore
- **GC content:** share of G and C letters; extremes cause problems
- **Dropout:** a strand lost entirely
- **Trace reconstruction:** recovering the original strand from several noisy copies
- **Pareto front:** the set of codecs where you can't improve density without needing more reads, or the reverse
- **Operating point:** one specific combination of channel, read budget and recovery target
