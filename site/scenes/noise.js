/* site/scenes/noise.js — beat 4, "why reading is expensive".
 *
 * One written strand, six reads of it, every read wrong in its own way.
 * The distinction the scene exists to make:
 *
 *   substitution — one cell goes magenta, nothing moves. Easy.
 *   deletion     — a letter collapses and EVERY letter after it slides one
 *                  cell left, off the column grid. Insertion does the same
 *                  to the right. That displacement is why indels are the
 *                  hard case, and the column guides make it impossible to
 *                  miss.
 *
 * Error rates and kinds follow the real Microsoft Nanopore set (roughly 2.0%
 * deletions, 2.2% substitutions, 1.7% insertions), and the shared deletion
 * sits inside the GG homopolymer, where real deletions concentrate.
 *
 * update(p) is pure: no clock, every attribute is a function of p.
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
const TINT_COST = 'rgba(196,20,119,0.12)';

const MONO = '"JetBrains Mono","Erbgut Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';
const SANS = '"Instrument Sans","Erbgut Sans","Helvetica Neue",Arial,sans-serif';

/* The strand and the six read scripts are shared with vote.js so the two
 * beats show the same data. Keep them identical if you change either. */
const TRUE22 = 'ACGTTGCAGGTACCATGCTAGG';
const SCRIPTS = [
  [{ t: 'sub', i: 3, ch: 'A' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'del', i: 6 }, { t: 'sub', i: 11, ch: 'G' }],
  [{ t: 'sub', i: 2, ch: 'A' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'ins', i: 5, ch: 'G' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'sub', i: 9, ch: 'T' }, { t: 'sub', i: 13, ch: 'T' }, { t: 'sub', i: 15, ch: 'C' }],
  [{ t: 'ins', i: 12, ch: 'A' }, { t: 'sub', i: 4, ch: 'C' }],
];

const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const seg = (p, a, b) => clamp((p - a) / (b - a));
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

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
const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
function mixHex(a, b, t) {
  const x = hex(a), y = hex(b);
  return '#' + ((1 << 24) + (Math.round(lerp(x[0], y[0], t)) << 16)
    + (Math.round(lerp(x[1], y[1], t)) << 8) + Math.round(lerp(x[2], y[2], t))).toString(16).slice(1);
}

/* Turn a script into glyphs that know where they sat before the error
 * (pre, in true-strand columns) and where they sit after it (post, in read
 * columns). A deletion makes post = pre - 1 for the whole tail. */
export function buildRead(trueStr, ops) {
  const subs = {}, dels = {}, inss = {};
  for (const o of ops) {
    if (o.i >= trueStr.length) continue;
    if (o.t === 'sub') subs[o.i] = o.ch;
    else if (o.t === 'del') dels[o.i] = true;
    else inss[o.i] = o.ch;
  }
  const glyphs = [];
  const events = [];
  let post = 0;
  for (let i = 0; i < trueStr.length; i++) {
    if (inss[i] != null) {
      glyphs.push({ ch: inss[i], kind: 'ins', pre: i - 0.5, post, col: i });
      events.push({ post, dir: 1 });
      post++;
    }
    if (dels[i]) {
      glyphs.push({ ch: trueStr[i], kind: 'del', pre: i, post, col: i });
      events.push({ post, dir: -1 });
    } else {
      const s = subs[i];
      glyphs.push({ ch: s != null ? s : trueStr[i], kind: s != null ? 'sub' : 'keep', pre: i, post, col: i });
      post++;
    }
  }
  return { glyphs, events, len: post };
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
  };
  if (portrait) {
    L.n = 16; L.x0 = 62; L.cell = 27.5; L.fs = 24;
    L.trueY = 188; L.row0 = 284; L.pitch = 72; L.labFs = 11;
  } else {
    L.n = 22; L.x0 = 96; L.cell = 36.7; L.fs = 26;
    L.trueY = 152; L.row0 = 232; L.pitch = 46; L.labFs = 13;
  }
  L.str = TRUE22.slice(0, L.n);
  return L;
}

