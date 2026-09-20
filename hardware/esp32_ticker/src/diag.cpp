// Display bring-up diagnostic for the ESP32-S3-BOX-3.
//
// Why this exists: the ticker firmware flashed and its USB link worked, but the panel stayed
// white with the backlight on. A white panel with a live serial link means the panel is not
// receiving SPI at all, so the question is which of panel driver, pins, bus or reset is
// wrong. Guessing one knob per reflash is slow, so this sketch sweeps the candidates by
// itself and only needs a pair of eyes.
//
// How it works: one configuration per boot. The index lives in RTC memory, so after five
// seconds the board restarts itself into the next one. Every configuration draws the same
// unmistakable pattern, with its own number in the middle, and prints a line over USB.
//
// Build and flash:
//   pio run -e diag -t upload --upload-port /dev/cu.usbmodem1101
// Watch it:
//   pio device monitor -e diag            (or any reader on /dev/cu.usbmodem1101)
//
// Serial keys while it runs:
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

enum PanelKind { P_ILI9341, P_ILI9342, P_ST7789 };

struct Candidate {
  const char *name;
  PanelKind panel;
  int8_t cs, dc, mosi, sclk, rst;
  uint32_t freq;
  bool invert;
  bool rgb_order;  // false = BGR
  uint8_t rotation;
  uint8_t host;    // 2 = SPI2_HOST, 3 = SPI3_HOST
  bool via_sprite; // draw through a PSRAM sprite instead of straight to the panel
  uint8_t spi_mode;
  bool manual_reset;  // pulse the reset pin by hand, slowly, before the driver runs
};

