# Erbgut live decode ticker, ESP32-S3-BOX-3

The box on the jury table. It shows a statistics header and, under it, DNA strands scrolling
through with every fix visibly applied, in the same visual language as the deck.

The box is a thin display. It decodes nothing and invents nothing. `scripts/ticker_server.py`
on the Mac runs the real encode, channel simulation and decode, and pushes one compact JSON
object per line down the USB cable.

**No WiFi.** On purpose: a stage demo must not depend on hackathon WiFi. There is no
`secrets.h` and no captive portal. Everything comes over USB.

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

## If the screen looks wrong

Every board guess lives in `src/board_config.h`. Change one value, reflash, look again.

| Symptom | Knob |
|---|---|
| Colours look like a photo negative | `LCD_INVERT` |
| Red and blue are swapped | `LCD_RGB_ORDER` |
| Picture rotated or mirrored | `LCD_ROTATION`, values 0 to 3 |
| Picture shifted by a few pixels | `LCD_OFFSET_X`, `LCD_OFFSET_Y` |
| Blank screen but `box_alive` is true | `LCD_PIN_BL`, then the SPI pins |
| Touch does nothing | set `TOUCH_ENABLED` to 0, or try `TOUCH_I2C_ADDR` 0x14 |

The pin numbers match the Espressif BSP for `esp32_s3_box_3`. The panel is driven as an
ILI9341 with inverted colour and BGR order.

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

**Not verified:** what the screen actually shows. Nobody looked at the panel while this was
written, so the layout, the orientation, the colour order and the touch mapping are
unconfirmed. The knobs above exist for exactly that reason. Plug the box in, run the server,
look at it, and if something is off it is almost certainly one line in `board_config.h`.

The browser view is the stage fallback and does not depend on any of this.
