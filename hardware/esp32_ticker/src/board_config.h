// ESP32-S3-BOX-3 board wiring and the few knobs worth turning on stage.
//
// Everything that could be wrong about a board sits in this one file. If the screen is
// blank, mirrored, upside down or shows inverted colours, change a value here, reflash and
// look again. Nothing else in the firmware needs touching.

#pragma once

// ---------------------------------------------------------------- display

#define LCD_WIDTH 320
#define LCD_HEIGHT 240

// SPI bus and panel pins of the ESP32-S3-BOX-3 (same numbers as the Espressif BSP
// bsp/esp32_s3_box_3).
#define LCD_SPI_HOST SPI2_HOST
#define LCD_PIN_MOSI 6
#define LCD_PIN_SCLK 7
#define LCD_PIN_CS 5
#define LCD_PIN_DC 4
#define LCD_PIN_RST 48
#define LCD_PIN_BL 47
#define LCD_SPI_HZ 40000000

// Panel orientation and colour. The BOX-3 panel is an ILI9341 mounted rotated, driven with
// inverted colour and BGR order. If the picture is wrong, these four are the knobs:
#define LCD_ROTATION 1   // 0..3, quarter turns
#define LCD_INVERT true  // false if the colours look like a photo negative
#define LCD_RGB_ORDER false  // false = BGR (the BOX-3 default), true = RGB
#define LCD_OFFSET_X 0
#define LCD_OFFSET_Y 0

// ---------------------------------------------------------------- touch (optional)

// GT911 capacitive touch on the shared I2C bus. If it does not answer, the firmware simply
// runs without touch and says so once on the screen.
#define TOUCH_ENABLED 1
#define TOUCH_PIN_SDA 8
#define TOUCH_PIN_SCL 18
#define TOUCH_PIN_INT 3
#define TOUCH_PIN_RST -1  // shared with the LCD reset on this board, so do not drive it here
#define TOUCH_I2C_ADDR 0x5D
#define TOUCH_I2C_HZ 400000

// ---------------------------------------------------------------- protocol

// The server trims every line to this many letters, so the box never has to wrap.
#define VISIBLE_LETTERS 38
#define MAX_READS 3
#define MAX_BLOCKS 4       // strands kept on screen
#define JSON_CAPACITY 3072 // one line is well under 512 bytes, this is roomy on purpose
#define LINE_BUFFER 1024

// How long without a line before the box calls the link down, in milliseconds.
#define LINK_TIMEOUT_MS 4000
