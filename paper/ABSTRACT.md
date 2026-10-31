# Abstract

**What an encoder should avoid depends on the class of decoder**

DNA data storage encodes under sequence rules — no homopolymer longer than three, GC content
between 40 and 60 percent — that are written as constants and copied between papers, as
though they were properties of the chemistry. We show they are properties of the chemistry
*and of the decoder*, and not merely of how strong the decoder is but of what kind it is. Under
algebraic error correction a strand is risky in proportion to how many errors it accumulates;
under learned reconstruction from multiple reads it is risky in proportion to how many of its
errors are *shared* across those reads, because an error one read makes is repaired by the
others for free while an error every read makes is unrecoverable at any coverage. These are
different quantities, so they imply different encoders, and the field writes one rule set for both.

We train a small model on nothing but the failures of a frozen reconstruction decoder and
read the learned rules back out of it. Without being told, it rates deletion contexts far above
substitution contexts of comparable measured error rate (0.934 against 0.444, against a background
of 0.526) and G/C homopolymer runs above A/T runs of equal length — distinctions that no rule
counting identical letters can express, and that would be meaningless under algebraic correction.
Used to choose, among equivalent encodings of the same data, the strand most likely to survive
voting, at zero cost in density, it raises file recovery from 27% to 98.3% at 4.5 reads per
strand with every other part of the codec held identical, over 300 held-out trials per point,
and the gain survives a structurally different simulator that is never used for tuning. The
gain shrinks as the decoder strengthens, from −3.95 to −2.76 points of strand failure,
which is the first evidence that the encoder's optimum tracks the decoder.

We also report a unit error that the natural approach invites. A per-strand risk score is
the obvious instrument for setting a global constraint, and it is the wrong one: our model
calls a homopolymer run of four nearly free per strand (0.436 against 0.460), while end to
end the field's limit of three beats a limit of four by 48 points of file recovery at 12 reads,
monotonically across limits of three, four, five, six and none. A file is over a thousand strands
and needs essentially all of them, so small per-strand differences compound. A per-sequence
score is correct for a per-sequence decision, such as how much parity a strand receives,
and incorrect for a constraint applied to every strand.

Concurrent independent work predicts per-sequence failure from a frozen decoder and reallocates
parity accordingly, on a simulated channel without context-dependent or read-shared errors. It
leaves sequences fixed where we change them, and its channel lacks the errors our finding is
about, so it neither reproduces nor contradicts the asymmetry reported here.  Our decoder is
not a contribution: a convolutional network over alignment statistics predicting a corrected
consensus is an established pattern, used here as a fixed instrument so that both sides of
every comparison share it. All channels are simulated and calibrated on real published reads;
the risk model's ranking transfers to real held-out Nanopore clusters (AUC 0.69 with coverage
held fixed, 0.61 with the longest run held fixed as well, where the hand rule itself falls to
chance), but we have not shown that selecting candidates by it lowers failures on synthesised DNA.
