/* Beat 10 — close. The ask, and the last frame of the page.
 *
 * The lockup draws itself: the two helix strands sweep out, the base pairs drop
 * in between them, their letters arrive, and then the wordmark wipes in. Every
 * path and colour here is quoted verbatim from marketing/logo/erbgut-logo.svg,
 * which is generated from the source artwork; see marketing/BRAND.md section 9.
 *
 * The wordmark is the artwork's own, and the artwork sets it lowercase. That is
 * deliberate and it is not a bug: the logotype is lowercase "erbgut", running
 * text is capitalised "Erbgut", which is the ordinary convention for a German
 * noun used as a logotype. The scene must not set a second wordmark of its own.
 *
 * Nothing in this scene fades out. It is the last thing a visitor sees, so it
 * assembles, lands by about p = 0.76, and then holds.
 *
 * Contract: site/SCROLLY.md section 1. update(p) is pure. The only motion that
 * is not driven by p is one slow ambient breath behind the mark, and it does not
 * run when ctx.reduced is true.
 */

const NS = 'http://www.w3.org/2000/svg';

const MUTED = '#3E6B52';
const INK = '#08130B';
const AUDIT = '#0B6E82';

/* the mark, quoted from erbgut-logo.svg. The binary written through the helix
 * is the one part left out: at this size it reads as noise. */
const HELIX = '#32C7DB';
const BASE_LETTER = '#101418';
const WORD_FILL = '#4F4F4F';          /* the artwork's own wordmark colour */

const MARK_W = 434;          /* the lockup's full width */
const MARK_H = 154;          /* outer edge of the ribbon, top to bottom */
const MARK_TOP = 76.25;      /* where that outer edge sits in lockup coords */
const MARK_AXIS = 153.25;    /* the helix centre line */
const MARK_SW = 25;

const STRAND_1 = 'M0 206.05C31.05 206.05 77.05 194.96 115 153.25C148.66 102.29 189.46 88.75 217 88.75C244.54 88.75 285.34 102.29 319 153.25C356.95 194.96 402.95 206.05 434 206.05';
const STRAND_2 = 'M0 100.45C31.05 100.45 77.05 111.54 115 153.25C148.66 204.21 189.46 217.75 217 217.75C244.54 217.75 285.34 204.21 319 153.25C356.95 111.54 402.95 100.45 434 100.45';

/* x, y, width, height, fill, upper letter, lower letter. The two pairs under
 * each crossing carry no letters, exactly as the artwork has them. */
const BASES = [
  [17, 101, 22.5, 89, '#FC68D6', '', ''],
  [53.5, 105.5, 22.5, 72, '#FEC746', '', ''],
  [159, 119, 22.5, 86, '#FEC746', 'A', 'T'],
  [195, 103.5, 23, 114.5, '#5BD67C', 'G', 'C'],
  [231.5, 104, 23, 113.5, '#FC68D6', 'C', 'G'],
  [267.5, 120.5, 23, 85, '#5BD67C', 'T', 'A'],
  [365.5, 105.5, 23, 76, '#FC68D6', '', ''],
  [400, 101, 22.5, 90, '#FEC746', '', ''],
];
const LETTER_SIZE = 28.5;   /* cap height 20.5, as in the file */
const LETTER_TOP = 148.75;
const LETTER_BOT = 185.75;

/* The wordmark, outlined, straight out of erbgut-logo.svg: [path, x advance].
 * Its glyph space is 2909 units wide and sits on its baseline at y = 0, with
 * 720 above it and 215 below. The lockup draws it at scale 0.058, so it is
 * always 0.058 * 2909 / 434, about 38.9 percent, of the mark's width. */
