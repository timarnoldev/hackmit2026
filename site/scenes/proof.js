/* Beat 9 — proof. The receipts, as few as possible.
 *
 * Three numbers counting up, nothing else. Every value and every label comes
 * from site/SCROLLY.md section 9, with provenance in docs/NUMBERS.md:
 *   88.1% against 67.2%  exact strands at six reads, 1,996 held-out real clusters
 *   0.8M parameters      trained in 13 minutes
 *   229                  strands exact, decoded on the microcontroller itself
 *
 * The counters are driven by p, so scrolling back down-counts.
 *
 * Contract: site/SCROLLY.md section 1. update(p) is pure, no timers, no rAF.
 */

const NS = 'http://www.w3.org/2000/svg';

const INK = '#08130B';
const MUTED = '#3E6B52';
const AUDIT = '#0B6E82';
const GAIN = '#0A7A41';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

const COLUMNS = [
  {
    colour: GAIN, from: 0, to: 88.1,
    fmt: (v) => v.toFixed(1) + '%',
    sub: (t) => 'against ' + (67.2 * t).toFixed(1) + '%',
    lines: ['exact strands, six reads', '1,996 held-out real clusters'],
    at: [0.02, 0.44], show: [-0.20, -0.10],
  },
  {
    colour: INK, from: 0, to: 0.8,
    fmt: (v) => v.toFixed(1) + 'M',
    sub: null,
    lines: ['parameters', 'trained in 13 minutes'],
    at: [0.18, 0.62], show: [-0.20, -0.10],
  },
  {
    colour: AUDIT, from: 0, to: 229,
    fmt: (v) => String(Math.round(v)),
    sub: 'of 300, against 211 classic',
    lines: ['strands exact, decoded', 'on the microcontroller itself'],
    at: [0.34, 0.82], show: [-0.20, -0.10],
  },
];

function e(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1);
const smooth = (t) => t * t * (3 - 2 * t);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

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
      vw: 720, vh: 900, narrow: true,
      cx: [360, 360, 360], cy: [186, 470, 754],
      numSize: 88, subSize: 17, lineSize: 17, ruleW: 92,
    };
  }
  return {
    vw: 1200, vh: 700, narrow: false,
    cx: [232, 600, 968], cy: [322, 322, 322],
    numSize: 100, subSize: 19, lineSize: 18, ruleW: 108,
  };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const cols = COLUMNS.map((spec, i) => {
    const cx = L.cx[i], cy = L.cy[i];
    const g = e('g', null, svg);

    const num = e('text', {
      x: cx, y: cy, 'text-anchor': 'middle', 'font-family': MONO,
      'font-size': L.numSize, 'font-weight': '600', 'letter-spacing': '-0.02em',
      fill: spec.colour,
    }, g);
    num.textContent = spec.fmt(spec.from);

    let sub = null;
    if (spec.sub) {
      sub = e('text', {
        x: cx, y: cy + L.numSize * 0.40, 'text-anchor': 'middle',
        'font-family': MONO, 'font-size': L.subSize, fill: MUTED,
      }, g);
      sub.textContent = spec.sub(0);
    }

    const ruleY = cy + L.numSize * (spec.sub ? 0.70 : 0.50);
    const rule = e('line', {
      x1: cx - L.ruleW / 2, y1: ruleY, x2: cx + L.ruleW / 2, y2: ruleY,
      stroke: spec.colour, 'stroke-width': 2.4, 'stroke-linecap': 'round',
      'stroke-opacity': '0.65', pathLength: '1',
      'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
    }, g);

    spec.lines.forEach((text, k) => {
      const t = e('text', {
        x: cx, y: ruleY + 34 + k * (L.lineSize * 1.45), 'text-anchor': 'middle',
        'font-family': SANS, 'font-size': L.lineSize, fill: MUTED,
      }, g);
      t.textContent = text;
    });

    return { g, num, sub, rule, spec, cx, cy };
  });

  S.parts = { cols };
}

function render(p) {
  const L = S.L, P = S.parts;
  if (!P) return;

  for (const c of P.cols) {
    const a = c.spec.at[0], b = c.spec.at[1];
    const t = easeOut(seg(p, a, b));
    /* all three are present from the first frame: the counting is the animation,
     * and a lone number on an empty stage reads as a broken frame. */
    const appear = smooth(seg(p, c.spec.show[0], c.spec.show[1]));
    c.num.textContent = c.spec.fmt(c.spec.from + (c.spec.to - c.spec.from) * t);
    if (c.sub) c.sub.textContent = c.spec.sub(t);
    c.rule.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, a + 0.02, b)));
    c.g.setAttribute('opacity', appear);
    c.g.setAttribute('transform', 'translate(0,' + ((1 - appear) * 16).toFixed(2) + ')');
  }
}

export default {
  id: 'proof',

  mount(root, ctx) {
    S.root = root;
    S.ctx = ctx || {};
    red();
    S.svg = e('svg', {
      xmlns: NS, viewBox: '0 0 1200 700', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label':
        '88.1 percent of strands exactly right against 67.2 percent for the classic vote, at six reads on 1,996 held-out real clusters. 0.8 million parameters, trained in 13 minutes. 229 strands of 300 reconstructed exactly on the microcontroller itself, against 211 for the classic method.',
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
    const mode = w / Math.max(h, 1) < 1.3 ? 'narrow' : 'wide';
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
