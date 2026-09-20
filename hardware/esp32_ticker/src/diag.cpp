// Display bring-up diagnostic for the ESP32-S3-BOX-3.
//
// Round one of this sweep found why the panel stayed white: the BOX-3 is an ILI9342C, GPIO
// 48 is an input pull-up and not a reset line, and the bus is three wire SPI. Round two is
// about colour. The panel came up but everything looked blue, which is what a channel order
// or inversion mismatch does, so this draws six named colour bars and asks a human which bar
// is actually which colour. Nothing else can answer that: the panel's MISO is not wired, so
// the firmware can never read back what it drew.
//
// One configuration per boot, six seconds each, the index kept in RTC memory.
//
// Build and flash:
//   pio run -e diag -t upload --upload-port /dev/cu.usbmodem1101
// Watch it:
//   pio device monitor -e diag        (or any reader on /dev/cu.usbmodem1101)
//
// Serial keys:
//   0..9 a..f  hold that configuration and stop cycling
//   C          resume cycling
//   n          jump to the next one now
//   ?          print the table again
//
// Back to the ticker firmware:
//   pio run -t upload --upload-port /dev/cu.usbmodem1101

#include <Arduino.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

// ---------------------------------------------------------------- the candidates

enum PanelKind { P_ILI9342, P_ILI9341 };

struct Candidate {
  const char *name;
  PanelKind panel;
  bool invert;
  bool rgb_order;  // false = BGR, true = RGB
};

// The wiring is settled, so only the two colour knobs and the controller vary. Candidate 0
// is what LovyanGFX's own ESP32_S3_BOX_V3 profile does.
static const Candidate CANDIDATES[] = {
    {"ILI9342  BGR  invert off", P_ILI9342, false, false},
    {"ILI9342  RGB  invert off", P_ILI9342, false, true},
    {"ILI9342  BGR  invert ON", P_ILI9342, true, false},
    {"ILI9342  RGB  invert ON", P_ILI9342, true, true},
    {"ILI9341  BGR  invert off", P_ILI9341, false, false},
    {"ILI9341  RGB  invert off", P_ILI9341, false, true},
    {"ILI9341  BGR  invert ON", P_ILI9341, true, false},
    {"ILI9341  RGB  invert ON", P_ILI9341, true, true},
};
static const int N_CANDIDATES = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);

// Settled by round one of the sweep, same as LovyanGFX's BOX-3 profile.
#define PIN_MOSI 6
#define PIN_SCLK 7
#define PIN_CS 5
#define PIN_DC 4
#define PIN_RST -1   // GPIO 48 is an input pull-up on this board, never a reset line
#define PIN_PULLUP 48
#define PIN_BL 47
#define SPI_HZ 40000000
#define OFFSET_ROTATION 1
#define ROTATION 1
#define HOLD_MS 6000

// ---------------------------------------------------------------- one configurable device

class DiagDisplay : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9342 _ili9342;
  lgfx::Panel_ILI9341 _ili9341;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;

 public:
  bool apply(const Candidate &c) {
    pinMode(PIN_PULLUP, INPUT_PULLUP);
    lgfx::Panel_LCD *panel = (c.panel == P_ILI9341) ? (lgfx::Panel_LCD *)&_ili9341
                                                    : (lgfx::Panel_LCD *)&_ili9342;
    {
      auto cfg = _bus.config();
      cfg.spi_host = SPI2_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = SPI_HZ;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = PIN_SCLK;
      cfg.pin_mosi = PIN_MOSI;
      cfg.pin_miso = -1;
      cfg.pin_dc = PIN_DC;
      _bus.config(cfg);
      panel->setBus(&_bus);
    }
    {
      auto cfg = panel->config();
      cfg.pin_cs = PIN_CS;
      cfg.pin_rst = PIN_RST;
      cfg.pin_busy = -1;
      cfg.offset_rotation = OFFSET_ROTATION;
      cfg.readable = false;
      cfg.invert = c.invert;
      cfg.rgb_order = c.rgb_order;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      panel->config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = PIN_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      panel->setLight(&_light);
    }
    setPanel(panel);
    const bool ok = init();
    setRotation(ROTATION);
    setBrightness(255);
    return ok;
  }
};

static DiagDisplay lcd;

// ---------------------------------------------------------------- the test pattern

