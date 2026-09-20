# Erbgut live decode ticker, ESP32-S3-BOX-3

The box on the jury table. A statistics header above a tape of DNA flowing right to left
through a decode head, in the same palette as the deck.

**Both models run on the chip.** Plugged into any USB power supply, with no Mac in sight,
the box decodes real clusters with the classic majority vote, corrects them with the learned
polisher, and scores every strand with the risk model. When a Mac is attached it hands over
and displays what the Mac decodes instead. It never asks anyone to choose.

**No WiFi.** On purpose: a stage demo must not depend on hackathon WiFi. There is no
`secrets.h` and no captive portal. Everything comes over USB, when there is a USB host at all.

## What runs where

| | standalone | with a Mac attached |
|---|---|---|
| clusters | 300 real ones compiled in, looping | streamed live over USB |
| classic decode | on the chip, 170 ms per strand | on the Mac |
| learned polisher | on the chip, int8, 5.0 s per strand | on the Mac, the multi draft variant |
| risk model | on the chip, float32 | not sent |
| header caption | "polish, on device" | "polish on the Mac" |

The caption only ever names what actually produced the strand on screen. If the weights do
not fit, or the buffers cannot be allocated, the firmware falls back to the classic decoder
and says so.

### How the models got onto the chip

| | parameters | on device | verified against Python |
|---|---|---|---|
| polisher | 0.8M | int8 weights, int16 activations, int32 accumulate | 96.7% same strand, 229 exact against 230 for the float model |
| risk model | 63k | float32, BatchNorm folded in | 1.2e-07 largest difference, rank correlation 1.000 |

All three figures come from `tools/verify_polish.py --clusters 300` and
`tools/verify_risk.py --strands 400` on this branch, which are deterministic, so they can be
reproduced exactly. The polisher's 229 against 230 is the point: on 300 real clusters int8
costs one strand, inside the noise of a single cluster draw, against 211 for the classic
majority vote.

The polisher is quantized because it is thirteen times larger and its convolutions are the
whole run time. The risk model is not, because at 6.4M multiply accumulates quantizing would
save flash that is not scarce and cost accuracy that is.

Regenerating either one:

```bash
# the frozen run the box plays when it is alone
uv run python scripts/ticker_server.py --dump-box hardware/esp32_ticker/src/box_data.h

# the polisher, quantized, plus the comparison against the float model
uv run python hardware/esp32_ticker/tools/quantize_polish.py --clusters 250 \
    --write ../src/box_polish_weights.h

# the risk model
uv run python hardware/esp32_ticker/tools/export_risk.py --write ../src/box_risk_weights.h
```

Checking them, without flashing anything, because both compile for the host too:

```bash
uv run python hardware/esp32_ticker/tools/verify_decoder.py --clusters 600
uv run python hardware/esp32_ticker/tools/verify_polish.py --clusters 300
uv run python hardware/esp32_ticker/tools/verify_risk.py --strands 400
```

## What is on the screen

320 x 240, with 14 px clear on every side.

```
 +--------------------------------------------------------------+
 |  Erbgut            polish  tape 1 in 20        (o) live       |  title row
 |                                                               |
 |   1396            77%             806                         |  three headline
 |   strands         exact           fixes                       |  numbers
 |                                                               |
 |  ####  ##  ####  #  ####  ###  ##########                     |  sparkline, 48 outcomes
 |  ===========================================------------      |  progress through the file
 +--------------------------------------------------------------+
 |            142 v                                              |  strand label, scrolls
 |  ~~~   G  C  A  A  T  T | G  C  C  G  A  T  G  A  T  G        |  read 1
 |  ~~~   G  C  A  A  T  C | G  C  C  G  A  T  G  A  T  G        |  read 2
 |  ~~~   G  C  A  A  T  T | G  C  C  G  A  T  G  A  T  G        |  read 3
 |                         |                                     |
 |  |||  G  C  A  A  T  T  T  G  C  C  G  A  T  G  A  T          |  the decoded strand
 |                         ^                                     |
 +--------------------------------------------------------------+
                      the decode head
```

The tape flows right to left at 3.5 letters per second by default, which is about 6.6
seconds per strand and keeps the tape behind the polisher's 5 seconds of inference. A column is undecided
until it reaches the head: it shows the letter the classic majority vote produced. As it
crosses, the read errors light up and a position the model corrected flashes cost for the
wrong letter, then gain as the corrected letter takes its place, then dissolves into the
strand. The trail behind the head keeps its final state and fades out to the left. A divider,
the strand number and a tick or a cross mark every strand boundary.

The left gutter holds a lane icon per row, drawn in code: a small noisy wave for each read,
and the Erbgut mark in miniature, bases standing on a strand, for the decoded line.

