# Erbgut brand

The identity for our HackMIT 2026 project. The repository is still called **Adaptive DNA Codec**; that name stays valid as the technical descriptor.

**One line:** We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

---

## 1. Name

**Erbgut**, chosen by the team. German for the genetic material an organism inherits and passes on, literally "inheritance goods". It carries the two things the project is about: DNA, and something valuable that has to survive being handed down.

**Say it:** AIR-b-goot (German "Erb" as in "air" with a rolled r, "gut" as in "good").

**Descriptor:** the repository is still called *Adaptive DNA Codec*, and that stays valid where a technical reader needs it: *Erbgut, an adaptive DNA codec tuner.*

**For an English-speaking audience,** gloss it on first mention in longer text: "Erbgut, German for the genetic material you inherit." In the pitch, one spoken half-sentence is enough. Don't over-explain it on a slide.

### Writing the name

- **Erbgut**, one word, capital E, lowercase rest. Never "ErbGut", "Erb Gut" or "ERBGUT" except in a wordmark.
- In code and URLs: `erbgut`.
- First mention in long text: "Erbgut (Adaptive DNA Codec)" where the repo name matters.

### Other candidates considered

| Name | Why not |
|---|---|
| StrandAudit | Descriptive of the main output, but less distinctive, and "Strand" is used in product names by a bioinformatics company |
| Adaptive DNA Codec | Safe descriptor, but sounds like we built a new codec, which invites the wrong question. Kept as the technical name |
| Earnbase | From the thesis, every rule earns its bases. Meaning isn't obvious without the tagline |
| Ruleproof | Several existing products with that name |

**Trademark note:** we did a quick web search only, and "Erbgut" is a common German noun, so it is unlikely to be ownable as a mark. We could not verify trademarks or domains. Check before using the name outside the hackathon.

**The mark still fits:** a double helix drawn as two strands that cross, each strand in one of the accent colors that the product uses for its verdicts. Inheritance, in the shape of the thing we measure.

---

## 2. Taglines

| Tagline | Use |
|---|---|
| **Measure the rule. Keep what pays.** | Primary. Hero, title slide, social. |
| Every rule earns its bases. | Secondary. Slide footers, stickers, closing line. |
| Rules are hypotheses. Test them per channel. | For researchers. Poster, LinkedIn. |
| Find out which rules your channel actually needs. | For storage engineers. One pager, Devpost. |

Avoid taglines that assume the outcome, such as "Stop paying for rules you don't need". The tool can also conclude that the hand rules are already right. That's a valid result, and the brand must survive it.

---

## 3. Positioning

**For** teams designing DNA data storage pipelines,
**who** choose sequence rules and redundancy by hand and apply them to every sequencing channel,
**Erbgut** is a codec tuning tool
**that** measures, per channel, whether each rule and each extra strand of redundancy actually reduces decoding failure, and learns from decoder failures what else to avoid.
**Unlike** fixed codecs and one-time decoder training,
**it** judges every setting against a fixed recovery target, with the same encoder and the same decoder, so any change it recommends is a measured one.

### What we are not

- Not a new encoder. The encoder is a standard Fountain code on purpose.
- Not a claim to beat DNA Fountain or DNAformer.
- Not wet lab work. Everything is measured on a channel simulator calibrated on real reads, and checked against real held-out reads.

Saying this up front is part of the brand. It makes every other claim more believable.

---

## 4. Audiences

| Audience | What they care about | What we lead with |
|---|---|---|
| **Hackathon judges** | A clear problem, a working demo, technical depth, honesty about limits | The rule audit view, then the Pareto plot. One sentence of problem, then show it. |
| **DNA storage researchers** | Whether the method is sound, whether the simulator is realistic, what's new relative to published work | The evidence design (ablation ladder, crossover matrix, sim-to-real firewall) and the calibration numbers. |
| **Storage engineers** | Cost to write and read, a tool they could run on their own constraints | Reads per strand and bits per base at a fixed recovery target. "Given my channel and budget, what should I use?" |

---

## 5. Key messages and proof points