// Six named bars. The name is the colour the bar is meant to be, so a person can read off
// exactly which channel went where. Two of them are the brand colours we actually care
// about, because "teal and magenta look right" is the real acceptance test.
struct Swatch {
  const char *name;
  uint8_t r, g, b;
};
static const Swatch SWATCHES[] = {
    {"RED", 255, 0, 0},      {"GREEN", 0, 255, 0},   {"BLUE", 0, 0, 255},
    {"WHITE", 230, 236, 234}, {"TEAL", 79, 214, 181}, {"MAGENTA", 255, 134, 176},
};
static const int N_SWATCHES = sizeof(SWATCHES) / sizeof(SWATCHES[0]);

static void drawPattern(int index, const Candidate &c) {
  const int w = lcd.width(), h = lcd.height();
  lcd.fillScreen(lcd.color888(15, 20, 23));  // Erbgut paper: this must look near black

  const int barW = w / N_SWATCHES;
  const int top = 40, barH = 120;
  for (int i = 0; i < N_SWATCHES; ++i) {
    const Swatch &s = SWATCHES[i];
    lcd.fillRect(i * barW, top, barW - 2, barH, lcd.color888(s.r, s.g, s.b));
    lcd.setFont(&fonts::Font0);
    lcd.setTextSize(1);
    lcd.setTextColor(lcd.color888(0, 0, 0));
    lcd.setCursor(i * barW + 3, top + barH - 12);
    lcd.print(s.name);
  }

  lcd.setTextSize(2);
  lcd.setTextColor(lcd.color888(230, 236, 234), lcd.color888(15, 20, 23));
  lcd.setCursor(8, 8);
  lcd.printf("cfg %d/%d", index, N_CANDIDATES - 1);
  lcd.setTextSize(1);
  lcd.setCursor(8, 172);
  lcd.print(c.name);
  lcd.setCursor(8, 188);
  lcd.print("background should be near black");
  lcd.setCursor(8, 202);
  lcd.print("each bar should match its label");
}

// ---------------------------------------------------------------- state across a restart

// RTC_NOINIT_ATTR, not RTC_DATA_ATTR: .rtc.data is reloaded from the image on every boot,
// including after esp_restart(), so a counter kept there never advances. .rtc_noinit is
// left alone, which is what a sweep across reboots needs. No initialisers, by definition.
RTC_NOINIT_ATTR static uint32_t rtcMagic;
RTC_NOINIT_ATTR static int rtcIndex;
RTC_NOINIT_ATTR static bool rtcHold;
static const uint32_t MAGIC = 0xE26B0705;

static void printTable() {
  Serial.println();
  Serial.println("Erbgut colour bring-up. One configuration per boot, six seconds each.");
  Serial.println("Keys: 0..9 a..f hold one, C resume cycling, n next now, ? this table.");
  for (int i = 0; i < N_CANDIDATES; ++i) {
    Serial.printf("  %d %s%s\n", i, CANDIDATES[i].name, i == rtcIndex ? "   <= now" : "");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  if (rtcMagic != MAGIC) {
    rtcMagic = MAGIC;
    rtcIndex = 0;
    rtcHold = false;
    printTable();
  }
  if (rtcIndex < 0 || rtcIndex >= N_CANDIDATES) rtcIndex = 0;
  const Candidate &c = CANDIDATES[rtcIndex];

  const bool ok = lcd.apply(c);
  Serial.printf("cfg %d | %-26s | init=%d %dx%d\n", rtcIndex, c.name, (int)ok, lcd.width(),
                lcd.height());
  drawPattern(rtcIndex, c);
}

void loop() {
  static uint32_t shown = 0;
  if (shown == 0) shown = millis();

  while (Serial.available()) {
    const int ch = Serial.read();
    if (ch == '?') {
      printTable();
    } else if (ch == 'C') {
      rtcHold = false;
      Serial.println("cycling again");
      shown = millis();
    } else if (ch == 'n') {
      rtcIndex = (rtcIndex + 1) % N_CANDIDATES;
      rtcHold = false;
      delay(60);
      esp_restart();
    } else {
      int pick = -1;
      if (ch >= '0' && ch <= '9') pick = ch - '0';
      if (ch >= 'a' && ch <= 'f') pick = 10 + (ch - 'a');
      if (pick >= 0 && pick < N_CANDIDATES) {
        rtcIndex = pick;
        rtcHold = true;
        Serial.printf("holding %d\n", pick);
        delay(60);
        esp_restart();
      }
    }
  }

  if (!rtcHold && millis() - shown > HOLD_MS) {
    rtcIndex = (rtcIndex + 1) % N_CANDIDATES;
    delay(40);
    esp_restart();
  }
  delay(5);
}
