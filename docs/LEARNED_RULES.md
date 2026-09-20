# What the risk model learned, read back out of it

We never taught the model a rule. It only ever saw which strands the decoder got wrong on a
given channel. Afterwards we asked it what it considers dangerous.

Everything here comes from the trained model of the final run, probed by inserting patterns into
random backgrounds:

```bash
uv run --extra train python scripts/what_the_model_learned.py --run-id run5
```

Full output: [`results/run5_learned_rules.txt`](../results/run5_learned_rules.txt). How the model
is built and trained: [MODELS.md](MODELS.md), section 2. What the channel actually does:
[ERRORS.md](ERRORS.md). Every number below is registered with its provenance in
[NUMBERS.md](NUMBERS.md).

---

## 1. It found the homopolymer rule by itself, and puts the line in a different place

Nanopore, a single run of identical letters of length r inserted into an otherwise unremarkable
strand:

| Run length | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|---|
| Predicted risk | 0.429 | 0.430 | 0.436 | 0.460 | 0.518 | 0.603 | 0.689 | 0.756 | **0.833** |

Flat up to 4, then it climbs steeply. **The standard rule in the field forbids runs longer than
3**, and the model says the damage starts at 5.

Where the line belongs is a claim about reads per strand, so we measured it in reads per strand
(`scripts/rule_recovery_curve.py --set threshold`, 300 held-out trials per point, recovery rate
against mean reads per strand):

| Limit on the longest run | 12 reads | 14 reads | 16 reads |
|---|---|---|---|
| **3, the field's rule** | **0.807** | **0.993** | 1.000 |
| 4 | 0.327 | 0.990 | 1.000 |
| 5, where the model draws the line | 0.030 | 0.940 | 1.000 |
| 6 | 0.003 | 0.587 | 0.997 |
| no limit | 0.000 | 0.287 | 0.990 |

Perfectly monotone: every step you loosen the rule costs recovery, and the field's 3 is the best
of the five. Full data:
[`results/rule_recovery_curve_threshold_nanopore_budget.json`](../results/rule_recovery_curve_threshold_nanopore_budget.json).

**The two measurements answer different questions, and the gap between them is the finding.** The
model answers a per-strand question: how likely is *this* strand to come back wrong. Between a
run of 3 and a run of 4 the answer is 0.436 against 0.460, a small difference, and the model is
not wrong about that. But a file is 1,239 strands and it needs essentially all of them. Small
per-strand differences compound into large file-level ones, which is why a limit the model
considers nearly free costs half the recoveries at 12 reads.

So the model is a good guide to *what* is dangerous and a bad guide to *where to draw a line*.
That is the reason this project measures end to end in reads per strand rather than trusting a
risk score.

## 2. It considers the GC rule pointless

Nanopore, GC content varied while runs are capped at 3, so this isolates GC:

| GC content | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 |
|---|---|---|---|---|---|---|---|
| Predicted risk | 0.453 | 0.455 | 0.459 | 0.465 | 0.473 | 0.482 | 0.489 |

A spread of 3.6 points across the entire range, and in the wrong direction for a rule that wants
GC near 0.5. The independent rule audit reaches the same verdict from a completely different
measurement: switching the GC rule off does not cost reads on this channel.

## 3. The patterns it names, and one distinction no standard rule makes

Most dangerous 5-mers, against a random background strand at **0.526**:

| Pattern | GGGGG | CCCCC | AGGGG | TCCCC | TTTTT | CGGGG | AAAAA | GGGGA | CCCCT | GGGGT |
|---|---|---|---|---|---|---|---|---|---|---|
| Risk | 0.707 | 0.685 | 0.638 | 0.615 | 0.613 | 0.611 | 0.608 | 0.603 | 0.596 | 0.595 |

They are all runs, which is expected. The interesting part is the ordering: **G and C runs rank
above A and T runs** (GGGGG 0.707 against AAAAA 0.608). No standard rule makes that distinction,
it simply counts identical letters.

## 4. The cross-check: it rediscovered what we measured in real data

Our channel model carries a per-5-mer error table fitted on real Nanopore reads. **The risk model
never saw that table.** It only saw decoder failures. Asking it about the table's worst patterns:

| Worst contexts for **deletions** in the real data | GAAAG (71x) | AAAAG (13x) | CAAAG (23x) | TAAAG (20x) |
|---|---|---|---|---|
| The model's risk for that pattern | **0.934** | **0.973** | 0.775 | 0.610 |

| Worst contexts for **substitutions** in the real data | CCCGA (12.8x) | TCGGG (8.2x) | CCCGT (8.0x) | CCCGG (7.3x) |
|---|---|---|---|---|
| The model's risk for that pattern | 0.444 | 0.465 | 0.530 | 0.484 |

**It is alarmed by the deletion contexts and relaxed about the substitution contexts**, even
though the substitution contexts have strongly elevated error rates too.

That is the right call, and nobody told it: majority voting across reads repairs a substitution,
because the other reads outvote it. A deletion shifts everything after it and is what actually
destroys a strand. **The model did not learn where errors happen. It learned where errors are
fatal.** A hand written rule cannot make that distinction, because it never sees a decoder.

## 5. On Illumina it learned that there is nothing to learn

