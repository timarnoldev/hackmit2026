/* site/scenes/vote.js — beat 5, "the classic fix".
 *
 * The six reads of beat 4 slide back onto the column grid (the deletions
 * leave a gap where the letter was, the insertions are squeezed out), a
 * sweep runs left to right, and each column elects a letter. Losing reads
 * dim, so the vote is visible, not asserted.
 *
 * Voting fixes almost everything and still loses two columns, and both
 * losses are the real ones: three of six reads drop the same G out of the
 * GG run, so the gap wins the column; four of six make the same
 * substitution, so the wrong letter wins. Errors the reads share are
 * exactly what more reads cannot fix, which is why the honest baseline is
 * 67.2% of strands exactly right at six reads, not 99%.
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
const TINT_AUDIT = 'rgba(11,110,130,0.10)';

const MONO = '"JetBrains Mono","Erbgut Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace';
const SANS = '"Instrument Sans","Erbgut Sans","Helvetica Neue",Arial,sans-serif';

/* Identical to noise.js on purpose: same strand, same six reads. */
const TRUE22 = 'ACGTTGCAGGTACCATGCTAGG';
const SCRIPTS = [
  [{ t: 'sub', i: 3, ch: 'A' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'del', i: 6 }, { t: 'sub', i: 11, ch: 'G' }],
  [{ t: 'sub', i: 2, ch: 'A' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'ins', i: 5, ch: 'G' }, { t: 'del', i: 9 }, { t: 'sub', i: 13, ch: 'T' }],
  [{ t: 'sub', i: 9, ch: 'T' }, { t: 'sub', i: 13, ch: 'T' }, { t: 'sub', i: 15, ch: 'C' }],
  [{ t: 'ins', i: 12, ch: 'A' }, { t: 'sub', i: 4, ch: 'C' }],
];
const TARGET = 67.2;   // docs/NUMBERS.md: baseline, 6 reads, held-out real Nanopore

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

function buildRead(trueStr, ops) {
  const subs = {}, dels = {}, inss = {};
  for (const o of ops) {
    if (o.i >= trueStr.length) continue;
    if (o.t === 'sub') subs[o.i] = o.ch;
    else if (o.t === 'del') dels[o.i] = true;
    else inss[o.i] = o.ch;
  }
  const glyphs = [], events = [];
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
  return { glyphs, events };
}

/* One column, six ballots. A deletion votes for a gap. */
function tally(ballots) {
  const order = ['A', 'C', 'G', 'T', '-'];
  const count = {};
  for (const b of ballots) count[b] = (count[b] || 0) + 1;
  let best = null, n = -1;
  for (const k of order) if ((count[k] || 0) > n) { n = count[k] || 0; best = k; }
  return { winner: best, count, n };
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
    L.n = 16; L.x0 = 62; L.cell = 27.5; L.fs = 24; L.labFs = 11;
    L.trueY = 172; L.row0 = 250; L.pitch = 58; L.consY = 636;
    L.numY = 772; L.numFs = 64;
  } else {
    L.n = 22; L.x0 = 96; L.cell = 36.7; L.fs = 26; L.labFs = 13;
    L.trueY = 110; L.row0 = 164; L.pitch = 37; L.consY = 432;
    L.numY = 556; L.numFs = 74;
  }
  L.str = TRUE22.slice(0, L.n);
  return L;
}

