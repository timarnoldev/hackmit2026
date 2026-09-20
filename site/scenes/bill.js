/* site/scenes/bill.js — beat 3, "the recurring cost".
 *
 * One strand at the top never changes: that is the data, written once. Under
 * it the same strand is read again and again, each read stacking below the
 * last, and the cost meter on the right climbs one notch per read. Nothing
 * about the strand grows. Only the reading does.
 *
 * No prices are shown, because we do not have one to show: the meter is a
 * bare proportion, ticked once per read.
 *
 * update(p) is pure: no clock, every attribute is a function of p.
 */

const SVGNS = 'http://www.w3.org/2000/svg';

const C = {
  surface: '#FFFFFF',
  surface2: '#DCEDE1',
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
const SANS = '"Instrument Sans","Erbgut Sans","Helvetica Neue",Arial,sans-serif';

const SEQ = 'ACGTTGCAGGTACCATGCTAGGCA';
const READS = 12;

const clamp = (v, a = 0, b = 1) => (v < a ? a : v > b ? b : v);
const lerp = (a, b, t) => a + (b - a) * t;
const seg = (p, a, b) => clamp((p - a) / (b - a));
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
  L.n = portrait ? 16 : SEQ.length;
  if (portrait) {
    L.x0 = 40; L.cell = 22; L.srcY = 168; L.srcH = 34;
    L.row0 = 252; L.pitch = 42; L.readH = 11;
    L.meterX = 430; L.meterW = 54; L.meterTop = 176; L.meterBot = 744;
    L.countY = 132; L.countSize = 34; L.labelY = 780;
  } else {
    L.x0 = 72; L.cell = 22; L.srcY = 122; L.srcH = 30;
    L.row0 = 196; L.pitch = 30.5; L.readH = 9;
    L.meterX = 752; L.meterW = 56; L.meterTop = 132; L.meterBot = 542;
    L.countY = 108; L.countSize = 32; L.labelY = 576;
  }
  L.blockW = L.cell * 0.70;
  return L;
}

