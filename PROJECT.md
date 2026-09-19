# Adaptive DNA Codec

**One line:** A system that adapts how data is encoded and decoded in DNA to the specific situation it will be stored in, by running a feedback loop between an AI-guided encoder and an AI decoder until the codec is tuned for that situation.

## The problem

DNA can store data for centuries at extreme density, but it's a very noisy medium. Writing (synthesis) and reading (sequencing) introduce errors: wrong letters, extra letters, missing letters, and whole strands that get lost.

Which errors dominate depends heavily on the situation:

| Situation | Dominant problem |
|---|---|
| Nanopore sequencing | Many insertions and deletions, especially in runs of the same letter |
| Illumina sequencing | Few errors, mostly single wrong letters |
| Long storage time | DNA degrades, more strands get lost entirely |
| Tight reading budget | Few noisy copies per strand to work with |

Today's codecs are **one size fits all**. Encoders follow fixed hand-written rules such as "avoid long runs of the same letter" and "keep GC content near 50%". Every such rule costs storage density. Rules that are too strict waste space on an easy channel, and rules that are too loose lose data on a hard one. Decoders, including recent AI decoders (DNAformer, Yaakobi lab, Technion), are trained once and then used as they are.

The research field has built the pieces (simulators, encoders, decoders), but they come from separate groups and don't adapt to each other or to the situation.

## Our idea: a feedback loop that tailors the codec to the situation

```
                        Situation profile
           (sequencing tech, storage time, read budget)
                               │
                               ▼
 ┌─────────────────┐    ┌────────────┐    ┌──────────┐    ┌──────────────┐
 │ Encoder         │──▶ │  Channel   │──▶ │ AI       │──▶ │ Failure      │
 │ generates       │    │ for this   │    │ decoder  │    │ analysis     │
 │ candidates,     │    │ situation  │    │ (adapts) │    │              │
 │ risk model      │    │            │    │          │    │              │
 │ picks safest    │    │            │    │          │    │              │
 └─────────────────┘    └────────────┘    └──────────┘    └──────────────┘
          ▲                                                      │
          └────── retrain risk model, adjust redundancy ◀────────┘

             repeat until density, accuracy and cost stop improving
                               │
                               ▼
                 Tailored codec for this situation
```

### The three AI components

1. **AI decoder.** A transformer that takes several noisy reads of the same strand and reconstructs the original. It's pretrained once and fine-tuned per situation.
2. **Risk model.** A small CNN that predicts how likely a given DNA sequence is to fail in this situation. It's trained on the decoder's failures.
3. **Loop optimizer.** Adjusts redundancy and strand length based on the observed dropout rate and cost.

### The encoder

The encoder is classic at its core (Fountain-code style), so every file is guaranteed to decode exactly when enough strands survive. Fountain encoders already generate many candidate strands and reject bad ones using hand-written rules. **We replace those rules with the learned risk model**, which differs per situation. That makes the encoding AI-guided without giving up correctness.

### One loop iteration

1. **Encode** test data. The risk model picks the lowest-risk candidates.
2. **Simulate** the channel for this situation (error profile, dropout rate, coverage).
3. **Fine-tune the decoder** on this channel.
4. **Analyze failures.** Which strands, patterns, and positions could the decoder not fix?
5. **Retrain the risk model** on those failures and adjust redundancy.
6. **Repeat** until the trade-off between density, accuracy, and reading cost stops improving.

**Output:** a codec tailored to this situation (risk model, encoder settings, decoder weights), with its expected density, accuracy, and cost.

Encoder and decoder **co-adapt**. A stronger decoder means fewer failures, so the risk model gets more permissive and more data fits per strand. A harder channel makes the risk model steer away from the patterns the decoder struggles with.

## What's new

| Existing work | What we add |
|---|---|
| One fixed codec for every situation | A codec tuned per situation |
| Hand-written rules for rejecting candidate strands | A learned risk model trained on real decoder failures |
| AI decoders trained once | Decoder fine-tuned to the specific channel |
| Encoder and decoder designed separately | Encoder and decoder adapting to each other in a loop |
| Papers with fixed parameters | An interactive tool for an engineer's actual constraints |

## The dashboard

**Inputs (the situation)**

- Sequencing technology (Illumina or Nanopore)
- Storage duration
- Reading budget (reads per strand)
- File size and acceptable error rate

