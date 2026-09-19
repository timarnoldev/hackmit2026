# Agent instructions

Read `PROJECT.md` for the idea. This file is the rules you work under.

## What we are building

An adaptive DNA storage codec. For each storage situation (profile), a loop encodes data, simulates the noisy channel, decodes with a transformer, learns from the failures with a risk model, and re-encodes. The output is a codec tailored to that situation that beats the one-size-fits-all default. A dashboard shows the loop and the results.

## Setup

```bash
uv sync                          # core deps
uv sync --extra train            # + torch, for Agents D, F, G
uv sync --extra dashboard        # + streamlit, plotly, pandas, for Agent E
scripts/download_data.sh         # real datasets (--full on the GPU machine)
uv run pytest -q                 # must stay green
```

## Layout

| Path | Owner | What |
|---|---|---|
| `dnacodec/types.py` | Architect | Shared types. **Do not change** |
| `dnacodec/profiles.py`, `profiles/*.json` | Architect, Agent A tunes numbers | Situation profiles |
| `dnacodec/seeds.py` | Architect | Train vs held-out seeds. **Do not change** |
| `dnacodec/realdata.py` | Architect | Real dataset loaders and fixed held-out split. **Do not change the split** |
| `dnacodec/results.py` | Architect | Result file format, the contract between loop and dashboard |
| `dnacodec/simulator.py` | Agent A | Channel simulator and calibration on real data |
| `dnacodec/encoder.py` | Agent B | Fountain encoder, rule scorer, recovery |
| `dnacodec/baseline.py`, `dnacodec/evaluate.py` | Agent C | Baseline decoder, evaluation harness |
| `dnacodec/model/` | Agent D | Transformer decoder, training, fine-tuning |
| `dashboard/` | Agent E | Streamlit dashboard, reads only `results/` |
| `dnacodec/risk.py` | Agent F | Risk model |
| `dnacodec/loop.py` | Agent G | The adaptive loop |

Only edit files you own. If you need something from another module that doesn't exist yet, write against the stub's docstring and mock it in your tests. Don't implement another agent's module.

## Hard rules

1. **Never change shared interfaces** (`types.py`, `seeds.py`, `results.py`, signatures in stubs). If one is wrong, stop and tell a human.
2. **Held-out data is sacred.**
   - Every random seed used for training, tuning, or choosing settings goes through `train_seed()`.
   - `heldout_seeds()` and `split="heldout"` are used only by evaluation and final benchmarks.
   - Real data: train on `split="train"` only.
   Violating this makes every number we show worthless.
3. **All metrics come from `dnacodec.evaluate.evaluate`.** Don't compute accuracy your own way anywhere else.
4. **Always report the baseline next to any model result.**
5. **No fake numbers.** Mock data only through `scripts/make_mock_results.py`, always `is_mock=True`. The dashboard shows a visible MOCK banner for mock runs.
6. **Tests.** Add tests for your module in `tests/test_<module>.py`. `uv run pytest -q` must pass before you hand work back.
7. **Report honestly.** When you finish, state what works, what you tested, and what you did not get to. Don't claim results you did not run.

## Conventions

- Python 3.11+, type hints, numpy for numerics, torch for models.
- Strands are plain `str` over `ACGT`. Clusters are `list[str]`, empty list = dropout.
- Randomness: `np.random.default_rng(seed)`, never global random state.
- Paths via the constants in the modules (`DATA_DIR`, `RESULTS_DIR`, `PROFILES_DIR`), never hard-coded absolute paths.
- Long-running jobs write progress to a log file and save checkpoints, so a crash never loses hours.

## Data facts

- Microsoft Nanopore set: 10,000 references of length 110, 269,709 reads, mean cluster size 27, median 21, 16 empty clusters. Error rates roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions.
- DNAformer set: references of length 140, Nanopore (2 flowcells) and Illumina, random and semantic files.
- Decoders must handle strand lengths up to 140 and clusters up to 16 reads (subsample larger clusters).
- Profile costs are placeholders. Don't present them as real.

## Tasks and definition of done

**Agent A, simulator.** First ship a working `simulate()` within the hour, using the profile numbers as they are, so pretraining can start. Then add `scripts/calibrate.py` that aligns real reads to references (Microsoft and DNAformer train splits), measures substitution, insertion and deletion rates, position dependence, homopolymer effect and coverage distribution, and writes calibrated profiles with `calibrated_from` set.
Done when simulated error statistics match the real ones within about 10% relative.

**Agent B, encoder.** `rule_scorer`, `encode`, `recover`. Fountain code (LT with robust soliton distribution), seed in the first `seed_bases` bases, candidate seeds ranked by the scorer.
Done when a random 100 KB file round-trips exactly on a noise-free channel, with up to `redundancy` fraction of strands dropped, and strands obey the hard constraints.

**Agent C, baseline and evaluation.** `MajorityVoteDecoder` and `evaluate()`.
Done when metrics are produced for all profiles and for the real Microsoft held-out split, and the baseline accuracy is plausible (on Microsoft data, full clusters, well above 50% exact strands).

**Agent D, transformer decoder.** See `dnacodec/model/__init__.py`. Start with `--smoke`, then full pretraining on simulated data across a wide range of error rates, then fine-tuning on real train splits.
Done when it beats the baseline on the Microsoft held-out split and a checkpoint loads through `TransformerDecoder`.

**Agent E, dashboard.** Streamlit app in `dashboard/app.py`, built first on `results/mock/`, with a run selector. Shows: loop progress per iteration, tailored vs default codec, accuracy vs coverage curve, per-position error heatmap, risky patterns, cost per MB.
Done when it renders every run in `results/` and shows a MOCK banner for mock runs.

**Agent F, risk model.** `RiskModel` in `dnacodec/risk.py`.
Done when, trained on decoder failures from one profile, it ranks held-out failing strands above succeeding ones (ROC AUC clearly above 0.5, reported).

**Agent G, loop.** `run_loop()` in `dnacodec/loop.py` plus `scripts/run_loop.py --profile NAME --run-id ID`.
Done when it runs end to end for all profiles with the baseline decoder, then with the transformer, saving results after every iteration.
