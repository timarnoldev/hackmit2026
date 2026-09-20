/* Beat 8 — audit. The product.
 *
 * Two rule switches against two channels. Each cell starts as a pending
 * placeholder, then resolves to a verdict, and the verdicts disagree across
 * channels. The punchline is the diagonal: each standard rule pays off on
 * exactly one channel.
 *
 * The table is fixed by site/SCROLLY.md section 8 and measured in
 * docs/NUMBERS.md section 5 ("the rule audit, strict measurement", 3 blocks of
 * 300 held-out trials):
 *
 *                   Nanopore                 Illumina
 *   no run over 3   pays off, 19.5 / 24.5    no effect
 *   GC 40 to 60%    no effect                pays off, 3.0 / 4.0
 *
 * Do not flip it.
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
const TBD_INK = '#6E6200';
const TBD_LINE = '#A9B81C';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

/* rows = rules, cols = channels. pays[r][c] is the measured verdict. */
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
      vw: 720, vh: 880, narrow: true, stacked: true,
      colX: [44, 374], colW: 302,
      cellY: [156, 486], cellH: 178,
      switchY: [116, 446], switchX: 44, ruleSize: 21,
      verdictSize: 19, detailSize: 13, chanSize: 16, chanY: 64,
    };
  }
  return {
    vw: 1200, vh: 700, narrow: false, stacked: false,
    colX: [452, 826], colW: 346,
    cellY: [206, 386], cellH: 158,
    switchY: [285, 465], switchX: 86, ruleSize: 24,
    verdictSize: 21, detailSize: 15, chanSize: 20, chanY: 150,
  };
}

function toggle(L, parent, x, y) {
  const w = L.narrow ? 46 : 52, h = L.narrow ? 25 : 28;
  const g = e('g', null, parent);
  const track = e('rect', { x: x, y: y - h / 2, width: w, height: h, rx: h / 2, fill: RULE }, g);
  const knob = e('circle', { cx: x + h / 2, cy: y, r: h / 2 - 4, fill: SURFACE, stroke: RULE, 'stroke-width': 1 }, g);
  return { track, knob, x, y, w, h };
}

/* a glyph plus a word, centred as one lockup. Instrument Sans runs about
 * 0.53 em per character at these weights, which is close enough to centre by. */
function metrics(cx, size, word) {
  const glyphD = size * 0.95, gap = size * 0.5;
  const textW = word.length * size * 0.53;
  const start = cx - (glyphD + gap + textW) / 2;
  return { glyphX: start + glyphD / 2, textX: start + glyphD + gap };
}

