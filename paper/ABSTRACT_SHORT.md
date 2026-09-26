# Short abstract

*For introducing the work to a potential academic collaborator.*

---

DNA data storage encodes under sequence rules — no homopolymer longer than three, GC content
between 40 and 60 percent — treated as constants of the chemistry. We find they are properties of
the chemistry *and of the decoder*. Under algebraic error correction a strand is risky in
proportion to how many errors it accumulates; under reconstruction from multiple reads it is risky
in proportion to how many errors are *shared* across them, since voting repairs the rest for free.

A small model trained on nothing but a frozen reconstruction decoder's failures confirms this
without being told: it rates deletion contexts at 0.934 and substitution contexts of comparable
measured error rate at 0.444. Used as the encoder's candidate scorer, at no cost in density, it
lifts file recovery from 27% to 98.3% at 4.5 reads per strand with every other part of the codec
held identical, over 300 held-out trials per point, and the gain survives a simulator never used
for tuning. It shrinks as the decoder strengthens, which is the first evidence that the encoder's
optimum tracks the decoder.

Channels are simulated and calibrated on real published reads. The ranking transfers to real
held-out Nanopore clusters; we have not shown it lowers failures on synthesised DNA.

---

**The ask:** we think this is a workshop or methods paper and we would like someone who knows the
field to tell us whether that judgement is right, and what it would take.
