# The landing page: contract and copy

One rule above all others: **a VC watches this page, they do not read it.** Every scene is a
picture that moves and at most two short lines of text. If a sentence can be replaced by the
animation showing it, delete the sentence.

Theme: **light, premium**. Tokens from `marketing/BRAND.md`, light column, with the darker text
step for body copy. Paper `#EAF3ED`, surface `#FFFFFF`, ink `#08130B`, muted `#3E6B52`,
audit `#0B6E82`, gain `#0A7A41`, cost `#C41477`.

---

## 1. The scene contract

The page is a shell plus independent scenes. The shell owns scrolling; a scene owns pixels and
knows nothing about scrolling.

```js
// site/scenes/<id>.js
export default {
  id: 'noise',                 // matches the section's data-scene
  mount(root, ctx) {},         // build DOM/SVG/canvas once; ctx = {reduced: boolean}
  update(p) {},                // p in [0,1]: how far through this scene we are
  resize(w, h) {},             // the sticky stage resized
  unmount() {},                // optional
};
```

Rules a scene must follow:

1. **`update(p)` is pure.** Given the same `p` it draws the same frame, whether the user scrolled
   down, up, or jumped. No accumulating state, no `setInterval` driving the story. Ambient
   idle motion (a slow drift, a pulse) may run on its own rAF, the story may not.
2. **`update` is called inside one shared rAF.** Never start your own loop for story frames,
   never write layout-reading code (`offsetWidth`) inside it.
3. **`ctx.reduced` is `prefers-reduced-motion`.** When true, `update(p)` should jump to the scene's
   final, readable state and skip ambient motion. The page must make sense as a series of
   stills.
4. **Self-contained.** A scene may not touch another scene's DOM, global styles, or the shell.
5. **No dependencies.** Vanilla JS, SVG or canvas. No libraries, nothing from a CDN.
6. **Sized by the stage.** The stage is `100vh` minus the header. Draw in a viewBox and let it
   scale; do not assume pixels. Must look right from 360px to 2560px wide.

The shell gives each `<section data-scene="id">` a scroll length, pins the stage, and calls
`update(p)`. Text blocks inside the section fade in and out at declared `data-at` positions, so
copy is HTML, not something the scene draws.

---

## 2. The story, in order

Ten beats. **Every line below is the final copy.** Do not add explanatory sentences, do not
expand a line into a paragraph, do not invent a number. If a beat needs more words to work, the
animation is not doing its job.

### 1. `hero` — the promise
> **Every DNA coding rule is a hypothesis.**
> We measure which ones pay off.

Ambient: loose A, C, G, T letters drifting and settling into a single clean strand. Slow, quiet,
no scroll needed. One scroll cue.

### 2. `write` — a file becomes DNA
> A file becomes DNA.

A block of bytes dissolves into 1,239 short strands. Show the count. End state: a calm field of
strands.

### 3. `bill` — the recurring cost
> Writing it is once.
> **Reading it back is the bill that comes back.**

One strand at the top. It gets read again and again, each read stacking below it, and a cost
meter climbs with every read. The meter is the point: **cost scales with reads, not with data.**

### 4. `noise` — why reading is expensive
> Every read comes back wrong. Differently wrong.

The same strand, six reads. Letters flicker to the cost colour as substitutions, a letter
vanishes and shifts the rest as a deletion, one extra letter appears. End state: six visibly
different reads under one true strand.

### 5. `vote` — the classic fix
> The field's answer: read more, take a vote.
> `67.2%` of strands come back exactly right.

The six reads align into columns, each column votes, a consensus strand assembles. Some columns
resolve green, a few resolve wrong in cost colour. The number counts up to 67.2% and stops.
Caption, small: *six reads per strand, real Nanopore data.*

### 6. `polish` — the money beat
> A small model learned what voting cannot fix.
> `88.1%`

A band sweeps along the consensus strand. Where voting got it wrong, the band flips the letter to
the gain colour. The 67.2% number climbs to 88.1% as the band passes. This beat should feel like
the best moment on the page.
Caption, small: *0.8M parameters. Same reads, same file, one extra step.*

### 7. `loop` — the part nobody else does
> Then we turn it around.
> The failures teach the encoder what to avoid.

The strands that still failed detach and flow backwards into an encoder block. The encoder is
choosing between candidate strands: one with a long run of G lights up in the cost colour and is
rejected, a safer one is accepted. **This is the feedback loop, and it is the thing to show.**

### 8. `audit` — the product
> Every rule, measured on your channel.

Two rule switches (`no run over 3`, `GC 40 to 60%`) and two channels (Nanopore, Illumina). As the
scene plays, each rule gets a verdict per channel, and the verdicts disagree across channels:

| | Nanopore | Illumina |
|---|---|---|
| no run over 3 | **pays off** | no effect |
| GC 40 to 60% | no effect | **pays off** |

> Each standard rule pays off on exactly one channel. Nobody measures that today.

### 9. `proof` — the receipts, as few as possible
Three numbers, counting up, nothing else:

- `88.1%` against `67.2%` — exact strands at six reads, on 1,996 held-out real clusters
- `0.8M` parameters — trained in 13 minutes
- `$50` — the whole pipeline runs on a microcontroller

### 10. `close` — the ask
> Erbgut
> Read the code · Open the pitch deck · [TEAM: names and contact]

---

## 3. Numbers you may use

These and no others. Provenance is `docs/NUMBERS.md`; do not round them differently.

| Number | Meaning |
|---|---|
| 67.2% ±0.8 | baseline exact strands, 6 reads, held-out real Nanopore |
| 88.1% ±0.5 | our decoder, same setting |
| 1,996 | held-out clusters, 20 independent read draws |
| 1,239 | strands for the 20 KB test file at default settings |
| 0.8M / 63k | polisher / risk model parameters |
| 13 minutes | polisher training time on the GX10 |
| 19.5 → 24.5 | Nanopore reads per strand with / without the run rule |
| 3.0 → 4.0 | Illumina reads per strand with / without the GC rule |
| 229 / 211 | exact strands of 300 on the microcontroller, ours / classic |

Never invent a market size, a TAM, or a price for DNA storage.
