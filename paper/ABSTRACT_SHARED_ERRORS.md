# Short abstract: errors that survive voting

*Puts the shared-error mechanism first, which is what separates this work from concurrent
per-sequence failure prediction. Numbers are registered in [`docs/NUMBERS.md`](../docs/NUMBERS.md)
and [`docs/LEARNED_RULES.md`](../docs/LEARNED_RULES.md).*

---

DNA storage reads every strand many times and votes. An error that one read makes is outvoted
by the others; an error that every read of a strand makes survives any number of reads. What
should make an encoder avoid a sequence is therefore not where errors happen, but where they
are *shared*. On real Nanopore reads such shared errors are largely predictable from the local
5-letter context (45 to 66% of their between-position variance, hot-spot AUC 0.80 to 0.90,
the same context families leading in two independent datasets), and the field's sequence rules,
which count identical letters and GC content, do not describe them.

We train a small model on nothing but the failures of a frozen multi-read decoder, on a channel
calibrated to carry these shared context errors, and read its learned rules back out. Without being
told, it rates deletion contexts far above substitution contexts of comparable measured error
rate (0.934 against 0.444, background 0.526), consistent with voting repairing substitutions
more readily than deletions. Used to choose, among equivalent encodings of the same data,
the strand most likely to survive voting, at zero cost in density, it raises file recovery
from 27% to 98.3% at 4.5 reads per strand with the rest of the codec held identical, and the
gain survives a structurally different simulator never used for tuning.

Concurrent work predicts per-strand failure to reallocate parity, on a channel without context
or read-shared errors, and finds the signal weak. Our account predicts exactly that. We test it
directly by regenerating the same context errors once shared across reads and once independent
per read at equal rate. All channels are simulated and calibrated on real published reads;
we have not shown the gain on synthesised DNA.
