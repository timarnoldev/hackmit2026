# Our decoder against published work on the same dataset

## What this document is

An honest comparison of our decoder chain against published trace reconstruction results on the
**Microsoft clustered Nanopore reads dataset**
([github.com/microsoft/clustered-nanopore-reads-dataset](https://github.com/microsoft/clustered-nanopore-reads-dataset),
10,000 references of length 110, 269,709 reads, mean cluster size 26.97).

**What our decoder is.** A classic consensus decoder (pick a medoid draft, align every read to
it, vote per position, rebuild, repeat three times) followed by a 0.8M-parameter dilated 1D CNN
that predicts an edit script over the draft. Details in [MODELS.md](MODELS.md).

**What it is not.** It is not a contribution to trace reconstruction research. We needed a
decoder good enough to close the codec loop: the project's claim is about tuning *encoder* rules
and redundancy per channel at a fixed recovery target, with the same decoder on both sides of
every comparison (see [PROJECT.md](../PROJECT.md), ablation B vs C). The decoder had to be solid
and fast, not novel. It trains in 13 minutes, and we did not set out to beat anyone with it.

**Where we are ahead, up front.**

1. **Accuracy per unit of compute.** We match a fine-tuned DNAformer at 4, 6 and 10 reads
   (66.1 / 88.8 / 96.2% against 65.9 / 88.3 / 95.7%) with **0.8M parameters against their 100M**,
   **577k training examples against 1.4 billion simulated reads**, and **13 minutes of training on
   one machine**. Same accuracy, roughly 1/125 the model and a training run measured in minutes
   rather than GPU days.
2. **It runs where they cannot.** The decoder fits on an ESP32-S3 microcontroller and decodes a
   strand there in 170 ms, with the whole demo running unplugged from any computer. No published
   trace reconstruction system runs on a 50 dollar chip.
3. **It answers a question nobody publishes.** Every method here reconstructs strands. None of
   them measures what a *coding rule* or a redundancy level costs and buys on a given channel at a
   fixed recovery target. That is what the decoder exists for in this project, and it is where the
   work is new (see [PROJECT.md](../PROJECT.md)).

**Where we are behind.** At low coverage, a published method (TReconLM, TMLR 2025) is clearly
better than ours at every read count from 2 to 10, and it is trained the same way we train, on
this dataset's own train split. On raw accuracy we are level with a fine-tuned DNAformer, ahead
of the classical algorithms, and behind TReconLM. We say so plainly, and the tables below show
it.

---

## Our numbers, and exactly what they mean

Exact strand accuracy on our held-out split of the Microsoft dataset.

| max reads per cluster | 2 | 4 | 6 | 10 | 16 | "full" |
|---|---|---|---|---|---|---|
| baseline majority vote | 4.8% | 39.2% | 68.0% | 85.3% | 90.5% | 90.3% |
| polisher (default) | 5.2% | 56.0% | 83.3% | 93.2% | 95.4% | 95.3% |
| polisher (3 drafts, 2 rounds, gain-mode edits) | 7.0% | 66.1% | 88.8% | 96.2% | 97.2% | **97.3%** |

Protocol, read off the code:

- **Metric.** `dnacodec/evaluate.py:48` — "exact matches / all strands (None and dropouts count
  as wrong)". All 110 bases must match. The denominator is every cluster in the split, including
  empty ones.
- **Split.** `dnacodec/realdata.py:41` — "Microsoft: every 5th cluster by position". 1,996 of
  10,000. Our own split; no published paper uses it.
- **Subsampling.** `scripts/eval_real.py` — per cluster, without replacement, seeded from
  `heldout_seeds()`. Clusters with fewer than *k* reads keep all of them, so "max reads = k" is
  an upper bound. Actual mean reads per strand at k = 2/4/6/10/16 is 2.0 / 4.0 / 5.9 / 9.7 / 14.4.
- **No cluster is excluded for being small.** Empty clusters count as failures.
- **The "full" column is not full coverage.** Both `dnacodec/baseline.py:16` (`MAX_READS = 16`)
  and `dnacodec/model/polish.py:504` subsample to at most 16 evenly spaced reads. "full" means
  every read was offered and the decoder used at most 16. That is why "full" and "16" differ by
  0.1 points. **We have no all-reads number.**

Two internal caveats a judge is entitled to:

