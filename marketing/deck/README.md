# Erbgut pitch deck

An animated web deck for the 3-minute HackMIT pitch, with a presenter view. Plain HTML, CSS and JS: no build step, no external scripts, fonts bundled, works offline.

## Present it

```bash
marketing/deck/serve.sh          # prints http://localhost:8000/ (first free port from 8000)
```

1. Open the printed URL in Chrome on the laptop. Drag that window to the projector and press **F** for fullscreen.
2. Press **P**. A presenter window opens; keep it on the laptop screen. It shows the current slide live, the next slide, the speaker notes, a timer, a pacing bar against the 3-minute plan and the clock.
3. Advance with the clicker, the arrow keys or space, in either window. Both windows move together.

No second screen, or mirrored displays? Press **S** for the presenter layout in the same window (or open `?presenter`), and **S** again to go back.

**Why serve it:** presenter sync is guaranteed over `http://localhost`. Opening `index.html` directly as a file also works in Chrome (tested: fonts, sync between tabs, the P popup and the next-slide preview), but some browsers isolate `file://` pages, so use `serve.sh` on stage. It needs only `python3`.

Before you go on stage: open the deck once, check no **SAMPLE DATA** badge and no red banner, press **Shift+Z** to reset the timer. The timer starts itself on your first click.

## Keys

| Key | Action |
|---|---|
| Right, Down, Space, PageDown, Enter, click | Next build, then next slide |
| Left, Up, PageUp, Backspace, Shift+click | Previous |
| Home / End | First / last slide |
| a number, then Enter | Go to that slide |
| F (or F5) | Fullscreen |
| B or . | Black screen (the presenter view shows a flag and keeps the slide) |
| O | Overview of all slides; arrows and Enter to pick one |
| P | Open the presenter window |
| S | Presenter layout in this window |
| Z / Shift+Z | Timer start or pause / reset (also buttons in the presenter view) |
| + / - | Notes text size |
| M | Motion on or off. Off shortens every animation to a fade. `prefers-reduced-motion` turns it off by default |
| ? | Help |

Clickers send PageDown and PageUp, which step through the builds of a slide before moving on. The URL hash (`#/5/2` = slide 5, build 2) keeps your place on reload. Motion, black screen and timer are shared between windows. The deck is light, always: there is no theme to get wrong on stage.

## Slides

Main flow, 3:00 (targets per slide are in `index.html` as `data-plan`, and drive the pacing bar):

| # | Slide | Target | Builds |
|---|---|---|---|
| 1 | Title | 0:00 to 0:08 | animated strand |
| 2 | The problem | 0:08 to 0:28 | rulebook stamped onto Nanopore and Illumina |
| 3 | How DNA storage works | 0:28 to 0:52 | bits to letters, synthesis, noisy reads, the decoder chain, checksum, Fountain code |
| 4 | Where the rules come in | 0:52 to 1:05 | 8 candidates, rules reject, scorer keeps one |
| 5 | Tier 1, the rule audit | 1:05 to 1:32 | switches flip, verdict table fills per channel |
| 6 | Tier 2, learning from failures | 1:32 to 1:52 | hot spots light up, measured evidence, encoder steers around |
| 7 | The loop and the Pareto plot | 1:52 to 2:14 | cycle, default point, tuning rounds, matched-density marker |
| 8 | Evidence: ablation and crossover | 2:14 to 2:32 | ladder A to E, tier brackets, crossover matrix |
| 9 | Evidence: simulator and firewall | 2:32 to 2:46 | real vs simulated curve, table, firewall steps |
| 10 | Results and close | 2:46 to 3:00 | headline results, tagline, team, repo |

Appendix (after the end, not timed): **A0 the architecture**, one screen with the whole system and four builds (storage path, the learned polisher, the risk model and the loop, evaluation and the firewall). This is the slide to jump to when a judge asks what you actually built, and its notes carry a 40 to 60 second walkthrough. Then A1 the channel simulator, A2 the two learned models, A3 the GX10 setup, A4 to A6 judge questions.

Speaker notes live in each slide's `<aside class="notes">`. `click` markers in the presenter view show where each build goes; the next one is highlighted.