**Outputs**

- The loop live: density, accuracy, and cost per iteration as the codec converges
- Final codec compared to the one-size-fits-all default
- Accuracy vs coverage curve for the tailored codec vs the default
- Heatmap of where errors occur along a strand and which patterns the risk model learned to avoid
- Cost per MB for writing and reading

**Demo:** run the loop for 2 or 3 contrasting situations (for example Nanopore with a tight budget vs Illumina for a 100 year archive). Show that each ends up with a different codec and that each tailored codec beats the default in its own situation. Finish by encoding and decoding a real file (an image) through the tailored codec.

## Key metric

> **Each situation gets a codec that beats the default: more data per strand, fewer reads needed, or both.**

Examples of the claims we want to be able to make:

- "On Illumina, the loop dropped unnecessary constraints and stored X% more data per strand at the same accuracy."
- "On Nanopore, the tailored codec needs 6 reads per strand instead of 15, making reading about 60% cheaper."

## Scope

**Core (must ship)**

- Channel simulator with 3 situation profiles: insertions, deletions, substitutions, dropouts, uneven coverage, pattern-dependent errors
- Fountain-style encoder with candidate generation and a pluggable scorer
- Baseline decoder (alignment plus majority vote per position)
- Transformer decoder (strands of about 110 letters, clusters of up to 16 reads)
- Risk model and the loop
- Evaluation harness
- Dashboard

**Stretch (only if core is done and verified)**

- Experimental fully learned encoder (end-to-end autoencoder through the channel), shown as a research comparison, not as the product

**Out of scope (and we say so openly)**

- Wet lab work
- Clustering reads by origin strand (standard assumption in the literature; the simulator gives us the true clusters)
- Basecalling from raw Nanopore signal

## Data: real reads and simulator

We use both. Real data alone can't drive the loop, because every loop iteration produces a new encoding, and nobody can synthesize and sequence it for us overnight. Only a simulator can "read" a new encoding.

**Public real datasets**