const WORDMARK = [
  ['M296.3 -10Q215.3 -10 154.7 23.8Q94 57.7 60.3 117.7Q26.7 177.7 26.7 255.7Q26.7 334.3 60.2 393.8Q93.7 453.3 154 486.7Q214.3 520 293.7 520Q370 520 426.2 488.3Q482.3 456.7 513.2 400Q544 343.3 544 266.7Q544 252.7 543.2 240.7Q542.3 228.7 540.3 217H105.3V305.3H443.3L417.3 281Q417.3 353 384.2 389.2Q351 425.3 292.3 425.3Q227.7 425.3 190.5 381.2Q153.3 337 153.3 254Q153.3 171.7 190.5 128.2Q227.7 84.7 297 84.7Q337.3 84.7 367 99.8Q396.7 115 411 146.3H530.3Q505.3 73.7 445.5 31.8Q385.7 -10 296.3 -10Z',
   -1481.17],
  ['M66.7 0V510H189.7V386.7H193.3V0ZM193.3 266 180.3 387Q198.3 452 241 486Q283.7 520 344.3 520Q365.3 520 374.3 516V397Q369.3 399 360.3 399.5Q351.3 400 338.3 400Q265 400 229.2 367.8Q193.3 335.7 193.3 266Z',
   -960.17],
  ['M358 -9.7Q285.3 -9.7 238.7 24.7Q192 59 180.7 119.7L189.7 121V0H66.7V720H193.3V395L183.7 397.3Q197.3 454.3 245.8 487.2Q294.3 520 365.7 520Q434.3 520 484.8 487.3Q535.3 454.7 563 395.7Q590.7 336.7 590.7 257Q590.7 176.3 561.7 116.3Q532.7 56.3 480.7 23.3Q428.7 -9.7 358 -9.7ZM326 93.7Q386.7 93.7 423 136.5Q459.3 179.3 459.3 256.3Q459.3 333 422.7 374.8Q386 416.7 324.7 416.7Q264.7 416.7 227.7 374.2Q190.7 331.7 190.7 254.7Q190.7 178.3 227.7 136Q264.7 93.7 326 93.7Z',
   -620.17],
  ['M291 -215Q190 -215 125.5 -170.8Q61 -126.7 48.7 -52H168.7Q177.7 -78 208.2 -92.5Q238.7 -107 288 -107Q359.3 -107 393.3 -75.5Q427.3 -44 427.3 20V130.3L436 128.3Q423.7 71.7 374.7 38.5Q325.7 5.3 255 5.3Q187.3 5.3 136.7 37.2Q86 69 58 126.3Q30 183.7 30 260.7Q30 338.7 59 397Q88 455.3 140.5 487.5Q193 519.7 263 519.7Q335 519.7 381.5 485.3Q428 451 438.7 390L431 389V510H554V21.3Q554 -88.3 483.7 -151.7Q413.3 -215 291 -215ZM296.3 105Q356.7 105 393.3 146.7Q430 188.3 430 263.3Q430 337.7 393 378.8Q356 420 295 420Q235 420 198.2 378.3Q161.3 336.7 161.3 261.7Q161.3 187.3 198.3 146.2Q235.3 105 296.3 105Z',
   -49.17],
  ['M232 -10Q179.3 -10 140.7 12.5Q102 35 81.3 73.8Q60.7 112.7 60.7 161.7V510H187.3V188Q187.3 140.7 210.8 117Q234.3 93.3 277.3 93.3Q316 93.3 345.7 111.3Q375.3 129.3 392.8 161.5Q410.3 193.7 410.3 235.3L423.3 113Q398.3 57.3 348.7 23.7Q299 -10 232 -10ZM414 0V120H410.3V510H537V0Z',
   521.83],
  ['M296 -10.3Q202.3 -10.3 157.8 34.2Q113.3 78.7 113.3 167.7V625.7L240 673V164.7Q240 127.7 260 109.7Q280 91.7 323.3 91.7Q340 91.7 353.5 94.5Q367 97.3 378.7 101.3V2.7Q366.7 -3.3 344.8 -6.8Q323 -10.3 296 -10.3ZM15 411.3V510H378.7V411.3Z',
   1075.83],
];
const WORD_W = 2909;         /* glyph-space width, measured from the file */
const WORD_X0 = -1454.47;    /* its left edge in glyph space */
const WORD_ASC = 720;        /* above the baseline */
const WORD_DESC = 215;       /* below it */
const WORD_SCALE = 0.058;    /* the lockup's own scale for this group */

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";

const REPO = 'https://github.com/timarnoldev/hackmit2026';
const DECK = 'deck/';

