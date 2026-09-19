"""Transformer decoder. Owner: Agent D.

Contents:
- data.py: training batches. ClusterPool (real train splits, or clusters from the loop),
  SimSource (random strands through dnacodec.simulator.simulate() with random profiles over
  a wide range of error rates, seeds via train_seed()), MixedSource. Coverage augmentation:
  every training cluster keeps a random 1..16 reads, with extra weight on 1..6.
- net.py: ConsensusNet. Per-read transformer encoder (token + position + read-index
  embeddings), then strand_length learned output queries (+ shared position code +
  strand-length embedding). Positions are anchored at both ends (sinusoids of the index from
  the start and from the end of each read / of the strand), which fixed a plateau where
  accuracy decayed along the strand from indel drift. Output queries go through transformer decoder layers that self-attend over output
  positions and cross-attend to every read token (Perceiver style). Logits over ACGT per
  position. Strands up to 160 bases, up to 16 reads, padding and empty reads masked.
  Presets: tiny (0.2M, smoke), small (3.8M), base (9.6M, default), large (~21M).
- train.py: CLI. --smoke memorization check, full runs with --source real|sim|mixed.
  Validation carved from the real TRAIN split picks best.pt; the real held-out split is
  only logged, at 2/4/6/10/16 reads.
- decoder.py: TransformerDecoder(checkpoint_path) implementing dnacodec.types.Decoder.
- benchmark.py: final held-out table for a checkpoint next to the baseline, same
  subsampling seeds as scripts/eval_real.py. Never used for selection.
- finetune.py: finetune(checkpoint_path, clusters, references, steps, out_path) -> Path,
  used by the loop. Only pass training clusters (train seeds or real train splits).

Import the submodules directly (dnacodec.model.decoder etc.); this package does not import
torch on its own.

GPU run
-------
Machine: ASUS Ascent GX10 (NVIDIA GB10, aarch64 Linux, 128 GB unified memory, 20 cores).
bf16 autocast and TF32 switch on automatically on cuda. Simulation runs on the fly in
DataLoader workers (a fresh random channel per batch, about 15 ms per batch of 128 on one
core), so no precomputed dataset is needed; --workers 8 keeps the GPU fed.

    git clone https://github.com/timarnoldev/hackmit2026.git && cd hackmit2026
    uv sync --extra train
    scripts/download_data.sh --full

    # 0) Does torch see the GPU?
    uv run python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0)); x = torch.randn(4096, 4096, device='cuda'); print((x @ x).sum().item())"
    # If False or an error (PyPI aarch64 wheels can be CPU-only), install the CUDA wheel:
    #   uv pip install --reinstall torch --index-url https://download.pytorch.org/whl/cu130
    #   and from then on use "uv run --no-sync ..." so uv does not put the CPU wheel back.
    # Fallback: NVIDIA's NGC PyTorch container, which supports GB10:
    #   docker run --gpus all -it --rm --ipc=host -v $PWD:/work -w /work nvcr.io/nvidia/pytorch:25.09-py3
    #   pip install numpy rapidfuzz pytest && pip install --no-deps -e .   # then run without "uv run"

    uv run pytest -q
    uv run python -m dnacodec.model.train --smoke          # must end with "smoke PASSED"

    # Before each long job, add a line to GPU_JOBS.md (owner, job, start, expected end, log path).

    # 1) Real-data run, works without the simulator (Microsoft train split, ~7,500 clusters):
    nohup uv run python -m dnacodec.model.train --source real --model base \\
        --steps 40000 --batch-size 128 --workers 4 --run-name real_base > real_base.out 2>&1 &

    # 2) Simulated pretraining over a wide range of channels (the main job):
    nohup uv run python -m dnacodec.model.train --source sim --model base \\
        --steps 100000 --batch-size 128 --workers 8 --run-name sim_base > sim_base.out 2>&1 &

    # 3) Fine-tune the pretrained model on real + simulated data:
    nohup uv run python -m dnacodec.model.train --source mixed --p-real 0.7 \\
        --init checkpoints/sim_base/best.pt --lr 1e-4 --warmup 500 \\
        --steps 20000 --batch-size 128 --workers 8 --run-name mixed_ft > mixed_ft.out 2>&1 &

    tail -f checkpoints/<run-name>/train.log   # eval every 1000 steps: val + held-out at 2/4/6/10/16 reads
    # crash or preemption: rerun the same command plus --resume checkpoints/<run-name>/last.pt

    # 4) Final numbers next to the baseline, same protocol and seeds as scripts/eval_real.py:
    uv run python scripts/eval_real.py                  # baseline column, once
    uv run python -m dnacodec.model.benchmark checkpoints/<run-name>/best.pt

Each run writes checkpoints/<run-name>/{train.log, metrics.jsonl, best.pt, last.pt,
step<N>.pt}. best.pt (best on the validation subset of the TRAIN split) is the checkpoint
for TransformerDecoder. Read the it/s in the log after a minute to get the real end time
and fix the GPU_JOBS.md line. If step time is too slow for the plan, use --model small.
"""