| Dataset | Platform | Content | Link |
|---|---|---|---|
| Microsoft clustered Nanopore reads | Nanopore (MinION) | 10,000 random strands of length 110, 269,709 reads, already clustered, ground truth in `Centers.txt`. Error rates roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions | [GitHub](https://github.com/microsoft/clustered-nanopore-reads-dataset) |
| DNAformer binned reads (Technion) | Nanopore (2 flowcells) and Illumina | Strands of length 140, clusters with reference label per cluster, random and semantic files, about 1.2 GB, CC BY 4.0 | [Zenodo](https://zenodo.org/records/17473983) |

**How we use real data**

1. **Calibrate the simulator.** Measure error rates, position dependence, run-length effects, and coverage distribution from real clusters versus their references. Our situation profiles for Nanopore and Illumina are fit to real data, not guessed.
2. **Seed the risk model.** The Microsoft strands are uniform random, so they contain risky patterns naturally. Real decoder failures on them show which patterns are dangerous on real Nanopore.
3. **Fine-tune the decoder** on real clusters after pretraining on simulated data.
4. **Honest benchmark.** A held-out split of real clusters that no training ever touches. Other papers (Trellis BMA, BBS, DNAformer) report numbers on the Microsoft dataset, so we can compare against published results.

**How we use the simulator**

- Pretraining the decoder on unlimited data
- Every loop iteration (new encodings, new situations such as long storage)

## Building with AI agents

Implementation is done by coding agents working in parallel. That makes writing code fast, so the bottlenecks move elsewhere:

- **GPU time.** Training doesn't get faster with agents. Secure compute in the first hour and keep the GPU busy at all times.
- **Integration.** Parallel agents only work if interfaces are fixed before they start.
- **Verification.** Agents write plausible code that can be quietly wrong. The biggest danger is inflated results, for example the decoder being tested on data it has already seen, or simulator and decoder sharing hidden assumptions.
- **Decisions and story.** Agents don't decide what the demo proves.

### Human roles

| Role | Responsibility |
|---|---|
| Architect | Owns interfaces and the repo, merges agent output, keeps the pipeline running end to end |
| ML verifier | Owns the evaluation harness and the held-out test set, sanity-checks every number |
| Compute and data | Owns GPU jobs, looks for real public datasets |
| Story and dashboard | Owns the pitch, decides what the demo must show, drives the dashboard agent |

With fewer people, combine roles. Architect and ML verifier should be different people.

### Phase 0 (hour 0 to 1): humans only, no agents yet

- Repo layout and a `CLAUDE.md` or `AGENTS.md` with the project context and rules
- Fixed interfaces:
  - `Strand = str` over ACGT, `Cluster = list[str]`
  - `SituationProfile` as JSON (error rates, dropout rate, coverage distribution, cost numbers)
  - `encode(bytes, settings, scorer) -> list[Strand]`
  - `simulate(strands, profile, seed) -> list[Cluster]`
  - `decode(clusters) -> list[Strand]`, `recover(strands) -> bytes`
  - `evaluate(...) -> Metrics` (strand accuracy, file recovery yes or no, bits per base, reads per strand, cost per MB)
- Fixed held-out test set seeds that no training code may touch
- Real data downloaded (the full 1.2 GB set goes straight onto the GPU machine, hackathon wifi is too slow), held-out real split fixed
- GPU secured (sponsor credits, cloud, Colab)

### Phase 1 (hours 1 to 4): parallel agents

| Agent | Task | Done when |
|---|---|---|
| A | Simulator v0 within the first hour (rates from the Microsoft README) so pretraining can start, then calibrate Nanopore and Illumina profiles on real data | Simulated error statistics match the real datasets |
| B | Fountain encoder, candidate generation, pluggable scorer, recovery | Exact file round trip on a noise-free channel |
| C | Baseline decoder and evaluation harness | Metrics on all 3 profiles, numbers look plausible |
| D | Transformer decoder training script | Overfits a tiny dataset, then starts full pretraining on GPU |
| E | Dashboard skeleton on mock data | All charts render from a results JSON format |

Human checkpoint: full pipeline runs end to end with the baseline decoder.

### Phase 2 (hours 4 to 10)

- Decoder pretraining running on GPU
- Agent F: risk model (CNN) and its training from failure logs
- Agent G: loop orchestrator (encode, simulate, fine-tune, analyze, retrain, repeat) writing results JSON per iteration
- Human checkpoint: transformer beats baseline on the held-out set, verified by the ML verifier

### Phase 3 (hours 10 to 18)

- Run the loop for all 3 situations
- Compare tailored codecs vs default, produce the key numbers
- Stretch agents in parallel: real dataset validation, experimental learned encoder
- Human checkpoint: results are reproducible from a clean run

### Phase 4 (hours 18 to 26)

- Dashboard wired to real results
- Precomputed loop runs, plus one live iteration for the demo
- Image round trip through the tailored codec

### Phase 5 (hours 26 to end)

- Freeze features
- Pitch, rehearsal, backup demo video
- Buffer for things that break

## Risks

- **Inflated results.** Agents may accidentally train or tune on test data. Mitigation: held-out seeds fixed in phase 0, evaluation owned by a human, baseline always reported alongside.
- **Simulator doesn't match reality.** Judges will ask whether this holds on real reads. Mitigation: simulator calibrated on real datasets, decoder fine-tuned and benchmarked on held-out real clusters. The long storage situation has no public real data, so we say its profile is extrapolated.
- **Loop too slow to run live.** Each iteration needs a fine-tune. Mitigation: small model, short fine-tunes, precomputed demo runs plus one live iteration.
- **Loop might not converge.** Mitigation: few discrete redundancy settings, cap on iterations, report the best iteration.
- **Learned patterns might rediscover known rules.** That still validates the approach. The new part is that the risk model differs per situation.
- **Integration chaos from parallel agents.** Mitigation: fixed interfaces, one merge owner, end-to-end run after every merge.

## Glossary

- **Strand:** one short piece of DNA, about 100 to 200 letters (A, C, G, T)
- **Synthesis:** chemically writing DNA; the expensive step
- **Sequencing:** reading DNA; produces many noisy copies (reads)
- **Coverage:** how many reads you have per original strand
- **Codec:** the encoder plus decoder pair
- **Density:** how many bits are stored per DNA letter (max 2)
- **Fountain code:** a code that turns data into many strands, any large enough subset of which recovers the file
- **Homopolymer:** a run of the same letter, like AAAAAA; error-prone
- **GC content:** share of G and C letters; extremes cause problems
- **Dropout:** a strand that is lost entirely
- **Trace reconstruction:** recovering the original strand from several noisy copies
