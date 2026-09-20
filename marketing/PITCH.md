# Erbgut pitch

The 3-minute pitch, a 60-second version, and the judge questions with honest answers. Every number
here comes from [`docs/NUMBERS.md`](../docs/NUMBERS.md), which carries its conditions and
provenance.

Setup: dashboard open on the rule audit view, Nanopore selected. The image round trip precomputed,
with one live run ready.

---

## 3-minute version

### 0:00 to 0:25 | The problem

> DNA can store data at extreme density for centuries. But writing and reading it is noisy: letters get swapped, added or dropped, and whole strands disappear.
>
> So every DNA storage pipeline follows a few hand-written rules. Never more than three of the same letter in a row. Keep GC content between 40 and 60%. Add 30% extra strands, just in case.
>
> Each rule costs you storage or reads. They were chosen once, copied from paper to paper, and applied to every sequencing machine. Nobody measures whether they actually pay off.

### 0:25 to 0:40 | What we built

> We built Erbgut, German for the genetic material you inherit. It measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.
>
> One fixed test: a 20 KB file has to come back exactly in all 300 held-out trials. Same encoder, same decoder. The only things that change are the rules and the redundancy.

### 0:40 to 1:15 | Demo beat 1: the rule audit

*Click: rule audit view, Nanopore.*

> This is the rule audit. We switch each rule off, one at a time, and measure what it costs and what it buys at that recovery target.
>
> On Nanopore, the run-length rule pays off: with it you hit the target at 19.5 reads per strand, without it you need 24.5.

*Click: switch to Illumina.*

> Same rule on Illumina: no measurable benefit. 3 reads with it, 2.5 without.
>
> And the GC rule is the mirror image. Nothing on Nanopore, worth a read per strand on Illumina. Each of the two standard rules pays off on exactly one of the two channels. You only know once you measure it.

### 1:15 to 1:45 | Demo beat 2: learned selection

*Click: Pareto view, Nanopore at a tight read budget.*

> Tier two goes beyond the hand rules. A small CNN learns from where our decoder actually fails, and the encoder picks the safest of several candidate strands. That costs no density at all, because a Fountain encoder generates those candidates anyway.
>
> We know there is something to learn: on real Nanopore reads, the errors every read shares are 45 to 66% predictable from the local five-letter context. The hand rules don't cover those contexts.
>
> Here is the cleanest form of that. Same redundancy, same strand length, same rules, same 32 candidates, same decoder, same seeds, same file. The only difference is which model picks the candidate. At 4.5 reads per strand, the hand rules recover 27% of files. The learned scorer recovers 98%.

### 1:45 to 2:05 | Demo beat 3: the crossover matrix

*Click: crossover matrix.*

> How do we know the codec is tuned, and not just better? We swap them. The Illumina codec, run on the Nanopore channel, never recovers the file at any coverage we tested. It is tuned for a channel that doesn't drop letters in homopolymers, and Nanopore does.
>
> The other direction is the one to be careful with. The Nanopore codec does fine on Illumina, but only because it carries two and a half times the redundancy. That's why we judge on the Pareto front of bits per base against reads per strand, not on reads alone.

### 2:05 to 2:35 | Demo beat 4: the image round trip

*Click: image demo. Encode, simulate Nanopore at 6 reads per strand, decode with both codecs.*

> Now something you can see. We encode an image into DNA, read it back through our Nanopore channel at only 6 reads per strand, and decode it with the default codec and the tuned one. Five seeds fixed in advance. The default recovers the file zero times out of five. The tuned codec gets it back four times out of five.
>
> And it wins there by writing more spare strands, not by better rules: 0.63 bits per letter against 1.19. The page says that too.

### 2:35 to 3:00 | Why you can trust it, and close

> We have no wet lab, so we built the evidence to catch us out. Our simulator is calibrated on real Nanopore and Illumina reads and lands within about 3 points of real reads on held-out data. Every gain has to survive a second, structurally different simulator, and the learned selection gain does: 4.2 points on the firewall simulator against 4.0 on our own.
>
> And where the answer on a channel is "the hand rules are already right", that's a result too. Now it's measured instead of assumed.
>
> Erbgut. Measure the rule. Keep what pays.

---

## 60-second version

> DNA storage pipelines follow hand-written rules: no more than three of the same letter in a row, GC content between 40 and 60%, 30% extra strands. Each rule costs storage or reads, and they're applied to every sequencing machine. Nobody measures whether they pay off.
>
> Erbgut does. For a given channel, it switches each rule off and measures, with the same encoder and the same decoder, what it takes to recover a 20 KB file in all 300 held-out trials. *(Show the rule audit.)* On Nanopore the run-length rule pays off, 19.5 reads per strand against 24.5. On Illumina it buys nothing, and the GC rule is the other way round.
>
> Then it learns from where the decoder fails what else to avoid. *(Show the Pareto plot.)* Change only which model ranks the candidate strands, and at 4.5 reads per strand file recovery goes from 27% to 98%.
>
> No wet lab, so our simulator is calibrated on real reads, within about 3 points on held-out data, and every gain must survive a second simulator. Measure the rule. Keep what pays.