export default {
  id: 'vote',

  mount(root, ctx) {
    this._ctx = ctx || { reduced: false };
    this._p = 0;

    if (!document.getElementById('eg-vote-style')) {
      const st = document.createElement('style');
      st.id = 'eg-vote-style';
      st.textContent =
        '.eg-vote{position:relative;width:100%;height:100%;}' +
        '.eg-vote>svg{display:block;width:100%;height:100%;}' +
        '.eg-vote text{font-family:' + MONO + ';font-weight:600;font-variant-numeric:tabular-nums;}' +
        '.eg-vote .egv-s{font-family:' + SANS + ';font-weight:400;}' +
        '.eg-vote .egv-n{font-family:' + SANS + ';font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-0.02em;}';
      document.head.appendChild(st);
    }

    this._box = document.createElement('div');
    this._box.className = 'eg-vote';
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
    const strandW = L.n * L.cell;

    const gBand = el('g', null);
    const gGrid = el('g', null);
    const gTrue = el('g', null);
    const gRows = el('g', null);
    const gCons = el('g', null);
    const gNum = el('g', null);
    svg.appendChild(gBand);
    svg.appendChild(gGrid);
    svg.appendChild(gTrue);
    svg.appendChild(gRows);
    svg.appendChild(gCons);
    svg.appendChild(gNum);

    const lastY = L.row0 + 5 * L.pitch;

    for (let i = 0; i <= L.n + 1; i++) {
      gGrid.appendChild(el('line', {
        x1: (L.x0 + i * L.cell).toFixed(2), y1: (L.trueY + L.fs * 0.85).toFixed(1),
        x2: (L.x0 + i * L.cell).toFixed(2), y2: (lastY + L.fs * 0.85).toFixed(1),
        stroke: C.rule, 'stroke-width': 1, opacity: 0.4,
      }));
    }

    /* the travelling column highlight */
    this._band = el('rect', {
      x: 0, y: (L.trueY - L.fs).toFixed(1), width: L.cell.toFixed(2),
      height: (L.consY + L.fs - L.trueY).toFixed(1), rx: 6, fill: TINT_AUDIT, opacity: 0,
    });
    gBand.appendChild(this._band);

    /* --- written strand, faint reference -------------------------------- */
    const lab = el('text', {
      x: L.x0 - 14, y: L.trueY, 'text-anchor': 'end', 'dominant-baseline': 'central',
      'font-size': L.labFs, fill: C.muted, class: 'egv-s',
    });
    lab.textContent = 'written';
    gTrue.appendChild(lab);
    for (let i = 0; i < L.n; i++) {
      const t = el('text', {
        x: x(i).toFixed(2), y: L.trueY, 'text-anchor': 'middle', 'dominant-baseline': 'central',
        'font-size': L.fs, fill: BASE[L.str[i]] || C.ink, opacity: 0.55,
      });
      t.textContent = L.str[i];
      gTrue.appendChild(t);
    }

    /* --- six reads, starting where beat 4 left them ---------------------- */
    this._rows = [];
    const ballots = [];
    for (let c = 0; c < L.n; c++) ballots.push([]);

    for (let k = 0; k < 6; k++) {
      const y = L.row0 + k * L.pitch;
      const g = el('g', null);
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
        const my = y + L.fs * 0.78;
        const xs = x(ev.post) - L.cell * 0.5;
        const mg = el('g', null);
        mg.appendChild(el('line', {
          x1: xs.toFixed(2), y1: my.toFixed(1), x2: (L.x0 + strandW).toFixed(2), y2: my.toFixed(1),
          stroke: C.cost, 'stroke-width': 2, 'stroke-linecap': 'round', opacity: 0.62,
        }));
        const tipX = (ev.dir < 0 ? xs : xs + L.cell * 0.22) + ev.dir * 9;
        mg.appendChild(el('path', {
          d: 'M' + tipX.toFixed(2) + ' ' + my.toFixed(1) + ' l' + (-ev.dir * 9).toFixed(1) + ' -4.5 l0 9 z',
          fill: C.cost,
        }));
        marks.push(mg);
        g.appendChild(mg);
      }

      const nodes = [];
      const byCol = new Array(L.n).fill(null);
      for (const gl of built.glyphs) {
        let chip = null;
        if (gl.kind === 'sub') {
          chip = el('rect', {
            y: (y - L.fs * 0.62).toFixed(2), width: (L.cell * 0.80).toFixed(2),
            height: (L.fs * 1.24).toFixed(2), rx: 5, fill: TINT_COST,
          });
          g.appendChild(chip);
        }
        let gap = null;
        if (gl.kind === 'del') {
          gap = el('text', {
            x: x(gl.col).toFixed(2), y, 'text-anchor': 'middle', 'dominant-baseline': 'central',
            'font-size': L.fs, fill: C.cost, opacity: 0,
          });
          gap.textContent = '–';
          g.appendChild(gap);
        }
        const t = el('text', {
          'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': L.fs,
          fill: gl.kind === 'keep' ? C.ink : C.cost,
        });
        t.textContent = gl.ch;
        g.appendChild(t);
        const rec = { gl, t, chip, gap };
        nodes.push(rec);
        if (gl.kind !== 'ins') {
          byCol[gl.col] = rec;
          ballots[gl.col].push(gl.kind === 'del' ? '-' : gl.ch);
        }
      }
      this._rows.push({ g, y, nodes, marks, byCol });
    }

    /* --- the vote -------------------------------------------------------- */
    this._cols = [];
    let wrong = 0;
    for (let c = 0; c < L.n; c++) {
      const r = tally(ballots[c]);
      const ok = r.winner === L.str[c];
      if (!ok) wrong++;
      this._cols.push({ winner: r.winner, ok, ballots: ballots[c] });
    }
    this._wrong = wrong;

    /* --- consensus row --------------------------------------------------- */
    this._consCard = el('rect', {
      x: (L.x0 - 16).toFixed(2), y: (L.consY - L.fs * 0.95).toFixed(2),
      width: (strandW + 32).toFixed(2), height: (L.fs * 1.9).toFixed(2),
      rx: 12, fill: C.surface, stroke: C.rule, 'stroke-width': 1.5, opacity: 0,
    });
    gCons.appendChild(this._consCard);
    this._consLab = el('text', {
      x: L.x0 - 16, y: L.consY - L.fs * 1.32, 'font-size': L.labFs, fill: C.muted,
      class: 'egv-s', opacity: 0, 'letter-spacing': '0.05em',
    });
    this._consLab.textContent = 'consensus';
    gCons.appendChild(this._consLab);

    this._cons = [];
    for (let c = 0; c < L.n; c++) {
      const col = this._cols[c];
      const t = el('text', {
        'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': L.fs,
        fill: col.ok ? C.gain : C.cost, opacity: 0,
      });
      t.textContent = col.winner === '-' ? '–' : col.winner;
      gCons.appendChild(t);
      let mark = null;
      if (!col.ok) {
        mark = el('path', {
          d: 'M-4.5 -4.5 L4.5 4.5 M4.5 -4.5 L-4.5 4.5',
          stroke: C.cost, 'stroke-width': 2, 'stroke-linecap': 'round', fill: 'none', opacity: 0,
        });
        gCons.appendChild(mark);
      }
      this._cons.push({ t, mark });
    }

    /* --- the number ------------------------------------------------------ */
    this._num = el('text', {
      x: L.CW / 2, y: L.numY, 'text-anchor': 'middle', 'font-size': L.numFs,
      fill: C.ink, class: 'egv-n', opacity: 0,
    });
    this._num.textContent = '0.0%';
    gNum.appendChild(this._num);
  },

  update(p) {
    this._p = (this._ctx && this._ctx.reduced) ? 1 : clamp(p);
    this._paint(this._p);
  },

  _paint(p) {
    const L = this._L;
    if (!L) return;
    const x = this._x;

    const A = easeInOut(seg(p, 0.02, 0.26));               // align onto the grid
    const sweep = -1 + (L.n + 2.2) * seg(p, 0.30, 0.72);   // voting sweep, in columns
    const tCol = (c) => clamp((sweep - c) / 1.4);

    for (let k = 0; k < 6; k++) {
      const row = this._rows[k];
      for (const n of row.nodes) {
        const gl = n.gl;
        const xp = lerp(x(gl.post), x(gl.pre), A);
        if (gl.kind === 'ins') {
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + (row.y - 8 * A).toFixed(2)
            + ') scale(' + lerp(1, 0.4, A).toFixed(3) + ')');
          setAttr(n.t, 'opacity', ((1 - A) * (1 - A)).toFixed(3));
          continue;
        }
        const t = tCol(gl.col);
        const win = this._cols[gl.col].winner;
        const mine = gl.kind === 'del' ? '-' : gl.ch;
        const loser = mine !== win;
        const dim = lerp(1, loser ? 0.16 : 1, t);

        if (gl.kind === 'del') {
          setAttr(n.t, 'opacity', '0');
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + row.y.toFixed(2) + ')');
          if (n.gap) setAttr(n.gap, 'opacity', (A * 0.55 * dim).toFixed(3));
        } else {
          const base = gl.kind === 'keep' ? 0.76 : 1;
          setAttr(n.t, 'transform', 'translate(' + xp.toFixed(2) + ' ' + row.y.toFixed(2) + ')');
          if (gl.kind === 'keep') {
            setAttr(n.t, 'fill', gl.post !== gl.pre ? mixHex(C.ink, C.cost, 0.34 * (1 - A)) : C.ink);
          }
          setAttr(n.t, 'opacity', (base * dim).toFixed(3));
          if (n.chip) {
            setAttr(n.chip, 'x', (xp - L.cell * 0.40).toFixed(2));
            setAttr(n.chip, 'opacity', dim.toFixed(3));
          }
        }
      }
      for (const m of row.marks) setAttr(m, 'opacity', ((1 - A) * (1 - A)).toFixed(3));
    }

    /* sweep band */
    if (sweep > -0.6 && sweep < L.n + 0.6) {
      setAttr(this._band, 'x', (x(clamp(sweep, 0, L.n - 1)) - L.cell / 2).toFixed(2));
      setAttr(this._band, 'opacity', (0.9 * clamp(sweep + 0.6, 0, 1) * clamp(L.n + 0.6 - sweep, 0, 1)).toFixed(3));
    } else {
      setAttr(this._band, 'opacity', '0');
    }

    /* consensus */
    const card = easeOut(seg(p, 0.24, 0.36));
    setAttr(this._consCard, 'opacity', card.toFixed(3));
    setAttr(this._consLab, 'opacity', card.toFixed(3));
    for (let c = 0; c < L.n; c++) {
      const t = easeOut(tCol(c));
      const o = this._cons[c];
      setAttr(o.t, 'transform', 'translate(' + x(c).toFixed(2) + ' '
        + (L.consY - 22 * (1 - t)).toFixed(2) + ') scale(' + lerp(0.7, 1, t).toFixed(3) + ')');
      setAttr(o.t, 'opacity', t.toFixed(3));
      if (o.mark) {
        setAttr(o.mark, 'transform', 'translate(' + x(c).toFixed(2) + ' ' + (L.consY + L.fs * 1.34).toFixed(2) + ')');
        setAttr(o.mark, 'opacity', (t * t).toFixed(3));
      }
    }

    /* the number */
    const nt = seg(p, 0.74, 0.96);
    const v = TARGET * easeOut(nt);
    const txt = v.toFixed(1) + '%';
    if (this._num.textContent !== txt) this._num.textContent = txt;
    setAttr(this._num, 'opacity', clamp(seg(p, 0.72, 0.80)).toFixed(3));
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
