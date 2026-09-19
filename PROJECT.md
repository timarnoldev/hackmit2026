# Adaptive DNA Codec

**One line:** Instead of deciding in advance which DNA sequences are dangerous, we let decoding failures tell the encoder what to avoid, separately for each storage and sequencing channel.

**Thesis:** A sequence constraint, and every extra strand of redundancy, should only be paid for when it measurably reduces decoding failure on the target channel.

**What we show:** For a given channel and read budget, a closed loop between an AI decoder and a learned candidate scorer finds a codec that reaches the same file recovery target with fewer reads per strand or more bits per base than a hand-tuned default using the *same* decoder. A crossover experiment shows the tailoring is real: each tailored codec wins on its own channel and loses its edge on the other.

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

## Our claim, stated narrowly

> We turn decoder failures into the training signal for encoder candidate selection, and we optimize the whole codec for one explicit operating point: channel, read budget, recovery target.

We do **not** claim that adaptive DNA coding is new, that we built a better DNAformer, or that the encoder is a neural network.

## Where the gain actually comes from

This matters because it decides which claims are honest.

1. **Candidate selection is free.** A Fountain encoder tries many seeds per strand anyway. Picking the lowest risk candidate among 8 costs zero density; it only costs compute.
2. **Lower strand failure is the direct effect.** If the risk model picks strands the decoder reconstructs more reliably, the per-strand failure rate drops at a given coverage.
3. **That converts into the two numbers people pay for:**
   - **Fewer reads per strand** to hit the recovery target (cheaper reading), or
   - **Less redundancy** at the same coverage, meaning more bits per synthesized base (cheaper writing).
4. **Dropping useless constraints widens the pool.** On a channel where homopolymers barely matter (Illumina), the loop may disable the hard homopolymer rule. That doesn't save density directly, but it lets the risk model choose among more candidates and spend its selection on what actually fails there.

So the headline is about **reads per strand and redundancy**. We will not claim large density gains from "dropping constraints" on their own, because in a Fountain code they are nearly free.

## Objective (fixed before any run)

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
| **Channel simulator** | Turns strands into clusters of noisy reads for a situation. Calibrated on real reads | No |
| **Encoder** | Fountain (LT) code. Generates candidates per strand, applies the hard constraints that are enabled for this situation, keeps the one the scorer rates safest. A checksum per strand turns decoder mistakes into erasures, so a confident wrong strand can never corrupt the file | No, but its choices are steered by the risk model |
| **Decoder** | Transformer that reconstructs a strand from up to 16 noisy reads. Pretrained on simulated data, fine-tuned per channel. The alignment plus majority vote baseline is always reported next to it | **Yes** |
| **Risk model** | Small CNN predicting P(decode failure \| sequence, situation). Replaces hand-written rejection rules | **Yes** |
| **Settings search** | Grid search over redundancy, strand length, which hard constraints are on, and the risk threshold | No, deliberately |

The settings search is a plain grid on purpose. The interesting learning happens in the decoder and the risk model; a third "AI optimizer" would add risk and weaken the story.

### Risk labels are probabilities, not single failures

A strand failing once may be bad luck: a cluster with 2 reads, a burst of deletions, an undertrained decoder. Learning "failed means bad" teaches the CNN noise. Instead, for every labeled strand:

```
sequence x  ──▶  simulate K times at this situation's coverage  ──▶  decoder
            ──▶  label = fraction of the K clusters decoded wrongly
```

The risk model regresses on that failure rate. `RiskModel.fit(strands, failed)` already accepts a float array, so this needs no interface change.

**Training sequences** are generated under control, not taken from Microsoft's data: uniform random strands plus strands with deliberately varied homopolymer length, GC content and motifs. That lets us check whether the model rediscovers known effects on Nanopore, and whether it correctly learns that they don't matter much on Illumina.

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

Each alternation is one point on the Pareto plot, so the dashboard can show the codec moving toward the front.

## Evidence we will produce

