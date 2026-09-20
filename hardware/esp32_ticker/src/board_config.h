// ESP32-S3-BOX-3 board wiring and the few knobs worth turning on stage.
//
// Everything that could be wrong about a board sits in this one file. If the screen is
// blank, mirrored, upside down or shows inverted colours, change a value here, reflash and
// look again. Nothing else in the firmware needs touching.

#pragma once

// ---------------------------------------------------------------- display

#define LCD_WIDTH 320
#define LCD_HEIGHT 240

// How the panel is set up. 1 = use LovyanGFX's own built-in profile for this exact board
// (LGFX_ESP32_S3_BOX_V3 in LGFX_AutoDetect_ESP32_all.hpp), which is the reference we trust:
// it names the panel, the pins, the backlight, the rotation and the touch controller.
// Set it to 0 to fall back to the hand written configuration below.
#define USE_AUTODETECT 1

// The hand written fallback. These are the same numbers LovyanGFX's BOX-3 profile uses, and
// the first bring-up got three of them wrong, so they are spelled out with the reason:
//
//   ILI9342C, not ILI9341. The BOX-3 panel is an ILI9342C, natively 320 x 240 (the ILI9341
//   is 240 x 320). Its init sequence is different and the memory layout is transposed.
//
//   No reset pin. GPIO 48 is NOT the panel reset on this board. LovyanGFX only sets it to
//   input with a pull-up. Driving it as a reset line is what left the screen white on the
//   first attempt, so LCD_PIN_RST is -1 and GPIO 48 is pulled up instead.
//
//   Three wire SPI. The panel shares one data line, so spi_3wire is true.
#define LCD_SPI_HOST SPI2_HOST
#define LCD_PIN_MOSI 6
#define LCD_PIN_SCLK 7
#define LCD_PIN_CS 5
#define LCD_PIN_DC 4
#define LCD_PIN_RST -1       // not a reset line on the BOX-3, see above
#define LCD_PIN_PULLUP 48    // held as input pull-up instead
#define LCD_PIN_BL 47
#define LCD_SPI_HZ 40000000
#define LCD_SPI_3WIRE true

// Panel orientation and colour. offset_rotation 1 plus rotation 1 is what puts the BOX-3
// panel the right way up in landscape.
#define LCD_ROTATION 1
#define LCD_OFFSET_ROTATION 1
#define LCD_INVERT false     // the ILI9342 profile does not invert on this board
#define LCD_RGB_ORDER false  // false = BGR
#define LCD_OFFSET_X 0
#define LCD_OFFSET_Y 0

// ---------------------------------------------------------------- touch (optional)

// GT911 capacitive touch on the shared I2C bus. With USE_AUTODETECT this comes from the
// library profile, which tries address 0x14 first and falls back to 0x5D. If the panel does
// not answer, the firmware simply runs without touch.
#define TOUCH_ENABLED 1
#define TOUCH_PIN_SDA 8
#define TOUCH_PIN_SCL 18
#define TOUCH_PIN_INT 3
#define TOUCH_PIN_RST -1
#define TOUCH_I2C_ADDR 0x14  // the BOX-3 answers here; 0x5D is the other GT911 address
#define TOUCH_I2C_HZ 400000
#define TOUCH_OFFSET_ROTATION 2
#define TOUCH_Y_MAX 279      // 239 plus the 40 pixel strip under the active area

// ---------------------------------------------------------------- protocol

// The server trims every line to this many letters, so the box never has to wrap.
#define VISIBLE_LETTERS 38
#define MAX_READS 3
#define MAX_BLOCKS 4       // strands kept on screen
#define JSON_CAPACITY 3072 // one line is well under 512 bytes, this is roomy on purpose
#define LINE_BUFFER 1024

// How long without a line before the box calls the link down, in milliseconds.
#define LINK_TIMEOUT_MS 4000
