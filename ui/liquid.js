/*
 * QuotaTank liquid engine — a small 1D shallow-water simulation per tank, drawn on canvas.
 *
 *   u_t = -g · h_x − damping·u            (horizontal flow between columns, walls: u = 0)
 *   h_t = −H_eff · u_x                    (surface height per column)
 *
 * H_eff = (W/π)·tanh(π·D/W) gives the first slosh mode the dispersion of real water
 * (shallow tanks swing slower than deep ones). Moving the window changes the tank's
 * velocity; in the tank's frame that is an equal and opposite kick to the liquid.
 * Fast-rising water at a wall throws droplets, which fall back and dent the surface.
 *
 * Loop: 60 fps while anything moves, a gentle ~20 fps idle ripple otherwise, and fully
 * stopped while the window is hidden.
 */
(function () {
  "use strict";

  const THEMES = {
    claude: {
      weekly: ["#d97757", "#b44b2c", "#7c2d12"],
      slice: ["#ffb088", "#f58b5c"],
      border: "#ffd4be",
      glow: "rgba(255, 212, 190, 0.75)",
      top: "#ffb088",
      back: "#e06b44",
      drop: "#ffc4a3",
    },
    codex: {
      weekly: ["#6b7280", "#374151", "#18181b"],
      slice: ["#ffffff", "#d4d4d8"],
      border: "#ffffff",
      glow: "rgba(255, 255, 255, 0.8)",
      top: "#ffffff",
      back: "#a1a1aa",
      drop: "#f4f4f5",
    },
    antigravity: {
      weekly: ["#2563eb", "#1d4ed8", "#0f172a"],
      slice: ["#7dd3fc", "#38bdf8"],
      border: "#bae6fd",
      glow: "rgba(186, 230, 253, 0.85)",
      top: "#7dd3fc",
      back: "#3b82f6",
      drop: "#bae6fd",
    },
  };

  const CFG = {
    cols: 44,
    g: 5200,            // px/s² — sets how fast the liquid swings (~1.3 Hz for a full tank)
    damping: 1.0,       // 1/s, horizontal flow friction
    viscosity: 150,     // px²/s, smooths grid-scale noise into round crests
    moveGain: 0.11,     // share of the window's velocity change the liquid feels (screen px >> tank px)
    maxFlow: 240,       // px/s cap on flow speed so hard flicks slosh instead of flinging
    shakeGain: 0.006,   // px of surface kick per px/s of vertical velocity change
    ambient: 1.5,       // px, idle ripple height
    idleFps: 20,         // idle ripple is only ~1.5 px: 20 fps looks the same as 30, a third less GPU
    splashRate: 230,    // px/s surface rise at a wall that throws droplets
    maxDrops: 48,
    bubbleRate: 0.7,    // bubbles per second at rest
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const rand = (a, b) => a + Math.random() * (b - a);

  class Tank {
    constructor(themeId) {
      this.theme = THEMES[themeId] || THEMES.codex;
      this.N = CFG.cols;
      this.h = new Float32Array(this.N);
      this.hPrev = new Float32Array(this.N);
      this.u = new Float32Array(this.N + 1);
      this.level = null;
      this.target = 0;
      this.slice = 0;
      this.sliceTarget = 0;
      this.drops = [];
      this.bubbles = [];
      this.canvas = null;
      this.ctx = null;
      this.W = 0;
      this.H = 0;
      this.dpr = 1;
      this.phase = Math.random() * 10;
      this.ys = new Float32Array(this.N); // scratch: surface y per column
    }

    attach(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.resize();
    }

    resize() {
      if (!this.canvas) return;
      const r = this.canvas.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      const dpr = window.devicePixelRatio || 1;
      this.W = r.width;
      this.H = r.height;
      this.dpr = dpr;
      const w = Math.round(r.width * dpr);
      const h = Math.round(r.height * dpr);
      if (this.canvas.width !== w || this.canvas.height !== h) {
        this.canvas.width = w;
        this.canvas.height = h;
      }
    }

    setTarget(levelPct, slicePct) {
      this.target = clamp(levelPct / 100, 0, 1);
      this.sliceTarget = clamp(slicePct / 100, 0, this.target);
      if (this.level === null) {
        this.level = this.target;
        this.slice = this.sliceTarget;
      }
    }

    get depth() {
      return (this.level || 0) * this.H;
    }

    impulse(dvx, dvy) {
      if (this.depth < 1) return;
      // Physics runs in pixels: a small (widget) tank gets proportionally smaller kicks,
      // otherwise the same flick would fling the liquid wall to wall.
      const sc = clamp(this.W / 200, 0.3, 1);
      const k = -CFG.moveGain * sc * dvx;
      const cap = CFG.maxFlow * sc;
      for (let j = 1; j < this.N; j++) this.u[j] = clamp(this.u[j] + k, -cap, cap);
      const s = Math.min(this.H * 0.06, Math.abs(dvy) * CFG.shakeGain * sc);
      if (s > 0.25) {
        const m = 1 + Math.floor(Math.random() * 3);
        const p = Math.random() * Math.PI * 2;
        for (let i = 0; i < this.N; i++) {
          const x = (i + 0.5) / this.N;
          this.h[i] += s * Math.cos(Math.PI * m * x + p) * rand(0.75, 1.15);
        }
      }
    }

    energy() {
      let e = 0;
      for (let i = 0; i < this.N; i++) e += this.h[i] * this.h[i];
      for (let j = 0; j <= this.N; j++) e += this.u[j] * this.u[j] * 0.0004;
      return e / this.N;
    }

    transitioning() {
      return Math.abs(this.target - this.level) > 0.0005 || Math.abs(this.sliceTarget - this.slice) > 0.0005;
    }

    step(dt, agitated) {
      const ease = 1 - Math.exp(-dt * 3.2);
      this.level += (this.target - this.level) * ease;
      this.slice += (this.sliceTarget - this.slice) * ease;

      const N = this.N;
      const h = this.h;
      const u = this.u;
      const D = this.depth;
      if (D < 0.5 || this.W < 2) {
        h.fill(0);
        u.fill(0);
        this.drops.length = 0;
        this.bubbles.length = 0;
        return;
      }

      const dx = this.W / N;
      const Heff = Math.max(2, (this.W / Math.PI) * Math.tanh((Math.PI * D) / this.W));
      const c = Math.sqrt(CFG.g * Heff);
      const sub = Math.max(1, Math.ceil((c * dt) / dx / 0.4));
      const sdt = dt / sub;
      const nu = Math.min(0.24, (CFG.viscosity * sdt) / (dx * dx));
      const fr = 1 - CFG.damping * sdt;

      this.hPrev.set(h);
      for (let s = 0; s < sub; s++) {
        for (let j = 1; j < N; j++) {
          u[j] = (u[j] - (CFG.g * (h[j] - h[j - 1]) / dx) * sdt) * fr;
        }
        for (let i = 0; i < N; i++) h[i] -= (Heff * (u[i + 1] - u[i]) / dx) * sdt;
        let prev = h[0];
        for (let i = 0; i < N; i++) {
          const cur = h[i];
          const l = i > 0 ? prev : cur;
          const r = i < N - 1 ? h[i + 1] : cur;
          h[i] = cur + nu * (l - 2 * cur + r);
          prev = cur;
        }
      }

      // Volume conservation + keep the surface inside the vessel.
      let mean = 0;
      for (let i = 0; i < N; i++) mean += h[i];
      mean /= N;
      for (let i = 0; i < N; i++) h[i] = clamp(h[i] - mean, -D, this.H - D + 2);

      // Splashes: water racing up a wall throws droplets.
      if (agitated) {
        for (const [i, dir] of [[0, 1], [N - 1, -1]]) {
          const rate = (h[i] - this.hPrev[i]) / dt;
          if (rate > CFG.splashRate && this.drops.length < CFG.maxDrops) {
            const n = 1 + Math.floor(Math.random() * Math.min(4, rate / CFG.splashRate + 1));
            for (let k = 0; k < n; k++) {
              this.drops.push({
                x: dir > 0 ? rand(3, 10) : this.W - rand(3, 10),
                y: this.H - (D + h[i]) - 2,
                vx: dir * rand(25, 110),
                vy: -rate * rand(0.45, 0.9),
                r: rand(1.1, 2.6),
              });
            }
          }
        }
      }

      // Droplets: ballistic, bounce off the glass, merge back with a small dent.
      for (let k = this.drops.length - 1; k >= 0; k--) {
        const d = this.drops[k];
        d.vy += CFG.g * dt;
        d.x += d.vx * dt;
        d.y += d.vy * dt;
        if (d.x < 2) { d.x = 2; d.vx = -d.vx * 0.35; }
        if (d.x > this.W - 2) { d.x = this.W - 2; d.vx = -d.vx * 0.35; }
        if (d.y < 2) { d.y = 2; d.vy = Math.abs(d.vy) * 0.2; }
        const idx = clamp(Math.floor(d.x / dx), 0, N - 1);
        const sy = this.H - (D + h[idx]);
        if (d.vy > 0 && d.y >= sy) {
          h[idx] -= d.r * 0.35;
          this.drops.splice(k, 1);
        }
      }

      // Bubbles: a slow trickle at rest, more while the liquid is stirred.
      const e = this.energy();
      const rate = CFG.bubbleRate * (1 + Math.min(6, e * 0.08)) * clamp(D / 60, 0.2, 1);
      if (this.bubbles.length < 18 && Math.random() < rate * dt) {
        this.bubbles.push({
          x: rand(0.15, 0.85) * this.W,
          y: this.H + 4,
          r: rand(1.4, 3.4),
          wob: Math.random() * 6.28,
        });
      }
      for (let k = this.bubbles.length - 1; k >= 0; k--) {
        const b = this.bubbles[k];
        b.y -= (24 + b.r * 11) * dt;
        b.wob += dt * 3;
        b.x += Math.sin(b.wob) * 6 * dt;
        const idx = clamp(Math.floor(b.x / dx), 0, N - 1);
        if (b.y <= this.H - (D + h[idx]) + b.r) this.bubbles.splice(k, 1);
      }
    }

    // ---------------------------------------------------------------- drawing

    surfacePath(ctx, ys, xs, closeToBottom) {
      const N = this.N;
      ctx.beginPath();
      ctx.moveTo(0, ys[0]);
      ctx.lineTo(xs[0], ys[0]);
      for (let i = 0; i < N - 1; i++) {
        const mx = (xs[i] + xs[i + 1]) / 2;
        const my = (ys[i] + ys[i + 1]) / 2;
        ctx.quadraticCurveTo(xs[i], ys[i], mx, my);
      }
      ctx.quadraticCurveTo(xs[N - 1], ys[N - 1], this.W, ys[N - 1]);
      if (closeToBottom) {
        ctx.lineTo(this.W, this.H + 2);
        ctx.lineTo(0, this.H + 2);
        ctx.closePath();
      }
    }

    draw(t, ambient) {
      const ctx = this.ctx;
      if (!ctx || this.W < 2) return;
      ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

      const D = this.depth;
      if (D < 0.5) {
        ctx.clearRect(0, 0, this.W, this.H);
        return;
      }
      const N = this.N;
      const dx = this.W / N;
      const th = this.theme;
      const amb = ambient * Math.min(1, D / 24);

      const xs = new Float32Array(N);
      const ys = this.ys;
      const yb = new Float32Array(N);
      for (let i = 0; i < N; i++) {
        const x = (i + 0.5) * dx;
        xs[i] = x;
        const k = x / this.W;
        const r1 = Math.sin(k * 9.4 - t * 2.3 + this.phase) * amb;
        const r2 = Math.sin(k * 15.7 + t * 1.6 + this.phase * 2) * amb * 0.45;
        ys[i] = this.H - (D + this.h[i] + r1 + r2);
        yb[i] = this.H - (D + this.h[i] * 0.92 + Math.sin(k * 11 + t * 1.9 + this.phase) * amb * 1.2) - 2.5;
      }
      const meanTop = this.H - D;
      const S = this.slice * this.H;
      let yi = null;
      if (S > 0.6) {
        yi = new Float32Array(N);
        for (let i = 0; i < N; i++) {
          yi[i] = Math.max(ys[i] + 1.5, this.H - (D - S) - this.h[i] * 0.55);
        }
      }

      ctx.clearRect(0, 0, this.W, this.H);
      this.paint(ctx, xs, ys, yb, yi, meanTop, S, th);
    }

    paint(ctx, xs, ys, yb, yi, meanTop, S, th) {
      const N = this.N;

      // back wave (lighter, slightly above the surface — the depth cue from the design)
      ctx.globalAlpha = 0.5;
      ctx.fillStyle = th.back;
      this.surfacePath(ctx, yb, xs, true);
      ctx.fill();
      ctx.globalAlpha = 1;

      // weekly body
      const gw = ctx.createLinearGradient(0, meanTop - 8, 0, this.H);
      gw.addColorStop(0, th.weekly[0]);
      gw.addColorStop(0.6, th.weekly[1]);
      gw.addColorStop(1, th.weekly[2]);
      ctx.fillStyle = gw;
      this.surfacePath(ctx, ys, xs, true);
      ctx.fill();

      // 5-hour slice: between the surface and an interface that follows it more softly
      if (yi) {
        ctx.save();
        this.surfacePath(ctx, ys, xs, false);
        ctx.lineTo(this.W, yi[N - 1]); // straight down the wall (a diagonal left a sliver of weekly colour)
        for (let i = N - 1; i >= 0; i--) ctx.lineTo(xs[i], yi[i]);
        ctx.lineTo(0, yi[0]);
        ctx.closePath();
        const gs = ctx.createLinearGradient(0, meanTop - 6, 0, meanTop + S + 6);
        gs.addColorStop(0, th.slice[0]);
        gs.addColorStop(1, th.slice[1]);
        ctx.fillStyle = gs;
        ctx.fill();
        ctx.restore();

        // dashed boundary
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(0, yi[0]);
        for (let i = 0; i < N; i++) ctx.lineTo(xs[i], yi[i]);
        ctx.lineTo(this.W, yi[N - 1]);
        // cheap glow: a wide translucent stroke under the dashes (shadowBlur is costly on the GPU)
        ctx.lineWidth = 6;
        ctx.strokeStyle = th.glow;
        ctx.globalAlpha = 0.35;
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = th.border;
        ctx.stroke();
        ctx.restore();
      }

      // bubbles (inside the liquid)
      if (this.bubbles.length) {
        ctx.save();
        this.surfacePath(ctx, ys, xs, true);
        ctx.clip();
        for (const b of this.bubbles) {
          ctx.beginPath();
          ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(255,255,255,0.22)";
          ctx.fill();
          ctx.beginPath();
          ctx.arc(b.x - b.r * 0.35, b.y - b.r * 0.35, b.r * 0.35, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(255,255,255,0.55)";
          ctx.fill();
        }
        ctx.restore();
      }

      // surface highlight
      ctx.save();
      this.surfacePath(ctx, ys, xs, false);
      ctx.lineWidth = 6;
      ctx.strokeStyle = th.glow;
      ctx.globalAlpha = 0.25;
      ctx.stroke();
      ctx.lineWidth = 2;
      ctx.strokeStyle = th.top;
      ctx.globalAlpha = 0.9;
      ctx.stroke();
      ctx.restore();

      // droplets
      if (this.drops.length) {
        ctx.fillStyle = th.drop;
        for (const d of this.drops) {
          const sp = Math.hypot(d.vx, d.vy);
          const stretch = clamp(sp / 400, 1, 2.2);
          const ang = Math.atan2(d.vy, d.vx);
          ctx.save();
          ctx.translate(d.x, d.y);
          ctx.rotate(ang);
          ctx.beginPath();
          ctx.ellipse(0, 0, d.r * stretch, d.r, 0, 0, Math.PI * 2);
          ctx.globalAlpha = 0.9;
          ctx.fill();
          ctx.restore();
        }
      }
    }
  }

  // ---------------------------------------------------------------- engine

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const tanks = new Map(); // key -> Tank
  let attached = new Set();
  let visible = true;
  let raf = 0;
  let idleTimer = 0;
  let lastTs = 0;
  let onFrame = null;
  // user settings (Ayarlar > Görünüm)
  //   onlyActive: idle ripple only while the user is on the window (focused or hovered)
  const opts = { idleWaves: true, slosh: true, idleFps: CFG.idleFps, onlyActive: true };
  let active = true;   // window focused or mouse over it (set by the page)
  let rippleGain = 1;  // eases 0..1 so the ripple settles / returns smoothly
  const rippleWanted = () =>
    !reduceMotion && opts.idleWaves && (active || !opts.onlyActive);
  const ambientNow = () => CFG.ambient * rippleGain;
  const ro = "ResizeObserver" in window
    ? new ResizeObserver((entries) => {
        for (const en of entries) {
          const tk = tanks.get(en.target.dataset.tankKey);
          if (tk) tk.resize();
        }
        wake();
      })
    : null;

  function busy() {
    for (const key of attached) {
      const tk = tanks.get(key);
      if (!tk) continue;
      if (tk.energy() > 0.02 || tk.drops.length || tk.transitioning()) return true;
    }
    return false;
  }

  function frame(ts) {
    raf = 0;
    if (!visible) return;
    const dt = clamp(lastTs ? (ts - lastTs) / 1000 : 1 / 60, 1 / 240, 1 / 20);
    lastTs = ts;
    if (onFrame) onFrame();
    const goal = rippleWanted() ? 1 : 0;
    rippleGain += (goal - rippleGain) * (1 - Math.exp(-dt * 4));
    if (Math.abs(goal - rippleGain) < 0.01) rippleGain = goal;
    const agitated = busy();
    const t = ts / 1000;
    for (const key of attached) {
      const tk = tanks.get(key);
      if (!tk) continue;
      tk.step(dt, agitated);
      tk.draw(t, ambientNow());
    }
    schedule();
  }

  function schedule() {
    if (raf || idleTimer || !visible) return;
    if (busy() || (onFrame && onFrame.pending && onFrame.pending())) {
      raf = requestAnimationFrame(frame);
    } else if (rippleWanted() || rippleGain > 0) {
      // idle ripple (or its fade-out) at a reduced frame rate
      idleTimer = setTimeout(() => {
        idleTimer = 0;
        raf = requestAnimationFrame(frame);
      }, 1000 / opts.idleFps);
    } else {
      lastTs = 0;
    }
  }

  function wake() {
    if (!visible) return;
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = 0;
    }
    if (!raf) raf = requestAnimationFrame(frame);
  }

  window.QuotaLiquid = {
    /** Bind canvases in `root` (after each re-render) to persistent simulations. */
    sync(root) {
      const next = new Set();
      if (ro) ro.disconnect();
      root.querySelectorAll("canvas.liquid-canvas").forEach((cv) => {
        const key = cv.dataset.tankKey;
        let tk = tanks.get(key);
        if (!tk) {
          tk = new Tank(cv.dataset.theme);
          tanks.set(key, tk);
        }
        tk.attach(cv);
        tk.setTarget(Number(cv.dataset.level) || 0, Number(cv.dataset.slice) || 0);
        next.add(key);
        if (ro) ro.observe(cv);
      });
      attached = next;
      lastTs = 0;
      // draw immediately so a re-render never flashes an empty tank
      for (const key of attached) tanks.get(key).draw(performance.now() / 1000, ambientNow());
      wake();
    },

    /** Window velocity change in px/s (positive = moving right / down). */
    impulse(dvx, dvy) {
      if (reduceMotion || !opts.slosh) return;
      for (const key of attached) tanks.get(key).impulse(dvx, dvy);
      wake();
    },

    /** { idleWaves, slosh, idleFps, onlyActive } — apply user settings; redraws once so a stopped ripple goes flat. */
    setOptions(o) {
      if (!o) return;
      if ("idleWaves" in o) opts.idleWaves = !!o.idleWaves;
      if ("slosh" in o) opts.slosh = !!o.slosh;
      if ("idleFps" in o) opts.idleFps = clamp(Number(o.idleFps) || CFG.idleFps, 5, 60);
      if ("onlyActive" in o) opts.onlyActive = !!o.onlyActive;
      wake();
    },

    /** Is the user on this window (focused or hovering)? Idle ripple pauses otherwise. */
    setActive(v) {
      if (active === !!v) return;
      active = !!v;
      wake();
    },

    setVisible(v) {
      visible = !!v;
      if (!visible) {
        if (raf) cancelAnimationFrame(raf);
        if (idleTimer) clearTimeout(idleTimer);
        raf = 0;
        idleTimer = 0;
        lastTs = 0;
      } else {
        wake();
      }
    },

    /** Called once per frame before stepping (used for drag-stop detection). */
    setFrameHook(fn) {
      onFrame = fn;
    },

    wake,
  };
})();