**The tape shows a sample.** It runs far slower than the decode, so only the freshest strand
waits its turn. The header counts every strand and the caption says "tape 1 in N", so the
difference is stated rather than implied. Nothing on the panel is animated that did not come
out of a real decode.

Colours are the deck's tokens (`marketing/deck/deck.css`), so the table and the slides match:
cost for an error, gain for a correction, ink and muted for neutral text, audit for an extra
letter and the decode head, tbd for a strand that came back with no reads.

Every strand's label carries the risk the model gave it, coloured from gain through amber to
cost. The number is always printed, so colour never carries the meaning alone.

**Measured:** 27 fps drawing the live tape, held while the models run, because decoding
happens on the second core. 170 ms for the classic decode, 5.0 s for the polisher, well
under a millisecond for the risk model. Flash 29% of the app partition.

## Touch

| Where | What |
|---|---|
| left third, tap | the tape flows slower, down to 1.5 letters per second |
| right third, tap | the tape flows faster, up to 14 |
| middle, **hold 400 ms** | pause or resume the decode on the Mac |
| middle, short tap | nothing, on purpose |

Pausing a demo by accident is worse than not being able to pause, so the middle needs a
deliberate press, touches in the first 1500 ms after boot are ignored, and every touch
prints its coordinates and what it did over USB.

---

## What you need

| Thing | State |
|---|---|
| PlatformIO CLI | `uv tool install platformio`, or `pip install platformio` |
| Toolchain | downloaded automatically on the first `pio run`, about 3 minutes |
| Cable | the box's USB-C port, which is its native USB Serial/JTAG |
| Port | `/dev/cu.usbmodem1101` on this Mac (Espressif "USB JTAG/serial debug unit", VID 0x303a PID 0x1001) |

No ESP-IDF install is needed. The firmware is Arduino plus LovyanGFX plus ArduinoJson, all
pulled in by `platformio.ini`.

## Build and flash

```bash
cd hardware/esp32_ticker
pio run                                            # build
pio run -t upload --upload-port /dev/cu.usbmodem1101   # build and flash
```

The upload resets the box itself, so no buttons need pressing. If the port is busy, see
"one port, one owner" below.

## Run the demo

```bash
# from the repository root, with the box plugged in
uv run python scripts/ticker_server.py --serial-port /dev/cu.usbmodem1101

# browser view only, box not needed
uv run python scripts/ticker_server.py --no-serial --open
```

