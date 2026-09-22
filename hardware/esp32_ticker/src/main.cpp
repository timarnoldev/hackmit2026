// Erbgut live decode ticker, ESP32-S3-BOX-3.
//
// The box is a thin display. It draws what the Mac sends it over the USB cable and nothing
// else: no WiFi, no decoding, no invented letters. scripts/ticker_server.py runs the real
// encode, channel simulation and decode, and pushes one compact JSON object per line.
//
// Screen, 320 x 240, everything inside a 14 px margin:
//   top     the wordmark, three headline numbers, a sparkline of recent outcomes, progress
//   bottom  a ticker of strands, two noisy reads above the decoded strand
//
// Colours are the Erbgut palette (marketing/BRAND.md, dark tokens):
//   cost magenta   a wrong letter in a read
//   audit indigo   an extra letter in a read
//   dashed magenta a letter the read is missing
//   gain teal      a correction the decoder applied, and a strand that came back exactly
//
// Everything moves. Counters tween to their new value and flash in the brand colour, the
// ticker eases into place instead of jumping, a corrected letter flashes magenta for the
// error then teal for the correction and decays over about twenty frames, the live dot
// breathes and the progress bar chases its target. One off-screen sprite is drawn and
// pushed per frame, so there is no flicker.
//
// Touch, if the GT911 panel answers: left third slower, middle pause and resume, right
// third faster. The box answers in lines: a line holding one character is a command, and
// everything else, like the frame rate reports, is diagnostic text the server only logs.
// That framing matters, because "fps=28.4" contains a 'p' and a loose byte scan would read
// it as a pause.

#include <Arduino.h>
#include <ArduinoJson.h>

#include "board_config.h"
#include "box_data.h"
#include "box_decoder.h"
#include "box_polish.h"
#include "box_risk.h"

// The panel. LovyanGFX ships a profile for this exact board, so we use it instead of
// hand writing pins: LGFX_ESP32_S3_BOX_V3 in the library's LGFX_AutoDetect_ESP32_all.hpp.
// It knows the panel is an ILI9342C, that GPIO 48 is an input pull-up and not a reset line,
// that the bus is three wire SPI on SPI2 at 40 MHz, that the backlight is GPIO 47, and how
// the GT911 touch panel is wired. Getting those by hand is what left the screen white.
#if USE_AUTODETECT
#define LGFX_ESP32_S3_BOX_V3
#define LGFX_AUTODETECT
#include <LovyanGFX.hpp>
#include <LGFX_AUTODETECT.hpp>
static LGFX lcd;
#else
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

// Hand written fallback, with the corrected values from board_config.h.
class Box3Display : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9342 _panel;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;
#if TOUCH_ENABLED
  lgfx::Touch_GT911 _touch;
#endif

 public:
  Box3Display() {
    pinMode(LCD_PIN_PULLUP, INPUT_PULLUP);  // GPIO 48, never driven on this board
    {
      auto cfg = _bus.config();
      cfg.spi_host = LCD_SPI_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = LCD_SPI_HZ;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = LCD_SPI_3WIRE;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = LCD_PIN_SCLK;
      cfg.pin_mosi = LCD_PIN_MOSI;
      cfg.pin_miso = -1;
      cfg.pin_dc = LCD_PIN_DC;
      _bus.config(cfg);
      _panel.setBus(&_bus);
    }
    {
      auto cfg = _panel.config();
      cfg.pin_cs = LCD_PIN_CS;
      cfg.pin_rst = LCD_PIN_RST;
      cfg.pin_busy = -1;
      cfg.offset_x = LCD_OFFSET_X;
      cfg.offset_y = LCD_OFFSET_Y;
      cfg.offset_rotation = LCD_OFFSET_ROTATION;
      cfg.readable = false;
      cfg.invert = LCD_INVERT;
      cfg.rgb_order = LCD_RGB_ORDER;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      _panel.config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = LCD_PIN_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      _panel.setLight(&_light);
    }
#if TOUCH_ENABLED
    {
      auto cfg = _touch.config();
      cfg.x_min = 0;
      cfg.x_max = LCD_WIDTH - 1;
      cfg.y_min = 0;
      cfg.y_max = TOUCH_Y_MAX;
      cfg.pin_int = TOUCH_PIN_INT;
      cfg.pin_rst = TOUCH_PIN_RST;
      cfg.bus_shared = false;
      cfg.offset_rotation = TOUCH_OFFSET_ROTATION;
      cfg.i2c_port = 0;
      cfg.i2c_addr = TOUCH_I2C_ADDR;
      cfg.pin_sda = TOUCH_PIN_SDA;
      cfg.pin_scl = TOUCH_PIN_SCL;
      cfg.freq = TOUCH_I2C_HZ;
      _touch.config(cfg);
      _panel.setTouch(&_touch);
    }
#endif
    setPanel(&_panel);
  }
};

static Box3Display lcd;
#endif

static LGFX_Sprite canvas(&lcd);
static bool canvasReady = false;

// ---------------------------------------------------------------- palette

// Erbgut dark tokens as 0xRRGGBB, so they can be blended before going to the driver. These
// are the deck's tokens (marketing/deck/deck.css, :root dark), so the box on the table and
// the slides behind it are the same palette. The meaning mapping is what matters and it is
// unchanged: cost for an error, gain for a correction, ink and muted for neutral text,
// audit for an extra letter and the decode head, tbd for a strand that came back with no
// reads at all.
static const uint32_t C_PAPER = 0x060F0A;    // --sa-paper
static const uint32_t C_SURFACE = 0x0B2116;  // --sa-surface
static const uint32_t C_INK = 0xEAFFF3;      // --sa-ink, neutral text
static const uint32_t C_MUTED = 0x6FCB98;    // --sa-muted, secondary text
static const uint32_t C_RULE = 0x1B4A2E;     // --sa-rule, lines and the read icons
static const uint32_t C_AUDIT = 0x39E6FF;    // --sa-audit, an extra letter, the decode head
static const uint32_t C_COST = 0xFF2E9C;     // --sa-cost, an error
static const uint32_t C_GAIN = 0x39FF6E;     // --sa-gain, a correction
static const uint32_t C_TBD = 0xFFE13D;      // --sa-tbd, no reads came back
static const uint32_t C_WHITE = 0xFFFFFF;

// The logo mark has its own four colours, which are not deck tokens: the mark does not
// invert between themes, so these are the same values the SVGs carry.
// marketing/logo/erbgut-mark-small.svg.
static const uint32_t C_MARK_HELIX = 0x32C7DB;
static const uint32_t C_MARK_A = 0xFEC746;  // the A/T base pair
static const uint32_t C_MARK_C = 0xFC68D6;  // the C/G base pair
static const uint32_t C_MARK_G = 0x5BD67C;  // the T/A base pair