These are core deliverables, not stretch goals. Without them a judge can't tell where an improvement comes from.

### 1. Ablation ladder (same channel, same held-out seeds)

| System | Encoder | Decoder |
|---|---|---|
| A | Fixed rules, fixed redundancy | Majority vote baseline |
| B | Fixed rules, fixed redundancy | Transformer, adapted to the channel |
| C | Learned risk scorer + settings search | Same transformer as B, frozen |
| D | Full alternating loop | Re-adapted transformer |

A to B is the decoder's contribution, which DNAformer already showed. **B to C is our contribution.** C to D is the value of co-adaptation. The "default" we compare against is always B, never A.

### 2. Crossover matrix

| Codec ↓ / Channel → | Nanopore, 6 reads | Illumina, 15 reads |
|---|---|---|
| Default (B) | baseline | baseline |
| Nanopore-tailored | **better** | neutral or worse |
| Illumina-tailored | worse | **better** |

If each tailored codec wins at home and not away, that proves the codec is tailored and not just better overall.

### 3. Sim-to-real firewall

The loop could learn a quirk of our own simulator instead of a property of DNA. To catch that:

| Stage | Channel used |
|---|---|
| Optimize | Simulator A (calibrated profile, train seeds) |
| Test 1 | Simulator A, held-out seeds |
| Test 2 | Simulator B: rates perturbed by ±30%, a different homopolymer and position error model, held-out seeds |
| Test 3 | Real reads: decoder accuracy on held-out real clusters, and risk model ranking (ROC AUC of predicted risk vs actual decoder failure on held-out real clusters) |

A gain that holds under Simulator B and a risk model whose ranking holds on real reads are the strongest evidence possible without a wet lab. A gain that disappears under B is reported as simulator overfitting, not hidden.

### 4. What the encoder learned

For each channel: the most penalized patterns (homopolymers, GC runs, motifs) with their learned risk, side by side, plus the same candidate strands accepted on one channel and rejected on the other. This is the visual proof that the same encoder behaves differently per situation.

## Claims we want to be able to make

Written as templates. The numbers come only from `dnacodec.evaluate` on held-out seeds.

- "On Nanopore at a budget of 6 reads per strand, the tailored codec recovered the file in X of 300 held-out trials, vs Y for the default with the same transformer decoder."
- "To reach the recovery target, the Nanopore-tailored codec needs N reads per strand instead of M."
- "On Illumina, the loop learned that homopolymers barely matter, disabled that rule, and reached the recovery target with Z% less redundancy, so W% more bits per base."
- "Each tailored codec loses its advantage on the other channel" (crossover matrix).
- "The advantage holds on a differently built simulator, and the risk model's ranking holds on real Nanopore reads (AUC = …)."

If a claim doesn't hold, we show the ablation that explains why. That is still a result.

## The dashboard

**Inputs:** channel (Nanopore or Illumina), reading budget, recovery target, file.

**Main view, the Pareto plot.** Bits per base on the x axis, reads per strand needed on the y axis. The default codec is a point; each loop alternation is a point moving toward the front. One panel per channel. A judge understands it in five seconds.

**Supporting views**

- Crossover matrix, colored
- Ablation ladder A to D as a bar chart, baseline always visible
- "What the encoder learned": risky patterns per channel side by side, plus accepted and rejected candidate examples
- Per-position error heatmap along the strand
- Accuracy vs coverage for default and tailored codecs
- Cost per MB, clearly labeled as based on placeholder prices

**Demo script**

1. Open on the problem: the same codec for Nanopore and Illumina.
2. Show the loop for Nanopore at a tight budget (precomputed, plus one live alternation).
3. Show the Illumina run ending up with a different codec and different learned patterns.
4. Show the crossover matrix: each wins at home.
5. Encode an image, run it through the Nanopore channel at 6 reads per strand, decode it with both codecs. The default fails or needs more reads; the tailored one recovers it exactly.

## Scope