function e(tag, attrs, parent) {
  const n = document.createElementNS(NS, tag);
  if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const seg = (p, a, b) => clamp((p - a) / (b - a), 0, 1);
const smooth = (t) => t * t * (3 - 2 * t);

const S = {
  root: null, svg: null, ctx: null, reduced: false, mode: '', p: 0, L: null, parts: null, raf: 0,
};

/* The shell hands the same ctx object to every scene and mutates ctx.reduced
 * when the setting changes, so read it per frame rather than latching it. */
function red() {
  S.reduced = !!(S.ctx && S.ctx.reduced);
  return S.reduced;
}

/* The close scene gets the whole stage: 1324 by 900 on a desktop, 354 by 844 on
 * a phone. The viewBox follows that aspect so the lockup fills the frame. */
function layout(mode) {
  if (mode === 'narrow') {
    return {
      vw: 720, vh: 1460, narrow: true,
      markSize: 580, markX: 360, markY: 470,
      wordY: 840,
      linkSize: 30, linkY: 962,
    };
  }
  return {
    vw: 1200, vh: 820, narrow: false,
    markSize: 512, markX: 600, markY: 200,
    wordY: 512,
    linkSize: 22, linkY: 606,
  };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

  const k = L.markSize / MARK_W;
  const ws = WORD_SCALE * k;                       /* wordmark glyph to scene */

  const defs = e('defs', null, svg);
  const halo = e('radialGradient', { id: 'eg-close-halo', cx: '0.5', cy: '0.5', r: '0.5' }, defs);
  e('stop', { offset: '0', 'stop-color': AUDIT, 'stop-opacity': '0.16' }, halo);
  e('stop', { offset: '0.55', 'stop-color': AUDIT, 'stop-opacity': '0.05' }, halo);
  e('stop', { offset: '1', 'stop-color': AUDIT, 'stop-opacity': '0' }, halo);

  const glow = e('ellipse', {
    cx: L.markX, cy: L.markY + (L.markSize * MARK_H) / (2 * MARK_W),
    rx: L.markSize * 1.3, ry: L.markSize * 1.3,
    fill: 'url(#eg-close-halo)', opacity: '0',
  }, svg);

  /* the mark, drawn in the lockup's coordinates and scaled to markSize */
  const mark = e('g', {
    transform: 'translate(' + (L.markX - L.markSize / 2) + ',' + L.markY + ') scale(' + k + ')',
  }, svg);
  const crop = e('g', { transform: 'translate(0,' + (-MARK_TOP) + ')' }, mark);
  const strandA = e('path', {
    d: STRAND_1, fill: 'none', stroke: HELIX, 'stroke-width': MARK_SW,
    pathLength: '1', 'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, crop);
  const strandC = e('path', {
    d: STRAND_2, fill: 'none', stroke: HELIX, 'stroke-width': MARK_SW,
    pathLength: '1', 'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, crop);

  /* each base pair is one group, so the capsule and its two letters arrive
   * together and can be scaled about the helix centre line */
  const bases = BASES.map(function (b) {
    const x = b[0], y = b[1], w = b[2], h = b[3], fill = b[4];
    const cx = x + w / 2;
    const g = e('g', {
      opacity: '0',
      transform: 'translate(' + cx + ',' + MARK_AXIS + ') scale(1,0) translate(' + (-cx) + ',' + (-MARK_AXIS) + ')',
    }, crop);
    e('rect', { x: x, y: y, width: w, height: h, rx: w / 2, fill: fill }, g);
    const pairs = [[b[5], LETTER_TOP], [b[6], LETTER_BOT]];
    for (const pr of pairs) {
      if (!pr[0]) continue;
      const t = e('text', {
        x: cx, y: pr[1], 'text-anchor': 'middle', 'font-family': SANS,
        'font-size': LETTER_SIZE, 'font-weight': '700', fill: BASE_LETTER,
      }, g);
      t.textContent = pr[0];
    }
    return { g: g, cx: cx };
  });

  /* the wordmark. Its six letters rise in one after another, the same beat as
   * the base pairs dropping into the helix above them. */
  const word = e('g', {
    transform: 'translate(' + L.markX + ',' + L.wordY + ') scale(' + ws + ',' + (-ws) + ')',
    fill: WORD_FILL,
  }, svg);
  const glyphs = WORDMARK.map(function (gl) {
    const g = e('g', { opacity: '0' }, word);
    e('path', { transform: 'translate(' + gl[1] + ' 0)', d: gl[0] }, g);
    return g;
  });
  /* a screen-space rise, expressed in glyph units: +y in glyph space is up */
  const rise = 14 / ws;

  /* links, one centred line so the browser does the measuring */
  const links = e('text', {
    x: L.markX, y: L.linkY, 'text-anchor': 'middle', 'font-family': SANS,
    'font-size': L.linkSize, fill: MUTED, opacity: '0',
  }, svg);
  const anchors = [];
  const addLink = function (href, label) {
    const a = e('a', { href: href, target: '_blank', rel: 'noopener' }, links);
    const t = e('tspan', { fill: AUDIT, 'text-decoration': 'underline', 'font-weight': '500' }, a);
    t.textContent = label;
    anchors.push({ a: a, t: t });
  };
  addLink(REPO, 'Read the code');
  const sepA = e('tspan', { fill: MUTED }, links);
  sepA.textContent = '  ·  ';
  addLink(DECK, 'Open the pitch deck');

  S.parts = { glow: glow, mark: mark, strandA: strandA, strandC: strandC, bases: bases,
    glyphs: glyphs, rise: rise, links: links, anchors: anchors };

  /* hover feedback without touching global styles */
  for (const an of anchors) {
    an.a.addEventListener('mouseenter', function () { an.t.setAttribute('fill', INK); });
    an.a.addEventListener('mouseleave', function () { an.t.setAttribute('fill', AUDIT); });
  }
}

function render(p) {
  const P = S.parts;
  if (!P) return;

  P.strandA.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.00, 0.24)));
  P.strandC.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.04, 0.28)));

  /* the base pairs drop in left to right once the strands are most of the way */
  P.bases.forEach(function (b, i) {
    const t = smooth(seg(p, 0.18 + i * 0.026, 0.34 + i * 0.026));
    b.g.setAttribute('opacity', t.toFixed(3));
    b.g.setAttribute('transform',
      'translate(' + b.cx + ',' + MARK_AXIS + ') scale(1,' + t.toFixed(3) + ') translate('
      + (-b.cx) + ',' + (-MARK_AXIS) + ')');
  });

  /* the wordmark rises in, letter by letter, and stays. Nothing fades back out. */
  P.glyphs.forEach(function (g, i) {
    const t = smooth(seg(p, 0.42 + i * 0.026, 0.60 + i * 0.026));
    g.setAttribute('opacity', t.toFixed(3));
    g.setAttribute('transform', 'translate(0,' + (-(1 - t) * P.rise).toFixed(1) + ')');
  });

  const linkIn = smooth(seg(p, 0.60, 0.76));
  P.links.setAttribute('opacity', linkIn.toFixed(3));
  P.links.setAttribute('transform', 'translate(0,' + ((1 - linkIn) * 12).toFixed(2) + ')');

  S.glowBase = smooth(seg(p, 0.05, 0.45));
  if (S.reduced) P.glow.setAttribute('opacity', S.glowBase);
}

