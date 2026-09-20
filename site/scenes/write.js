/* site/scenes/write.js — beat 2, "a file becomes DNA".
 *
 * A block of bytes comes apart and every fragment flies out as one short
 * strand. There are exactly 1,239 fragments on screen, because that is how
 * many strands the 20 KB test file becomes at default settings
 * (docs/NUMBERS.md). The counter climbs with the fragments that have landed.
 *
 * update(p) is pure: no clock anywhere, every attribute is a function of p.
 *
 * Palette: marketing/BRAND.md section 9, light column, darker text step.
 */

const SVGNS = 'http://www.w3.org/2000/svg';

const C = {
  surface: '#FFFFFF',
  surface2: '#DCEDE1',
  ink: '#08130B',
  muted: '#3E6B52',
  rule: '#C3D9C9',
  audit: '#0B6E82',
  gain: '#0A7A41',
};

const MONO = '"JetBrains Mono","Erbgut Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';
const SANS = '"Instrument Sans","Erbgut Sans","Helvetica Neue",Arial,sans-serif';

const N_STRANDS = 1239;      // 20 KB test file, default settings
const GRID_C = 59;           // 59 x 21 = 1239 exactly
const GRID_R = 21;

const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const seg = (p, a, b) => clamp((p - a) / (b - a));
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function el(name, attrs) {
  const e = document.createElementNS(SVGNS, name);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function setAttr(node, name, value) {
  const k = '_a' + name;
  if (node[k] === value) return;
  node[k] = value;
  node.setAttribute(name, value);
}

const comma = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');

function layoutFor(w, h) {
  const aspect = w > 0 && h > 0 ? w / h : 1.6;
  const portrait = aspect < 1.05;
  const CW = portrait ? 560 : 1000;
  const CH = portrait ? 860 : 620;
  let vw, vh;
  if (aspect > CW / CH) { vh = CH; vw = CH * aspect; } else { vw = CW; vh = CW / aspect; }
  const L = {
    portrait, CW, CH,
    vb: [(CW - vw) / 2, (CH - vh) / 2, vw, vh].map((v) => Math.round(v * 100) / 100).join(' '),
  };
  if (portrait) {
    L.card = { x: 164, y: 112, w: 232, h: 158 };
    L.field = { x: 26, y: 322, w: 508, h: 392 };
    L.cols = 29;
    L.countY = 792;
    L.countSize = 62;
  } else {
    L.card = { x: 372, y: 96, w: 256, h: 170 };
    L.field = { x: 62, y: 292, w: 876, h: 198 };
    L.cols = GRID_C;
    L.countY = 570;
    L.countSize = 64;
  }
  L.rows = Math.ceil(N_STRANDS / L.cols);
  return L;
}

export default {
  id: 'write',

  mount(root, ctx) {
    this._ctx = ctx || { reduced: false };
    this._p = 0;

    if (!document.getElementById('eg-write-style')) {
      const st = document.createElement('style');
      st.id = 'eg-write-style';
      st.textContent =
        '.eg-write{position:relative;width:100%;height:100%;}' +
        '.eg-write>svg{display:block;width:100%;height:100%;}' +
        '.eg-write .egw-m{font-family:' + MONO + ';font-variant-numeric:tabular-nums;}' +
        '.eg-write .egw-n{font-family:' + SANS + ';font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;}' +
        '.eg-write .egw-s{font-family:' + SANS + ';}';
      document.head.appendChild(st);
    }

    this._box = document.createElement('div');
    this._box.className = 'eg-write';
    this._svg = el('svg', { preserveAspectRatio: 'xMidYMid meet' });
    this._box.appendChild(this._svg);
    root.appendChild(this._box);

    this._build(layoutFor(root.clientWidth || 1440, root.clientHeight || 800));
  },

  _build(L) {
    this._L = L;
    const svg = this._svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute('viewBox', L.vb);

    const gCard = el('g', null);
    const gBits = el('g', null);
    const gText = el('g', null);
    svg.appendChild(gCard);
    svg.appendChild(gBits);
    svg.appendChild(gText);

    /* --- the file ------------------------------------------------------ */
    const cd = L.card;
    this._cardG = gCard;
    gCard.appendChild(el('rect', {
      x: cd.x - 12, y: cd.y - 30, width: cd.w + 24, height: cd.h + 44,
      rx: 12, fill: C.surface, stroke: C.rule, 'stroke-width': 1.5,
    }));
    gCard.appendChild(el('rect', {
      x: cd.x - 12, y: cd.y - 30, width: cd.w + 24, height: 30,
      rx: 12, fill: C.surface2, opacity: 0.85,
    }));
    gCard.appendChild(el('rect', {
      x: cd.x - 12, y: cd.y - 14, width: cd.w + 24, height: 14, fill: C.surface2, opacity: 0.85,
    }));
    const tag = el('text', {
      x: cd.x, y: cd.y - 15, 'dominant-baseline': 'central',
      'font-size': 14, fill: C.muted, class: 'egw-m',
    });
    tag.textContent = '20 KB';
    gCard.appendChild(tag);
    for (let i = 0; i < 3; i++) {
      gCard.appendChild(el('circle', { cx: cd.x + cd.w - 8 - i * 14, cy: cd.y - 15, r: 2.6, fill: C.rule }));
    }

    /* --- 1,239 fragments ----------------------------------------------- */
    const r = rng(0x1239);
    const cellW = cd.w / GRID_C;
    const cellH = cd.h / GRID_R;
    const fx = L.field, fcw = fx.w / L.cols, fch = fx.h / L.rows;
    const dashW = Math.min(fcw * 0.66, 15);
    const dashH = Math.max(1.8, Math.min(fch * 0.42, 3.4));

    // shuffle field slots so the block dissolves instead of simply zooming
    const slots = new Array(N_STRANDS);
    for (let i = 0; i < N_STRANDS; i++) slots[i] = i;
    for (let i = N_STRANDS - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      const t = slots[i]; slots[i] = slots[j]; slots[j] = t;
    }

    this._bits = [];
    for (let i = 0; i < N_STRANDS; i++) {
      const col = i % GRID_C, row = (i / GRID_C) | 0;
      const sx = cd.x + (col + 0.5) * cellW;
      const sy = cd.y + (row + 0.5) * cellH;

      const s = slots[i];
      const frow = (s / L.cols) | 0;
      const fcol = s % L.cols;
      const inRow = Math.min(L.cols, N_STRANDS - frow * L.cols);
      const tx = fx.x + (fx.w - inRow * fcw) / 2 + (fcol + 0.5) * fcw + (r() - 0.5) * fcw * 0.30;
      const ty = fx.y + (frow + 0.5) * fch + (r() - 0.5) * fch * 0.34;

      const node = el('rect', {
        x: (-dashW / 2).toFixed(2), y: (-dashH / 2).toFixed(2),
        width: dashW.toFixed(2), height: dashH.toFixed(2),
        rx: (dashH / 2).toFixed(2), fill: C.audit,
      });
      gBits.appendChild(node);
      this._bits.push({
        node, sx, sy, tx, ty,
        rot: (r() - 0.5) * 22,
        cx: (r() - 0.5) * 260,   // control offset, bends the flight path
        cy: -40 - r() * 110,
        s0: 0.06 + 0.52 * r(),
        op: 0.42 + 0.46 * r(),
        // squeezed into the file: a byte, not yet a strand
        w0: Math.max(0.9, cellW * 0.58) / dashW,
        h0: Math.max(0.9, cellH * 0.46) / dashH,
      });
    }

    /* --- counter -------------------------------------------------------- */
    this._count = el('text', {
      x: L.CW / 2, y: L.countY, 'text-anchor': 'middle',
      'font-size': L.countSize, fill: C.ink, class: 'egw-n',
    });
    this._count.textContent = '0';
    gText.appendChild(this._count);
    const lab = el('text', {
      x: L.CW / 2, y: L.countY + 28, 'text-anchor': 'middle',
      'font-size': 17, fill: C.muted, class: 'egw-s', 'letter-spacing': '0.04em',
    });
    lab.textContent = 'strands';
    gText.appendChild(lab);
    this._countLab = lab;
  },

  update(p) {
    this._p = (this._ctx && this._ctx.reduced) ? 1 : clamp(p);
    this._paint(this._p);
  },

  _paint(p) {
    const L = this._L;
    if (!L) return;

    for (let i = 0; i < this._bits.length; i++) {
      const b = this._bits[i];
      const t = easeInOut(seg(p, b.s0, b.s0 + 0.30));
      let x, y;
      if (t <= 0) { x = b.sx; y = b.sy; }
      else if (t >= 1) { x = b.tx; y = b.ty; }
      else {
        // quadratic bezier through a lifted control point
        const mx = (b.sx + b.tx) / 2 + b.cx, my = (b.sy + b.ty) / 2 + b.cy;
        const u = 1 - t;
        x = u * u * b.sx + 2 * u * t * mx + t * t * b.tx;
        y = u * u * b.sy + 2 * u * t * my + t * t * b.ty;
      }
      const sw = lerp(b.w0, 1, t);
      const sh = lerp(b.h0, 1, t);
      setAttr(b.node, 'transform',
        'translate(' + x.toFixed(1) + ' ' + y.toFixed(1) + ') rotate(' + (b.rot * t).toFixed(1) + ') scale(' + sw.toFixed(3) + ' ' + sh.toFixed(3) + ')');
      setAttr(b.node, 'opacity', lerp(0.72, b.op, t).toFixed(3));
      setAttr(b.node, 'fill', t < 0.5 ? C.ink : C.audit);
    }

    const frac = easeInOut(seg(p, 0.06, 0.88));
    setAttr(this._cardG, 'opacity', (1 - easeOut(seg(p, 0.30, 0.62))).toFixed(3));

    const n = Math.round(N_STRANDS * frac);
    const txt = comma(n);
    if (this._count.textContent !== txt) this._count.textContent = txt;
    setAttr(this._count, 'opacity', clamp(frac * 4).toFixed(3));
    setAttr(this._countLab, 'opacity', clamp(frac * 3).toFixed(3));
  },

  resize(w, h) {
    const L = layoutFor(w, h);
    if (!this._L || L.portrait !== this._L.portrait) this._build(L);
    else { this._L = L; this._svg.setAttribute('viewBox', L.vb); }
    this._paint(this._p);
  },

  unmount() {
    if (this._box && this._box.parentNode) this._box.parentNode.removeChild(this._box);
  },
};