static inline uint32_t rgb(uint32_t hex) {
  return lcd.color888((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF);
}

// Linear blend in 888. t = 0 gives a, t = 1 gives b. Every fade in this firmware is one of
// these: the panel has no alpha channel, so things fade by moving toward the paper colour.
static uint32_t mix(uint32_t a, uint32_t b, float t) {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const int ar = (a >> 16) & 0xFF, ag = (a >> 8) & 0xFF, ab = a & 0xFF;
  const int br = (b >> 16) & 0xFF, bg = (b >> 8) & 0xFF, bb = b & 0xFF;
  return (uint32_t)(ar + (br - ar) * t) << 16 | (uint32_t)(ag + (bg - ag) * t) << 8 |
         (uint32_t)(ab + (bb - ab) * t);
}

// Where the strands are coming from. The box never asks anyone to choose: it plays the
// frozen run that is compiled into it, and hands over to the Mac the moment a host line
// arrives, then takes over again when the host goes quiet.
enum Source { SRC_EMBEDDED, SRC_HOST };
static Source source = SRC_EMBEDDED;

// Is the strand currently on screen the polisher's work? Only when a host is attached and
// that host said its model is live. The frozen run is always the classic vote.
static bool modelOnScreen();

static inline float clamp01(float v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
// Ease out cubic, for anything that slides into place.
static inline float easeOut(float t) { const float u = 1.0f - clamp01(t); return 1.0f - u * u * u; }

// ---------------------------------------------------------------- state

// One strand as it arrives. It is held until the tape has room for it.
struct Strand {
  bool used = false;
  bool dropout = false;
  bool ok = false;
  int index = 0;
  int nreads = 0;
  int shown = 0;
  char reads[MAX_READ_LANES][VISIBLE_LETTERS + 1];
  char rmark[MAX_READ_LANES][VISIBLE_LETTERS + 1];
  char cons[VISIBLE_LETTERS + 1];
  char was[VISIBLE_LETTERS + 1];   // what the classic decoder had, per position
  char fix[VISIBLE_LETTERS + 1];   // 's', 'i', 'd' or ' '
  float risk = -1.0f;              // what the risk model gives this strand, -1 = not scored
  int edits = 0;                   // Levenshtein distance from the true strand
  int bases = 0;                   // length of the true strand, the denominator for that
};

// One column of the tape: the same strand position across every lane, so the reads and the
// decoded letter under them always line up at the same x.
struct Col {
  uint8_t kind = 0;   // 0 letter, 1 strand divider, 2 idle gap while nothing is queued
  char r[MAX_READ_LANES];
  char rm[MAX_READ_LANES];
  char cons = 0;
  char was = 0;
  char fix = 0;
  uint8_t verdict = 0;  // dividers: 1 exact, 2 wrong, 3 no reads
  float risk = -1.0f;   // dividers: the risk model's score for the strand that follows
  uint16_t strand = 0;
  uint16_t age = 0;     // frames since this column crossed the head, 0 = not resolved yet
};

static Col ring[COLS];
static int ringHead = 0, ringCount = 0;
static float flowPx = 0;        // how far the leftmost column has already slid off
static float flowSpeed = FLOW_DEFAULT;

static inline Col &colAt(int i) { return ring[(ringHead + i) % COLS]; }

// The tape is slower than the decode on purpose, so it shows a sample of the strands while
// the header counts every one of them. skipped says how many never made it onto the tape.
static Strand pending;
static bool hasPending = false;
static Strand cur;
static bool curActive = false;
static int curPos = 0, curLen = 0, curDivider = 0;
static uint32_t shownStrands = 0, skippedStrands = 0;

// A number on the header: the value we draw eases toward the value we were told.
struct Counter {
  float shown = 0;
  float target = 0;
  uint8_t flash = 0;
  bool good = true;

  void set(float value, bool isGood) {
    if (fabsf(value - target) > 0.001f) {
      flash = FLASH_FRAMES;
      good = isGood;
    }
    target = value;
  }
  void step() {
    shown += (target - shown) * TWEEN;
    if (fabsf(target - shown) < 0.01f) shown = target;
    if (flash) --flash;
  }
  uint32_t colour(uint32_t base) const {
    if (!flash) return base;
    return mix(base, good ? C_WHITE : C_COST, (float)flash / FLASH_FRAMES * 0.85f);
  }
};

static Counter cDone, cAcc, cFix, cProg, cReads, cErrors;

static uint8_t spark[SPARK_N];  // 0 none, 1 exact, 2 wrong, 3 dropout
static int sparkAt = 0;

static int embeddedAt = 0;          // next record in the frozen run, wraps for a seamless loop
static uint32_t embDone = 0, embOk = 0, embFix = 0, embErr = 0, embReads = 0;
// edits and bases give the per base accuracy, which is the number a storage system cares
// about; drop counts the strands that lost every read, which still count as strands.
static uint32_t embDrop = 0, embEdits = 0, embBases = 0;

static char decoderName[36] = "waiting for the Mac";
static char decoderTag[10] = "";
// What the HOST said about itself, from its hello event. Kept separate from what is
// currently on screen: falling back to the frozen run must not erase it, or the tag goes
// stale and stops telling the truth the moment the host comes back.
static bool hostModelLive = false;
// Did the learned polisher actually run on this chip? Only then may the header say so.
static bool gPolishOnDevice = false;
static bool gRiskOnDevice = false;
static uint32_t gRiskUs = 0;
static char channelName[34] = "";
static bool linkUp = false;
static bool paused = false;
static bool haveData = false;
static uint32_t lastLineMs = 0;
static char touchNote[24] = "";
static uint32_t touchNoteUntil = 0;
static float fps = 0;
// Anything that stops the box working has to say so on the panel. A splash screen that
// claims it is waiting for a Mac while the real problem is an allocation failure is the
// worst thing this firmware could do on a table in front of a judge.
static char gFault[72] = "";
static uint32_t gLastStrandMs = 0;
static uint32_t gDecodeUs = 0;   // rolling mean time for one on-device decode
static uint32_t gDecoded = 0;
static inline uint32_t micros_now() { return (uint32_t)micros(); }

static bool modelOnScreen() {
  return source == SRC_HOST ? hostModelLive : gPolishOnDevice;
}

// ---------------------------------------------------------------- marks

// "3x11e25m" -> a kind per position. Mirrors pack_marks in scripts/ticker_server.py.
static void unpackMarks(const char *packed, char *out) {
  memset(out, ' ', VISIBLE_LETTERS);
  out[VISIBLE_LETTERS] = 0;
  if (!packed) return;
  int value = -1;
  for (const char *p = packed; *p; ++p) {
    if (*p >= '0' && *p <= '9') {
      value = (value < 0 ? 0 : value) * 10 + (*p - '0');
    } else if (value >= 0) {
      if (value < VISIBLE_LETTERS) out[value] = *p;
      value = -1;
    }
  }
}

// The same walk, but pairing each mark with its replaced letter from the "fp" field, so a
// corrected column knows the letter the classic decoder had there.
static void unpackReplaced(const char *packed, const char *replaced, char *out) {
  memset(out, 0, VISIBLE_LETTERS);
  if (!packed || !replaced) return;
  int value = -1, k = 0;
  for (const char *p = packed; *p; ++p) {
    if (*p >= '0' && *p <= '9') {
      value = (value < 0 ? 0 : value) * 10 + (*p - '0');
    } else if (value >= 0) {
      if (value < VISIBLE_LETTERS && k < (int)strlen(replaced)) out[value] = replaced[k];
      ++k;
      value = -1;
    }
  }
}

// ---------------------------------------------------------------- the frozen run

// One record of box_data.h. The full reads and the reference are what the box needs: it
// decodes the cluster itself with box_decoder, the same majority vote dnacodec.baseline
// runs, and only uses the reference to judge whether it got the strand exactly right. The
// display fields the Mac precomputed are ignored, so nothing on screen is replayed.
static char gFullReads[boxdec::kMaxReads][boxdec::kMaxLen + 1];
static const char *gReadPtr[boxdec::kMaxReads];
static int gReadLen[boxdec::kMaxReads];

static bool parseRecord(const char *rec, Strand &out, int &nfix, uint32_t &micros) {
  out = Strand();
  nfix = 0;
  int nFull = 0;
  char reference[boxdec::kMaxLen + 1] = {0};
  int nRef = 0;

  const char *p = rec;
  while (*p) {
    const char kind = *p;
    const char *line = p + 2;  // skip "k|"
    const char *eol = strchr(line, '\n');
    const size_t len = eol ? (size_t)(eol - line) : strlen(line);

    if (kind == 'i') {
      int idx = 0, ok = 0, nr = 0, nf = 0;
      sscanf(line, "%d|%d|%d|%d", &idx, &ok, &nr, &nf);
      out.index = idx;
      out.nreads = nr;
      out.dropout = nr == 0;
    } else if (kind == 'f' && nFull < boxdec::kMaxReads) {
      const size_t keep = len < boxdec::kMaxLen ? len : boxdec::kMaxLen;
      memcpy(gFullReads[nFull], line, keep);
      gFullReads[nFull][keep] = 0;
      gReadPtr[nFull] = gFullReads[nFull];
      gReadLen[nFull] = (int)keep;
      ++nFull;
    } else if (kind == 'x') {
      nRef = (int)(len < boxdec::kMaxLen ? len : boxdec::kMaxLen);
      memcpy(reference, line, (size_t)nRef);
      reference[nRef] = 0;
    }
    if (!eol) break;
    p = eol + 1;
  }

  if (nFull == 0) {  // a strand with no reads at all, the fountain code covers it
    out.dropout = true;
    out.used = true;
    micros = 0;
    return true;
  }

  const uint32_t t0 = micros_now();
  boxdec::Cluster c;
  boxdec::subsample(gReadPtr, gReadLen, nFull, boxdec::kMaxReads, c);

  char cons[boxdec::kMaxLen + 1] = {0};
  char draft[boxdec::kMaxLen + 1] = {0};
  int nCons = 0, nDraft = 0;
  if (gPolishOnDevice) {
    // the learned polisher runs the classic vote first and then corrects it, so the classic
    // answer is the "before" strand and every teal flip is a real model edit
    nCons = boxpolish::polish(c, BOX_DATA_STRAND_LENGTH, draft, sizeof(draft), cons,
                              sizeof(cons));
    nDraft = (int)strlen(draft);
  }
  if (nCons == 0) {  // no model, or it declined: fall back to the classic decoder
    nCons = boxdec::reconstruct(c, BOX_DATA_STRAND_LENGTH, 3, cons, sizeof(cons));
    nDraft = boxdec::pickDraft(c, BOX_DATA_STRAND_LENGTH, draft, sizeof(draft));
  }
  micros = micros_now() - t0;

  out.ok = (nRef == nCons) && memcmp(cons, reference, (size_t)nCons) == 0;
  out.edits = boxdec::editDistance(cons, nCons, reference, nRef);
  out.bases = nRef;
  strncpy(out.cons, cons, VISIBLE_LETTERS);
  out.cons[VISIBLE_LETTERS] = 0;

  // what the vote corrected, against the single best raw read it started from
  char marks[VISIBLE_LETTERS + 1], replaced[VISIBLE_LETTERS + 1];
  nfix = boxdec::fixMarks(draft, nDraft, cons, nCons, VISIBLE_LETTERS, marks, replaced);
  memcpy(out.fix, marks, VISIBLE_LETTERS + 1);
  memcpy(out.was, replaced, VISIBLE_LETTERS + 1);

  if (gRiskOnDevice) {
    // the other model: how likely this strand was to come back wrong in the first place
    const uint32_t r0 = micros_now();
    out.risk = boxrisk::score(cons, nCons);
    gRiskUs = micros_now() - r0;
  }

  const int lanes = nFull < MAX_READ_LANES ? nFull : MAX_READ_LANES;
  for (int r = 0; r < lanes; ++r) {
    strncpy(out.reads[r], c.read[r], VISIBLE_LETTERS);
    out.reads[r][VISIBLE_LETTERS] = 0;
    boxdec::readMarks(c.read[r], c.len[r], cons, nCons, VISIBLE_LETTERS, out.rmark[r]);
  }
  out.shown = lanes;
  out.used = true;
  return true;
}

// Decoding happens on the second core, one strand ahead of the tape.
//
// A strand costs about 160 ms of classic majority vote, which is nothing against the four
// or five seconds it takes to flow across the screen, but it is five dropped frames if it
// runs inside the render loop. The S3 has two cores, so the decode gets its own task and the
// animation never waits for it. The handoff is a single slot with two flags, which is enough
// because exactly one task writes each of them.
static TaskHandle_t gDecodeTask = nullptr;
static Strand gSlot;              // written by the decoder task, read by the render loop
static int gSlotFix = 0;
static volatile bool gSlotReady = false;   // set by the task, cleared by the loop
static volatile bool gSlotWanted = true;   // set by the loop, cleared by the task

// Decode a slice of the embedded run as fast as the chip can, and print one line per
// strand. This exists because the panel once showed a percentage the host verification did
// not agree with, and the only way to settle that is to make the device report per strand
// what it decoded so the two can be diffed record by record.
static volatile int gSelfTest = 0;

static void runSelfTest(int count) {
  Serial.printf("selftest begin n=%d of %d records\n", count, BOX_DATA_STRANDS);
  uint32_t ok = 0, drop = 0, fixes = 0, edits = 0, bases = 0;
  const uint32_t t0 = millis();
  for (int i = 0; i < count && i < BOX_DATA_STRANDS; ++i) {
    Strand b;
    int nfix = 0;
    uint32_t us = 0;
    const int saved = embeddedAt;
    embeddedAt = i;
    parseRecord(kBoxData[i], b, nfix, us);
    embeddedAt = saved;
    ok += b.ok ? 1 : 0;
    drop += b.dropout ? 1 : 0;
    fixes += nfix;
    edits += b.dropout ? BOX_DATA_STRAND_LENGTH : b.edits;
    bases += b.dropout ? BOX_DATA_STRAND_LENGTH : b.bases;
    Serial.printf("st i=%d ok=%d drop=%d ed=%d fx=%d us=%lu\n", i, (int)b.ok, (int)b.dropout,
                  b.edits, nfix, (unsigned long)us);
    vTaskDelay(1);
  }
  Serial.printf("selftest done n=%d ok=%lu drop=%lu fixes=%lu edits=%lu bases=%lu "
                "exact=%.2f%% perbase=%.3f%% seconds=%lu\n", count, (unsigned long)ok,
                (unsigned long)drop, (unsigned long)fixes, (unsigned long)edits,
                (unsigned long)bases, count ? 100.0 * ok / count : 0.0,
                bases ? 100.0 * (1.0 - (double)edits / (double)bases) : 0.0,
                (unsigned long)((millis() - t0) / 1000));
}

static void decodeTask(void *) {
  for (;;) {
    if (gSelfTest) {
      const int n = gSelfTest;
      gSelfTest = 0;
      runSelfTest(n);
    }
    if (gSlotWanted && !gSlotReady) {
      uint32_t us = 0;
      parseRecord(kBoxData[embeddedAt], gSlot, gSlotFix, us);
      embeddedAt = (embeddedAt + 1) % BOX_DATA_STRANDS;  // wraps, so the run loops seamlessly
      if (us) {
        gDecodeUs = gDecoded ? (gDecodeUs * 7 + us) / 8 : us;
        ++gDecoded;
      }
      gSlotWanted = false;
      gSlotReady = true;
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

// Take whatever the decoder task has ready, and keep the header's own running totals. In
// this mode nobody is sending statistics, so the box counts what it decoded.
static bool feedEmbedded() {
  if (!gSlotReady) {
    gSlotWanted = true;  // ask for the next one and let the tape keep flowing
    return false;
  }
  Strand b = gSlot;
  const int nfix = gSlotFix;
  gSlotReady = false;
  gSlotWanted = true;

  ++embDone;
  embOk += b.ok ? 1 : 0;
  embDrop += b.dropout ? 1 : 0;
  // a strand that lost every read contributes its whole length as errors, because that is
  // what it is: nothing came back. Counting it any other way would flatter the number.
  embEdits += b.dropout ? BOX_DATA_STRAND_LENGTH : b.edits;
  embBases += b.dropout ? BOX_DATA_STRAND_LENGTH : b.bases;
  embFix += nfix;
  embReads += b.nreads;
  for (int r = 0; r < b.shown; ++r)
    for (int i = 0; i < VISIBLE_LETTERS; ++i)
      if (b.rmark[r][i] != ' ') ++embErr;

  cDone.set((float)embDone, true);
  cAcc.set(embDone ? 100.0f * embOk / embDone : 0.0f, true);
  cFix.set((float)embFix, true);
  cErrors.set((float)embErr, false);
  cReads.set(embDone ? (float)embReads / embDone : 0.0f, true);
  cProg.set((float)embeddedAt / BOX_DATA_STRANDS, true);

  spark[sparkAt] = b.dropout ? 3 : (b.ok ? 1 : 2);
  sparkAt = (sparkAt + 1) % SPARK_N;

  pending = b;
  hasPending = true;
  haveData = true;
  return true;
}

// ---------------------------------------------------------------- drawing

static void putChar(int x, int y, char c, uint32_t fg, uint32_t bg, int size) {
  canvas.setTextSize(size);
  canvas.setTextColor(rgb(fg), rgb(bg));
  canvas.setCursor(x, y);
  canvas.write(c);
}

static const int LANE_Y[MAX_READ_LANES] = {LANE1_Y, LANE2_Y, LANE3_Y};

// Low risk reads as gain, high risk as cost, through amber in the middle. Colour alone never
// carries it: the number is printed next to the label.
static uint32_t riskColour(float r) {
  if (r < 0.0f) return C_MUTED;
  return r < 0.5f ? mix(C_GAIN, C_TBD, r * 2.0f) : mix(C_TBD, C_COST, (r - 0.5f) * 2.0f);
}

// The lane icons, drawn in code so nothing depends on a font. A read lane gets a small
// noisy wave, because that is what a read is. The decoded strand gets the Erbgut mark in
// miniature: bases standing on a strand. Both are dim on purpose, so they label the lanes
// without competing with the letters.
static void drawReadIcon(int cy, uint32_t colour) {
  const uint32_t c = rgb(colour);
  int prev = cy;
  for (int dx = 0; dx < 13; ++dx) {
    const int y = cy + (int)lroundf(2.2f * sinf(dx * 0.9f));
    canvas.drawLine(ICON_CX - 6 + dx - 1, prev, ICON_CX - 6 + dx, y, c);
    prev = y;
  }
}

static void drawStrandIcon(int cy, uint32_t colour) {
  const uint32_t c = rgb(colour);
  canvas.drawFastHLine(ICON_CX - 6, cy + 4, 13, c);   // the strand
  for (int i = 0; i < 3; ++i)                          // bases standing on it
    canvas.fillRect(ICON_CX - 5 + i * 5, cy - 3, 3, 7, c);
}

static void drawLaneIcons() {
  for (int r = 0; r < MAX_READ_LANES; ++r) drawReadIcon(LANE_Y[r] + 8, C_RULE);
  drawStrandIcon(CONS_Y + 11, C_MUTED);
}

// The tape. Columns flow right to left. A column is undecided until it reaches the head:
// the reads look clean and the strand shows the letter the classic decoder produced. As it
// crosses the head it resolves, the read errors light up, and a corrected position flashes
// cost magenta on the wrong letter and then gain teal as the corrected letter takes its
// place. Behind the head the trail keeps its final state and fades as it leaves.
static void drawTape() {
  canvas.fillRect(0, TICKER_TOP, LCD_WIDTH, TICKER_BOTTOM - TICKER_TOP, rgb(C_PAPER));
  drawLaneIcons();  // outside the clip, so the tape never draws over them
  canvas.setClipRect(TAPE_X0, TICKER_TOP, LCD_WIDTH - TAPE_X0, TICKER_BOTTOM - TICKER_TOP);

  // the resolve zone, a faint band the letters pass through
  for (int dx = 0; dx < CELL_W; ++dx) {
    const float t = 1.0f - (float)dx / CELL_W;
    canvas.drawFastVLine(HEAD_X + dx, LANE_TOP, LANE_BOTTOM - LANE_TOP,
                         rgb(mix(C_PAPER, C_AUDIT, 0.10f * t)));
  }

  for (int i = 0; i < ringCount; ++i) {
    const Col &c = colAt(i);
    const int x = (int)(TAPE_X0 + i * CELL_W - flowPx);
    if (x <= TAPE_X0 - CELL_W || x >= LCD_WIDTH) continue;

    float fade = 0.0f;
    if (x < HEAD_X) fade = clamp01((float)(HEAD_X - x) / (HEAD_X - TAPE_X0 + CELL_W)) * 0.62f;
    if (x > LCD_WIDTH - 70) fade = fmaxf(fade, clamp01((float)(x - (LCD_WIDTH - 70)) / 70.0f) * 0.75f);

    if (c.kind == 2) continue;  // an idle gap draws nothing, the tape just keeps moving

    if (c.kind == 1) {  // a strand boundary
      const uint32_t vc = c.verdict == 3 ? C_TBD : (c.verdict == 1 ? C_GAIN : C_COST);
      canvas.drawFastVLine(x + CELL_W / 2, LANE_TOP, LANE_BOTTOM - LANE_TOP,
                           rgb(mix(C_RULE, C_PAPER, fade)));
      canvas.setFont(&fonts::Font0);
      canvas.setTextSize(1);
      canvas.setTextColor(rgb(mix(C_MUTED, C_PAPER, fade)), rgb(C_PAPER));
      canvas.setCursor(x + CELL_W / 2 + 4, LABEL_Y);
      canvas.printf("%u", (unsigned)(c.strand + 1));
      if (c.risk >= 0.0f) {  // what the risk model thought of this strand before it was read
        canvas.setTextColor(rgb(mix(riskColour(c.risk), C_PAPER, fade)), rgb(C_PAPER));
        canvas.setCursor(x + CELL_W / 2 + 4 + 6 * 5, LABEL_Y);
        canvas.printf("risk %.2f", c.risk);
      }
      // a tick when the strand came back exactly, a cross when it did not
      const uint32_t tc = mix(vc, C_PAPER, fade);
      const int tx = x + CELL_W / 2 + 4, ty = LABEL_Y + 10;
      if (c.verdict == 1) {
        canvas.drawLine(tx, ty + 2, tx + 2, ty + 4, rgb(tc));
        canvas.drawLine(tx + 2, ty + 4, tx + 7, ty - 1, rgb(tc));
      } else if (c.verdict == 2) {
        canvas.drawLine(tx, ty - 1, tx + 6, ty + 5, rgb(tc));
        canvas.drawLine(tx + 6, ty - 1, tx, ty + 5, rgb(tc));
      } else {
        canvas.drawCircle(tx + 3, ty + 2, 3, rgb(tc));
      }
      continue;
    }

    const bool resolved = c.age > 0;
    const float phase = resolved ? clamp01((float)c.age / FIX_FRAMES) : 0.0f;

    // the reads
    canvas.setFont(&fonts::Font0);
    for (int r = 0; r < MAX_READ_LANES; ++r) {
      if (!c.r[r]) continue;
      const int ly = LANE_Y[r];
      uint32_t fg = mix(C_MUTED, C_PAPER, fmaxf(fade, resolved ? 0.0f : 0.35f));
      uint32_t bg = C_PAPER;
      if (resolved) {
        const char k = c.rm[r];
        if (k == 'x' || k == 'e') {
          const uint32_t base = (k == 'x') ? C_COST : C_AUDIT;
          const float lift = clamp01(1.0f - (float)c.age / 8.0f);  // a white pop on arrival
          bg = mix(mix(base, C_WHITE, lift * 0.7f), C_PAPER, fade);
          fg = C_PAPER;
        } else if (k == 'm') {
          const uint32_t dc = mix(C_COST, C_PAPER, fade);
          for (int dy = ly; dy < ly + 16; dy += 3) canvas.drawPixel(x, dy, rgb(dc));
        }
      }
      if (bg != C_PAPER) canvas.fillRect(x + 2, ly - 1, CELL_W - 4, 18, rgb(bg));
      putChar(x + 4, ly, c.r[r], fg, bg, READ_SIZE);
    }

    // the decoded strand
    char letter = c.cons;
    uint32_t fg = mix(C_INK, C_PAPER, fade);
    uint32_t bg = C_PAPER;
    if (!resolved) {
      // not decided yet: show what the classic decoder had, dimmed
      letter = c.was ? c.was : c.cons;
      fg = mix(C_MUTED, C_PAPER, fmaxf(fade, 0.3f));
    } else if (c.fix == 's' || c.fix == 'i') {
      if (phase < 0.35f) {  // the wrong letter, held up in cost magenta
        letter = (c.fix == 's' && c.was) ? c.was : c.cons;
        bg = mix(C_COST, C_PAPER, fade);
        fg = C_PAPER;
      } else {  // the correction lands, in gain teal, then dissolves into the strand
        const float k = clamp01((phase - 0.35f) / 0.65f);
        bg = mix(mix(C_GAIN, C_PAPER, k), C_PAPER, fade);
        fg = mix(C_PAPER, C_INK, k);
      }
    } else if (c.fix == 'd') {
      canvas.drawFastVLine(x, CONS_Y, 24, rgb(mix(C_GAIN, C_PAPER, fade)));
    }
    if (bg != C_PAPER) canvas.fillRect(x, CONS_Y - 2, CELL_W - 1, 28, rgb(bg));
    if (letter) putChar(x + 1, CONS_Y, letter, fg, bg, CONS_SIZE);
  }

  // the head marker, drawn last so it sits over the letters
  const float pulse = 0.72f + 0.28f * sinf(millis() / 300.0f);
  const uint32_t hc = mix(C_PAPER, C_AUDIT, pulse);
  canvas.drawFastVLine(HEAD_X - 1, LANE_TOP, LANE_BOTTOM - LANE_TOP, rgb(mix(C_PAPER, C_AUDIT, 0.25f)));
  canvas.drawFastVLine(HEAD_X, LANE_TOP, LANE_BOTTOM - LANE_TOP, rgb(hc));
  for (int k = 0; k < 4; ++k) {  // a small arrow above and below
    canvas.drawFastHLine(HEAD_X - 3 + k, LANE_TOP - 5 + k, 7 - 2 * k, rgb(hc));
    canvas.drawFastHLine(HEAD_X - 3 + k, LANE_BOTTOM + 4 - k, 7 - 2 * k, rgb(hc));
  }
  canvas.clearClipRect();
}

static void drawSparkline(int x, int y, int w, int h) {
  const int cell = w / SPARK_N;
  for (int i = 0; i < SPARK_N; ++i) {
    const int slot = (sparkAt + i) % SPARK_N;  // oldest on the left
    const uint8_t v = spark[slot];
    if (!v) continue;
    const uint32_t c = (v == 1) ? C_GAIN : (v == 2 ? C_COST : C_TBD);
    const int bh = (v == 1) ? h : (v == 2 ? h - 2 : 2);
    const float recency = (float)i / SPARK_N;  // the newest end is the brightest
    canvas.fillRect(x + i * cell, y + (h - bh), cell - 1, bh,
                    rgb(mix(mix(c, C_PAPER, 0.55f), c, recency)));
  }
}

static void drawHeader() {
  canvas.fillRect(0, 0, LCD_WIDTH, HEADER_H, rgb(C_SURFACE));

  // wordmark, then what is running, then the link state, all on one row
  canvas.setFont(&fonts::FreeSansBold9pt7b);
  canvas.setTextSize(1);
  canvas.setTextColor(rgb(C_INK), rgb(C_SURFACE));
  canvas.setCursor(MARGIN, H_TITLE_Y);
  canvas.print("Erbgut");

  const bool up = linkUp && (millis() - lastLineMs) < LINK_TIMEOUT_MS;
  const char *status = paused ? "paused" : (up ? "live" : "On Edge");
  const uint32_t sc = paused ? C_TBD : C_GAIN;
  const int sw = strlen(status) * 6;

  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  char caption[48];
  uint32_t capColour = C_MUTED;
  if (touchNote[0] && millis() < touchNoteUntil) {
    snprintf(caption, sizeof(caption), "%s", touchNote);
    capColour = C_AUDIT;
  } else if (source == SRC_EMBEDDED) {
    if (gPolishOnDevice) caption[0] = 0;
    else snprintf(caption, sizeof(caption), "majority vote, the model did not load");
  } else if (!hostModelLive) {
    // the Mac is attached but its polisher is not loaded, which would otherwise be invisible
    snprintf(caption, sizeof(caption), "majority vote, on the Mac");
    capColour = C_COST;
  } else {
    // The tape is slower than the decode, so it shows a sample. The numbers above count
    // every strand; this says how much of the flow a viewer is actually seeing.
    const uint32_t seen = shownStrands ? shownStrands : 1;
    const uint32_t one_in = (uint32_t)(cDone.target / seen + 0.5f);
    if (one_in > 1)
      snprintf(caption, sizeof(caption), "polish on the Mac, tape 1 in %u", (unsigned)one_in);
    else
      snprintf(caption, sizeof(caption), "polish on the Mac");
  }
  canvas.setTextColor(rgb(capColour), rgb(C_SURFACE));
  const int cw = strlen(caption) * 6;
  canvas.setCursor(LCD_WIDTH - MARGIN - sw - 14 - cw, H_TITLE_Y + 4);
  canvas.print(caption);

  // the dot breathes while the link is live, so the panel never looks frozen
  const float breath = up && !paused ? 0.5f + 0.5f * sinf(millis() / 420.0f) : 1.0f;
  canvas.fillCircle(LCD_WIDTH - MARGIN - sw - 7, H_TITLE_Y + 7, 3,
                    rgb(mix(C_SURFACE, sc, 0.35f + 0.65f * breath)));
  canvas.setTextColor(rgb(sc), rgb(C_SURFACE));
  canvas.setCursor(LCD_WIDTH - MARGIN - sw, H_TITLE_Y + 4);
  canvas.print(status);

  // three headline numbers, the ones a judge reads from two metres
  const int col[3] = {MARGIN, 116, 218};
  char value[16];
  canvas.setFont(&fonts::FreeSansBold18pt7b);
  canvas.setTextSize(1);

  snprintf(value, sizeof(value), "%ld", lroundf(cDone.shown));
  canvas.setTextColor(rgb(cDone.colour(C_INK)), rgb(C_SURFACE));
  canvas.setCursor(col[0], H_VALUE_Y);
  canvas.print(value);

  snprintf(value, sizeof(value), "%d%%", (int)lroundf(cAcc.shown));
  canvas.setTextColor(rgb(cAcc.colour(C_GAIN)), rgb(C_SURFACE));
  canvas.setCursor(col[1], H_VALUE_Y);
  canvas.print(value);

  snprintf(value, sizeof(value), "%ld", lroundf(cFix.shown));
  canvas.setTextColor(rgb(cFix.colour(C_GAIN)), rgb(C_SURFACE));
  canvas.setCursor(col[2], H_VALUE_Y);
  canvas.print(value);

  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextColor(rgb(C_MUTED), rgb(C_SURFACE));
  // "fixes" is the model's edits when the polisher is live, and the vote's own corrections
  // when it is not. Different things, so the label says which.
  const char *labels[3] = {"strands", "exact", modelOnScreen() ? "fixes" : "vote fixes"};
  for (int i = 0; i < 3; ++i) {
    canvas.setCursor(col[i], H_LABEL_Y);
    canvas.print(labels[i]);
  }

  drawSparkline(MARGIN, H_SPARK_Y, LCD_WIDTH - 2 * MARGIN, H_SPARK_H);

  // progress through the file, easing toward its target, with 10 px clear above it
  canvas.fillRect(MARGIN, H_BAR_Y, LCD_WIDTH - 2 * MARGIN, H_BAR_H, rgb(C_RULE));
  const int pw = (int)((LCD_WIDTH - 2 * MARGIN) * clamp01(cProg.shown));
  canvas.fillRect(MARGIN, H_BAR_Y, pw, H_BAR_H, rgb(cProg.shown >= 0.999f ? C_GAIN : C_AUDIT));
  canvas.drawFastHLine(0, HEADER_H, LCD_WIDTH, rgb(C_RULE));
}

// ---------------------------------------------------------------- the logo mark

// One lens of the double helix with three base pairs inside it, that is
// marketing/logo/erbgut-mark-small.svg, in its own 64 unit box. The panel API has no
// curves, so each half strand is walked as the cubic the SVG writes and stamped with
// round dots, and each base pair is a rectangle capped with two circles. Only fillRect
// and fillCircle are used, so this needs nothing the rest of the firmware does not.
//
// The full lockup is not attempted here. At 320x240 the binary digits and the base
// letters would be two or three pixels tall, which is mush, so the box shows the mark
// and sets the wordmark in the panel font underneath it.
struct MarkArc {
  float x0, y0, x1, y1, x2, y2, x3, y3;
};
static const MarkArc MARK_ARCS[4] = {
    {1.5f, 32.0f, 11.56f, 13.04f, 23.77f, 8.0f, 32.0f, 8.0f},
    {32.0f, 8.0f, 40.23f, 8.0f, 52.44f, 13.04f, 62.5f, 32.0f},
    {1.5f, 32.0f, 11.56f, 50.96f, 23.77f, 56.0f, 32.0f, 56.0f},
    {32.0f, 56.0f, 40.23f, 56.0f, 52.44f, 50.96f, 62.5f, 32.0f},
};
struct MarkBase {
  float x, y, h;
  uint32_t colour;
};
static const MarkBase MARK_BASES[3] = {
    {10.5f, 18.0f, 28.0f, C_MARK_A},
    {26.75f, 11.0f, 42.0f, C_MARK_C},
    {43.0f, 18.0f, 28.0f, C_MARK_G},
};
static const float MARK_BASE_W = 10.5f;
static const float MARK_STROKE = 9.0f;

// lift[i] in 0..1 fades base pair i in from the paper colour, so the splash can breathe.
static void drawMark(int ox, int oy, float k, const float lift[3]) {
  const int r = (int)lroundf(MARK_STROKE * k * 0.5f);
  const uint32_t helix = rgb(C_MARK_HELIX);
  for (const MarkArc &a : MARK_ARCS) {
    const int steps = 40;
    for (int i = 0; i <= steps; ++i) {
      const float t = (float)i / steps, u = 1.0f - t;
      const float x = u * u * u * a.x0 + 3 * u * u * t * a.x1 + 3 * u * t * t * a.x2 +
                      t * t * t * a.x3;
      const float y = u * u * u * a.y0 + 3 * u * u * t * a.y1 + 3 * u * t * t * a.y2 +
                      t * t * t * a.y3;
      canvas.fillCircle(ox + (int)lroundf(x * k), oy + (int)lroundf(y * k), r, helix);
    }
  }
  const int bw = (int)lroundf(MARK_BASE_W * k);
  const int br = bw / 2;
  for (int i = 0; i < 3; ++i) {
    const MarkBase &b = MARK_BASES[i];
    const uint32_t c = rgb(mix(mix(b.colour, C_PAPER, 0.5f), b.colour, lift[i]));
    const int x = ox + (int)lroundf(b.x * k);
    const int y = oy + (int)lroundf(b.y * k);
    const int h = (int)lroundf(b.h * k);
    canvas.fillCircle(x + br, y + br, br, c);
    canvas.fillCircle(x + br, y + h - br, br, c);
    canvas.fillRect(x, y + br, bw, h - 2 * br, c);
  }
}


// Standalone is the normal state, so the splash says what the box is doing rather than
// asking for a Mac it does not need. If something is broken it says that instead, because a
// box that sits on a hopeful message while an allocation failed is worse than one that
// admits it.
static void splash() {
  canvas.fillSprite(rgb(C_PAPER));
  // the Erbgut mark, centred over where the wordmark sits
  const float t = millis() / 600.0f;
  float lift[3];
  for (int i = 0; i < 3; ++i) lift[i] = 0.5f + 0.5f * sinf(t - i * 0.7f);
  drawMark(103, 18, 1.7f, lift);
  canvas.setFont(&fonts::FreeSansBold18pt7b);
  canvas.setTextSize(1);
  canvas.setTextColor(rgb(C_INK), rgb(C_PAPER));
  canvas.setCursor(116, 142);
  canvas.print("Erbgut");

  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  if (gFault[0]) {
    canvas.setTextColor(rgb(C_COST), rgb(C_PAPER));
    canvas.setCursor(MARGIN, 182);
    canvas.print("fault");
    canvas.setCursor(MARGIN, 194);
    canvas.print(gFault);
    canvas.setTextColor(rgb(C_MUTED), rgb(C_PAPER));
    canvas.setCursor(MARGIN, 210);
    canvas.print("see the USB log for the numbers");
  } else {
    canvas.setTextColor(rgb(C_MUTED), rgb(C_PAPER));
    canvas.setCursor(MARGIN, 186);
    canvas.printf("%s, decoding the first strand",
                  gPolishOnDevice ? "polish on device" : "majority vote on device");
    canvas.setCursor(MARGIN, 200);
    canvas.printf("%d strands compiled in, no Mac needed", BOX_DATA_STRANDS);
  }
  canvas.pushSprite(0, 0);
}

static void render() {
  // one off-screen frame, pushed in one go, so nothing ever flickers
  canvas.fillSprite(rgb(C_PAPER));
  drawHeader();
  drawTape();
  if (gFault[0]) {  // a band across the bottom, impossible to mistake for normal operation
    canvas.fillRect(0, LCD_HEIGHT - 14, LCD_WIDTH, 14, rgb(C_COST));
    canvas.setFont(&fonts::Font0);
    canvas.setTextSize(1);
    canvas.setTextColor(rgb(C_PAPER), rgb(C_COST));
    canvas.setCursor(MARGIN, LCD_HEIGHT - 10);
    canvas.print(gFault);
  }
  canvas.pushSprite(0, 0);
}

// ---------------------------------------------------------------- the tape feeder

static void pushCol(const Col &c) {
  if (ringCount >= COLS) {  // drop the leftmost, it is already off screen
    ringHead = (ringHead + 1) % COLS;
    --ringCount;
  }
  ring[(ringHead + ringCount) % COLS] = c;
  ++ringCount;
}

// Emit one column on the right hand edge: a divider, the next letter of the strand being
// played, or an idle gap when nothing is queued.
static void appendColumn() {
  Col c;
  // nothing queued and no host talking: take the next strand from the frozen run
  if (!curActive && curDivider == 0 && !hasPending && source == SRC_EMBEDDED) feedEmbedded();
  if (!curActive && curDivider == 0 && hasPending) {
    cur = pending;
    hasPending = false;
    curActive = true;
    curPos = 0;
    curLen = (int)strlen(cur.cons);
    curDivider = 1;  // a boundary goes in front of every strand
    ++shownStrands;
  }
  if (curDivider > 0) {
    --curDivider;
    c.kind = 1;
    c.strand = cur.index;
    c.verdict = cur.dropout ? 3 : (cur.ok ? 1 : 2);
    c.risk = cur.risk;
    pushCol(c);
    if (curLen == 0) curActive = false;  // a dropout strand is just its boundary
    return;
  }
  if (curActive && curPos < curLen) {
    c.kind = 0;
    for (int r = 0; r < MAX_READ_LANES; ++r) {
      c.r[r] = (r < cur.shown && curPos < (int)strlen(cur.reads[r])) ? cur.reads[r][curPos] : 0;
      c.rm[r] = (r < cur.shown) ? cur.rmark[r][curPos] : ' ';
    }
    c.cons = cur.cons[curPos];
    c.fix = cur.fix[curPos] == ' ' ? 0 : cur.fix[curPos];
    c.was = cur.was[curPos] ? cur.was[curPos] : c.cons;
    ++curPos;
    if (curPos >= curLen) curActive = false;
    pushCol(c);
    return;
  }
  c.kind = 2;  // idle: the tape keeps moving so the panel never looks frozen
  pushCol(c);
}

// ---------------------------------------------------------------- animation step

static void step(float dt) {
  // The host wins while it is talking. When it stops, the frozen run takes over, and the
  // counters restart from what the box itself has played, because the two sources count
  // different runs and adding them together would be a lie.
  const bool hostTalking = linkUp && (millis() - lastLineMs) < HOST_TIMEOUT_MS;
  const Source want = hostTalking ? SRC_HOST : SRC_EMBEDDED;
  if (want != source) {
    source = want;
    if (source == SRC_EMBEDDED) {
      embDone = embOk = embFix = embErr = embReads = 0;
      embDrop = embEdits = embBases = 0;
    } else {
      // ask the host to say what it is running, so the tag is right within a frame or two
      Serial.println('?');
    }
    Serial.printf("source=%s\n", source == SRC_HOST ? "host" : "embedded");
    haveData = true;
  }

  cDone.step();
  cAcc.step();
  cFix.step();
  cProg.step();
  cReads.step();
  cErrors.step();

  if (!paused) flowPx += flowSpeed * CELL_W * dt;
  while (flowPx >= CELL_W && ringCount > 0) {
    flowPx -= CELL_W;
    ringHead = (ringHead + 1) % COLS;
    --ringCount;
  }
  // keep the right hand edge fed
  while (ringCount < COLS && (TAPE_X0 + ringCount * CELL_W - flowPx) < LCD_WIDTH + CELL_W)
    appendColumn();

  // a column resolves the moment its middle reaches the head, and ages from there
  for (int i = 0; i < ringCount; ++i) {
    Col &c = colAt(i);
    const float x = TAPE_X0 + i * CELL_W - flowPx;
    if (c.age == 0) {
      if (c.kind != 2 && x + CELL_W * 0.5f <= HEAD_X) c.age = 1;
    } else if (c.age < 60000) {
      ++c.age;
    }
  }
}

// ---------------------------------------------------------------- events

static void applyStats(JsonObjectConst st) {
  if (st.isNull()) return;
  const long done = st["done"] | (long)cDone.target;
  const long ok = st["ok"] | 0L;
  cDone.set((float)done, true);
  cAcc.set(done ? 100.0f * ok / done : 0.0f, true);
  // mfix is the model's edit count over the WHOLE strand, not just the visible window
  if (st["mfix"].is<long>()) cFix.set((float)st["mfix"].as<long>(), true);
  if (st["rfix"].is<long>()) cErrors.set((float)st["rfix"].as<long>(), false);
  if (st["rps"].is<float>()) cReads.set(st["rps"].as<float>(), true);
  if (st["prog"].is<float>()) cProg.set(st["prog"].as<float>(), true);
}

static void handleEvent(JsonDocument &doc) {
  const char *t = doc["t"] | "";
  linkUp = true;
  lastLineMs = millis();

  if (!strcmp(t, "s")) {
    Strand b;
    b.used = true;
    b.index = doc["i"] | 0;
    b.nreads = doc["nr"] | 0;
    b.ok = doc["ok"] | false;
    b.dropout = doc["d"] | false;
    JsonArrayConst reads = doc["r"];
    int j = 0;
    for (JsonObjectConst r : reads) {
      if (j >= MAX_READ_LANES) break;
      strncpy(b.reads[j], r["s"] | "", VISIBLE_LETTERS);
      b.reads[j][VISIBLE_LETTERS] = 0;
      unpackMarks(r["m"] | "", b.rmark[j]);
      ++j;
    }
    b.shown = j;
    strncpy(b.cons, doc["c"] | "", VISIBLE_LETTERS);
    b.cons[VISIBLE_LETTERS] = 0;
    unpackMarks(doc["f"] | "", b.fix);
    unpackReplaced(doc["f"] | "", doc["fp"] | "", b.was);

    applyStats(doc["st"]);
    // The tape runs slower than the decode, so only the freshest strand waits its turn and
    // the rest are counted as skipped. The header numbers still count every strand.
    if (hasPending) ++skippedStrands;
    pending = b;
    hasPending = true;

    spark[sparkAt] = b.dropout ? 3 : (b.ok ? 1 : 2);
    sparkAt = (sparkAt + 1) % SPARK_N;
    haveData = true;
  } else if (!strcmp(t, "hello")) {
    strncpy(decoderName, doc["dec"] | "?", sizeof(decoderName) - 1);
    const char *layer = doc["layer"] | "";
    hostModelLive = !strcmp(layer, "model");
    snprintf(decoderTag, sizeof(decoderTag), "%s", hostModelLive ? "polish" : "vote only");
    snprintf(channelName, sizeof(channelName), "%s", doc["ch"] | "");
  } else if (!strcmp(t, "pass")) {
    snprintf(channelName, sizeof(channelName), "%.14s %.12s", doc["ch"] | "", doc["file"] | "");
  } else if (!strcmp(t, "state")) {
    paused = doc["paused"] | false;
  } else if (!strcmp(t, "hi")) {
    Serial.println('?');  // the server just opened the port, ask it what is running
  }
}

// ---------------------------------------------------------------- serial

static char lineBuf[LINE_BUFFER];
static size_t lineLen = 0;

static void pumpSerial() {
  while (Serial.available()) {
    const int c = Serial.read();
    if (c < 0) break;
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        lineBuf[lineLen] = 0;
        if (lineLen == 1 && lineBuf[0] == 't') {
          gSelfTest = 40;  // diagnostic, see runSelfTest
        } else if (lineLen == 1 && lineBuf[0] == 'T') {
          gSelfTest = BOX_DATA_STRANDS;
        } else {
          JsonDocument doc;
          if (!deserializeJson(doc, lineBuf, lineLen)) handleEvent(doc);
        }
        lineLen = 0;
      }
      continue;
    }
    if (lineLen + 1 < sizeof(lineBuf)) lineBuf[lineLen++] = (char)c;
    else lineLen = 0;  // a line that long is corrupt, start over
  }
}

// ---------------------------------------------------------------- touch

// Pausing by accident on stage is worse than not being able to pause, so the middle of the
// screen needs a deliberate long press and a short tap there does nothing. The GT911 reports
// ghost touches shortly after boot, so nothing is believed for the first TOUCH_IGNORE_MS.
// Every action prints what caused it, with the coordinates, so a stray pause is never a guess.
static uint32_t lastActionMs = 0;
static bool touchDown = false;
static uint32_t touchStart = 0;
static int32_t touchX = 0, touchY = 0;
static bool touchFired = false;

static void note(const char *text) {
  strncpy(touchNote, text, sizeof(touchNote) - 1);
  touchNote[sizeof(touchNote) - 1] = 0;
  touchNoteUntil = millis() + 1400;
}

static void fire(char key, const char *what) {
  Serial.println(key);  // a one character line is a command, see the server's _handle_line
  Serial.printf("touch x=%ld y=%ld held=%lums action=%s\n", (long)touchX, (long)touchY,
                (unsigned long)(millis() - touchStart), what);
  note(what);
  lastActionMs = millis();
  touchFired = true;
}

static void pumpTouch() {
#if TOUCH_ENABLED
  int32_t x = 0, y = 0;
  const bool down = lcd.getTouch(&x, &y);
  const uint32_t now = millis();

  if (now < TOUCH_IGNORE_MS) {  // ghost touches right after boot are not real
    touchDown = false;
    return;
  }

  if (down && !touchDown) {  // finger went down
    touchDown = true;
    touchStart = now;
    touchX = x;
    touchY = y;
    touchFired = false;
    return;
  }

  if (down && touchDown) {  // still held
    touchX = x;
    touchY = y;
    const bool middle = x >= LCD_WIDTH / 3 && x <= (2 * LCD_WIDTH) / 3;
    if (middle && !touchFired && now - touchStart >= TOUCH_HOLD_MS &&
        now - lastActionMs > TOUCH_REPEAT_MS) {
      fire(paused ? 'r' : 'p', paused ? "resume" : "pause");
    }
    return;
  }

  if (!down && touchDown) {  // finger lifted
    touchDown = false;
    const uint32_t held = now - touchStart;
    if (touchFired || held < 40 || now - lastActionMs <= TOUCH_REPEAT_MS) return;
    if (touchX < LCD_WIDTH / 3) {
      flowSpeed = fmaxf(FLOW_MIN, flowSpeed / 1.4f);
      char msg[24];
      snprintf(msg, sizeof(msg), "%.1f letters/s", flowSpeed);
      note(msg);
      Serial.printf("touch x=%ld y=%ld action=tape slower, %.1f letters/s\n", (long)touchX,
                    (long)touchY, flowSpeed);
      lastActionMs = millis();
    } else if (touchX > (2 * LCD_WIDTH) / 3) {
      flowSpeed = fminf(FLOW_MAX, flowSpeed * 1.4f);
      char msg[24];
      snprintf(msg, sizeof(msg), "%.1f letters/s", flowSpeed);
      note(msg);
      Serial.printf("touch x=%ld y=%ld action=tape faster, %.1f letters/s\n", (long)touchX,
                    (long)touchY, flowSpeed);
      lastActionMs = millis();
    } else {
      // a short tap in the middle is ignored on purpose; pause needs a long press
      Serial.printf("touch x=%ld y=%ld held=%lums action=ignored, hold to pause\n",
                    (long)touchX, (long)touchY, (unsigned long)held);
      note("hold to pause");
    }
  }
#endif
}

// ---------------------------------------------------------------- arduino

void setup() {
  Serial.setRxBufferSize(2048);  // a strand line is a few hundred bytes, never lose one
  Serial.begin(115200);
  delay(1200);  // let the host enumerate the CDC endpoint before we say anything
  Serial.println("boot");
  Serial.flush();

  const bool lcdOk = lcd.init();
  lcd.setRotation(LCD_ROTATION);
  lcd.setBrightness(200);

  // Hot in internal RAM, cold in PSRAM. The frame is rewritten twenty to thirty times a
  // second, so it stays internal; the two models' working buffers are touched once per
  // strand and live in the 8 MB nobody else is using. Putting the frame in PSRAM as well
  // costs about eight frames a second and buys nothing once the models have moved out.
  Serial.printf("psram size=%u free=%u internal free=%u\n", (unsigned)ESP.getPsramSize(),
                (unsigned)ESP.getFreePsram(),
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
  canvas.setColorDepth(16);
  canvas.setPsram(false);
  canvasReady = canvas.createSprite(LCD_WIDTH, LCD_HEIGHT) != nullptr;
  bool inPsram = false;
  if (!canvasReady && ESP.getPsramSize() > 0) {  // no internal room: PSRAM rather than nothing
    canvas.setPsram(true);
    canvasReady = canvas.createSprite(LCD_WIDTH, LCD_HEIGHT) != nullptr;
    inPsram = canvasReady;
  }
  if (!canvasReady) snprintf(gFault, sizeof(gFault), "no memory for the 320x240 frame");
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  canvas.setTextWrap(false);

  Serial.printf("lcd init=%d %dx%d sprite=%d sprite_in_psram=%d heap=%u internal=%u\n",
                (int)lcdOk, lcd.width(), lcd.height(), (int)canvasReady, (int)inPsram,
                (unsigned)ESP.getFreeHeap(),
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
  memset(spark, 0, sizeof(spark));
  if (canvasReady) splash();
  boxpolish::setYield([]() { vTaskDelay(1); });
  gPolishOnDevice = boxpolish::available() && boxpolish::begin();
  Serial.printf("polish on device=%d working=%u bytes heap=%u\n", (int)gPolishOnDevice,
                (unsigned)boxpolish::workingBytes(), (unsigned)ESP.getFreeHeap());
  gRiskOnDevice = boxrisk::available() && boxrisk::begin();
  Serial.printf("risk on device=%d working=%u bytes heap=%u internal=%u psram free=%u\n",
                (int)gRiskOnDevice, (unsigned)boxrisk::workingBytes(),
                (unsigned)ESP.getFreeHeap(),
                (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                (unsigned)ESP.getFreePsram());
  if (!gPolishOnDevice) snprintf(gFault, sizeof(gFault), "polisher did not fit, classic only");

  // the decoder gets the core the renderer is not on
  // A 12 KB stack out of internal RAM. This used to be created without checking, and when
  // the heap was tight it silently never started: the panel sat on the splash screen and the
  // box looked like it was waiting for a Mac.
  gDecodeTask = nullptr;
  const BaseType_t made = xTaskCreatePinnedToCore(decodeTask, "decode", 12288, nullptr, 1,
                                                  &gDecodeTask,
                                                  xPortGetCoreID() == 0 ? 1 : 0);
  if (made != pdPASS || gDecodeTask == nullptr) {
    snprintf(gFault, sizeof(gFault), "decode task did not start, heap %u",
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    Serial.printf("FAULT: %s\n", gFault);
  }
  Serial.printf("decode task started=%d\n", (int)(gDecodeTask != nullptr));
  Serial.println("display ready");
  Serial.println('?');  // ask the server what is running
}

void loop() {
  static uint32_t lastFrame = 0;
  static uint32_t lastBeat = 0;
  static uint32_t fpsWindow = 0;
  static uint16_t frames = 0;

  pumpSerial();  // twice per frame: never let the CDC buffer back up while we draw
  pumpTouch();

  const uint32_t now = millis();
  if (now - lastBeat > 1000) {
    lastBeat = now;
    Serial.println('.');  // heartbeat, the server uses it to tell whether the box is running
  }

  if (now - lastFrame >= FRAME_MS) {
    lastFrame = now;
    if (linkUp && now - lastLineMs > LINK_TIMEOUT_MS) linkUp = false;
    static uint32_t prev = 0;
    const float dt = prev ? fminf(0.2f, (now - prev) / 1000.0f) : 1.0f / TARGET_FPS;
    prev = now;
    step(dt);
    if (canvasReady) {
      if (haveData) render();
      else splash();
    }
    ++frames;
  }
  pumpSerial();

  if (now - fpsWindow > 3000) {
    fps = frames * 1000.0f / (now - fpsWindow);
    // ESP-IDF's high water mark is already in bytes, unlike vanilla FreeRTOS which
    // reports words. Multiplying by four here once claimed more free stack than the task
    // was ever given.
    const unsigned stackLeft =
        gDecodeTask ? (unsigned)uxTaskGetStackHighWaterMark(gDecodeTask) : 0u;
    Serial.printf("fps=%.1f heap=%u internal=%u psram=%u decode=%luus risk=%luus n=%lu "
                  "stack_left=%u\n", fps, (unsigned)ESP.getFreeHeap(),
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                  (unsigned)ESP.getFreePsram(), (unsigned long)gDecodeUs,
                  (unsigned long)gRiskUs, (unsigned long)gDecoded, stackLeft);
    Serial.printf("tally done=%lu ok=%lu drop=%lu fixes=%lu edits=%lu bases=%lu\n",
                  (unsigned long)embDone, (unsigned long)embOk, (unsigned long)embDrop,
                  (unsigned long)embFix, (unsigned long)embEdits, (unsigned long)embBases);
    // standalone and nothing has come out of the decoder for a long time: say so
    if (source == SRC_EMBEDDED && gFault[0] == 0 && gDecoded == 0 && now > 25000)
      snprintf(gFault, sizeof(gFault), "no strand decoded in %lus", (unsigned long)(now / 1000));
    fpsWindow = now;
    frames = 0;
  }
}
