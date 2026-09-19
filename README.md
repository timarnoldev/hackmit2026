# Adaptive DNA Codec

**HackMIT 2026.** A DNA data storage codec that adapts to the situation it's used in. An AI-guided encoder and an AI decoder improve each other in a feedback loop until the codec is tuned for a given sequencing technology, storage duration, and reading budget.

> **Status: in progress.** The foundation is done: shared interfaces, situation profiles, real data loaders, tests. The simulator, encoder, decoders, risk model, loop, and dashboard are being built in parallel. See [Status](#status).

---

## Contents

- [Why](#why)
- [The idea](#the-idea)
- [How it works](#how-it-works)
- [What's new](#whats-new)
- [Status](#status)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Data](#data)
- [Situation profiles](#situation-profiles)
- [Evaluation and honesty rules](#evaluation-and-honesty-rules)
- [Development workflow](#development-workflow)
- [DNA storage in 60 seconds](#dna-storage-in-60-seconds)
- [References](#references)

---

## Why

DNA stores data at extreme density and lasts for centuries, but it's a very noisy medium. Writing (synthesis) and reading (sequencing) introduce errors: wrong letters, extra letters, missing letters, and entire strands that get lost.

Which errors dominate depends on the situation:

| Situation | Dominant problem |
|---|---|
| Nanopore sequencing | Many insertions and deletions, especially in runs of the same letter |
| Illumina sequencing | Few errors, mostly single wrong letters |
| Long-term storage | DNA degrades, more strands are lost entirely |
| Tight reading budget | Few noisy copies per strand to reconstruct from |

Today's codecs are **one size fits all**. Encoders follow fixed, hand-written rules such as "avoid long runs of the same letter" and "keep GC content near 50%". Every rule costs storage density. Rules that are too strict waste space on an easy channel, and rules that are too loose lose data on a hard one. Decoders, including recent AI decoders, are trained once and used as they are.

An engineer who wants to archive data in DNA has no tool that answers:

> *"Given my budget, my sequencing technology, and how long I need to store the data, which encoding and how much redundancy should I use?"*

## The idea

Close the loop between encoder and decoder, separately for each situation.

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

The output for each situation is a tailored codec (encoder settings, risk model, decoder weights) along with its expected density, accuracy, and cost.

Encoder and decoder **co-adapt**. A stronger decoder fails less, so the risk model becomes more permissive and more data fits per strand. A harder channel makes the risk model steer away from the patterns the decoder struggles with.

## How it works

### Components

| Component | What it does | Module |
|---|---|---|
| **Channel simulator** | Turns strands into clusters of noisy reads for a situation: substitutions, insertions, deletions, homopolymer and position effects, dropouts, uneven coverage. Calibrated on real sequencing data | `dnacodec/simulator.py` |
| **Encoder** | DNA Fountain style LT code. For each strand it generates several candidates, rejects those that break hard constraints, and keeps the one the scorer rates safest. A checksum per strand lets recovery discard corrupted strands | `dnacodec/encoder.py` |
| **Baseline decoder** | Classic alignment plus majority vote. Reported next to every model result | `dnacodec/baseline.py` |
| **Transformer decoder** | Reconstructs the original strand from up to 16 noisy reads. Pretrained on simulated and real data, fine-tuned per situation | `dnacodec/model/` |
| **Risk model** | Small CNN that learns which strands the decoder fails on in a given situation. Replaces the encoder's hand-written rejection rules | `dnacodec/risk.py` |
| **Loop** | Runs encode, simulate, fine-tune, analyze, retrain, repeat. Writes results after every iteration | `dnacodec/loop.py` |
| **Evaluation** | The only place metrics are computed | `dnacodec/evaluate.py` |
| **Dashboard** | Streamlit app showing the loop, tailored vs default codecs, accuracy vs coverage, error patterns, and cost | `dashboard/` |

### One loop iteration

1. **Encode** test data. The risk model picks the lowest-risk candidate strands.
2. **Simulate** the channel for this situation.
3. **Fine-tune the decoder** on this channel.
4. **Analyze failures**: which strands, patterns, and positions the decoder could not fix.
5. **Retrain the risk model** on those failures and adjust redundancy.
6. **Evaluate** on held-out seeds and record the iteration.

### Why the encoder isn't a neural network

A fully learned encoder (an autoencoder trained through the channel) sounds appealing, but insertions and deletions are hard to differentiate through, and storage needs every bit back, not "mostly right". We keep a classic fountain code at the core, which guarantees exact recovery once enough strands survive, and let AI decide which candidate strands to use. DNA Fountain already generates and screens candidates with hand-written rules. We replace those rules with a learned, situation-specific risk model.

## What's new

| Existing work | What we add |
|---|---|
| One fixed codec for every situation | A codec tuned per situation |
| Hand-written rules for rejecting candidate strands | A learned risk model trained on actual decoder failures |
| AI decoders trained once | A decoder fine-tuned to the specific channel |
| Encoder and decoder designed separately | Encoder and decoder adapting to each other in a loop |
| Papers with fixed parameters | An interactive tool for an engineer's real constraints |

**Key metric:** each situation gets a codec that beats the default, storing more data per strand, needing fewer reads, or both.

## Status

| Part | State |
|---|---|
| Shared types, profiles, seeds, result format | ✅ Done |
| Real data loaders (Microsoft, DNAformer) with fixed held-out split | ✅ Done |
| Mock results for dashboard development | ✅ Done |
| Channel simulator and calibration | 🚧 In progress |
| Fountain encoder | 🚧 In progress |
| Baseline decoder and evaluation | 🚧 In progress |
| Transformer decoder | 🚧 In progress |
| Dashboard | 🚧 In progress |
| Risk model | ⏳ Next |
| Adaptive loop | ⏳ Next |

No results are published yet. Anything under `results/mock/` is fake data for building the dashboard and is flagged as such.

## Getting started

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), git. A CUDA GPU for full decoder training.

```bash
git clone https://github.com/timarnoldev/hackmit2026.git
cd hackmit2026

uv sync                        # core dependencies
uv sync --extra train          # + PyTorch, for the decoder, risk model, and loop
uv sync --extra dashboard      # + Streamlit, Plotly, pandas, for the dashboard

scripts/download_data.sh       # Microsoft set + small DNAformer subset (~90 MB)
scripts/download_data.sh --full   # all datasets (~1.2 GB), use on the GPU machine

uv run pytest -q               # run the tests
```

Generate mock results for the dashboard:

```bash
uv run python -m scripts.make_mock_results
```

Commands for training, running the loop, and launching the dashboard will be added here as those components land.

## Repository layout

```
.
├── PROJECT.md              Full project brief: idea, plan, risks
├── AGENTS.md               Rules and task specs for coding agents (CLAUDE.md points here)
├── dnacodec/
│   ├── types.py            Shared types: Strand, Cluster, EncoderSettings, Metrics, Decoder
│   ├── profiles.py         SituationProfile: load, validate, save
│   ├── seeds.py            Train vs held-out seed discipline
│   ├── realdata.py         Loaders for real datasets and the fixed held-out split
│   ├── results.py          Result file format (the contract between loop and dashboard)
│   ├── simulator.py        Channel simulator
│   ├── encoder.py          Fountain encoder, rule scorer, recovery
│   ├── baseline.py         Majority vote baseline decoder
│   ├── evaluate.py         Metrics
│   ├── model/              Transformer decoder: data, network, training, inference
│   ├── risk.py             Risk model
│   └── loop.py             The adaptive loop
├── profiles/               Situation profiles as JSON
├── dashboard/              Streamlit dashboard
├── scripts/                Data download, mock results, calibration, evaluation
├── tests/                  pytest suite
├── data/                   Downloaded datasets (not committed)
├── checkpoints/            Model checkpoints (not committed)
└── results/                Loop output, one folder per run (not committed)
```

## Data

We use real sequencing data **and** a simulator. Real data alone can't drive the loop: every iteration produces a new encoding, and only a simulator can "read" it overnight. Real data keeps the simulator honest.

| Dataset | Platform | Content | License |
|---|---|---|---|
| [Microsoft clustered Nanopore reads](https://github.com/microsoft/clustered-nanopore-reads-dataset) | Nanopore (MinION) | 10,000 random references of length 110, 269,709 reads, already clustered. Mean cluster size 27, median 21. Error rates roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions | MIT |
| [DNAformer binned reads](https://zenodo.org/records/17473983) (Technion) | Nanopore (2 flowcells) and Illumina | References of length 140, clusters labeled with their reference, random and semantic files, about 1.2 GB | CC BY 4.0 |

How real data is used:

1. **Calibrate the simulator**: error rates, position dependence, homopolymer effects, and coverage are fit to real reads.
2. **Fine-tune the decoder** on real clusters after pretraining on simulated data.
3. **Seed the risk model** with real decoder failures.
4. **Benchmark honestly** on held-out real clusters that no training touches.

Loading:

```python
from dnacodec import realdata

train = realdata.load_microsoft("train")      # 8,000 clusters
heldout = realdata.load_microsoft("heldout")  # 2,000 clusters, evaluation only
illumina = realdata.load_dnaformer("BinnedTestIllumina_Random", "train")
```

## Situation profiles

A profile describes the storage channel in one situation. Profiles live in `profiles/*.json`.

| Field | Meaning |
|---|---|
| `technology` | `nanopore` or `illumina` |
| `sub_rate`, `ins_rate`, `del_rate` | Per-base error rates |
| `homopolymer_factor` | Error multiplier per extra base in a run of the same letter |
| `end_factor` | Error multiplier at the end of the strand vs the start |
| `dropout_rate`, `gc_dropout_factor` | Strand loss, and extra loss for unbalanced GC content |
| `coverage_mean`, `coverage_dispersion` | Reading budget in reads per strand, and how uneven it is |
| `storage_years`, `decay_per_year` | Extra strand loss from aging |
| `synthesis_usd_per_base`, `sequencing_usd_per_read` | Cost model |
| `calibrated_from` | Dataset the numbers were fit to, or `null` if estimated |

Current profiles:

| Profile | Situation |
|---|---|
| `nanopore_budget` | Portable Nanopore reading on a tight budget, short-term storage |
| `illumina_standard` | Lab Illumina reading, normal budget, short-term storage |
| `illumina_archive_100y` | Century-scale archive read with Illumina; heavy strand loss (extrapolated, no real data exists) |

> Cost numbers are placeholders until verified and are not presented as real figures.

## Evaluation and honesty rules

Our numbers are only worth something if they're measured cleanly. These rules are enforced in code where possible:

- **Held-out seeds** (`seeds.heldout_seeds()`) and the **held-out real split** (every 5th cluster) are used only for evaluation. `seeds.train_seed()` raises an error when training code touches a held-out seed.
- **All metrics come from `dnacodec.evaluate`.** No ad-hoc accuracy calculations elsewhere.
- **The baseline is always reported** next to every model result.
- **Mock data is flagged** (`is_mock=True`) and the dashboard shows a banner for it.
- **Uncalibrated or extrapolated numbers are labeled** as such.

Metrics per evaluation: exact strand accuracy, mean edit distance, dropout rate, reads per strand, per-position error, file recovered (yes or no), net density in bits per base, and write and read cost per MB.

## Development workflow

The code is written largely by AI coding agents working in parallel, with humans owning interfaces, verification, and the story.

- `AGENTS.md` defines ownership per file, hard rules, and a "done when" check for each component.
- Each agent works on its own branch in its own git worktree. Shared interfaces (`types.py`, `seeds.py`, `results.py`) don't change without the architect.
- One person merges into `main` and runs `uv run pytest -q` after every merge. The full pipeline must run end to end after each merge.
- An ML verifier checks every reported number before it's used.

## DNA storage in 60 seconds

1. **Encode**: bits become a sequence of A, C, G, T (up to 2 bits per letter).
2. **Split into strands** of about 100 to 200 letters. Each strand carries an index, because strands are stored unordered in a tube.
3. **Synthesis**: strands are written chemically. Expensive, and introduces errors.
4. **Storage and PCR**: strands are copied many times before reading. Some get copied more, some are lost.
5. **Sequencing**: the machine returns many noisy copies ("reads") of each strand.
6. **Clustering**: reads are grouped by the strand they came from.
7. **Trace reconstruction**: each strand is recovered from its noisy copies. This is where the decoder works.
8. **Outer error correction**: the fountain code recovers the file even when strands are missing.

Glossary:

- **Coverage**: number of reads per original strand. Sequencing cost scales with it.
- **Density**: stored bits per synthesized letter (maximum 2).
- **Homopolymer**: a run of the same letter, like `AAAAAA`. Error-prone, especially on Nanopore.
- **GC content**: share of G and C letters. Extremes cause synthesis and reading problems.
- **Dropout**: a strand lost entirely.

## References

- Erlich, Zielinski. *DNA Fountain enables a robust and efficient storage architecture.* Science, 2017.
- Organick et al. *Random access in large-scale DNA data storage.* Nature Biotechnology, 2018.
- Srinivasavaradhan, Gopi, Pfister, Yekhanin. *Trellis BMA: Coded trace reconstruction on IDS channels for DNA storage.* ISIT 2021. [arXiv:2107.06440](https://arxiv.org/abs/2107.06440)
- Bar-Lev et al. *Scalable and robust DNA-based storage via coding theory and deep learning.* Nature Machine Intelligence, 2025. [Link](https://www.nature.com/articles/s42256-025-01003-z)
- Rashtchian et al. *Clustering billions of reads for DNA data storage.* NeurIPS 2017.

Datasets: [Microsoft clustered Nanopore reads](https://github.com/microsoft/clustered-nanopore-reads-dataset) (MIT), [DNAformer binned reads](https://zenodo.org/records/17473983) (CC BY 4.0).
