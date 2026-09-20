/* The fallback scene.
 *
 * The shell loads site/scenes/<id>.js for every <section data-scene="id">. When
 * that module is missing, fails to parse, or throws while mounting, this one is
 * used instead, so a half-finished page still scrolls and still looks composed.
 *
 * It is also the smallest complete example of the scene contract:
 *   mount(root, ctx)  build DOM once, ctx = { reduced, id, width, height }
 *   update(p)         pure: the same p always draws the same frame
 *   resize(w, h)      the stage changed size
 *   unmount()         optional
 *
 * Because one stub serves several sections at once it also exports create(),
 * which the shell prefers over the default export, so every section gets its
 * own instance and its own state.
 */

const NS = 'http://www.w3.org/2000/svg';
const N = 34;                       // bases in the row
const STEP = 30;
const W = N * STEP;
const BASES = ['A', 'C', 'G', 'T'];
const INK = {
  A: 'var(--audit)',
  C: 'var(--cost)',
  G: 'var(--gain)',
  T: 'var(--tbd-ink)',
};

export function create() {
  let host = null;
  let bars = [];
  let wave = null;
  let reduced = false;
  let last = -1;

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    return node;
  }

  return {
    id: '_stub',

    mount(root, ctx) {
      host = root;
      reduced = !!(ctx && ctx.reduced);

      const svg = el('svg', {
        viewBox: '0 0 ' + W + ' 300',
        preserveAspectRatio: 'xMidYMid meet',
        'aria-hidden': 'true',
        focusable: 'false',
      });
      svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block';

      const base = el('line', { x1: 0, y1: 150, x2: W, y2: 150, 'stroke-width': 1.5 });
      base.style.stroke = 'var(--rule)';       /* var() only resolves via style */
      svg.appendChild(base);

      bars = [];
      for (let i = 0; i < N; i++) {
        const nt = BASES[(i * 7 + (i >> 2)) % 4];
        const g = el('g', { transform: 'translate(' + (i * STEP + STEP / 2) + ' 150)' });
        const rect = el('rect', { x: -6, y: -26, width: 12, height: 52, rx: 6 });
        rect.style.fill = INK[nt];
        rect.style.opacity = '0.1';
        g.appendChild(rect);
        svg.appendChild(g);
        bars.push({ g, rect, o: -1, y: -1 });
      }

      wave = el('circle', { cx: 0, cy: 150, r: 5 });
      wave.style.fill = 'var(--audit)';
      wave.style.opacity = '0';
      svg.appendChild(wave);

      host.appendChild(svg);
      this.update(reduced ? 1 : 0);
    },

    update(p) {
      if (!bars.length) return;
      const q = reduced ? 1 : (p < 0 ? 0 : p > 1 ? 1 : p);
      if (Math.abs(q - last) < 0.0005) return;
      last = q;

      const head = q * (N + 5);
      for (let i = 0; i < N; i++) {
        const b = bars[i];
        let t = (head - i) / 5;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        t = t * t * (3 - 2 * t);
        const o = Math.round((0.1 + 0.72 * t) * 100) / 100;
        const y = Math.round((1 - t) * 34);
        if (o !== b.o) { b.o = o; b.rect.style.opacity = o; }
        if (y !== b.y) {
          b.y = y;
          b.g.setAttribute('transform', 'translate(' + (i * STEP + STEP / 2) + ' ' + (150 + y) + ')');
        }
      }

      const lead = Math.min(head, N) * STEP;
      wave.setAttribute('cx', lead.toFixed(1));
      wave.style.opacity = q > 0.02 && q < 0.98 ? '0.9' : '0';
    },

    resize() {
      /* the viewBox does the work */
    },

    unmount() {
      if (host) host.textContent = '';
      host = null;
      bars = [];
      wave = null;
      last = -1;
    },
  };
}

export default create();
