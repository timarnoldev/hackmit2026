# Contributing

Engineering rules for this repository. `PROJECT.md` has the idea and the evidence design;
`docs/NUMBERS.md` is the registry of every number we quote. This file is the discipline that
keeps those numbers worth quoting.

`CLAUDE.md` points here, so the same rules apply to coding agents and to people.

## What this repository is

A tool that measures whether each DNA coding rule pays off on a given channel and tunes the codec
accordingly (tier 1, the rule audit), then learns from decoder failures what to avoid beyond the
hand rules (tier 2, learned selection). The claim is narrow: at a fixed recovery target, the
tailored codec needs fewer reads per strand or carries less redundancy than the default **with the
same decoder** (ablation B against C).

The two core channels are `nanopore_budget` and `illumina_standard`. `illumina_archive_100y` is
extrapolated and labelled as such.

## Setup

```bash
uv sync                          # core deps
uv sync --extra train            # + torch, for the decoder and risk model
uv sync --extra dashboard        # + streamlit, plotly, pandas
scripts/download_data.sh         # real datasets (--full for everything)
uv run --extra train --extra dashboard pytest -q   # 260 tests, must stay green
```

Without the extras, pytest skips the torch and dashboard suites and reports 185 tests.

## Hard rules

These are the reason our numbers mean anything. Several are enforced in code.

1. **Never change shared interfaces** without agreement: `dnacodec/types.py`, `dnacodec/seeds.py`,
   `dnacodec/results.py`. They are the contract between the encoder, the decoder, the loop and the
   dashboard.
2. **Held-out data is sacred.**
   - Every seed used for training, tuning or choosing settings goes through `seeds.train_seed()`,
     which raises if it is handed a held-out seed.
   - `seeds.heldout_seeds()` and `split="heldout"` belong to evaluation and final benchmarks only.
   - Real data: train on `split="train"` only. The held-out real split is every 5th cluster.
3. **All metrics come from `dnacodec.evaluate.evaluate`.** No ad-hoc accuracy calculation anywhere
   else, so two tables in this repo can never mean subtly different things.
4. **Always report the baseline next to any model result.** The majority vote decoder is the bar.
5. **No fake numbers.** Mock data exists only for dashboard development, comes only from
   `scripts/make_mock_results.py`, is always written with `is_mock=True`, and the dashboard shows a
   MOCK banner for it.
6. **Tests.** Every module has `tests/test_<module>.py`. `uv run pytest -q` passes before anything
   is merged.
7. **Report honestly.** State what was measured, what was not, and what did not work. A result that
   contradicts an earlier one gets measured again, not quietly dropped. `docs/COMPARISON.md` is
   written to be unflattering where the numbers are, and stays that way.

## Compute

GPU work runs on one ASUS Ascent GX10 (NVIDIA GB10 Grace Blackwell).

- **ARM64 (aarch64), not x86.** Every dependency has to work on linux-aarch64. First check on the
  machine: `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"`
  plus a small matmul. If the pip wheel does not see the GPU, use NVIDIA's NGC PyTorch container
  rather than fighting wheels.
- **128 GB unified memory** shared by CPU and GPU. Memory is not the limit at our model sizes, so
  simulated datasets stay in RAM.
- **Blackwell GPU:** train in bf16 autocast. It is roughly a strong desktop GPU, well below an
  H100, so model sizes do not scale with the name on the box.
- **20 ARM CPU cores.** The simulator, the baseline decoder, recovery trials and risk labelling are
  CPU work, parallelised with `concurrent.futures.ProcessPoolExecutor`, one seed per task, so
  results stay deterministic per seed regardless of worker count.

## Module ownership

Each module has one owner so interfaces stay stable. Write against another module's docstring and
mock it in your tests rather than implementing it yourself.

| Path | What |
|---|---|
| `dnacodec/types.py` | Shared types. Treat as frozen |
| `dnacodec/seeds.py` | Train against held-out seed discipline. Treat as frozen |
| `dnacodec/results.py` | Result file format, the contract between loop and dashboard |
| `dnacodec/realdata.py` | Real dataset loaders and the fixed held-out split |
| `dnacodec/profiles.py`, `profiles/*.json` | Channel profiles |
| `dnacodec/simulator.py`, `dnacodec/simulator_b.py`, `scripts/calibrate.py` | Channel simulator, calibration, Simulator B for the firewall |
| `dnacodec/encoder.py` | Fountain encoder, rule scorer, recovery |
| `dnacodec/baseline.py`, `dnacodec/evaluate.py` | Baseline decoder, evaluation harness |
| `dnacodec/model/` | Learned decoders and their training |
| `dnacodec/risk.py` | Risk model |
| `dnacodec/loop.py`, `scripts/run_loop.py`, `scripts/run_experiments.py` | The alternating loop and the evidence experiments |
| `dashboard/` | Streamlit dashboard, reads `results/` only |

## Conventions

- Python 3.11+, type hints, numpy for numerics, torch for models.
- Strands are plain `str` over `ACGT`. Clusters are `list[str]`; an empty list is a dropout.
- Randomness through `np.random.default_rng(seed)`, never global random state.
- Paths through the module constants (`DATA_DIR`, `RESULTS_DIR`, `PROFILES_DIR`), never hard-coded
  absolute paths.
- Long-running jobs write progress to a log file and checkpoint as they go, so a crash never costs
  hours.
- Comments explain *why*. The *what* is in the code.

## Data facts worth knowing before you touch the data

- **Microsoft Nanopore set:** 10,000 references of length 110, 269,709 reads, mean cluster size 27,
  median 21, 16 empty clusters. Roughly 1.7% insertions, 2.0% deletions, 2.2% substitutions.
- **Microsoft caveat.** Its README (note of 8/12/2024) states that the references are *not*
  uniformly random, because of a generation bug, and that some clusters may be malformed. Use it
  for reconstruction benchmarks, calibration and comparison with published work. **Never train the
  risk model on its references.** The per-5-mer error table fit on its reads is allowed: it
  measures an error rate *given* a context, it is cross-checked on DNAformer, and Simulator B uses
  a DNAformer table instead.
- **DNAformer set:** references of length 140, Nanopore (two flowcells) and Illumina, random and
  semantic files.
- Decoders handle strand lengths up to 140 and clusters up to 16 reads; larger clusters are
  subsampled.
- Profile cost fields are placeholders and are labelled as such wherever they are shown.

## The objective, fixed before any run

Changing any of this invalidates every comparison in the repository.

- **Recovery target:** the file is recovered exactly in all 300 held-out trials.
- **Test file:** `dnacodec/testfile.py:test_file()`, 20 KB of random bytes from a fixed seed. Its
  size and seed never change, and a test fails if they do.
- **Trial budget:** 50 train-seed trials per candidate in the settings search, a 300 train-seed
  re-check before a setting is chosen, 300 held-out trials once per final codec.
- **Primary metrics:** the minimum mean reads per strand meeting the target at matched bits per
  base, and the maximum bits per base meeting the target at the channel's budget. Codecs are judged
  on the Pareto front of the two, so accuracy bought with extra redundancy is not an improvement.
- **The comparison default is always system B:** fixed rules and redundancy, *same decoder*.