/* ambient only: one slow breath behind the mark, never part of the story */
function ambient(now) {
  S.raf = requestAnimationFrame(ambient);
  if (!S.parts) return;
  const b = S.glowBase || 0;
  if (S.ctx && S.ctx.reduced) { S.parts.glow.setAttribute('opacity', b); return; }
  S.parts.glow.setAttribute('opacity', (b * (0.72 + 0.28 * (0.5 + 0.5 * Math.sin(now / 1700)))).toFixed(3));
}

export default {
  id: 'close',

  mount(root, ctx) {
    S.root = root;
    S.ctx = ctx || {};
    red();
    S.svg = e('svg', {
      xmlns: NS, viewBox: '0 0 1200 820', preserveAspectRatio: 'xMidYMid meet',
      role: 'img', 'aria-label': 'Erbgut. Read the code, open the pitch deck.',
    });
    S.svg.style.width = '100%';
    S.svg.style.height = '100%';
    S.svg.style.display = 'block';
    root.appendChild(S.svg);
    S.mode = 'wide';
    S.L = layout(S.mode);
    build();
    render(S.reduced ? 1 : 0);
    if (!S.reduced) S.raf = requestAnimationFrame(ambient);
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
    if (S.raf) cancelAnimationFrame(S.raf);
    S.raf = 0;
    if (S.svg && S.svg.parentNode) S.svg.parentNode.removeChild(S.svg);
    S.svg = null; S.parts = null;
  },
};
