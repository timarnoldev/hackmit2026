/* Beat 7 — loop. The part nobody else does.
 *
 * The strands that still failed detach from the decoded field and flow backwards
 * into the encoder. The encoder then picks between two candidate strands for the
 * same droplet: the one carrying a long run of G lights up in the cost colour and
 * is rejected, the safer one is accepted.
 *
 * Grounded in docs/LEARNED_RULES.md: the risk model found runs by itself, and it
 * rates G and C runs above A and T runs. Three of twenty-four strands are shown
 * failing, which is the measured share (11.9% at six reads, docs/NUMBERS.md 1).
 *
 * Contract: site/SCROLLY.md section 1. update(p) is pure, no timers, no rAF.
 */

const NS = 'http://www.w3.org/2000/svg';

const INK = '#08130B';
const MUTED = '#3E6B52';
const RULE = '#C3D9C9';
const SURFACE = '#FFFFFF';
const AUDIT = '#0B6E82';
const GAIN = '#0A7A41';
const COST = '#C41477';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

function e(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1);
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (t) => t * t * (3 - 2 * t);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

function rgb(h) {
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}
function mix(a, b, t) {
  const A = rgb(a), B = rgb(b);
  return 'rgb(' + Math.round(lerp(A[0], B[0], t)) + ',' + Math.round(lerp(A[1], B[1], t)) + ',' + Math.round(lerp(A[2], B[2], t)) + ')';
}
function cbez(p0, c1, c2, p1, t) {
  const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
  return [a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0], a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1]];
}

const S = { root: null, svg: null, ctx: null, reduced: false, mode: '', p: 0, L: null, parts: null };

/* The shell hands the same ctx object to every scene and mutates ctx.reduced
 * when the setting changes, so read it per frame rather than latching it. */
function red() {
  S.reduced = !!(S.ctx && S.ctx.reduced);
  return S.reduced;
}

function layout(mode) {
  if (mode === 'narrow') {
    return {
      vw: 720, vh: 940, narrow: true,
      cols: 4, rows: 6, fx: 62, fy: 96, sx: 152, sy: 38, pw: 128, ph: 12,
      failed: [[0, 2], [2, 0], [4, 3]],
      encX: 240, encY: 340, encW: 240, encH: 182, grid: 4,
      pre: 'ACTT', suf: 'TACCAT', runN: 6, step: 21, letter: 23,
      rowX: 115, chipW: 150, chipH: 38, cardH: 76, row1: 620, row2: 740,
    };
  }
  return {
    vw: 1200, vh: 700, narrow: false,
    cols: 6, rows: 4, fx: 420, fy: 178, sx: 126, sy: 42, pw: 104, ph: 12,
    failed: [[0, 4], [1, 1], [3, 3]],
    encX: 96, encY: 294, encW: 252, encH: 292, grid: 5,
    pre: 'ACTTGCA', suf: 'TACCATGAT', runN: 6, step: 24, letter: 26,
    rowX: 442, chipW: 162, chipH: 40, cardH: 80, row1: 382, row2: 506,
  };
}

