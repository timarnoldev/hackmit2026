// Erbgut live decode ticker, ESP32-S3-BOX-3.
//
// The box is a thin display. It draws what the Mac sends it over the USB cable and nothing
// else: no WiFi, no decoding, no invented letters. scripts/ticker_server.py runs the real
// encode, channel simulation and decode, and pushes one compact JSON object per line.
//
// Screen, 320 x 240:
//   top    a statistics header, the same five numbers as the browser view
//   bottom a ticker of strands, each one a few noisy reads and the decoded strand under them
//
// Colours are the Erbgut palette (marketing/BRAND.md, dark tokens):
//   cost magenta   a wrong letter in a read
//   audit indigo   an extra letter in a read
//   dashed magenta a letter the read is missing
//   gain teal      a correction the decoder applied, and a strand that came back exactly
//
// Touch, if the panel answers: left third slower, middle pause and resume, right third
// faster. One character goes back up the same USB link.

#include <Arduino.h>
#include <ArduinoJson.h>

#include "board_config.h"

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

// ---------------------------------------------------------------- palette

// Erbgut dark tokens, converted to RGB565 at run time by the driver.
static uint32_t C_PAPER = 0x0F1417;
static uint32_t C_SURFACE = 0x172026;
static uint32_t C_INK = 0xE6ECEA;
static uint32_t C_MUTED = 0x9AA9AE;
static uint32_t C_RULE = 0x2C353B;
static uint32_t C_AUDIT = 0xA3A6FF;
static uint32_t C_COST = 0xFF86B0;
static uint32_t C_GAIN = 0x4FD6B5;
static uint32_t C_TBD = 0xF4C45A;

