/* site/scenes/hero.js — beat 1, "the promise".
 *
 * Loose A, C, G, T letters drift in an unordered cloud and settle, left to
 * right, onto a double helix. Grey-green while they are loose, coloured by
 * base once they are in place: chaos is monochrome, order is lit.
 *
 * update(p) is pure. The only time-driven motion is the idle drift of the
 * loose letters and the scroll cue, and both are faded out by p = 0.06, so
 * every frame at p > 0.06 is a function of p alone.
 *
 * Palette: marketing/BRAND.md section 9, light column, darker text step.
 */

const SVGNS = 'http://www.w3.org/2000/svg';

const C = {
  surface: '#FFFFFF',
  ink: '#08130B',
  muted: '#3E6B52',
  rule: '#C3D9C9',
  audit: '#0B6E82',
  cost: '#C41477',
  gain: '#0A7A41',
  tbd: '#6E6200',
};
const BASE = { A: C.audit, C: C.cost, G: C.gain, T: C.tbd };

const MONO = '"JetBrains Mono","Erbgut Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';

const SEQ = 'GATTACAGGCATCGTAACGGTCATGC'; // 26 bases

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

const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
function mixHex(a, b, t) {
  const x = hex(a), y = hex(b);
  const r = Math.round(lerp(x[0], y[0], t));
  const g = Math.round(lerp(x[1], y[1], t));
  const bl = Math.round(lerp(x[2], y[2], t));
  return '#' + ((1 << 24) + (r << 16) + (g << 8) + bl).toString(16).slice(1);
}

/* One cheap cache per node so a held frame costs no DOM writes. */
function setAttr(node, name, value) {
  const k = '_a' + name;
  if (node[k] === value) return;
  node[k] = value;
  node.setAttribute(name, value);
}

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
    n: portrait ? 14 : 17,
    fs: portrait ? 34 : 34,
    amp: portrait ? 58 : 52,
    strandY: CH * (portrait ? 0.60 : 0.615),
  };
  L.x0 = CW * (portrait ? 0.09 : 0.47);
  L.cell = (CW * (portrait ? 0.82 : 0.46)) / (L.n - 1);
  return L;
}

