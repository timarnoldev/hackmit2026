# StrandAudit pitch

Follows the demo script in `PROJECT.md`: problem, rule audit, the loop on the Pareto plot, crossover matrix, image round trip.

**Before you pitch:** every `[RESULT: ...]` must be replaced with a number from `dnacodec.evaluate` on held-out seeds, or the line must be cut. Each result line below is written so it works whether the result is a gain or "the hand rules are already near optimal". Pick the matching variant and say it plainly.

**Setup:** dashboard open on the rule audit view, Nanopore selected. The image round trip precomputed, with one live run ready. Backup video queued.

---

## 3-minute version

### 0:00 to 0:25 | The problem (slide 1 or the dashboard's landing view)

> DNA can store data at extreme density for centuries. But writing and reading it is noisy: letters get swapped, added or dropped, and whole strands disappear.
>
> So every DNA storage pipeline follows a few hand-written rules. Never more than three of the same letter in a row. Keep GC content between 40 and 60%. Add 30% extra strands, just in case.
>
> Each rule costs you storage or reads. They were chosen once, copied from paper to paper, and applied to every sequencing machine. Nobody measures whether they actually pay off.

### 0:25 to 0:40 | What we built

> We built StrandAudit. It measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.
>
> One fixed test: a 20 KB file has to come back exactly in all 300 held-out trials. Same encoder, same decoder. The only things that change are the rules and the redundancy.

### 0:40 to 1:15 | Demo beat 1: the rule audit

*Click: rule audit view, Nanopore.*

> This is the rule audit. We switch each rule off, one at a time, and measure what it costs and what it buys at that recovery target.
>
> On Nanopore, the run-length rule [RESULT: verdict and effect, e.g. "pays off: without it you need N instead of M reads per strand" or "costs X and buys nothing measurable"].

*Click: switch to Illumina.*

> Same rule, on Illumina: [RESULT: verdict and effect on Illumina].
>
> Same rule, different channel, [RESULT: "different answer" or "same answer"]. That's the point: you only know once you measure it.

### 1:15 to 1:45 | Demo beat 2: the loop on the Pareto plot

*Click: Pareto view, Nanopore at a tight read budget.*

> Tier two goes beyond the hand rules. A small CNN learns from where our decoder actually fails, and the encoder picks the safest of several candidate strands. That costs no density at all.
>
> We know there is something to learn: on real Nanopore reads, the errors every read shares are 45 to 66% predictable from the local five-letter context. The hand rules don't cover those contexts.
>
> Here's the default codec, and here's each round of tuning. Down is fewer reads, right is more bits per base. [RESULT: where the tuned codec lands, e.g. "we reach the target with N instead of M reads per strand at the same density" or "the audit alone gets us here, and learned selection adds nothing measurable on this channel"].

*If time allows: trigger the one live alternation and let it run in the background.*

### 1:45 to 2:05 | Demo beat 3: the crossover matrix

*Click: crossover matrix.*

> How do we know the codec is tuned, and not just better? We swap them. The Nanopore codec runs on Illumina and the other way round.
>
> [RESULT: crossover outcome, e.g. "each one wins at home and loses its edge away" or "the Nanopore codec also holds on Illumina, so the gain there is general, not channel specific"].

### 2:05 to 2:35 | Demo beat 4: the image round trip

*Click: image demo. Encode, simulate Nanopore at 6 reads per strand, decode with both codecs.*

> Now something you can see. We encode an image into DNA, read it back through our Nanopore channel at only 6 reads per strand, and decode it with the default codec and the tuned one.
>
> [RESULT: what happens, e.g. "the default fails to recover the file; the tuned codec gets it back bit for bit" or "both recover it; the tuned one uses N% fewer strands"].

*Optional, only if built and working: tap the query on the ESP32-S3-BOX-3, which asks the GX10 for the verdict and shows it on the touch screen.*

### 2:35 to 3:00 | Why you can trust it, and close

> We have no wet lab, so we built the evidence to catch us out. Our simulator is calibrated on real Nanopore and Illumina reads and lands within about 3 points of real reads on held-out data. Every gain has to survive a second, structurally different simulator and a check on real reads.
>
> And if the answer on some channel is "the hand rules are already right", that's a result too. Now it's measured instead of assumed.
>
> StrandAudit. Measure the rule. Keep what pays.

---

## 60-second version

