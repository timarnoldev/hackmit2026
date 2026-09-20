# Every number we quote, and where it comes from

One page for the pitch and for judge questions. If a number is not in here, don't say it on stage.

Status legend: ✅ measured and independently re-checked · ☑️ measured once · ⏳ pending (run in progress) · ⚠️ superseded or unreliable

---

## 1. The decoder, on real reads

**Setup:** Microsoft clustered Nanopore reads, **held-out split** (1,996 clusters; every 5th cluster, never used for training, tuning or calibration). Each cluster subsampled to at most N reads with fixed seeds from `heldout_seeds()`. Metric: **share of strands reconstructed exactly**, all 110 letters correct. Protocol: `scripts/eval_real.py`, numbers from `dnacodec.evaluate.evaluate`.

Averaged over **20 independent read draws per point** (a single draw moves the number by up to a point, which is why earlier tables in this repo disagreed slightly). Mean ± standard deviation:

| Reads per strand | 2 | 4 | 6 | 10 | 16 | Status |
|---|---|---|---|---|---|---|
| Baseline (align + majority vote) | 5.2% ±0.5 | 39.9% ±0.9 | 67.2% ±0.8 | 84.1% ±0.5 | 89.7% ±0.3 | ✅ |
| Polisher, default (used in the loops) | 5.8% ±0.5 | 56.7% ±1.2 | 82.3% ±0.9 | 92.9% ±0.3 | 95.3% ±0.2 | ✅ |
| Polisher, best variant (3 drafts, 2 rounds, gain-mode edits) | 7.0% ±0.7 | **66.6% ±1.1** | **88.1% ±0.5** | 95.7% ±0.3 | 97.2% ±0.2 | ✅ |

Reproduce: `uv run --extra train python scripts/stable_decoder_numbers.py --draws 20`, raw numbers in `results/decoder_real_heldout.json`. Note that "16 reads" is also our highest-coverage number: both decoders cap at 16 reads per cluster, so we have no all-reads figure.

- **Independently re-checked** by the architect with separate counting code and different seeds; every value sits inside the spread above.
- **Headline sentence:** at 6 reads per strand the learned decoder reconstructs 88.1% of strands exactly, against 67.2% for the classic method, on real Nanopore data it never saw. That is 21 points, and it more than halves the failures, from 32.8% to 11.9%.
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

### The same audit as a curve, not a threshold ✅

`scripts/rule_recovery_curve.py`, 300 held-out trials per point, same decoder and file, Nanopore. Recovery rate against mean reads per strand (`results/rule_recovery_curve_rules_nanopore_budget.json`):

| codec | 12 | 14 | 16 | 18 | 20 |
|---|---|---|---|---|---|
| both rules (standard) | **0.807** | **0.993** | 1.000 | 1.000 | 0.993 |
| homopolymer rule off | 0.000 | 0.287 | 0.990 | 1.000 | 1.000 |
| GC rule off | 0.833 | 0.987 | 1.000 | 0.993 | 1.000 |
| both rules, payload only | 0.713 | 0.997 | 0.997 | 1.000 | 1.000 |

This independently confirms both verdicts above: the homopolymer rule is the difference between recovering four files in five and none at 12 reads, and the GC rule tracks the standard codec to within noise everywhere.

**Why we now quote curves when two codecs are close.** The threshold metric ("fewest reads at which all 300 trials recover") is brittle near the top: the standard codec reaches 1.000 at 16 and then dips to 0.993 at 20, a single failure out of 300 well inside the region where it plainly works. An all-or-nothing rule reads that dip as failure. The threshold stays the headline, because "the file always comes back" is the promise, but comparisons ride on the curve. See [LEARNED_RULES.md](LEARNED_RULES.md) for the correction this forced.

**Tier 2, measured directly** (same settings, same decoder, paired held-out trials, rule scorer vs learned risk model): ☑️

| Channel | Simulator | Candidates | Strand failures, rules | with learned selection | Difference (95% CI) |
|---|---|---|---|---|---|
| Nanopore, 6 reads | A | 32 | 51.96% | 48.01% | **−3.95 points** (−4.18 to −3.72) |
| Nanopore, 6 reads | **B (firewall)** | 32 | 50.92% | 46.76% | **−4.16 points** (−4.37 to −3.96) |
| Nanopore, 19.5 reads | A | 32 | 16.60% | 13.94% | −2.66 points |
| Illumina | A and B | 8 and 32 | 3.60% | 3.53% | about 0 |

The gain survives a structurally different simulator. In the coarse target metric it is worth about half a read.

⚠️ **Run 1 numbers are superseded.** Run 1 suggested 6 vs 8 reads on Nanopore; that was a winner's curse (best of hundreds of settings on the same trials). The final round of run 1 then missed the target on held-out data (297 of 300). Run 2 adds a margin check, three seed blocks and train-only selection. Say this openly; it is a strength, not a weakness.

## 6. Does the risk model transfer to real reads? Yes, once the measurement is done right ✅

