"""Transformer decoder. Owner: Agent D.

Contents:
- data.py: training batches. ClusterPool (real train splits, or clusters from the loop),
  SimSource (random strands through dnacodec.simulator.simulate() with random profiles over
  a wide range of error rates, seeds via train_seed()), MixedSource. Coverage augmentation:
  every training cluster keeps a random 1..16 reads, with extra weight on 1..6.
- net.py: ConsensusNet. Per-read transformer encoder (token + position + read-index
  embeddings), then strand_length learned output queries (+ shared position embedding +
  strand-length embedding) through transformer decoder layers that self-attend over output
  positions and cross-attend to every read token (Perceiver style). Logits over ACGT per
  position. Strands up to 160 bases, up to 16 reads, padding and empty reads masked.
  Presets: tiny (0.2M, smoke), small (3.8M), base (9.6M, default), large (~21M).
- train.py: CLI. --smoke memorization check, full runs with --source real|sim|mixed.
  Validation carved from the real TRAIN split picks best.pt; the real held-out split is
  only logged, at 2/4/6/10/16 reads.
- decoder.py: TransformerDecoder(checkpoint_path) implementing dnacodec.types.Decoder.
- finetune.py: finetune(checkpoint_path, clusters, references, steps, out_path) -> Path,
  used by the loop. Only pass training clusters (train seeds or real train splits).

Import the submodules directly (dnacodec.model.decoder etc.); this package does not import
torch on its own.

GPU run
-------
On the GPU machine (Linux + CUDA, bf16 autocast is used automatically on cuda)::

    git clone https://github.com/timarnoldev/hackmit2026.git && cd hackmit2026
    uv sync --extra train
    scripts/download_data.sh --full
    uv run pytest -q
    uv run python -m dnacodec.model.train --smoke          # must print "smoke PASSED"

    # 1) Real-data run, works today (Microsoft train split, about 7,500 clusters):
    nohup uv run python -m dnacodec.model.train --source real --model base \\
        --steps 40000 --batch-size 128 --workers 4 --run-name real_base > real_base.out 2>&1 &

    # 2) Simulated pretraining, once Agent A's simulate() is merged:
    nohup uv run python -m dnacodec.model.train --source sim --model base \\
        --steps 100000 --batch-size 128 --workers 8 --run-name sim_base > sim_base.out 2>&1 &

    # 3) Fine-tune the pretrained model on real + simulated data:
    nohup uv run python -m dnacodec.model.train --source mixed --p-real 0.7 \\
        --init checkpoints/sim_base/best.pt --lr 1e-4 --warmup 500 \\
        --steps 20000 --batch-size 128 --workers 8 --run-name mixed_ft > mixed_ft.out 2>&1 &

    tail -f checkpoints/<run-name>/train.log           # eval lines every 1000 steps
    # crash or preemption: add --resume checkpoints/<run-name>/last.pt to the same command

Each run writes checkpoints/<run-name>/{train.log, metrics.jsonl, best.pt, last.pt,
step<N>.pt}. best.pt is the checkpoint to hand to TransformerDecoder. Check GPU memory with
nvidia-smi in the first minutes; if it runs out, halve --batch-size.
"""
