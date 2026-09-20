# Marketing

Brand and launch materials for **Erbgut** (repo name: Adaptive DNA Codec), our HackMIT 2026 project.

> We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

Tagline: **Measure the rule. Keep what pays.**

## Files

| File | What it is | Use it for |
|---|---|---|
| `BRAND.md` | Brand guide: name candidates and recommendation, taglines, positioning, audiences, key messages, voice and tone, messaging hierarchy, color tokens, typography, iconography, logo rules, placeholder convention | The source of truth. Read it before writing anything public |
| `logo/` | The logo: the source artwork, the four SVGs built from it, and the script that builds them | Anything that needs the real asset |
| `logo.svg` | Full lockup for light backgrounds, a copy of `logo/erbgut-logo.svg` | Slides, Devpost header, README |
| `logo-dark.svg` | Full lockup for dark backgrounds | Dark slides, dark dashboard header |
| `logo-mark.svg` | One lens with its four lettered base pairs | Avatars, Devpost thumbnail, anything at 48 px and up |
| `logo/erbgut-mark-small.svg` | One lens, three capsules, no letters | Favicons, the site header, the ESP32 splash screen |
| `ONE_PAGER.md` | One-page overview for judges and sponsors | Print it, or paste it into a sponsor form |
| `PITCH.md` | The 3-minute pitch with timing marks, a 60-second version, and judge questions with honest answers | Rehearsal and Q&A prep |
| `DEVPOST.md` | Devpost submission text, section by section | Paste into the Devpost form |
| `deck/` | The animated web pitch deck with presenter mode (serve it, press `P`) | Presenting |
| `ticker/` | The ESP32 ticker's web front end and a recorded run | The hardware demo |
| `social/social-preview.html` | Source for the 1280x640 social card, rendered with headless Chrome | Re-render it whenever a number on it changes |
| `social/social-preview.png` | The rendered card | Open Graph image, X card, Devpost |

## How to use them

**The landing page** lives in [`site/`](../site/) and is what GitHub Pages publishes, together with `deck/`. It is not in this folder.

**Logos:** the artwork is a DNA double helix with four lettered base pairs and binary written through it, above the `erbgut` wordmark. Everything shipped is vector, rebuilt from `logo/erbgut-logo-source.jpeg` by `logo/build_logo.py`; the wordmark and the base letters are Instrument Sans converted to outlines, so the SVGs render identically through an `<img>` tag, offline and in print. The mark does not invert: only the binary and the wordmark change between the light and dark lockups. `BRAND.md` section 9 has the geometry and the colours.

```bash
uv run --with fonttools --with brotli python marketing/logo/build_logo.py
```

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
