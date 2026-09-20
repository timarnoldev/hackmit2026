// The classic majority vote decoder, on the device. See box_decoder.h.
//
// Ported from dnacodec/baseline.py. Every tie break here exists because the Python side has
// one, and getting them wrong does not produce a crash, it produces a different strand.
// tools/verify_decoder.cpp diffs this against Python on real clusters for exactly that
// reason.

#include "box_decoder.h"

#include <string.h>

namespace boxdec {

static const char kAlphabet[4] = {'A', 'C', 'G', 'T'};
static const int kDel = 4;

// Kept so the host verifier keeps building; the alignment no longer branches on it.
int gBacktraceOrder = 0;

static inline int baseIndex(char c) {
  switch (c) {
    case 'C': return 1;
    case 'G': return 2;
    case 'T': return 3;
    default: return 0;  // non ACGT votes as A, like dnacodec.baseline._LOOKUP
  }
}

// ---------------------------------------------------------------- subsample

// numpy rounds halves to even, and np.linspace(...).round() is what picks the reads, so a
// plain +0.5 would silently choose a different subset on some cluster sizes.
static int roundHalfToEven(double v) {
  const double f = (v < 0) ? -v : v;
  const long i = (long)f;
  const double frac = f - (double)i;
  long r;
  if (frac > 0.5) r = i + 1;
  else if (frac < 0.5) r = i;
  else r = (i % 2 == 0) ? i : i + 1;
  return (int)((v < 0) ? -r : r);
}

void subsample(const char *const *reads, const int *lens, int n, int k, Cluster &out) {
  out.n = 0;
  if (n <= 0) return;
  if (n <= k) {
    for (int i = 0; i < n && out.n < kMaxReads; ++i) {
      out.read[out.n] = reads[i];
      out.len[out.n] = lens[i];
      ++out.n;
    }
    return;
  }
  for (int j = 0; j < k && out.n < kMaxReads; ++j) {
    // np.linspace(0, n - 1, k)
    const double v = (k == 1) ? 0.0 : (double)(n - 1) * (double)j / (double)(k - 1);
    int idx = roundHalfToEven(v);
    if (idx < 0) idx = 0;
    if (idx > n - 1) idx = n - 1;
    out.read[out.n] = reads[idx];
    out.len[out.n] = lens[idx];
    ++out.n;
  }
}

// ---------------------------------------------------------------- edit distance

int editDistance(const char *a, int na, const char *b, int nb) {
  static uint16_t prev[kMaxLen + 1], cur[kMaxLen + 1];
  if (na > kMaxLen) na = kMaxLen;
  if (nb > kMaxLen) nb = kMaxLen;
  for (int j = 0; j <= nb; ++j) prev[j] = (uint16_t)j;
  for (int i = 1; i <= na; ++i) {
    cur[0] = (uint16_t)i;
    for (int j = 1; j <= nb; ++j) {
      const uint16_t sub = (uint16_t)(prev[j - 1] + (a[i - 1] != b[j - 1] ? 1 : 0));
      const uint16_t del = (uint16_t)(prev[j] + 1);
      const uint16_t ins = (uint16_t)(cur[j - 1] + 1);
      uint16_t best = sub < del ? sub : del;
      if (ins < best) best = ins;
      cur[j] = best;
    }
    memcpy(prev, cur, sizeof(uint16_t) * (nb + 1));
  }
  return prev[nb];
}

// ---------------------------------------------------------------- the draft

int pickDraft(const Cluster &c, int strandLength, char *out, size_t outCap) {
  if (c.n == 0) return 0;
  int best = 0;
  if (c.n <= 2) {
    // min by |len - strandLength|, first minimum wins, like Python's min()
    int bestGap = c.len[0] - strandLength;
    if (bestGap < 0) bestGap = -bestGap;
    for (int i = 1; i < c.n; ++i) {
      int gap = c.len[i] - strandLength;
      if (gap < 0) gap = -gap;
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    }
  } else {
    int total[kMaxReads];
    for (int i = 0; i < c.n; ++i) total[i] = 0;
    for (int i = 0; i < c.n; ++i)
      for (int j = i + 1; j < c.n; ++j) {
        const int d = editDistance(c.read[i], c.len[i], c.read[j], c.len[j]);
        total[i] += d;
        total[j] += d;
      }
    int bestGap = c.len[0] - strandLength;
    if (bestGap < 0) bestGap = -bestGap;
    int bestTotal = total[0];
    for (int i = 1; i < c.n; ++i) {
      int gap = c.len[i] - strandLength;
      if (gap < 0) gap = -gap;
      // (total, |len - strandLength|) ascending, first minimum wins
      if (total[i] < bestTotal || (total[i] == bestTotal && gap < bestGap)) {
        bestTotal = total[i];
        bestGap = gap;
        best = i;
      }
    }
  }
  int n = c.len[best];
  if ((size_t)n >= outCap) n = (int)outCap - 1;
  memcpy(out, c.read[best], (size_t)n);
  out[n] = 0;
  return n;
}

// ---------------------------------------------------------------- votes

// The vote columns of one draft against every read, the same three arrays
// dnacodec.baseline._votes builds.
struct Votes {
  int32_t base[kMaxLen][5];      // per draft position: A, C, G, T, missing
  int32_t ins[kMaxLen + 1];      // reads with an extra base in the gap in front of a position
  int32_t insBase[kMaxLen + 1][4];
  int nPos = 0;
  int nReads = 0;
};

static Votes gVotes;                              // one at a time, too big for the stack
static uint8_t gDP[(kMaxLen + 1) * (kMaxLen + 1)];  // the alignment matrix, reused per read

// Align one read to the draft and fold it into the vote columns.
//
static void alignInto(const char *draft, int nd, const char *read, int nr, Votes &v) {
  const int stride = nr + 1;
  uint8_t *dp = gDP;
  for (int j = 0; j <= nr; ++j) dp[j] = (uint8_t)(j > 255 ? 255 : j);
  for (int i = 1; i <= nd; ++i) {
    uint8_t *row = dp + (size_t)i * stride;
    const uint8_t *up = dp + (size_t)(i - 1) * stride;
    row[0] = (uint8_t)(i > 255 ? 255 : i);
    for (int j = 1; j <= nr; ++j) {
      const int sub = up[j - 1] + (draft[i - 1] != read[j - 1] ? 1 : 0);
      const int del = up[j] + 1;
      const int ins = row[j - 1] + 1;
      int best = sub < del ? sub : del;
      if (ins < best) best = ins;
      row[j] = (uint8_t)best;
    }
  }

  // Walk back to the origin, preferring a deletion, then an insertion, then a match or
  // replace. Levenshtein has many equally short alignments and they vote differently, so
  // this order is not cosmetic: it is the one of the six that agrees with rapidfuzz most
  // often, measured on real clusters by tools/verify_decoder.py. It is not a perfect match,
  // because rapidfuzz recovers its alignment from Myers' bit vectors rather than from a
  // matrix walk, and the two pick different tied alignments on some clusters. See the
  // verifier's report for how often, and for the accuracy that results either way.
  int i = nd, j = nr;
  while (i > 0 && j > 0) {
    const int here = dp[(size_t)i * stride + j];
    const int up = dp[(size_t)(i - 1) * stride + j];
    const int left = dp[(size_t)i * stride + (j - 1)];
    const int diag = dp[(size_t)(i - 1) * stride + (j - 1)];
    const int cost = (draft[i - 1] != read[j - 1]) ? 1 : 0;

    if (here == up + 1) {  // deletion: the draft has a base this read does not
      --i;                 // the position keeps its "missing" vote
    } else if (here == left + 1) {  // insertion: the read has an extra base in this gap
      --j;
      v.ins[i] += 1;
      v.insBase[i][baseIndex(read[j])] += 1;
    } else if (here == diag + cost) {  // match or replace
      --i;
      --j;
      v.base[i][baseIndex(read[j])] += 1;
    } else {
      break;  // cannot happen for a consistent matrix, but never loop forever on the box
    }
  }

  while (j > 0) {  // the read starts before the draft does: all insertions into gap 0
    --j;
    v.ins[0] += 1;
    v.insBase[0][baseIndex(read[j])] += 1;
  }
  // leftover i > 0 are deletions, which is the absence of a vote, so nothing to record
}

static void buildVotes(const char *draft, int nd, const Cluster &c, Votes &v) {
  v.nPos = nd;
  v.nReads = c.n;
  memset(v.base, 0, sizeof(int32_t) * 5 * (size_t)(nd > 0 ? nd : 1));
  for (int p = 0; p < nd; ++p)
    for (int b = 0; b < 5; ++b) v.base[p][b] = 0;
  for (int g = 0; g <= nd; ++g) {
    v.ins[g] = 0;
    for (int b = 0; b < 4; ++b) v.insBase[g][b] = 0;
  }
  for (int r = 0; r < c.n; ++r) alignInto(draft, nd, c.read[r], c.len[r], v);
  // every read that did not vote a base at a position counts as a deletion there
  for (int p = 0; p < nd; ++p) {
    int seen = 0;
    for (int b = 0; b < 4; ++b) seen += v.base[p][b];
    v.base[p][kDel] = c.n - seen;
  }
}

// ---------------------------------------------------------------- rebuild

// Plurality per draft position plus majority voted insertions. Ties keep the draft: a draft
// base beats an equally voted other base or deletion, and an insertion needs a strict
// majority of the reads.
static int rebuild(const char *draft, int nd, const Votes &v, int nReads, char *out,
                   size_t outCap) {
  int n = 0;
  for (int g = 0; g <= nd; ++g) {
    if (v.ins[g] * 2 > nReads) {
      int choice = 0;  // argmax, first maximum wins
      for (int b = 1; b < 4; ++b)
        if (v.insBase[g][b] > v.insBase[g][choice]) choice = b;
      if ((size_t)n + 1 < outCap) out[n++] = kAlphabet[choice];
    }
    if (g < nd) {
      const int current = baseIndex(draft[g]);
      int best = 0;
      for (int b = 1; b < 4; ++b)
        if (v.base[g][b] > v.base[g][best]) best = b;
      if (v.base[g][best] == v.base[g][current]) best = current;  // a tie keeps the draft
      if (v.base[g][kDel] <= v.base[g][best] && (size_t)n + 1 < outCap)
        out[n++] = kAlphabet[best];
    }
  }
  out[n] = 0;
  return n;
}

// ---------------------------------------------------------------- force the length

static int fixLength(const char *draft, int nd, const Votes &v, int strandLength, char *out,
                     size_t outCap) {
  if (nd == strandLength) {
    memcpy(out, draft, (size_t)nd);
    out[nd] = 0;
    return nd;
  }

  if (nd > strandLength) {
    // drop the positions with the most deletion votes; ties go to the least supported base,
    // then to the later position
    const int nDrop = nd - strandLength;
    bool drop[kMaxLen];
    for (int p = 0; p < nd; ++p) drop[p] = false;
    for (int k = 0; k < nDrop; ++k) {
      int best = -1, bestDel = -1, bestMax = 0;
      for (int p = 0; p < nd; ++p) {
        if (drop[p]) continue;
        int mx = 0;
        for (int b = 0; b < 4; ++b)
          if (v.base[p][b] > mx) mx = v.base[p][b];
        const int del = v.base[p][kDel];
        // key ascending: (-del, mx, -p)
        if (best < 0 || del > bestDel || (del == bestDel && mx < bestMax) ||
            (del == bestDel && mx == bestMax && p > best)) {
          best = p;
          bestDel = del;
          bestMax = mx;
        }
      }
      if (best < 0) break;
      drop[best] = true;
    }
    int n = 0;
    for (int p = 0; p < nd; ++p)
      if (!drop[p] && (size_t)n + 1 < outCap) out[n++] = draft[p];
    out[n] = 0;
    return n;
  }

  // too short: add bases at the gaps with the most insertion votes, later gaps first
  const int nAdd = strandLength - nd;
  bool add[kMaxLen + 1];
  for (int g = 0; g <= nd; ++g) add[g] = false;
  for (int k = 0; k < nAdd; ++k) {
    int best = -1, bestIns = -1;
    for (int g = 0; g <= nd; ++g) {
      if (add[g]) continue;
      const int ins = v.ins[g];
      if (best < 0 || ins > bestIns || (ins == bestIns && g > best)) {  // (-ins, -g) ascending
        best = g;
        bestIns = ins;
      }
    }
    if (best < 0) break;
    add[best] = true;
  }
  int n = 0;
  for (int g = 0; g <= nd; ++g) {
    if (add[g]) {
      int choice = 0;
      for (int b = 1; b < 4; ++b)
        if (v.insBase[g][b] > v.insBase[g][choice]) choice = b;
      if ((size_t)n + 1 < outCap) out[n++] = kAlphabet[choice];
    }
    if (g < nd && (size_t)n + 1 < outCap) out[n++] = draft[g];
  }
  while (n < strandLength && (size_t)n + 1 < outCap) out[n++] = 'A';  // very short drafts
  if (n > strandLength) n = strandLength;
  out[n] = 0;
  return n;
}

// ---------------------------------------------------------------- reconstruct

int reconstruct(const Cluster &c, int strandLength, int iterations, char *out, size_t outCap) {
  if (c.n == 0) return 0;

  char draft[kMaxLen + 1];
  int nd = pickDraft(c, strandLength, draft, sizeof(draft));
  if (nd == 0) return 0;

  Votes &v = gVotes;
  buildVotes(draft, nd, c, v);

  char next[kMaxLen + 1];
  for (int it = 0; it < iterations; ++it) {
    const int nn = rebuild(draft, nd, v, c.n, next, sizeof(next));
    if (nn == nd && memcmp(next, draft, (size_t)nn) == 0) break;  // settled
    memcpy(draft, next, (size_t)nn);
    draft[nn] = 0;
    nd = nn;
    buildVotes(draft, nd, c, v);
  }
  return fixLength(draft, nd, v, strandLength, out, outCap);
}


// ---------------------------------------------------------------- marks for the display

// The same matrix walk as alignInto(), but reporting the edits instead of voting with them.
// One pass fills both kinds of mark, because the display needs the read's errors and the
// decoder's corrections to line up on the same columns.
template <typename Fn>
static void walkEdits(const char *a, int na, const char *b, int nb, Fn emit) {
  if (na > kMaxLen) na = kMaxLen;
  if (nb > kMaxLen) nb = kMaxLen;
  const int stride = nb + 1;
  uint8_t *dp = gDP;
  for (int j = 0; j <= nb; ++j) dp[j] = (uint8_t)j;
  for (int i = 1; i <= na; ++i) {
    uint8_t *row = dp + (size_t)i * stride;
    const uint8_t *up = dp + (size_t)(i - 1) * stride;
    row[0] = (uint8_t)i;
    for (int j = 1; j <= nb; ++j) {
      const int sub = up[j - 1] + (a[i - 1] != b[j - 1] ? 1 : 0);
      const int del = up[j] + 1;
      const int ins = row[j - 1] + 1;
      int best = sub < del ? sub : del;
      if (ins < best) best = ins;
      row[j] = (uint8_t)best;
    }
  }
  int i = na, j = nb;
  while (i > 0 && j > 0) {
    const int here = dp[(size_t)i * stride + j];
    const int up = dp[(size_t)(i - 1) * stride + j];
    const int left = dp[(size_t)i * stride + (j - 1)];
    const int diag = dp[(size_t)(i - 1) * stride + (j - 1)];
    const int cost = (a[i - 1] != b[j - 1]) ? 1 : 0;
    if (here == up + 1) {            // a has a letter b does not
      --i;
      emit('D', i, j, a[i]);
    } else if (here == left + 1) {   // b has a letter a does not
      --j;
      emit('I', i, j, b[j]);
    } else if (here == diag + cost) {
      --i;
      --j;
      if (cost) emit('R', i, j, a[i]);
    } else {
      break;
    }
  }
  while (i > 0) { --i; emit('D', i, 0, a[i]); }
  while (j > 0) { --j; emit('I', 0, j, b[j]); }
}

void readMarks(const char *read, int nr, const char *final, int nf, int visible, char *out) {
  for (int k = 0; k < visible; ++k) out[k] = ' ';
  out[visible] = 0;
  walkEdits(read, nr, final, nf, [&](char kind, int ai, int /*bi*/, char /*ch*/) {
    if (ai >= visible || ai < 0) return;
    if (kind == 'R') out[ai] = 'x';        // the read has the wrong letter here
    else if (kind == 'D') out[ai] = 'e';   // an extra letter the strand does not have
    else if (kind == 'I' && out[ai] == ' ') out[ai] = 'm';  // a letter the read is missing
  });
}

int fixMarks(const char *before, int nb, const char *final, int nf, int visible, char *marks,
             char *replaced) {
  for (int k = 0; k < visible; ++k) {
    marks[k] = ' ';
    replaced[k] = 0;
  }
  marks[visible] = 0;
  replaced[visible] = 0;
  int total = 0;
  walkEdits(before, nb, final, nf, [&](char kind, int /*ai*/, int bi, char ch) {
    ++total;
    if (bi >= visible || bi < 0) return;
    if (kind == 'R') {          // substituted
      marks[bi] = 's';
      replaced[bi] = ch;
    } else if (kind == 'I') {   // inserted, the draft had nothing here
      marks[bi] = 'i';
      replaced[bi] = '-';
    } else if (kind == 'D' && marks[bi] == ' ') {  // deleted a letter in front of this one
      marks[bi] = 'd';
      replaced[bi] = ch;
    }
  });
  return total;
}

}  // namespace boxdec
