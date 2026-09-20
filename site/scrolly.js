/* Erbgut scrollytelling shell.
 *
 * The shell owns scrolling. A scene owns pixels and knows nothing about scrolling.
 *
 * For every <section data-scene="id"> the shell:
 *   - pins its [data-stage] (CSS sticky, the section carries the scroll length),
 *   - computes p in [0,1] for how far the reader is through that scene,
 *   - calls scene.update(p) inside ONE shared requestAnimationFrame,
 *   - fades the section's [data-at] copy blocks in and out around their position,
 *   - dynamically imports site/scenes/<id>.js and falls back to scenes/_stub.js
 *     when the module is missing or throws, so the page never breaks.
 *
 * A scene module is:
 *   export default { id, mount(root, ctx), update(p), resize(w, h), unmount() }
 * and may additionally `export function create()` returning a fresh instance
 * (the shell prefers create() when it exists, which is how one stub can serve
 * several sections at once).
 *
 * ctx is { reduced, id, width, height }. `reduced` is prefers-reduced-motion and
 * is kept live: the shell mutates it on the same object if the setting changes.
 *
 * Rules the shell holds itself to:
 *   - no layout reads inside the rAF; everything is measured on resize and cached,
 *   - it only ever writes opacity, a --sy custom property and transforms,
 *   - a scene that throws is disabled, never allowed to take the page with it.
 */

const root = document.documentElement;
root.dataset.scrollyReady = '1';

const FADE = 0.09;                       // length of a copy cross-fade, in p
const LIFT = 18;                         // px a copy block travels while fading

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
const smooth = (t) => t * t * (3 - 2 * t);

const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
let reduced = mq.matches;

const bar = document.querySelector('[data-progress]');
const dots = Array.from(document.querySelectorAll('.dots a'));

let stubModule = null;
function loadStub() {
  if (!stubModule) stubModule = import('./scenes/_stub.js').catch(() => null);
  return stubModule;
}

/* ── register the scenes ─────────────────────────────────────────────── */

const scenes = Array.from(document.querySelectorAll('main [data-scene]')).map((section, i) => ({
  i,
  id: section.dataset.scene,
  section,
  stage: section.querySelector('[data-stage]') || section,
  art: section.querySelector('[data-art]'),
  blocks: collectBlocks(section),
  ctx: null,
  inst: null,
  loading: false,
  broken: false,
  visible: false,
  p: -1,          // last p handed to the scene
  top: 0,
  height: 1,
  travel: 1,
  w: 0,
  h: 0,
  lastW: -1,
  lastH: -1,
}));

/* Copy blocks. A block fades in around its data-at. It fades out at data-until
 * if it has one, otherwise at the next block inside the same [data-copy] group
 * (that is the cross-fade for two blocks stacked in the same place), and blocks
 * marked data-hold never fade out at all (the receipts, which accumulate). */
function collectBlocks(section) {
  const all = Array.from(section.querySelectorAll('[data-at]')).map((el) => ({
    el,
    at: parseFloat(el.dataset.at) || 0,
    until: el.dataset.until != null ? parseFloat(el.dataset.until) : null,
    hold: el.hasAttribute('data-hold'),
    group: el.closest('[data-copy]'),
    o: -1,
    y: 0,
  }));

  const groups = new Map();
  for (const b of all) {
    if (!groups.has(b.group)) groups.set(b.group, []);
    groups.get(b.group).push(b);
  }
  for (const [group, list] of groups) {
    if (!group) continue;                       // ungrouped: needs an explicit until
    list.sort((a, b) => a.at - b.at);
    for (let i = 0; i < list.length - 1; i++) {
      const b = list[i];
      if (b.hold || b.until != null) continue;
      b.until = list[i + 1].at;
    }
  }
  return all;
}

/* ── measuring: the only place that reads layout ─────────────────────── */

let viewH = window.innerHeight;
let docTravel = 1;
let scrollTop = window.scrollY;

function measure() {
  // read
  viewH = window.innerHeight;
  scrollTop = window.scrollY;
  docTravel = Math.max(1, root.scrollHeight - viewH);
  const read = scenes.map((s) => {
    const box = s.section.getBoundingClientRect();
    const height = s.section.offsetHeight;
    const stageH = s.stage.offsetHeight;
    return {
      top: box.top + scrollTop,
      height,
      travel: Math.max(1, height - stageH),
      w: s.art ? s.art.clientWidth : s.stage.clientWidth,
      h: s.art ? s.art.clientHeight : s.stage.clientHeight,
    };
  });

  // write
  scenes.forEach((s, i) => {
    Object.assign(s, read[i]);
    s.p = -1;                                  // force the next frame to redraw
    if (s.ctx) { s.ctx.width = s.w; s.ctx.height = s.h; }
    if (s.inst && (s.w !== s.lastW || s.h !== s.lastH)) {
      s.lastW = s.w;
      s.lastH = s.h;
      call(s, 'resize', s.w, s.h);
    }
  });
  schedule();
}

/* ── the one shared rAF ──────────────────────────────────────────────── */

let frame = 0;
function schedule() {
  if (!frame) frame = requestAnimationFrame(render);
}

