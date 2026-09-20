/* Beat 10 — close. The ask.
 *
 * The mark draws itself: two strands crossing, the Audit strand over the Cost
 * strand, held by two Gain rungs. Geometry and colours are quoted verbatim from
 * marketing/BRAND.md section 9 ("Logo"), including the inversion rule: on a
 * light page the tile is ink and the strands take the bright deck values.
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

/* the mark, on a light page (BRAND.md section 9, "Logo") */
const TILE = '#08130B';
const STRAND_A = '#39E6FF';
const STRAND_C = '#FF2E9C';
const RUNG_G = '#39FF6E';

const PATH_A = 'M22 10 C 46 10 46 26 32 30 C 18 34 18 50 42 54';
const PATH_C = 'M42 10 C 18 10 18 26 32 30 C 46 34 46 50 22 54';

const SANS = "'Instrument Sans','Erbgut Sans',system-ui,-apple-system,'Segoe UI',sans-serif";
const MONO = "'JetBrains Mono','Erbgut Mono',ui-monospace,SFMono-Regular,Menlo,monospace";

const REPO = 'https://github.com/timarnoldev/hackmit2026';
const DECK = 'deck/';
const TEAM_SLOT = '[TEAM: names and contact]';

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
      markSize: 176, markX: 360, markY: 200,
      wordSize: 72, wordY: 464,
      linkSize: 19, linkY: 566,
      teamSize: 15, teamY: 636,
    };
  }
  return {
    vw: 1200, vh: 700, narrow: false,
    markSize: 196, markX: 600, markY: 112,
    wordSize: 80, wordY: 394,
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
    cx: L.markX, cy: L.markY + L.markSize / 2,
    rx: L.markSize * 1.5, ry: L.markSize * 1.5,
    fill: 'url(#eg-close-halo)', opacity: '0',
  }, svg);

  /* the mark, drawn in its own 64 unit square and scaled */
  const k = L.markSize / 64;
  const mark = e('g', {
    transform: 'translate(' + (L.markX - L.markSize / 2) + ',' + L.markY + ') scale(' + k + ')',
  }, svg);
  const tile = e('rect', { x: 0, y: 0, width: 64, height: 64, rx: 14, fill: TILE, opacity: '0' }, mark);
  const strandC = e('path', {
    d: PATH_C, fill: 'none', stroke: STRAND_C, 'stroke-width': 3.4,
    'stroke-linecap': 'round', pathLength: '1',
    'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, mark);
  const strandA = e('path', {
    d: PATH_A, fill: 'none', stroke: STRAND_A, 'stroke-width': 3.4,
    'stroke-linecap': 'round', pathLength: '1',
    'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, mark);
  const rungs = [16, 48].map((y) => e('line', {
    x1: 24, y1: y, x2: 40, y2: y, stroke: RUNG_G, 'stroke-width': 3,
    'stroke-linecap': 'round', pathLength: '1',
    'stroke-dasharray': '1 2', 'stroke-dashoffset': '1',
  }, mark));

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

  S.parts = { glow, mark, tile, strandA, strandC, rungs, word, links, anchors, teamG };

  /* hover feedback without touching global styles */
  for (const { a, t } of anchors) {
    a.addEventListener('mouseenter', () => t.setAttribute('fill', INK));
    a.addEventListener('mouseleave', () => t.setAttribute('fill', AUDIT));
  }
}

function render(p) {
  const P = S.parts;
  if (!P) return;

  P.tile.setAttribute('opacity', smooth(seg(p, 0.0, 0.14)));
  P.strandA.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.06, 0.36)));
  P.strandC.setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.10, 0.40)));
  P.rungs[0].setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.30, 0.44)));
  P.rungs[1].setAttribute('stroke-dashoffset', 1 - smooth(seg(p, 0.34, 0.48)));

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