export default {
  id: 'noise',

  mount(root, ctx) {
    this._ctx = ctx || { reduced: false };
    this._p = 0;

    if (!document.getElementById('eg-noise-style')) {
      const st = document.createElement('style');
      st.id = 'eg-noise-style';
      st.textContent =
        '.eg-noise{position:relative;width:100%;height:100%;}' +
        '.eg-noise>svg{display:block;width:100%;height:100%;}' +
        '.eg-noise text{font-family:' + MONO + ';font-weight:600;}' +
        '.eg-noise .egn-s{font-family:' + SANS + ';font-weight:400;}';
      document.head.appendChild(st);
    }

    this._box = document.createElement('div');
    this._box.className = 'eg-noise';
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

    const x = (k) => L.x0 + (k + 0.5) * L.cell;
    this._x = x;
    const lastY = L.row0 + 5 * L.pitch;

    const gGrid = el('g', null);
    const gTrue = el('g', null);
    const gRows = el('g', null);
    svg.appendChild(gGrid);
    svg.appendChild(gTrue);
    svg.appendChild(gRows);

    /* column guides: the grid the reads fall off */
    this._guides = [];
    for (let i = 0; i <= L.n + 1; i++) {
      const g = el('line', {
        x1: (L.x0 + i * L.cell).toFixed(2), y1: (L.trueY + L.fs * 0.85).toFixed(1),
        x2: (L.x0 + i * L.cell).toFixed(2), y2: (lastY + L.fs * 0.85).toFixed(1),
        stroke: C.rule, 'stroke-width': 1, opacity: 0,
      });
      this._guides.push(g);
      gGrid.appendChild(g);
    }
    this._gridLine = el('line', {
      x1: L.x0, y1: (L.trueY + L.fs * 0.85).toFixed(1),
      x2: (L.x0 + L.n * L.cell).toFixed(2), y2: (L.trueY + L.fs * 0.85).toFixed(1),
      stroke: C.rule, 'stroke-width': 1.5, opacity: 0,
    });
    gGrid.appendChild(this._gridLine);

    /* --- the written strand -------------------------------------------- */
    const lab = el('text', {
      x: L.x0 - 14, y: L.trueY, 'text-anchor': 'end', 'dominant-baseline': 'central',
      'font-size': L.labFs, fill: C.muted, class: 'egn-s',
    });
    lab.textContent = 'written';
    gTrue.appendChild(lab);
    this._true = [];
    for (let i = 0; i < L.n; i++) {
      const t = el('text', {
        x: x(i).toFixed(2), y: L.trueY, 'text-anchor': 'middle', 'dominant-baseline': 'central',
        'font-size': L.fs, fill: BASE[L.str[i]] || C.ink, opacity: 0,
      });
      t.textContent = L.str[i];
      this._true.push(t);
      gTrue.appendChild(t);
    }

    /* --- the six reads --------------------------------------------------- */
    this._rows = [];
    for (let k = 0; k < 6; k++) {
      const y = L.row0 + k * L.pitch;
      const g = el('g', { opacity: 0 });
      gRows.appendChild(g);

      const rl = el('text', {
        x: L.x0 - 14, y, 'text-anchor': 'end', 'dominant-baseline': 'central',
        'font-size': L.labFs, fill: C.muted, opacity: 0.8,
      });
      rl.textContent = String(k + 1);
      g.appendChild(rl);

      const built = buildRead(L.str, SCRIPTS[k]);

      const marks = [];
      for (const ev of built.events) {
        const mg = el('g', { opacity: 0 });
        const my = y + L.fs * 0.78;
        const xs = x(ev.post) - L.cell * 0.5;
        const xe = L.x0 + L.n * L.cell;
        mg.appendChild(el('line', {
          x1: xs.toFixed(2), y1: my.toFixed(1), x2: xe.toFixed(2), y2: my.toFixed(1),
          stroke: C.cost, 'stroke-width': 2, 'stroke-linecap': 'round', opacity: 0.62,
        }));
        const tipX = (ev.dir < 0 ? xs : xs + L.cell * 0.22) + ev.dir * 9;
        mg.appendChild(el('path', {
          d: 'M' + tipX.toFixed(2) + ' ' + my.toFixed(1)
            + ' l' + (-ev.dir * 9).toFixed(1) + ' -4.5 l0 9 z',
          fill: C.cost,
        }));
        marks.push(mg);
        g.appendChild(mg);
      }

      const nodes = [];
      for (const gl of built.glyphs) {
        const chip = (gl.kind === 'sub')
          ? el('rect', {
            x: (x(gl.post) - L.cell * 0.40).toFixed(2), y: (y - L.fs * 0.62).toFixed(2),
            width: (L.cell * 0.80).toFixed(2), height: (L.fs * 1.24).toFixed(2),
            rx: 5, fill: TINT_COST, opacity: 0,
          })
          : null;
        if (chip) g.appendChild(chip);
        const t = el('text', {
          'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': L.fs, fill: C.ink,
        });
        t.textContent = gl.ch;
        g.appendChild(t);
        nodes.push({ gl, t, chip });
      }
      this._rows.push({ g, y, nodes, marks });
    }
  },

  update(p) {
    this._p = (this._ctx && this._ctx.reduced) ? 1 : clamp(p);
    this._paint(this._p);
  },

  _paint(p) {
    const L = this._L;
    if (!L) return;
    const x = this._x;

    for (let i = 0; i < L.n; i++) {
      const t = easeOut(seg(p, 0.005 + 0.06 * (i / L.n), 0.10 + 0.06 * (i / L.n)));
      setAttr(this._true[i], 'opacity', t.toFixed(3));
    }
    const grid = easeOut(seg(p, 0.06, 0.24));
    for (const g of this._guides) setAttr(g, 'opacity', (0.36 * grid).toFixed(3));
    setAttr(this._gridLine, 'opacity', (0.9 * grid).toFixed(3));

    for (let k = 0; k < 6; k++) {
      const row = this._rows[k];
      const s0 = 0.11 + k * 0.128;
      const app = easeOut(seg(p, s0, s0 + 0.055));
      const err = easeInOut(seg(p, s0 + 0.045, s0 + 0.15));
      setAttr(row.g, 'opacity', app.toFixed(3));
      setAttr(row.g, 'transform', 'translate(0 ' + (-9 * (1 - app)).toFixed(2) + ')');

      for (const n of row.nodes) {
        const gl = n.gl;
        const xp = lerp(x(gl.pre), x(gl.post), err);
        if (gl.kind === 'del') {
          const f = 1 - err;
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + (row.y - 9 * err).toFixed(2)
            + ') scale(' + lerp(1, 0.45, err).toFixed(3) + ')');
          setAttr(n.t, 'fill', mixHex(C.ink, C.cost, Math.min(1, err * 2)));
          setAttr(n.t, 'opacity', (0.7 * f * f).toFixed(3));
        } else if (gl.kind === 'ins') {
          setAttr(n.t, 'transform', 'translate(' + x(gl.post).toFixed(2) + ' '
            + (row.y - 10 * (1 - err)).toFixed(2) + ') scale(' + lerp(0.5, 1, err).toFixed(3) + ')');
          setAttr(n.t, 'fill', C.cost);
          setAttr(n.t, 'opacity', err.toFixed(3));
        } else if (gl.kind === 'sub') {
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + row.y.toFixed(2) + ')');
          setAttr(n.t, 'fill', mixHex(C.ink, C.cost, err));
          setAttr(n.t, 'opacity', (0.76 + 0.24 * err).toFixed(3));
          if (n.chip) setAttr(n.chip, 'opacity', err.toFixed(3));
        } else {
          const shifted = gl.post !== gl.pre;
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + row.y.toFixed(2) + ')');
          setAttr(n.t, 'fill', shifted ? mixHex(C.ink, C.cost, 0.34 * err) : C.ink);
          setAttr(n.t, 'opacity', '0.76');
        }
      }
      for (const m of row.marks) setAttr(m, 'opacity', (0.95 * err).toFixed(3));
    }
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
