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
[ERRORS.md](ERRORS.md).

---

## 1. It found the homopolymer rule by itself, and disagrees about where to draw the line

Nanopore, a single run of identical letters of length r inserted into an otherwise unremarkable
strand:

| Run length | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|---|
| Predicted risk | 0.429 | 0.430 | 0.436 | 0.460 | 0.518 | 0.603 | 0.689 | 0.756 | **0.833** |

Flat up to 4, then it climbs steeply. **The standard rule in the field forbids runs longer than
3.** The model says the damage starts at 5, so the usual rule is stricter than the channel
requires, and that strictness costs candidates for no benefit.

## 2. It considers the GC rule pointless

Nanopore, GC content varied while runs are capped at 3, so this isolates GC:

| GC content | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 |
|---|---|---|---|---|---|---|---|
| Predicted risk | 0.453 | 0.455 | 0.459 | 0.465 | 0.473 | 0.482 | 0.489 |

A spread of 3.6 points across the entire range, and in the wrong direction for a rule that wants
GC near 0.5. The independent rule audit reached the same verdict from a completely different
measurement: switching the GC rule off does not cost reads.

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

## Why this matters for the project

Tier 2 of our claim is that decoder failures can teach the encoder what to avoid. This document
is the qualitative side of that claim, next to the quantitative one in
[NUMBERS.md](NUMBERS.md): learned candidate selection cuts strand failures by about 4 points on
Nanopore, and the gain survives a structurally different simulator.

Read together, the two say the same thing from different directions. The model finds the known
rule, corrects its threshold, discards the rule that does not pay, adds a distinction nobody
writes down, and ranks patterns by how much they hurt the decoder rather than by how often the
channel gets them wrong.

---

# A second finding, and the check that took it back

The rule audit reported both standard rules as **harmful** on `nanopore_budget`, reproducibly
across two runs at 3 x 300 held-out trials: 24.5 reads with the rules, 19.5 without the
homopolymer rule, 16.0 without the GC rule. We went looking for the mechanism, found a plausible
one, and then a control measurement contradicted the whole thing. Both halves are below, because
the second half is the more useful lesson.

## The mechanism we found, which is real

Screening candidate droplets against the sequence rules also screens their **seeds**. The seed
lives in the first 16 bases of the strand and has to satisfy the rules like everything else, and
the seed is what selects which data chunks a droplet combines. Filtering seeds therefore filters
the structure of the erasure code. Encoding the same file three ways:

| | Mean droplet degree | Minimum coverage of a data chunk |
|---|---|---|
| Rules applied to the whole strand (standard) | 9.83 | **3** |
| Rules applied to the payload only | 10.37 | 5 |
| No rules | 11.97 | 6 |

The worst-covered chunk sits in 3 droplets instead of 6. Lose those three and the file is gone,
however clean the sequence was. That effect is real and reproducible, and `constrain_seed=False`
in `EncoderSettings` now exists to avoid it.

It is also **not** a sequence effect: without the homopolymer rule, 25.4% of strands carry a run
of 5 or more and their mean deletion multiplier is worse (1.013 against 0.931) by our own
calibrated channel table. By sequence quality alone the rule should help.

## The control that contradicted the conclusion

A direct measurement of the same three codecs, same channel, same decoder, 300 held-out trials:

| Codec | Reads per strand needed |
|---|---|
| Rules on, seed screened too (standard) | **16.0** |
| Rules on, payload only | target not met within the grid (> 16) |
| Rules off | target not met within the grid (> 16) |

That is the opposite ranking to the audit, with identical encoder settings. The explanation is
not a bug in either measurement, it is the metric: the recovery target is "all 300 trials
recover", and around 16 reads the recovery rate sits right at the knife edge, so a single failed
trial moves the answer by several reads. Different held-out seed blocks then disagree.

## What we actually claim

- **The redundancy result is robust.** Tuning redundancy per channel takes Nanopore from 24.5
  reads to 6.0 at the recovery target, and that reproduces across every run and every seed block
  we measured.
- **The two hand rules show no measurable benefit** on either channel, and the model's own
  probes agree (flat GC response, and a homopolymer threshold at 4 to 5 rather than 3).
- **We do not claim the rules are harmful.** The audit said so twice, and a control measurement
  said the reverse, so the honest statement is that the effect is inside the noise of an
  all-or-nothing target at this operating point.
- **The seed screening effect is real but its cost is unmeasured.** It is a clean hypothesis for
  future work, with the code path already in place.

This is the least comfortable page in the repo, and it is the one we would show first to anyone
asking how carefully we measured.
