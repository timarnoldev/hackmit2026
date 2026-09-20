/* Beat 8 — audit. The product.
 *
 * The point of this scene is that there is a *set* of settings we hand the
 * encoder, and we measure them. So the scene is a rack of the real knobs from
 * dnacodec/types.py `EncoderSettings`: strand length, redundancy, candidates
 * per slot, and which scorer ranks those candidates. They are swept across
 * their range — the search — and then settle on the value the loop runs.
 *
 * Under them sit the two hard rules everyone ships, and those are the only
 * things in this scene that get a verdict, because they are the only things we
 * measured. The table is fixed by site/SCROLLY.md section 8 and measured in
 * docs/NUMBERS.md section 5 ("the rule audit, strict measurement", 3 blocks of
 * 300 held-out trials):
 *
 *                   Nanopore                 Illumina
 *   no run over 3   pays off, 19.5 / 24.5    no effect
 *   GC 40 to 60%    no effect                pays off, 3.0 / 4.0
 *
 * Do not flip it, and do not give the four swept knobs a verdict: a settled
 * value is a setting, not a measured benefit. Every value shown is a default
 * from `EncoderSettings` and appears in docs/NUMBERS.md (110 bases, section 1;
 * redundancy 0.3, section 4; 8 candidates, section 5).
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
const TBD_LINE = '#A9B81C';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

/* The continuous knobs. lo/hi is the range the sweep shows, `at` is where the
 * loop settles, which is the EncoderSettings default. */
const KNOBS = [
  { label: 'strand length', short: 'strand length', lo: 70, hi: 160, at: 110, fmt: (v) => Math.round(v) + '', phase: 0.00 },
  { label: 'redundancy', short: 'redundancy', lo: 0.05, hi: 0.80, at: 0.30, fmt: (v) => v.toFixed(2), phase: 0.37 },
  { label: 'candidates per slot', short: 'candidates/slot', lo: 1, hi: 32, at: 8, fmt: (v) => Math.round(v) + '', phase: 0.71 },
];
/* The fourth knob is a choice, not a range: which scorer ranks the candidates. */
const SCORER = ['hand rules', '+ learned'];

const CHANNELS = ['Nanopore', 'Illumina'];
const RULES = ['no run over 3', 'GC 40 to 60%'];
const PAYS = [[true, false], [false, true]];
const DETAIL = [
  ['19.5 reads with · 24.5 without', ''],
  ['', '3.0 reads with · 4.0 without'],
];

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

function rgb(h) {
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}
function mix(a, b, t) {
  const A = rgb(a), B = rgb(b);
  return 'rgb(' + Math.round(lerp(A[0], B[0], t)) + ',' + Math.round(lerp(A[1], B[1], t)) + ',' + Math.round(lerp(A[2], B[2], t)) + ')';
}

/* a glyph plus a word, centred as one lockup. Instrument Sans runs about
 * 0.53 em per character at these weights, which is close enough to centre by. */
