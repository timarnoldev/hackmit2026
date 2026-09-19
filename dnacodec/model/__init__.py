"""Transformer decoder. Owner: Agent D.

Expected contents:
- data.py: builds training batches from dnacodec.simulator with train_seed() seeds,
  sampling random profiles in a wide range of error rates, and from real train splits
- net.py: the model. Input: up to 16 reads per cluster, padded, strands around 110 to 140 bases.
  Output: per-position distribution over ACGT for strand_length positions
- train.py: CLI with --smoke (tiny data, must reach near-zero loss) and full pretraining,
  checkpoints to checkpoints/ every 30 minutes, held-out eval logged at every checkpoint
- decoder.py: TransformerDecoder implementing dnacodec.types.Decoder, loads a checkpoint
- finetune(profile, ...) used by the loop
"""
