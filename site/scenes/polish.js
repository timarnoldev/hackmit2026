/* Beat 6 — polish. The money beat.
 *
 * A band sweeps the consensus strand left to right. Where the vote got a letter
 * wrong, the band flips it to the correct letter in the gain colour. A few stay
 * wrong: roughly one strand in eight still fails, and the honest picture is the
 * better one. The headline climbs 67.2% -> 88.1% as the band travels.
 *
 * Numbers: docs/NUMBERS.md section 1. 67.2% baseline / 88.1% ours, exact strands
 * at six reads per strand on 1,996 held-out real Nanopore clusters. The share of
 * marked letters mirrors the measured failure shares (32.8% -> 11.9%); it is a
 * picture of strands, which is what the track under the number is labelled with.
 *
 * Contract: site/SCROLLY.md section 1. update(p) is pure, no timers, no rAF.
 */

const NS = 'http://www.w3.org/2000/svg';

/* marketing/BRAND.md section 9, light column, darker text step. */
const INK = '#08130B';
const MUTED = '#3E6B52';
const RULE = '#C3D9C9';
const SURFACE = '#FFFFFF';
const PAPER = '#EAF3ED';
const GAIN = '#0A7A41';
const COST = '#C41477';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

const BASE_SEQ = 'ACGTTGACCATGGCTAAGCTTGCAAGTCCTGATCAGGTACCTTGAACGATGCCAGTTACGGATCTAGCATTGCAC';

function e(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1);
const lerp = (a, b, t) => a + (b - a) * t;
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const smooth = (t) => t * t * (3 - 2 * t);

function rgb(h) {
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}
function mix(a, b, t) {
  const A = rgb(a), B = rgb(b);
  return 'rgb(' + Math.round(lerp(A[0], B[0], t)) + ',' + Math.round(lerp(A[1], B[1], t)) + ',' + Math.round(lerp(A[2], B[2], t)) + ')';
}

/* Which letters the vote got wrong, and which of those the model cannot save.
 * Deterministic for a given n, evenly spread, ~34% wrong and ~11% left wrong. */
function faults(n) {
  const gaps = [3, 2, 4, 3, 2, 3, 4, 2];
  const wrong = [];
  let i = 2, g = 0;
  while (i < n - 1) { wrong.push(i); i += gaps[g++ % gaps.length]; }
  const stays = new Set();
  for (let k = 1; k < wrong.length; k += 3) stays.add(wrong[k]);
  return { wrong, stays };
}

const S = {
  root: null, svg: null, ctx: null, reduced: false, mode: '', p: 0, L: null, parts: null,
};

/* The shell hands the same ctx object to every scene and mutates ctx.reduced
 * when the setting changes, so read it per frame rather than latching it. */
function red() {
  S.reduced = !!(S.ctx && S.ctx.reduced);
  return S.reduced;
}

function layout(mode) {
  if (mode === 'narrow') {
    return {
      vw: 720, vh: 900, n: 26, step: 22, letter: 27, mark: 8,
      numY: 208, numSize: 116, trackY: 268, trackX: 96, trackW: 528,
      cardY: 470, cardH: 150, strandY: 552, legendY: 700, band: 86,
    };
  }
  return {
    vw: 1200, vh: 700, n: 44, step: 24, letter: 30, mark: 9,
    numY: 196, numSize: 134, trackY: 252, trackX: 236, trackW: 728,
    cardY: 372, cardH: 156, strandY: 462, legendY: 600, band: 96,
  };
}

