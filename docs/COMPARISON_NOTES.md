# Provenance behind COMPARISON.md

Where each number in [COMPARISON.md](COMPARISON.md) comes from: the arithmetic we did ourselves,
the figures we refused to digitize, the papers we checked and ruled out, and what we could not
verify. Anyone auditing that comparison should start here.

## 1. Our protocol, verified from code (not from slides)

| Fact | Source |
|---|---|
| `strand_accuracy` = exact matches / all clusters; `None` and dropouts count as wrong; an empty cluster counts as wrong even if the decoder returned something | `dnacodec/evaluate.py:48`, `:56` |
| Held-out split = every 5th cluster by position, 1,996 of 10,000 | `dnacodec/realdata.py:41` |
| Subsample per cluster, without replacement, `np.random.default_rng(seed)`, seeds from `heldout_seeds()`; clusters with ≤ k reads keep all | `scripts/eval_real.py` docstring and `subsample_clusters()` |
| Decoder read cap 16, evenly spaced | `dnacodec/baseline.py:16`, `dnacodec/model/polish.py:87` and `:504` |
| "best variant" = `polish_gain_d3r2` = `dict(rounds=2, drafts=3, select="confidence", mode="gain")` | `scripts/polish_experiments.py` VARIANTS |
| gain margins `{"low_max_reads": 3, "low": (0.5, -0.5), "high": (0.0, -1.0)}`, "tuned by the `tune` subcommand on the train val carve" | same file, GAIN_THRESHOLDS |

**The "full" column is not full coverage.** Every read is offered, the decoder uses at most 16.
This is the single most load-bearing correction in the comparison, because several papers report
at the dataset's native mean of 27.

**Mean reads per strand actually achieved** (README baseline table): 2.0 / 4.0 / 5.9 / 9.7 / 14.4
at caps of 2 / 4 / 6 / 10 / 16. So at caps 2 and 4 our coverage is exactly matched to a paper
that fixes N = 2 or 4; at 6, 10 and 16 we are slightly below.

## 2. Internal number spread (seed noise)

One subsample draw moves the baseline's mid-coverage columns by up to two points. Tables in this
repository that quote a single draw therefore disagree with each other at that resolution:

| source | 2 | 4 | 6 | 10 | 16 | full |
|---|---|---|---|---|---|---|
| the canonical table used in COMPARISON.md | 4.8 | 39.2 | 68.0 | 85.3 | 90.5 | 90.3 |
| `README.md`, "Baseline on real data" | 4.8 | 39.2 | 68.1 | 85.3 | 90.6 | — |
| `docs/ERRORS.md`, held-out calibration check | 4.9 | 39.8 | 66.5 | 84.5 | 90.1 | — |

The 16 and "full" columns do not subsample and do not carry this noise.
[NUMBERS.md](NUMBERS.md) section 1 is the reconciled version: 20 independent draws per point with
a standard deviation, and every single-draw value above sits inside its spread. Quote that one.

## 3. Arithmetic done here (not quoted from any paper)

- **TReconLM success rates** = 1 − failure rate, from Table 7 of arXiv:2507.12927 **v2**.
  Worked: 8.91e-1 → 10.9%; 2.32e-1 → 76.8%; 8.81e-2 → 91.2%; 1.37e-2 → 98.6% (TReconLM
  fine-tuned at N = 2/4/6/10). Same conversion applied to every other column of that table.
- **DNAformer success rate** = 1 − 0.1458 = 85.42%. The BBS paper independently quotes 85.42%,
  which cross-checks the conversion.
- **Mean cluster size of the dataset** = 269,709 / 10,000 = 26.97. The dataset README states the
  two totals but not the mean.
- **Our per-base rate**, if ever needed: mean edit distance / 110. Baseline at ≤16 reads is
  0.21 / 110 = 0.19%. Not used in COMPARISON.md because the denominators differ from the papers'.

## 4. Figure-only numbers we deliberately did not use

- **Trellis BMA Fig. 2(a)** (uncoded real data, normalized Hamming distance, x = 2/4/6/8/10
  traces). The paper has no result tables at all. We did not digitize it and quote no value.
- **BBS Fig. 4**, the coverage sweep. Two reasons: log-scale figure with no backing table, and
  the 500 clusters are drawn only from clusters with ≥ 50 original reads
  (`datasets/scripts/subsample_dataset.py`, `minimum_coverage=50`), which the paper does not say.
  That is a systematically easier subset than ours.
- **DNAformer Supp. Fig. 1**, their own coverage sweep, figure only.
- **DNAformer Fig. 3a** per-method bars: we used the authors' own tabulation of the same data in
  their NVMW 2025 extended abstract (Table II) rather than reading the bars.
- **DNARetrace Fig. 2C/2D**, **GradHC Figs. 4 and 5**, **Belief-Combining Fig. 4**: figure only,
  and none of them is a comparable per-cluster consensus benchmark anyway.

