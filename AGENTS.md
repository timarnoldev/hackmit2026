# Agent instructions

Read `PROJECT.md` for the idea. This file is the rules you work under.

## What we are building

A tool that measures whether each DNA coding rule pays off on a given channel, and tunes the codec accordingly (tier 1: rule audit), then learns from decoder failures what to avoid beyond the hand rules (tier 2: learned selection). Decoder failures tell the encoder what to avoid, separately per channel. For each situation (profile), an alternating loop adapts and freezes the decoder, labels strands by their failure rate over K simulations, trains a risk model on those labels, grid-searches encoder settings with the risk-scored encoder, and re-adapts the decoder. The claim is narrow: at a fixed recovery target, the tailored codec needs fewer reads per strand or carries less redundancy than the default **with the same decoder** (ablation B vs C). PROJECT.md is the source of truth for the objective, the evidence (ablation ladder, crossover matrix, sim-to-real firewall) and the claims.

Core situations: `nanopore_budget` and `illumina_standard`. `illumina_archive_100y` is stretch only.

## Setup

```bash
uv sync                          # core deps
uv sync --extra train            # + torch, for Agents D, F, G
uv sync --extra dashboard        # + streamlit, plotly, pandas, for Agent E
scripts/download_data.sh         # real datasets (--full on the GPU machine)
uv run pytest -q                 # must stay green
```

## Compute: ASUS Ascent GX10

All GPU work runs on one ASUS Ascent GX10 (NVIDIA GB10 Grace Blackwell, the same chip as DGX Spark).
- **ARM64 (aarch64), not x86.** Every dependency must work on linux-aarch64. The first check on the machine is `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` plus a small matmul on the GPU. If the pip wheel doesn't see the GPU, use NVIDIA's NGC PyTorch container instead of fighting wheels.
- **128 GB unified memory** shared by CPU and GPU. Memory is never the limit for our model sizes; keep simulated datasets in RAM.
- **Blackwell GPU:** train in bf16 autocast. It's roughly a strong desktop GPU for training, well below a datacenter H100, so don't scale models up because of the name.
- **20 ARM CPU cores:** the simulator, the baseline decoder, recovery trials and risk labeling are CPU work. Parallelize them across cores with `concurrent.futures.ProcessPoolExecutor`, one seed per task, so results stay deterministic per seed.
- **One shared GPU.** Long jobs declare themselves in `GPU_JOBS.md` (who, what, expected end). Pretraining has priority until hour 10.

## Layout

| Path | Owner | What |
|---|---|---|
| `dnacodec/types.py` | Architect | Shared types. **Do not change** |
| `dnacodec/profiles.py`, `profiles/*.json` | Architect, Agent A tunes numbers | Situation profiles |
| `dnacodec/seeds.py` | Architect | Train vs held-out seeds. **Do not change** |
| `dnacodec/realdata.py` | Architect | Real dataset loaders and fixed held-out split. **Do not change the split** |
| `dnacodec/results.py` | Architect | Result file format, the contract between loop and dashboard |
| `dnacodec/simulator.py`, `dnacodec/simulator_b.py`, `scripts/calibrate.py` | Agent A | Channel simulator, calibration on real data, Simulator B for the firewall |
| `dnacodec/encoder.py` | Agent B | Fountain encoder, rule scorer, recovery |
| `dnacodec/baseline.py`, `dnacodec/evaluate.py` | Agent C | Baseline decoder, evaluation harness |
| `dnacodec/model/` | Agent D | Transformer decoder, training, fine-tuning |
| `dashboard/` | Agent E | Streamlit dashboard, reads only `results/` |
| `dnacodec/risk.py` | Agent F | Risk model |
| `dnacodec/loop.py`, `scripts/run_loop.py`, `scripts/run_experiments.py` | Agent G | The alternating loop and the evidence experiments |

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
- **Microsoft caveat:** its README (note of 8/12/2024) says the references are *not* uniformly random (generation bug, long-range dependencies) and some clusters may be malformed. Use it for reconstruction benchmarks, calibration, and comparison with papers. **Never train the risk model on its references.** The per-5-mer error table fit on its reads is allowed: it's an error rate given a context, cross-checked on DNAformer, and Simulator B uses a DNAformer table instead.
- DNAformer set: references of length 140, Nanopore (2 flowcells) and Illumina, random and semantic files.
- Decoders must handle strand lengths up to 140 and clusters up to 16 reads (subsample larger clusters).
- Profile costs are placeholders. Don't present them as real.

## Objective (fixed, from PROJECT.md)

