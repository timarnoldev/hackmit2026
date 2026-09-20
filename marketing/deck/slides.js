/* Erbgut deck: generated and data-driven slide content.
 *
 * Everything that depends on results reads window.RESULTS (results.js, or results.sample.js
 * with ?sample). A missing value (null) always renders as an amber dashed placeholder,
 * "[RESULT: ...]", never as a number. Exposes window.SlideHooks for deck.js.
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------ helpers */

  const ICON = {
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    cross: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    none: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12h8"/>',
    tuned: '<path d="M4 8h9M18 8h2M4 16h3M12 16h8"/><circle cx="15.5" cy="8" r="2.4"/><circle cx="9.5" cy="16" r="2.4"/>',
    pending: '<circle cx="12" cy="12" r="8.5" stroke-dasharray="3.4 3.1"/>',
    down: '<path d="M12 5v14M6 13l6 6 6-6"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    equal: '<path d="M6 9.5h12M6 14.5h12"/>',
  };
  const icon = (name) =>
    `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${ICON[name]}</svg>`;

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const isNum = (v) => typeof v === 'number' && isFinite(v);
  const isText = (v) => typeof v === 'string' && v.trim() !== '';
  const get = (obj, path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);

  const fmt = {
    reads: (v) => (Number.isInteger(v) ? String(v) : v.toFixed(1)),
    bpb: (v) => v.toFixed(2),
    auc: (v) => v.toFixed(2),
    pct: (v) => (v * 100).toFixed(1) + '%',
    num: (v) => String(v),
    text: (v) => esc(v),
  };
  const val = (v, kind) => `<span class="val">${fmt[kind || 'reads'](v)}</span>`;

  /** Amber dashed placeholder. `what` is specific enough to fill it without guessing. */
  function ph(what, extra) {
    const label = /^[A-Z]+:/.test(what) ? what : 'RESULT: ' + what;
    return `<span class="ph${extra ? ' ' + extra : ''}">[${esc(label)}]</span>`;
  }

  const VERDICTS = {
    'pays off': { cls: 'v-pays', icon: 'check', text: 'Pays off', say: 'pays off' },
    'no measurable benefit': { cls: 'v-none', icon: 'none', text: 'No measurable benefit', say: 'buys nothing measurable' },
    harmful: { cls: 'v-harm', icon: 'cross', text: 'Harmful', say: 'hurts' },
    'tuned is better': { cls: 'v-tuned', icon: 'tuned', text: 'Tuned is better', say: 'is beaten by the tuned value' },
    'default is fine': { cls: 'v-fine', icon: 'check', text: 'Default is fine', say: 'is fine as it is' },
  };
  const verdictOf = (v) => (isText(v) ? VERDICTS[v.trim().toLowerCase()] : null);
  function badge(v, what) {
    const d = verdictOf(v);
    if (!d) return `<span class="verdict v-pending">${icon('pending')}[RESULT: ${esc(what)}]</span>`;
    return `<span class="verdict ${d.cls}">${icon(d.icon)}${d.text}</span>`;
  }

  const CH = { nanopore: 'Nanopore', illumina: 'Illumina' };
  const validPt = (p) => p && isNum(p.bpb) && isNum(p.reads);

  /* ------------------------------------------------------------ DNA letters */

  const TOKEN = /\[([ACGT])>([ACGT])\]|\[\+([ACGT])\]|\[-([ACGT])\]|([ACGT])/g;

  /** "CAGA[C>T]GG[+A]C[-G]" : substitution, insertion, deletion. Static = show the end state only. */
  function seqHTML(s, isStatic) {
    let out = '';
    let i = 0;
    let m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(s))) {
      const st = `style="--i:${i}"`;
      if (m[5]) out += `<span class="nt nt-${m[5]}" ${st}>${m[5]}</span>`;
      else if (m[1]) {
        out += isStatic
          ? `<span class="nt nt-${m[2]} err-sub" ${st}>${m[2]}</span>`
          : `<span class="nt sub" ${st}><b class="o nt-${m[1]}">${m[1]}</b><b class="n nt-${m[2]}">${m[2]}</b></span>`;
      } else if (m[3]) {
        out += isStatic
          ? `<span class="nt nt-${m[3]} err-ins" ${st}>${m[3]}</span>`
          : `<span class="nt ins nt-${m[3]}" ${st}>${m[3]}</span>`;
      } else if (m[4]) {
        out += isStatic ? `<span class="err-del"></span>` : `<span class="nt del nt-${m[4]}" ${st}>${m[4]}</span>`;
      }
      i++;
    }
    return out;
  }

  function expandSeqs(root) {
    root.querySelectorAll('[data-seq]').forEach((el) => {
      el.innerHTML = seqHTML(el.getAttribute('data-seq'), el.hasAttribute('data-static'));
      el.setAttribute('aria-label', el.getAttribute('data-seq').replace(/\[[^\]]*\]/g, ''));
    });
  }

  function maxRun(s) {
    let best = { len: 1, start: 0 };
    let start = 0;
    for (let i = 1; i <= s.length; i++) {
      if (i === s.length || s[i] !== s[start]) {
        if (i - start > best.len) best = { len: i - start, start };
        start = i;
      }
    }
    return best;
  }
  const gcShare = (s) => (s.split('').filter((c) => c === 'G' || c === 'C').length / s.length);

  /* ------------------------------------------------------------ logo mark */

  /* marketing/logo/erbgut-mark-small.svg, inlined so the tokens in deck.css can
     paint it. Same 64x64 box as before, so every .mark size rule still holds. */
  const MARK =
    '<svg class="mark" viewBox="0 0 64 64" aria-hidden="true">' +
    '<path class="strand s1" d="M1.5 32C11.56 13.04 23.77 8 32 8C40.23 8 52.44 13.04 62.5 32" fill="none" stroke-width="9" stroke-linecap="round"/>' +
    '<path class="strand s2" d="M1.5 32C11.56 50.96 23.77 56 32 56C40.23 56 52.44 50.96 62.5 32" fill="none" stroke-width="9" stroke-linecap="round"/>' +
    '<rect class="base b1" x="10.5" y="18" width="10.5" height="28" rx="5.25"/>' +
    '<rect class="base b2" x="26.75" y="11" width="10.5" height="42" rx="5.25"/>' +
    '<rect class="base b3" x="43" y="18" width="10.5" height="28" rx="5.25"/>' +
    '</svg>';

  /* ------------------------------------------------------------ slide 1: ribbon */

  function buildRibbon() {
    const track = document.querySelector('#s-title .ribbon-track');
    if (!track) return;
    const chunk = 'ACTGAGGGGTCAAGCTTAGCC';
    const run = maxRun(chunk);
    const w = 44;
    let html = '';
    for (let k = 0; k < 6; k++) {
      html +=
        `<span class="chunk"><span class="seq">${seqHTML(chunk)}</span>` +
        `<span class="dimline" style="left:${run.start * w + 6}px;width:${run.len * w - 12}px"></span></span>`;
    }
    track.innerHTML = html;
  }

  /* ------------------------------------------------------------ slide 4: candidates */

  // Same chunk, eight seeds. Rule checks and risk scores below are computed, not typed in.
  // #6 passes every hand rule (run of 3 is legal) but carries the real CCCT deletion hot spot
  // from docs/ERRORS.md, so the risk model, not the hand rule, is what catches it.
  const CANDS = [
    'ATACACGTCAGCACGAAACT',
    'TTCGTACCTTGGGGGTCGTT',
    'ACCACTCTGTTCCCACGAGC',
    'GGCATTTCTGGATGGCCAGC',
    'ATTGTGCTTGTTCAATTCTT',
    'AGTCACCCTGATGCAGTCAG',
    'GGCTCCGACGAATTTTTAAT',
    'GCACGGAGTGGTTAGGCTTG',
  ];
  // Real hot-spot families measured on Nanopore reads (docs/ERRORS.md, docs/NUMBERS.md).
  const HOT_MOTIFS = ['CGGG', 'CCCG', 'CCCT', 'GGGA'];
  function riskOf(s) {
    const dev = Math.abs(gcShare(s) - 0.5);
    let score = 5 + Math.round(dev * 60);
    const hit = HOT_MOTIFS.find((m) => s.includes(m));
    if (hit) score += 34;
    return { score: Math.min(score, 96), hit };
  }

  function buildCandidates() {
    const host = document.getElementById('cands');
    if (!host) return;
    const W = 27;
    const meta = CANDS.map((s) => {
      const run = maxRun(s);
      const gc = gcShare(s);
      const runFail = run.len > 3;
      const gcFail = gc < 0.4 || gc > 0.6;
      const fail = runFail || gcFail;
      return { s, run, gc, runFail, gcFail, fail, risk: fail ? null : riskOf(s) };
    });
    let bestIdx = -1;
    let bestScore = Infinity;
    meta.forEach((m, i) => {
      if (!m.fail && m.risk.score < bestScore) {
        bestScore = m.risk.score;
        bestIdx = i;
      }
    });
    host.innerHTML = meta.map((m, i) => {
      const { s, run, gc, runFail, gcFail, fail, risk } = m;
      const why = runFail ? `run of ${run.len}` : gcFail ? `GC ${Math.round(gc * 100)}%, outside 40 to 60%` : 'passes';
      const isBest = i === bestIdx;
      const isHot = !fail && !!risk.hit;
      return (
        `<div class="cand ${fail ? 'fail' : 'pass'}${isBest ? ' kept' : ''}${isHot ? ' airisk' : ''}" style="--i:${i}">` +
        `<span class="no">#${i + 1}</span>` +
        `<span class="seqwrap"><span class="seq">${seqHTML(s)}</span>` +
        (runFail ? `<span class="runmark" style="left:${run.start * W + 3}px;width:${run.len * W - 6}px"></span>` : '') +
        `<span class="strike"></span></span>` +
        `<span class="why ${fail ? 'bad' : 'ok'}"><span class="why-txt">${icon(fail ? 'cross' : 'check')}${why}</span>` +
        (!fail ? `<span class="risk">${risk.score}%</span>` : '') +
        (isHot ? `<span class="hotflag">${icon('cross')}${risk.hit}</span>` : '') +
        `</span>` +
        (isBest ? '<span class="keep">kept</span>' : '') +
        `</div>`
      );
    }).join('');
  }

  /* ------------------------------------------------------------ slide 5: rule audit */

  function readsLine(rule, cell) {
    if (!cell) return '';
    const on = cell.readsOn;
    const off = cell.readsOff;
    const r = (v) => (isNum(v) ? val(v) : '<span class="val">not reached</span>');
    if (!isNum(on) && !isNum(off)) return '';
    if (/^redundancy/.test(rule.id || '')) {
      const tr = isNum(cell.tunedRedundancy) ? `${Math.round(cell.tunedRedundancy * 100)}%` : 'tuned';
      let s = `30%: ${r(on)} reads, ${tr}: ${r(off)} reads`;
      if (isNum(cell.bpbOn) && isNum(cell.bpbOff)) s += `<br>${val(cell.bpbOn, 'bpb')} to ${val(cell.bpbOff, 'bpb')} bits per base`;
      return s;
    }
    return `with rule ${r(on)}, without ${r(off)} reads per strand`;
  }

  function auditRows(R) {
    return Array.isArray(R && R.ruleAudit) ? R.ruleAudit : [];
  }

  function contrastRow(R) {
    const rows = auditRows(R);
    for (let k = 0; k < rows.length; k++) {
      const a = verdictOf(get(rows[k], 'nanopore.verdict'));
      const b = verdictOf(get(rows[k], 'illumina.verdict'));
      if (a && b && a !== b) return k;
    }
    return -1;
  }
  function allVerdictsKnown(R) {
    const rows = auditRows(R);
    return rows.length > 0 && rows.every((r) => verdictOf(get(r, 'nanopore.verdict')) && verdictOf(get(r, 'illumina.verdict')));
  }

  function buildAudit(R) {
    const host = document.getElementById('audit-table');
    if (!host) return;
    const rows = auditRows(R);
    let html =
      '<div class="th"></div>' +
      '<div class="th">Nanopore <small>calibrated on real reads</small></div>' +
      '<div class="th">Illumina <small>calibrated on real reads</small></div>';
    rows.forEach((row, idx) => {
      html +=
        `<div class="cell rulecell" data-row="${idx}"><span class="toggle" style="--i:${idx}"></span>` +
        `<div><div class="rt">${esc(row.label || row.id)}</div><div class="rs">${esc(row.detail || row.id || '')}</div></div></div>`;
      ['nanopore', 'illumina'].forEach((ch) => {
        const cell = row[ch] || {};
        html +=
          `<div class="cell c-${ch}" style="--i:${idx}"><span class="measuring"></span>` +
          `<div class="v">${badge(cell.verdict, `verdict, ${CH[ch]}`)}</div>` +
          `<div class="v reads">${readsLine(row, cell)}</div></div>`;
      });
    });
    html += '<div class="rowhl" aria-hidden="true"></div>';
    host.innerHTML = html;

    const legend = document.getElementById('audit-legend');
    if (legend) {
      legend.innerHTML = ['pays off', 'no measurable benefit', 'harmful', 'tuned is better']
        .map((v) => badge(v))
        .join('');
    }

    const callout = document.getElementById('audit-callout');
    if (callout) {
      const k = contrastRow(R);
      if (k >= 0) {
        const row = rows[k];
        callout.innerHTML =
          `<span class="big">Same rule, different verdict per channel.</span>` +
          `<span class="muted" style="font-size:28px">${esc(row.label || row.id)}: ${verdictOf(row.nanopore.verdict).text.toLowerCase()} on Nanopore, ${verdictOf(row.illumina.verdict).text.toLowerCase()} on Illumina</span>`;
      } else if (allVerdictsKnown(R)) {
        callout.innerHTML = `<span class="big">Same verdict on both channels. Now it's measured, not assumed.</span>`;
      } else {
        callout.innerHTML = ph('same rule, different verdict per channel, or the same answer on both', 'block');
      }
    }
  }

  function placeAuditHighlight(R) {
    const host = document.getElementById('audit-table');
    if (!host) return;
    const hl = host.querySelector('.rowhl');
    const k = contrastRow(R);
    if (!hl) return;
    if (k < 0) {
      hl.style.display = 'none';
      return;
    }
    const cell = host.querySelector(`.rulecell[data-row="${k}"]`);
    if (!cell) return;
    hl.style.display = '';
    hl.style.top = cell.offsetTop - 6 + 'px';
    hl.style.height = cell.offsetHeight + 12 + 'px';
  }

  /* ------------------------------------------------------------ slide 6: hot spots */

  // Passes every hand rule (max run 3, GC 57%) but carries three real Nanopore hot-spot families.
  const T2_RISKY = 'ATCGGGTACCCTGAAGTCAGCCCGATTGCA';
  // Another candidate for the same slot: max run 2, GC 50%, none of those contexts.
  const T2_SAFE = 'TGACTCAGGATCCATGCAGTTCAGCATGAC';
  const T2_HOT = [
    { motif: 'CGGG', label: 'substitutions around', pos: 'up' },
    { motif: 'CCCT', label: 'deletions right after', pos: 'down' },
    { motif: 'CCCG', label: 'substitutions around', pos: 'up' },
  ];

  function buildTier2() {
    const host = document.getElementById('t2-strandbox');
    if (!host) return;
    const W = 33;
    let letters = '';
    for (let i = 0; i < T2_RISKY.length; i++) {
      const a = T2_RISKY[i];
      const b = T2_SAFE[i];
      letters += `<span class="nt" style="--i:${i}"><span class="o nt-${a}">${a}</span><span class="n nt-${b}">${b}</span></span>`;
    }
    let hots = '';
    T2_HOT.forEach((h, k) => {
      const at = T2_RISKY.indexOf(h.motif);
      if (at < 0) return;
      hots +=
        `<span class="hot" style="--k:${k};left:${at * W - 6}px;width:${h.motif.length * W + 12}px">` +
        `<span class="hotlab ${h.pos}">${h.label} <span class="mono">${h.motif}</span></span></span>`;
    });
    const run = maxRun(T2_RISKY);
    const gc = Math.round(gcShare(T2_RISKY) * 100);
    host.innerHTML =
      `<div class="kept">${icon('check')}Kept another candidate: same data, same density, none of those contexts</div>` +
      `<div class="strandwrap"><div class="strand"><span class="seq strandline">${letters}</span></div>${hots}</div>` +
      `<div class="checks">` +
      `<span class="pill">${icon('check')}longest run ${run.len}</span>` +
      `<span class="pill">${icon('check')}GC ${gc}%</span>` +
      `<span class="pill">${icon('check')}passes every hand rule</span>` +
      `<span class="pill illus">illustration, real strands are 110 letters</span></div>`;
  }

  /* ------------------------------------------------------------ appendix A0: architecture */

  // One canvas, 1680 x 700. Builds: 1 the storage path, 2 the polisher, 3 the loop, 4 evaluation
  // and the firewall. "learned" boxes are the two learned models, everything else is classic code.
  // One canvas, 1680 x 700. Five boxes, four builds: 1 the storage path, 2 the polisher,
  // 3 the loop that feeds the encoder, 4 the firewall. The arrows carry the argument, so the
  // boxes carry a title and almost nothing else.
  const ARCH_BOXES = [
    { f: 1, x: 0, y: 150, w: 380, h: 170, title: 'Encoder',
      text: '8 to 32 candidates per slot' },
    { f: 1, x: 450, y: 150, w: 340, h: 170, title: 'Simulator A',
      text: 'the channel, fit on real reads' },
    { f: 1, x: 860, y: 115, w: 420, h: 240, title: 'Decoder', subs: [
      { f: 1, title: 'Majority vote' },
      { f: 2, learned: true, title: 'Polisher', text: '0.8M parameters' },
    ] },
    { f: 1, x: 1350, y: 150, w: 330, h: 170, title: 'Rebuild and score',
      text: '300 held-out trials' },
    { f: 3, x: 0, y: 470, w: 380, h: 150, learned: true, title: 'Risk model',
      text: '63k parameters' },
    { f: 4, x: 450, y: 0, w: 340, h: 100, firewall: true, title: 'Simulator B',
      text: 'different mechanisms' },
  ];

  function arrow(x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const hx = -uy;
    const hy = ux;
    const p = (a, b) => `${(x2 - ux * 14 + hx * a).toFixed(1)} ${(y2 - uy * 14 + hy * b).toFixed(1)}`;
    return `M${p(8, 8)} L${x2} ${y2} L${p(-8, -8)}`;
  }

  /* ------------------------------------------------------------ appendix: what the risk model learned */

  // Measured, from docs/LEARNED_RULES.md (the trained model of the final run, probed with patterns).
  const RUN_RISK = [[1, 0.429], [2, 0.430], [3, 0.436], [4, 0.460], [5, 0.518], [6, 0.603], [7, 0.689], [8, 0.756], [10, 0.833]];
  const GC_RISK = [[0.2, 0.453], [0.3, 0.455], [0.4, 0.459], [0.5, 0.465], [0.6, 0.473], [0.7, 0.482], [0.8, 0.489]];
  const KMERS = [['GGGGG', 0.707], ['CCCCC', 0.685], ['AGGGG', 0.638], ['TCCCC', 0.615], ['TTTTT', 0.613]];
  const KMER_BG = 0.526;

  function buildLearned() {
    const host = document.getElementById('learned');
    if (!host) return;
    const W = 790;
    const H = 318;
    const m = { l: 96, r: 26, t: 26, b: 64 };
    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    const Y = (v) => m.t + ph - ((v - 0.4) / 0.5) * ph;   // both charts share 0.40 to 0.90
    const yaxis = () => {
      let g = '';
      [0.4, 0.5, 0.6, 0.7, 0.8, 0.9].forEach((t) => {
        g += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/>` +
             `<text class="tick" x="${m.l - 14}" y="${Y(t) + 7}" text-anchor="end">${t.toFixed(1)}</text>`;
      });
      return g + `<path class="axis" d="M${m.l} ${m.t}V${m.t + ph}H${m.l + pw}"/>` +
        `<text class="alabel" transform="translate(26 ${m.t + ph / 2}) rotate(-90)" text-anchor="middle">Predicted risk</text>`;
    };
    const path = (pts, X) => pts.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join('');

    // 1. homopolymer response
    const Xr = (v) => m.l + ((v - 1) / 9) * pw;
    let a = yaxis();
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].forEach((t) => {
      a += `<text class="tick" x="${Xr(t)}" y="${m.t + ph + 32}" text-anchor="middle">${t}</text>`;
    });
    a += `<text class="alabel" x="${m.l + pw / 2}" y="${H - 8}" text-anchor="middle">Longest run of the same letter</text>`;
    a += `<rect class="band frag fade" data-f="1" x="${Xr(4)}" y="${m.t}" width="${Xr(5) - Xr(4)}" height="${ph}" rx="6"/>`;
    a += `<path class="curve frag fade" data-f="1" d="${path(RUN_RISK, Xr)}"/>`;
    RUN_RISK.forEach((p) => {
      a += `<circle class="dot frag fade" data-f="1" cx="${Xr(p[0])}" cy="${Y(p[1])}" r="7"/>`;
    });
    a += `<g class="frag fade" data-f="1"><line class="stdline" x1="${Xr(3)}" x2="${Xr(3)}" y1="${m.t}" y2="${m.t + ph}"/>` +
      `<text class="stdlab" x="${Xr(3) - 12}" y="${m.t + 26}" text-anchor="end">standard rule:</text>` +
      `<text class="stdlab" x="${Xr(3) - 12}" y="${m.t + 52}" text-anchor="end">no run over 3</text>` +
      `<text class="modellab" x="${Xr(5) + 14}" y="${m.t + 26}">the model would draw</text>` +
      `<text class="modellab" x="${Xr(5) + 14}" y="${m.t + 52}">the line at 4 to 5</text>` +
      `<text class="modellab warn" x="${Xr(5.8)}" y="${m.t + ph - 74}">measured, and it is wrong:</text>` +
      `<text class="modellab warn" x="${Xr(5.8)}" y="${m.t + ph - 50}">at 12 reads a limit of 4</text>` +
      `<text class="modellab warn" x="${Xr(5.8)}" y="${m.t + ph - 26}">recovers 33% of files, 3 does 81%</text></g>`;

    // 2. GC response, same scale, so flat looks flat
    const Xg = (v) => m.l + ((v - 0.2) / 0.6) * pw;
    let b = yaxis();
    [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8].forEach((t) => {
      b += `<text class="tick" x="${Xg(t)}" y="${m.t + ph + 32}" text-anchor="middle">${t.toFixed(1)}</text>`;
    });
    b += `<text class="alabel" x="${m.l + pw / 2}" y="${H - 8}" text-anchor="middle">GC content</text>`;
    b += `<path class="curve flat frag fade" data-f="2" d="${path(GC_RISK, Xg)}"/>`;
    GC_RISK.forEach((p) => {
      b += `<circle class="dot flat frag fade" data-f="2" cx="${Xg(p[0])}" cy="${Y(p[1])}" r="7"/>`;
    });
    b += `<text class="modellab frag fade" data-f="2" style="fill:var(--sa-audit)" x="${m.l + pw / 2}" y="${Y(0.465) - 30}" text-anchor="middle">3.6 points across the whole range</text>`;

    // 3. the patterns it names
    const KW = 790;
    const KH = 268;
    const km = { l: 150, r: 150, t: 16, b: 44 };
    const kpw = KW - km.l - km.r;
    const Xk = (v) => km.l + ((v - 0.45) / 0.33) * kpw;
    let k = '';
    KMERS.forEach((p, i) => {
      const y = km.t + i * 44;
      k += `<g class="frag fade" data-f="3" style="transition-delay:${i * 60}ms">` +
        `<text class="klab" x="${km.l - 18}" y="${y + 26}" text-anchor="end">${p[0]}</text>` +
        `<rect class="kbar" x="${km.l}" y="${y + 6}" width="${Math.max(2, Xk(p[1]) - km.l)}" height="26" rx="6"/>` +
        `<text class="kval" x="${Xk(p[1]) + 14}" y="${y + 27}">${p[1].toFixed(3)}</text></g>`;
    });
    k += `<g class="frag fade" data-f="3"><line x1="${Xk(KMER_BG)}" x2="${Xk(KMER_BG)}" y1="${km.t - 4}" y2="${km.t + 5 * 44 + 4}" stroke="var(--sa-muted)" stroke-width="3" stroke-dasharray="8 6"/>` +
      `<text class="kval" x="${Xk(KMER_BG)}" y="${km.t + 5 * 44 + 32}" text-anchor="middle">random background ${KMER_BG.toFixed(3)}</text></g>`;

    host.innerHTML =
      `<div class="panel frag fade" data-f="1" style="left:0;top:0;width:${W}px">` +
      `<h3>It found the homopolymer rule by itself</h3>` +
      `<p class="sub">Risk of a strand carrying one run of length r, Nanopore</p>` +
      `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Predicted risk against run length">${a}</svg></div>` +
      `<div class="panel frag fade" data-f="2" style="left:890px;top:0;width:${W}px">` +
      `<h3>It considers the GC rule pointless</h3>` +
      `<p class="sub">Same model, GC varied with runs capped at 3. Our rule audit agrees</p>` +
      `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Predicted risk against GC content">${b}</svg></div>` +
      `<div class="panel" style="left:0;top:424px;width:${KW}px">` +
      `<h3 class="frag fade" data-f="3">The patterns it names</h3>` +
      `<p class="sub frag fade" data-f="3">G and C runs rank above A and T runs. No hand rule makes that distinction</p>` +
      `<svg class="chart" width="${KW}" height="${KH}" viewBox="0 0 ${KW} ${KH}" role="img" aria-label="Most dangerous 5-mers">${k}</svg></div>` +
      `<div class="panel card frag fade" data-f="4" style="left:890px;top:424px;width:${W}px">` +
      `<div class="row">Worst <b>deletion</b> contexts in our real-read channel table: <span class="mono">GAAAG</span> 71x, <span class="mono">AAAAG</span> 13x. The model, which never saw that table: <span class="risk">0.934</span> and <span class="risk">0.973</span></div>` +
      `<div class="row">Worst <b>substitution</b> context: <span class="mono">CCCGA</span> 12.8x. The model shrugs: <span class="calm">0.444</span></div>` +
      `<div class="punch">It did not learn where errors happen. It learned <b>where errors are fatal</b>.</div>` +
      `<div class="note">Voting across reads repairs a substitution. A deletion shifts everything after it and destroys the strand. On Illumina every pattern comes back at risk 0.000, correctly.</div></div>`;
  }

  /* ------------------------------------------------------------ appendix: the two models */

  const TWO_LEFT = [
    { t: 'Input', p: 'Not raw letters. The classic decoder’s draft plus its vote columns: 17 numbers per position, votes for A, C, G, T, for a deletion, for an inserted letter and which one, coverage, the draft base, agreement and relative position, all as fractions' },
    { t: 'Trunk', p: 'Dilated 1D CNN. Stem of kernel 5, then 8 residual blocks with dilations 1, 2, 4, 8, 1, 2, 4, 8, at 128 channels, GroupNorm and GELU. About 0.8M parameters, receptive field about 65 positions', learned: true },
    { t: 'Two heads', p: 'Per position: keep, substitute to A, C, G or T, or delete. Per gap: nothing, or insert A, C, G or T' },
    { t: 'Output', p: 'The edits are applied so deletions and insertions balance, which keeps the strand length exact. Trained in 13 minutes' },
  ];
  const TWO_RIGHT = [
    { t: 'Input', p: 'One candidate strand as one-hot letters. Nothing else: no reads, no votes, no channel numbers' },
    { t: 'Trunk', p: 'A small 1D CNN over the letters', learned: true },
    { t: 'Labels', p: 'Each training strand is simulated 32 times and decoded, so the target is a failure rate, not a yes or no' },
    { t: 'Output', p: 'One number between 0 and 1: the predicted fraction of decoding attempts that fail for that strand' },
  ];
  const TWO_Y = [[64, 176], [256, 150], [422, 118], [566, 122]];

  function buildTwoModels() {
    const host = document.getElementById('twomodels');
    if (!host) return;
    const W = 760;
    const col = (items, x, f) =>
      items.map((b, i) => {
        const [y, h] = TWO_Y[i];
        return (
          `<div class="abox${b.learned ? ' learned' : ''} frag fade" data-f="${f}" style="left:${x}px;top:${y}px;width:${W}px;height:${h}px">` +
          `<h4>${b.t}</h4><p>${b.p}</p>${b.learned ? '<span class="tagl">learned</span>' : ''}</div>`
        );
      }).join('');
    const down = (x, y1, y2, f) =>
      `<path class="frag fade" data-f="${f}" d="M${x} ${y1} V${y2 - 14} ${arrow(x, y2 - 24, x, y2)}"/>`;
    let wires = '';
    [380, 1300].forEach((x, k) => {
      for (let i = 0; i < TWO_Y.length - 1; i++) {
        wires += down(x, TWO_Y[i][0] + TWO_Y[i][1], TWO_Y[i + 1][0], k + 1);
      }
    });
    host.innerHTML =
      `<div class="colhead frag fade" data-f="1" style="left:0;top:0;width:${W}px">Polisher<span class="lt">learned</span><span class="where">inside the decoder</span></div>` +
      `<div class="colhead frag fade" data-f="2" style="left:920px;top:0;width:${W}px">Risk model<span class="lt">learned</span><span class="where">inside the encoder</span></div>` +
      `<svg class="wires" width="1680" height="726" viewBox="0 0 1680 726" aria-hidden="true">${wires}</svg>` +
      col(TWO_LEFT, 0, 1) + col(TWO_RIGHT, 920, 2) +
      `<div class="closer frag fade" data-f="3" style="top:706px">The decoder’s failures become the risk model’s labels` +
      `<svg viewBox="0 0 46 28" aria-hidden="true"><path d="M2 14h36M30 5l9 9-9 9" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
      `so the encoder writes what the decoder can read</div>`;
  }

  function buildArchitecture() {
    const host = document.getElementById('arch');
    if (!host) return;
    const box = (b) => {
      const cls = ['abox', b.learned ? 'learned' : '', b.firewall ? 'firewall' : '', 'frag', 'fade'].filter(Boolean).join(' ');
      const inner = b.subs
        ? b.subs.map((s) =>
            `<div class="sub ${s.learned ? 'learned frag fade' : ''}"${s.learned ? ` data-f="${s.f}"` : ''}>` +
            `<b>${s.title}</b>${s.text ? `<p>${s.text}</p>` : ''}${s.learned ? '<span class="tagl">learned</span>' : ''}</div>`).join('')
        : `<p>${b.text || ''}</p>`;
      return (
        `<div class="${cls}" data-f="${b.f}" style="left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px">` +
        `<h4>${b.title}</h4>${inner}` +
        (b.learned && !b.subs ? '<span class="tagl">learned</span>' : '') +
        `</div>`
      );
    };
    const wire = (f, d, cls) => `<path class="frag fade ${cls || ''}" data-f="${f}" d="${d}"/>`;
    const label = (f, x, y, text, anchor, cls) =>
      `<text class="frag fade ${cls || ''}" data-f="${f}" x="${x}" y="${y}"${anchor ? ` text-anchor="${anchor}"` : ''}>${text}</text>`;

    const wires =
      // 1. the storage path, left to right at the row's mid line
      wire(1, `M380 235 H431 ${arrow(380, 235, 435, 235)}`) +
      wire(1, `M790 235 H841 ${arrow(790, 235, 845, 235)}`) +
      wire(1, `M1280 235 H1331 ${arrow(1280, 235, 1335, 235)}`) +
      // 3. the loop: the decoder's failures train the risk model, the risk model ranks candidates
      wire(3, `M1070 355 V545 H396 ${arrow(410, 545, 392, 545)}`) +
      label(3, 740, 528, 'where the decoder fails', 'middle') +
      wire(3, `M190 470 V340 ${arrow(190, 360, 190, 336)}`, 'learnedwire') +
      label(3, 212, 408, 'ranks every candidate', null, 'key') +
      // 4. the firewall: a second channel that only ever checks a result
      wire(4, `M415 235 V50 H434 ${arrow(415, 50, 438, 50)}`, 'dash') +
      label(4, 400, 132, 'firewall', 'end') +
      wire(4, `M790 50 H1070 V97 ${arrow(1070, 80, 1070, 101)}`, 'dash') +
      label(4, 1096, 44, 'checks a result, never tunes one');

    host.innerHTML =
      `<div class="legend"><span><i></i>classic code</span><span><i class="learned"></i>learned</span><span><i class="firewall"></i>firewall only</span></div>` +
      `<svg class="wires" width="1680" height="700" viewBox="0 0 1680 700" aria-hidden="true">${wires}</svg>` +
      ARCH_BOXES.map(box).join('');
  }

  /* ------------------------------------------------------------ slide 9: tier 2, isolated
   * The paired scorer experiment: everything fixed, only the ranking model changes.
   * Values live in results.js under tier2 (300 held-out trials per point).
   */

  function buildTier2Curve(R) {
    const T = (R && R.tier2) || {};
    const reads = Array.isArray(T.reads) ? T.reads : [];
    const rules = Array.isArray(T.rules) ? T.rules : [];
    const learned = Array.isArray(T.learned) ? T.learned : [];
    const ok = reads.length > 1 && rules.length === reads.length && learned.length === reads.length;

    const chips = document.getElementById('t2chips');
    if (chips) {
      const held = Array.isArray(T.held) && T.held.length ? T.held : ['same codec', 'same decoder', 'same seeds'];
      chips.innerHTML =
        `<span class="hlab frag fade" data-f="1">Held fixed</span>` +
        held.map((h, i) => `<span class="hchip frag fade" data-f="1" style="transition-delay:${i * 50}ms">${esc(h)}</span>`).join('') +
        `<span class="hchip change frag fade" data-f="1" style="transition-delay:${held.length * 50}ms">only the scorer changes</span>`;
    }

    const host = document.getElementById('t2plot');
    if (!host) return;
    if (!ok) {
      host.innerHTML = `<div class="pending-box" style="left:0;right:0;top:40px">${ph('tier 2 recovery curve, hand rules against the learned model')}</div>`;
      return;
    }
    const W = 1680;
    const H = 430;
    const m = { l: 130, r: 130, t: 26, b: 70 };
    const pw = W - m.l - m.r;
    const phh = H - m.t - m.b;
    const lo = reads[0];
    const hi = reads[reads.length - 1];
    const X = (v) => m.l + ((v - lo) / (hi - lo)) * pw;
    const Y = (v) => m.t + phh - v * phh;
    let g = '';
    [0, 0.25, 0.5, 0.75, 1].forEach((t) => {
      g += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/>` +
        `<text class="tick" x="${m.l - 16}" y="${Y(t) + 7}" text-anchor="end">${Math.round(t * 100)}%</text>`;
    });
    reads.forEach((r) => {
      g += `<text class="tick" x="${X(r)}" y="${m.t + phh + 34}" text-anchor="middle">${r.toFixed(1)}</text>`;
    });
    g += `<path class="axis" d="M${m.l} ${m.t}V${m.t + phh}H${m.l + pw}"/>` +
      `<text class="alabel" x="${m.l + pw / 2}" y="${H - 12}" text-anchor="middle">Reads per strand</text>` +
      `<text class="alabel" transform="translate(30 ${m.t + phh / 2}) rotate(-90)" text-anchor="middle">Files recovered</text>`;
    const line = (vals, cls, f, label, row) => {
      const d = vals.map((v, i) => `${i ? 'L' : 'M'}${X(reads[i]).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
      const ly = m.t + phh - 40 - row * 44;
      const lx = m.l + pw - 30;
      return `<g class="t2line ${cls} frag fade" data-f="${f}"><path d="${d}"/>` +
        vals.map((v, i) => `<circle cx="${X(reads[i])}" cy="${Y(v)}" r="9"/>`).join('') +
        `<path class="key" d="M${lx - 300} ${ly} H${lx - 250}"/>` +
        `<circle cx="${lx - 275}" cy="${ly}" r="9"/>` +
        `<text class="t2lab" x="${lx - 236}" y="${ly + 9}">${label}</text></g>`;
    };
    g += line(rules, 'rules', 2, 'ranked by the hand rules', 0);
    g += line(learned, 'learned', 3, 'ranked by the learned model', 1);

    // the gap at the read count where the two differ most
    let k = 0;
    reads.forEach((_, i) => { if (learned[i] - rules[i] > learned[k] - rules[k]) k = i; });
    const gap = Math.round((learned[k] - rules[k]) * 1000) / 10;
    const gx = X(reads[k]);
    const tx = gx + (pw / (reads.length - 1)) * 1.25;
    const ty = m.t + phh * 0.46;
    g += `<g class="t2gap frag fade" data-f="3">` +
      `<path d="M${gx} ${Y(rules[k]) - 12} V${Y(learned[k]) + 12}M${gx - 13} ${Y(rules[k]) - 12}H${gx + 13}M${gx - 13} ${Y(learned[k]) + 12}H${gx + 13}" fill="none" stroke="var(--sa-gain)" stroke-width="4" stroke-linecap="round"/>` +
      `<path d="M${gx + 16} ${(Y(rules[k]) + Y(learned[k])) / 2} H${tx - 16}" fill="none" stroke="var(--sa-gain)" stroke-width="2.5" opacity="0.5"/>` +
      `<text class="t2big" x="${tx}" y="${ty}">+${gap.toFixed(1)} points</text>` +
      `<text class="t2sub" x="${tx}" y="${ty + 34}">${Math.round(rules[k] * 100)}% of files come back, against ${(learned[k] * 100).toFixed(1)}%</text>` +
      `<text class="t2sub" x="${tx}" y="${ty + 62}">at ${reads[k].toFixed(1)} reads per strand</text></g>`;

    host.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of files recovered against reads per strand, candidates ranked by the hand rules or by the learned model">${g}</svg>`;

    const foot = document.getElementById('t2foot');
    if (foot) {
      const trials = isNum(T.trials) ? `${T.trials} held-out trials per point` : '';
      foot.innerHTML =
        `<span class="pill2 gain frag fade" data-f="3">${icon('check')}Choosing among candidates costs no density</span>` +
        (isText(T.reproduced) ? `<span class="pill2 audit frag fade" data-f="4">${esc(T.reproduced)}</span>` : '') +
        (trials ? `<span class="pill2 frag fade" data-f="2">${trials}</span>` : '');
    }
  }

  /* ------------------------------------------------------------ slide 12: against published decoders
   * docs/COMPARISON.md: TReconLM (TMLR 2025) Table 7, and our held-out split.
   */

  const FIELD_READS = [2, 4, 6, 10];
  // Their released fine-tuned checkpoint, run on our held-out clusters under our protocol,
  // scored on the 415 clusters that are outside their own fine-tuning split. docs/NUMBERS.md 6a.
  const FIELD = [
    { name: 'TReconLM, fine-tuned', v: [13.0, 75.7, 90.4, 96.4], cls: 'ahead' },
    { name: 'Erbgut', v: [7.2, 67.0, 88.4, 95.4], cls: 'ours' },
    { name: 'TReconLM, pretrained', v: [4.3, 58.1, 80.0, 89.4], cls: 'peer' },
    { name: 'Majority vote', v: [4.8, 40.0, 69.9, 84.8], cls: 'other' },
  ];

  function buildFieldChart() {
    const host = document.getElementById('fieldchart');
    if (host) {
      const W = 940;
      const H = 540;
      const m = { l: 80, r: 250, t: 20, b: 76 };
      const pw = W - m.l - m.r;
      const phh = H - m.t - m.b;
      const X = (i) => m.l + (i / (FIELD_READS.length - 1)) * pw;
      const Y = (v) => m.t + phh - (v / 100) * phh;
      let g = '';
      [0, 25, 50, 75, 100].forEach((t) => {
        g += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/>` +
          `<text class="tick" x="${m.l - 14}" y="${Y(t) + 7}" text-anchor="end">${t}%</text>`;
      });
      FIELD_READS.forEach((r, i) => {
        g += `<text class="tick" x="${X(i)}" y="${m.t + phh + 36}" text-anchor="middle">${r}</text>`;
      });
      g += `<path class="axis" d="M${m.l} ${m.t}V${m.t + phh}H${m.l + pw}"/>` +
        `<text class="alabel" x="${m.l + pw / 2}" y="${H - 14}" text-anchor="middle">Reads per cluster</text>` +
        `<text class="alabel" transform="translate(22 ${m.t + phh / 2}) rotate(-90)" text-anchor="middle">Exact strands</text>`;
      // labels at the right edge, pushed apart so they never collide
      const order = FIELD.map((f, i) => ({ i, y: Y(f.v[3]) })).sort((a, b) => a.y - b.y);
      const GAP = 34;
      order.forEach((o, k) => {
        if (k > 0 && o.y - order[k - 1].y < GAP) o.y = order[k - 1].y + GAP;
      });
      const labY = [];
      order.forEach((o) => { labY[o.i] = o.y; });
      FIELD.forEach((f, k) => {
        const d = f.v.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
        const fr = f.cls === 'ahead' ? 2 : f.cls === 'ours' ? 3 : f.cls === 'peer' ? 3 : 1;
        g += `<g class="fline ${f.cls} frag fade" data-f="${fr}" style="transition-delay:${k * 60}ms">` +
          `<path d="${d}"/>` +
          f.v.map((v, i) => `<circle cx="${X(i)}" cy="${Y(v)}" r="${f.cls === 'other' ? 5 : 8}"/>`).join('') +
          `<path class="lead" d="M${X(3) + 10} ${Y(f.v[3])} L${X(3) + 26} ${labY[k]}" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4"/>` +
          `<text class="flab" x="${X(3) + 32}" y="${labY[k] + 7}">${f.name}</text></g>`;
      });
      host.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Exact strand accuracy against reads per cluster, published methods and ours">${g}</svg>`;
    }

    const cost = document.getElementById('fieldcost');
    if (!cost) return;
    const row = (label, ratio, ours, theirs, oursW) =>
      `<div class="costrow frag fade" data-f="4"><div class="clab">${label}<em>${ratio}</em></div>` +
      `<div class="cbar"><i class="ours" style="width:${oursW}%"></i><span class="ours">${ours}</span></div>` +
      `<div class="cbar"><i class="theirs" style="width:100%"></i><span>${theirs}</span></div></div>`;
    cost.innerHTML =
      `<h3 class="frag fade" data-f="4">What that costs them</h3>` +
      row('Parameters', '48x fewer', '0.8M', '38.5M', 2.1) +
      row('Seconds for 2,000 clusters', '100x faster', '4.6 s', '463 s', 1.0) +
      `<div class="costrow frag fade" data-f="3"><div class="clab">Against their pretrained model<em>we win</em></div>` +
      `<div class="cbar"><span class="ours">88.4% at six reads</span></div>` +
      `<div class="cbar"><span>80.0% at six reads</span></div></div>`;
  }

  /* ------------------------------------------------------------ slide 14: the verdicts nobody else measures */

  function buildVerdicts(R) {
    const host = document.getElementById('verdicts');
    if (!host) return;
    const rows = (Array.isArray(R && R.ruleAudit) ? R.ruleAudit : []).filter((r) => !/^redundancy/.test(r.id || ''));
    let html = '<div class="vhead"></div><div class="vhead">Nanopore</div><div class="vhead">Illumina</div>';
    rows.forEach((row, i) => {
      html += `<div class="vname frag fade" data-f="3" style="transition-delay:${i * 90}ms">${esc(row.label || row.id)}</div>`;
      ['nanopore', 'illumina'].forEach((ch) => {
        const cell = row[ch] || {};
        const reads = isNum(cell.readsOn) && isNum(cell.readsOff)
          ? `${fmt.reads(cell.readsOn)} reads with it, ${fmt.reads(cell.readsOff)} without`
          : '';
        html += `<div class="vcell frag fade" data-f="3" style="transition-delay:${i * 90 + 60}ms">` +
          badge(cell.verdict, `verdict, ${CH[ch]}`) + (reads ? `<div class="vreads">${reads}</div>` : '') + '</div>';
      });
    });
    host.innerHTML = html;
  }

  /* ------------------------------------------------------------ slide 8: ablation and crossover */

  // The A and B rungs describe which decoder the run used, so they come from the data. A run made
  // with the classic decoder on every rung must not be labelled as if it used the polisher.
  function buildCrossover(R) {
    const host = document.getElementById('xm');
    if (!host) return;
    const X = (R && R.crossover) || {};
    const rows = [
      ['default', 'Default (B)'],
      ['nanopore', 'Tuned for Nanopore'],
      ['illumina', 'Tuned for Illumina'],
    ];
    const cols = ['nanopore', 'illumina'];
    let html = '<div></div>' + cols.map((c) => `<div class="h">${CH[c]} channel</div>`).join('');
    let n = 0;
    rows.forEach(([key, label]) => {
      html += `<div class="rh">${label}</div>`;
      cols.forEach((c) => {
        const v = get(X, `${key}.${c}`);
        const d = get(X, `default.${c}`);
        let cls = '';
        let cmp = '';
        if (v === 'not reached') {
          // A measured failure, not a missing number: this codec never recovered the file.
          html += `<div class="xc miss" style="--i:${n++}"><span class="val">not reached</span>` +
            `<span class="cmp">${icon('cross')}never recovers</span></div>`;
          return;
        }
        if (!isNum(v)) {
          html += `<div class="xc" style="--i:${n++}">${ph('reads')}</div>`;
          return;
        }
        if (key === 'default') {
          cls = 'base';
          cmp = 'baseline';
        } else if (isNum(d)) {
          if (v < d) {
            cls = 'better';
            cmp = icon('down') + 'fewer than default';
          } else if (v > d) {
            cls = 'worse';
            cmp = icon('up') + 'more than default';
          } else {
            cls = 'same';
            cmp = icon('equal') + 'same as default';
          }
        }
        const home = key === c ? ' home' : '';
        html += `<div class="xc ${cls}${home}" style="--i:${n++}"><span class="n">${fmt.reads(v)}</span><span class="cmp">${cmp}</span></div>`;
      });
    });
    html += `<div></div><div class="h" style="grid-column:span 2;font-weight:500;font-size:20px">Outlined: each codec on its own channel</div>`;
    host.innerHTML = html;
  }

  /* ------------------------------------------------------------ slide 9: calibration and firewall */

  // Held-out calibration, docs/ERRORS.md. Measured, not results.js: these are final.
  const CAL = {
    reads: [2, 4, 6, 10, 16],
    real: [4.9, 39.8, 66.5, 84.5, 90.1],
    sim: [3.6, 40.3, 63.7, 83.0, 90.3],
  };

  function buildCalibration() {
    const host = document.getElementById('calchart');
    if (!host) return;
    const W = 960;
    const H = 400;
    const m = { l: 86, r: 30, t: 24, b: 62 };
    const pw = W - m.l - m.r;
    const phh = H - m.t - m.b;
    const X = (v) => m.l + (v / 17) * pw;
    const Y = (v) => m.t + phh - (v / 100) * phh;
    let s = '';
    [0, 25, 50, 75, 100].forEach((t) => {
      s += `<line class="grid" x1="${m.l}" x2="${m.l + pw}" y1="${Y(t)}" y2="${Y(t)}"/><text class="tick" x="${m.l - 14}" y="${Y(t) + 7}" text-anchor="end">${t}%</text>`;
    });
    CAL.reads.forEach((r) => {
      s += `<text class="tick" x="${X(r)}" y="${m.t + phh + 32}" text-anchor="middle">${r}</text>`;
    });
    s += `<path class="axis" d="M${m.l} ${m.t}V${m.t + phh}H${m.l + pw}"/>`;
    s += `<text class="alabel" x="${m.l + pw / 2}" y="${H - 4}" text-anchor="middle">Reads per strand</text>`;
    s += `<text class="alabel" transform="translate(22 ${m.t + phh / 2}) rotate(-90)" text-anchor="middle">Exact strands</text>`;
    const line = (arr, cls) => {
      let len = 0;
      for (let i = 1; i < arr.length; i++) len += Math.hypot(X(CAL.reads[i]) - X(CAL.reads[i - 1]), Y(arr[i]) - Y(arr[i - 1]));
      const d = arr.map((v, i) => `${i ? 'L' : 'M'}${X(CAL.reads[i]).toFixed(1)} ${Y(v).toFixed(1)}`).join('');
      let out = `<path class="line ${cls}" d="${d}" style="--len:${Math.ceil(len) + 2}"/>`;
      arr.forEach((v, i) => {
        out += `<circle class="dot ${cls}" style="--i:${i}" cx="${X(CAL.reads[i])}" cy="${Y(v)}" r="${cls === 'real' ? 9 : 10}"/>`;
      });
      return out;
    };
    s += line(CAL.real, 'real') + line(CAL.sim, 'sim');
    s +=
      `<g class="lg"><line x1="${m.l + 30}" x2="${m.l + 80}" y1="${m.t + 22}" y2="${m.t + 22}" stroke="var(--sa-ink)" stroke-width="5"/>` +
      `<text x="${m.l + 94}" y="${m.t + 30}" style="font-size:23px;fill:var(--sa-ink);font-weight:600">Real reads, held-out</text>` +
      `<line x1="${m.l + 30}" x2="${m.l + 80}" y1="${m.t + 62}" y2="${m.t + 62}" stroke="var(--sa-audit)" stroke-width="5"/>` +
      `<circle cx="${m.l + 55}" cy="${m.t + 62}" r="9" fill="var(--sa-paper)" stroke="var(--sa-audit)" stroke-width="4"/>` +
      `<text x="${m.l + 94}" y="${m.t + 70}" style="font-size:23px;fill:var(--sa-audit);font-weight:600">Simulator A</text></g>`;
    host.innerHTML =
      `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Exact strands vs reads per strand, real held-out reads and Simulator A">${s}</svg>` +
      `<div class="callout3 frag" data-f="2" style="left:500px;top:200px">Within about 3 points at every read count</div>`;
    host.style.position = 'relative';
  }

  function statusBadge(entry, what) {
    const st = entry && isText(entry.status) ? entry.status.trim().toLowerCase() : null;
    const note = entry && isText(entry.note) ? ` <span class="muted" style="font-weight:500">${esc(entry.note)}</span>` : '';
    if (st === 'holds') return `<span class="verdict v-pays">${icon('check')}Holds</span>${note}`;
    if (st === 'does not hold') return `<span class="verdict v-harm">${icon('cross')}Does not hold, reported as such</span>${note}`;
    if (st === 'measured') return `<span class="verdict v-none">${icon('equal')}Measured</span>${note}`;
    return `<span class="verdict v-pending">${icon('pending')}[RESULT: ${esc(what)}]</span>`;
  }

  function buildFirewall(R) {
    const host = document.getElementById('firewall');
    if (!host) return;
    const F = (R && R.firewall) || {};
    const steps = [
      ['Test 1: Simulator A', 'Held-out seeds', statusBadge(F.simAHeldout, 'gain on held-out seeds')],
      ['Test 2: Simulator B', 'Different mechanisms, context table from another dataset, never optimized on', statusBadge(F.simB, 'does the gain survive Simulator B')],
      ['Test 3: Real reads', "The risk model's ranking on held-out real clusters", statusBadge(F.real, 'risk model ranking on real reads')],
    ];
    host.innerHTML =
      `<h3 class="frag fade" data-f="3">Sim-to-real firewall</h3>` +
      `<p class="fwsub frag fade" data-f="3">Optimized on Simulator A with train seeds only, then:</p>` +
      steps
        .map(
          (s, i) =>
            `<div class="fwstep" style="--i:${i}"><span class="stepno">${i + 1}</span>` +
            `<div><div class="t">${s[0]}</div><div class="d">${s[1]}</div>${s[2] ? `<div class="st">${s[2]}</div>` : ''}</div></div>`
        )
        .join('');
  }

  /* ------------------------------------------------------------ slide 10: results */

  function matchedFor(R, ch) {
    const P = get(R, `pareto.${ch}`) || {};
    const rounds = (Array.isArray(P.rounds) ? P.rounds : []).filter(validPt);
    const last = rounds[rounds.length - 1];
    const md = P.matchedDefault;
    if (!last || !validPt(md)) return null;
    return { bpb: last.bpb, def: md.reads, tuned: last.reads };
  }

  function buildResults(R) {
    const host = document.getElementById('results-grid');
    if (!host) return;
    const card = (i, title, main, sm) => `<div class="rc card" style="--i:${i}"><h4>${title}</h4><div class="main">${main}</div>${sm ? `<div class="sm">${sm}</div>` : ''}</div>`;
    const reads = (ch) => {
      const r = matchedFor(R, ch);
      if (!r) return [ph(`reads per strand, default vs tuned, ${CH[ch]} at matched density`), ''];
      return [
        `${val(r.def)}<span class="arrow">to</span>${val(r.tuned)} reads per strand`,
        `default rules vs tuned codec, both at ${fmt.bpb(r.bpb)} bits per base, 300 held-out trials`,
      ];
    };
    const n = reads('nanopore');
    const il = reads('illumina');
    host.innerHTML =
      card(0, 'Nanopore, same bits per base', n[0], n[1]) +
      card(1, 'Illumina, same bits per base', il[0], il[1]) +
      card(2, 'Crossover', say.crossover(R), '');
    const demo = document.getElementById('results-demo');
    if (demo) demo.innerHTML = `<h4>An image through DNA at 6 reads per strand</h4>${say.imageDemo(R)}`;
  }

  /* ------------------------------------------------------------ spoken sentences for notes */

  function ruleSentence(cell, what) {
    const d = verdictOf(cell && cell.verdict);
    if (!d) return ph(what);
    const on = cell.readsOn;
    const off = cell.readsOff;
    const f = (v) => (isNum(v) ? val(v) : null);
    const v = cell.verdict.trim().toLowerCase();
    if (v === 'pays off') {
      return f(off) && f(on)
        ? `pays off: without it you need ${f(off)} instead of ${f(on)} reads per strand`
        : f(on)
        ? `pays off: without it the target isn't reached in our coverage grid`
        : 'pays off';
    }
    if (v === 'harmful') return f(off) && f(on) ? `hurts: without it you need only ${f(off)} instead of ${f(on)} reads per strand` : 'hurts';
    if (v === 'no measurable benefit') return f(off) && f(on) ? `buys nothing measurable: ${f(on)} reads per strand with it, ${f(off)} without` : 'buys nothing measurable';
    return d.say;
  }

  const say = {
    auditNanopore: (R) => ruleSentence(get(R, 'ruleAudit.0.nanopore'), 'verdict and effect of the run-length rule on Nanopore'),
    auditIllumina: (R) => ruleSentence(get(R, 'ruleAudit.0.illumina'), 'verdict and effect of the run-length rule on Illumina'),
    auditContrast: (R) => {
      if (contrastRow(R) >= 0) return 'Same rule, different channel, different answer';
      if (allVerdictsKnown(R)) return 'Same answer on both channels, and now we know';
      return ph('"same rule, different answer" or "same answer on both"');
    },
    headlineNanopore: (R) => {
      const r = matchedFor(R, 'nanopore');
      return r ? `${val(r.def)} reads per strand for the default rules, ${val(r.tuned)} for the tuned codec, at ${val(r.bpb, 'bpb')} bits per base` : ph('reads per strand, default vs tuned, Nanopore at matched density');
    },
    headlineIllumina: (R) => {
      const r = matchedFor(R, 'illumina');
      return r ? `${val(r.def)} reads per strand for the default rules, ${val(r.tuned)} for the tuned codec, at ${val(r.bpb, 'bpb')} bits per base` : ph('reads per strand, default vs tuned, Illumina at matched density');
    },
    crossover: (R) => {
      const X = (R && R.crossover) || {};
      if (isText(X.summary)) return esc(X.summary);
      const g = (a, b) => get(X, `${a}.${b}`);
      const all = ['default', 'nanopore', 'illumina'].every((a) => isNum(g(a, 'nanopore')) && isNum(g(a, 'illumina')));
      if (all) {
        const homeN = g('nanopore', 'nanopore') < g('default', 'nanopore');
        const homeI = g('illumina', 'illumina') < g('default', 'illumina');
        const awayN = g('nanopore', 'illumina') < g('default', 'illumina');
        const awayI = g('illumina', 'nanopore') < g('default', 'nanopore');
        if (homeN && homeI && !awayN && !awayI) return 'Each tuned codec wins on its own channel and loses its edge on the other';
      }
      return ph('one-line crossover outcome, e.g. each tuned codec wins at home and loses its edge away');
    },
    imageDemo: (R) => {
      const t = get(R, 'headline.imageDemo');
      return isText(t) ? esc(t) : ph('image round trip at 6 reads per strand, default vs tuned codec');
    },
    firewall: (R) => {
      const e = get(R, 'firewall.simB');
      const st = e && isText(e.status) ? e.status.trim().toLowerCase() : null;
      if (st === 'holds') return `Under Simulator B, the gain holds${isText(e.note) ? ': ' + esc(e.note) : ''}.`;
      if (st === 'does not hold') return `Under Simulator B, the gain does not hold${isText(e.note) ? ': ' + esc(e.note) : ''}, and we say so.`;
      return ph('does the gain survive Simulator B');
    },
  };

  /* ------------------------------------------------------------ generic result spans */

  function renderRes(R) {
    document.querySelectorAll('.res[data-key]').forEach((el) => {
      const v = get(R || {}, el.getAttribute('data-key'));
      const kind = el.getAttribute('data-fmt') || 'reads';
      const ok = kind === 'text' ? isText(v) : isNum(v);
      el.innerHTML = ok ? (kind === 'text' ? esc(v) : val(v, kind)) : ph(el.getAttribute('data-ph') || el.getAttribute('data-key'));
    });
    document.querySelectorAll('.res[data-say]').forEach((el) => {
      const fn = say[el.getAttribute('data-say')];
      el.innerHTML = fn ? fn(R || {}) : ph(el.getAttribute('data-say'));
    });
  }

  /* ------------------------------------------------------------ public */

  window.SlideHooks = {
    markSVG: MARK,
    seqHTML,
    init(R) {
      document.querySelectorAll('[data-mark]').forEach((el) => {
        el.outerHTML = MARK;
      });
      expandSeqs(document);
      buildRibbon();
      buildCandidates();
      buildAudit(R);
      buildTier2();
      buildArchitecture();
      buildLearned();
      buildTwoModels();
      buildTier2Curve(R);
      buildFieldChart();
      buildCrossover(R);
      buildCalibration();
      buildFirewall(R);
      buildVerdicts(R);
      buildResults(R);
      renderRes(R);
    },
    layout(R) {
      placeAuditHighlight(R);
    },
  };
})();