## 5. Things we could not verify

- **Which split the best-of-22 variant selection ran on.** `scripts/polish_experiments.py`
  documents the right thing ("train split for tuning decisions, heldout only for final numbers"),
  and the gain margins are tuned on a validation carve of the train split. We have not verified in
  the run logs which split the *selection among the 22 variants* actually scored on. 95.3%, the
  default variant, is the number that does not depend on the answer.
- **Whether our 1,996-cluster subset is easier than the full 10,000.** Our plain majority vote
  scores 90.3% at ≤16 reads, above MUSCLE's 84.70% and ITR's 87.58% at coverage 27 in BBS Table 2.
  That is surprising and unexplained. Running the baseline on all 10,000 clusters would settle it
  in minutes once the data is downloaded, and it is the single most useful follow-up.
- **DNAformer's Microsoft number: was CPL active?** The paper never says whether the confidence
  filter and CPL ran on the public datasets, so 14.58% is a system number of unclear composition.
- **DNA-GAN's MAFFT baseline.** The sentence in the paper is garbled: "whose accuracy is 93.92%
  lower than DNA-GAN". Most plausibly MAFFT = 93.92%, but we did not treat it as unambiguous and
  left it out.
- **MACL's 30x coverage on a dataset with mean 27.** Never explained in the paper. Their re-run
  of DNAformer (45.02% recovery) also disagrees with DNAformer's self-report (85.42%) by 40
  points. We reported MACL's numbers but drew no comparison from them.
- **TReconLM v1 versus v2.** v1 reports a different table (N = 10 fine-tuned failure 2.35e-2 in
  v1 against 1.37e-2 in v2) and different test-set sizes (5,235 against 5,109). COMPARISON.md
  uses v2 throughout.
- **DNAformer's Nature Methods body and Methods section are paywalled.** The numbers used come
  from the freely available Supplementary PDF, the abstract and figure captions, the arXiv v3
  preprint, and the authors' NVMW 2025 abstract. We did not read the paywalled main body.

## 6. Papers checked and ruled out

Do not use these as comparison points; they do not evaluate on this dataset.

- **RobuSeqNet** (Qin, Zhu, Xi, Song, *Robust multi-read reconstruction from noisy clusters using
  deep neural network for DNA storage*, Comput. Struct. Biotechnol. J. 23:1076–1087, 2024). Its
  §4.1 says "We use three well-known datasets ... Erlich et al., Organick et al., and Chandak et
  al." Its numbers on the Microsoft dataset exist only because TReconLM and MACL re-ran it. Note
  that the brief called this "the GAN-based / deep neural network multi-read paper"; the GAN one
  is a separate paper (DNA-GAN, Zheng et al., Sci Rep 2024), which does use the dataset.
- Nahum, Ben-Tolila, Anavy, *Single-Read Reconstruction ... Using Transformers* (arXiv:2109.05478):
  simulated data only.
- Hedges (Press et al., PNAS 2020): predates the dataset.
- **"DNArch": no such paper found.** Probably a misremembered name.
- Bibliography-only citations of Trellis BMA, checked and excluded: Welter et al.
  (arXiv:2406.12955), Neural Polar Decoders (arXiv:2506.17076), BCJRFormer (arXiv:2511.00999),
  Gungnir (Nat. Commun. 2026), DNA StairLoop (Nat. Commun. 2025), Beyond the Alphabet
  (arXiv:2410.06188), and several coding-theory preprints.

Unresolved, paywalled with no preprint, plausible users of the dataset: **DBSP** (IEEE TMBMC 2026,
DOI 10.1109/TMBMC.2025.3613268, same group as MACL) and *Multi-Bit Decision-Based Bitwise Majority
Alignment* (IEEE Comm. Letters 2026, DOI 10.1109/LCOMM.2025.3648494).

## 7. The dataset caveat, in the authors' own words

Identical in the GitHub README ("Note added on 8/12/2024") and arXiv:2107.06440 v2 §III:

> "the collection of 10,000 DNA sequences of length 110 generated for this study exhibits
> long-range dependencies instead of being uniformly random. This is due to an error in the
> generation process. Since the input sequences are not uniform, the clustering algorithm from
> [41] may have unexpected behavior and some recovered clusters may be malformed, making the
> trace reconstruction problem harder."

Corroborated independently by GradHC (Bioinformatics 2024, §4.1.1): "all algorithms performed
poorly on dataset IV ... such designs are likely to lead clustering algorithms to complete
failure."

Different papers see different cluster counts for the same file: 10,000 (Trellis BMA, TReconLM),
9,984 (BBS, Sabary, GradHC, MACL), 9,954 (DNAformer, own index binning), 2,833 (DNA-GAN, after a
≥30-read filter). Reported total error rate ranges from 4.72% to "roughly 7%". Any cross-paper
table on this dataset has to carry that.
