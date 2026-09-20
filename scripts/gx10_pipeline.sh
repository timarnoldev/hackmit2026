#!/bin/bash
# Runs the next steps on the GX10 by itself, independent of any SSH session.
# Start a mode inside tmux, e.g.: tmux new -d -s ft './pipeline.sh finetune'
cd ~/hackmit2026 && export PATH=$HOME/.local/bin:$PATH
log() { echo "$(date "+%F %T") $*" | tee -a pipeline.log; }
case "$1" in
  after_loops)
    while tmux has-session -t loop_np 2>/dev/null || tmux has-session -t loop_il 2>/dev/null; do sleep 30; done
    log "loops finished, starting experiments for run1"
    uv run --no-sync python scripts/run_experiments.py --run-id run1 --workers 14 > run1_experiments.out 2>&1
    log "experiments finished (exit $?), see run1_experiments.out and results/run1/summary.json" ;;
  after_train)
    while tmux has-session -t train 2>/dev/null; do sleep 30; done
    log "pretraining finished, benchmarking sim_small against the baseline on real held-out data"
    uv run --no-sync python -m dnacodec.model.benchmark checkpoints/sim_small/best.pt > sim_small_benchmark.out 2>&1
    log "benchmark finished (exit $?), see sim_small_benchmark.out" ;;
  finetune)
    # Fine-tune the pretrained small model on real Microsoft train clusters mixed with simulated data.
    log "fine-tuning sim_small on mixed real and simulated data"
    uv run --no-sync python -m dnacodec.model.train --source mixed --p-real 0.7 --model small \
      --init checkpoints/sim_small/best.pt --lr 1e-4 --warmup 200 --steps ${STEPS:-5000} \
      --batch-size 128 --workers 4 --run-name mixed_small > mixed_small.out 2>&1
    log "fine-tuning finished (exit $?), benchmarking"
    uv run --no-sync python -m dnacodec.model.benchmark checkpoints/mixed_small/best.pt > mixed_small_benchmark.out 2>&1
    log "benchmark finished (exit $?), see mixed_small_benchmark.out" ;;
  transformer_run)
    # Loops and experiments with the transformer decoder: ./pipeline.sh transformer_run RUN_ID CHECKPOINT
    RUN=${2:?run id}; CKPT=${3:?checkpoint}
    log "loops with transformer $CKPT for $RUN"
    uv run --no-sync python scripts/run_loop.py --profile nanopore_budget --run-id "$RUN" --decoder transformer --checkpoint "$CKPT" --workers 8 > "${RUN}_nanopore.out" 2>&1 &
    uv run --no-sync python scripts/run_loop.py --profile illumina_standard --run-id "$RUN" --decoder transformer --checkpoint "$CKPT" --workers 8 > "${RUN}_illumina.out" 2>&1
    wait
    log "loops finished for $RUN, starting experiments"
    uv run --no-sync python scripts/run_experiments.py --run-id "$RUN" --workers 16 > "${RUN}_experiments.out" 2>&1
    log "experiments finished for $RUN (exit $?)" ;;
esac