**Core (must ship)**

- Channel simulator with two calibrated situations: `nanopore_budget` and `illumina_standard`
- Simulator B (perturbed variant) for the firewall test
- Fountain encoder with per-strand checksum, configurable hard constraints and a pluggable scorer
- Baseline decoder
- Transformer decoder (strands up to 140 letters, clusters up to 16 reads)
- Risk model trained on failure rates from controlled sequences
- Alternating loop with grid settings search
- Evaluation harness, ablation ladder, crossover matrix, firewall tests
- Dashboard

**Transformer fallback.** Our question is not "can a transformer reconstruct DNA" (known) but "can decoder failures steer the encoder". If our transformer doesn't beat the baseline by hour 10, we switch to the published DNAformer code or run the whole loop with the baseline decoder. The B to C comparison still works with any fixed decoder.

**Stretch (only if core is done and verified)**

- Third situation `illumina_archive_100y`. Decay is mostly strand loss, an erasure problem, so tailoring there is mainly redundancy tuning. Its profile is extrapolated because no real data exists, and we say so.
- Conditional risk model: one network taking the sequence plus a situation vector (error rates, dropout, coverage mean and spread, storage age, technology). Could interpolate to operating points it was never trained on.
- Experimental end-to-end learned encoder as a research comparison, not the product.

**Out of scope (and we say so openly)**

- Wet lab work, so no newly designed strand is ever physically sequenced
- Clustering reads by origin strand (standard assumption; the simulator gives true clusters)
- Basecalling from raw Nanopore signal

## Data: real reads and simulator

Real data alone can't drive the loop: every alternation produces new strands, and only a simulator can "read" them overnight. Real data keeps the simulator and the models honest.

