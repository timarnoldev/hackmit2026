# Erbgut social posts

Replace every `[RESULT: ...]` before posting, or cut that line. Never post a placeholder and never round a result up. Each result line has two variants; use the one that matches what the runs show.

Keep each post under 280 characters after filling results (posts 1 and 3 are already near the limit). Hashtags, sparingly: #HackMIT #DNAStorage. Link: github.com/timarnoldev/hackmit2026. Attach the rule audit screenshot or the demo video.

---

## X / Twitter thread

**1/**
DNA storage pipelines follow hand-written rules: no more than 3 of the same letter in a row, GC content 40 to 60%, 30% extra strands.

They're applied to every sequencing machine. Nobody measures whether they pay off.

At #HackMIT we built Erbgut to measure it. A thread:

**2/**
The idea: treat every rule as a hypothesis.

For one channel (sequencing tech, read budget), switch the rule off and measure what it takes to recover a 20 KB file in all 300 held-out trials. Same encoder, same decoder. Keep only what pays.

**3/**
Tier 2 goes beyond the hand rules.

On real Nanopore reads, the errors every read shares are 45 to 66% predictable from the local 5-letter context, in two independent datasets. The standard rules don't cover those contexts. A small CNN learns them from where our decoder fails.

**4/**
The encoder writes the same data as several candidate strands and keeps the safest one.

That costs zero density. It only costs compute.

**5/**
Results:
Nanopore: [RESULT: verdict for the run-length rule, reads per strand default vs tuned]
Illumina: [RESULT: verdict, bits per base default vs tuned]

Variant if nothing moves: "On Nanopore the hand rules held up. Now that's measured, not assumed."

**6/**
No wet lab, so we built the evidence to catch us out:
• simulator within about 3 points of real reads on held-out data
• a second, differently built simulator every gain must survive
• an ablation ladder so you see exactly where a gain comes from

**7/**
We don't claim a new encoder (ours is a standard Fountain code on purpose) or a better decoder. Our opponent is the hand-tuned default.

Measure the rule. Keep what pays.
Code: github.com/timarnoldev/hackmit2026

---

## LinkedIn

Every DNA data storage pipeline follows a few hand-written rules. Never more than three of the same letter in a row. Keep GC content between 40 and 60%. Add 30% extra strands.

Each rule costs storage density or sequencing reads. They were chosen once, copied between papers, and are applied to every sequencing channel, even though Nanopore and Illumina fail in very different ways.

At HackMIT 2026 we built Erbgut, a tool that measures whether a DNA coding rule actually pays off on your channel, and tunes the codec accordingly.

How it works:
1. Rule audit. For a given channel and read budget, it switches each rule off and measures the reads per strand and bits per base needed to recover a fixed 20 KB file in all 300 held-out trials. Same encoder, same decoder.
2. Learned selection. A small CNN learns from real decoder failures which strands are risky, and the Fountain encoder keeps the safest of several candidates, at no cost in density.

We have no wet lab, so the numbers had to be earned. Our channel simulator is calibrated on real Nanopore and Illumina reads and lands within about 3 points of real reads on held-out data. We also found that shared Nanopore errors are 45 to 66% predictable from the local 5-letter context, patterns the standard rules don't cover. Every gain has to survive a second, structurally different simulator and a check on real reads.

What we found: [RESULT: two sentences on the rule audit and the tuned codec vs the default, or "on Nanopore the hand rules held up, on Illumina ..."].

Everything runs on an ASUS Ascent GX10 (NVIDIA GB10). About 11,000 lines of Python, about 2,500 of them tests, much of it written with coding agents working in parallel while we owned the interfaces and checked every number.

Thanks to the teams who published the Microsoft clustered Nanopore reads and the DNAformer datasets. Real reads made this possible.

Code: github.com/timarnoldev/hackmit2026
Team: [TEAM: names, tagged]

#HackMIT #DNAStorage #MachineLearning

---

## Demo video voiceover (about 75 seconds)

Target: 75 seconds at a calm pace, roughly 170 words. Screen directions in italics.

| Time | Screen | Voiceover |
|---|---|---|
| 0:00 to 0:08 | *Logo on paper background, then the strand with the dimension line drawing over GGGG.* | DNA can store data for centuries. But every DNA storage pipeline follows the same hand-written rules. |
| 0:08 to 0:18 | *The three rules appear one by one.* | No more than three of the same letter in a row. GC content between 40 and 60%. Thirty percent extra strands. Applied to every sequencing machine, and never measured. |
| 0:18 to 0:26 | *Title card: Erbgut. Measure the rule. Keep what pays.* | Erbgut measures whether each rule actually pays off on your channel, and tunes the codec accordingly. |
| 0:26 to 0:40 | *Dashboard, rule audit view, Nanopore, then Illumina.* | We switch each rule off and measure what it takes to recover a 20 KB file in all 300 held-out trials. Same encoder, same decoder. On Nanopore: [RESULT: one clause]. On Illumina: [RESULT: one clause]. |
| 0:40 to 0:52 | *Pareto plot, points moving from the default toward the front.* | Then it learns from where the decoder fails. On real Nanopore reads, the errors all copies share are 45 to 66% predictable from the local five-letter context, and no hand rule covers that. [RESULT: where the tuned codec lands]. |
| 0:52 to 1:03 | *Image round trip, default and tuned side by side at 6 reads per strand.* | An image, through DNA, at six reads per strand. [RESULT: default outcome, tuned outcome]. |
| 1:03 to 1:15 | *Calibration table, then logo and repo link.* | No wet lab, so our simulator is calibrated on real reads, within about 3 points on held-out data, and every gain must survive a second simulator. Erbgut. Measure the rule. Keep what pays. |