function metrics(cx, size, word) {
  const glyphD = size * 0.95, gap = size * 0.44;
  const textW = word.length * size * 0.53;
  const start = cx - (glyphD + gap + textW) / 2;
  return { glyphX: start + glyphD / 2, textX: start + glyphD + gap };
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

/* The art box is close to square at every width (831x768 on a desktop stage,
 * 354x374 on a phone), so the layout switches on width, not on aspect. */
function layout(mode) {
  if (mode === 'narrow') {
    return {
      vw: 420, vh: 452, narrow: true,
      knobY: [30, 66, 102], scorerY: 140,
      labX: 12, trackX: 166, trackW: 124, valX: 306,
      knobSize: 14, valSize: 14,
      divY: 172,
      /* rules stack: each rule is a label row plus one full-width row per
         channel, because two columns cannot hold a caption at 354px. */
      ruleY: [200, 330], rowGap: 42, rowH: 34, rowX: 12, rowW: 396,
      swX: 12, ruleSize: 15,
      verdictSize: 14, detailSize: 10.5, chanSize: 12, scorerW: 176, scorerText: 11,
      swW: 28, swH: 16,
    };
  }
  return {
    vw: 1200, vh: 1080, narrow: false,
    knobY: [110, 200, 290], scorerY: 384,
    labX: 54, trackX: 470, trackW: 430, valX: 942,
    knobSize: 27, valSize: 27,
    divY: 452,
    headY: 528, colX: [630, 970], colW: 320,
    ruleY: [566, 800], ruleH: 196, swX: 54, ruleSize: 27,
    verdictSize: 24, detailSize: 15, chanSize: 21, scorerW: 430, scorerText: 19,
    swW: 54, swH: 29,
  };
}

/* ── pieces ───────────────────────────────────────────────────────────── */

function toggle(L, parent, x, y) {
  const w = L.swW, h = L.swH;
  const g = e('g', null, parent);
  const track = e('rect', { x: x, y: y - h / 2, width: w, height: h, rx: h / 2, fill: RULE }, g);
  const knob = e('circle', { cx: x + h / 2, cy: y, r: h / 2 - h * 0.15, fill: SURFACE, stroke: RULE, 'stroke-width': 1 }, g);
  return { track, knob, x, y, w, h };
}

function verdictRow(L, parent, cy, channel, pays, detail) {
  const g = e('g', null, parent);
  const box = e('rect', {
    x: L.rowX, y: cy - L.rowH / 2, width: L.rowW, height: L.rowH, rx: 9,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 1.2, 'stroke-dasharray': '4 5',
  }, g);
  const ct = e('text', {
    x: L.rowX + 12, y: cy + L.chanSize * 0.35, 'font-family': MONO,
    'font-size': L.chanSize, fill: AUDIT,
  }, g);
  ct.textContent = channel;

  const vx = L.rowX + 104;
  const pend = e('g', null, g);
  e('circle', {
    cx: vx + L.verdictSize * 0.45, cy: cy - 1, r: L.verdictSize * 0.40,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 1.6, 'stroke-dasharray': '3 4',
  }, pend);
  const pt = e('text', {
    x: vx + L.verdictSize * 1.35, y: cy + L.verdictSize * 0.35, 'font-family': SANS,
    'font-size': L.verdictSize, 'font-weight': '500', fill: '#6E6200',
  }, pend);
  pt.textContent = 'measuring';

  const res = e('g', { opacity: '0' }, g);
  const gr = L.verdictSize * 0.40;
  const gx = vx + L.verdictSize * 0.45;
  if (pays) {
    e('path', {
      d: 'M' + (gx - gr) + ' ' + (cy - 1 - gr * 0.5)
        + ' l' + (gr * 0.75) + ' ' + (gr * 0.8) + ' l' + (gr * 1.3) + ' ' + (-gr * 1.55),
      fill: 'none', stroke: GAIN, 'stroke-width': 2.2,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }, res);
  } else {
    e('line', {
      x1: gx - gr, y1: cy - 1 - gr * 0.55, x2: gx + gr, y2: cy - 1 - gr * 0.55,
      stroke: MUTED, 'stroke-width': 2.2, 'stroke-linecap': 'round',
    }, res);
  }
  const vt = e('text', {
    x: vx + L.verdictSize * 1.35, y: cy + L.verdictSize * 0.35, 'font-family': SANS,
    'font-size': L.verdictSize, 'font-weight': pays ? '600' : '400',
    fill: pays ? GAIN : MUTED,
  }, res);
  vt.textContent = pays ? 'pays off' : 'no effect';

  if (detail) {
    const dt = e('text', {
      x: L.rowX + L.rowW - 10, y: cy + L.detailSize * 0.35, 'text-anchor': 'end',
      'font-family': MONO, 'font-size': L.detailSize, fill: MUTED,
    }, res);
    dt.textContent = detail;
  }
  return { g: g, box: box, pend: pend, res: res, cx: L.rowX + L.rowW / 2, cy: cy, pays: pays };
}

function verdict(L, parent, cx, cy, pays, detail) {
  const g = e('g', null, parent);
  const w = L.colW, h = L.narrow ? 76 : 132;
  const box = e('rect', {
    x: cx - w / 2, y: cy - h / 2, width: w, height: h, rx: L.narrow ? 11 : 16,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 1.3, 'stroke-dasharray': '5 6',
  }, g);

  /* pending: dashed outline plus the word, never colour alone */
  const pend = e('g', null, g);
  const pm = metrics(cx, L.verdictSize, 'measuring');
  e('circle', {
    cx: pm.glyphX, cy: cy - L.verdictSize * 0.32, r: L.verdictSize * 0.42,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 1.8, 'stroke-dasharray': '3 4',
  }, pend);
  const pt = e('text', {
    x: pm.textX, y: cy + L.verdictSize * 0.34, 'font-family': SANS,
    'font-size': L.verdictSize, 'font-weight': '500', fill: '#6E6200',
  }, pend);
  pt.textContent = 'measuring';

  /* resolved */
  const res = e('g', { opacity: '0' }, g);
  const word = pays ? 'pays off' : 'no effect';
  const vy = cy + (detail ? -L.verdictSize * 0.22 : L.verdictSize * 0.34);
  const m = metrics(cx, L.verdictSize, word);
  const gr = L.verdictSize * 0.42;
  if (pays) {
    e('path', {
      d: 'M' + (m.glyphX - gr) + ' ' + (vy - gr * 0.5)
        + ' l' + (gr * 0.75) + ' ' + (gr * 0.8) + ' l' + (gr * 1.3) + ' ' + (-gr * 1.55),
      fill: 'none', stroke: GAIN, 'stroke-width': L.narrow ? 2.2 : 2.8,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }, res);
  } else {
    e('line', {
      x1: m.glyphX - gr, y1: vy - gr * 0.55, x2: m.glyphX + gr, y2: vy - gr * 0.55,
      stroke: MUTED, 'stroke-width': L.narrow ? 2.2 : 2.8, 'stroke-linecap': 'round',
    }, res);
  }
  const vt = e('text', {
    x: m.textX, y: vy, 'font-family': SANS, 'font-size': L.verdictSize,
    'font-weight': pays ? '600' : '400', fill: pays ? GAIN : MUTED,
  }, res);
  vt.textContent = word;

  if (detail) {
    const dt = e('text', {
      x: cx, y: cy + L.verdictSize * 1.25, 'text-anchor': 'middle',
      'font-family': MONO, 'font-size': L.detailSize, fill: MUTED,
    }, res);
    dt.textContent = detail;
  }
  return { g, box, pend, res, cx, cy, pays };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const defs = e('defs', null, svg);
  const glow = e('radialGradient', { id: 'eg-audit-glow', cx: '0.5', cy: '0.5', r: '0.5' }, defs);
  e('stop', { offset: '0', 'stop-color': GAIN, 'stop-opacity': '0.16' }, glow);
  e('stop', { offset: '1', 'stop-color': GAIN, 'stop-opacity': '0' }, glow);

  /* ── the rack of settings ──────────────────────────────────────────── */
  const rack = e('g', null, svg);
  const knobs = KNOBS.map((spec, i) => {
    const y = L.knobY[i];
    const lab = e('text', {
      x: L.labX, y: y + L.knobSize * 0.35, 'font-family': MONO,
      'font-size': L.knobSize, fill: INK,
    }, rack);
    lab.textContent = L.narrow ? spec.short : spec.label;

    const th = L.narrow ? 4 : 6;
    e('rect', { x: L.trackX, y: y - th / 2, width: L.trackW, height: th, rx: th / 2, fill: RULE }, rack);
    const fill = e('rect', { x: L.trackX, y: y - th / 2, width: 0, height: th, rx: th / 2, fill: AUDIT, 'fill-opacity': '0.45' }, rack);
    const capW = L.narrow ? 9 : 15, capH = L.narrow ? 20 : 32;
    const cap = e('rect', {
      x: 0, y: y - capH / 2, width: capW, height: capH, rx: capW / 2,
      fill: AUDIT,
    }, rack);
    const val = e('text', {
      x: L.valX, y: y + L.valSize * 0.35, 'font-family': MONO,
      'font-size': L.valSize, 'font-weight': '600', fill: MUTED,
    }, rack);
    val.textContent = spec.fmt(spec.at);
    return { spec, y, fill, cap, capW, val, lab };
  });

  /* the scorer: a choice, so a two-position segmented control */
  const scY = L.scorerY;
  const scLab = e('text', {
    x: L.labX, y: scY + L.knobSize * 0.35, 'font-family': MONO,
    'font-size': L.knobSize, fill: INK,
  }, rack);
  scLab.textContent = 'scorer';
  const segH = L.narrow ? 26 : 44;
  const segW = L.scorerW / 2;
  e('rect', {
    x: L.trackX, y: scY - segH / 2, width: L.scorerW, height: segH, rx: segH / 2,
    fill: 'none', stroke: RULE, 'stroke-width': 1.2,
  }, rack);
  const segSel = e('rect', {
    x: L.trackX, y: scY - segH / 2, width: segW, height: segH, rx: segH / 2,
    fill: AUDIT, 'fill-opacity': '0.14', stroke: AUDIT, 'stroke-width': 1.4,
  }, rack);
  const segT = SCORER.map((t, i) => {
    const tx = e('text', {
      x: L.trackX + segW * (i + 0.5), y: scY + L.detailSize * 0.38, 'text-anchor': 'middle',
      'font-family': MONO, 'font-size': L.scorerText, fill: MUTED,
    }, rack);
    tx.textContent = t;
    return tx;
  });

  e('line', {
    x1: L.labX, y1: L.divY, x2: L.vw - L.labX, y2: L.divY,
    stroke: RULE, 'stroke-width': 1.2,
  }, svg);

  /* ── the two rules, the only things here with a verdict ────────────── */
  const switches = [], labels = [], cells = [], glows = [];

  if (!L.narrow) {
    for (let c = 0; c < 2; c++) {
      const t = e('text', {
        x: L.colX[c], y: L.headY, 'text-anchor': 'middle', 'font-family': SANS,
        'font-size': L.chanSize, 'font-weight': '600', fill: AUDIT,
      }, svg);
      t.textContent = CHANNELS[c];
      e('line', {
        x1: L.colX[c] - L.colW / 2, y1: L.headY + L.chanSize * 0.75,
        x2: L.colX[c] + L.colW / 2, y2: L.headY + L.chanSize * 0.75,
        stroke: AUDIT, 'stroke-width': 1.3, 'stroke-opacity': '0.34',
      }, svg);
    }
  }

  for (let r = 0; r < 2; r++) {
    const top = L.ruleY[r];
    const cy = L.narrow ? top : top + L.ruleH / 2;
    const sw = toggle(L, svg, L.swX, cy);
    switches.push(sw);
    const lt = e('text', {
      x: L.swX + sw.w + (L.narrow ? 10 : 18), y: cy + L.ruleSize * 0.35,
      'font-family': MONO, 'font-size': L.ruleSize, fill: INK,
    }, svg);
    lt.textContent = RULES[r];
    labels.push(lt);

    const row = [];
    for (let c = 0; c < 2; c++) {
      if (L.narrow) {
        const ry = top + L.rowGap + c * (L.rowH + 10);
        const gl = e('rect', {
          x: L.rowX, y: ry - L.rowH / 2, width: L.rowW, height: L.rowH, rx: 9,
          fill: GAIN, opacity: '0',
        }, svg);
        glows.push(gl);
        row.push(verdictRow(L, svg, ry, CHANNELS[c], PAYS[r][c], DETAIL[r][c]));
      } else {
        const gl = e('ellipse', {
          cx: L.colX[c], cy: cy, rx: L.colW * 0.75, ry: L.ruleH * 0.85,
          fill: 'url(#eg-audit-glow)', opacity: '0',
        }, svg);
        glows.push(gl);
        row.push(verdict(L, svg, L.colX[c], cy, PAYS[r][c], DETAIL[r][c]));
      }
    }
    cells.push(row);
  }

  S.parts = { rack, knobs, segSel, segT, segW, scLab, switches, labels, cells, glows };
}

/* ── timing ───────────────────────────────────────────────────────────── */

const SWEEP_END = 0.30;                                  /* the search runs to here */
const SETTLE = [[0.22, 0.34], [0.26, 0.38], [0.30, 0.42]];
const SCORER_SETTLE = [0.34, 0.46];
const SWITCH_T = [[0.44, 0.52], [0.62, 0.70]];
const CELL_T = [[[0.50, 0.62], [0.56, 0.68]], [[0.68, 0.80], [0.74, 0.86]]];

function render(p) {
  const L = S.L, P = S.parts;
  if (!P) return;

  const punch = smooth(seg(p, 0.88, 1.0));

  /* --- the knobs sweep their range, then settle ----------------------- */
  P.knobs.forEach((k, i) => {
    const target = (k.spec.at - k.spec.lo) / (k.spec.hi - k.spec.lo);
    const u = seg(p, 0, SWEEP_END);
    /* a deterministic back-and-forth: the same p always gives the same frame */
    const osc = 0.5 + 0.45 * Math.sin((u * 2.6 + k.spec.phase) * 2 * Math.PI);
    const done = smooth(seg(p, SETTLE[i][0], SETTLE[i][1]));
    const pos = S.reduced ? target : lerp(osc, target, done);

    const x = L.trackX + pos * (L.trackW - k.capW);
    k.cap.setAttribute('x', x);
    k.fill.setAttribute('width', Math.max(0, x + k.capW / 2 - L.trackX));
    k.val.textContent = k.spec.fmt(lerp(k.spec.lo, k.spec.hi, pos));
    k.val.setAttribute('fill', mix(MUTED, INK, done));
  });

  /* --- the scorer flips, then settles on the learned one -------------- */
  const su = seg(p, 0, SCORER_SETTLE[0]);
  const flip = Math.sin(su * 3.0 * 2 * Math.PI) > 0 ? 1 : 0;
  const sDone = smooth(seg(p, SCORER_SETTLE[0], SCORER_SETTLE[1]));
  const sPos = S.reduced ? 1 : lerp(flip, 1, sDone);
  P.segSel.setAttribute('x', L.trackX + sPos * P.segW);
  P.segT.forEach((t, i) => {
    const on = Math.abs(sPos - i) < 0.5;
    t.setAttribute('fill', on ? AUDIT : MUTED);
    t.setAttribute('font-weight', on ? '600' : '400');
  });
  /* the rack recedes at the punchline, but the settled values stay readable */
  P.rack.setAttribute('opacity', (1 - 0.22 * punch).toFixed(3));

  /* --- the two rules get measured ------------------------------------- */
  for (let r = 0; r < 2; r++) {
    const on = smooth(seg(p, SWITCH_T[r][0], SWITCH_T[r][1]));
    const sw = P.switches[r];
    sw.track.setAttribute('fill', mix(RULE, AUDIT, on));
    sw.knob.setAttribute('cx', lerp(sw.x + sw.h / 2, sw.x + sw.w - sw.h / 2, on));
    sw.knob.setAttribute('stroke', mix(RULE, AUDIT, on));
    P.labels[r].setAttribute('fill', mix(MUTED, INK, 0.35 + 0.65 * on));

    for (let c = 0; c < 2; c++) {
      const cl = P.cells[r][c];
      const t = smooth(seg(p, CELL_T[r][c][0], CELL_T[r][c][1]));
      const pays = PAYS[r][c];

      cl.pend.setAttribute('opacity', 1 - t);
      cl.res.setAttribute('opacity', t);
      cl.box.setAttribute('stroke', mix(TBD_LINE, pays ? GAIN : RULE, t));
      cl.box.setAttribute('stroke-dasharray', t > 0.55 ? 'none' : (L.narrow ? '4 5' : '5 6'));
      cl.box.setAttribute('stroke-width', 1.2 + (pays ? 0.8 : -0.4) * t);
      cl.box.setAttribute('fill', t < 0.02 ? 'none' : (pays ? 'rgba(10,122,65,0.08)' : SURFACE));
      cl.box.setAttribute('fill-opacity', pays ? t : t * 0.5);

      /* the punchline: the diagonal lifts, the rest recedes */
      const sc = 1 + 0.03 * (pays ? punch : 0);
      cl.g.setAttribute('transform',
        'translate(' + (cl.cx * (1 - sc)) + ',' + (cl.cy * (1 - sc)) + ') scale(' + sc.toFixed(4) + ')');
      cl.g.setAttribute('opacity', pays ? 1 : 1 - 0.32 * punch);
      P.glows[r * 2 + c].setAttribute('opacity', pays ? punch * (L.narrow ? 0.09 : 1) : 0);
    }
  }
}

export default {
  id: 'audit',

  mount(root, ctx) {
    S.root = root;
    S.ctx = ctx || {};
    red();
    S.svg = e('svg', {
      xmlns: NS, viewBox: '0 0 1200 1080', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label':
        'The settings handed to the encoder are searched: strand length settles at 110, redundancy at 0.30, eight candidates per slot, and the learned scorer ranks them. Then the two hard rules are measured per channel. No run over 3 pays off on Nanopore, 19.5 reads with it against 24.5 without, and has no effect on Illumina. GC 40 to 60 percent has no effect on Nanopore and pays off on Illumina, 3.0 reads with it against 4.0 without.',
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
    const mode = w < 620 ? 'narrow' : 'wide';
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