static inline uint16_t rgb(uint32_t hex) {
  return lcd.color565((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF);
}

// ---------------------------------------------------------------- layout

#define HEADER_H 66
#define CELL_W 8
#define CELL_H 11
#define SEQ_X 40   // the label gutter to the left of the letters
#define BLOCK_GAP 5

// ---------------------------------------------------------------- state

struct ReadLine {
  char seq[VISIBLE_LETTERS + 1];
  char mark[VISIBLE_LETTERS + 1];  // one kind per position, ' ' for none
};

struct Block {
  bool used = false;
  bool dropout = false;
  bool ok = false;
  int index = 0;
  int total = 0;
  int nreads = 0;
  int nfix = 0;
  int shown = 0;
  ReadLine reads[MAX_READS];
  ReadLine cons;
};

static Block blocks[MAX_BLOCKS];  // blocks[0] is the newest
static bool dirty = true;

struct Stats {
  long done = 0;
  long ok = 0;
  long rfix = 0;
  long mfix = 0;
  float rps = 0;
  float prog = 0;
} stats;

static char decoderName[40] = "waiting";
static char channelName[28] = "";
static char fileName[28] = "";
static bool linkUp = false;
static bool paused = false;
static uint32_t lastLineMs = 0;
static char touchNote[40] = "";

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

static void fillLine(ReadLine &line, const char *seq, const char *marks) {
  strncpy(line.seq, seq ? seq : "", VISIBLE_LETTERS);
  line.seq[VISIBLE_LETTERS] = 0;
  unpackMarks(marks, line.mark);
}

// ---------------------------------------------------------------- drawing

static void drawSeq(int x, int y, const ReadLine &line, bool consensus) {
  const int n = strlen(line.seq);
  for (int i = 0; i < n && i < VISIBLE_LETTERS; ++i) {
    const int cx = x + i * CELL_W;
    const char kind = line.mark[i];
    uint16_t fg = consensus ? rgb(C_INK) : rgb(C_MUTED);
    uint16_t bg = 0;
    bool filled = false;
    switch (kind) {
      case 'x': bg = rgb(C_COST); fg = rgb(C_PAPER); filled = true; break;   // wrong letter
      case 'e': bg = rgb(C_AUDIT); fg = rgb(C_PAPER); filled = true; break;  // extra letter
      case 's':
      case 'i': bg = rgb(C_GAIN); fg = rgb(C_PAPER); filled = true; break;   // a correction
      default: break;
    }
    if (filled) canvas.fillRect(cx, y, CELL_W, CELL_H, bg);
    if (kind == 'm') {  // a letter the read is missing: a dashed edge in front of it
      for (int dy = y + 1; dy < y + CELL_H - 1; dy += 3) canvas.drawPixel(cx, dy, rgb(C_COST));
    }
    if (kind == 'd') canvas.drawFastVLine(cx, y + 1, CELL_H - 2, rgb(C_GAIN));
    canvas.setTextColor(fg, filled ? bg : rgb(C_PAPER));
    canvas.setCursor(cx + 1, y + 2);
    canvas.print(line.seq[i]);
  }
}

static void drawHeader() {
  canvas.fillRect(0, 0, LCD_WIDTH, HEADER_H, rgb(C_SURFACE));
  canvas.setTextSize(1);

  canvas.setTextColor(rgb(C_INK), rgb(C_SURFACE));
  canvas.setCursor(6, 4);
  canvas.print("ERBGUT");
  canvas.setTextColor(rgb(C_MUTED), rgb(C_SURFACE));
  canvas.setCursor(56, 4);
  canvas.print("live decode");

  const bool up = linkUp && (millis() - lastLineMs) < LINK_TIMEOUT_MS;
  canvas.setTextColor(up ? rgb(C_GAIN) : rgb(C_COST), rgb(C_SURFACE));
  const char *status = paused ? "paused" : (up ? "live" : "no link");
  canvas.setCursor(LCD_WIDTH - 6 - strlen(status) * 6, 4);
  canvas.print(status);

  // five numbers, the same ones the browser view shows
  const int cols = 5;
  const int w = LCD_WIDTH / cols;
  char value[16];
  const char *labels[cols] = {"strands", "exact", "read err", "fixes", "reads/str"};
  uint16_t colors[cols] = {rgb(C_INK), rgb(C_GAIN), rgb(C_COST), rgb(C_GAIN), rgb(C_AUDIT)};
  for (int c = 0; c < cols; ++c) {
    switch (c) {
      case 0: snprintf(value, sizeof(value), "%ld", stats.done); break;
      case 1: snprintf(value, sizeof(value), "%d%%",
                       stats.done ? (int)lroundf(100.0f * stats.ok / stats.done) : 0); break;
      case 2: snprintf(value, sizeof(value), "%ld", stats.rfix); break;
      case 3: snprintf(value, sizeof(value), "%ld", stats.mfix); break;
      default: snprintf(value, sizeof(value), "%.1f", stats.rps); break;
    }
    canvas.setTextSize(2);
    canvas.setTextColor(colors[c], rgb(C_SURFACE));
    canvas.setCursor(c * w + 4, 18);
    canvas.print(value);
    canvas.setTextSize(1);
    canvas.setTextColor(rgb(C_MUTED), rgb(C_SURFACE));
    canvas.setCursor(c * w + 4, 36);
    canvas.print(labels[c]);
  }

  canvas.setTextSize(1);
  canvas.setTextColor(rgb(C_MUTED), rgb(C_SURFACE));
  canvas.setCursor(6, 48);
  char line[64];
  snprintf(line, sizeof(line), "%.20s  %.22s", channelName[0] ? channelName : "channel",
           decoderName);
  canvas.print(line);

  canvas.drawFastHLine(0, HEADER_H - 4, LCD_WIDTH, rgb(C_RULE));
  canvas.fillRect(0, HEADER_H - 4, (int)(LCD_WIDTH * stats.prog), 3,
                  stats.prog >= 0.999f ? rgb(C_GAIN) : rgb(C_AUDIT));
}

static int blockHeight(const Block &b) {
  if (!b.used) return 0;
  if (b.dropout) return CELL_H + BLOCK_GAP;
  return (b.shown + 1) * CELL_H + BLOCK_GAP;
}

static void drawTicker() {
  canvas.fillRect(0, HEADER_H, LCD_WIDTH, LCD_HEIGHT - HEADER_H, rgb(C_PAPER));
  canvas.setTextSize(1);
  int y = HEADER_H + 3;
  for (int k = 0; k < MAX_BLOCKS; ++k) {
    const Block &b = blocks[k];
    if (!b.used) break;
    const int h = blockHeight(b);
    if (y + h > LCD_HEIGHT) break;

    const uint16_t edge = b.dropout ? rgb(C_TBD) : (b.ok ? rgb(C_GAIN) : rgb(C_COST));
    canvas.fillRect(0, y, 2, h - BLOCK_GAP, edge);

    char label[16];
    if (b.dropout) {
      canvas.setTextColor(rgb(C_TBD), rgb(C_PAPER));
      canvas.setCursor(6, y + 2);
      snprintf(label, sizeof(label), "%d", b.index + 1);
      canvas.print(label);
      canvas.setTextColor(rgb(C_MUTED), rgb(C_PAPER));
      canvas.setCursor(SEQ_X, y + 2);
      canvas.print("no reads came back");
      y += h;
      continue;
    }

    for (int r = 0; r < b.shown; ++r) {
      canvas.setTextColor(rgb(C_RULE), rgb(C_PAPER));
      canvas.setCursor(6, y + 2);
      snprintf(label, sizeof(label), "r%d", r + 1);
      canvas.print(label);
      drawSeq(SEQ_X, y, b.reads[r], false);
      y += CELL_H;
    }
    canvas.setTextColor(b.ok ? rgb(C_GAIN) : rgb(C_COST), rgb(C_PAPER));
    canvas.setCursor(6, y + 2);
    snprintf(label, sizeof(label), "%d", b.index + 1);
    canvas.print(label);
    drawSeq(SEQ_X, y, b.cons, true);
    y += CELL_H + BLOCK_GAP;
  }

  if (touchNote[0]) {
    canvas.setTextColor(rgb(C_MUTED), rgb(C_PAPER));
    canvas.setCursor(6, LCD_HEIGHT - 10);
    canvas.print(touchNote);
  }
}

static void render() {
  drawHeader();
  drawTicker();
  canvas.pushSprite(0, 0);
  dirty = false;
}

// ---------------------------------------------------------------- events

static void pushBlock(const Block &b) {
  for (int k = MAX_BLOCKS - 1; k > 0; --k) blocks[k] = blocks[k - 1];
  blocks[0] = b;
}

static void applyStats(JsonObjectConst st) {
  if (st.isNull()) return;
  if (st["done"].is<long>()) stats.done = st["done"].as<long>();
  if (st["ok"].is<long>()) stats.ok = st["ok"].as<long>();
  if (st["rfix"].is<long>()) stats.rfix = st["rfix"].as<long>();
  if (st["mfix"].is<long>()) stats.mfix = st["mfix"].as<long>();
  if (st["rps"].is<float>()) stats.rps = st["rps"].as<float>();
  if (st["prog"].is<float>()) stats.prog = st["prog"].as<float>();
}

static void handleEvent(JsonDocument &doc) {
  const char *t = doc["t"] | "";
  linkUp = true;
  lastLineMs = millis();

  if (!strcmp(t, "s")) {
    Block b;
    b.used = true;
    b.index = doc["i"] | 0;
    b.total = doc["n"] | 0;
    b.nreads = doc["nr"] | 0;
    b.ok = doc["ok"] | false;
    b.dropout = doc["d"] | false;
    JsonArrayConst reads = doc["r"];
    int j = 0;
    for (JsonObjectConst r : reads) {
      if (j >= MAX_READS) break;
      fillLine(b.reads[j], r["s"] | "", r["m"] | "");
      ++j;
    }
    b.shown = j;
    fillLine(b.cons, doc["c"] | "", doc["f"] | "");
    applyStats(doc["st"]);
    pushBlock(b);
    dirty = true;
  } else if (!strcmp(t, "hello")) {
    strncpy(decoderName, doc["dec"] | "?", sizeof(decoderName) - 1);
    snprintf(channelName, sizeof(channelName), "%s %gx", doc["ch"] | "", doc["rps"] | 0.0f);
    dirty = true;
  } else if (!strcmp(t, "pass")) {
    strncpy(fileName, doc["file"] | "", sizeof(fileName) - 1);
    snprintf(channelName, sizeof(channelName), "%.14s %.10s", doc["ch"] | "", fileName);
    dirty = true;
  } else if (!strcmp(t, "state")) {
    paused = doc["paused"] | false;
    dirty = true;
  } else if (!strcmp(t, "hi")) {
    // the server just opened the port; ask it to resend what it knows
    Serial.write('?');
    dirty = true;
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
        JsonDocument doc;
        if (!deserializeJson(doc, lineBuf, lineLen)) handleEvent(doc);
        lineLen = 0;
      }
      continue;
    }
    if (lineLen + 1 < sizeof(lineBuf)) lineBuf[lineLen++] = (char)c;
    else lineLen = 0;  // a line that long is corrupt, start over
  }
}