| Dataset | Platform | Content | Link |
|---|---|---|---|
| Microsoft clustered Nanopore reads | Nanopore (MinION) | 10,000 references of length 110, 269,709 reads, already clustered. Error rates roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions | [GitHub](https://github.com/microsoft/clustered-nanopore-reads-dataset) |
| DNAformer binned reads (Technion) | Nanopore (2 flowcells) and Illumina | References of length 140, clusters labeled with their reference, random and semantic files, about 1.2 GB, CC BY 4.0 | [Zenodo](https://zenodo.org/records/17473983) |

**Caveat on the Microsoft set.** The dataset README (note added 8/12/2024) says the references are *not* uniformly random: a generation bug gave them long-range dependencies, and some clusters may be malformed as a result. We therefore don't use it to discover risky motifs. We use it as a reconstruction benchmark, for calibration and for comparison with published results (Trellis BMA, BBS, DNAformer).

**How we use real data**

1. **Calibrate the simulator:** error rates, position dependence, homopolymer effect and coverage distribution, fit to real clusters vs their references.
2. **Fine-tune the decoder** on real train clusters after pretraining on simulated data.
3. **Validate the risk model:** its predicted risk must rank held-out real failing strands above succeeding ones.
4. **Benchmark honestly** on held-out real clusters that no training touches.

**How we use the simulator**

- Pretraining the decoder on unlimited data
- Risk labels (K simulations per strand)
- Every loop alternation
- Simulator B, only for the firewall test and never for optimization

## Building with AI agents

Coding agents implement in parallel. That makes code fast, so the bottlenecks move:

- **GPU time.** Training doesn't get faster with agents. Secure compute in the first hour and keep the GPU busy.
- **Integration.** Parallel agents only work if interfaces are fixed before they start.
- **Verification.** Agents write plausible code that can be quietly wrong. The biggest danger is inflated results: testing on seen data, or simulator and decoder sharing hidden assumptions.
- **Decisions and story.** Agents don't decide what the demo proves. This document does.

### Human roles

| Role | Responsibility |
|---|---|
| Architect | Owns interfaces and the repo, merges agent output, keeps the pipeline running end to end |
| ML verifier | Owns the evaluation harness, held-out seeds, ablation ladder, crossover matrix and firewall; sanity-checks every number |
| Compute and data | Owns GPU jobs and datasets |
| Story and dashboard | Owns the pitch, decides what the demo must show, drives the dashboard agent |

With fewer people, combine roles. Architect and ML verifier should be different people.

### Phase 0 (hour 0 to 1): humans only

- Repo layout, `AGENTS.md` with rules and ownership
- Fixed interfaces in `dnacodec/types.py`, `seeds.py`, `results.py`
- Held-out seeds and the held-out real split, fixed and untouchable by training code
- **Objective fixed in writing** (see above), so no one can pick a metric after seeing results
- Real data on the GPU machine, GPU secured

### Phase 1 (hours 1 to 4): parallel agents

| Agent | Task | Done when |
|---|---|---|
| A | Simulator v0 within the first hour so pretraining can start; then calibration on real data; then Simulator B | Simulated error statistics match real data within about 10% relative |
| B | Fountain encoder, per-strand checksum, candidate generation, pluggable scorer, recovery | Exact round trip on a noise-free channel; corrupted strands are discarded, never crash recovery |
| C | Baseline decoder and evaluation harness, including trial-based file recovery rate | Metrics on both profiles and on the Microsoft held-out split, numbers plausible |
| D | Transformer decoder training | Overfits a tiny set, then full pretraining on GPU |
| E | Dashboard skeleton on mock data, Pareto plot first | All views render from results JSON, MOCK banner visible |

Human checkpoint: full pipeline runs end to end with the baseline decoder.

### Phase 2 (hours 4 to 10)

- Decoder pretraining and per-channel fine-tuning on GPU
- Agent F: controlled sequence generator, failure rate labeling (K simulations per strand), risk model
- Agent G: alternating loop with grid settings search, results JSON after every alternation
- Human checkpoint: transformer beats baseline on held-out data, or the fallback is triggered. Risk model AUC clearly above 0.5 on held-out simulated strands.

### Phase 3 (hours 10 to 18)

- Run the loop for both situations
- Ablation ladder, crossover matrix, firewall tests (Simulator B and real data)
- Human checkpoint: every headline number reproduces from a clean run and survives the firewall, or is labeled as not surviving
- Stretch agents in parallel if the checkpoint is green: archive situation, conditional risk model

### Phase 4 (hours 18 to 26)

- Dashboard wired to real results
- Precomputed runs plus one live alternation for the demo
- Image round trip through default and tailored codecs at a tight budget

### Phase 5 (hours 26 to end)

- Feature freeze
- Pitch, rehearsal, backup demo video
- Buffer for things that break

## Risks

| Risk | Mitigation |
|---|---|
| **Improvement is really just the decoder** | Default is always B (same decoder). Ablation ladder separates the contributions |
| **Loop optimizes against our own simulator** | Simulator B test and real-data risk ranking. Gains that don't survive are reported as such |
| **Metric gaming** (accuracy bought with redundancy) | Objective and recovery target fixed in phase 0; results judged on the Pareto front |
| **Noisy risk labels** | Failure rate over K simulations instead of binary single outcomes |
| **Moving target between decoder and risk model** | Alternate and freeze, at most three alternations |
| **Transformer outputs a confident wrong strand** | Per-strand checksum turns it into an erasure the Fountain code handles |
| **Transformer underperforms** | Fallback to published DNAformer code or the baseline; the core claim (B to C) holds with any fixed decoder |
| **Biased real data** | Microsoft references are known to be non-random; we don't learn motifs from them |
| **Gains are small** | Selection is free, so any gain is a pure win; the ablation shows exactly how large it is. A small, clean, verified effect beats a large unverified one |
| **Learned patterns just rediscover known rules** | That validates the method on Nanopore. The new part is that Illumina learns different, weaker rules, visible in the crossover matrix |
| **Integration chaos from parallel agents** | Fixed interfaces, one merge owner, end-to-end run after every merge |

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
