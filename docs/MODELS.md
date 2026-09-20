# The AI models

The project has exactly two learned models. Everything else (encoder, simulator, settings search, evaluation) is classic code.

| Model | Question it answers | Input | Output | Used by |
|---|---|---|---|---|
| **Polisher** (`PolishNet`, a 1D CNN) | "What was the original strand?" | The classic decoder's draft plus its vote columns, from up to 16 noisy reads | One strand of exactly `strand_length` letters (or `None`) | Evaluation, the loop, the demo |
| **Risk model** (1D CNN) | "How likely is this strand to be decoded wrongly on this channel?" | One or more candidate strands | One number in [0, 1] per strand | The encoder, when choosing between candidate strands |

Both are plugged in through the shared interfaces in `dnacodec/types.py` (`Decoder` and `Scorer`), so either can be swapped for the classic alternative: the majority vote baseline decoder, or the hand-written rule scorer. A third model, a from-scratch transformer, lost to the classic baseline and is not used; section 3 records what it was and what it cost.

Every accuracy figure below is registered with its provenance in [NUMBERS.md](NUMBERS.md).

---

## 1. Decoder: the learned polisher

Code: `dnacodec/model/polish.py` (model and `PolishDecoder`), training in `scripts/train_polish.py`, comparison against the baseline in `dnacodec/model/benchmark.py --polish`.

### What it does

Sequencing returns many noisy copies ("reads") of each stored strand: wrong letters, extra letters, missing letters, each read different. Reconstructing the original strand from them is called trace reconstruction.

We split that in two: the classic majority vote decoder does the **alignment** (which letter of read 3 belongs to position 47), and a small learned model does the **correction** of what's left. That's how Nanopore assembly polishers work, and it is the reason this model trains in 13 minutes where a from-scratch transformer, which had to learn the alignment as well, did not get there at all (section 3).

### Output, exactly

```python
from dnacodec.model.polish import PolishDecoder

decoder = PolishDecoder("checkpoints/polish/polish.pt")
strands = decoder.decode(clusters, strand_length=110)
```

- Same contract as any `Decoder`: one entry per cluster, in order; each is a string of **exactly** `strand_length` letters over ACGT, or `None` for an empty cluster.
- `name = "polish"`, `main_process_only = True` (it decodes on the GPU in the main process and batches internally, 512 clusters per batch by default).
- Speed: about 2,300 to 4,700 clusters per second on the GX10, roughly 1.3 to 1.7 times the cost of the baseline, which dominates through the shared draft building.

### How it works

```
cluster of up to 16 reads
   │  classic: pick a medoid draft, align every read to it, count votes per position,
   │  rebuild, repeat 3 times, force the draft to strand_length
   ▼
draft (already about 90% correct at 16 reads) + vote columns
   │  17 features per draft position:
   │    votes for A, C, G, T, votes for "this position is missing in the read",
   │    votes for "an extra letter in the gap before this position" and which letter,
   │    coverage, the draft base, agreement, relative position
   ▼
dilated 1D CNN: stem (kernel 5) + 8 residual blocks with dilations 1,2,4,8,1,2,4,8,
128 channels, about 0.8M parameters, receptive field about 65 positions
   │
   ├─▶ op head, 6 classes per position: keep, substitute to A/C/G/T, delete
   └─▶ insert head, 5 classes per gap: nothing, insert A/C/G/T
   │
   ▼  apply_edits: draft and truth both have exactly strand_length bases, so a correct
      edit script has as many deletions as insertions. Keeping the k most confident of
      each makes the output exactly strand_length by construction.
corrected strand
```

**Why it beats plain voting:** the model may overrule the majority, because it sees two things voting ignores. First the **local context**, about 65 neighbouring positions, so it learns that Nanopore drops a letter after CCCT more often than elsewhere, which is exactly the 5-mer context effect measured on real reads (see [ERRORS.md](ERRORS.md)). Second the **coverage**, so "4 against 2" means something different at 6 reads than at 16.