Every pattern, every run length and every GC value comes back at **risk 0.000**. The channel is
clean enough that the decoder does not fail on sequence grounds at all, so there is nothing for
candidate selection to avoid.

This is the same conclusion the rule audit reaches on that channel, and it is why the loop
switches both hand rules off there and spends the freedom on density instead.

---

# The same two rules, measured end to end

The sections above are what the model believes. This one is what the file does. Measured as a
recovery curve at 300 held-out trials per point: the share of trials in which the 20 KB file came
back byte for byte, at each coverage, same decoder, same seeds, same file.

```bash
uv run python scripts/rule_recovery_curve.py --profile nanopore_budget --trials 300
```

`nanopore_budget`, recovery rate against mean reads per strand:

| codec | 10 | 12 | 14 | 16 | 18 | 20 | 22 | 24 |
|---|---|---|---|---|---|---|---|---|
| both rules (standard) | 0.000 | **0.807** | **0.993** | 1.000 | 1.000 | 0.993 | 0.997 | 1.000 |
| homopolymer rule off | 0.000 | 0.000 | 0.287 | 0.990 | 1.000 | 1.000 | 1.000 | 1.000 |
| GC rule off | 0.000 | 0.833 | 0.987 | 1.000 | 0.993 | 1.000 | 0.997 | 1.000 |
| both rules off | 0.000 | 0.000 | 0.257 | 0.977 | 1.000 | 1.000 | 1.000 | 1.000 |
| both rules, payload only | 0.000 | 0.713 | 0.997 | 0.997 | 1.000 | 1.000 | 1.000 | 1.000 |

Full data: [`results/rule_recovery_curve_rules_nanopore_budget.json`](../results/rule_recovery_curve_rules_nanopore_budget.json).

**The homopolymer rule pays off, clearly.** At 12 reads per strand it is the difference between
recovering the file four times in five and never recovering it at all. That is worth roughly two
reads per strand, and it agrees with the rule audit's headline in
[NUMBERS.md](NUMBERS.md) (19.5 reads with the rule, 24.5 without).

**The GC rule is neutral** on this channel, exactly as section 2 says the model believes. Its
curve tracks the standard codec to within a fraction of a point at every coverage (0.833 against
0.807 at 12, with a standard error of 0.023, so the small lead is noise). Three independent
measurements agree on it: the model probe, the rule audit, and this curve.

## Why we compare on curves, not on the threshold

"Fewest reads at which *all* trials recover" is the headline number, because "the file always
comes back" is the promise a storage system has to make. It is a brittle thing to *compare* on.
The standard codec reaches 1.000 at 16 reads, then dips back to 0.993 at 20 and 0.997 at 22:
single failures out of 300, deep inside the region where the codec plainly works. An
all-or-nothing threshold reads those dips as failure and reports a number in the twenties for a
codec that is already reliable at 16, and the ranking it produces can flip on a single trial.

So the threshold stays as the headline and the curve is what two close codecs are compared on.

## Screening seeds thins the erasure code, and the sequence benefit outweighs it

A Fountain droplet's seed lives in the first 16 bases, and those bases have to satisfy the
sequence rules too, so screening candidates also screens seeds and thins out the code. Over the
strand set of the default codec:

| | Mean droplet degree | Minimum coverage of a data chunk |
|---|---|---|
| Default, both rules | 9.83 | **3** |
| Rules off | 11.97 | **6** |

The last row of the curve table is the direct test of what that costs: apply both rules to the
payload only (`EncoderSettings(constrain_seed=False)`), leave the seed bases unconstrained, and
the structural cost disappears by construction. It does not help. At 12 reads that codec recovers
0.713 against 0.807 for the standard one. The same three codecs measured as a threshold rather
than a curve: `uv run python scripts/seed_constraint_test.py --trials 300`.

So the trade is real and it runs in favour of the rule: **screening seeds does thin the erasure
code, and the sequence benefit outweighs it anyway.** On this channel, paid in reads per strand,
the homopolymer rule is worth more than the droplet degree it costs.

## On Illumina, at these settings, there is nothing to trade

All five codecs are identical from 3 reads per strand upward, and within noise at 2
(0.957 to 0.970). The channel is clean enough that sequence constraints change nothing, which is
the same answer the model gives in section 5.

**One caveat, so this is not over-read.** This curve runs the *default* codec at 1.20 bits per
base. The rule audit in [NUMBERS.md](NUMBERS.md) runs the *tuned* Illumina codec, which spends the
clean channel on density instead of margin, and at that operating point it finds the GC rule does
pay off (3.0 reads with it against 4.0 without). The two are not in conflict: they are different
codecs. A rule that is free to drop when you have margin to spare is not necessarily free to drop
once you have spent that margin. Which is, in a sentence, the reason this project measures rules
per channel *and* per operating point rather than once.

---

## Why this matters for the project

Tier 2 of our claim is that decoder failures can teach the encoder what to avoid. This document
is the qualitative side of that claim, next to the quantitative one in
[NUMBERS.md](NUMBERS.md): learned candidate selection cuts strand failures by about 4 points on
Nanopore, and the gain survives a structurally different simulator.

Read together, the two say the same thing from different directions. The model finds the known
rule, moves its threshold, discards the rule that does not pay, adds a distinction nobody writes
down, and ranks patterns by how much they hurt the decoder rather than by how often the channel
gets them wrong.