| Message | Proof point (measured or designed, never invented) |
|---|---|
| **Coding rules are folklore. We measure them.** | The rule audit switches each rule on and off per channel and reports what it costs and what it buys, from held-out recovery trials. |
| **The comparison is fair.** | Same encoder, same decoder. The default we compare against always uses the same decoder (ablation step B). Fixed 20 KB test file, 300 held-out trials, objective fixed before any run. |
| **The simulator is honest.** | Within about 3 points of real Nanopore reads at every read count on held-out Microsoft data, and where it deviates it's slightly harder. |
| **There is something to learn beyond the hand rules.** | On real Nanopore reads, errors shared by all reads are 45 to 66% predictable from the local 5-letter context (held-apart data, two independent datasets), with hot-spot AUC 0.80 to 0.90. The hand rules don't cover these contexts. |
| **We guard against fooling ourselves.** | Sim-to-real firewall: a structurally different Simulator B, and the risk model's ranking checked on real held-out reads. Gains that don't survive are reported as such. |
| **Any outcome is a useful answer.** | If the hand rules are already near optimal on a channel, the tool shows that with measurements. |
| **It's real engineering.** | About 11,000 lines of Python, about 2,500 of them tests. Baseline decoder at 90.6% exact strands with 16 reads on real held-out data. Runs on an ASUS Ascent GX10 (NVIDIA GB10). |

Results that don't exist yet are always written as placeholders, for example **[RESULT: reads per strand saved on Nanopore at matched density]**. See section 8.

---

## 6. Voice and tone

Plain, confident, concrete. We sound like a careful engineer explaining a measurement, not a startup announcing a revolution.

- **Plain:** short sentences, common words. Explain every DNA term once.
- **Confident:** state what we measured without hedging it into mush.
- **Concrete:** numbers with units and conditions ("at 16 reads, on held-out data"), never adjectives in place of numbers.
- **Honest about scope:** say what we didn't do before someone asks.

### Do and don't

| Do | Don't |
|---|---|
| "We measure whether each rule pays off on your channel." | "We revolutionize DNA storage." |
| "Within about 3 points of real reads at every read count, on held-out data." | "Our simulator is basically perfect." |
| "Same encoder, same decoder. Only the rules and redundancy change." | "Our AI codec beats the state of the art." |
| "If the hand rules are already near optimal on Nanopore, the audit shows that." | "Hand rules are always wrong." |
| "No wet lab. We check against real sequencing reads instead." | Implying we synthesized or sequenced anything. |
| "[RESULT: reads saved on Nanopore at matched density]" | Any number that hasn't come out of `dnacodec.evaluate`. |
| Commas and full stops. | Em dashes or hyphens used as sentence punctuation. |
| "a tool", "the audit", "the channel" | "first ever", "world's best", "breakthrough", "game changer". |

### Words we use

rule, channel, pays off, reads per strand, bits per base, recovery target, held-out, audit, verdict, measured.

### Words we avoid

revolutionary, breakthrough, first, best, magic, AI-powered (as a selling point), unlimited, guaranteed.

---

## 7. Messaging hierarchy

### One-liner (5 seconds)

We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

### 30 seconds

DNA storage pipelines follow hand-written rules: no long runs of the same letter, GC content between 40 and 60%, a fixed amount of redundancy. They're chosen once and applied to every sequencing channel, and nobody measures whether they pay off. Erbgut does. For a given channel, it switches each rule on and off and measures what it costs and buys at a fixed recovery target, with the same encoder and decoder. Then it learns from where the decoder actually fails what else to avoid. Our simulator is within about 3 points of real Nanopore reads on held-out data, so the measurements mean something.

### 2 minutes

DNA can store data at extreme density for centuries, but writing and reading it is noisy. Letters get swapped, added or dropped, and whole strands go missing. To cope, pipelines follow rules: avoid runs of the same letter, keep GC content between 40 and 60%, add a fixed amount of redundancy. Every rule costs density or reads. The rules were chosen once, copied between papers, and applied to Nanopore and Illumina alike, even though those channels fail in very different ways.

