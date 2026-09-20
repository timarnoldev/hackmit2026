/* Erbgut deck engine. No dependencies, no build step.
 *
 * Modes (query string):
 *   (none)       audience view
 *   ?presenter   presenter layout: current slide, next slide, notes, timer, pacing, clock
 *   ?embed       preview inside the presenter (no keys, no sync; driven by postMessage)
 *   ?sample      load results.sample.js instead of results.js (shows a SAMPLE DATA badge)
 *
 * Sync between windows: BroadcastChannel, plus postMessage to the opener / popup, plus a
 * localStorage "storage" event fallback. All three carry the same message; duplicates are dropped.
 */
(function () {
  'use strict';

  const params = new URLSearchParams(location.search);
  const EMBED = params.has('embed');
  const PRESENTER = params.has('presenter');
  const SAMPLE = params.has('sample');
  const root = document.documentElement;
  const ID = Math.random().toString(36).slice(2, 10);

  const store = {
    get(k) {
      try {
        return localStorage.getItem(k);
      } catch (e) {
        return null;
      }
    },
    set(k, v) {
      try {
        localStorage.setItem(k, v);
      } catch (e) {
        /* storage blocked: fine, nothing depends on it */
      }
    },
  };

  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));

  /* ------------------------------------------------------------ state */

  let slides = [];
  let steps = [];
  let mainCount = 0;
  const cur = { i: 0, f: 0 };
  let black = false;
  let overview = false;
  let ovSel = 0;
  let presenterLayout = false;
  let presenterWin = null;
  let R = null;
  const peers = new Set();
  let timer = loadTimer();

  /* ------------------------------------------------------------ boot */

  function loadResults(done) {
    const s = document.createElement('script');
    s.src = SAMPLE ? 'results.sample.js' : 'results.js';
    s.onload = () => done();
    s.onerror = () => done();
    document.head.appendChild(s);
  }

  function boot() {
    R = window.RESULTS && typeof window.RESULTS === 'object' ? window.RESULTS : null;
    if (!R) root.classList.add('results-broken');
    if (R && R.meta && R.meta.sample) root.classList.add('sample');
    if (SAMPLE && !(R && R.meta && R.meta.sample)) root.classList.add('sample');

    try {
      window.SlideHooks.init(R);
    } catch (e) {
      console.error('Slide build failed', e); // eslint-disable-line no-console
    }

    slides = $$('.stage > section.slide');
    steps = slides.map((s) => {
      const fs = $$('[data-f]', s).map((el) => parseInt(el.getAttribute('data-f'), 10) || 0);
      const explicit = parseInt(s.getAttribute('data-steps') || '0', 10);
      return Math.max(0, explicit, ...fs);
    });
    mainCount = slides.filter((s) => !s.hasAttribute('data-appendix')).length;
    addChrome();

    const start = parseHash(location.hash) || { i: 0, f: 0 };
    go(start.i, start.f, { instant: true, silent: true, noTimer: true });

    fitAll();
    window.addEventListener('resize', fitAll);
    if (window.ResizeObserver) new ResizeObserver(fitAll).observe(document.body);

    const relayout = () => {
      try {
        window.SlideHooks.layout(R);
      } catch (e) {
        /* layout helpers are cosmetic */
      }
    };
    relayout();
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(relayout);

    if (EMBED) {
      window.addEventListener('message', onEmbedMessage);
      return;
    }

    buildHelp();
    setupSync();
    document.addEventListener('keydown', onKey);
    $('#viewport').addEventListener('click', onStageClick);
    setupSwipe();
    window.addEventListener('hashchange', () => {
      const h = parseHash(location.hash);
      if (h && (h.i !== cur.i || h.f !== cur.f)) go(h.i, h.f, { instant: true });
    });
    if (PRESENTER) setPresenterLayout(true);
    send('hello', {});
    setInterval(tick, 500);
  }

  /* ------------------------------------------------------------ slide chrome */

  function addChrome() {
    let mainNo = 0;
    let appNo = 0;
    slides.forEach((s, k) => {
      const appx = s.hasAttribute('data-appendix');
      const label = appx ? `A${appNo++}` : `${String(++mainNo).padStart(2, '0')} / ${String(mainCount).padStart(2, '0')}`;
      s.dataset.num = appx ? `A${appNo - 1}` : String(mainNo);
      const foot = document.createElement('div');
      foot.className = 'foot';
      foot.innerHTML = `${window.SlideHooks.markSVG}<span class="name">Erbgut</span><span class="line">Every rule earns its bases.</span><span class="num">${label}</span>`;
      s.appendChild(foot);
      const ov = document.createElement('span');
      ov.className = 'ovnum';
      ov.textContent = appx ? `A${appNo - 1}` : String(mainNo);
      s.appendChild(ov);
      s.setAttribute('aria-roledescription', 'slide');
      s.setAttribute('aria-label', `${s.dataset.title || ''} (${label})`);
      if (k === 0) s.classList.add('present');
    });
  }

  /* ------------------------------------------------------------ fitting the 1920 x 1080 stage */

  function fit(viewport) {
    const stage = $('.stage', viewport);
    if (!stage) return;
    const w = viewport.clientWidth;
    const h = viewport.clientHeight;
    if (!w || !h) return;
    const s = Math.min(w / 1920, h / 1080);
    const x = Math.round((w - 1920 * s) / 2);
    const y = Math.round((h - 1080 * s) / 2);
    stage.style.transform = `translate(${x}px, ${y}px) scale(${s})`;
  }
  function fitAll() {
    $$('.viewport').forEach(fit);
    if (overview) layoutOverview();
  }

  /* ------------------------------------------------------------ navigation */

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function snap(fn) {
    root.classList.add('instant');
    fn();
    void document.body.offsetHeight; // force style flush with transitions off
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove('instant')));
  }

  function setSteps(slide, k, f) {
    for (let n = 1; n <= steps[k]; n++) slide.classList.toggle('s' + n, n <= f);
    $$('[data-f]', slide).forEach((el) => el.classList.toggle('on', (parseInt(el.getAttribute('data-f'), 10) || 0) <= f));
    slide.dataset.step = String(f);
  }

  /** Go to slide i (0-based), build f. opts: instant, silent (don't broadcast), noTimer. */
  function go(i, f, opts) {
    opts = opts || {};
    i = clamp(i | 0, 0, slides.length - 1);
    f = f === 'end' ? steps[i] : clamp(f | 0, 0, steps[i]);
    const changed = i !== cur.i;
    const forward = i > cur.i || (i === cur.i && f > cur.f);
    const apply = () => {
      slides.forEach((s, k) => {
        s.classList.toggle('present', k === i);
        s.classList.toggle('past', k < i);
        s.classList.toggle('future', k > i);
        s.setAttribute('aria-hidden', k === i ? 'false' : 'true');
      });
      setSteps(slides[i], i, f);
    };
    if (opts.instant) snap(apply);
    else if (changed) {
      // the incoming slide jumps to its state without replaying transitions, then fades in
      root.classList.add('instant');
      setSteps(slides[i], i, f);
      void slides[i].offsetHeight;
      root.classList.remove('instant');
      apply();
    } else apply();

    cur.i = i;
    cur.f = f;
    writeHash();
    updateProgress();
    if (!EMBED) {
      // the talk clock starts itself on the first forward step seen by a presenter view
      if (presenterLayout && !opts.noTimer && forward && !timer.running && timerElapsed() === 0) timerStart();
      if (!opts.silent) send('state', snapshot());
      updatePresenter();
    }
  }

  function next() {
    if (cur.f < steps[cur.i]) go(cur.i, cur.f + 1);
    else if (cur.i < slides.length - 1) go(cur.i + 1, 0);
  }
  function prev() {
    if (cur.f > 0) go(cur.i, cur.f - 1);
    else if (cur.i > 0) go(cur.i - 1, 'end');
  }

  function parseHash(h) {
    const m = /^#\/(\d+)(?:\/(\d+|end))?/.exec(h || '');
    if (!m) return null;
    return { i: parseInt(m[1], 10) - 1, f: m[2] === 'end' ? 'end' : parseInt(m[2] || '0', 10) };
  }
  function writeHash() {
    const h = `#/${cur.i + 1}/${cur.f}`;
    if (location.hash !== h) {
      try {
        history.replaceState(null, '', h);
      } catch (e) {
        location.hash = h;
      }
    }
  }

  function updateProgress() {
    const bar = $('#progress');
    if (!bar) return;
    const s = slides[cur.i];
    let p;
    if (s.hasAttribute('data-appendix')) p = 1;
    else p = (cur.i + (steps[cur.i] ? cur.f / (steps[cur.i] + 1) : 0)) / Math.max(1, mainCount - 1);
    bar.style.transform = `scaleX(${clamp(p, 0, 1)})`;
  }

  /* ------------------------------------------------------------ black, theme, motion, fullscreen */

  function setBlack(v, silent) {
    black = !!v;
    root.classList.toggle('black', black);
    if (!silent) send('state', snapshot());
    updatePresenter();
  }

  function setTheme(t, silent) {
    t = t === 'light' ? 'light' : 'dark';
    root.setAttribute('data-theme', t);
    store.set('sa-theme', t);
    postToPreview({ type: 'prefs', theme: t, motion: motionOn() });
    if (!silent) {
      send('prefs', { theme: t, motion: motionOn() });
      toast(t === 'light' ? 'Light theme' : 'Dark theme');
    }
  }
  function motionOn() {
    return !root.classList.contains('reduce-motion');
  }
  function setMotion(on, silent) {
    root.classList.toggle('reduce-motion', !on);
    store.set('sa-motion', on ? 'on' : 'off');
    postToPreview({ type: 'prefs', theme: root.getAttribute('data-theme'), motion: on });
    if (!silent) {
      send('prefs', { theme: root.getAttribute('data-theme'), motion: on });
      toast(on ? 'Motion on' : 'Motion reduced to fades');
    }
    updatePresenter();
  }

  function toggleFullscreen() {
    const d = document;
    if (d.fullscreenElement || d.webkitFullscreenElement) {
      (d.exitFullscreen || d.webkitExitFullscreen).call(d);
    } else {
      const el = d.documentElement;
      const req = el.requestFullscreen || el.webkitRequestFullscreen;
      if (req) {
        const p = req.call(el);
        if (p && p.catch) p.catch(() => toast('Fullscreen was blocked by the browser'));
      }
    }
  }

  let toastTimer = 0;
  function toast(msg) {
    const t = $('#toast');
    if (!t || EMBED) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 1600);
  }

  /* ------------------------------------------------------------ overview */

  function layoutOverview() {
    const n = slides.length;
    const cols = Math.ceil(Math.sqrt(n));
    const rows = Math.ceil(n / cols);
    const pad = 44;
    const gap = 30;
    const s = Math.min((1920 - 2 * pad - gap * (cols - 1)) / cols / 1920, (1080 - 2 * pad - gap * (rows - 1)) / rows / 1080);
    const cw = 1920 * s;
    const ch = 1080 * s;
    const ox = (1920 - (cols * cw + (cols - 1) * gap)) / 2;
    const oy = (1080 - (rows * ch + (rows - 1) * gap)) / 2;
    slides.forEach((sl, k) => {
      const c = k % cols;
      const r = Math.floor(k / cols);
      sl.style.setProperty('--ov-t', `translate(${ox + c * (cw + gap)}px, ${oy + r * (ch + gap)}px) scale(${s})`);
    });
  }
  function openOverview() {
    overview = true;
    ovSel = cur.i;
    layoutOverview();
    snap(() => slides.forEach((sl, k) => setSteps(sl, k, steps[k])));
    root.classList.add('overview');
    markOvSel();
  }
  function closeOverview(target) {
    overview = false;
    root.classList.remove('overview');
    slides.forEach((sl) => sl.classList.remove('ov-sel'));
    const t = target == null ? cur.i : target;
    snap(() => slides.forEach((sl, k) => setSteps(sl, k, k < t ? steps[k] : 0)));
    go(t, target == null ? cur.f : 0, { instant: true });
  }
  function markOvSel() {
    slides.forEach((sl, k) => sl.classList.toggle('ov-sel', k === ovSel));
  }

  /* ------------------------------------------------------------ help */

  function buildHelp() {
    const keys = [
      ['Next', 'Right, Down, Space, PageDown, Enter'],
      ['Previous', 'Left, Up, PageUp, Backspace'],
      ['First / last slide', 'Home / End'],
      ['Go to slide n', 'type n, then Enter'],
      ['Fullscreen', 'F'],
      ['Black screen', 'B or .'],
      ['Overview of all slides', 'O'],
      ['Presenter window', 'P'],
      ['Presenter layout in this window', 'S'],
      ['Timer start or pause / reset', 'Z / Shift+Z'],
      ['Notes text size', '+ / -'],
      ['Light or dark theme', 'T'],
      ['Motion on or off', 'M'],
      ['This help', '?'],
    ];
    $('#help').innerHTML =
      `<div class="help-card"><h2>Keys</h2><div class="cols">` +
      keys.map((k) => `<div class="k"><span>${k[0]}</span><span>${k[1].split(', ').map((x) => `<kbd>${x}</kbd>`).join(' ')}</span></div>`).join('') +
      `</div><p>Clickers send PageDown and PageUp, which advance builds first, then slides. The presenter window stays in sync in both directions; ` +
      `sync is guaranteed when the deck is served over http://localhost (run serve.sh). Add ?sample to the URL to preview with sample data.</p></div>`;
    $('#help').addEventListener('click', () => toggleHelp(false));
  }
  function toggleHelp(v) {
    const h = $('#help');
    h.classList.toggle('open', v == null ? !h.classList.contains('open') : v);
  }

  /* ------------------------------------------------------------ input */

  let digits = '';
  let digitTimer = 0;

  function onKey(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target && e.target.tagName) || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    const k = e.key;
    const helpOpen = $('#help').classList.contains('open');

    if (overview) {
      const n = slides.length;
      const cols = Math.ceil(Math.sqrt(n));
      const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols, PageDown: 1, PageUp: -1 };
      if (k in moves) {
        ovSel = clamp(ovSel + moves[k], 0, n - 1);
        markOvSel();
      } else if (k === 'Enter' || k === ' ') closeOverview(ovSel);
      else if (k === 'Escape' || k === 'o' || k === 'O') closeOverview(null);
      else return;
      e.preventDefault();
      return;
    }

    if (/^[0-9]$/.test(k)) {
      digits += k;
      clearTimeout(digitTimer);
      digitTimer = setTimeout(() => (digits = ''), 1500);
      toast(`Go to slide ${digits}, press Enter`);
      e.preventDefault();
      return;
    }

    switch (k) {
      case 'ArrowRight':
      case 'ArrowDown':
      case 'PageDown':
      case 'n':
      case 'N':
        next();
        break;
      case ' ':
      case 'Spacebar':
        if (e.shiftKey) prev();
        else next();
        break;
      case 'Enter':
        if (digits) {
          const n = parseInt(digits, 10);
          digits = '';
          go(n - 1, 0);
        } else next();
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
      case 'PageUp':
      case 'Backspace':
        prev();
        break;
      case 'Home':
        go(0, 0);
        break;
      case 'End':
        go(slides.length - 1, 'end');
        break;
      case 'f':
      case 'F':
      case 'F5':
        toggleFullscreen();
        break;
      case 'b':
      case 'B':
      case '.':
        setBlack(!black);
        break;
      case 'o':
      case 'O':
        openOverview();
        break;
      case 'Escape':
        if (helpOpen) toggleHelp(false);
        else if (presenterLayout && !PRESENTER) setPresenterLayout(false);
        else return;
        break;
      case '?':
      case '/':
        toggleHelp();
        break;
      case 'p':
      case 'P':
        openPresenterWindow();
        break;
      case 's':
      case 'S':
        if (!PRESENTER) setPresenterLayout(!presenterLayout);
        break;
      case 't':
      case 'T':
        setTheme(root.getAttribute('data-theme') === 'light' ? 'dark' : 'light');
        break;
      case 'm':
      case 'M':
        setMotion(!motionOn());
        break;
      case 'z':
        timerToggle();
        break;
      case 'Z':
        timerReset();
        break;
      case '+':
      case '=':
        notesSize(2);
        break;
      case '-':
      case '_':
        notesSize(-2);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function onStageClick(e) {
    if (e.target.closest('a, button, .p-btn')) return;
    if (overview) {
      const s = e.target.closest('.slide');
      if (s) closeOverview(slides.indexOf(s));
      return;
    }
    if (e.shiftKey) prev();
    else next();
  }

  function setupSwipe() {
    let x0 = null;
    let y0 = null;
    const vp = $('#viewport');
    vp.addEventListener('touchstart', (e) => {
      x0 = e.touches[0].clientX;
      y0 = e.touches[0].clientY;
    }, { passive: true });
    vp.addEventListener('touchend', (e) => {
      if (x0 == null) return;
      const dx = e.changedTouches[0].clientX - x0;
      const dy = e.changedTouches[0].clientY - y0;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
        if (dx < 0) next();
        else prev();
      }
      x0 = null;
    }, { passive: true });
  }

  /* ------------------------------------------------------------ sync between windows */

  let chan = null;
  let seq = 0;
  const seen = new Set();
  const SYNC_KEY = 'erbgut-sync';

  function snapshot() {
    return { i: cur.i, f: cur.f, black };
  }

  function setupSync() {
    try {
      if ('BroadcastChannel' in window) {
        chan = new BroadcastChannel('erbgut-deck');
        chan.onmessage = (e) => receive(e.data, null);
      }
    } catch (e) {
      chan = null;
    }
    window.addEventListener('message', (e) => receive(e.data, e.source));
    window.addEventListener('storage', (e) => {
      if (e.key !== SYNC_KEY || !e.newValue) return;
      try {
        receive(JSON.parse(e.newValue), null);
      } catch (err) {
        /* ignore */
      }
    });
    if (window.opener) peers.add(window.opener);
    // a popup re-announces itself, so a reloaded audience window finds it again
    if (window.opener) setInterval(() => send('ping', {}), 2000);
  }

  function send(type, payload) {
    if (EMBED) return;
    const msg = { __sa: 1, from: ID, id: `${ID}:${++seq}`, type, payload, t: Date.now() };
    if (chan) {
      try {
        chan.postMessage(msg);
      } catch (e) {
        /* ignore */
      }
    }
    if (presenterWin) peers.add(presenterWin);
    peers.forEach((w) => {
      try {
        if (w && !w.closed) w.postMessage(msg, '*');
        else peers.delete(w);
      } catch (e) {
        peers.delete(w);
      }
    });
    store.set(SYNC_KEY, JSON.stringify(msg));
  }

  function receive(msg, source) {
    if (!msg || msg.__sa !== 1 || msg.from === ID || seen.has(msg.id)) return;
    seen.add(msg.id);
    if (seen.size > 500) seen.clear();
    if (source && source !== window) peers.add(source);
    const p = msg.payload || {};
    switch (msg.type) {
      case 'state':
        if (overview) closeOverview(null);
        if (p.i !== cur.i || p.f !== cur.f) go(p.i, p.f, { silent: true, instant: p.i !== cur.i && Math.abs(p.i - cur.i) > 1 });
        if (!!p.black !== black) setBlack(p.black, true);
        break;
      case 'prefs':
        if (p.theme && p.theme !== root.getAttribute('data-theme')) setTheme(p.theme, true);
        if (typeof p.motion === 'boolean' && p.motion !== motionOn()) setMotion(p.motion, true);
        break;
      case 'timer':
        if (p && typeof p.changedAt === 'number' && p.changedAt > timer.changedAt) {
          timer = p;
          saveTimer();
          updatePresenter();
        }
        break;
      case 'hello':
        // the audience window is the authority on position; presenters only share the timer
        if (!PRESENTER) send('state', snapshot());
        send('prefs', { theme: root.getAttribute('data-theme'), motion: motionOn() });
        if (timer.changedAt) send('timer', timer);
        break;
      case 'ping':
        break;
      default:
        break;
    }
  }

  function onEmbedMessage(e) {
    const d = e.data;
    if (!d || d.__saEmbed !== 1) return;
    if (d.type === 'goto') go(d.i, d.f, { instant: true, silent: true });
    if (d.type === 'prefs') {
      if (d.theme) root.setAttribute('data-theme', d.theme === 'light' ? 'light' : 'dark');
      if (typeof d.motion === 'boolean') root.classList.toggle('reduce-motion', !d.motion);
    }
  }

  function openPresenterWindow() {
    const url = `${location.pathname}?presenter${SAMPLE ? '&sample' : ''}#/${cur.i + 1}/${cur.f}`;
    try {
      presenterWin = window.open(url, 'erbgut-presenter', 'popup,width=1480,height=900');
    } catch (e) {
      presenterWin = null;
    }
    if (!presenterWin) {
      toast('Popup blocked. Allow popups, or press S for the presenter layout here');
      return;
    }
    peers.add(presenterWin);
    try {
      presenterWin.focus();
    } catch (e) {
      /* ignore */
    }
    toast('Presenter window opened. Drag it to your laptop screen');
  }

  /* ------------------------------------------------------------ timer */

  function loadTimer() {
    try {
      const t = JSON.parse(store.get('sa-timer') || 'null');
      // a stopped timer from a rehearsal hours ago is not today's talk
      if (t && typeof t.acc === 'number' && (t.running || Date.now() - (t.changedAt || 0) < 6 * 3600 * 1000)) return t;
    } catch (e) {
      /* ignore */
    }
    return { running: false, startedAt: 0, acc: 0, changedAt: 0 };
  }
  function saveTimer() {
    store.set('sa-timer', JSON.stringify(timer));
  }
  function timerElapsed() {
    return timer.acc + (timer.running ? Date.now() - timer.startedAt : 0);
  }
  function timerCommit() {
    timer.changedAt = Date.now();
    saveTimer();
    send('timer', timer);
    updatePresenter();
  }
  function timerStart() {
    if (timer.running) return;
    timer = { running: true, startedAt: Date.now(), acc: timer.acc, changedAt: 0 };
    timerCommit();
  }
  function timerPause() {
    if (!timer.running) return;
    timer = { running: false, startedAt: 0, acc: timerElapsed(), changedAt: 0 };
    timerCommit();
  }
  function timerToggle() {
    if (timer.running) timerPause();
    else timerStart();
  }
  function timerReset() {
    timer = { running: false, startedAt: 0, acc: 0, changedAt: 0 };
    timerCommit();
    toast('Timer reset');
  }
  const mmss = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };

  /* ------------------------------------------------------------ presenter layout */

  const P = {};

  function planOf(k) {
    const m = /^(\d+)-(\d+)$/.exec(slides[k].getAttribute('data-plan') || '');
    return m ? { a: +m[1], b: +m[2] } : null;
  }
  const planEnd = () => Math.max(0, ...slides.map((s, k) => (planOf(k) ? planOf(k).b : 0)));

  function buildPresenter() {
    const host = $('#presenter');
    host.innerHTML =
      `<div class="p-top">` +
      `<div class="p-slide"></div><div class="p-flags"></div><div class="p-spacer"></div>` +
      `<div class="p-pace idle">Timer not started</div>` +
      `<div class="p-timer"><span class="p-elapsed">0:00</span>` +
      `<button class="p-btn" data-act="toggle" title="Start or pause (Z)">Start</button>` +
      `<button class="p-btn" data-act="reset" title="Reset (Shift+Z)">Reset</button></div>` +
      `<div class="p-clock"></div>` +
      (PRESENTER ? '' : `<button class="p-btn" data-act="exit" title="Back to the audience view (S)">Exit</button>`) +
      `</div>` +
      `<div class="p-left"><div class="p-cur"></div>` +
      `<div class="p-nextrow"><div class="p-next"><iframe title="Next slide preview" tabindex="-1"></iframe><div class="p-end" hidden>End of deck</div></div>` +
      `<div class="p-side"><div class="p-label">Next slide</div><div class="p-next-title"></div><div class="p-builds"></div></div></div>` +
      `<div class="p-plan"><div class="p-bar"></div><div class="p-planlab"></div></div></div>` +
      `<div class="p-right"><div class="p-notes"></div></div>`;
    P.slide = $('.p-slide', host);
    P.flags = $('.p-flags', host);
    P.pace = $('.p-pace', host);
    P.elapsed = $('.p-elapsed', host);
    P.toggle = $('[data-act="toggle"]', host);
    P.clock = $('.p-clock', host);
    P.cur = $('.p-cur', host);
    P.bar = $('.p-bar', host);
    P.planlab = $('.p-planlab', host);
    P.nextTitle = $('.p-next-title', host);
    P.iframe = $('.p-next iframe', host);
    P.end = $('.p-end', host);
    P.builds = $('.p-builds', host);
    P.notes = $('.p-notes', host);
    P.lastNotes = -1;
    P.lastNext = null;

    host.addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const act = b.getAttribute('data-act');
      if (act === 'toggle') timerToggle();
      if (act === 'reset') timerReset();
      if (act === 'exit') setPresenterLayout(false);
      b.blur();
    });

    // plan bar segments, main slides only
    const total = planEnd() || 180;
    let segs = '';
    slides.forEach((s, k) => {
      const p = planOf(k);
      if (!p) return;
      segs += `<div class="p-seg" data-k="${k}" style="left:${(p.a / total) * 100}%;width:${((p.b - p.a) / total) * 100}%">${s.dataset.num}</div>`;
    });
    P.bar.innerHTML = segs + '<div class="p-now"></div>';
    P.now = $('.p-now', P.bar);
    P.planlab.innerHTML = `<span>0:00</span><span>plan ${mmss(total * 1000)}</span>`;

    const qs = `?embed${SAMPLE ? '&sample' : ''}&theme=${root.getAttribute('data-theme')}&motion=${motionOn() ? 'on' : 'off'}`;
    const n = Math.min(cur.i + 1, slides.length - 1);
    P.iframe.src = `${location.pathname}${qs}#/${n + 1}/end`;
    P.iframe.addEventListener('load', () => {
      P.lastNext = null;
      updatePresenter();
    });
    P.built = true;
  }

  function setPresenterLayout(on) {
    presenterLayout = on;
    root.classList.toggle('layout-presenter', on);
    if (on && !P.built) buildPresenter();
    const vp = $('#viewport');
    if (on) P.cur.appendChild(vp);
    else document.body.insertBefore(vp, document.body.firstChild);
    fitAll();
    updatePresenter();
    document.title = on ? 'Erbgut presenter' : 'Erbgut pitch';
  }

  function postToPreview(msg) {
    if (!P.iframe || !P.iframe.contentWindow) return;
    try {
      P.iframe.contentWindow.postMessage(Object.assign({ __saEmbed: 1 }, msg), '*');
    } catch (e) {
      /* ignore */
    }
  }

  function updatePresenter() {
    if (!presenterLayout || !P.built) return;
    const s = slides[cur.i];
    const appx = s.hasAttribute('data-appendix');
    const label = appx ? `Appendix ${s.dataset.num}` : `Slide ${s.dataset.num} of ${mainCount}`;
    P.slide.innerHTML = `${label}: ${s.dataset.title || ''}<span class="mono">build ${cur.f} of ${steps[cur.i]}</span>`;

    const flags = [];
    if (root.classList.contains('sample')) flags.push('<span class="p-flag warn">SAMPLE DATA</span>');
    if (root.classList.contains('results-broken')) flags.push('<span class="p-flag warn">results.js did not load</span>');
    if (black) flags.push('<span class="p-flag black">Audience screen is black (B)</span>');
    if (!motionOn()) flags.push('<span class="p-flag">Motion off</span>');
    P.flags.innerHTML = flags.join('');

    // next slide preview (final state of the next slide)
    const n = cur.i + 1;
    if (n < slides.length) {
      P.end.hidden = true;
      P.iframe.style.visibility = 'visible';
      P.nextTitle.textContent = `${slides[n].hasAttribute('data-appendix') ? '' : slides[n].dataset.num + '. '}${slides[n].dataset.title || ''}`;
      if (P.lastNext !== n) {
        postToPreview({ type: 'goto', i: n, f: 'end' });
        P.lastNext = n;
      }
    } else {
      P.end.hidden = false;
      P.iframe.style.visibility = 'hidden';
      P.nextTitle.textContent = '';
    }

    // remaining builds on this slide
    const labels = (s.getAttribute('data-labels') || '').split('|').filter(Boolean);
    if (steps[cur.i] > 0) {
      const rest = [];
      for (let k = cur.f + 1; k <= steps[cur.i]; k++) rest.push(labels[k - 1] || `build ${k}`);
      P.builds.innerHTML = rest.length
        ? `Builds left on this slide: <span class="nextb">${rest[0]}</span>${rest.length > 1 ? ', then ' + rest.slice(1).join(', ') : ''}`
        : 'All builds shown. Next click goes to the next slide.';
    } else P.builds.textContent = 'No builds on this slide.';

    // notes
    if (P.lastNotes !== cur.i) {
      const aside = $('aside.notes', s);
      const p = planOf(cur.i);
      const target = p ? `Target ${mmss(p.a * 1000)} to ${mmss(p.b * 1000)}, ${p.b - p.a} s` : 'Appendix, not in the 3 minutes';
      P.notes.innerHTML = `<span class="p-target">${target}</span>${aside ? aside.innerHTML : '<p>No notes.</p>'}`;
      $$('.cue', P.notes).forEach((c, k) => {
        c.dataset.k = String(k + 1);
        c.textContent = `click ${k + 1}`;
      });
      P.notes.scrollTop = 0;
      P.lastNotes = cur.i;
    }
    $$('.cue', P.notes).forEach((c) => {
      const k = +c.dataset.k;
      c.classList.toggle('done', k <= cur.f);
      c.classList.toggle('next', k === cur.f + 1);
    });
    const nextCue = $('.cue.next', P.notes);
    if (nextCue) {
      const top = nextCue.offsetTop - P.notes.offsetTop;
      if (top > P.notes.scrollTop + P.notes.clientHeight * 0.7 || top < P.notes.scrollTop) {
        P.notes.scrollTo({ top: Math.max(0, top - 60), behavior: motionOn() ? 'smooth' : 'auto' });
      }
    }
    const size = parseInt(store.get('sa-notes-size') || '27', 10);
    P.notes.style.setProperty('--notes-size', size + 'px');

    $$('.p-seg', P.bar).forEach((seg) => {
      const k = +seg.dataset.k;
      seg.classList.toggle('cur', k === cur.i);
      seg.classList.toggle('done', k < cur.i);
    });
    tick();
  }

  function notesSize(d) {
    const size = clamp(parseInt(store.get('sa-notes-size') || '27', 10) + d, 16, 48);
    store.set('sa-notes-size', String(size));
    updatePresenter();
  }

  function tick() {
    if (!presenterLayout || !P.built) return;
    const ms = timerElapsed();
    const sec = ms / 1000;
    P.elapsed.textContent = mmss(ms);
    P.toggle.textContent = timer.running ? 'Pause' : ms > 0 ? 'Resume' : 'Start';
    P.clock.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const total = planEnd() || 180;
    P.now.style.left = `${clamp(sec / total, 0, 1) * 100}%`;
    P.now.style.background = sec > total ? '#FF86B0' : '#F4C45A';

    const p = planOf(cur.i);
    let cls = 'idle';
    let txt = 'Timer not started';
    if (!p) {
      txt = 'Appendix';
    } else if (ms > 0 || timer.running) {
      if (sec > p.b + 2) {
        cls = 'behind';
        txt = `Behind by ${Math.round(sec - p.b)} s`;
      } else if (sec < p.a - 2) {
        cls = 'ahead';
        txt = `Ahead by ${Math.round(p.a - sec)} s`;
      } else {
        cls = 'on';
        txt = `On pace, ${Math.max(0, Math.round(p.b - sec))} s left on this slide`;
      }
      if (sec > total) {
        cls = 'behind';
        txt = `Over time by ${Math.round(sec - total)} s`;
      }
    }
    P.pace.className = `p-pace ${cls}`;
    P.pace.textContent = txt;
  }

  /* ------------------------------------------------------------ start */

  window.Deck = {
    go: (i, f) => go(i, f),
    next,
    prev,
    state: () => ({ i: cur.i, f: cur.f, black, steps: steps.slice(), presenterLayout, timer: Object.assign({}, timer) }),
    openPresenterWindow,
    setPresenterLayout,
  };

  const start = () => loadResults(boot);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