---

## Judge questions

**"You have no wet lab. How do you know any of this is real?"**
We don't synthesize or sequence anything, and we say so. Three things keep us honest. First, the simulator is calibrated on real Nanopore and Illumina reads and matches the baseline decoder's accuracy on real reads within about 3 points at every read count, on held-out data no calibration step touched. A version that only matched average error rates was far too easy, so we know averages aren't enough. Second, every gain has to survive Simulator B, which builds the same channel from different mechanisms and is never used for optimization. Third, the risk model's ranking is checked on real held-out reads: holding coverage fixed, it ranks real decoder failures at AUC 0.69, against 0.66 for the homopolymer rule alone and 0.50 for GC deviation.

**"Isn't this just DNA Fountain?"**
The encoder is DNA Fountain style on purpose: an LT code that tries many seeds per strand and screens them with hand-written rules. We keep all of that. What we change is how the codec is configured: which rules are on, how much redundancy, and which candidate strand to keep, measured per channel. We don't claim a better encoder than DNA Fountain.

**"What if the hand rules are already optimal?"**
Then the tool says so, with measurements, and that's a useful result. It is also what we found on one channel each: the homopolymer rule is right on Nanopore and dead weight on Illumina, the GC rule the other way round. An engineer learns which of the two their channel is paying for without reason.

**"Isn't the improvement just your decoder?"**
No. The default we compare against, system B in our ablation ladder, already uses the same decoder. B to C isolates the rule audit, C to D isolates learned selection. The decoder's own contribution is A to B, which DNAformer already showed, and we don't claim it. Both tiers also hold with the plain majority vote decoder: the tier-2 gain was first measured with the classic decoder and then reproduced with the learned one, smaller but still clearly separated from zero.

**"Couldn't you just buy accuracy with more redundancy?"**
That's exactly what we rule out. Codecs are judged on the Pareto front of bits per base against reads per strand at a fixed recovery target. A codec that needs more redundancy to hit the target doesn't count as better. The cleanest tier-2 measurement holds redundancy fixed for exactly this reason.

**"How is this different from DNAformer or adaptive constrained coding?"**
Those optimize the decoder, the constraints or the redundancy on their own. We close the loop: the decoder's actual failures on a target channel decide what the encoder avoids and how much redundancy the codec carries, and we measure the result against a fixed target. We compete at the system level, against the hand-tuned default.

**"Why not learn the encoder end to end?"**
Insertions and deletions are hard to differentiate through, and storage needs every bit back, not most of them. A Fountain code guarantees exact recovery once enough strands survive. We keep that guarantee and let learning decide only which candidate strands to use.

**"What has the risk model actually learned?"**
It rediscovers the homopolymer rule without being told, and puts its threshold at a run of 4 to 5 rather than the field's 3. It rates G and C runs above A and T runs, a distinction no standard rule makes. It calls the GC rule pointless. And it is alarmed by exactly the five-letter contexts where real Nanopore reads drop letters, while staying relaxed about the contexts where they substitute letters, which is right: voting repairs a substitution, a deletion shifts everything after it. It never saw that error table. `docs/LEARNED_RULES.md` has the probes.

**"Your model says a run of 4 is nearly free, but you kept the limit at 3. Which is it?"**
Both, and the gap is the interesting part. The model answers a per-strand question, and per strand the difference between a run of 3 and a run of 4 really is small: 0.436 against 0.460. But a file is 1,239 strands and needs essentially all of them, so small per-strand differences compound. Measured at file level, the field's limit of 3 is the best of five thresholds and every step looser costs recovery. The model is a good guide to *what* is dangerous and a bad guide to *where to draw a line*, which is why we measure end to end in reads per strand.

**"The Microsoft dataset has known issues. Does that matter?"**
Yes, and we handle it. Its README says the references aren't uniformly random. We use it for calibration and reconstruction benchmarks, never to learn risky motifs. The risk model trains on strands we generate ourselves, and Simulator B uses a context table fit on the independent DNAformer dataset.

**"Is your decoder state of the art?"**
No. TReconLM (TMLR 2025) beats it at every read count from 2 to 10 on this dataset, including where the coverage is genuinely matched, and it trains on the same split we do. We are level with a fine-tuned DNAformer and ahead of the classical algorithms, with 0.8M parameters against DNAformer's 100M and 13 minutes of training. `docs/COMPARISON.md` has the full table and is written to be unflattering where the numbers are. It does not change the claim, because the claim is about the encoder with the decoder held fixed.

**"What would a real user do with this?"**
Describe their channel as a profile (technology, error rates, read budget, storage time), calibrate it on a sample of their own reads, and let the tool tell them which rules and how much redundancy to use. The cost figures in the dashboard use placeholder prices, and we label them that way.

**"What did the GX10 do?"**
All the heavy work: training the polisher and the risk model, labeling strands (each simulated 32 times and decoded), and the recovery trials, 300 held-out trials per final codec, with the CPU work spread across its 20 cores.
