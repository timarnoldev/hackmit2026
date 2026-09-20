# Devpost submission, final text

Paste these into the Devpost form fields. Numbers are the measured ones as of the night before
submission; check `docs/NUMBERS.md` for provenance and update if the final runs move them.
Only `[TEAM: ...]` needs filling in by a human.

## Inspiration

DNA can store data for centuries at extreme density, and the field has a set of rules everyone
follows when encoding: never more than three identical letters in a row, keep GC content between
40 and 60 percent, add 30 percent redundancy. We went looking for the paper that shows those
rules pay off on a given sequencer, and we could not find it. They are folklore, copied between
projects, applied to every channel. Every rule costs storage space. So we built the tool that
measures them.

## What it does

Erbgut tunes a DNA storage codec for one specific channel. You tell it your sequencing
technology, how many reads per strand you can afford, and how reliably the file must come back.
It then switches each coding rule on and off, measures what each one costs and what it buys, and
keeps only what pays off. On top of that it learns from where the decoder actually fails, so the
encoder can avoid patterns the hand written rules do not cover. The result is a codec tuned to
your channel, plus a verdict per rule.

Our measurements: on Nanopore the run length rule pays off, 19.5 reads per strand with it against
24.5 without. The GC rule pays off on neither channel. Tuning redundancy per channel takes
Nanopore from 19.5 reads down to 5.5, and on Illumina the loop switches both rules off and stores
31 percent more data per DNA letter at the same recovery target.

## How we built it

A Fountain code encoder that generates several candidate strands per slot, a channel simulator
calibrated on two public real sequencing datasets, a decoder, a learned risk model, and a search
loop that measures everything on held out trials.

The decoder is the part we are happiest with. A from scratch transformer with 9.6 million
parameters failed: it had to learn how to align reads that shift against each other, and after
hours of training it was still far behind the classic method. So we inverted it. The classic
algorithm does the alignment, which it is good at, and a small CNN with 0.8 million parameters
corrects what is left. It trained in 13 minutes and beats the classic method by 21 points on real
held out data.

Everything runs on an ASUS Ascent GX10. The live demo runs on an ESP32-S3-BOX-3 that decodes on
the chip itself, so it works unplugged from any laptop.

## Individual Contributions

[TEAM: who did what] Much of the implementation was written by coding agents working in parallel
on separate branches, while we owned the interfaces, the evaluation discipline and every number
we report.

## Challenges we ran into

Our own optimizer cheated, and we caught it. The loop tests hundreds of settings and keeps the
best, so the winner is partly lucky. On fresh seeds the chosen codec then failed 3 of 300 trials
and missed the target. We added a margin check on unseen seeds, three independent evaluation
blocks and selection on training data only.

Our best result was nearly an artifact. The GC rule looked worthwhile on Illumina, until we
noticed the dropout parameter behind it had never been calibrated and was 10 to 35 times higher
than anything in the real data. We measured it, fixed it, and the finding disappeared.

The risk model looked dead on real reads at AUC 0.516. It turned out that read count alone
predicts failure at 0.78, so a sequence only score cannot show up unless you hold coverage fixed.
Once we did, it reached 0.69.

## Accomplishments that we're proud of

Our simulator is within 3 points of real sequencing data at every read count, on data it never
saw. Our decoder reconstructs 88 percent of strands exactly at 6 reads per strand against 67
percent for the classic method, on real Nanopore reads. The learned candidate selection cuts
strand failures by 4 percentage points, and the gain survives a second simulator we built from
different mechanisms specifically to try to break it. And the whole thing runs on a
microcontroller on the table.

Mostly we are proud of what we did not claim. Every number has a held out measurement behind it,
and the documentation says where we are weaker than published work.

## What we learned

Put the model where the knowledge is missing, not where the problem is hardest. The classic
algorithm already solves alignment. What it lacks is knowledge about the channel, and that is
exactly what a small model can supply.

Also: measure the measurement. Three of our results changed once we looked at how they were
measured, twice against us and once in our favour.

## What's next for our project

The obvious next step is a wet lab. Everything we do is simulated, calibrated on real reads and
checked against them, but we have never had our own strands synthesized and sequenced. That is
the one test our firewall cannot replace.

Beyond that: a risk model that knows about coverage as well as sequence, a reference set whose
strands actually vary so the effect can be measured on real DNA, and per channel decoders instead
of one.

## Built with

python, pytorch, numpy, streamlit, plotly, rapidfuzz, uv, esp-idf/platformio, lovyangfx, c++,
nvidia gb10 (asus ascent gx10), esp32-s3
