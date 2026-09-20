# Marketing

Brand and launch materials for **Erbgut** (repo name: Adaptive DNA Codec), our HackMIT 2026 project.

> We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

Tagline: **Measure the rule. Keep what pays.**

## Files

| File | What it is | Use it for |
|---|---|---|
| `BRAND.md` | Brand guide: name, taglines, positioning, audiences, key messages, voice and tone, messaging hierarchy, color tokens, typography, iconography, logo rules | The source of truth. Read it before writing anything public |
| `logo.svg` | Full logo (mark plus wordmark) for light backgrounds | Slides, Devpost header, README |
| `logo-dark.svg` | Full logo for dark backgrounds | Dark slides, dark dashboard header |
| `logo-mark.svg` | Mark only | Favicon, avatars, Devpost thumbnail, ESP32 splash screen |
| `ONE_PAGER.md` | One-page overview for judges and sponsors | Print it, or paste it into a sponsor form |
| `PITCH.md` | The 3-minute pitch with timing marks, a 60-second version, and judge questions with honest answers | Rehearsal and Q&A prep |
| `DEVPOST.md` | Devpost submission text, section by section | Paste into the Devpost form |
| `deck/` | The animated web pitch deck with presenter mode (serve it, press `P`) | Presenting |
| `ticker/` | The ESP32 ticker's web front end and a recorded run | The hardware demo |
| `social/social-preview.html` | Source for the 1280x640 social card, rendered with headless Chrome | Re-render it whenever a number on it changes |
| `social/social-preview.png` | The rendered card | Open Graph image, X card, Devpost |

## How to use them

**The landing page** lives in [`site/`](../site/) and is what GitHub Pages publishes, together with `deck/`. It is not in this folder.

**Logos:** the mark is the deck's double helix and the wordmark is already converted to outlines from the deck's own Instrument Sans file, so the SVGs render identically through an `<img>` tag, offline and in print. `BRAND.md` section 8 has the geometry and the two color sets.

**Social card:** re-render it after editing `social/social-preview.html`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --hide-scrollbars --window-size=1280,640 \
  --screenshot=marketing/social/social-preview.png marketing/social/social-preview.html
```

## Numbers in public material

Every number in this folder comes from [`docs/NUMBERS.md`](../docs/NUMBERS.md), which is the
registry of what we measured and how. Nothing goes on a slide, a post or a page unless it is in
there with its conditions. If a number in the registry changes, it changes everywhere in this
folder in the same pass.

`[TEAM: ...]` is the one placeholder still open. It marks the spots waiting on real names and
links:

```bash
grep -rn "\[TEAM" marketing/
```

**Name check:** we did a quick web search only. Trademarks and domains are not verified.
