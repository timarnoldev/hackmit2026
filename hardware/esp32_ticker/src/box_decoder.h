// The classic majority vote decoder, on the device.
//
// A line for line port of dnacodec/baseline.py: medoid draft, align every read to it with
// Levenshtein opcodes, vote per draft position including deletions and insertions, rebuild,
// iterate, then force the strand to its exact length. Pure integer work, no Arduino and no
// floating point, so the same file compiles into the firmware and into a host binary that
// is diffed against Python (see tools/verify_decoder.cpp).
//
// The tie breaking is the part that has to match, not just the distance: the same cluster
// must produce the same string as rapidfuzz does, or the box and the Mac would quietly
// disagree about what was decoded. tools/verify_decoder.cpp is what proves it does.

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace boxdec {

// Tie break order for equally optimal alignments, 0..5. See box_decoder.cpp.
extern int gBacktraceOrder;

// dnacodec.baseline.MAX_READS: larger clusters are subsampled to this many reads.
static const int kMaxReads = 16;
// Long enough for the strands we ship (110) plus the slack an alignment can add.
static const int kMaxLen = 160;

// One cluster's reads, already trimmed to kMaxReads by the caller or by subsample().
struct Cluster {
  int n = 0;
  const char *read[kMaxReads] = {nullptr};
  int len[kMaxReads] = {0};
};

// Deterministic, order spread subsample: k evenly spaced reads, no randomness.
// Mirrors dnacodec.baseline.subsample, including its rounding.
void subsample(const char *const *reads, const int *lens, int n, int k, Cluster &out);

// The consensus of one non empty cluster, exactly strandLength bases long.
// Returns the number of bases written, or 0 when the cluster has no usable read.
int reconstruct(const Cluster &c, int strandLength, int iterations, char *out, size_t outCap);

// The medoid read, the single raw read the vote starts from. The display shows the
// difference between this and the consensus as the corrections the vote made.
int pickDraft(const Cluster &c, int strandLength, char *out, size_t outCap);

// Levenshtein distance, exposed for the verifier.
int editDistance(const char *a, int na, const char *b, int nb);

// Where a noisy read disagrees with the decoded strand, in read coordinates, as one kind
// per position: 'x' wrong letter, 'e' an extra letter the strand does not have, 'm' a letter
// the read is missing in front of this position, ' ' nothing. Mirrors read_marks() in
// scripts/ticker_server.py, so the box and the Mac mark the same things.
void readMarks(const char *read, int nr, const char *final, int nf, int visible, char *out);

// What the decoder changed, in final-strand coordinates: 's' substituted, 'i' inserted,
// 'd' deleted a letter in front of this position. `replaced` receives the letter `before`
// had at each marked position, or '-' where it had none. Returns the total number of edits
// over the whole strand, which is what the header counts, not just the visible window.
int fixMarks(const char *before, int nb, const char *final, int nf, int visible, char *marks,
             char *replaced);

}  // namespace boxdec