- Recovery target: file recovered exactly in all 300 held-out trials.
- Test file: `dnacodec/testfile.py:test_file()`, 20 KB of random bytes from a fixed seed. Never change its size or seed.
- Trial budget: 50 train-seed trials per candidate in the settings search, a 300 train-seed re-check before choosing a setting, 300 held-out trials once per final codec.
- Primary metrics: minimum mean reads per strand meeting the target (at matched bits per base), and maximum bits per base meeting the target (at the situation's budget). Results are judged on the Pareto front of (bits per base, reads per strand). Buying accuracy with extra redundancy is not an improvement.
- The comparison default is always system B: fixed rules and redundancy, **same transformer decoder**.

## Tasks and definition of done

**Agent A, simulator.** Done: `simulate()`, calibration of `nanopore_budget` on Microsoft train. Next:
1. Realism, using the new optional profile fields `homopolymer_run_factors`, `read_quality_spread`, `malformed_read_rate` (defaults reproduce the old model). Known gap: with the calibrated profile at mean coverage 27, the baseline scores higher on simulated than on real reads (6 reads: 83% sim vs 67% real; 16 reads: 96% vs 90%), and lower at 2 reads (0.5% vs 5.5%). Real reads vary more in quality, long runs fail more, and some clusters are malformed. Fit the new fields so that baseline accuracy vs coverage on simulated reads matches real within a few points.
2. Calibrate `illumina_standard` on `BinnedTestIllumina_Random` (train split). Prefer DNAformer Nanopore for per-position effects, since Microsoft references are biased.
3. Simulator B in `dnacodec/simulator_b.py`, same signature: rates perturbed by up to ±30%, a structurally different homopolymer and position error model (e.g. bursty errors, non-linear position curve). Used only for the firewall test, never for optimization.

**Agent B, encoder.** `rule_scorer`, `encode`, `recover`. Fountain code (LT with robust soliton distribution), seed in the first `seed_bases` bases, per-strand checksum, candidate seeds ranked by the scorer. Honor `settings.risk_threshold`: candidates the scorer rates above it are rejected like hard-constraint violations (None disables).
Done (merged). Recovery tolerates losing up to 1 − 1/(1 + redundancy) of strands, the theoretical limit. Original criterion: a random 100 KB file round-trips exactly on a noise-free channel, with strands dropped, corrupted strands are discarded without crashing, and strands obey the hard constraints.

**Agent C, evaluation.** Done: `evaluate()`, `MajorityVoteDecoder`, real-data baseline. Next, trial-based file recovery in `dnacodec/evaluate.py`:
- `recovery_trials` and `min_reads_at_target`: signatures and contract are fixed in the stubs in `dnacodec/evaluate.py`. Parallelize trials across CPU cores.
- Use held-out seeds only when called for final evaluation; the loop calls these with train seeds.

**Agent D, transformer decoder.** See `dnacodec/model/__init__.py`. Pretrain on simulated data across a wide range of error rates, fine-tune per channel. Log held-out accuracy at 2/4/6/10/16 reads against the baseline table in README. Fallback if it doesn't beat the baseline by hour 10: published DNAformer code or the baseline decoder; the B vs C comparison works with any fixed decoder.
Done when it beats the baseline on the Microsoft held-out split at low coverage and a checkpoint loads through `TransformerDecoder`.

**Agent E, dashboard.** Streamlit app in `dashboard/app.py`, built on `results/mock/` (run `uv run python -m scripts.make_mock_results`). Reads `load_runs()` and `load_summary()`. Views, in priority order:
1. **Rule audit** (main view, from `summary.rule_audit`): per channel, every rule with its verdict and measured cost and benefit.
2. **Pareto plot** (main view): bits per base on x, reads per strand needed on y; default point plus one point per alternation, one panel per channel.
3. Crossover matrix, colored.
4. Ablation ladder A to E as bars, baseline always visible.
5. What the encoder learned: risky patterns per channel side by side, plus accepted and rejected candidate examples.
6. Per-position error heatmap, accuracy vs coverage, cost per MB labeled as placeholder prices.
Done when it renders every run in `results/` and shows a MOCK banner for mock data.

**Agent F, risk model.** In `dnacodec/risk.py`:
- A controlled sequence generator: uniform random strands plus strands with deliberately varied homopolymer length, GC content and motifs. Not Microsoft references.
- Labels are failure rates: simulate each strand K times at the situation's coverage, decode with the frozen decoder, label = fraction decoded wrongly. `fit(strands, failed)` takes these floats.
- Small CNN regressing the failure rate; `top_kmers` for the dashboard.
Done when ROC AUC of predicted risk vs actual failure on held-out simulated strands is clearly above 0.5, and the risk ranking is also reported on held-out real clusters (firewall test 3).

**Agent G, loop and experiments.** `run_loop()` in `dnacodec/loop.py` plus `scripts/run_loop.py --profile NAME --run-id ID`:
- Alternate and freeze, per PROJECT.md: adapt decoder and freeze, label, train risk model, grid search (redundancy, strand length, which hard constraints are on, risk threshold) keeping the cheapest setting meeting the target on train seeds, re-adapt decoder. At most three alternations. Save a RunResult after every alternation.
- `scripts/run_experiments.py`: rule audit (each rule toggled from the default codec with the same decoder, plus default vs tuned redundancy, verdict per rule and channel, as `RuleAuditEntry`), ablation ladder A to E (see `AblationEntry` docstring: C = audited rules and tuned redundancy with the rule scorer only, D = C plus the learned risk scorer), crossover matrix, firewall (Simulator B, real risk AUC), candidate examples, saved via `save_summary`.
Done when both core situations run end to end with the baseline decoder, then with the transformer.
