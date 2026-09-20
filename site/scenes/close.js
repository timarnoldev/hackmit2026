/* Beat 10 — close. The ask.
 *
 * The mark draws itself: the two strands of the helix sweep out from the left
 * and right crossings, then the four base pairs drop in between them, then
 * their letters. Geometry and colours are quoted verbatim from
 * marketing/logo/erbgut-mark.svg, which is generated from the source artwork;
 * see marketing/BRAND.md section 9 ("Logo"). The mark does not invert, so the
 * same values serve on a light page and a dark one.
 *
 * Then the wordmark, then the links. The team slot is a placeholder, so it is
 * Tbd coloured and dashed, exactly as the brand requires, until a human fills it
 * in below.
 *
 * Contract: site/SCROLLY.md section 1. update(p) is pure. The only motion that
 * is not driven by p is one slow ambient breath behind the mark, and it does not
 * run when ctx.reduced is true.
 */

const NS = 'http://www.w3.org/2000/svg';

const INK = '#08130B';
const MUTED = '#3E6B52';
const AUDIT = '#0B6E82';
const TBD_INK = '#6E6200';
const TBD_LINE = '#A9B81C';

/* the mark (BRAND.md section 9, "Logo"), quoted from erbgut-logo.svg. This is
 * the whole helix, not the square crop: the close scene has the width for it,
 * and the binary and the wordmark are the only parts left out. */
const HELIX = '#32C7DB';
const BASE_LETTER = '#101418';

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

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

const REPO = 'https://github.com/timarnoldev/hackmit2026';
const DECK = 'deck/';
const TEAM_SLOT = '';

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

function layout(mode) {
  if (mode === 'narrow') {
    return {
      vw: 720, vh: 900, narrow: true,
      /* markSize is the mark's drawn width; its height follows MARK_H / MARK_W */
      markSize: 470, markX: 360, markY: 214,
      wordSize: 72, wordY: 456,
      linkSize: 19, linkY: 566,
      teamSize: 15, teamY: 636,
    };
  }
  return {
    vw: 1200, vh: 700, narrow: false,
    markSize: 512, markX: 600, markY: 118,
    wordSize: 80, wordY: 386,
    linkSize: 21, linkY: 480,
    teamSize: 16, teamY: 552,
  };
}

function build() {
  const L = S.L, svg = S.svg;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  svg.setAttribute('viewBox', '0 0 ' + L.vw + ' ' + L.vh);

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
  const k = L.markSize / MARK_W;
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
  const bases = BASES.map(([x, y, w, h, fill, top, bot]) => {
    const cx = x + w / 2;
    const g = e('g', {
      opacity: '0',
      transform: 'translate(' + cx + ',' + MARK_AXIS + ') scale(1,0) translate(' + (-cx) + ',' + (-MARK_AXIS) + ')',
    }, crop);
    e('rect', { x: x, y: y, width: w, height: h, rx: w / 2, fill: fill }, g);
    for (const [ch, base] of [[top, LETTER_TOP], [bot, LETTER_BOT]]) {
      if (!ch) continue;
      const t = e('text', {
        x: cx, y: base, 'text-anchor': 'middle', 'font-family': SANS,
        'font-size': LETTER_SIZE, 'font-weight': '700', fill: BASE_LETTER,
      }, g);
      t.textContent = ch;
    }
    return { g: g, cx: cx };
  });

  /* wordmark */
  const word = e('text', {
    x: L.markX, y: L.wordY, 'text-anchor': 'middle', 'font-family': SANS,
    'font-size': L.wordSize, 'font-weight': '600', 'letter-spacing': '-0.03em',
    fill: INK, opacity: '0',
  }, svg);
  word.textContent = 'Erbgut';

  /* links, one centred line so the browser does the measuring */
  const links = e('text', {
    x: L.markX, y: L.linkY, 'text-anchor': 'middle', 'font-family': SANS,
    'font-size': L.linkSize, fill: MUTED, opacity: '0',
  }, svg);
  const anchors = [];
  const addLink = (href, label) => {
    const a = e('a', { href: href, target: '_blank', rel: 'noopener' }, links);
    const t = e('tspan', {
      fill: AUDIT, 'text-decoration': 'underline',
      'font-weight': '500',
    }, a);
    t.textContent = label;
    anchors.push({ a, t });
  };
  addLink(REPO, 'Read the code');
  const sepA = e('tspan', { fill: MUTED }, links);
  sepA.textContent = '\u00a0\u00a0·\u00a0\u00a0';
  addLink(DECK, 'Open the pitch deck');

  /* the team slot is a placeholder: Tbd coloured and dashed, never styled
   * like a real value. Replace the string in TEAM_SLOT when the names exist. */
  const teamG = e('g', { opacity: '0' }, svg);
  const team = e('text', {
    x: L.markX, y: L.teamY, 'text-anchor': 'middle', 'font-family': MONO,
    'font-size': L.teamSize, fill: TBD_INK,
  }, teamG);
  team.textContent = TEAM_SLOT;
  const teamW = TEAM_SLOT.length * L.teamSize * 0.60;
  e('line', {
    x1: L.markX - teamW / 2, y1: L.teamY + 9, x2: L.markX + teamW / 2, y2: L.teamY + 9,
    stroke: TBD_LINE, 'stroke-width': 1.6, 'stroke-dasharray': '5 5',
  }, teamG);

  S.parts = { glow, mark, strandA, strandC, bases, word, links, anchors, teamG };

  /* hover feedback without touching global styles */
  for (const { a, t } of anchors) {
    a.addEventListener('mouseenter', () => t.setAttribute('fill', INK));
    a.addEventListener('mouseleave', () => t.setAttribute('fill', AUDIT));
  }
}

function render(p) {
  const P = S.parts;
  if (!P) return;

  P.strandA.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.02, 0.34)));
  P.strandC.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.06, 0.38)));

  /* the base pairs drop in left to right once the strands are most of the way */
  P.bases.forEach((b, i) => {
    const t = smooth(seg(p, 0.24 + i * 0.030, 0.42 + i * 0.030));
    b.g.setAttribute('opacity', t.toFixed(3));
    b.g.setAttribute('transform',
      'translate(' + b.cx + ',' + MARK_AXIS + ') scale(1,' + t.toFixed(3) + ') translate('
      + (-b.cx) + ',' + (-MARK_AXIS) + ')');
  });

  const wordIn = smooth(seg(p, 0.40, 0.60));
  P.word.setAttribute('opacity', wordIn);
  P.word.setAttribute('transform', 'translate(0,' + ((1 - wordIn) * 14).toFixed(2) + ')');

  const linkIn = smooth(seg(p, 0.60, 0.78));
  P.links.setAttribute('opacity', linkIn);
  P.links.setAttribute('transform', 'translate(0,' + ((1 - linkIn) * 12).toFixed(2) + ')');

  P.teamG.setAttribute('opacity', smooth(seg(p, 0.78, 0.94)) * 0.95);

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
      xmlns: NS, viewBox: '0 0 1200 700', preserveAspectRatio: 'xMidYMid meet',
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
    const mode = w / Math.max(h, 1) < 1.15 ? 'narrow' : 'wide';
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