// ---------------------------------------------------------------- touch

static uint32_t lastTouchMs = 0;

static void pumpTouch() {
#if TOUCH_ENABLED
  int32_t x = 0, y = 0;
  if (!lcd.getTouch(&x, &y)) return;
  if (millis() - lastTouchMs < 350) return;  // one action per tap
  lastTouchMs = millis();
  if (x < LCD_WIDTH / 3) {
    Serial.write('-');
    strncpy(touchNote, "slower", sizeof(touchNote) - 1);
  } else if (x > (2 * LCD_WIDTH) / 3) {
    Serial.write('+');
    strncpy(touchNote, "faster", sizeof(touchNote) - 1);
  } else {
    Serial.write(paused ? 'r' : 'p');
    strncpy(touchNote, paused ? "resume" : "pause", sizeof(touchNote) - 1);
  }
  dirty = true;
#endif
}

// ---------------------------------------------------------------- splash

static void splash() {
  canvas.fillSprite(rgb(C_PAPER));
  // the Erbgut mark: four bases on a strand, a dimension line over the run of two
  const int bx = 116, by = 78;
  const uint32_t bars[4] = {C_AUDIT, C_COST, C_GAIN, C_GAIN};
  for (int i = 0; i < 4; ++i) canvas.fillRect(bx + i * 24, by, 16, 46, rgb(bars[i]));
  canvas.fillRect(bx - 6, by + 46, 112, 4, rgb(C_INK));
  canvas.fillRect(bx + 48, by - 16, 46, 2, rgb(C_INK));
  canvas.fillRect(bx + 48, by - 22, 2, 14, rgb(C_INK));
  canvas.fillRect(bx + 92, by - 22, 2, 14, rgb(C_INK));
  canvas.setTextSize(2);
  canvas.setTextColor(rgb(C_INK), rgb(C_PAPER));
  canvas.setCursor(116, 146);
  canvas.print("Erbgut");
  canvas.setTextSize(1);
  canvas.setTextColor(rgb(C_MUTED), rgb(C_PAPER));
  canvas.setCursor(70, 172);
  canvas.print("live decode ticker, waiting for USB");
  canvas.setCursor(58, 186);
  canvas.print("run scripts/ticker_server.py on the Mac");
  canvas.pushSprite(0, 0);
}