The first number we had was **AUC 0.516**, essentially chance, and it was a measurement artifact. On real reads, failure is dominated by how many reads a strand happens to get: **read count alone ranks failures at AUC 0.78**. A score that only looks at the sequence cannot show up in an unstratified measurement, by construction.

Holding coverage fixed (every cluster decoded at the same number of reads, 4 subsamples each, Microsoft held-out split, 1,996 clusters):

| What ranks real decoder failures? | AUC |
|---|---|
| **Our risk model** (trained only on simulated channels) | **0.69** |
| The homopolymer rule alone (longest run) | 0.66 |
| The 5-mer context table (calibrated, not learned) | 0.61 |
| GC deviation | 0.50, chance |

**The decisive test:** holding coverage *and* longest run fixed, so the hand rule's own feature is neutralised, the model still reaches **0.61** while the rule itself drops to **0.50**. On real DNA the model knows something the hand rule does not. The same picture appears with the classic baseline decoder (0.71 pooled), so it is not an artifact of the polisher.

**Why the DNAformer set shows nothing:** 98.1% of its references have a longest run of 3 or 4 and its GC standard deviation is 0.014, so the features barely vary. Every sequence-based score, including the hand rule, sits at 0.51 to 0.53 there. A risk model trained directly on real failures reaches 0.558 on that set, reproduced at 0.550 on a second flowcell.

**Remaining honest limitation:** we show the ranking transfers to real reads. We have not shown that *selecting* candidates by it reduces failures on real DNA, because that would need new strands synthesized and sequenced.

Reproduce: `python scripts/risk_real_analysis.py --dataset microsoft --risk-model <run>/nanopore_budget/risk_it3.pkl --decoder polish --coverages 4 6 8 12 16 --repeats 4`

## 6b. Where we stand against published decoders ☑️

`docs/COMPARISON.md` has the full table. Short version, exact strands on the same Microsoft dataset:

| reads per cluster | 2 | 4 | 6 | 10 |
|---|---|---|---|---|
| TReconLM (TMLR 2025), fine-tuned | 10.9% | 76.8% | 91.2% | 98.6% |
| **Ours, best variant** | 7.0% | 66.1% | 88.8% | 96.2% |
| DNAformer, fine-tuned by TReconLM's authors | 3.0% | 65.9% | 88.3% | 95.7% |
| ITR | 4.9% | 57.8% | 78.1% | 89.0% |
| **Ours, classic baseline** | 4.8% | 39.2% | 68.0% | 85.3% |
| Trellis BMA | 0.1% | 39.1% | 64.6% | 82.9% |

**Where we are ahead:**

- **Accuracy per unit of compute.** We match a fine-tuned DNAformer at 4, 6 and 10 reads with **0.8M parameters against their 100M**, **577k training examples against 1.4 billion**, and **13 minutes of training** instead of GPU days.
- **Where it runs.** The same decoder fits on an ESP32-S3 and decodes a strand in 170 ms, so the demo runs unplugged. No published system here runs on a microcontroller.
- **The question it answers.** Every method in the table reconstructs strands. None measures what a coding rule costs and buys on a given channel, which is what this project is about.

**Where we are behind:** TReconLM is clearly better on raw accuracy at 2 to 10 reads, and it trains on the same split we do. Say it plainly if asked; the full table is in `docs/COMPARISON.md`.

## 6c. What the risk model learned, read back out of it ✅

From the trained model of the final run, probed by inserting patterns into random backgrounds
(`scripts/what_the_model_learned.py --run-id run5`, full output in `results/run5_learned_rules.txt`):

**Nanopore, response to a homopolymer run of length r:**

| Run length | 1 | 3 | 4 | 5 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|---|
| Predicted risk | 0.43 | 0.44 | 0.46 | 0.52 | 0.60 | 0.76 | **0.83** |

It learned the effect by itself, and it puts the threshold at 4 to 5, not at 3 where the standard rule puts it.

**Nanopore, response to GC content** (runs capped at 3, so this is GC alone): 0.453 at GC 0.2 rising to 0.489 at GC 0.8. Essentially flat, so it considers the GC rule pointless, which matches the rule audit measured independently.

**Most dangerous patterns it names:** GGGGG 0.71, CCCCC 0.69, AGGGG 0.64, TCCCC 0.62, TTTTT 0.61, against a random background at 0.53. It rates G and C runs more dangerous than A and T runs, which no standard rule distinguishes.

**The cross-check that makes this quotable:** our channel was calibrated on real reads, and its worst deletion contexts are GAAAG (71x) and AAAAG (13x). The risk model never saw that table, it only saw which strands the decoder got wrong. Its risk for those two patterns: **0.934 and 0.973**. For the worst substitution contexts (CCCGA, 12.8x) it stays near 0.44, which is also right: majority voting repairs substitutions, deletions are what kills a strand. **The model did not learn where errors happen, it learned where errors are fatal.**

**Illumina:** every pattern gets risk 0.000. It correctly learned there is nothing to avoid on that channel.

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