function build() {
  const L = S.L;
  const svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const defs = e('defs', null, svg);
  const bg = e('linearGradient', { id: 'eg-polish-band', x1: '0', y1: '0', x2: '1', y2: '0' }, defs);
  e('stop', { offset: '0', 'stop-color': GAIN, 'stop-opacity': '0' }, bg);
  e('stop', { offset: '0.55', 'stop-color': GAIN, 'stop-opacity': '0.10' }, bg);
  e('stop', { offset: '0.93', 'stop-color': GAIN, 'stop-opacity': '0.26' }, bg);
  e('stop', { offset: '1', 'stop-color': GAIN, 'stop-opacity': '0.34' }, bg);

  const col = e('radialGradient', { id: 'eg-polish-col', cx: '0.5', cy: '0.5', r: '0.5' }, defs);
  e('stop', { offset: '0', 'stop-color': GAIN, 'stop-opacity': '0.20' }, col);
  e('stop', { offset: '0.45', 'stop-color': GAIN, 'stop-opacity': '0.09' }, col);
  e('stop', { offset: '1', 'stop-color': GAIN, 'stop-opacity': '0' }, col);

  const sh = e('filter', { id: 'eg-polish-shadow', x: '-12%', y: '-40%', width: '124%', height: '190%' }, defs);
  e('feDropShadow', { dx: '0', dy: '6', stdDeviation: '14', 'flood-color': '#08130B', 'flood-opacity': '0.07' }, sh);

  const n = L.n;
  const totalW = (n - 1) * L.step;
  const x0 = L.vw / 2 - totalW / 2;
  const xs = [];
  for (let i = 0; i < n; i++) xs.push(x0 + i * L.step);
  const f = faults(n);
  const wrongSet = new Set(f.wrong);

  /* ---- the headline number ------------------------------------------- */
  const num = e('text', {
    x: L.vw / 2, y: L.numY, 'text-anchor': 'middle', 'font-family': MONO,
    'font-size': L.numSize, 'font-weight': '600', 'letter-spacing': '-0.02em', fill: INK,
  }, svg);
  const numVal = e('tspan', null, num);
  numVal.textContent = '67.2';
  const numPct = e('tspan', { 'font-size': L.numSize * 0.46, dx: L.numSize * 0.04 }, num);
  numPct.textContent = '%';

  /* ---- the measure track under it ------------------------------------ */
  const tx = L.trackX, tw = L.trackW, ty = L.trackY;
  e('rect', { x: tx, y: ty, width: tw, height: 5, rx: 2.5, fill: RULE }, svg);
  const fill = e('rect', { x: tx, y: ty, width: tw * 0.672, height: 5, rx: 2.5, fill: GAIN }, svg);

  const ghostX = tx + tw * 0.672;
  e('line', { x1: ghostX, y1: ty - 9, x2: ghostX, y2: ty + 14, stroke: MUTED, 'stroke-width': 1.4, 'stroke-opacity': '0.75' }, svg);
  const ghostLbl = e('text', {
    x: ghostX, y: ty + 33, 'text-anchor': 'middle', 'font-family': MONO,
    'font-size': 14, fill: MUTED,
  }, svg);
  ghostLbl.textContent = '67.2 vote';
  const trackLbl = e('text', { x: tx, y: ty + 33, 'font-family': MONO, 'font-size': 14, fill: MUTED }, svg);
  trackLbl.textContent = 'exact strands';

  const head = e('circle', { cx: ghostX, cy: ty + 2.5, r: 7, fill: GAIN }, svg);

  /* ---- the strand card ------------------------------------------------ */
  const cardX = x0 - L.step * 1.6;
  const cardW = totalW + L.step * 3.2;
  e('rect', {
    x: cardX, y: L.cardY, width: cardW, height: L.cardH, rx: 18,
    fill: SURFACE, stroke: RULE, 'stroke-width': 1, filter: 'url(#eg-polish-shadow)',
  }, svg);

  /* the sweeping column of light behind everything */
  const column = e('ellipse', {
    cx: 0, cy: L.cardY + L.cardH / 2, rx: L.band * 2.1, ry: L.cardH * 1.15,
    fill: 'url(#eg-polish-col)', opacity: '0',
  }, svg);
  const band = e('rect', { x: 0, y: L.cardY + 1, width: L.band, height: L.cardH - 2, fill: 'url(#eg-polish-band)', opacity: '0' }, svg);
  const edge = e('rect', { x: 0, y: L.cardY + 1, width: 2.4, height: L.cardH - 2, fill: GAIN, opacity: '0' }, svg);

  /* ---- letters --------------------------------------------------------- */
  const chipLayer = e('g', null, svg);
  const letters = [];
  for (let i = 0; i < n; i++) {
    const trueCh = BASE_SEQ[i % BASE_SEQ.length];
    const g = e('g', { transform: 'translate(' + xs[i] + ',' + L.strandY + ')' }, svg);
    const t = e('text', {
      x: 0, y: 0, 'text-anchor': 'middle', 'font-family': MONO,
      'font-size': L.letter, 'font-weight': '600', fill: INK,
    }, g);
    let wrongCh = trueCh;
    if (wrongSet.has(i)) {
      const order = 'ACGT';
      wrongCh = order[(order.indexOf(trueCh) + 1 + (i % 3)) % 4];
    }
    t.textContent = wrongSet.has(i) ? wrongCh : trueCh;
    let chip = null;
    if (wrongSet.has(i)) {
      chip = e('rect', {
        x: xs[i] - L.step * 0.46, y: L.strandY - L.letter * 0.92,
        width: L.step * 0.92, height: L.letter * 1.38, rx: L.step * 0.28,
        fill: COST, opacity: '0.10',
      }, chipLayer);
    }
    letters.push({
      i, g, t, chip, x: xs[i], trueCh, wrongCh,
      wrong: wrongSet.has(i), stays: f.stays.has(i),
    });
  }

  /* underlines for the ones still wrong once the band has gone */
  const scars = [];
  for (const l of letters) {
    if (!l.stays) continue;
    const u = e('line', {
      x1: l.x - L.step * 0.34, y1: L.strandY + L.letter * 0.48,
      x2: l.x + L.step * 0.34, y2: L.strandY + L.letter * 0.48,
      stroke: COST, 'stroke-width': 2.6, 'stroke-linecap': 'round', opacity: '0',
    }, svg);
    scars.push({ l, u });
  }

  /* ---- legend ---------------------------------------------------------- */
  const legend = [];
  const mk = (cx, color, label) => {
    const g = e('g', { opacity: '0' }, svg);
    e('circle', { cx: 0, cy: -5, r: 5.5, fill: color }, g);
    const t = e('text', { x: 14, y: 0, 'font-family': SANS, 'font-size': 17, fill: MUTED }, g);
    t.textContent = label;
    g.setAttribute('transform', 'translate(' + cx + ',' + L.legendY + ')');
    legend.push(g);
    return g;
  };
  const gap = L.vw < 900 ? 150 : 185;
  mk(L.vw / 2 - gap, GAIN, 'recovered');
  mk(L.vw / 2 + (L.vw < 900 ? 85 : 105), COST, 'still wrong');

  S.parts = { num, numVal, numPct, fill, head, band, edge, column, letters, scars, legend, xs, tx, tw, ty, x0, totalW };
}