// ---------------------------------------------------------------- arduino

void setup() {
  Serial.begin(115200);  // cosmetic on native USB, kept for a UART bridge
  delay(1200);           // let the host enumerate the CDC endpoint before we say anything
  Serial.write('B');     // "the sketch is running", before anything that could hang
  Serial.flush();
  const bool lcdOk = lcd.init();
  lcd.setRotation(LCD_ROTATION);
  lcd.setBrightness(200);
  Serial.printf("lcd init=%d %dx%d\n", (int)lcdOk, lcd.width(), lcd.height());
  canvas.setPsram(true);
  canvas.setColorDepth(16);
  canvas.createSprite(LCD_WIDTH, LCD_HEIGHT);
  canvas.setFont(&fonts::Font0);
  canvas.setTextSize(1);
  splash();
#if TOUCH_ENABLED
  int32_t x, y;
  lcd.getTouch(&x, &y);  // a first call tells us whether the panel answers at all
#endif
  Serial.write('D');  // "the display came up"
  Serial.write('?');  // ask the server for a hello as soon as the cable is up
}

void loop() {
  pumpSerial();
  pumpTouch();

  // A heartbeat the server can see. The server ignores it; it only proves the box is alive
  // and that the link works in both directions.
  static uint32_t lastBeat = 0;
  if (millis() - lastBeat > 1000) {
    lastBeat = millis();
    Serial.write('.');
  }

  static uint32_t lastDraw = 0;
  const uint32_t now = millis();
  const bool linkChanged = linkUp && (now - lastLineMs > LINK_TIMEOUT_MS);
  if ((dirty || linkChanged) && now - lastDraw > 40) {
    if (linkChanged) linkUp = false;
    if (stats.done == 0 && !blocks[0].used) splash();
    else render();
    lastDraw = now;
  }
  delay(2);
}
