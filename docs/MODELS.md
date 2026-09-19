# The AI models

The project has exactly two learned models. Everything else (encoder, simulator, settings search, evaluation) is classic code.

| Model | Question it answers | Input | Output | Used by |
|---|---|---|---|---|
| **Transformer decoder** (`ConsensusNet`) | "What was the original strand?" | Up to 16 noisy reads of one strand | One strand of exactly `strand_length` letters (or `None`) | Evaluation, the loop, the demo |
| **Risk model** (1D CNN) | "How likely is this strand to be decoded wrongly on this channel?" | One or more candidate strands | One number in [0, 1] per strand | The encoder, when choosing between candidate strands |

Both are plugged in through the shared interfaces in `dnacodec/types.py` (`Decoder` and `Scorer`), so either can be swapped for the classic alternative: the majority vote baseline decoder, or the hand-written rule scorer.

---

## 1. Transformer decoder

Code: `dnacodec/model/` (`net.py` model, `decoder.py` inference, `train.py` training, `finetune.py` per-channel adaptation, `benchmark.py` real-data comparison).

### What it does

Sequencing returns many noisy copies ("reads") of each stored strand. Each read has wrong, extra, or missing letters at different places. The decoder takes the reads of one strand and reconstructs the original strand. This task is called trace reconstruction.

### Output, exactly

```python
from dnacodec.model.decoder import TransformerDecoder

decoder = TransformerDecoder("checkpoints/mixed_ft/best.pt")
strands = decoder.decode(clusters, strand_length=110)
```

- `clusters`: a list of clusters, each a list of read strings over `ACGT`. Reads can differ in length.
- Returns a list of the same length as `clusters`, in the same order. Each entry is either:
  - a string of **exactly** `strand_length` letters over `ACGT`: the model's best guess for the original strand, or
  - `None`, if the cluster has no reads (the strand was lost).
- Internally the network produces, for every output position, a probability over the four letters (logits of shape `clusters × strand_length × 4`). The returned letter is the most likely one at each position.
- The decoder never says "I'm not sure". A wrong guess is still a full-length strand. Catching wrong guesses is the job of the per-strand checksum in the encoder, which turns them into lost strands (see [ERRORS.md](ERRORS.md)).

Behavior details:

- Clusters with more than 16 reads keep the 16 reads whose length is closest to `strand_length`.
- Strands up to 160 letters are supported. Our data uses 110 (Microsoft) and 140 (DNAformer).
- `main_process_only = True`: the evaluation code gathers the clusters of many trials and calls `decode()` once in the main process. `decode()` then works through them in GPU-sized chunks (at most 256 clusters and 1,024 reads per chunk).

### Architecture

```
reads of one cluster (up to 16, each up to ~180 letters)
  │
  │  embedding per letter: letter + read number + position
  │  (position is anchored at both ends of the read, because insertions and
  │   deletions shift everything after them; counting from both ends keeps
  │   the tail of the strand locatable)
  ▼
Transformer encoder, run on each read separately
  │
  ▼
all read tokens of the cluster
  │
  │  one learned query per output position (plus position code and strand length)
  │  Transformer decoder layers: queries attend to each other and to every read token
  │  (Perceiver-style cross-attention)
  ▼
logits over A, C, G, T for each output position  →  most likely letter per position
```

| Size | d_model | Heads | Encoder layers | Decoder layers | Parameters |
|---|---|---|---|---|---|
| tiny (smoke test only) | 64 | 4 | 1 | 2 | 0.2M |
| small | 192 | 6 | 3 | 4 | 3.8M |
| **base (default)** | 256 | 8 | 4 | 6 | 9.6M |
| large | 384 | 8 | 4 | 6 | 21.5M |

### Training

- **Loss:** cross-entropy per position against the true strand.
- **Data sources:** `sim` (unlimited clusters from the channel simulator across a wide range of error rates), `real` (Microsoft train clusters), or `mixed`.
- **Plan:** pretrain on `sim`, then fine-tune on `mixed` (70% real). Per channel, the loop fine-tunes further with `finetune()`.
- **Coverage augmentation:** each training cluster keeps a random 1 to 16 reads, and half the time at most 6. That weights low coverage, where the baseline is weakest.
- **Seeds:** all training data uses `train_seed()`. Checkpoints are selected on a validation subset of the train split. Held-out numbers are only logged, never used for selection.

