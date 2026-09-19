# How errors are made and how they are fixed

This document covers the two halves of the error story:

1. **The error engine** (channel simulator): how we turn stored strands into realistic noisy reads, and how it's calibrated on real sequencing data.
2. **The error correction chain**: how a file survives those errors, from decoder to checksum to Fountain code.

---

## Part 1: The error engine

Code: `dnacodec/simulator.py` (Simulator A, used for everything), `dnacodec/simulator_b.py` (Simulator B, only for the firewall test), `scripts/calibrate.py` (fitting on real data), profiles in `profiles/*.json`.

### Why we need it

We can't synthesize and sequence DNA at a hackathon. Every time the loop tries a new encoding, something has to "read" it. The simulator plays the role of the lab: it takes the strands the encoder wrote and returns what a sequencer would return. It's only useful if it's as hard as reality in the same ways, so it's fit to real reads and checked against them.

### Input and output

```python
from dnacodec.simulator import simulate
from dnacodec.profiles import load_profile

clusters = simulate(strands, load_profile("nanopore_budget"), seed=42)
```

- **Input:** the encoded strands, a situation profile (the channel), and a seed.
- **Output:** one cluster per strand, in the same order. A cluster is the list of noisy reads of that strand. An empty list means the strand was lost.
- The same seed always gives the same output.
- Speed: about 250,000 reads per second on a laptop, vectorized with numpy.

### What happens to each strand, step by step

```
strand
  │
  ├─ 1. Survives at all?         dropout: lost with probability
  │                               total_dropout + gc_dropout_factor × (GC deviation from 0.5) / 0.1
  │
  ├─ 2. How many reads?           negative binomial with mean coverage_mean and shape coverage_dispersion
  │                               (some strands get many reads, some very few, like real sequencing)
  │
  ├─ 3. Shared errors             once per strand and position, drawn for all reads of this strand:
  │                               a random multiplier (lognormal, mean 1, sigma position_rate_spread)
  │                               → errors that every copy shares and more reads can't average away
  │
  └─ 4. For each read:
        ├─ read quality           one random multiplier per read (lognormal, mean 1, sigma read_quality_spread)
        ├─ (optional) malformed   with probability malformed_read_rate the read comes from another strand
        └─ for each base:         delete it, insert a random base before it, substitute it, or copy it
```

### The per-base error probability

For each base `i` of a strand and each error type (substitution, insertion, deletion):

```
p(error at i) = base rate for this type
              × run multiplier(i)       deletions only: grows with the length of the letter run i is in
              × position ramp(i)        rises linearly from 1 at the start to end_factor at the end
              × context multiplier(i)   depends on the 5 letters centered on i (from real reads)
              × shared multiplier(i)    random per strand and position, same for all reads
              × read quality            random per read
```

The three probabilities together are capped at 0.5 per base. Then each base independently gets a deletion, an insertion, a substitution, or nothing.

What each factor stands for, and why it's there:

| Factor | Profile field | What it models | Evidence from real Nanopore reads |
|---|---|---|---|
| Base rates | `sub_rate`, `ins_rate`, `del_rate` | Overall error level of the technology | Measured directly |
| Run multiplier | `homopolymer_run_factors` | Nanopore struggles to count identical letters in a row | Deletions rise 8x from runs of 1 to runs of 6; substitutions barely change, so it applies to deletions only |
| Position ramp | `end_factor` | Reads get worse toward the end | End is about 1.2x the start on Microsoft data |
| **Context multiplier** | `context_table` | Certain local sequences are systematically misread | The centered 5-mer explains 30 to 45% of the position-to-position variance in error rates, on held-apart data, in two independent datasets |
| Shared multiplier | `position_rate_spread` | The remaining systematic errors that all reads of a strand share | Without it the simulator was 15 points too easy at 6 reads |
| Read quality | `read_quality_spread` | Some reads are much cleaner than others | Real per-read error rates vary widely |
| Dropout | `dropout_rate`, `gc_dropout_factor`, `decay_per_year` | Strands lost in synthesis, storage, or PCR | Set per situation |
| Coverage | `coverage_mean`, `coverage_dispersion` | Uneven number of reads per strand | Shape fit on Microsoft cluster sizes |

### The context table: the learnable part

The context table is what makes this simulator useful for the project's second claim tier. It holds one multiplier per 5-letter pattern (4^5 = 1,024 patterns) and per error type, measured on real train reads. Examples from real data:

- Substitutions are much more frequent around `CGGG` and `CCCG`.
- Deletions are frequent right after `CCCT` and `GGGA`.
- Insertions are frequent around `CGAA`.
- Patterns like `GATCC` and `AGTTC` almost never cause errors.

The same families lead in both real datasets (Microsoft and DNAformer), and a table fit on one predicts the other's error hot spots with AUC 0.71 to 0.79. These are real properties of the sequencing channel, and the hand-written rules (run length, GC window) don't cover them. The risk model can learn to avoid them. Everything the simulator does *randomly* (the shared multiplier, read quality) can't be learned from sequence, on purpose.

Tables live in `profiles/context/`. Bases within 2 letters of a strand end use multiplier 1.

### Calibration

`scripts/calibrate.py` fits the profile on **train splits only**, in this order:

1. Base rates, position ramp, coverage shape, from aligning real reads to their references.
2. The 5-mer context table, matched per pattern. Run multipliers are fit only for runs of 4 or more, so the table and the run factors don't both claim the same deletions.
3. The two random spreads (shared and per read), by matching the baseline decoder's accuracy curve.

**Acceptance test:** the majority vote baseline decoder must score about the same on simulated reads as on real ones, on clusters no part of the fit touched:

| Reads per strand | 2 | 4 | 6 | 10 | 16 |
|---|---|---|---|---|---|
| Real Nanopore (Microsoft) | 4.5% | 39.9% | 65.3% | 83.5% | 89.1% |
| Simulator A (calibrated) | 4.5% | 40.8% | 63.0% | 83.5% | 90.5% |
| First simulator (rates only) | 0.4% | 50.0% | 83.7% | 96.4% | 97.5% |

Simulator A is within 2.3 points of reality at every read count. The first version, which only matched average error rates, was far too easy. Matching averages isn't enough; the structure of the errors matters.

Current `nanopore_budget` values (fit on Microsoft train): substitution 1.52%, insertion 1.89%, deletion 2.20% per base; run multipliers for runs of 1 to 7 of 1.0, 1.0, 1.0, 1.05, 2.28, 5.02, 6.66 (deletions); end ramp 1.2; read quality spread 0.5; shared spread 0.8; no malformed reads.

`illumina_standard` is fit on DNAformer Illumina train reads. Illumina errors are about 100x rarer and show no context effect worth a table: almost all position-to-position variation there is sampling noise.

Known deviations: the simulator has 30 to 50% more error hot spots than real reads (the price of matching the accuracy curve), and the negative binomial gives about 2x too many strands with 3 or fewer reads.

### Simulator B: the firewall

If we optimize a codec against Simulator A, the codec might exploit a quirk of Simulator A instead of a real property of DNA. Simulator B builds the same channel from **different mechanisms** and is never used for optimization, only to check that gains survive:

| Effect | Simulator A | Simulator B |
|---|---|---|
| Rates | Profile rates | Substitutions ×1.3, insertions ×0.75, deletions ×1.2 |
| Position | Linear ramp | Cubic rise toward the end plus a bump at the start |
| Letter runs | Per-base deletion multiplier | One run-shortening event per run and read, plus run extensions |
| Shared errors | Independent per position | A few damaged contiguous segments per strand |
| Read quality | Lognormal per read | Gamma per read, same variance |
| Errors within a read | Independent per base | Bursty (a two-state Markov chain) |
| Substitutions | Uniform over the other 3 letters | Transitions (A↔G, C↔T) twice as likely |
| Insertions | Random letter | Half duplicate the neighbor, half random |
| Malformed reads | Whole read from another strand | Chimeras: start of own strand, end of another |
| Context table | Fit on Microsoft | Fit on DNAformer (clipped and rescaled) |

On real-reads accuracy, Simulator B is 2 to 7 points easier than Simulator A. It isn't tuned to match; the mismatch is the test.

---

## Part 2: The error correction chain

A file only comes back if every layer does its job. Each layer turns one kind of error into something the next layer can handle.

```
noisy reads of each strand
  │
  │  1. DECODER (transformer or majority vote baseline)
  │     cluster of noisy reads → one best-guess strand
  │     fixes: random substitutions, insertions, deletions that differ between reads
  │     can't fix: errors all reads share; lost strands
  ▼
best-guess strand (may still be wrong)
  │
  │  2. CHECKSUM (CRC-16 inside every strand)
  │     wrong strand → detected and thrown away
  │     turns "silently wrong" into "missing", which is much easier to handle
  ▼
correct strands + missing strands
  │
  │  3. FOUNTAIN CODE (LT code with redundancy)
  │     any large enough subset of strands rebuilds the file
  │     fixes: missing strands, as long as not too many are missing
  ▼
file (checked with a CRC-32 over the whole file; on mismatch nothing is returned, never a wrong file)
```

### 1. Decoder

Takes up to 16 noisy reads of a strand and outputs one strand of the right length. Details in [MODELS.md](MODELS.md). The baseline aligns every read to a draft, votes per position (letter or deletion) and per gap (insertion), rebuilds the draft, and repeats.

### 2. Checksum

Every strand carries a CRC-16 over its seed and payload. A decoded strand whose checksum doesn't match is treated exactly like a lost strand. A wrong strand still passes the check with probability 1 in 65,536. For that case, recovery tracks which strands each rebuilt chunk came from, finds the culprit, drops it, and solves again.

### 3. Fountain code

- The file is cut into chunks. Each strand holds a seed (its first 16 letters) and the XOR of a few chunks, chosen by that seed (robust soliton distribution).
- The encoder writes `ceil(chunks × (1 + redundancy))` strands. At the default redundancy of 0.3, up to 1 − 1/1.3 = 23% of strands can be lost in theory.
- Recovery first uses peeling (solve chunks one at a time), then Gaussian elimination if peeling gets stuck.
- The payload is scrambled per seed, so even a file of all zeros produces balanced, varied strands.

### Where the codec choices come in

The encoder generates several candidate strands per slot (different seeds give different strands for the same data). The hard rules (max run length, GC window) reject some. A scorer ranks the rest and the safest one is kept:

- **Rule scorer** (the default): every candidate that passes the rules is equally good.
- **Risk model** (claim tier 2): prefers candidates the decoder is less likely to get wrong on this channel.

The loop tunes which rules are on, the redundancy, and the risk threshold per channel (claim tier 1), measured by file recovery over 300 trials.

### Why the numbers work the way they do

With the 20 KB test file (1,239 strands at default settings), the file comes back when the share of correctly decoded, non-lost strands stays comfortably above 1/(1 + redundancy), which is 77% at redundancy 0.3. With the baseline decoder and default settings:

| Channel | Reads per strand needed for 300 of 300 trials |
|---|---|
| Illumina | 8 |
| Nanopore | 16 (measured with the earlier, easier simulator; higher with the calibrated one) |

Lowering these numbers at the same bits per base, or raising bits per base at the same number of reads, is what the project measures.