export default {
  id: 'bill',

  mount(root, ctx) {
    this._ctx = ctx || { reduced: false };
    this._p = 0;

    if (!document.getElementById('eg-bill-style')) {
      const st = document.createElement('style');
      st.id = 'eg-bill-style';
      st.textContent =
        '.eg-bill{position:relative;width:100%;height:100%;}' +
        '.eg-bill>svg{display:block;width:100%;height:100%;}' +
        '.eg-bill .egb-m{font-family:' + MONO + ';font-variant-numeric:tabular-nums;}' +
        '.eg-bill .egb-n{font-family:' + SANS + ';font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;}' +
        '.eg-bill .egb-s{font-family:' + SANS + ';}';
      document.head.appendChild(st);
    }

    this._box = document.createElement('div');
    this._box.className = 'eg-bill';
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

    const gSrc = el('g', null);
    const gReads = el('g', null);
    const gMeter = el('g', null);
    svg.appendChild(gReads);
    svg.appendChild(gSrc);
    svg.appendChild(gMeter);

    const strandW = (L.n - 1) * L.cell + L.blockW;

    /* --- the strand, written once -------------------------------------- */
    gSrc.appendChild(el('rect', {
      x: L.x0 - 16, y: L.srcY - 16, width: strandW + 32, height: L.srcH + 32,
      rx: 12, fill: C.surface, stroke: C.rule, 'stroke-width': 1.5,
    }));
    for (let i = 0; i < L.n; i++) {
      gSrc.appendChild(el('rect', {
        x: (L.x0 + i * L.cell).toFixed(2), y: L.srcY,
        width: L.blockW.toFixed(2), height: L.srcH,
        rx: Math.min(4, L.blockW / 3).toFixed(2),
        fill: BASE[SEQ[i]] || C.ink, opacity: 0.88,
      }));
    }
    const srcLab = el('text', {
      x: L.x0 - 16, y: L.srcY - 28, 'font-size': 15, fill: C.muted,
      class: 'egb-s', 'letter-spacing': '0.05em',
    });
    srcLab.textContent = 'one strand';
    gSrc.appendChild(srcLab);

    /* --- the reads ------------------------------------------------------ */
    const r = rng(0xb111);
    this._reads = [];
    for (let k = 0; k < READS; k++) {
      const g = el('g', { opacity: 0 });
      const dx = (r() - 0.5) * L.cell * 1.15;
      for (let i = 0; i < L.n; i++) {
        g.appendChild(el('rect', {
          x: (L.x0 + i * L.cell).toFixed(2), y: 0,
          width: L.blockW.toFixed(2), height: L.readH,
          rx: Math.min(3, L.blockW / 3).toFixed(2),
          fill: C.muted, opacity: (0.24 + 0.34 * r()).toFixed(2),
        }));
      }
      gReads.appendChild(g);
      this._reads.push({ g, y: L.row0 + k * L.pitch, dx });
    }

    /* the bracket that grows with the stack */
    this._brack = el('path', {
      fill: 'none', stroke: C.rule, 'stroke-width': 1.5, 'stroke-linecap': 'round', opacity: 0,
    });
    gReads.appendChild(this._brack);

    /* --- the meter ------------------------------------------------------ */
    const mh = L.meterBot - L.meterTop;
    gMeter.appendChild(el('rect', {
      x: L.meterX, y: L.meterTop, width: L.meterW, height: mh,
      rx: L.meterW / 2, fill: C.surface, stroke: C.rule, 'stroke-width': 1.5,
    }));
    this._fill = el('rect', {
      x: L.meterX + 4, y: L.meterBot - 4, width: L.meterW - 8, height: 0,
      rx: (L.meterW - 8) / 2, fill: C.cost,
    });
    gMeter.appendChild(this._fill);

    this._ticks = [];
    const tickX = L.meterX + L.meterW + 12;
    for (let k = 0; k < READS; k++) {
      const y = L.meterBot - (mh * (k + 1)) / READS;
      const t = el('line', {
        x1: tickX, y1: y.toFixed(1), x2: tickX + (k === READS - 1 ? 22 : 13), y2: y.toFixed(1),
        stroke: C.rule, 'stroke-width': 1.5, 'stroke-linecap': 'round',
      });
      this._ticks.push(t);
      gMeter.appendChild(t);
    }

    this._count = el('text', {
      x: L.meterX + L.meterW / 2, y: L.countY, 'text-anchor': 'middle',
      'font-size': L.countSize, fill: C.cost, class: 'egb-n', opacity: 0,
    });
    this._count.textContent = '\u00d70';
    gMeter.appendChild(this._count);

    const lab = el('text', {
      x: L.meterX + L.meterW / 2, y: L.labelY, 'text-anchor': 'middle',
      'font-size': 16, fill: C.muted, class: 'egb-s', 'letter-spacing': '0.05em',
    });
    lab.textContent = 'cost of reading';
    gMeter.appendChild(lab);
  },

  update(p) {
    this._p = (this._ctx && this._ctx.reduced) ? 1 : clamp(p);
    this._paint(this._p);
  },

  _paint(p) {
    const L = this._L;
    if (!L) return;
    const readsF = READS * easeOut(seg(p, 0.05, 0.86));

    let lastY = L.row0;
    for (let k = 0; k < READS; k++) {
      const t = clamp(readsF - k);
      const rd = this._reads[k];
      const e = easeOut(t);
      setAttr(rd.g, 'opacity', e.toFixed(3));
      setAttr(rd.g, 'transform',
        'translate(' + (rd.dx * e).toFixed(2) + ' ' + (rd.y - 14 * (1 - e)).toFixed(2) + ')');
      if (t > 0.02) lastY = rd.y + L.readH;
    }

    const bx = L.x0 - 30;
    setAttr(this._brack, 'd',
      'M' + (bx + 7) + ' ' + L.row0.toFixed(1) + ' L' + bx + ' ' + L.row0.toFixed(1) +
      ' L' + bx + ' ' + lastY.toFixed(1) + ' L' + (bx + 7) + ' ' + lastY.toFixed(1));
    setAttr(this._brack, 'opacity', clamp(readsF * 1.4).toFixed(3));

    const mh = L.meterBot - L.meterTop - 8;
    const f = readsF / READS;
    const hgt = mh * f;
    setAttr(this._fill, 'height', hgt.toFixed(2));
    setAttr(this._fill, 'y', (L.meterBot - 4 - hgt).toFixed(2));
    setAttr(this._fill, 'opacity', f > 0.001 ? '1' : '0');

    for (let k = 0; k < READS; k++) {
      const on = readsF >= k + 0.98;
      setAttr(this._ticks[k], 'stroke', on ? C.cost : C.rule);
      setAttr(this._ticks[k], 'opacity', on ? '1' : '0.7');
    }

    const n = Math.min(READS, Math.round(readsF));
    const txt = '\u00d7' + n;
    if (this._count.textContent !== txt) this._count.textContent = txt;
    setAttr(this._count, 'opacity', clamp(readsF * 2).toFixed(3));
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