// Candidate 0 is exactly what the ticker firmware does, so if it draws, the bug was the
// sprite and not the panel. The rest change one thing at a time.
static const Candidate CANDIDATES[] = {
    {"ILI9341 base, as the ticker", P_ILI9341, 5, 4, 6, 7, 48, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9341, invert off", P_ILI9341, 5, 4, 6, 7, 48, 40000000, false, false, 1, 2, false, 0, false},
    {"ILI9341, RGB order", P_ILI9341, 5, 4, 6, 7, 48, 40000000, true, true, 1, 2, false, 0, false},
    {"ILI9341, no reset pin", P_ILI9341, 5, 4, 6, 7, -1, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9341, slow bus 10 MHz", P_ILI9341, 5, 4, 6, 7, 48, 10000000, true, false, 1, 2, false, 0, false},
    {"ILI9342 (the other BOX panel)", P_ILI9342, 5, 4, 6, 7, 48, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9342, invert off", P_ILI9342, 5, 4, 6, 7, 48, 40000000, false, false, 1, 2, false, 0, false},
    {"ST7789", P_ST7789, 5, 4, 6, 7, 48, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9341, MOSI and SCLK swapped", P_ILI9341, 5, 4, 7, 6, 48, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9341, CS and DC swapped", P_ILI9341, 4, 5, 6, 7, 48, 40000000, true, false, 1, 2, false, 0, false},
    {"ILI9341 on SPI3_HOST", P_ILI9341, 5, 4, 6, 7, 48, 40000000, true, false, 1, 3, false, 0, false},
    {"ILI9341 base, drawn via a PSRAM sprite", P_ILI9341, 5, 4, 6, 7, 48, 40000000, true, false, 1, 2, true, 0, false},
    {"ILI9341, SPI mode 3", P_ILI9341, 5, 4, 6, 7, 48, 40000000, true, false, 1, 2, false, 3, false},
    {"ILI9341, 2 MHz and a slow manual reset", P_ILI9341, 5, 4, 6, 7, 48, 2000000, true, false, 1, 2, false, 0, true},
};
static const int N_CANDIDATES = sizeof(CANDIDATES) / sizeof(CANDIDATES[0]);

#define BACKLIGHT_PIN 47
#define HOLD_MS 5000

// ---------------------------------------------------------------- one configurable device

class DiagDisplay : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9341 _ili9341;
  lgfx::Panel_ILI9342 _ili9342;
  lgfx::Panel_ST7789 _st7789;
  lgfx::Bus_SPI _bus;
  lgfx::Light_PWM _light;

 public:
  bool apply(const Candidate &c) {
    if (c.manual_reset && c.rst >= 0) {  // some panels want a much longer reset than the driver gives
      pinMode(c.rst, OUTPUT);
      digitalWrite(c.rst, HIGH);
      delay(20);
      digitalWrite(c.rst, LOW);
      delay(50);
      digitalWrite(c.rst, HIGH);
      delay(150);
    }
    lgfx::Panel_LCD *panel = nullptr;
    switch (c.panel) {
      case P_ILI9342: panel = &_ili9342; break;
      case P_ST7789: panel = &_st7789; break;
      default: panel = &_ili9341; break;
    }
    {
      auto cfg = _bus.config();
      cfg.spi_host = (c.host == 3) ? SPI3_HOST : SPI2_HOST;
      cfg.spi_mode = c.spi_mode;
      cfg.freq_write = c.freq;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = false;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = c.sclk;
      cfg.pin_mosi = c.mosi;
      cfg.pin_miso = -1;  // the BOX-3 does not wire the panel's MISO, so nothing is readable
      cfg.pin_dc = c.dc;
      _bus.config(cfg);
      panel->setBus(&_bus);
    }
    {
      auto cfg = panel->config();
      cfg.pin_cs = c.cs;
      cfg.pin_rst = c.rst;
      cfg.pin_busy = -1;
      cfg.panel_width = 240;
      cfg.panel_height = 320;
      cfg.memory_width = 240;
      cfg.memory_height = 320;
      cfg.offset_x = 0;
      cfg.offset_y = 0;
      cfg.offset_rotation = 0;
      cfg.readable = false;
      cfg.invert = c.invert;
      cfg.rgb_order = c.rgb_order;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      panel->config(cfg);
    }
    {
      auto cfg = _light.config();
      cfg.pin_bl = BACKLIGHT_PIN;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      _light.config(cfg);
      panel->setLight(&_light);
    }
    setPanel(panel);
    const bool ok = init();
    setRotation(c.rotation);
    setBrightness(255);
    return ok;
  }
};

static DiagDisplay lcd;

// ---------------------------------------------------------------- the test pattern

// Four quadrants, a border, the configuration number in the middle and its name under it.
// Anything visible at all answers the only question that matters: does the panel take SPI.
template <typename Target>
static void drawPattern(Target &g, int w, int h, int index, const Candidate &c) {
  const int hw = w / 2, hh = h / 2;
  g.fillRect(0, 0, hw, hh, g.color888(255, 0, 0));      // red, top left
  g.fillRect(hw, 0, w - hw, hh, g.color888(0, 255, 0)); // green, top right
  g.fillRect(0, hh, hw, h - hh, g.color888(0, 0, 255)); // blue, bottom left
  g.fillRect(hw, hh, w - hw, h - hh, g.color888(255, 255, 255));  // white, bottom right

  g.drawRect(0, 0, w, h, g.color888(255, 255, 0));
  g.drawRect(1, 1, w - 2, h - 2, g.color888(255, 255, 0));

  g.fillRect(hw - 74, hh - 34, 148, 68, g.color888(0, 0, 0));
  g.setTextColor(g.color888(255, 255, 255), g.color888(0, 0, 0));
  g.setTextSize(6);
  g.setCursor(hw - 54, hh - 22);
  g.printf("%02d", index);
  g.setTextSize(1);
  g.setCursor(6, h - 26);
  g.setTextColor(g.color888(0, 0, 0), g.color888(255, 255, 255));
  g.printf("%d/%d %s", index, N_CANDIDATES - 1, c.name);
  g.setCursor(6, h - 14);
  g.printf("R%d G%d B%d W  inv=%d rgb=%d rot=%d %luMHz", 1, 2, 3, c.invert, c.rgb_order,
           c.rotation, (unsigned long)(c.freq / 1000000));
}

// ---------------------------------------------------------------- state across a restart

// RTC_NOINIT_ATTR, not RTC_DATA_ATTR: .rtc.data is reloaded from the image on every boot,
// including after esp_restart(), so a counter kept there never advances. .rtc_noinit is
// left alone, which is what a sweep across reboots needs. No initialisers, by definition.
RTC_NOINIT_ATTR static uint32_t rtcMagic;
RTC_NOINIT_ATTR static int rtcIndex;
RTC_NOINIT_ATTR static bool rtcHold;
static const uint32_t MAGIC = 0xE26B0704;

static void printTable() {
  Serial.println();
  Serial.println("Erbgut display bring-up. One configuration per boot, five seconds each.");
  Serial.println("Keys: 0..9 a..f hold one, C resume cycling, n next now, ? this table.");
  for (int i = 0; i < N_CANDIDATES; ++i) {
    Serial.printf("  %2d %s%s\n", i, CANDIDATES[i].name, i == rtcIndex ? "   <= now" : "");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);  // let the host enumerate the CDC endpoint before the first line

  if (rtcMagic != MAGIC) {
    rtcMagic = MAGIC;
    rtcIndex = 0;
    rtcHold = false;
    printTable();
  }
  if (rtcIndex < 0 || rtcIndex >= N_CANDIDATES) rtcIndex = 0;
  const Candidate &c = CANDIDATES[rtcIndex];

  const bool ok = lcd.apply(c);
  const int w = lcd.width(), h = lcd.height();

  Serial.printf("cfg %2d | %-38s | init=%d w=%d h=%d | cs=%d dc=%d mosi=%d sclk=%d rst=%d "
                "host=SPI%d %luMHz inv=%d rgb=%d rot=%d sprite=%d\n",
                rtcIndex, c.name, (int)ok, w, h, c.cs, c.dc, c.mosi, c.sclk, c.rst, c.host,
                (unsigned long)(c.freq / 1000000), (int)c.invert, (int)c.rgb_order,
                (int)c.rotation, (int)c.via_sprite);
  Serial.printf("        psram=%u free=%u  mode=%d manual_reset=%d\n", (unsigned)ESP.getPsramSize(),
                (unsigned)ESP.getFreePsram(), (int)c.spi_mode, (int)c.manual_reset);

  if (c.via_sprite) {
    LGFX_Sprite sprite(&lcd);
    sprite.setPsram(true);
    sprite.setColorDepth(16);
    void *buf = sprite.createSprite(w, h);
    Serial.printf("        sprite %dx%d allocated=%d psram_free=%u\n", w, h, buf != nullptr,
                  (unsigned)ESP.getFreePsram());
    if (buf) {
      drawPattern(sprite, w, h, rtcIndex, c);
      sprite.pushSprite(0, 0);
      sprite.deleteSprite();
    } else {
      drawPattern(lcd, w, h, rtcIndex, c);  // fall back so the screen is never left blank
    }
  } else {
    drawPattern(lcd, w, h, rtcIndex, c);
  }
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
      Serial.printf("jumping to %d\n", rtcIndex);
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
