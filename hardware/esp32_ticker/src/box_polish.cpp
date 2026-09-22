// The learned polisher, on the device. See box_polish.h.
//
// Arithmetic, matching hardware/esp32_ticker/tools/quantize_polish.py exactly:
//   activations  int16, one calibrated scale per tensor, layout [position][channel] so the
//                inner product over input channels is contiguous
//   weights      int8, one scale per output channel, layout [out][tap][in] for the same
//                reason, generated in that order by the exporter
//   accumulate   int32, then rescale to the next tensor's int16 scale
//   GroupNorm    float, statistics per strand, because they depend on the input
//   GELU         the tanh approximation, in float, on the dequantized tensor
//
// Everything that decides an edit is float at the end: the two heads produce logits, those
// go through a softmax, and apply_edits' thresholds are applied on the probabilities.

#include "box_polish.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "box_polish_weights.h"

namespace boxpolish {

#define C POLISH_CHANNELS
#define NF POLISH_FEATURES
// The strands are 110 long. Sizing the activation buffers for the decoder's 160 would cost
// 60 KB of internal RAM for nothing, and internal RAM is what makes the convolutions fast.
#define MAXL 120

// The three int16 activation buffers are the hot ones and want internal RAM; the float
// scratch is touched once per position and is happy in PSRAM.
#if defined(ARDUINO) || defined(ESP_PLATFORM)
#include "esp_heap_caps.h"
static void *allocFast(size_t n) {
  void *p = heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return p ? p : heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
}
static void *allocSlow(size_t n) {
  void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
  return p ? p : malloc(n);
}
#else
static void *allocFast(size_t n) { return malloc(n); }
static void *allocSlow(size_t n) { return malloc(n); }
#endif

static int16_t *gA = nullptr;  // three activation buffers, [position][channel]
static int16_t *gB = nullptr;
static int16_t *gR = nullptr;  // the residual held across a block
static float *gFeat = nullptr;   // [position][feature]
static float *gOpP = nullptr;    // [position][POLISH_OPS]
static float *gInsP = nullptr;   // [position][POLISH_INS]
static bool gReady = false;
static void (*gYield)() = nullptr;

void setYield(void (*fn)()) { gYield = fn; }

bool available() { return true; }

size_t workingBytes() {
  return 3 * sizeof(int16_t) * MAXL * C + sizeof(float) * MAXL * NF +
         sizeof(float) * MAXL * (POLISH_OPS + POLISH_INS);
}

bool begin() {
  if (gReady) return true;
  gA = (int16_t *)allocFast(sizeof(int16_t) * MAXL * C);
  gB = (int16_t *)allocFast(sizeof(int16_t) * MAXL * C);
  gR = (int16_t *)allocFast(sizeof(int16_t) * MAXL * C);
  gFeat = (float *)allocSlow(sizeof(float) * MAXL * NF);
  gOpP = (float *)allocSlow(sizeof(float) * MAXL * POLISH_OPS);
  gInsP = (float *)allocSlow(sizeof(float) * MAXL * POLISH_INS);
  gReady = gA && gB && gR && gFeat && gOpP && gInsP;
  return gReady;
}

// ---------------------------------------------------------------- pieces

static inline float geluf(float x) {
  return 0.5f * x * (1.0f + tanhf(0.7978845608028654f * (x + 0.044715f * x * x * x)));
}

// GroupNorm over 8 groups, then GELU, then straight back to int16 in one pass.
//
// This used to normalise into a float scratch buffer and quantize in a second pass. Fusing
// the three steps removes 61 KB of PSRAM and the round trip through it, which is worth more
// than it looks: the scratch was the only part of the network's inner loop living in
// external memory.
static void gnGelu(const int16_t *x, float xScale, int len, const float *gw, const float *gb,
                   float outScale, int16_t *out) {
  const int per = C / POLISH_GROUPS;
  const float invOut = 1.0f / outScale;
  for (int g = 0; g < POLISH_GROUPS; ++g) {
    const int c0 = g * per;
    double sum = 0, sq = 0;
    for (int p = 0; p < len; ++p) {
      const int16_t *row = x + (size_t)p * C;
      for (int c = c0; c < c0 + per; ++c) {
        const double v = (double)row[c] * xScale;
        sum += v;
        sq += v * v;
      }
    }
    const double n = (double)len * per;
    const double mean = sum / n;
    const double var = sq / n - mean * mean;
    const float inv = (float)(1.0 / sqrt(var + 1e-5));
    const float fmean = (float)mean;
    for (int p = 0; p < len; ++p) {
      const int16_t *row = x + (size_t)p * C;
      int16_t *orow = out + (size_t)p * C;
      for (int c = c0; c < c0 + per; ++c) {
        const float v = ((float)row[c] * xScale - fmean) * inv;
        float q = geluf(v * gw[c] + gb[c]) * invOut;
        q = q < -32767.0f ? -32767.0f : (q > 32767.0f ? 32767.0f : q);
        orow[c] = (int16_t)lrintf(q);
      }
    }
  }
}

// One dilated convolution: int16 activations by int8 weights into int32, rescaled to int16.
// cin and cout are always C except for the stem and the heads.
static void convQ(const int16_t *x, float xScale, int len, int cin, int cout, int k, int dil,
                  const int8_t *w, const float *wScale, const float *bias, float outScale,
                  int16_t *out, float *outF) {
  const int pad = dil * (k - 1) / 2;
  const float invOut = outScale > 0 ? 1.0f / outScale : 0.0f;
  for (int oc = 0; oc < cout; ++oc) {
    const int8_t *wo = w + (size_t)oc * k * cin;  // [tap][in], contiguous over in
    const float s = xScale * wScale[oc];
    const float b = bias[oc];
    for (int p = 0; p < len; ++p) {
      int32_t acc = 0;
      for (int t = 0; t < k; ++t) {
        const int sp = p + t * dil - pad;
        if (sp < 0 || sp >= len) continue;
        const int16_t *xr = x + (size_t)sp * C;
        const int8_t *wt = wo + (size_t)t * cin;
        // this loop is essentially the whole run time of the network
        int c = 0;
        int32_t a0 = 0, a1 = 0, a2 = 0, a3 = 0;
        for (; c + 4 <= cin; c += 4) {
          a0 += (int32_t)xr[c + 0] * (int32_t)wt[c + 0];
          a1 += (int32_t)xr[c + 1] * (int32_t)wt[c + 1];
          a2 += (int32_t)xr[c + 2] * (int32_t)wt[c + 2];
          a3 += (int32_t)xr[c + 3] * (int32_t)wt[c + 3];
        }
        for (; c < cin; ++c) a0 += (int32_t)xr[c] * (int32_t)wt[c];
        acc += a0 + a1 + a2 + a3;
      }
      const float v = (float)acc * s + b;
      if (outF) outF[(size_t)p * cout + oc] = v;
      if (out) {
        float q = v * invOut;
        q = q < -32767.0f ? -32767.0f : (q > 32767.0f ? 32767.0f : q);
        out[(size_t)p * C + oc] = (int16_t)lrintf(q);
      }
    }
  }
}

// ---------------------------------------------------------------- the network

static const int kDil[POLISH_BLOCKS] = {1, 2, 4, 8, 1, 2, 4, 8};

struct BlockRefs {
  const int8_t *w1;
  const float *ws1;
  const float *b1;
  const int8_t *w2;
  const float *ws2;
  const float *b2;
  const float *gw1;
  const float *gb1;
  const float *gw2;
  const float *gb2;
  float sn1, sc1, sn2, sc2, sout;
};

#define BLK(i)                                                                       \
  {kW_blocks_##i##_conv1, kWS_blocks_##i##_conv1, kB_blocks_##i##_conv1,              \
   kW_blocks_##i##_conv2, kWS_blocks_##i##_conv2, kB_blocks_##i##_conv2,              \
   kGW_blocks_##i##_norm1, kGB_blocks_##i##_norm1, kGW_blocks_##i##_norm2,            \
   kGB_blocks_##i##_norm2, kAS_blocks_##i##_norm1, kAS_blocks_##i##_conv1,            \
   kAS_blocks_##i##_norm2, kAS_blocks_##i##_conv2, kAS_blocks_##i##_out}

static const BlockRefs kBlocks[POLISH_BLOCKS] = {BLK(0), BLK(1), BLK(2), BLK(3),
                                                 BLK(4), BLK(5), BLK(6), BLK(7)};

static void runNet(int len) {
  const float inScale = 1.0f / 32767.0f;
  // the features are already in [0, 1], so quantizing them is a plain scale
  for (int p = 0; p < len; ++p) {
    const float *f = gFeat + (size_t)p * NF;
    int16_t *row = gA + (size_t)p * C;
    for (int c = 0; c < NF; ++c) {
      float q = f[c] / inScale;
      q = q < -32767.0f ? -32767.0f : (q > 32767.0f ? 32767.0f : q);
      row[c] = (int16_t)lrintf(q);
    }
  }

  convQ(gA, inScale, len, NF, C, POLISH_STEM_K, 1, kW_stem, kWS_stem, kB_stem, kAS_stem, gB,
        nullptr);
  float hScale = kAS_stem;
  memcpy(gR, gB, sizeof(int16_t) * (size_t)len * C);
  int16_t *h = gB;

  for (int i = 0; i < POLISH_BLOCKS; ++i) {
    if (gYield) gYield();  // a strand takes seconds; the idle task still has to run
    const BlockRefs &bl = kBlocks[i];
    const float resScale = hScale;
    memcpy(gR, h, sizeof(int16_t) * (size_t)len * C);

    gnGelu(h, hScale, len, bl.gw1, bl.gb1, bl.sn1, gA);
    convQ(gA, bl.sn1, len, C, C, 3, kDil[i], bl.w1, bl.ws1, bl.b1, bl.sc1, gB, nullptr);

    gnGelu(gB, bl.sc1, len, bl.gw2, bl.gb2, bl.sn2, gA);
    convQ(gA, bl.sn2, len, C, C, 3, kDil[i], bl.w2, bl.ws2, bl.b2, bl.sc2, gB, nullptr);

    // the residual add, back into this block's output scale
    const float invOut = 1.0f / bl.sout;
    for (int p = 0; p < len; ++p) {
      const int16_t *r = gR + (size_t)p * C;
      int16_t *o = gB + (size_t)p * C;
      for (int c = 0; c < C; ++c) {
        float v = ((float)r[c] * resScale + (float)o[c] * bl.sc2) * invOut;
        v = v < -32767.0f ? -32767.0f : (v > 32767.0f ? 32767.0f : v);
        o[c] = (int16_t)lrintf(v);
      }
    }
    h = gB;
    hScale = bl.sout;
  }

  if (gYield) gYield();
  gnGelu(h, hScale, len, kGW_norm, kGB_norm, kAS_norm, gA);
  convQ(gA, kAS_norm, len, C, POLISH_OPS, 1, 1, kW_op_head, kWS_op_head, kB_op_head, 0.0f,
        nullptr, gOpP);
  convQ(gA, kAS_norm, len, C, POLISH_INS, 1, 1, kW_ins_head, kWS_ins_head, kB_ins_head, 0.0f,
        nullptr, gInsP);

  for (int p = 0; p < len; ++p) {  // softmax over the class axis, per position
    float *o = gOpP + (size_t)p * POLISH_OPS;
    float m = o[0];
    for (int c = 1; c < POLISH_OPS; ++c) m = o[c] > m ? o[c] : m;
    float sum = 0;
    for (int c = 0; c < POLISH_OPS; ++c) {
      o[c] = expf(o[c] - m);
      sum += o[c];
    }
    for (int c = 0; c < POLISH_OPS; ++c) o[c] /= sum;

    float *n = gInsP + (size_t)p * POLISH_INS;
    m = n[0];
    for (int c = 1; c < POLISH_INS; ++c) m = n[c] > m ? n[c] : m;
    sum = 0;
    for (int c = 0; c < POLISH_INS; ++c) {
      n[c] = expf(n[c] - m);
      sum += n[c];
    }
    for (int c = 0; c < POLISH_INS; ++c) n[c] /= sum;
  }
}

// ---------------------------------------------------------------- features

static inline int baseIdx(char c) {
  switch (c) {
    case 'C': return 1;
    case 'G': return 2;
    case 'T': return 3;
    default: return 0;
  }
}

// dnacodec.model.polish.features_of, position major.
static void buildFeatures(const char *draft, int len, const boxdec::VoteView &v) {
  const float n = v.nReads > 1 ? (float)v.nReads : 1.0f;
  for (int p = 0; p < len; ++p) {
    float *f = gFeat + (size_t)p * NF;
    for (int b = 0; b < 4; ++b) f[b] = (float)v.base[p * 5 + b] / n;
    f[4] = (float)v.base[p * 5 + 4] / n;
    f[5] = (float)v.ins[p] / n;
    for (int b = 0; b < 4; ++b) f[6 + b] = (float)v.insBase[p * 4 + b] / n;
    const int d = baseIdx(draft[p]);
    for (int b = 0; b < 4; ++b) f[10 + b] = (b == d) ? 1.0f : 0.0f;
    f[14] = (float)v.nReads / (float)boxdec::kMaxReads;
    f[15] = (float)v.base[p * 5 + d] / n;
    f[16] = len > 1 ? (float)p / (float)(len - 1) : 0.0f;
  }
}

// ---------------------------------------------------------------- apply the edits

// dnacodec.model.polish.apply_edits: substitutions are free, deletions and insertions are
// paired and the k most confident of each are kept, which makes the output exactly
// strandLength long by construction.
static int applyEdits(const char *draft, int len, int strandLength, float subT, float indelT,
                      char *out, size_t outCap) {
  static const char kA[4] = {'A', 'C', 'G', 'T'};
  static uint8_t sub[MAXL], drop[MAXL], insert[MAXL], baseChoice[MAXL], insChoice[MAXL];
  static float delScore[MAXL], insScore[MAXL];
  if (len > MAXL) return 0;

  for (int p = 0; p < len; ++p) {
    const float *o = gOpP + (size_t)p * POLISH_OPS;
    int best = 0;
    for (int c = 1; c < POLISH_OPS; ++c)
      if (o[c] > o[best]) best = c;
    int bb = 0;
    for (int c = 1; c < 4; ++c)
      if (o[1 + c] > o[1 + bb]) bb = c;
    baseChoice[p] = (uint8_t)bb;
    const float subScore = o[1 + bb];
    sub[p] = (best >= 1 && best <= 4 && subScore >= subT) ? 1 : 0;
    delScore[p] = o[5];
    drop[p] = (!sub[p] && best == 5 && delScore[p] >= indelT) ? 1 : 0;

    const float *n = gInsP + (size_t)p * POLISH_INS;
    int bi = 0;
    for (int c = 1; c < POLISH_INS; ++c)
      if (n[c] > n[bi]) bi = c;
    insChoice[p] = (uint8_t)bi;
    insScore[p] = 1.0f - n[0];
    insert[p] = (bi > 0 && insScore[p] >= indelT) ? 1 : 0;
  }

  int nDel = 0, nIns = 0;
  for (int p = 0; p < len; ++p) {
    nDel += drop[p];
    nIns += insert[p];
  }
  const int keep = nDel < nIns ? nDel : nIns;
  // keep the k most confident of each; ties go to the earlier position, as argsort does
  while (nDel > keep) {
    int worst = -1;
    for (int p = 0; p < len; ++p)
      if (drop[p] && (worst < 0 || delScore[p] < delScore[worst])) worst = p;
    drop[worst] = 0;
    --nDel;
  }
  while (nIns > keep) {
    int worst = -1;
    for (int p = 0; p < len; ++p)
      if (insert[p] && (worst < 0 || insScore[p] < insScore[worst])) worst = p;
    insert[worst] = 0;
    --nIns;
  }

  int n = 0;
  for (int p = 0; p < len; ++p) {
    if (insert[p] && (size_t)n + 1 < outCap) out[n++] = kA[insChoice[p] - 1];
    if (!drop[p] && (size_t)n + 1 < outCap)
      out[n++] = sub[p] ? kA[baseChoice[p]] : draft[p];
  }
  if (n != strandLength) {  // defensive, the pairing should make this impossible
    while (n < strandLength && (size_t)n + 1 < outCap) out[n++] = draft[n < len ? n : len - 1];
    if (n > strandLength) n = strandLength;
  }
  out[n] = 0;
  return n;
}

// ---------------------------------------------------------------- entry point

int polish(const boxdec::Cluster &c, int strandLength, char *draft, size_t draftCap, char *out,
           size_t outCap) {
  if (!gReady || c.n == 0) return 0;

  if (strandLength > MAXL) return 0;  // the caller falls back to the classic decoder
  const int nd = boxdec::reconstruct(c, strandLength, 3, draft, draftCap);
  if (nd == 0 || nd > MAXL) return 0;

  boxdec::VoteView v;
  boxdec::computeVotes(draft, nd, c, v);
  buildFeatures(draft, nd, v);
  runNet(nd);

  const bool low = c.n <= POLISH_LOW_MAX_READS;
  const float subT = low ? POLISH_LOW_SUB : POLISH_HIGH_SUB;
  const float indelT = low ? POLISH_LOW_INDEL : POLISH_HIGH_INDEL;
  return applyEdits(draft, nd, strandLength, subT, indelT, out, outCap);
}

}  // namespace boxpolish