### Status

The code is complete and tested. The model is **not trained yet**: 600 steps on a busy laptop showed only that the pipeline runs (2% exact strands at 16 reads, vs 91% for the baseline). Full training runs on the GX10 (see README, "Running on the GX10"). Measure it against the baseline with `python -m dnacodec.model.benchmark <checkpoint>`.

**Fallback:** if it doesn't beat the baseline in time, the whole pipeline runs with the majority vote baseline. Both claim tiers compare codecs with the same decoder, so they hold with any fixed decoder.

---

## 2. Risk model

Code: `dnacodec/risk.py`, training script `scripts/train_risk.py`.

### What it does

The Fountain encoder can produce many different candidate strands for the same slot at no cost in density. The risk model predicts, for each candidate, how likely the decoder is to get it wrong on this channel. The encoder then keeps the safest candidate. This is how decoder failures steer encoding (claim tier 2).

### Output, exactly

```python
from dnacodec.risk import load_risk_model

risk = load_risk_model("checkpoints/risk_nanopore_budget.pt")
scores = risk(["ACGT...", "GGTA..."])   # numpy array, one value per strand
```

- `risk(strands)` returns a numpy array of shape `(len(strands),)` with values in **[0, 1]**: the predicted fraction of decoding attempts that fail for that strand, on this channel, at this channel's read budget, with the decoder it was trained against.
- Higher means riskier. It's a rate, not a yes or no: 0.35 means "about 35% of noisy readouts of this strand would be decoded wrongly".
- The model object is directly usable as the encoder's `Scorer`: `encode(data, settings, scorer=risk)`. With `settings.risk_threshold` set, candidates above the threshold are rejected outright.
- Fast: about 0.5 s for 8,192 strands on a GPU or Apple MPS.
- `risk.top_kmers(k=6, n=20)` returns the most dangerous short patterns with their risk, for the dashboard ("what the encoder learned"). It works by inserting each pattern into random background strands and measuring the change in predicted risk.

A risk model is specific to **one channel, one read budget, and one decoder**. The Nanopore model's numbers mean nothing on Illumina. That's the point: the same strand can be safe on one channel and risky on another.

### How it's trained

```
generate_strands()       controlled training strands: uniform random, plus strands with deliberately
                         inserted letter runs (2 to 10), skewed GC content (0.15 to 0.85), repeats
                         and motifs. Never Microsoft references (their README says they're not random).
        │
label_failure_rates()    simulate each strand K times (K = 32) at the channel's read budget,
                         decode each noisy cluster with the frozen decoder,
                         label = fraction of clusters decoded wrongly.
                         Lost strands (no reads) are left out: loss isn't the decoder's fault.
        │
RiskModel.fit()          1D CNN over one-hot letters (4 residual blocks, 48 channels,
                         masked mean and max pooling), trained with binary cross-entropy
                         on the soft labels.
```

Why rates instead of single failures: a strand can fail once by bad luck (few reads, a burst of errors). Training on single outcomes would teach the model noise. Averaging over K simulations gives a stable target.

A simpler `KmerRiskModel` (logistic regression on short-pattern counts) has the same interface and serves as a fallback.

### Status and current numbers

Trained locally for both core channels (baseline decoder, 12,000 strands, K = 32):

| Test | Result |
|---|---|
| Nanopore, held-out simulated strands (mixed set) | AUC 0.91, Spearman 0.76 |
| Nanopore, held-out uniform random strands | AUC 0.65 |
| Illumina | Predicted risk is flat at about 1%, correctly: sequence doesn't matter on that channel |
| Real Nanopore reads, held-out (firewall preview) | AUC 0.54, weak |

What it has learned so far: risk rises steeply with the longest run of identical letters (0.36 for runs of 3 or less, 0.81 for runs of 8 or more). That was the only sequence effect in the simulator it was trained on, and the hand-written homopolymer rule already covers it.

**Next:** the simulator now includes sequence-context error patterns measured on real reads (see [ERRORS.md](ERRORS.md)). The risk model will be retrained on that simulator. Then it can learn patterns the hand rules don't cover, which is what claim tier 2 needs. The real-data AUC is the check for whether it learned something real.