function render() {
  frame = 0;
  const y = scrollTop;

  if (bar) bar.style.transform = 'scaleX(' + clamp01(y / docTravel).toFixed(4) + ')';

  let active = 0;
  for (const s of scenes) {
    if (y + viewH * 0.35 >= s.top) active = s.i;

    // visibility straight from the cached geometry: deterministic, no layout read,
    // and never a frame behind the way an IntersectionObserver can be on a jump.
    const bottom = s.top + s.height;
    const near = s.top < y + viewH * 2.6 && bottom > y - viewH * 1.6;
    const on = s.top < y + viewH * 1.08 && bottom > y - viewH * 0.08;

    if (near) ensureScene(s);

    if (on !== s.visible) {
      s.visible = on;
      for (const b of s.blocks) b.el.style.willChange = on ? 'opacity, transform' : '';
      if (on) {
        s.p = -1;
      } else {
        const end = y > s.top ? 1 : 0;      // settle at whichever end we left on
        for (const b of s.blocks) paint(b, end);
        drive(s, end);
      }
    }
    if (!on) continue;

    const p = clamp01((y - s.top) / s.travel);
    for (const b of s.blocks) paint(b, p);
    drive(s, p);
  }
  setActiveDot(active);
}

function paint(b, p) {
  const enter = clamp01((p - (b.at - FADE)) / FADE);
  const exit = b.until == null ? 0 : clamp01((p - (b.until - FADE)) / FADE);
  const o = smooth(enter) * (1 - smooth(exit));
  const y = reduced ? 0 : Math.round(((1 - enter) - exit) * LIFT * 10) / 10;
  if (Math.abs(o - b.o) > 0.003) {
    b.o = o;
    b.el.style.opacity = o.toFixed(3);
  }
  if (y !== b.y) {
    b.y = y;
    b.el.style.setProperty('--sy', y + 'px');
  }
}

function drive(s, p) {
  if (!s.inst || s.broken) return;
  const q = reduced ? 1 : p;                   // reduced motion: the end state, always
  if (Math.abs(q - s.p) < 0.0004) return;
  s.p = q;
  call(s, 'update', q);
}

function call(s, method, a, b) {
  const fn = s.inst && s.inst[method];
  if (typeof fn !== 'function') return;
  try {
    fn.call(s.inst, a, b);
  } catch (err) {
    s.broken = true;
    s.section.dataset.sceneFailed = method;
    console.error('[scrolly] scene "' + s.id + '" failed in ' + method + '()', err);
  }
}

/* ── loading scenes ──────────────────────────────────────────────────── */

async function ensureScene(s) {
  if (s.inst || s.loading || !s.art) return;
  s.loading = true;

  let mod = null;
  try {
    mod = await import('./scenes/' + s.id + '.js');
  } catch (err) {
    mod = null;                                // missing or broken: the stub takes over
  }
  let inst = instanceOf(mod);
  if (!inst) inst = instanceOf(await loadStub());
  if (!inst) { s.loading = false; return; }

  s.ctx = { reduced, id: s.id, width: s.w, height: s.h };
  s.inst = inst;
  call(s, 'mount', s.art, s.ctx);

  if (s.broken) {                              // mount threw: fall back to the stub
    s.art.textContent = '';
    s.broken = false;
    s.inst = instanceOf(await loadStub());
    if (s.inst) call(s, 'mount', s.art, s.ctx);
  }

  s.lastW = s.w;
  s.lastH = s.h;
  call(s, 'resize', s.w, s.h);
  s.p = -1;
  schedule();
}

function instanceOf(mod) {
  if (!mod) return null;
  if (typeof mod.create === 'function') {
    try { return mod.create(); } catch (err) { return null; }
  }
  const d = mod.default;
  return d && typeof d.update === 'function' ? d : null;
}

/* ── dots ────────────────────────────────────────────────────────────── */

let lastDot = -1;
function setActiveDot(i) {
  if (i === lastDot || !dots.length) return;
  if (dots[lastDot]) dots[lastDot].classList.remove('is-on');
  if (dots[i]) dots[i].classList.add('is-on');
  lastDot = i;
}

/* ── events ──────────────────────────────────────────────────────────── */

window.addEventListener('scroll', () => {
  scrollTop = window.scrollY;                  // cheap, and outside the rAF
  schedule();
}, { passive: true });

let resizeTimer = 0;
function remeasure() {
  // debounced on a timer, never chained through a second rAF: measuring reads
  // layout, so it must stay outside the render frame, and the render frame must
  // never be one generation behind the measurement.
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(measure, 50);
}

window.addEventListener('resize', remeasure, { passive: true });
window.addEventListener('orientationchange', remeasure, { passive: true });
window.addEventListener('load', remeasure);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(remeasure);
if ('ResizeObserver' in window) new ResizeObserver(remeasure).observe(document.body);

const onMotionChange = () => {
  reduced = mq.matches;
  for (const s of scenes) { if (s.ctx) s.ctx.reduced = reduced; s.p = -1; }
  remeasure();
};
if (mq.addEventListener) mq.addEventListener('change', onMotionChange);
else if (mq.addListener) mq.addListener(onMotionChange);

measure();
