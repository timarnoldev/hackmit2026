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
//
// CONFIRMED ON THIS UNIT by the colour sweep in src/diag.cpp: configuration 0, that is
// ILI9342C with BGR order and inversion off, is the one where every named colour bar matched
// its label on a near black background. That is exactly what the library profile does, so
// USE_AUTODETECT 1 is correct and the hand written values below match it.
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

// The server trims every line to this many letters, so the box never has to wrap. 22 of the
// strand's 110 letters, at 12 px per cell, is what fits between the margins while the
// letters stay readable from two metres. The browser view shows a wider window.
#define VISIBLE_LETTERS 22
#define MAX_READ_LANES 3   // three read lanes above the decoded strand
#define MAX_BLOCKS 1       // the tape holds one strand at a time, plus one pending
#define LINE_BUFFER 1024

// How long without a line before the box calls the link down, in milliseconds.
#define LINK_TIMEOUT_MS 4000

// ---------------------------------------------------------------- layout

// Every edge keeps this much clear. Nothing is drawn outside it.
#define MARGIN 14

// Header rhythm. Every element gets at least 4 px of air, and 8 px or more between groups,
// so the stats never visually merge into the progress bar under them.
#define H_TITLE_Y 8        // wordmark row
#define H_VALUE_Y 27       // the three headline numbers
#define H_LABEL_Y 57       // their labels
#define H_SPARK_Y 73       // sparkline, 6 px tall
#define H_SPARK_H 6
#define H_BAR_Y 89         // progress bar, 3 px tall, 10 px clear above it
#define H_BAR_H 3
#define HEADER_H 98
#define TICKER_TOP 104
#define TICKER_BOTTOM 232  // 8 px of clear panel below

// The flowing tape. Letters run right to left through a fixed decode head, one column per
// strand position, with the reads above the decoded strand and everything column aligned.
#define CELL_W 20          // one column: one letter position of one strand
#define TAPE_X0 26         // the tape starts here; left of it is the lane icon gutter
#define ICON_CX 13         // centre of the icon column, inside the margin
#define HEAD_X 106         // the decode head, about a third in from the left
#define COLS 44            // ring buffer of columns, more than fit on screen

// Three read lanes and the hero line fit without shrinking any type: the tape used to stop
// at y 190 and leave 42 px of panel unused underneath.
#define LABEL_Y 106        // the strand label strip, scrolls with the tape
#define LANE1_Y 118
#define LANE2_Y 136
#define LANE3_Y 154
#define CONS_Y 178         // the decoded strand, the hero line
#define LANE_TOP 114       // where the head marker starts
#define LANE_BOTTOM 208    // where it ends, 24 px clear below

#define READ_SIZE 2        // 12 x 16 glyphs for the reads
#define CONS_SIZE 3        // 18 x 24 glyphs for the decoded strand

// ---------------------------------------------------------------- animation

#define TARGET_FPS 30
#define FRAME_MS (1000 / TARGET_FPS)

// How fast the tape flows, in letters per second. Slow on purpose: a judge has to be able to
// read a letter as it crosses the head. Touching the left or right third changes it.
#define FLOW_MIN 1.5f
#define FLOW_MAX 14.0f
#define FLOW_DEFAULT 5.0f
#define TWEEN 0.22f        // how fast a counter closes on its new value, per frame
#define SCROLL_TWEEN 0.28f // how fast the ticker settles after a new strand
#define FLASH_FRAMES 14    // a changed number stays lit this long
#define FIX_FRAMES 22      // a corrected letter decays over this many frames
#define SPARK_N 48         // strand outcomes in the sparkline

// ---------------------------------------------------------------- touch

// Pausing the demo on stage by accident is worse than not being able to pause at all, so the
// middle of the screen needs a deliberate long press. A short tap there does nothing. The
// GT911 also reports ghost touches right after boot, so the first second is ignored outright.
#define TOUCH_HOLD_MS 400    // how long the middle must be held before pause or resume fires
#define TOUCH_IGNORE_MS 1500 // no touch is believed before this many ms after boot
#define TOUCH_REPEAT_MS 500  // minimum gap between two actions