1. **Seed noise.** Four tables in this repo report the same baseline measurement with different
   subsample seeds: the canonical table above, `README.md`, `docs/MODELS.md` §1, and the
   calibration check in `docs/ERRORS.md`. They disagree by up to 2 points at 4 to 6 reads (68.0 /
   68.1 / 67.0 / 66.5 at 6 reads). That bounds how finely the mid-coverage columns can be read.
   The "16" and "full" columns do not subsample and do not carry this noise.
2. **Variant selection.** `scripts/polish_experiments.py` defines about 22 decoder variants
   (the best is `polish_gain_d3r2`). The gain-mode margins were tuned on a validation carve of
   the *train* split, which is correct. Picking the best of 22 is a second selection step, and
   the row above reports its held-out score. 95.3% is the safer number to quote.

---

## The comparison that matters: fixed reads per cluster

**TReconLM** (Weindel, Girsch, Heckel, TU Munich; TMLR 2025, arXiv:2507.12927 v2) is the only
published work that reports exact-strand accuracy on this dataset at fixed, low read counts, in
a table, for a whole field of methods. Its protocol is the closest to ours of anything published,
and its results are the ones we most have to answer for.

Success rate (= 1 − their failure rate), from **TReconLM Table 7**. All their columns use
subclusters of **exactly** N reads. Our columns use **at most** k reads, with the mean shown.

| reads per cluster | 2 | 4 | 6 | 10 |
|---|---|---|---|---|
| **TReconLM (fine-tuned)** | **10.9%** | **76.8%** | **91.2%** | **98.6%** |
| DNAformer (fine-tuned by the TReconLM authors) | 3.0% | 65.9% | 88.3% | 95.7% |
| TReconLM (pretrained only) | 3.9% | 61.1% | 85.1% | 93.2% |
| **ours, polisher best variant** | 7.0% | 66.1% | 88.8% | 96.2% |
| **ours, polisher default** | 5.2% | 56.0% | 83.3% | 93.2% |
| ITR (iterative reconstruction) | 4.9% | 57.8% | 78.1% | 89.0% |
| **ours, majority vote baseline** | 4.8% | 39.2% | 68.0% | 85.3% |
| Trellis BMA | 0.1% | 39.1% | 64.6% | 82.9% |
| BMALA | 0.5% | 13.9% | 36.4% | 67.8% |
| MUSCLE | 0.8% | 26.1% | 46.8% | 72.1% |
| VS | 2.5% | 3.1% | 2.7% | 4.3% |
| RobuSeqNet (fine-tuned) | 0.5% | 1.9% | 4.7% | 8.5% |
| our mean reads per strand | 2.0 | 4.0 | 5.9 | 9.7 |

Read that table plainly:

- **TReconLM beats us at every coverage**, by 3.9 points at 2 reads, 10.7 at 4, 2.4 at 6 and
  2.4 at 10. At 2 and 4 reads our mean coverage matches theirs exactly (2.0 and 4.0), so the gap
  there is not a coverage artifact.
- **We are level with a fine-tuned DNAformer**: 66.1 vs 65.9 at four reads, 88.8 vs 88.3 at six,
  96.2 vs 95.7 at ten. Those differences are inside our own seed noise.
- **We are ahead of the classical algorithms** at every coverage, and our plain majority vote
  baseline is itself competitive with Trellis BMA and well ahead of MUSCLE on this metric.
- TReconLM is trained on this dataset's 80% train split, exactly as our polisher is trained on
  our 80% train split. So "we tuned in-domain" is not an excuse available to us here.

### What is still not matched in that table

- **At most k reads versus exactly N.** At 6 and 10 our means are 5.9 and 9.7, so we are running
  slightly below their coverage. At 2 and 4 we are not.
- **Different denominators.** They evaluate 5,109 subcluster examples drawn from a 10% test split
  of clusters; we evaluate 1,996 whole clusters from a 20% split. Their procedure splits a large
  cluster into several examples and discards the leftover when fewer than two reads remain; we
  keep every cluster once and count empty ones as failures.
- **Version.** These are arXiv v2 numbers. v1 reports a different table (e.g. N=10 TReconLM
  failure 2.35e-2 in v1 against 1.37e-2 in v2). Cite v2.

---

## The comparison at high coverage

The cleanest reference point here is **BBS Table 2**, which runs five methods on the same 9,984
clusters (all 10,000 minus the empty ones) at mean coverage 27.01, with no error-correcting code.
The other rows sit at various coverages and cluster counts, listed per row; this table is a
survey, not a ranking.

