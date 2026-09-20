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

# A second finding: why the hand rules actually hurt on our Nanopore channel

The rule audit says both standard rules are **harmful** on `nanopore_budget`, reproducibly at
3 x 300 held-out trials: reads needed go from 24.5 with the rules to 19.5 without the homopolymer
rule and 16.0 without the GC rule. That is a large effect in the wrong direction, so we went
looking for the mechanism instead of reporting it as a curiosity.

**It is not the sequences.** Without the homopolymer rule, 25.4% of strands contain a run of 5 or
more, and by our own calibrated channel table their mean deletion multiplier is *worse*
(1.013 against 0.931). By sequence quality alone, the rule should help.

**It is the code.** Encoding the same file with and without the rules:

| | Mean droplet degree | Minimum coverage of a data chunk |
|---|---|---|
| Default, both rules | 9.83 | **3** |
| Rules off | 11.97 | **6** |

The seed of a Fountain droplet lives in the first 16 bases of the strand, and **those bases have
to satisfy the sequence rules too**. Seeds whose base-4 spelling contains a long run, or whose
letters push the strand out of the GC window, are rejected. Since the seed is what selects which
data chunks a droplet combines, filtering seeds filters the *structure of the code*: chunk
coverage becomes uneven, and the worst-covered chunk sits in 3 droplets instead of 6. Lose those
three and the file is gone, however clean the sequence was.

So the rule trades a small, real sequence benefit for a structural weakness in the erasure code,
and on this channel the trade is a loss.

**Why this is worth saying out loud.** DNA Fountain, the standard construction, screens candidate
droplets against exactly these constraints. Our measurement says that screening carries a hidden
cost that is not usually accounted for, and it is easy to avoid:

1. apply the constraints to the payload only, not to the seed bases, or
2. encode the seed so that it satisfies the constraints by construction, so no droplet is ever
   rejected for its seed.

We did not implement either, and we do not claim a fix that we have not measured. What we claim
is the measurement: on a channel calibrated to real Nanopore reads, screening droplets by the two
standard rules costs more reads than it saves, and the reason is the erasure code, not the
chemistry.
