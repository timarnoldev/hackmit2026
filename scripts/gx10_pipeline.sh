#!/bin/bash
# Runs the next steps on the GX10 by itself, independent of any SSH session.
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
esac