| Method | Success rate | Avg edit distance | Clusters | Coverage | Same metric as ours? | Same coverage? |
|---|---|---|---|---|---|---|
| **ours, polisher best variant** | **97.3%** | not measured | 1,996 (shipped clusters, empties counted wrong) | ≤16 reads | — | — |
| **ours, polisher default** | 95.3% | not measured | 1,996 | ≤16 reads | — | — |
| CPL (DNAformer's refinement stage) | 94.93% | 0.150 | 9,984 | full, mean 27.01 | **yes** | no, higher |
| BBS | 94.77% | 0.168 | 9,984 | full, mean 27.01 | **yes** | no, higher |
| DNA-GAN (Zheng et al., Sci Rep 2024) | 91.73% | — | **2,833** (only clusters with ≥30 reads) | 30–50 reads | **yes** | no, much higher |
| **ours, majority vote baseline** | 90.3% | 0.21 | 1,996 | ≤16 reads | — | — |
| ITR (Sabary et al.) | 87.58% | 0.232 | 9,984 | full (ITR caps at 25) | **yes** | no, higher |
| DNAformer (self-reported) | 85.42% | — | 9,954, their own index re-binning | ≤16 reads | **yes** | **yes** |
| MUSCLE v5 | 84.70% | 0.259 | 9,984 | full, mean 27.01 | **yes** | no, higher |
| Hybrid (Sabary et al.) | 80.02% | — | 9,984 | full | **yes** | no, higher |
| BMA Lookahead w=3 (Sabary et al.) | 77.99% | — | 9,984 | full | **yes** | no, higher |
| MACL (Li et al., 2026) | 70.59% "recovery rate" | — | 30% test split of 9,984 | fixed 30x | **yes** | no, higher |
| Trellis BMA, re-run by BBS at default parameters | 69.57% | 0.908 | 9,984 | full, mean 27.01 | **yes** | no, higher |
| Trellis BMA, **as its own authors report it** | no number extractable | normalized Hamming distance per base; BCJR-once AIR in bits/base | test set only, clusters 2501–10000 | K = 2,4,6,8,10 | **no** | partly |

Our mean edit distance for the baseline is 0.21 at ≤16 reads (`README.md`), which sits between
MUSCLE's 0.259 and ITR's 0.232 and is a reasonable sanity check on the column. Our denominators
differ slightly (`evaluate.py` averages over strands with a non-`None` decode, theirs over all
clusters), so treat it as a sanity check, not a ranking. We have no published edit distance for
the polisher.

### Numbers that could not be placed in either table

- **Trellis BMA's own figures.** The paper contains no result tables at all; every number is a
  curve. Figure 2(a) is the uncoded real-data panel (y axis normalized Hamming distance, ticks
  0/0.1/0.2/0.3/0.4; x axis 2/4/6/8/10 traces). We did not digitize it, so we quote no value.
- **BBS's coverage sweep (Figure 4).** Not usable; see the BBS paragraph.
- **DNAformer's per-method Fig. 3a bars** on this dataset were read from the authors' own NVMW
  2025 Table II tabulation of the same data, not from the figure.

---

## What is not comparable, paper by paper

### TReconLM (Weindel, Girsch, Heckel; TMLR 2025, arXiv:2507.12927)

The closest protocol to ours and the result that goes against us.

- **Same metric.** §5: "Failure rate: The fraction of test examples in which the reconstructed
  sequence x̂ differs from the original sequence x."
- **Different unit of evaluation.** Appendix G: large clusters are split into subclusters by
  "repeatedly sampling a cluster size between 2 and 10 and selecting that many traces without
  replacement. We stop when fewer than two traces remain and discard them." So one big cluster
  becomes several test examples, and reads that do not fill a subcluster are thrown away. We
  evaluate each cluster once and count empty clusters as failures. Their 5,109 test examples come
  from a 10% split; our 1,996 clusters from a 20% split.
- **Exactly N reads versus at most k.** Their N is exact. Our k is a cap, and the realised mean
  is below it at k = 6 and 10.
- **They also train in-domain.** 80/10/10 train/validation/test on this dataset, with pretraining
  on synthetic data first. Table 3 shows pretraining is worth a lot: failure rate 0.342 from
  scratch against 0.205 pretrained. This is the same recipe we use (simulated pretraining plus
  real fine-tuning), executed better.
- **No outer code.** Pure trace reconstruction, like ours.
- **One clean cross-check they provide.** Appendix J.2: under DNAformer's own index-based
  re-clustering protocol, 9,729 test examples, TReconLM pretrained gets 0.111 failure against
  DNAformer's self-reported 0.146, "with fewer reads and no dynamic-programming postprocessing".

### BBS (Gu, Xin, Sharma, Goh, Wong, Nagarajan; iScience 28(11):113791, 2025; RECOMB-seq 2025)

- **Same metric.** STAR Methods: "Success rate, defined by the percentage of the clusters that
  are reconstructed correctly without any error."
- **Different cluster set.** Table 2 uses 9,984 clusters, all 10,000 minus the empty ones ("we
  remove the empty clusters for the Microsoft CNR dataset", `bbs-test/datasets/README.md`). We
  keep empty clusters and count them as failures, worth about 0.16 points against us. Our 1,996
  are a 20% subset of theirs.
- **Higher coverage.** Whole clusters, mean 27.01, against our cap of 16. At the top of our curve
  more reads buy us almost nothing (16 → "full" is +0.1), but we have not measured that against
  27 reads and cannot claim it.
- **Their coverage sweep is not usable.** The paper says "we randomly sampled 500 clusters from
  the Srinivasavaradhan et al. dataset and performed a subsampling experiment", which sounds like
  our protocol. `datasets/scripts/subsample_dataset.py` passes `minimum_coverage=50`: the 500
  clusters are drawn only from clusters that originally had at least 50 reads, in a dataset whose
  mean is 27. Those are the best-sequenced clusters. The paper does not state this, the
  subsamples are nested, and the values exist only on a log-scale figure. So the low-coverage
  comparison we would most want from BBS cannot be made honestly.
- **No error correction code is assumed** anywhere in BBS.
- **BBS is not the best method in its own table.** CPL wins on all three accuracy metrics. BBS's
  unambiguous advantage is speed: 20 s for the whole dataset against 361 s for CPL, 7,351 s for
  ITR and 17,859 s for Trellis BMA, single-threaded on one i9-13900H.
- **Their Trellis BMA baseline is weakened.** Run with fixed p_S = 0.03, p_D = 0.02, p_I = 0.02
  across all datasets. Combined with the metric mismatch below, 69.57% should not be read as
  "Trellis BMA is bad".
- **BBS loses on synthetic data**: its own supplement says "the ITR algorithm performs the best
  in both cases" on synthetic IDS channels. Relevant to us, since our loop runs on a simulator.

### Trellis BMA (Srinivasavaradhan, Gopi, Pfister, Yekhanin; ISIT 2021, arXiv:2107.06440)

The paper that introduced the dataset, and the one we can least compare against.

- **Different metric.** §II-E defines the target as expected Hamming distance,
  `E[d(X̂,X)] = Σ_n Pr(X̂_n ≠ X_n)`, per base, plus a BCJR-once achievable information rate. The
  phrase "success rate" never appears. §II-C rejects our framing outright: "exact reconstruction
  is typically impossible from only a few traces". A method tuned for per-base Hamming error is
  not tuned to maximise perfect strands, and the two rank methods differently.
- **No tables.** Every result is a curve in Fig. 2 (real) or Fig. 3 (simulated).
- **Different split.** §V: "The 10000 clusters ... are divided into training (clusters 1-2000),
  validation (clusters 2001-2500) and test sets (clusters 2501-10000)." Their results are on
  7,500 clusters. Our every-5th split overlaps their training and validation sets.
- **Coded versus uncoded.** Only panels 2(a) and 2(d) are uncoded. Panels (b), (c), (e), (f) use
  a marker-repeat inner code at rate 104/110 or 100/110, coded side information we do not have.
- **The erratum.** The note added 8/12/2024, in both README and arXiv v2 §III: the references
  "exhibit long-range dependencies instead of being uniformly random", so clustering "may have
  unexpected behavior and some recovered clusters may be malformed, making the trace
  reconstruction problem harder". Fig. 3's caption says the simulated results are unaffected,
  which implies the real-data Fig. 2 curves are. Their published curves predate that
  understanding; ours do not, but we inherit the same malformed clusters.
- **Compute.** No wall-clock numbers. O(K·N·|Q|·Δ); exact multi-trace BCJR was "computationally
  infeasible" above 3 traces.

### DNAformer (Bar-Lev, Orr, Sabary, Etzion, Yaakobi; Nature Machine Intelligence 7:639–649, 2025)

- **Same metric and the same read cap as us.** Supp. Table 8: "Wrong prediction is defined as
  having at least one wrong character out of the entire predicted sequence." Supp. §2.1: "in our
  reconstruction accuracy evaluations, the DNAformer used only up to 16 reads per cluster, while
  other algorithms used various larger cluster sizes", because "the DNAformer was implemented on
  GPU, for inference efficiency purposes". Identical cap, identical reason.
- **Different clusters.** They do not use the shipped clusters: "all of them were clustered using
  our binning approach ... we used the first unique symbols of these sequences as they were
  indices in the clustering process." The result is "9,954 clusters ... with a total error rate
  of 4.72% and an average cluster size of 17", against the dataset's own mean of 26.97. Their
  binning discards roughly a third of the reads.
- **No in-domain training at all, by design.** "we did not use real data during the training of
  the models", trained on "1.4B simulated DNA reads", 180 epochs on a single A40. Their model is
  built for their own length-140 sequences with a designed 12-base index. For them, this dataset
  is an out-of-distribution transfer test; for us it is the training distribution.
- **So 85.42% is not DNAformer's ceiling here.** When the TReconLM authors fine-tuned the same
  architecture on this dataset's train split, it reached 95.7% at ten reads and 88.3% at six,
  against the 85.42% the original paper self-reports at up to sixteen. Most of the gap between
  our number and DNAformer's published number is domain adaptation, not architecture. Our
  polisher is level with the fine-tuned version, not ten points above DNAformer.
- **It is a system number, not a transformer number.** Supp. Table 7 separates them on their own
  data: Siamese 100M alone gives 2,720 errors, plus CPL gives 1,842. For the Microsoft dataset
  the paper never says whether the confidence filter and CPL were active.
- **No outer code in the 14.58%.** The public datasets were not encoded with their tensor-product
  scheme. Their outer code and safety-margin analysis runs only on their own 110,000-sequence
  data, where the mechanism deliberately trades accuracy for correctability by converting
  substitutions into erasures.
- **Compute.** 100M parameters, 1.4B simulated training reads, single A40; 1.52 s for 10,000
  clusters of 16 reads against 61.2 hours for Trellis BMA on 32 CPU cores. The headline 3,200x is
  GPU against CPU baselines, not an algorithmic claim.

**A warning these papers hand us for free.** The same DNAformer system is reported at 85.42%
(self-reported, own re-binning, ≤16 reads), its CPL stage at 94.93% (BBS, shipped clusters,
coverage 27), 65.9% at four reads (TReconLM's fine-tune), and 45.02% "recovery rate" (MACL's
re-run at 30x). That is a spread of tens of points for one method on one dataset, driven entirely
by clustering, coverage and who ran it. It is wider than any gap between our decoder and the
field, and it is the best reason to distrust cross-paper arithmetic here.

### Other work on this dataset

- **DNA-GAN** (Zheng et al., Scientific Reports 14:32071, 2024). Reports 91.73% success, but:
  "We only use 2,833 of their sequences because the rest has less than 30 sequenced reads." That
  keeps 28% of the dataset, the best-covered 28%, at cluster sizes 30–50. Not comparable to any
  whole-dataset number, ours included. They also report 100% success at 20,000 shuffles, which
  should make anyone cautious about the protocol. "Success rate" is never formally defined; only
  edit distance is. No public code or data.
- **MACL** (Li et al., Synthetic and Systems Biotechnology 12:422–432, 2026). Reports "recovery
  rate" 70.59% at 30x, 42.39% at 20x, 19.62% at 10x on a 30% test split, where recovery rate is
  the fraction of perfectly reconstructed strands. Two problems: the dataset's mean cluster size
  is 27, so how a fixed 30x coverage is obtained is never explained; and their re-run of
  DNAformer gives 45.02% recovery against DNAformer's self-reported 85.42%. Their absolute levels
  are far below everyone else's at nominally higher coverage, so we do not draw a comparison.
- **Sabary et al.** (Scientific Reports 14:1951, 2024), the source of ITR. Table 1, Experiment B,
  9,984 clusters, full coverage: ITR 0.875714 success, Hybrid 0.8002, BMA Lookahead (w=3)
  0.779948. Independently reproduced by BBS at 87.58% for ITR, which is reassuring about that one
  number.
- **Belief-Combining Framework** (Nouri, arXiv:2601.18920, 2026). Uncoded multi-trace MAP on this
  dataset at 1 < K ≤ 16, but averages over 300 randomly selected sequences and reports edit
  distance only, in a figure. No usable number.
- **DNARetrace**, **GradHC**, **DNA-Storalator** use the dataset for graph-based assembly,
  clustering benchmarks and error characterisation respectively, not per-cluster consensus. Not
  comparable.
- **Ruled out after checking**: RobuSeqNet (Qin et al., CSBJ 2024) evaluates on Erlich, Organick
  and Chandak, **not** this dataset; its numbers here exist only because TReconLM and MACL
  re-ran it. Nahum et al. (single-read transformers) is simulated data only. Hedges predates the
  dataset. No paper called "DNArch" was found.

---

## Honest conclusion: where we stand

**We do not beat the state of the art, and at the coverage we care about most the gap is clear.**

1. **At low coverage we are behind the best published method.** TReconLM reports 10.9% / 76.8% /
   91.2% / 98.6% exact strands at 2 / 4 / 6 / 10 reads. Our best variant reports 7.0% / 66.1% /
   88.8% / 96.2%. At 2 and 4 reads our mean coverage matches theirs exactly, so the 3.9 and 10.7
   point gaps are real. And the usual excuse is unavailable: TReconLM trains on this dataset's
   train split just as we do, so it is not beating us by having seen more of the domain. It is a
   better model, trained better, with more compute, and it does the same job.
2. **We are roughly level with a fine-tuned DNAformer** at 4, 6 and 10 reads, within our own
   seed noise. That is a reasonable place for a decoder built in an afternoon to land, and it is
   what the codec loop needed.
3. **We are ahead of the classical algorithms.** Our polisher beats ITR, Trellis BMA, BMALA and
   MUSCLE at every coverage in TReconLM's table, and our plain majority vote baseline beats
   MUSCLE outright. That says more about how strong medoid-plus-vote is on shipped clusters than
   about our model.
4. **At full coverage our 95.3% / 97.3% sits above CPL's 94.93% and BBS's 94.77%, and we should
   not claim it.** Different cluster set (1,996 against 9,984), different coverage (≤16 against
   27), different empty-cluster handling, and we trained in-domain while BBS does not train at
   all. The literature's own spread on this dataset is wider than that margin.
5. **Our baseline is suspiciously strong at full coverage too.** A plain majority vote at ≤16
   reads scores 90.3%, above MUSCLE's 84.70% and ITR's 87.58% at coverage 27. Either our
   1,996-cluster subset is easier than the full set, or shipped-cluster majority voting is a
   better baseline than the literature's MSA baselines on this data. We have not determined
   which, so the absolute level of our whole column carries a few points of uncertainty.

**What the better methods do that we do not.** TReconLM is a language model over traces with
large-scale synthetic pretraining and a proper train/validation discipline. DNAformer is 100M
parameters trained on 1.4B simulated reads, validated on four public datasets and two of its own
at two sequence lengths and two sequencing technologies, with an outer code and a confidence
filter that converts substitutions into erasures. Trellis BMA produces calibrated per-symbol
posteriors that feed an outer decoder, quantified as achievable information rates; we emit a hard
strand and a CRC-16 and throw that information away. BBS reconstructs the whole dataset in 20
seconds single-threaded on a CPU with no training at all, where we need a GPU and a checkpoint.
And every one of them reports on more than one dataset; we report on the one we tuned on.

**The one-sentence version.** Our decoder is a competent, fast, in-domain-tuned consensus
polisher that is level with a fine-tuned DNAformer, ahead of the classical algorithms, and
clearly behind TReconLM at low coverage, which is exactly what the codec loop needed and is not
a research result.

---

## What a judge might ask

**"Is your decoder state of the art?"**

No. TReconLM (TMLR 2025) beats it at every read count from 2 to 10 on this dataset, including at
2 and 4 reads where the coverage is genuinely matched, and it trains on the same dataset's train
split that we do. We are about level with a fine-tuned DNAformer and ahead of the classical
algorithms. We found this out by looking, and it does not change the project's claim, because the
claim is about the encoder with the decoder held fixed.

**"Then why does your number look higher than BBS and CPL at full coverage?"**

Because the protocols differ more than the numbers do. We evaluate 1,996 clusters at up to 16
reads; they evaluate 9,984 clusters at all 27 and drop the empty ones. We train on 80% of this
sequencing run; BBS does not train at all. And the literature disagrees with itself by more than
our margin: DNAformer's own components are reported anywhere from 45% to 95% on this dataset
depending on who ran them and how the reads were clustered. We would not put weight on a
two-point gap inside that.

**"You picked the evaluation protocol yourself. How do I know it is not flattering?"**

Two parts of it cut against us: we count empty and malformed clusters as failures where BBS
deletes them, and our decoder uses at most 16 reads where the full-coverage numbers use 27. One
part clearly flatters us: our held-out split is every 5th cluster and the polisher trained on the
other four fifths of the same sequencing run. That is legal and enforced in code (`seeds.py`
raises if training touches a held-out seed), and it is the main reason not to read our
full-coverage column as a better algorithm. One part we have not resolved: our plain majority
vote baseline scores above MUSCLE and ITR at higher coverage, which hints our subset may be
easier than the full set.

**"Does any of this matter for your actual claim?"**

No, and that is the point. The project's claim is that auditing encoder rules and redundancy per
channel reaches a fixed recovery target more cheaply, measured with the *same* decoder on both
sides (ablation B vs C). A better or worse decoder shifts both sides equally. We needed a decoder
good enough that the loop was not measuring decoder noise, and this comparison says we have one.
If we wanted a better decoder tomorrow, the honest move would be to use TReconLM's, not to tune
ours further.

---

## Sources

- Weindel, Girsch, Heckel. *Trace Reconstruction with Language Models.* TMLR 2025.
  [arXiv:2507.12927](https://arxiv.org/abs/2507.12927) (v2; v1 reports different numbers).
  Code [MLI-lab/TReconLM](https://github.com/MLI-lab/TReconLM).
- Gu, Xin, Sharma, Goh, Wong, Nagarajan. *Efficient trace reconstruction in DNA storage systems
  using bidirectional beam search.* iScience 28(11):113791, 2025.
  DOI [10.1016/j.isci.2025.113791](https://doi.org/10.1016/j.isci.2025.113791). PMCID PMC12630026.
  Code [GZHoffie/bbs](https://github.com/GZHoffie/bbs), scripts
  [GZHoffie/bbs-test](https://github.com/GZHoffie/bbs-test).
- Srinivasavaradhan, Gopi, Pfister, Yekhanin. *Trellis BMA: Coded trace reconstruction on IDS
  channels for DNA storage.* ISIT 2021, pp. 2453–2458. DOI 10.1109/ISIT45174.2021.9517821.
  Extended version [arXiv:2107.06440](https://arxiv.org/abs/2107.06440) v2 (21 Aug 2024).
- Bar-Lev, Orr, Sabary, Etzion, Yaakobi. *Scalable and robust DNA-based storage via coding theory
  and deep learning.* Nature Machine Intelligence 7(4):639–649, 2025.
  [s42256-025-01003-z](https://www.nature.com/articles/s42256-025-01003-z). Preprint
  [arXiv:2109.00031](https://arxiv.org/abs/2109.00031) v3. Author Correction Nat Mach Intell 8:134
  (2026), a missing citation only, no numbers changed.
- Sabary, Yucovich, Shapira, Yaakobi. *Reconstruction algorithms for DNA-storage systems.*
  Scientific Reports 14:1951, 2024. DOI 10.1038/s41598-024-51730-3.
- Zheng, Xie, Yao, Su, Chu, Xu, Liu. *A generative adversarial network for multiple reads
  reconstruction in DNA storage.* Scientific Reports 14:32071, 2024.
  DOI 10.1038/s41598-024-83806-5.
- Li, Zheng, Shao, Wang, Li, Wang, Zhou, Cao, Zheng. *Highly biased DNA sequence reconstruction in
  DNA storage with multi-scale attention mechanism and contrast learning.* Synthetic and Systems
  Biotechnology 12:422–432, 2026. DOI 10.1016/j.synbio.2026.01.028.
- Dataset: [microsoft/clustered-nanopore-reads-dataset](https://github.com/microsoft/clustered-nanopore-reads-dataset)
  (MIT), including the note added 8/12/2024 on non-uniform references and malformed clusters.