Erbgut treats each rule as a hypothesis. For a channel (sequencing technology, read budget, recovery target), tier 1 audits every rule and the redundancy level: switch it off, measure the reads per strand and bits per base needed to recover a fixed 20 KB file in all 300 held-out trials, and keep only what pays off. Tier 2 goes further. A small CNN learns from real decoder failures which strands are risky, and the Fountain encoder picks the safest of several candidate strands, which costs no density.

We can't run a wet lab at a hackathon, so we built the evidence to be hard to fool. The channel simulator is calibrated on real Nanopore and Illumina reads and lands within about 3 points of real reads at every read count on held-out data. We found that shared Nanopore errors are 45 to 66% predictable from the local 5-letter context, patterns the hand rules don't cover. And every result goes through an ablation ladder, a crossover matrix and a sim-to-real firewall with a structurally different second simulator.

The outcome is a measured answer per channel: [RESULT: headline rule audit verdict per channel]. If the hand rules turn out to be near optimal on a channel, that's the answer, and now it's measured instead of assumed.

---

## 8. Placeholder convention

Final results are not in yet. Every place that needs one uses this exact form:

```
[RESULT: what will go here, with its conditions]
```

- Square brackets, the word RESULT in capitals, a colon, then a description specific enough that someone can fill it from the results folder without guessing.
- Write the surrounding sentence so it works whether the result is a gain or a "no difference". For example: "On Nanopore at matched density, the tuned codec needs [RESULT: reads per strand, tuned vs default]."
- On the landing page and slides, placeholders are styled in the **Tbd** color (section 9) with a dashed outline, so they can't be mistaken for results.
- Before any public use: `grep -rn "\[RESULT" marketing/` must return nothing, or every hit must be intentionally left as "pending".

---

## 9. Visual identity

> **The old palette is retired.** Indigo, magenta, teal and amber on near-black (`#3A3FC2`, `#B02A63`, `#0B7465`, `#F4C45A`, `#0F1417`) and the bar-chart mark with a dimension line are gone. Nothing new should use them. The pitch deck in `marketing/deck/` is the source of truth for this section; if the deck and this file ever disagree, the deck wins and this file gets fixed.

### Concept: the strand, lit

Pale, high-chroma accents on a deep green-black. The palette reads as an instrument panel in a dark room: the page recedes, the measurements glow. It is meant to be seen on a projector from the back of a room, and to survive being photographed off a screen.

The mark is a double helix: two strands crossing, the Audit strand over the Cost strand, held by two Gain rungs. The strands are drawn in the same colors the product uses for its verdicts, so the logo and the data are one system. It is a rounded tile, so it works as an avatar, a favicon and a splash screen without a container.

### Color

The four brand accents map to the four bases, and each has a job in the product. The mapping is our own, not the classic sequencer trace colors, so it doesn't read as a generic chromatogram.

| Base | Token | Role | Dark | Light |
|---|---|---|---|---|
| A | `--sa-audit` | **Audit.** Brand accent, links, the tool's own marks, the tailored codec | `#39E6FF` | `#10A6C4` |
| C | `--sa-cost` | **Cost.** A rule that costs without paying off, failures, risk | `#FF2E9C` | `#C41477` |
| G | `--sa-gain` | **Gain.** A rule that pays off, recovered, safe | `#39FF6E` | `#0E9C55` |
| T | `--sa-tbd` | **To be measured.** Placeholders, pending results | `#FFE13D` | `#8A7A00` |

Mnemonic: **A**udit, **C**ost, **G**ain, **T**o be measured.

Neutrals:

| Token | Role | Dark | Light |
|---|---|---|---|
| `--sa-paper` | Page background | `#060F0A` | `#EAF3ED` |
| `--sa-surface` | Cards, panels, tables | `#0B2116` | `#FFFFFF` |
| `--sa-surface-2` | A second level inside a card | `#10331F` | `#DCEDE1` |
| `--sa-ink` | Body text | `#EAFFF3` | `#08130B` |
| `--sa-muted` | Secondary text | `#6FCB98` | `#3E6B52` |
| `--sa-rule` | Lines and borders | `#1B4A2E` | `#C3D9C9` |
| `--on-accent` | Text on a solid accent fill | `#060F0A` | `#FFFFFF` |

Placeholder support tokens:

| Token | Role | Dark | Light |
|---|---|---|---|
| `--sa-tbd-fill` | Solid fill for a pending swatch | `#FFE13D` | `#FFE13D` |
| `--sa-tbd-bg` | Background behind a placeholder | `#1E2A0C` | `#EAF5DA` |
| `--sa-tbd-line` | Its dashed border | `#D4B62E` | `#A9B81C` |

Tints, for a faint wash of an accent behind a verdict:

```
--tint-audit: rgba(57, 230, 255, 0.14)    --tint-cost: rgba(255, 46, 156, 0.15)
--tint-gain:  rgba(57, 255, 110, 0.18)    --tint-ink:  rgba(230, 236, 234, 0.06)
```

#### Text colors on light: use the darker step

The light values above are the deck's, and they are tuned for headline-size type on a projector. Three of them are below WCAG AA at normal text size. **For text on a light background, use these instead.** They are the same hues, one step darker, and they are what `marketing/index.html`, `dashboard/app.py` and `dnacodec/demo.py` use.

| Role | Light text value | Instead of |
|---|---|---|
| Audit | `#0B6E82` | `#10A6C4` |
| Gain | `#0A7A41` | `#0E9C55` |
| Tbd | `#6E6200` | `#8A7A00` |
| Cost | `#C41477` (unchanged) | |

#### Measured contrast (WCAG 2.x)

Dark theme, on `--sa-paper` `#060F0A`. All pass AA for normal text, all but Cost pass AAA:

| Pair | Ratio |
|---|---|
| Ink on paper | 18.6 : 1 |
| Muted on paper | 9.9 : 1 |
| Audit on paper | 12.9 : 1 |
| Gain on paper | 14.5 : 1 |
| Tbd on paper | 14.9 : 1 |
| Cost on paper | 5.7 : 1 |
| Ink on surface `#0B2116` | 16.2 : 1 |
| Tbd on tbd-bg `#1E2A0C` | 11.6 : 1 |
| Paper on an Audit or Gain fill | 12.9 : 1 and 14.5 : 1 |

Light theme, on `--sa-paper` `#EAF3ED`, using the darker text step:

| Pair | Ratio |
|---|---|
| Ink on paper | 16.7 : 1 |
| Muted on paper | 5.4 : 1 |
| Audit `#0B6E82` on paper | 5.2 : 1 |
| Cost `#C41477` on paper | 5.0 : 1 |
| Gain `#0A7A41` on paper | 4.8 : 1 |
| Tbd `#6E6200` on tbd-bg `#EAF5DA` | 5.5 : 1 |
| White on an Audit, Cost or Gain fill | 5.9, 5.7, 5.4 : 1 |

The deck's own light values, for reference, are below AA at normal size: Audit `#10A6C4` 2.6 : 1, Gain `#0E9C55` 3.1 : 1, Tbd `#8A7A00` 3.8 : 1. They are fine for 60 px headlines on a projector and nowhere else.

Cost and Gain are magenta and green rather than red and green, and Audit is cyan, so the three stay separable under deuteranopia and protanopia: they differ in lightness as well as hue. Even so, **never use color alone for a verdict**: always pair it with a word or a symbol ("pays off" with a check, "doesn't pay off" with a cross, "pending" with a dashed outline).

### Typography

| Role | Family | Weights | Notes |
|---|---|---|---|
| Display and text | **Instrument Sans** (`SA Sans` in the deck) | 400 to 700, variable | A measuring-instrument name for a measuring tool. Headlines at 600 with tight tracking (-0.02em, -0.03em on the wordmark). Body at 400. |
| Sequences and numbers | **JetBrains Mono** (`SA Mono`) | 400, 600 | Only for DNA sequences (`ACGTGGGA`), code, and aligned numeric columns. Not for decorative labels. |

The deck self-hosts both as woff2 in `marketing/deck/fonts/`, so it works with no network. Everything else may load them from Google Fonts:

```html
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
```

**Deck type scale** (logical px on the 1920 x 1080 stage): 176 wordmark, 94 tagline, 72 slide title, 60 small title, 38 one-liner, 32 lede, 30 verdict, 28 kicker, 24 pill, 22 footer, 20 slide number.

