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

**The mark still fits:** four bases with a dimension line measuring a run of identical letters. It says "this is being measured", which is what the tool does, whatever the name.

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

### Concept: the strand under audit

The mark shows four bases (A, C, G, G) as bars standing on a strand. The last two bars are the same base, a run, and an engineering dimension line sits over them. A run of identical letters is exactly what the best known hand rule polices, and the dimension line is the universal sign for "this is being measured". The picture says: this piece of the strand is under audit.

The bars are deliberately level and plain. It reads as a sequence first and a measurement second, never as a chart that claims a result.

We deliberately avoid the double helix, the cliché of DNA branding, and anything that looks like a lab product.

### Color

The four brand accents map to the four bases, and each has a job in the product. The mapping is our own, not the classic sequencer trace colors, so it doesn't read as a generic chromatogram.

| Base | Token | Role | Light | Dark |
|---|---|---|---|---|
| A | `--sa-audit` | **Audit.** Brand accent, links, the tool's own marks | `#3A3FC2` | `#A3A6FF` |
| C | `--sa-cost` | **Cost.** A rule that costs without paying off, failures, risk | `#B02A63` | `#FF86B0` |
| G | `--sa-gain` | **Gain.** A rule that pays off, recovered, safe | `#0B7465` | `#4FD6B5` |
| T | `--sa-tbd` | **To be measured.** Placeholders, pending results | `#8A5A00` (text), `#F4C45A` (fill) | `#F4C45A` |

Mnemonic: **A**udit, **C**ost, **G**ain, **T**o be measured.

Neutrals:

| Token | Role | Light | Dark |
|---|---|---|---|
| `--sa-paper` | Page background | `#F2F5F4` | `#0F1417` |
| `--sa-surface` | Panels, tables | `#FFFFFF` | `#172026` |
| `--sa-ink` | Body text, the mark's tile | `#131B20` | `#E6ECEA` |
| `--sa-muted` | Secondary text | `#4B5A60` | `#9AA9AE` |
| `--sa-rule` | Lines and borders | `#CBD4D2` | `#2C353B` |

Measured contrast (WCAG 2.x), all at least AA for normal text:

| Pair | Light | Dark |
|---|---|---|
| Ink on paper | 15.9 : 1 | 15.5 : 1 |
| Muted on paper | 6.5 : 1 | 7.7 : 1 |
| Audit on paper | 7.2 : 1 | 8.4 : 1 |
| Cost on paper | 5.7 : 1 | 8.2 : 1 |
| Gain on paper | 5.2 : 1 | 10.3 : 1 |
| Tbd on paper | 5.4 : 1 | 11.4 : 1 |
| Ink on Tbd fill | 10.7 : 1 | |

Cost and gain were picked as magenta and teal rather than red and green, so they stay distinct for the most common forms of color blindness. Even so, **never use color alone for a verdict**: always pair it with a word or a symbol ("pays off" with a check, "doesn't pay off" with a cross).

### Typography

Both from Google Fonts.

| Role | Family | Weights | Notes |
|---|---|---|---|
| Display and text | **Instrument Sans** | 400, 500, 600, 700 | A measuring-instrument name for a measuring tool. Headlines at 600 with slightly tight tracking (-0.01em to -0.02em). Body at 400, 17 to 18 px, line height 1.55. |
| Sequences and numbers in tables | **JetBrains Mono** | 400, 600 | Only for DNA sequences (`ACGTGGGA`), code, and aligned numeric columns. Not for decorative labels. |

Type scale (1.25 ratio, 18 px base): 14, 18, 22, 28, 35, 44, 55. Sentence case everywhere. No all-caps labels.

Import:

```html
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
```

### Iconography

- Line icons on a 24 px grid, 2 px stroke, round caps and joins, no fills except the four base bars.
- Build from the logo's parts: bars (bases), the dimension line (a measured window), a horizontal line (the strand).
- Verdict icons: a check in `--sa-gain`, a cross in `--sa-cost`, a dashed circle in `--sa-tbd` for pending.
- No helices, test tubes, pipettes or flasks. We don't do wet lab work, so we don't show it.

### Logo files

| File | Use |
|---|---|
| `logo.svg` | Full logo, mark plus wordmark, on light backgrounds |
| `logo-dark.svg` | Full logo on dark backgrounds |
| `logo-mark.svg` | Mark only: favicons, avatars, the ESP32-S3-BOX-3 splash screen if we show it |

The wordmark is set in Instrument Sans through an SVG `<text>` element with a system sans fallback. For print or merch, open the file with the font installed and convert the text to outlines.

### Usage rules

- **Clear space:** at least half the mark's height on every side.
- **Minimum size:** mark 16 px (tested as a favicon), full logo 120 px wide.
- **Backgrounds:** `logo.svg` on paper, surface or white. `logo-dark.svg` on `--sa-paper` dark or any background darker than `#3A4650`. Never on photos or busy gradients.
- **Don't** recolor the bars, rotate the mark, stretch it, add a helix, add shadows or outlines, or change the order of the bars (the run of two identical bars under the dimension line is the point).
- **Placeholders** are always Tbd colored and dashed, never styled like a real number.
- **Verdict colors** (cost, gain) are only used for verdicts and data, never as decoration.