### Training

- **Data:** 577k examples in 18 seconds: 400k simulated clusters across randomly drawn channels (wide error-rate range, coverage 1 to 16 weighted toward low coverage, strand lengths 110 and 140) plus 24 passes over the real Microsoft **train** clusters. Drafts and features are built in 8 worker processes.
- **Loss:** cross-entropy on both heads, labels from `Levenshtein.editops(draft, truth)`.
- **Steps:** 10,000, about 13 minutes on the GX10. Validation accuracy plateaued around step 8,000.
- **Thresholds:** the confidence thresholds for applying deletions and insertions are tuned on a validation carve of the train split, separately for clusters with at most 3 reads and for larger ones.
- Held-out data is never used for training or tuning.

### This is the decoder used in all results

Measured on the real Microsoft **held-out** split (1,996 clusters), exact strands, protocol of
`scripts/eval_real.py`, averaged over 20 independent read draws per point. The full table with
error bars is [NUMBERS.md](NUMBERS.md) section 1; the headline is 88.1% exact strands at 6 reads
for the best inference variant against 67.2% for the baseline.

The gain is largest at 4 to 6 reads, which is the range the loop operates in. At 2 reads it's a tie: the draft is essentially a single read there, so there is nothing to correct with.

**On simulated channels:** on `nanopore_budget` it gains everywhere (6 reads: 55.4% vs 48.3%). On `illumina_standard` it matches the baseline to within 0.2 points, because that channel is clean enough that the draft is already right.

**Ceiling:** at 6 reads the polisher fails on 18.6% of strands, and 19.6% of those also fail with all 27 reads. So 4.7% of strands are unrecoverable in principle (errors shared by all reads, plus malformed clusters in the dataset), and about 14.9% is headroom at 6 reads.

---

## 2. Risk model

Code: `dnacodec/risk.py`, training script `scripts/train_risk.py`.

### What it does

The Fountain encoder can produce many different candidate strands for the same slot at no cost in density. The risk model predicts, for each candidate, how likely the decoder is to get it wrong on this channel. The encoder then keeps the safest candidate. This is how decoder failures steer encoding (claim tier 2).

### Output, exactly

```python
from dnacodec.risk import load_risk_model

risk = load_risk_model("checkpoints/risk/risk_nanopore_budget.pkl")
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

### What it measures out at

Trained for both core channels on the simulator that carries the real-read context table
(baseline decoder, 12,000 strands, K = 32). Held-out simulated strands, Illumina flatness and the
transfer to real reads are all registered in [NUMBERS.md](NUMBERS.md) section 6; the patterns the
model names are read back out of it in [LEARNED_RULES.md](LEARNED_RULES.md).

The short version: it rediscovers the homopolymer effect by itself, puts its threshold at a run of
4 to 5 rather than the field's 3, rates G and C runs above A and T runs, treats the channel's
real deletion contexts as dangerous and its substitution contexts as harmless, and returns a flat
zero on Illumina. On real Nanopore reads its ranking holds at AUC 0.69 once coverage is held
fixed, against 0.66 for the homopolymer rule alone.

## 3. What didn't work: a from-scratch transformer

`ConsensusNet` (9.6M parameters, `dnacodec/model/net.py`, `train.py`, `decoder.py`) tried to learn
the whole task end to end, alignment included, instead of correcting a classic draft. After 11,300
steps on simulated data it reached 1.4% / 5.6% / 10.8% exact strands at 2 / 6 / 16 reads on real
Microsoft validation data, against 4.8% / 68.1% / 90.6% for the baseline, and it was improving too
slowly to catch up within the time budget. It was dropped in favour of the polisher, which is 12
times smaller, trains in 13 minutes and beats the baseline. The code stays in the repository,
unused, because it is the measurement behind that decision.

The lesson: the model belongs where the knowledge is missing, not where the problem is hardest.
The classic algorithm is very good at alignment. What it lacks is knowledge about the channel, and
that is what the small CNN supplies.

