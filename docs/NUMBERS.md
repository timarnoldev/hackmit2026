# Every number we quote, and where it comes from

One page for the pitch and for judge questions. If a number is not in here, don't say it on stage.

Status legend: ✅ measured and independently re-checked · ☑️ measured once · ⏳ pending (run in progress) · ⚠️ superseded or unreliable

---

## 1. The decoder, on real reads

**Setup:** Microsoft clustered Nanopore reads, **held-out split** (1,996 clusters; every 5th cluster, never used for training, tuning or calibration). Each cluster subsampled to at most N reads with fixed seeds from `heldout_seeds()`. Metric: **share of strands reconstructed exactly**, all 110 letters correct. Protocol: `scripts/eval_real.py`, numbers from `dnacodec.evaluate.evaluate`.

| Reads per strand | 2 | 4 | 6 | 10 | 16 | full (mean 27) | Status |
|---|---|---|---|---|---|---|---|
| Baseline (align + majority vote) | 4.8% | 39.2% | 68.0% | 85.3% | 90.5% | 90.3% | ✅ |
| Polisher, default (used in the loops) | 5.2% | 56.0% | 83.3% | 93.2% | 95.4% | 95.3% | ✅ |
| Polisher, best variant (3 drafts, 2 rounds, gain-mode edits) | 7.0% | 66.1% | 88.8% | 96.2% | 97.2% | 97.3% | ☑️ |

- **Independent re-check** (architect, own counting code, different subsample seeds): baseline 5.0 / 41.0 / 67.0 / 83.0 / 90.0%, polisher default 5.9 / 56.8 / 82.5 / 92.7 / 95.1%. Differences of about one point against the table above are subsample noise.
- **Headline sentence:** at 6 reads per strand the learned decoder reconstructs 88.8% of strands exactly, against 68.0% for the classic method, on real Nanopore data it never saw.
- **Speed:** baseline about 7,600 clusters/s, polisher default about 3,800, best variant about 860 (single process, GX10).

**Ceiling, measured:** with *all* reads the polisher still fails on **4.7%** of strands, so those are unrecoverable in principle (errors shared by every read, plus malformed clusters). At 6 reads it fails on 18.6%, of which 19.6% are in that hopeless group. ✅

**Reference point:** a single read is exactly right **1.1%** of the time. That is what you get without a decoder. ✅

## 2. The simulator, against reality

**Setup:** same held-out clusters; the baseline decodes real reads and simulated reads of the same references, same number of reads per strand.

| Reads per strand | 2 | 4 | 6 | 10 | 16 | Status |
|---|---|---|---|---|---|---|
| Real | 4.9% | 39.8% | 66.5% | 84.5% | 90.1% | ✅ |
| Simulator A (calibrated) | 3.6% | 40.3% | 63.7% | 83.0% | 90.3% | ✅ |

Within about 3 points everywhere, and where it deviates it is slightly harder than reality. Calibration itself used train data only.

**Error rates in the data:** about 1.7% insertions, 2.0% deletions, 2.2% substitutions per base, so roughly 5.8% of letters are wrong. ☑️ (dataset README plus our own measurement)

## 3. Sequence context, the reason a risk model can exist

Errors shared by all reads of a strand, measured on held-apart halves of the train data:

| | Microsoft Nanopore | DNAformer Nanopore | Status |
|---|---|---|---|
| Share of between-position error variance explained by the centred 5-mer | 45% (64% with run length) | 66% | ☑️ |
| Hot-spot prediction, AUC | 0.85 / 0.80 / 0.90 (sub / ins / del) | 0.87 / 0.81 / 0.86 | ☑️ |
| Cross-dataset transfer (table fit on one, hot spots of the other) | AUC 0.71 to 0.79 | | ☑️ |

Examples that lead in both datasets: substitutions around CGGG and CCCG, deletions after CCCT and GGGA, insertions around CGAA. Illumina shows no context effect worth modelling.

## 4. The codec

- Test file: **20 KB** of random bytes, fixed seed, `dnacodec/testfile.py`. At default settings that is **1,239 strands at 1.202 bits per base**. ✅
- Dropout tolerance at redundancy 0.3: theory 23%, measured clean recovery up to **22%**. ☑️
- Recovery target: the file must come back in **all 300 held-out trials**. Searches use 50-trial screens plus a 300-trial re-check on train seeds, and a margin check at 10% fewer reads. ✅ (method)

## 5. Results of the tuning loop

**Run 2** (baseline decoder, 3 blocks × 100 held-out trials, selection margin on). ☑️