function candidateRow(L, svg, y, seqRun, safe) {
  const n = L.pre.length + L.runN + L.suf.length;
  const letterW = (n - 1) * L.step;
  const cardX = L.rowX - L.step * 0.9;
  const cardW = letterW + L.step * 1.8 + 26 + L.chipW;

  /* the empty slot, so the composition is whole before the candidate arrives */
  const slot = e('rect', {
    x: cardX, y: y, width: cardW, height: L.cardH, rx: 14,
    fill: 'none', stroke: RULE, 'stroke-width': 1.2, 'stroke-dasharray': '5 7',
  }, svg);

  const g = e('g', { opacity: '0' }, svg);

  const card = e('rect', {
    x: cardX, y: y, width: cardW, height: L.cardH, rx: 14,
    fill: SURFACE, stroke: RULE, 'stroke-width': 1,
  }, g);

  const baseline = y + L.cardH * 0.64;
  const runX0 = L.rowX + (L.pre.length - 0.5) * L.step;
  const runW = L.runN * L.step;

  /* the wash behind the run, only on the rejected candidate */
  const runTint = e('rect', {
    x: runX0, y: y + L.cardH * 0.13, width: runW, height: L.cardH * 0.58, rx: 8,
    fill: safe ? GAIN : COST, opacity: '0',
  }, g);

  const seq = L.pre + seqRun + L.suf;
  const chars = [];
  for (let i = 0; i < n; i++) {
    const t = e('text', {
      x: L.rowX + i * L.step, y: baseline, 'text-anchor': 'middle',
      'font-family': MONO, 'font-size': L.letter, 'font-weight': '600', fill: INK,
    }, g);
    t.textContent = seq[i];
    chars.push(t);
  }

  /* bracket under the run */
  const by = y + L.cardH * 0.86;
  const bracket = e('path', {
    d: 'M' + runX0 + ' ' + (by - 6) + ' L' + runX0 + ' ' + by + ' L' + (runX0 + runW) + ' ' + by + ' L' + (runX0 + runW) + ' ' + (by - 6),
    fill: 'none', stroke: safe ? GAIN : COST, 'stroke-width': 2,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    pathLength: '1', 'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, g);

  /* verdict chip: colour is never alone, it always carries the word */
  const chipX = cardX + cardW - L.chipW - L.step * 0.9;
  const chipY = y + (L.cardH - L.chipH) / 2;
  const chip = e('g', { opacity: '0' }, g);
  e('rect', {
    x: chipX, y: chipY, width: L.chipW, height: L.chipH, rx: L.chipH / 2,
    fill: safe ? 'rgba(10,122,65,0.12)' : 'rgba(196,20,119,0.11)',
    stroke: safe ? GAIN : COST, 'stroke-width': 1.2, 'stroke-opacity': '0.5',
  }, chip);
  const gx = chipX + L.chipH * 0.5, gy = chipY + L.chipH * 0.5;
  if (safe) {
    e('path', {
      d: 'M' + (gx - 6) + ' ' + gy + ' l4.5 5 l8 -10',
      fill: 'none', stroke: GAIN, 'stroke-width': 2.4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }, chip);
  } else {
    e('path', {
      d: 'M' + (gx - 5.5) + ' ' + (gy - 5.5) + ' l11 11 M' + (gx + 5.5) + ' ' + (gy - 5.5) + ' l-11 11',
      fill: 'none', stroke: COST, 'stroke-width': 2.4, 'stroke-linecap': 'round',
    }, chip);
  }
  const ct = e('text', {
    x: chipX + L.chipH * 0.92, y: chipY + L.chipH * 0.66,
    'font-family': SANS, 'font-size': L.narrow ? 17 : 18, 'font-weight': '500',
    fill: safe ? GAIN : COST,
  }, chip);
  ct.textContent = safe ? 'accepted' : 'rejected';

  return { g, slot, card, chars, runTint, bracket, chip, n, y, cardX, cardW, preN: L.pre.length };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const defs = e('defs', null, svg);
  const glow = e('radialGradient', { id: 'eg-loop-glow', cx: '0.5', cy: '0.5', r: '0.5' }, defs);
  e('stop', { offset: '0', 'stop-color': AUDIT, 'stop-opacity': '0.22' }, glow);
  e('stop', { offset: '1', 'stop-color': AUDIT, 'stop-opacity': '0' }, glow);

  /* paths live under the field, so a pill always occludes the line */
  const pathLayer = e('g', null, svg);

  /* ---- the decoded field ---------------------------------------------- */
  const failKey = new Set(L.failed.map((f) => f[0] + ':' + f[1]));
  const pills = [];
  const fieldLayer = e('g', null, svg);
  for (let r = 0; r < L.rows; r++) {
    for (let c = 0; c < L.cols; c++) {
      const x = L.fx + c * L.sx, y = L.fy + r * L.sy;
      const failed = failKey.has(r + ':' + c);
      const g = e('g', null, fieldLayer);
      const rect = e('rect', {
        x: 0, y: -L.ph / 2, width: L.pw, height: L.ph, rx: L.ph / 2,
        fill: failed ? COST : RULE,
      }, g);
      g.setAttribute('transform', 'translate(' + x + ',' + y + ')');
      pills.push({ g, rect, x, y, failed, cx: x + L.pw / 2, cy: y });
    }
  }

  /* ---- the encoder block ---------------------------------------------- */
  const encCX = L.encX + L.encW / 2, encCY = L.encY + L.encH / 2;
  const encGlow = e('ellipse', {
    cx: encCX, cy: encCY, rx: L.encW * 1.05, ry: L.encH * 1.05,
    fill: 'url(#eg-loop-glow)', opacity: '0',
  }, svg);
  const encBox = e('rect', {
    x: L.encX, y: L.encY, width: L.encW, height: L.encH, rx: 20,
    fill: SURFACE, stroke: RULE, 'stroke-width': 1.4,
  }, svg);
  e('rect', {
    x: L.encX + 13, y: L.encY + 13, width: L.encW - 26, height: L.encH - 26, rx: 12,
    fill: 'none', stroke: RULE, 'stroke-width': 1, 'stroke-opacity': '0.7',
  }, svg);

  const encLabel = e('text', {
    x: L.encX + 22, y: L.encY + 34,
    'font-family': MONO, 'font-size': L.narrow ? 16 : 17, fill: MUTED,
  }, svg);
  encLabel.textContent = 'encoder';

  const dots = [];
  const gridN = L.grid;
  const padT = 62, padB = 38, padX = 40;
  const gw = L.encW - padX * 2, gh = L.encH - padT - padB;
  for (let r = 0; r < gridN; r++) {
    for (let c = 0; c < gridN; c++) {
      dots.push(e('circle', {
        cx: L.encX + padX + (gw / (gridN - 1)) * c,
        cy: L.encY + padT + (gh / (gridN - 1)) * r,
        r: 4.2, fill: RULE,
      }, svg));
    }
  }

  /* ---- the backward paths --------------------------------------------- */
  const flyers = [];
  const archY = L.fy - 62;
  const entryXs = [encCX - L.encW * 0.26, encCX, encCX + L.encW * 0.26];
  L.failed.forEach((f, k) => {
    const pill = pills[f[0] * L.cols + f[1]];
    const p0 = [pill.x + L.pw / 2, pill.y];
    const p1 = [entryXs[k], L.encY + 4];
    const top = L.narrow ? p1[1] - 82 : archY;
    const c1 = [p0[0], top];
    const c2 = [p1[0], top];
    const path = e('path', {
      d: 'M' + p0[0] + ' ' + p0[1] + ' C' + c1[0] + ' ' + c1[1] + ' ' + c2[0] + ' ' + c2[1] + ' ' + p1[0] + ' ' + p1[1],
      fill: 'none', stroke: AUDIT, 'stroke-width': 1.5, 'stroke-opacity': '0.4',
      'stroke-linecap': 'round', pathLength: '1',
      'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
    }, pathLayer);
    const g = e('g', { opacity: '0' }, svg);
    e('rect', { x: -L.pw / 2, y: -L.ph / 2, width: L.pw, height: L.ph, rx: L.ph / 2, fill: COST }, g);
    flyers.push({ path, g, p0, c1, c2, p1, pill, k });
  });

  /* ---- the encoder hands out two candidates --------------------------- */
  const n = L.pre.length + L.runN + L.suf.length;
  const cardX = L.rowX - L.step * 0.9;
  const cardW = (n - 1) * L.step + L.step * 1.8 + 26 + L.chipW;
  const y1 = L.row1 + L.cardH / 2, y2 = L.row2 + L.cardH / 2;
  let outD, stubA, stubB;
  if (L.narrow) {
    const bx = L.vw / 2;
    outD = 'M' + bx + ' ' + (L.encY + L.encH) + ' L' + bx + ' ' + (L.row1 - 34);
    stubA = 'M' + (cardX - 26) + ' ' + y1 + ' L' + (cardX - 6) + ' ' + y1;
    stubB = 'M' + (cardX - 26) + ' ' + y2 + ' L' + (cardX - 6) + ' ' + y2;
  } else {
    const bx = cardX - 36;
    outD = 'M' + (L.encX + L.encW) + ' ' + encCY
      + ' C' + (L.encX + L.encW + 40) + ' ' + encCY + ' ' + (bx - 34) + ' ' + encCY + ' ' + bx + ' ' + encCY
      + ' M' + bx + ' ' + y1 + ' L' + bx + ' ' + y2;
    stubA = 'M' + bx + ' ' + y1 + ' L' + (cardX - 6) + ' ' + y1;
    stubB = 'M' + bx + ' ' + y2 + ' L' + (cardX - 6) + ' ' + y2;
  }
  const outPath = e('path', {
    d: outD, fill: 'none', stroke: AUDIT, 'stroke-width': 1.8, 'stroke-opacity': '0.5',
    'stroke-linecap': 'round', pathLength: '1',
    'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, svg);
  const stubs = [stubA, stubB].map((d) => e('path', {
    d: d, fill: 'none', stroke: AUDIT, 'stroke-width': 1.8, 'stroke-opacity': '0.5',
    'stroke-linecap': 'round', pathLength: '1',
    'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, svg));
  const heads = [y1, y2].map((yy) => e('path', {
    d: 'M' + (cardX - 14) + ' ' + (yy - 6) + ' L' + (cardX - 5) + ' ' + yy + ' L' + (cardX - 14) + ' ' + (yy + 6),
    fill: 'none', stroke: AUDIT, 'stroke-width': 1.8, 'stroke-linecap': 'round',
    'stroke-linejoin': 'round', opacity: '0',
  }, svg));

  const rowA = candidateRow(L, svg, L.row1, 'G'.repeat(L.runN), false);
  const rowB = candidateRow(L, svg, L.row2, 'GTCAGT'.slice(0, L.runN), true);

  S.parts = { pills, encBox, encGlow, dots, encLabel, flyers, outPath, stubs, heads, rowA, rowB, encCX, encCY };
}

function render(p) {
  const L = S.L, P = S.parts;
  if (!P) return;

  /* --- failures detach and flow backwards ------------------------------ */
  let arrived = 0;
  P.flyers.forEach((f, k) => {
    const t0 = 0.10 + k * 0.07;
    const t = easeOut(seg(p, t0, t0 + 0.26));
    const draw = seg(p, t0 - 0.04, t0 + 0.22);
    f.path.setAttribute('stroke-dashoffset', 1 - draw);
    f.path.setAttribute('stroke-opacity', 0.40 * (1 - seg(p, 0.60, 0.80) * 0.55));

    const pt = cbez(f.p0, f.c1, f.c2, f.p1, t);
    const shrink = 1 - 0.55 * smooth(seg(t, 0.72, 1));
    f.g.setAttribute('transform', 'translate(' + pt[0] + ',' + pt[1] + ') scale(' + shrink.toFixed(3) + ',1)');
    f.g.setAttribute('opacity', t > 0.001 ? (1 - smooth(seg(t, 0.85, 1))) : 0);
    /* the slot it left behind empties out */
    f.pill.rect.setAttribute('opacity', t > 0.001 ? 0.16 : 1);
    f.pill.rect.setAttribute('fill', t > 0.001 ? RULE : COST);
    arrived += smooth(seg(t, 0.75, 1));
  });
  const lit = arrived / P.flyers.length;

  /* --- the encoder takes them in --------------------------------------- */
  P.encGlow.setAttribute('opacity', lit);
  P.encBox.setAttribute('stroke', mix(RULE, AUDIT, lit));
  P.encBox.setAttribute('stroke-width', 1.4 + 0.9 * lit);
  P.encLabel.setAttribute('fill', mix(MUTED, AUDIT, lit));
  const nd = P.dots.length;
  P.dots.forEach((d, i) => {
    const order = ((i * 7) % nd) / nd;          /* a fixed scatter, not a raster */
    const a = smooth(clamp((lit * 1.3125 - order) * 3.2, 0, 1));
    d.setAttribute('fill', mix(RULE, AUDIT, a));
    d.setAttribute('r', 4.2 + 1.5 * a);
  });

  /* --- the encoder hands out candidates -------------------------------- */
  const out = smooth(seg(p, 0.40, 0.52));
  P.outPath.setAttribute('stroke-dashoffset', 1 - out);
  P.stubs[0].setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.50, 0.56)));
  P.stubs[1].setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.74, 0.80)));
  P.heads[0].setAttribute('opacity', smooth(seg(p, 0.54, 0.60)) * 0.85);
  P.heads[1].setAttribute('opacity', smooth(seg(p, 0.78, 0.84)) * 0.85);

  /* candidate A: appears, its run lights up, it is rejected and dims */
  const aIn = smooth(seg(p, 0.48, 0.60));
  const aRun = smooth(seg(p, 0.58, 0.70));
  const aVer = smooth(seg(p, 0.68, 0.78));
  const aDim = smooth(seg(p, 0.80, 0.92));
  P.rowA.slot.setAttribute('opacity', 1 - aIn);
  P.rowB.slot.setAttribute('opacity', 1 - smooth(seg(p, 0.74, 0.86)));
  P.rowA.g.setAttribute('opacity', aIn * (1 - 0.55 * aDim));
  P.rowA.g.setAttribute('transform', 'translate(' + (1 - aIn) * -26 + ',0)');
  P.rowA.runTint.setAttribute('opacity', 0.16 * aRun);
  P.rowA.bracket.setAttribute('stroke-dashoffset', 1 - aRun);
  P.rowA.chip.setAttribute('opacity', aVer);
  P.rowA.card.setAttribute('stroke', mix(RULE, COST, 0.55 * aVer));
  for (let i = 0; i < P.rowA.n; i++) {
    const inRun = i >= P.rowA.preN && i < P.rowA.preN + L.runN;
    P.rowA.chars[i].setAttribute('fill', inRun ? mix(INK, COST, aRun) : INK);
    P.rowA.chars[i].setAttribute('font-weight', inRun && aRun > 0.5 ? '600' : '600');
  }

  /* candidate B: appears, is accepted */
  const bIn = smooth(seg(p, 0.74, 0.86));
  const bVer = smooth(seg(p, 0.88, 0.98));
  P.rowB.g.setAttribute('opacity', bIn);
  P.rowB.g.setAttribute('transform', 'translate(' + (1 - bIn) * -26 + ',0)');
  P.rowB.runTint.setAttribute('opacity', 0.14 * bVer);
  P.rowB.bracket.setAttribute('stroke-dashoffset', 1 - bVer);
  P.rowB.chip.setAttribute('opacity', bVer);
  P.rowB.card.setAttribute('stroke', mix(RULE, GAIN, 0.6 * bVer));
  P.rowB.card.setAttribute('stroke-width', 1 + 0.8 * bVer);
  for (let i = 0; i < P.rowB.n; i++) {
    const inRun = i >= P.rowB.preN && i < P.rowB.preN + L.runN;
    P.rowB.chars[i].setAttribute('fill', inRun ? mix(INK, GAIN, bVer) : INK);
  }
}

export default {
  id: 'loop',

  mount(root, ctx) {
    S.root = root;
    S.ctx = ctx || {};
    red();
    S.svg = e('svg', {
      xmlns: NS, viewBox: '0 0 1200 700', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label':
        'The strands that still failed detach from the decoded field and flow backwards into the encoder. The encoder then rejects a candidate strand carrying a long run of G and accepts a safer one.',
    });
    S.svg.style.width = '100%';
    S.svg.style.height = '100%';
    S.svg.style.display = 'block';
    root.appendChild(S.svg);
    S.mode = 'wide';
    S.L = layout(S.mode);
    build();
    render(S.reduced ? 1 : 0);
  },

  update(p) {
    S.p = p;
    render(red() ? 1 : clamp(p, 0, 1));
  },

  resize(w, h) {
    const mode = w / Math.max(h, 1) < 1.05 ? 'narrow' : 'wide';
    if (mode === S.mode) return;
    S.mode = mode;
    S.L = layout(mode);
    build();
    render(red() ? 1 : clamp(S.p, 0, 1));
  },

  unmount() {
    if (S.svg && S.svg.parentNode) S.svg.parentNode.removeChild(S.svg);
    S.svg = null; S.parts = null;
  },
};