> DNA storage pipelines follow hand-written rules: no more than three of the same letter in a row, GC content between 40 and 60%, 30% extra strands. Each rule costs storage or reads, and they're applied to every sequencing machine. Nobody measures whether they pay off.
>
> StrandAudit does. For a given channel, it switches each rule off and measures, with the same encoder and the same decoder, what it takes to recover a 20 KB file in all 300 held-out trials. *(Show the rule audit.)* On Nanopore, [RESULT: one-line verdict]. On Illumina, [RESULT: one-line verdict].
>
> Then it learns from where the decoder fails what else to avoid. On real Nanopore reads, shared errors are 45 to 66% predictable from the five-letter context, which no hand rule covers. *(Show the Pareto plot.)* [RESULT: where the tuned codec lands vs the default].
>
> No wet lab, so our simulator is calibrated on real reads, within about 3 points on held-out data, and every gain must survive a second simulator. If the hand rules turn out to be right, that's measured now too. Measure the rule. Keep what pays.

---

## Anticipated judge questions

**"You have no wet lab. How do you know any of this is real?"**
We don't synthesize or sequence anything, and we say so. Three things keep us honest. First, the simulator is calibrated on real Nanopore and Illumina reads and matches the baseline decoder's accuracy on real reads within about 3 points at every read count, on held-out data no calibration step touched. The first version, which only matched average error rates, was far too easy, so we know averages aren't enough. Second, every gain has to survive Simulator B, which builds the same channel from different mechanisms and is never used for optimization. Third, the risk model's ranking is checked on real held-out reads: [RESULT: real-read AUC of the risk model]. A gain that disappears under those checks gets reported as simulator overfitting.

**"Isn't this just DNA Fountain?"**
The encoder is DNA Fountain style on purpose: an LT code that tries many seeds per strand and screens them with hand-written rules. We keep all of that. What we change is how the codec is configured: which rules are on, how much redundancy, and which candidate strand to keep, measured per channel. We don't claim a better encoder than DNA Fountain.

**"What if the hand rules are already optimal?"**
Then the tool says so, with measurements, and that's a useful result. An engineer learns that on this channel the defaults are right and can stop second guessing them. The rule audit holds either way, because it's a measurement, not a bet on the rules being wrong. [RESULT: say which channels this turned out to be the case for, if any.]

**"Isn't the improvement just your transformer decoder?"**
No. The default we compare against, system B in our ablation ladder, already uses the same decoder. B to C isolates the rule audit, C to D isolates learned selection. The decoder's own contribution is A to B, which DNAformer already showed, and we don't claim it. Both tiers also work with the plain majority vote decoder.

**"Couldn't you just buy accuracy with more redundancy?"**
That's exactly what we rule out. Codecs are judged on the Pareto front of bits per base against reads per strand at a fixed recovery target. A codec that needs more redundancy to hit the target doesn't count as better.

**"How is this different from DNAformer or adaptive constrained coding?"**
Those optimize the decoder, the constraints or the redundancy on their own. We close the loop: the decoder's actual failures on a target channel decide what the encoder avoids and how much redundancy the codec carries, and we measure the result against a fixed target. We compete at the system level, against the hand-tuned default.

**"Why not learn the encoder end to end?"**
Insertions and deletions are hard to differentiate through, and storage needs every bit back, not most of them. A Fountain code guarantees exact recovery once enough strands survive. We keep that guarantee and let learning decide only which candidate strands to use.

**"What has the risk model actually learned?"**
Honestly: the first version rediscovered the homopolymer rule, because run length was the only sequence effect in the simulator it trained on, and its ranking on real reads was weak. That's why we measured real context errors and put them into the simulator. [RESULT: top patterns the retrained model avoids per channel, and its real-read AUC].

**"The Microsoft dataset has known issues. Does that matter?"**
Yes, and we handle it. Its README says the references aren't uniformly random. We use it for calibration and reconstruction benchmarks, never to learn risky motifs. The risk model trains on strands we generate ourselves, and Simulator B uses a context table fit on the independent DNAformer dataset.

**"Is the transformer better than the baseline?"**
[RESULT: transformer vs majority vote at 2, 4 and 6 reads on the real held-out split]. The baseline is strong at high coverage (90.6% exact strands at 16 reads on real held-out data), so the room is at low coverage, where reading gets cheap. If the transformer doesn't beat it, the whole pipeline runs on the baseline and both claims still stand.

**"What would a real user do with this?"**
Describe their channel as a profile (technology, error rates, read budget, storage time), calibrate it on a sample of their own reads, and let the tool tell them which rules and how much redundancy to use. The cost figures in the dashboard use placeholder prices, and we label them that way.

**"What did the GX10 do?"**
All the heavy work runs on it: training the transformer decoder, labeling strands for the risk model (each simulated 32 times and decoded), and the recovery trials, 300 held-out trials per final codec, with the CPU work spread across its 20 cores.