export default {
  id: 'hero',

  mount(root, ctx) {
    this._ctx = ctx || { reduced: false };
    this._root = root;
    this._p = 0;

    if (!document.getElementById('eg-hero-style')) {
      const st = document.createElement('style');
      st.id = 'eg-hero-style';
      st.textContent =
        '.eg-hero{position:relative;width:100%;height:100%;}' +
        '.eg-hero>svg{display:block;width:100%;height:100%;}' +
        '.eg-hero text{font-family:' + MONO + ';font-weight:600;}';
      document.head.appendChild(st);
    }

    this._box = document.createElement('div');
    this._box.className = 'eg-hero';
    this._svg = el('svg', { preserveAspectRatio: 'xMidYMid meet' });
    this._box.appendChild(this._svg);
    root.appendChild(this._box);

    const w = root.clientWidth || 1440;
    const h = root.clientHeight || 800;
    this._build(layoutFor(w, h));

    if (!this._ctx.reduced) this._startIdle();
  },

  _build(L) {
    this._L = L;
    const svg = this._svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute('viewBox', L.vb);

    const gDust = el('g', null);
    const gHelix = el('g', null);
    const gLetters = el('g', null);
    const gCue = el('g', null);
    svg.appendChild(gDust);
    svg.appendChild(gHelix);
    svg.appendChild(gLetters);
    svg.appendChild(gCue);

    /* Helix geometry: the letters ride the front curve, a thin audit line is
     * the back curve, faint rungs hold the two together. */
    const period = L.n / 2.35;
    const yFront = (i) => L.strandY + L.amp * Math.sin((2 * Math.PI * i) / period);
    const yBack = (i) => L.strandY - L.amp * Math.sin((2 * Math.PI * i) / period);
    const xAt = (i) => L.x0 + i * L.cell;
    this._yFront = yFront;
    this._xAt = xAt;

    const path = (fn) => {
      let d = '';
      for (let i = 0; i <= (L.n - 1) * 4; i++) {
        const t = i / 4;
        d += (i ? 'L' : 'M') + xAt(t).toFixed(1) + ' ' + fn(t).toFixed(1);
      }
      return d;
    };

    this._back = el('path', {
      d: path(yBack), fill: 'none', stroke: C.audit,
      'stroke-width': 2.2, 'stroke-linecap': 'round', opacity: 0,
    });
    this._backGlow = el('path', {
      d: path(yBack), fill: 'none', stroke: C.audit,
      'stroke-width': 9, 'stroke-linecap': 'round', opacity: 0,
    });
    this._front = el('path', {
      d: path(yFront), fill: 'none', stroke: C.muted,
      'stroke-width': 1.4, 'stroke-linecap': 'round', opacity: 0,
    });
    gHelix.appendChild(this._backGlow);
    gHelix.appendChild(this._back);
    gHelix.appendChild(this._front);

    this._rungs = [];
    for (let i = 0; i < L.n; i += 2) {
      const r = el('line', {
        x1: xAt(i).toFixed(1), y1: yFront(i).toFixed(1),
        x2: xAt(i).toFixed(1), y2: yBack(i).toFixed(1),
        stroke: C.gain, 'stroke-width': 2, 'stroke-linecap': 'round', opacity: 0,
      });
      this._rungs.push(r);
      gHelix.appendChild(r);
    }

    const r = rng(0x5eed21);
    this._letters = [];
    for (let i = 0; i < L.n; i++) {
      const ch = SEQ[i % SEQ.length];
      const node = el('text', { 'text-anchor': 'middle', 'dominant-baseline': 'central' });
      node.textContent = ch;
      gLetters.appendChild(node);
      const ang = r() * Math.PI * 2;
      const rad = 0.30 + 0.72 * r();
      this._letters.push({
        node, ch,
        // scattered start: a loose ring around the middle of the stage
        sx: L.CW * (0.5 + 0.52 * Math.cos(ang) * rad),
        sy: L.CH * (0.46 + 0.50 * Math.sin(ang) * rad),
        rot0: (r() - 0.5) * 110,
        ph: r() * Math.PI * 2,
        sp: 0.5 + r(),
        bow: (r() - 0.5) * 120,
        start: 0.03 + 0.50 * (i / Math.max(1, L.n - 1)) + (r() - 0.5) * 0.05,
        col: BASE[ch] || C.ink,
      });
    }

    /* Dust: the letters that are not part of this strand. They drift while
     * the page is at rest and clear out as the strand assembles. */
    this._dust = [];
    const ALPH = 'ACGT';
    for (let i = 0; i < 70; i++) {
      const node = el('text', { 'text-anchor': 'middle', 'dominant-baseline': 'central' });
      node.textContent = ALPH[(r() * 4) | 0];
      node.setAttribute('font-size', (L.fs * (0.30 + 0.40 * r())).toFixed(1));
      node.setAttribute('fill', C.muted);
      gDust.appendChild(node);
      this._dust.push({
        node,
        x: L.CW * (-0.06 + 1.12 * r()),
        y: L.CH * (-0.04 + 1.08 * r()),
        rot: (r() - 0.5) * 140,
        ph: r() * Math.PI * 2,
        sp: 0.4 + r() * 1.1,
        op: 0.10 + 0.16 * r(),
        out: 0.10 + 0.45 * r(),
      });
    }

    /* Scroll cue: a hairline the dot falls down, fading out on first scroll. */
    this._cue = gCue;
    const cx = L.CW / 2;
    const cy = L.CH - (L.portrait ? 74 : 54);
    gCue.appendChild(el('line', {
      x1: cx, y1: cy - 26, x2: cx, y2: cy + 26,
      stroke: C.rule, 'stroke-width': 1.5, 'stroke-linecap': 'round',
    }));
    this._cueDot = el('circle', { cx, cy: cy - 26, r: 3.2, fill: C.audit });
    gCue.appendChild(this._cueDot);
    this._cueY0 = cy - 26;
    this._cueY1 = cy + 26;
  },

  _startIdle() {
    if (this._raf) return;
    const tick = () => {
      this._raf = null;
      const drift = 1 - clamp(this._p / 0.06);
      if (drift <= 0) return;
      this._paint(this._p, performance.now());
      this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  },

  update(p) {
    const reduced = this._ctx && this._ctx.reduced;
    this._p = reduced ? 1 : clamp(p);
    this._paint(this._p, reduced ? 0 : performance.now());
    if (!reduced && this._p < 0.06) this._startIdle();
  },

  _paint(p, now) {
    const L = this._L;
    if (!L) return;
    const reduced = this._ctx && this._ctx.reduced;
    const drift = reduced ? 0 : 1 - clamp(p / 0.06);
    const t = now * 0.0007;

    for (let i = 0; i < this._letters.length; i++) {
      const g = this._letters[i];
      const s = easeInOut(seg(p, g.start, g.start + 0.27));
      const tx = this._xAt(i);
      const ty = this._yFront(i);
      const wob = drift * (1 - s);
      const x = lerp(g.sx, tx, s) + Math.sin(t * g.sp + g.ph) * 13 * wob;
      const y = lerp(g.sy, ty, s) + Math.cos(t * g.sp * 0.83 + g.ph * 1.7) * 11 * wob
        + g.bow * s * (1 - s);
      const rot = lerp(g.rot0, 0, s) + Math.sin(t * g.sp * 0.6 + g.ph) * 5 * wob;
      const sc = lerp(0.74, 1, s);
      setAttr(g.node, 'transform',
        'translate(' + x.toFixed(1) + ' ' + y.toFixed(1) + ') rotate(' + rot.toFixed(1) + ') scale(' + sc.toFixed(3) + ')');
      setAttr(g.node, 'font-size', L.fs);
      setAttr(g.node, 'fill', mixHex(C.muted, g.col, easeOut(s)));
      setAttr(g.node, 'opacity', (0.42 + 0.58 * s).toFixed(3));
    }

    for (let i = 0; i < this._dust.length; i++) {
      const d = this._dust[i];
      const gone = easeInOut(seg(p, d.out, d.out + 0.24));
      const dx = Math.sin(t * d.sp + d.ph) * 16 * drift;
      const dy = Math.cos(t * d.sp * 0.77 + d.ph * 1.3) * 13 * drift - 26 * gone;
      setAttr(d.node, 'transform', 'translate(' + (d.x + dx).toFixed(1) + ' ' + (d.y + dy).toFixed(1)
        + ') rotate(' + d.rot.toFixed(1) + ')');
      setAttr(d.node, 'opacity', (d.op * (1 - gone)).toFixed(3));
    }

    const h = easeInOut(seg(p, 0.34, 0.86));
    setAttr(this._back, 'opacity', (0.62 * h).toFixed(3));
    setAttr(this._backGlow, 'opacity', (0.07 * h).toFixed(3));
    setAttr(this._front, 'opacity', (0.22 * h).toFixed(3));
    for (let i = 0; i < this._rungs.length; i++) {
      const hh = easeInOut(seg(p, 0.38 + 0.30 * (i / Math.max(1, this._rungs.length - 1)), 0.92));
      setAttr(this._rungs[i], 'opacity', (0.26 * hh).toFixed(3));
    }

    const cue = reduced ? 0 : 1 - clamp(p / 0.06);
    setAttr(this._cue, 'opacity', cue.toFixed(3));
    if (cue > 0) {
      const u = (now * 0.00045) % 1;
      const e = u < 0.75 ? easeOut(u / 0.75) : 1;
      setAttr(this._cueDot, 'cy', lerp(this._cueY0, this._cueY1, e).toFixed(1));
      setAttr(this._cueDot, 'opacity', (u < 0.75 ? 1 : 1 - (u - 0.75) / 0.25).toFixed(3));
    }
  },

  resize(w, h) {
    const L = layoutFor(w, h);
    if (!this._L || L.portrait !== this._L.portrait || L.n !== this._L.n) {
      this._build(L);
    } else {
      this._L = L;
      this._svg.setAttribute('viewBox', L.vb);
    }
    this._paint(this._p, performance.now());
  },

  unmount() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    if (this._box && this._box.parentNode) this._box.parentNode.removeChild(this._box);
  },
};
