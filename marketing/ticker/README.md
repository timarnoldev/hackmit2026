# Live decode ticker, browser view

`index.html` is the stage view: a statistics header and, under it, DNA strands scrolling
through with every fix visibly applied. Dark, full screen, Erbgut palette and fonts, built to
be read from the back of a room.

It is the fallback for the pitch. The ESP32-S3-BOX-3 on the jury table
(`hardware/esp32_ticker/`) shows the same two zones from the same event stream, but the
browser view needs nothing except this Mac.

## Run it

```bash
uv run python scripts/ticker_server.py --no-serial --open
```

That serves the page at http://127.0.0.1:8777/ and starts a real decode: encode a file with
`dnacodec.encoder`, push it through `dnacodec.simulator`, decode cluster by cluster with
`PolishDecoder` (or the classic majority vote when no checkpoint is there, which the page
then says). Press `f` for full screen.

Useful switches:

```bash
--channel illumina_standard     # any profile in profiles/
--reads 6                       # reads per strand
--rate 2                        # strands per second on screen
--file path/to/anything         # or the built-ins: message, image
--replay marketing/ticker/recorded-run.jsonl    # no compute at all
```

Keyboard: space pauses and resumes, `+` and `-` change the speed, `f` goes full screen. The
same controls sit in the footer and fade out when the mouse stops.

The page can also point at a server on another machine:
`index.html?server=http://192.168.1.20:8777`.

## The stage fallback of the fallback

`recorded-run.jsonl` is a real run, recorded event by event. Replaying it needs no decoder,
no torch and no GPU:

```bash
uv run python scripts/ticker_server.py --replay marketing/ticker/recorded-run.jsonl --no-serial --open
```

Record a fresh one with `--dump out.jsonl --dump-strands 260`, or record while presenting
with `--record out.jsonl`.

## What the colours mean

| Colour | Where | Meaning |
|---|---|---|
| cost magenta | a read | the read has the wrong letter here |
| audit indigo | a read | the read has an extra letter the strand does not have |
| dashed magenta | a read | the read is missing a letter in front of this position |
| gain teal | the strand | the model corrected this position |
| left edge teal, magenta, amber | a block | the strand came back exactly, came back wrong, or had no reads |

Same language as slide 3 of the deck.

## Honesty

The page draws only what the server sends, and the server streams a real decode of real
simulated reads. Two things are done for the eye, and both are on screen in the footer:

- the events are **paced** (default 4 strands per second) because the decode is much faster
  than a person can read; the pacing changes nothing about what was decoded,
- each strand is **trimmed** to 3 of its reads and the first 38 of 110 letters, because that
  is what fits the box; correctness is still judged over the whole strand.