| | Nanopore | Illumina |
|---|---|---|
| Default codec, reads needed | 19.5 | 3.0 |
| Tier 1 (rules audited + redundancy tuned) | 5.5 reads at 0.64 bits/base | 5.5 reads at 1.50 bits/base |
| Best round, reads needed | 5.0 | 5.0 |
| Rule "max 3 identical letters" | see the strict measurement below | see below |
| Rule "GC 40 to 60%" | see the strict measurement below | see below |
| Redundancy default vs tuned | tuned better: 19.5 → 5.5 reads | tuned better: 1.20 → 1.50 bits/base |

### The rule audit, strict measurement ✅

Run 2's codecs re-measured with **3 blocks × 300 held-out trials** (`results/run2_strict`), the version to quote:

| Rule | Nanopore | Illumina |
|---|---|---|
| **Max 3 identical letters in a row** | **pays off: 19.5 reads with it, 24.5 without** | no measurable benefit (3.0 vs 2.5) |
| **GC between 40 and 60%** | no measurable benefit (19.5 vs 20.0) | **pays off: 3.0 reads with it, 4.0 without** |
| Redundancy, default vs tuned | tuned better: 19.5 → 5.5 reads | tuned better: 3.0 reads → 1.50 bits per base |

**The quotable sentence:** each of the two standard rules pays off on exactly one of the two channels and does nothing on the other. Nobody measures that today.

This also settles an earlier wobble: at 100 trials per block the Nanopore homopolymer verdict came out as "no measurable benefit", at 300 trials it is a clear 5 reads, matching run 1. Fewer trials made the test too weak, not the rule less real.

**Tier 2, measured directly** (same settings, same decoder, paired held-out trials, rule scorer vs learned risk model): ☑️

| Channel | Simulator | Candidates | Strand failures, rules | with learned selection | Difference (95% CI) |
|---|---|---|---|---|---|
| Nanopore, 6 reads | A | 32 | 51.96% | 48.01% | **−3.95 points** (−4.18 to −3.72) |
| Nanopore, 6 reads | **B (firewall)** | 32 | 50.92% | 46.76% | **−4.16 points** (−4.37 to −3.96) |
| Nanopore, 19.5 reads | A | 32 | 16.60% | 13.94% | −2.66 points |
| Illumina | A and B | 8 and 32 | 3.60% | 3.53% | about 0 |

The gain survives a structurally different simulator. In the coarse target metric it is worth about half a read.

⚠️ **Run 1 numbers are superseded.** Run 1 suggested 6 vs 8 reads on Nanopore; that was a winner's curse (best of hundreds of settings on the same trials). The final round of run 1 then missed the target on held-out data (297 of 300). Run 2 adds a margin check, three seed blocks and train-only selection. Say this openly; it is a strength, not a weakness.

## 6. The weak spot, stated before anyone asks

**Risk model on real reads: AUC 0.516**, essentially chance (held-out DNAformer Nanopore clusters, 24% decoder failures). ☑️ Known confounders: those references are constrained (98.6% have a longest run of 3 or 4, GC between 0.44 and 0.56), so the features the model uses barely vary there, and real failure is dominated by coverage (read count alone gives AUC 0.77). A stratified analysis is running. ⏳

Honest sentence: "On simulated channels the learned selection clearly helps, and it survives an independently built simulator. On real reads we cannot yet show that the ranking transfers."

## 7. The live demo

Nanopore channel, 6 reads per strand, 5 seeds fixed in advance (the first five training seeds, never searched for a winner): ☑️

| | Default codec | Tuned codec |
|---|---|---|
| File recovered | 0 of 5 | 4 of 5 |
| Density | 1.19 bits per letter | 0.63 bits per letter |

The tuned codec wins here by writing more spare strands, not by better rules. Say that; the page says it too.

## 8. The project

- About 11,000 lines of Python, about 2,500 of them tests; 226 tests green. ✅
- Two learned models: the polisher (0.8M parameters, 13 minutes of training) and the risk model (small CNN). The from-scratch transformer (9.6M parameters) failed and is documented. ✅
- Hardware: one ASUS Ascent GX10 (NVIDIA GB10, 20 ARM cores, 128 GB unified memory). ✅
- Data: two public datasets, Microsoft (MIT licence) and DNAformer/Technion (CC BY 4.0). No wet lab. ✅

## Pending, will be filled tonight

| Run | What it adds | Status |
|---|---|---|
| `run3` | Loops and experiments with the polisher as decoder: all read counts drop, ablation A vs B becomes meaningful | ⏳ |
| `run4` | Baseline decoder, full grid, **3 × 300 trials**: the statistically strict version of run 2 | ⏳ |
| `run2_strict` | Run 2's experiments re-measured at 3 × 300 trials: settles whether the rule verdicts are real | ⏳ |
