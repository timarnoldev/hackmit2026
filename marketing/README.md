# Marketing

Brand and launch materials for **Erbgut** (repo name: Adaptive DNA Codec), our HackMIT 2026 project.

> We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

Tagline: **Measure the rule. Keep what pays.**

## Files

| File | What it is | Use it for |
|---|---|---|
| `BRAND.md` | Brand guide: name candidates and recommendation, taglines, positioning, audiences, key messages, voice and tone, messaging hierarchy, color tokens, typography, iconography, logo rules, placeholder convention | The source of truth. Read it before writing anything public |
| `logo.svg` | Full logo (mark plus wordmark) for light backgrounds | Slides, Devpost header, README |
| `logo-dark.svg` | Full logo for dark backgrounds | Dark slides, dark dashboard header |
| `logo-mark.svg` | Mark only | Favicon, avatars, Devpost thumbnail, ESP32 splash screen |
| `ONE_PAGER.md` | One-page overview for judges and sponsors | Print it, or paste it into a sponsor form |
| `PITCH.md` | 3-minute pitch with timing marks aligned to the demo script in `PROJECT.md`, a 60-second version, and judge questions with honest answers | Rehearsal and Q&A prep |
| `DECK_OUTLINE.md` | Seven slides plus one backup: title, text, visual and speaker notes | Building the deck |
| `DEVPOST.md` | Full Devpost submission text | Paste section by section into Devpost |
| `SOCIAL.md` | X thread, LinkedIn post, 75-second demo video voiceover | Posting after judging, recording the backup video |
| `index.html` | Single-file landing page with light and dark mode, responsive to phone width | Open locally, or host anywhere static |
| `social/social-preview.html` | Source for the 1280x640 social card, rendered with headless Chrome | Re-render it whenever a number on it changes |
| `social/social-preview.png` | The rendered card | Open Graph image, X card, Devpost |

## How to use them

**Open the landing page:** open `marketing/index.html` in a browser. It needs no build step and no JavaScript. Fonts load from Google Fonts; without a network it falls back to system fonts.

**Logos:** the mark is the deck's double helix and the wordmark is already converted to outlines from the deck's own Instrument Sans file, so the SVGs render identically through an `<img>` tag, offline and in print. `BRAND.md` section 9 has the geometry and the two color sets.

**Social card:** re-render it after editing `social/social-preview.html`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu \
  --hide-scrollbars --window-size=1280,640 \
  --screenshot=marketing/social/social-preview.png marketing/social/social-preview.html
```

**Filling in results:** final results don't exist yet. Every place that needs one has a placeholder in this exact form:

```
[RESULT: what goes here, with its conditions]
[TEAM: names and roles]
```

Each sentence around a placeholder is written so it works whether the result is a gain or "the hand rules are already near optimal". Before anything goes public:

```bash
grep -rn "\[RESULT\|\[TEAM" marketing/
```

must come back empty, or every remaining hit must be a deliberate "pending". Only use numbers that come from `dnacodec.evaluate` on held-out seeds, and have the ML verifier sign off on them. On the landing page, placeholders are styled amber with a dashed outline; remove the notice box in the results section once they're all filled.

**Numbers already used** (all measured, sources in `README.md` and `docs/`): simulator within about 3 points of real reads at every read count on held-out Microsoft data; shared Nanopore errors 45 to 66% predictable from the 5-letter context, hot-spot AUC 0.80 to 0.90; baseline 90.6% exact strands at 16 reads on real held-out data; 20 KB test file, 300 held-out trials; about 11,000 lines of Python, about 2,500 of them tests. The landing page and deck also show the held-out calibration table from `docs/ERRORS.md`. If any of these change, update them everywhere in this folder.

**Name check:** we did a quick web search only. Trademarks and domains are not verified.