The server prints the browser view URL (http://127.0.0.1:8777/ by default) and the serial
port it found. `http://127.0.0.1:8777/api/state` shows whether the box is connected and
alive:

```json
"serial": {"connected": true, "port": "/dev/cu.usbmodem1101", "box_alive": true, "beats": 49}
```

`box_alive` comes from the box's own heartbeat, one `.` per second up the same cable. If it
is `false` while `connected` is `true`, the cable is fine but the firmware is not running.

## One port, one owner

`pio device monitor` holds `/dev/cu.usbmodem1101` open, and so does the server. Only one of
them can have it. Before starting the server:

```bash
# stop any monitor first (ctrl-c in its window), then
lsof /dev/cu.usbmodem1101     # should print nothing
```

If the server cannot open the port it says so, keeps running, and retries every 1.5 seconds.
The browser view is never affected. Unplugging the box mid demo is safe: the server keeps
decoding, and when the port comes back it sends a handshake and the box catches up.

Flashing while the server is running also works if you stop the server first; `pio run -t
upload` needs the port exclusively.

## Protocol

Line delimited JSON, one compact object per line, UTF-8, `\n` terminated. Lines stay under
512 bytes (measured maximum on a live run: 383 bytes, and `tests/test_ticker.py` asserts it).
The baud rate is cosmetic on native USB; the server still sets 921600 so a UART bridge would
work unchanged.

Mac to box:

| Line | Meaning |
|---|---|
| `{"t":"hi","v":1}` | the server just opened the port |
| `{"t":"hello","dec":...,"layer":...,"ch":...,"rps":...}` | which decoder and channel are running |
| `{"t":"pass","n":229,"file":"erbgut-mark.png","bytes":2723,"pass":1}` | a new pass over the file started |
| `{"t":"s","i":..,"n":..,"nr":..,"ok":..,"d":..,"r":[{"s":..,"m":..}],"c":..,"f":..,"st":{..}}` | one strand |
| `{"t":"file","ok":true,...}` | the pass finished, the file came back or did not |
| `{"t":"state","paused":..,"rate":..}` | pause and speed changed |

`m` and `f` are mark strings: a position then a kind, for example `3x11e25m`.

| Kind | Where | Meaning |
|---|---|---|
| `x` | a read | wrong letter, drawn on cost magenta |
| `e` | a read | extra letter the strand does not have, drawn on audit indigo |
| `m` | a read | a letter the read is missing, a dashed edge in front of the position |
| `s` `i` `d` | the strand | the model substituted, inserted or deleted here, drawn in gain teal |

Box to Mac, single characters:

| Byte | Meaning |
|---|---|
| `.` | heartbeat, once a second |
| `?` | please resend the hello and the current pass |
| `p` `r` | pause, resume |
| `+` `-` | faster, slower |
| `B` `D` | sent once at boot: the sketch started, the display came up |

Touch, if the GT911 panel answers: tap the left third for slower, the middle for pause and
resume, the right third for faster.

## The white screen, and what it actually was

First bring-up flashed fine, the USB link worked, the backlight was on, and the panel stayed
white. Three things were wrong, all of them in the hand written panel configuration:

1. **Wrong controller.** The BOX-3 panel is an **ILI9342C**, not an ILI9341. It is natively
   320 x 240, where the ILI9341 is 240 x 320, and its init sequence is different.
2. **GPIO 48 is not the reset line.** It was being driven as one. On this board it is only
   set to input with a pull-up. Driving it is what kept the panel dark.
3. **Three wire SPI.** The panel shares one data line; `spi_3wire` has to be true.

The fix is to stop hand writing it. LovyanGFX ships a profile for this exact board, and the
firmware now uses it:

```cpp
#define LGFX_ESP32_S3_BOX_V3
#define LGFX_AUTODETECT
#include <LovyanGFX.hpp>
#include <LGFX_AUTODETECT.hpp>
static LGFX lcd;
```

That one profile supplies the controller, the pins, the 40 MHz three wire SPI bus on SPI2,
the GPIO 47 backlight, `offset_rotation = 1`, and the GT911 touch panel at address 0x14 with
a 0x5D fallback. It is defined in the library's `LGFX_AutoDetect_ESP32_all.hpp` as
`_detector_ESP32_S3_BOX_V3_t`, which is the reference to check against if anything else ever
looks wrong.

`USE_AUTODETECT` in `src/board_config.h` switches back to the hand written path, which now
carries the same corrected numbers, if a future board ever needs different ones.

## If the screen still looks wrong

| Symptom | Knob in `src/board_config.h` |
|---|---|
| Colours look like a photo negative | `LCD_INVERT` (needs `USE_AUTODETECT 0`) |
| Red and blue are swapped | `LCD_RGB_ORDER` (needs `USE_AUTODETECT 0`) |
| Picture rotated or mirrored | `LCD_ROTATION`, values 0 to 3 |
| Blank screen but `box_alive` is true | `USE_AUTODETECT`, then the bring-up sweep below |
| Touch does nothing | `TOUCH_ENABLED 0`, or `TOUCH_I2C_ADDR` 0x5D |

## The bring-up sweep

`src/diag.cpp` is a separate firmware that walks a list of panel configurations, one per
boot, five seconds each, drawing a numbered colour pattern and printing what it is trying
over USB. It exists because a white panel gives you nothing to go on, and it is the fastest
way to find out which knob matters on a board nobody has brought up before.

```bash
pio run -e diag -t upload --upload-port /dev/cu.usbmodem1101   # sweep
pio device monitor -e diag                                     # watch it
pio run -t upload --upload-port /dev/cu.usbmodem1101           # back to the ticker
```

Keys over serial: `0`..`9` `a`..`f` hold one configuration, `C` resume cycling, `n` next now,
`?` print the table.

## What is tested and what is not

Tested on this Mac against the box at `/dev/cu.usbmodem1101`:

- the firmware builds and flashes (`pio run -t upload`, 380 KB, 5.8% of flash, 7.2% of RAM),
- the sketch runs and the display driver comes up (`B` and `D` arrive at boot),
- the box receives and parses JSON lines and answers a `hi` with `?`,
- the heartbeat arrives once a second while the server streams,
- the server keeps decoding when the box stops answering: an upload attempted while the
  server held the port failed (as it should), `box_alive` went to false within three
  seconds, `connected` stayed true, and the browser view carried on without a hiccup.

Not exercised with a real cable pull, only with the port being taken away, but that is the
same code path: any read or write error closes the port and the sender retries every 1.5
seconds.

After the fix the firmware reports `lcd init=1 320x240` over USB, which the first version
never did correctly because it was configured as a 240 x 320 ILI9341.

**Still to confirm with eyes on the panel:** the layout, the rotation and the touch mapping.

The browser view is the stage fallback and does not depend on any of this.
