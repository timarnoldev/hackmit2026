# GPU jobs on the GX10

One shared GPU. Add a line before starting a long job, remove it when done.

| Owner | Job | Started | Expected end | Command or log |
|---|---|---|---|---|
| Architect (Claude) | Transformer pretraining, small model, sim data, 20k steps, batch 128 | 12:48 | ~15:15 | tmux `train`, `~/hackmit2026/sim_small.out` |
| Architect (Claude) | Loop run1 nanopore_budget, baseline decoder (CPU, 7 workers) | 12:48 | ~14:00 | tmux `loop_np`, `results/run1/log.txt` |
| Architect (Claude) | Loop run1 illumina_standard, baseline decoder (CPU, 7 workers) | 12:48 | ~13:30 | tmux `loop_il` |