function render(p) {
  const L = S.L, P = S.parts;
  if (!P) return;

  const sweep = easeInOut(seg(p, 0.06, 0.80));
  const w = L.band;
  const left = P.x0 - L.step * 1.6;
  const right = P.x0 + P.totalW + L.step * 1.6;
  const bandX = lerp(left - w, right + w * 0.4, sweep);

  /* number and track */
  const val = lerp(67.2, 88.1, sweep);
  P.numVal.textContent = val.toFixed(1);
  const numCol = mix(INK, GAIN, smooth(clamp(sweep * 1.25, 0, 1)));
  P.num.setAttribute('fill', numCol);
  P.fill.setAttribute('width', P.tw * (val / 100));
  P.head.setAttribute('cx', P.tx + P.tw * (val / 100));

  /* the band itself */
  const alive = S.reduced ? 0 : Math.min(seg(p, 0.03, 0.10), 1 - seg(p, 0.84, 0.96));
  P.band.setAttribute('x', bandX - w);
  P.band.setAttribute('opacity', alive);
  P.edge.setAttribute('x', bandX - 1.2);
  P.edge.setAttribute('opacity', alive * 0.9);
  P.column.setAttribute('cx', bandX - w * 0.35);
  P.column.setAttribute('opacity', alive * 0.9);

  /* letters */
  const win = w * 0.95;
  for (const l of P.letters) {
    const d = bandX - l.x;
    const u = clamp((d + win * 0.45) / win, 0, 1);
    const pop = S.reduced ? 0 : Math.sin(Math.PI * u);
    const f = clamp(d / (L.step * 1.2), 0, 1);
    let dy, sc, col;
    if (l.wrong && !l.stays) {
      dy = -L.letter * 0.5 * pop;
      sc = 1 + 0.34 * pop;
      col = mix(COST, GAIN, smooth(f));
      l.t.textContent = f > 0.5 ? l.trueCh : l.wrongCh;
      l.chip.setAttribute('fill', f > 0.5 ? GAIN : COST);
      l.chip.setAttribute('opacity', 0.10 + 0.16 * pop);
    } else if (l.stays) {
      dy = -L.letter * 0.12 * pop;
      sc = 1 + 0.06 * pop;
      col = COST;
      l.t.textContent = l.wrongCh;
      l.chip.setAttribute('opacity', 0.10 + 0.05 * smooth(f));
    } else {
      dy = -L.letter * 0.34 * pop;
      sc = 1 + 0.11 * pop;
      col = mix(INK, GAIN, 0.25 * pop);
      l.t.textContent = l.trueCh;
    }
    l.g.setAttribute('transform', 'translate(' + l.x + ',' + (L.strandY + dy) + ') scale(' + sc.toFixed(3) + ')');
    l.t.setAttribute('fill', col);
  }

  for (const s of P.scars) {
    const f = clamp((bandX - s.l.x - L.step) / (L.step * 2), 0, 1);
    s.u.setAttribute('opacity', smooth(f) * 0.95);
  }

  P.legend[0].setAttribute('opacity', smooth(seg(p, 0.08, 0.22)));
  P.legend[1].setAttribute('opacity', smooth(seg(p, 0.26, 0.42)));
}

export default {
  id: 'polish',

  mount(root, ctx) {
    S.root = root;
    S.ctx = ctx || {};
    red();
    S.svg = e('svg', {
      xmlns: NS, viewBox: '0 0 1200 700', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label':
        'A band sweeps a consensus strand. Letters the vote got wrong flip to the corrected letter in green, a few stay pink, and the headline climbs from 67.2 percent to 88.1 percent of strands exactly right.',
    });
    S.svg.style.width = '100%';
    S.svg.style.height = '100%';
    S.svg.style.display = 'block';
    S.svg.style.overflow = 'visible';
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