## Results: one file

Final results don't exist yet. Every result on every slide, chart and speaker note comes from **`results.js`**. Anything `null` renders as an amber dashed `[RESULT: ...]` placeholder, and the charts show a "results pending" state, so a missing number can't be mistaken for a real one.

**Fill it from a finished run:**

```bash
python3 marketing/deck/tools/results_from_run.py <run_id>      # reads results/<run_id>/
```

It fills the rule audit, Pareto points, ablation ladder, crossover matrix and firewall from `summary.json` and the per-channel run files, keeps the human-written fields, refuses mock runs, and lists what is still pending. Then check the numbers and fill the rest by hand:

- `meta.team`: names and roles (title and closing slides)
- `headline.imageDemo`: one sentence about the image round trip at 6 reads per strand
- `crossover.summary`: optional one-line outcome (computed automatically when each codec wins at home and not away)
- `firewall.*.status`: `"holds"`, `"does not hold"` or `"measured"`, with a short `note`. The converter proposes these from the numbers; confirm each one.
- `qa.riskModelRealAuc`, `qa.riskTopPatterns`, `qa.handRulesOptimalOn`: short sentences for the appendix answers

The field list is at the top of `results.js`. Keep the object valid JSON (double quotes, no trailing commas). If the file breaks, the deck still opens with a red banner and everything pending.

Only use numbers that came out of `dnacodec.evaluate` on held-out seeds and that the ML verifier signed off. The rule of the brand applies: write it so it works whether the result is a gain or "the hand rules are already right".

**Preview with fake numbers:** open `?sample` (for example `http://localhost:8000/?sample`). That loads `results.sample.js`, whose values are made up, and shows a **SAMPLE DATA** badge on every slide and in the presenter view. Never present with it.

## Numbers the deck states

Measured and documented, never invented: shared Nanopore errors 45 to 66% explained by the 5-letter context and hot spots at AUC 0.80 to 0.90 on held-apart data in two datasets; the held-out calibration table (real 4.9 / 39.8 / 66.5 / 84.5 / 90.1% vs Simulator A 3.6 / 40.3 / 63.7 / 83.0 / 90.3% exact strands at 2 / 4 / 6 / 10 / 16 reads); the decoder chain on real held-out clusters, baseline to polished, 41.0% to 56.8% exact strands at 4 reads, 67.0% to 82.5% at 6 and 90.0% to 95.1% at 16, with 4.7% of strands unrecoverable even from all 27 reads; 20 KB test file, 300 held-out trials; about 11,000 lines of Python, about 2,500 of them tests; ASUS Ascent GX10 (NVIDIA GB10). The appendix also quotes specs from `docs/ERRORS.md`, `docs/MODELS.md` and `README.md` (fitted error rates, model size, 32 simulations per label). The DNA strands on slides 3, 4 and 6 are illustrations; the rule checks on slide 4 are computed from the shown letters.

## Files

| File | What |
|---|---|
| `index.html` | All slides and speaker notes |
| `deck.css` | Brand tokens (dark and light), layout, animations, presenter view |
| `deck.js` | Engine: scaling, navigation, builds, hash, overview, help, black screen, presenter view, window sync, timer and pacing |
| `slides.js` | Generated and data-driven content: DNA letters, candidates, rule audit, charts, the A0 architecture diagram, placeholders, spoken result sentences |
| `results.js` | The one place for results (pending until the runs finish) |
| `results.sample.js` | Fake values for previewing, labeled SAMPLE |
| `tools/results_from_run.py` | Fills `results.js` from `results/<run_id>/` |
| `serve.sh` | Serves this folder on localhost |
| `fonts/` | Instrument Sans and JetBrains Mono (latin subset, SIL Open Font License, licenses included) |
| `preview/` | Small preview images |

## Offline and browsers

Everything is local: fonts are bundled, there are no CDN scripts, and nothing is fetched at runtime. Tested in Chrome (headless, 1920 x 1080, dark and light, with and without results). Other modern browsers should work; the window sync falls back from BroadcastChannel to postMessage between the deck and its presenter popup, then to localStorage events.