function cell(L, parent, x, y, pays, detail) {
  const g = e('g', null, parent);
  const box = e('rect', {
    x: x, y: y, width: L.colW, height: L.cellH, rx: 16,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 1.4, 'stroke-dasharray': '6 7',
  }, g);

  const cx = x + L.colW / 2;

  /* pending: dashed outline plus the word, never colour alone */
  const pend = e('g', null, g);
  const pm = metrics(cx, L.verdictSize, 'measuring');
  e('circle', {
    cx: pm.glyphX, cy: y + L.cellH / 2 - 6, r: L.verdictSize * 0.44,
    fill: 'none', stroke: TBD_LINE, 'stroke-width': 2, 'stroke-dasharray': '3 4',
  }, pend);
  const pt = e('text', {
    x: pm.textX, y: y + L.cellH / 2 + 1, 'font-family': SANS,
    'font-size': L.verdictSize, 'font-weight': '500', fill: TBD_INK,
  }, pend);
  pt.textContent = 'measuring';

  /* resolved */
  const res = e('g', { opacity: '0' }, g);
  const col = pays ? GAIN : MUTED;
  const word = pays ? 'pays off' : 'no effect';
  const vy = y + (detail ? L.cellH / 2 - 9 : L.cellH / 2 + 1);
  const m = metrics(cx, L.verdictSize, word);
  const gr = L.verdictSize * 0.44;
  if (pays) {
    e('path', {
      d: 'M' + (m.glyphX - gr) + ' ' + (vy - gr * 0.5)
        + ' l' + (gr * 0.75) + ' ' + (gr * 0.8)
        + ' l' + (gr * 1.3) + ' ' + (-gr * 1.55),
      fill: 'none', stroke: GAIN, 'stroke-width': 2.8,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    }, res);
  } else {
    e('line', {
      x1: m.glyphX - gr, y1: vy - gr * 0.55, x2: m.glyphX + gr, y2: vy - gr * 0.55,
      stroke: MUTED, 'stroke-width': 2.8, 'stroke-linecap': 'round',
    }, res);
  }
  const vt = e('text', {
    x: m.textX, y: vy, 'font-family': SANS,
    'font-size': L.verdictSize, 'font-weight': pays ? '600' : '400', fill: col,
  }, res);
  vt.textContent = word;

  let dt = null;
  if (detail) {
    dt = e('text', {
      x: cx, y: y + L.cellH / 2 + 30, 'text-anchor': 'middle',
      'font-family': MONO, 'font-size': L.detailSize, fill: MUTED,
    }, res);
    dt.textContent = detail;
  }

  return { g, box, pend, res, x, y, cx, cy: y + L.cellH / 2 };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const defs = e('defs', null, svg);
  const glow = e('radialGradient', { id: 'eg-audit-glow', cx: '0.5', cy: '0.5', r: '0.5' }, defs);
  e('stop', { offset: '0', 'stop-color': GAIN, 'stop-opacity': '0.16' }, glow);
  e('stop', { offset: '1', 'stop-color': GAIN, 'stop-opacity': '0' }, glow);

  /* channel headers */
  for (let c = 0; c < 2; c++) {
    const g = e('g', null, svg);
    const t = e('text', {
      x: L.colX[c] + L.colW / 2, y: L.chanY, 'text-anchor': 'middle',
      'font-family': SANS, 'font-size': L.chanSize, 'font-weight': '600',
      'letter-spacing': '0.01em', fill: AUDIT,
    }, g);
    t.textContent = CHANNELS[c];
    e('line', {
      x1: L.colX[c], y1: L.chanY + 16, x2: L.colX[c] + L.colW, y2: L.chanY + 16,
      stroke: AUDIT, 'stroke-width': 1.4, 'stroke-opacity': '0.34',
    }, g);
  }

  /* rule rows */
  const switches = [];
  const labels = [];
  const cells = [];
  const glows = [];
  for (let r = 0; r < 2; r++) {
    const sw = toggle(L, svg, L.switchX, L.switchY[r]);
    switches.push(sw);
    const lt = e('text', {
      x: L.switchX + sw.w + 18, y: L.switchY[r] + L.ruleSize * 0.35,
      'font-family': MONO, 'font-size': L.ruleSize, 'font-weight': '400', fill: INK,
    }, svg);
    lt.textContent = RULES[r];
    labels.push(lt);

    const row = [];
    for (let c = 0; c < 2; c++) {
      const y = L.stacked ? L.cellY[r] : L.cellY[r];
      const gl = e('ellipse', {
        cx: L.colX[c] + L.colW / 2, cy: y + L.cellH / 2,
        rx: L.colW * 0.72, ry: L.cellH * 0.9,
        fill: 'url(#eg-audit-glow)', opacity: '0',
      }, svg);
      glows.push(gl);
      row.push(cell(L, svg, L.colX[c], y, PAYS[r][c], DETAIL[r][c]));
    }
    cells.push(row);
  }

  S.parts = { switches, labels, cells, glows };
}

/* per-cell timing: the switch flips, then its two channels answer */
const CELL_T = [[[0.18, 0.36], [0.28, 0.46]], [[0.54, 0.72], [0.64, 0.82]]];
const SWITCH_T = [[0.08, 0.18], [0.44, 0.54]];

function render(p) {
  const L = S.L, P = S.parts;
  if (!P) return;


  const punch = smooth(seg(p, 0.84, 1.0));

  for (let r = 0; r < 2; r++) {
    /* the switch */
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
      cl.box.setAttribute('stroke-dasharray', t > 0.55 ? 'none' : '6 7');
      cl.box.setAttribute('stroke-width', 1.4 + (pays ? 0.7 : -0.4) * t);
      cl.box.setAttribute('fill', t < 0.02 ? 'none' : (pays ? 'rgba(10,122,65,0.07)' : SURFACE));
      cl.box.setAttribute('fill-opacity', pays ? t : t * 0.55);

      /* the punchline: the diagonal lifts, the rest recedes */
      const lift = pays ? punch : 0;
      const sc = 1 + 0.028 * lift;
      cl.g.setAttribute('transform',
        'translate(' + (cl.cx * (1 - sc)) + ',' + (cl.cy * (1 - sc)) + ') scale(' + sc.toFixed(4) + ')');
      cl.g.setAttribute('opacity', pays ? 1 : 1 - 0.55 * punch);
      P.glows[r * 2 + c].setAttribute('opacity', pays ? punch : 0);
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
      xmlns: NS, viewBox: '0 0 1200 700', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label':
        'Two coding rules measured on two channels. No run over 3 pays off on Nanopore, 19.5 reads with it against 24.5 without, and has no effect on Illumina. GC 40 to 60 percent has no effect on Nanopore and pays off on Illumina, 3.0 reads with it against 4.0 without.',
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
    const mode = w / Math.max(h, 1) < 1.15 ? 'narrow' : 'wide';
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
