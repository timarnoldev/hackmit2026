// The risk model, on the device. See box_risk.h.
//
// A stem convolution over one-hot bases, four residual blocks of a width 5 convolution and a
// 1x1 convolution, masked mean and max pooling, then a two layer head and a sigmoid. float32
// throughout: at 63k parameters and 6.4M multiply accumulates it is small enough that
// quantizing would save flash that is not scarce and cost accuracy that is.
//
// BatchNorm is already folded into the convolution in front of it by the exporter, and the
// mask is gone because the tensor past the strand's end is zero at every stage, so running
// at the strand's own length gives the same answer as padding to 140 and masking.

#include "box_risk.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "box_risk_weights.h"

namespace boxrisk {

#define RC RISK_CHANNELS
#define MAXL 160

static float *gH = nullptr;   // [position][channel]
static float *gT = nullptr;
static float *gU = nullptr;
static float *gIn = nullptr;  // [position][RISK_IN]
static bool gReady = false;

bool available() { return true; }
size_t workingBytes() { return sizeof(float) * MAXL * (3 * RC + RISK_IN); }

#if defined(ARDUINO) || defined(ESP_PLATFORM)
#include "esp_heap_caps.h"
static void *alloc(size_t n) {
  void *p = heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  return p ? p : heap_caps_malloc(n, MALLOC_CAP_SPIRAM);
}
#else
static void *alloc(size_t n) { return malloc(n); }
#endif

bool begin() {
  if (gReady) return true;
  gH = (float *)alloc(sizeof(float) * MAXL * RC);
  gT = (float *)alloc(sizeof(float) * MAXL * RC);
  gU = (float *)alloc(sizeof(float) * MAXL * RC);
  gIn = (float *)alloc(sizeof(float) * MAXL * RISK_IN);
  gReady = gH && gT && gU && gIn;
  return gReady;
}

// The exact GELU, the one torch uses by default. erff is cheap enough here: a few tens of
// thousands of calls per strand against millions of multiply accumulates.
static inline float geluf(float x) {
  return 0.5f * x * (1.0f + erff(x * 0.70710678118654752f));
}

// y[p][oc] = sum over taps and input channels. Weights are [out][tap][in].
static void conv(const float *x, int len, int cin, int cout, int k, const float *w,
                 const float *b, float *y, bool applyGelu) {
  const int pad = (k - 1) / 2;
  for (int oc = 0; oc < cout; ++oc) {
    const float *wo = w + (size_t)oc * k * cin;
    const float bias = b[oc];
    for (int p = 0; p < len; ++p) {
      float acc = 0.0f;
      for (int t = 0; t < k; ++t) {
        const int sp = p + t - pad;
        if (sp < 0 || sp >= len) continue;  // zero padding, same as torch
        const float *xr = x + (size_t)sp * cin;
        const float *wt = wo + (size_t)t * cin;
        for (int c = 0; c < cin; ++c) acc += xr[c] * wt[c];
      }
      acc += bias;
      y[(size_t)p * cout + oc] = applyGelu ? geluf(acc) : acc;
    }
  }
}

float score(const char *strand, int len) {
  if (!gReady || len <= 0) return -1.0f;
  if (len > MAXL) len = MAXL;

  // one hot A C G T, then the mask, then the relative position
  for (int p = 0; p < len; ++p) {
    float *r = gIn + (size_t)p * RISK_IN;
    for (int c = 0; c < RISK_IN; ++c) r[c] = 0.0f;
    int idx;
    switch (strand[p]) {
      case 'C': idx = 1; break;
      case 'G': idx = 2; break;
      case 'T': idx = 3; break;
      default: idx = 0; break;  // non ACGT counts as A, like dnacodec.risk._CODE
    }
    r[idx] = 1.0f;
    r[4] = 1.0f;
    r[5] = len > 1 ? (float)p / (float)(len - 1) : 0.0f;
  }

  conv(gIn, len, RISK_IN, RC, RISK_STEM_K, kR_stem_w, kR_stem_b, gH, true);

  const float *c1w[RISK_BLOCKS] = {kR_b0_c1w, kR_b1_c1w, kR_b2_c1w, kR_b3_c1w};
  const float *c1b[RISK_BLOCKS] = {kR_b0_c1b, kR_b1_c1b, kR_b2_c1b, kR_b3_c1b};
  const float *c2w[RISK_BLOCKS] = {kR_b0_c2w, kR_b1_c2w, kR_b2_c2w, kR_b3_c2w};
  const float *c2b[RISK_BLOCKS] = {kR_b0_c2b, kR_b1_c2b, kR_b2_c2b, kR_b3_c2b};

  for (int i = 0; i < RISK_BLOCKS; ++i) {
    conv(gH, len, RC, RC, RISK_CONV1_K, c1w[i], c1b[i], gT, true);  // BatchNorm is folded in
    conv(gT, len, RC, RC, 1, c2w[i], c2b[i], gU, false);
    for (int p = 0; p < len; ++p) {  // the residual add
      float *h = gH + (size_t)p * RC;
      const float *u = gU + (size_t)p * RC;
      for (int c = 0; c < RC; ++c) h[c] += u[c];
    }
  }

  // masked mean and max over the strand, concatenated
  float pooled[2 * RC];
  for (int c = 0; c < RC; ++c) {
    float sum = 0.0f, mx = -1e4f;
    for (int p = 0; p < len; ++p) {
      const float v = gH[(size_t)p * RC + c];
      sum += v;
      if (v > mx) mx = v;
    }
    pooled[c] = sum / (float)len;
    pooled[RC + c] = mx;
  }

  float hid[RC];
  for (int o = 0; o < RC; ++o) {
    float acc = kR_h0b[o];
    const float *row = kR_h0w + (size_t)o * 2 * RC;
    for (int c = 0; c < 2 * RC; ++c) acc += pooled[c] * row[c];
    hid[o] = geluf(acc);
  }
  float logit = kR_h1b[0];
  for (int c = 0; c < RC; ++c) logit += hid[c] * kR_h1w[c];
  return 1.0f / (1.0f + expf(-logit));
}

}  // namespace boxrisk
