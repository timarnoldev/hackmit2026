# StrandAudit deck outline

Seven slides, built to support the 3-minute pitch in `PITCH.md`. Most of the time is spent in the live dashboard; the slides frame it and are the fallback if the demo fails.

**Style:** paper background (`#F2F5F4`) or dark paper (`#0F1417`), Instrument Sans for text, JetBrains Mono only for DNA sequences and numeric tables. One idea per slide. Every placeholder uses the Tbd style from `BRAND.md` (amber, dashed outline) so it can't be mistaken for a result. Footer on every slide: the mark, "StrandAudit", slide number.

---

## Slide 1: Title

- **Title:** Measure the rule. Keep what pays.
- **Text:** StrandAudit. We built a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly. HackMIT 2026. [TEAM: names]
- **Visual:** the hero strand from the landing page, large and in mono: `ACTGAGGGGTCAAGCTTAGCC…` with the dimension line over `GGGG` in audit indigo. Full logo top left.
- **Speaker notes:** Don't read the slide. Open with the first line of the pitch: "DNA can store data at extreme density for centuries. But writing and reading it is noisy."

## Slide 2: The problem

- **Title:** Rules chosen once, applied to every channel
- **Text:** three rows, each with its cost:
  - No run of the same letter longer than 3
  - GC content between 40 and 60%
  - 30% extra strands, on every channel
  - Bottom line: "Each one costs storage or reads. Nobody measures whether it pays off."
- **Visual:** two columns under the rules, Nanopore ("many insertions and deletions, especially in runs") and Illumina ("few errors, mostly single wrong letters"), to show the channels fail differently.
- **Speaker notes:** Keep it to 20 seconds. The key word is "every": the same rules on channels that fail in completely different ways.

## Slide 3: What StrandAudit does

- **Title:** Two tiers, one fixed target
- **Text:**
  - Tier 1, rule audit: switch each rule off, measure what it costs and buys, keep what pays.
  - Tier 2, learned selection: learn from decoder failures what else to avoid; pick the safest candidate strand at no cost in density.
  - Target: a fixed 20 KB file, recovered exactly in all 300 held-out trials. Same encoder, same decoder.
- **Visual:** the five-step loop as a horizontal row: adapt decoder and freeze, label strands (32 simulations each), train risk model, search settings, re-adapt decoder.
- **Speaker notes:** Stress "same encoder, same decoder". This preempts "isn't it just a better decoder". Then switch to the dashboard.

## Slide 4: The rule audit (demo, with fallback screenshot)

- **Title:** Does this rule pay off here?
- **Text:** the audit table, one row per rule, one column per channel:

  | Rule | Nanopore | Illumina |
  |---|---|---|
  | Max run 3 | [RESULT: verdict, reads with rule on vs off] | [RESULT: verdict, reads with rule on vs off] |
  | GC 40 to 60% | [RESULT] | [RESULT] |
  | Redundancy 0.3 | [RESULT: tuned value] | [RESULT: tuned value] |

- **Visual:** live dashboard rule audit view. Fallback: a screenshot of it from the final run. Verdicts shown with a word and an icon (check in gain teal, cross in cost magenta), never color alone.
- **Speaker notes:** Read one row on each channel, no more. Use the variant that matches the result: "same rule, different answer" or "same answer on both, and now we know".

## Slide 5: The Pareto plot and the crossover

- **Title:** Tuned for this channel, not just better
- **Text:** left: "Down is fewer reads. Right is more bits per base." Right: "Each tuned codec on its own channel and on the other."
- **Visual:** left half, the Pareto plot for Nanopore at a tight budget, default point plus one point per tuning round (from the dashboard). Right half, the 3 by 2 crossover matrix, colored, with [RESULT] cells until the runs finish.
- **Speaker notes:** Pareto first: [RESULT: default vs tuned reads per strand at matched density]. Then the crossover: [RESULT: wins at home and not away, or holds on both]. Mention the 45 to 66% context predictability here if tier 2 moved the point.

## Slide 6: See it: the image round trip

- **Title:** An image through DNA, at 6 reads per strand
- **Text:** "Default codec" and "Tuned codec", each with its outcome: [RESULT: recovered or not, strands used].
- **Visual:** the demo image decoded side by side, live. Fallback: the two precomputed outputs. If the ESP32-S3-BOX-3 front end is built and working, show it querying the GX10 here; otherwise leave it out.
- **Speaker notes:** Let the picture do the talking. Say the read budget out loud: 6 reads per strand. If both codecs recover it, say how much less the tuned one needed.

## Slide 7: Why you can trust it

- **Title:** Built so we can't fool ourselves
- **Text:**
  - Simulator within about 3 points of real Nanopore reads at every read count, held-out data
  - Shared Nanopore errors 45 to 66% predictable from the 5-letter context, hot-spot AUC 0.80 to 0.90, two independent datasets
  - Ablation ladder A to E, crossover matrix, sim-to-real firewall with a second simulator
  - No wet lab. No claim to beat DNA Fountain or DNAformer.
  - Closing line: "If the hand rules are right, now it's measured. Measure the rule. Keep what pays."
- **Visual:** the calibration table (real vs simulated exact strands at 2, 4, 6, 10, 16 reads on the held-out split, from `docs/ERRORS.md`), plus a small ladder graphic A B C D E with "B to C: tier 1" and "C to D: tier 2" marked.
- **Speaker notes:** This is the slide judges photograph. End on the tagline and stop talking. Back pocket: GX10 (NVIDIA GB10), about 11,000 lines of Python, about 2,500 of them tests.

---

## Optional backup slide: Ablation ladder

- **Title:** Where the gain comes from
- **Text:** A: fixed rules, majority vote. B: fixed rules, transformer. C: audited rules and tuned redundancy. D: plus learned risk scorer. E: full loop. The default is always B.
- **Visual:** bar chart from the dashboard, reads per strand needed per system, baseline always visible. [RESULT: bar values]
- **Speaker notes:** Only for the "isn't it the decoder" question.