**Web and dashboard scale** (1.25 ratio, 18 px base): 14, 18, 22, 28, 35, 44, 55. Body 17 to 18 px at line height 1.55. Sentence case everywhere. No all-caps labels.

### Logo

The artwork is a DNA double helix drawn as two crossing cyan ribbons, with four base pairs
held in the middle lens as rounded capsules, letters in black: **A/T** yellow, **G/C**
green, **C/G** pink, **T/A** green. Two more capsule pairs, pink and yellow, sit under the
crossings at each end without letters. Binary digits scatter above and below the centre,
because the point of the whole project is data written into a strand. The wordmark
**erbgut**, lowercase, sits underneath.

The master is `marketing/logo/erbgut-logo-source.jpeg`. Everything shipped is vector,
rebuilt from it by `marketing/logo/build_logo.py`, which is where the geometry lives:

```
helix        two ribbons, stroke 25, crossing at x 115 and x 319 of a 434 unit box
             half separation 64.5 at the centre lobe, 52.8 at the two end lobes
base pairs   rounded capsules 22.5 to 23 wide, fully rounded ends
type         Instrument Sans, outlined: 700 for the base letters and the binary,
             600 at -0.05em for the wordmark
```

| Role | Value |
|---|---|
| Helix | `#32C7DB` |
| A/T capsule | `#FEC746` |
| G/C and T/A capsules | `#5BD67C` |
| C/G capsule | `#FC68D6` |
| Base letters | `#101418` |
| Binary, light background | `#101418` |
| Binary, dark background | `#E6F6F8` |
| Wordmark, light background | `#4F4F4F` |
| Wordmark, dark background | `#F2F7F6` |

**The mark does not invert.** The helix and the capsules carry their own colours on any
background; only the binary and the wordmark change, which is the whole difference between
`erbgut-logo.svg` and `erbgut-logo-dark.svg`.

The wordmark is Instrument Sans 600 at tracking -0.05em, optically centred on the helix. In
every SVG it is converted to outlines from the repository's own font file, so it renders the
same through an `<img>` tag, offline, and in print.

| File | Use |
|---|---|
| `logo/erbgut-logo.svg` | Full lockup on light backgrounds. The primary asset |
| `logo/erbgut-logo-dark.svg` | Full lockup on dark backgrounds |
| `logo/erbgut-mark.svg` | One lens with its four lettered base pairs. Avatars, the deck footer, anywhere at 48 px and up |
| `logo/erbgut-mark-small.svg` | One lens, three fatter capsules, no letters. Favicons, the site header, the ESP32-S3-BOX-3 splash |
| `logo.svg`, `logo-dark.svg`, `logo-mark.svg` | Copies at the old paths, so existing links keep working |
| `site/assets/logo.svg`, `mark.svg`, `favicon.svg` | The site's copies |

### Usage rules

- **Clear space:** at least half the helix's stroke width on every side of the lockup.
- **Minimum size:** the lettered mark holds down to about 48 px; below that the base letters
  close up, so use `erbgut-mark-small.svg`, which is drawn for 16 to 32 px. Full lockup 180 px
  wide.
- **Backgrounds:** paper, surface, white, or anything darker than `#1B4A2E` with the dark
  lockup. Never on photos, busy gradients or a mid-tone.
- **Don't** recolour the helix or the capsules, reorder the base pairs, rotate or stretch the
  artwork, add a glow or a drop shadow, or set the wordmark in another face.
- **Placeholders** are always Tbd coloured and dashed, never styled like a real number.
- **Verdict colours** (Cost, Gain) are only used for verdicts and data, never as decoration.
  The logo's four capsule colours are the logo's, not verdict colours.

### Iconography

- Line icons on a 24 px grid, 2 px stroke, round caps and joins, no fills.
- Verdict icons: a check in `--sa-gain`, a cross in `--sa-cost`, a dashed outline in `--sa-tbd` for pending. Always with a word next to them.
- Sequence letters are colored by base with the same four accents (`--nt-A`, `--nt-C`, `--nt-G`, `--nt-T` in the deck), never highlighted in a fifth color.
- No test tubes, pipettes or flasks. We don't do wet lab work, so we don't show it.
