(function () {
  "use strict";

  const { ICONS, SHORT_TITLES, TITLES, esc, pct, formatCountdown } = window.QT;

  const el = {
    root: document.getElementById("wRoot"),
    grid: document.getElementById("wGrid"),
    sync: document.getElementById("wSync"),
    refresh: document.getElementById("wRefresh"),
    expand: document.getElementById("wExpand"),
    hide: document.getElementById("wHide"),
    quit: document.getElementById("wQuit"),
    pass: document.getElementById("wPass"),
  };

  let latestData = null;
  let lastColumnCount = -1;
  let busy = false;

  // Compact countdown for narrow columns: "2:10", "45dk", "3g 4s".
  function shortCountdown(epoch) {
    if (!epoch) return "";
    const diff = Math.floor(epoch - Date.now() / 1000);
    if (diff <= 0) return "yenilendi";
    const d = Math.floor(diff / 86400);
    const h = Math.floor((diff % 86400) / 3600);
    const m = Math.floor((diff % 3600) / 60);
    if (d > 0) return `${d}g ${h}s`;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}`;
    return `${m}dk`;
  }

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  function vessel(provider, tank) {
    if (!tank) {
      return `<div class="tank-vessel"><div class="glare"></div>
        <div class="vessel-center"><span class="w-none">—</span></div></div>`;
    }
    const level = Math.max(0, Math.min(100, tank.long_remaining));
    const hasSlice = tank.spendable !== null && tank.spendable !== undefined && level > 0;
    const slicePct = hasSlice ? Math.min(level, tank.spendable) : 0;
    const crit = level < 15 ? `<div class="vessel-center"><span class="w-crit">!</span></div>` : "";
    return `
      <div class="tank-vessel">
        <div class="glare"></div>
        <canvas class="liquid-canvas" data-tank-key="${esc(provider.id + ":" + tank.long_key)}"
                data-theme="${esc(provider.id)}" data-level="${level}" data-slice="${slicePct}"></canvas>
        <span class="w-level mono">%${Math.round(level)}</span>
        ${crit}
      </div>`;
  }

  function column(provider, best) {
    const tank = QT.pickTank(provider);
    const isBest = !!best && best === provider.id;
    const hasShort = tank && tank.short_remaining !== null && tank.short_remaining !== undefined;
    const name = (tank && provider.tanks && provider.tanks.length > 1 && tank.label) || SHORT_TITLES[provider.id];
    const low =
      tank && (tank.long_remaining <= 25 || (hasShort && tank.short_remaining <= 20));

    let tip = TITLES[provider.id] || provider.id;
    if (tank) {
      tip += `\nHaftalık kalan: %${pct(tank.long_remaining)} · yenilenme ${formatCountdown(tank.long_resets_at)}`;
      if (hasShort) {
        tip += `\n5 saatlik kalan: %${pct(tank.short_remaining)} · yenilenme ${formatCountdown(tank.short_resets_at)}`;
      }
      if (tank.spendable !== null && tank.spendable !== undefined) {
        tip += `\nBu 5 saatte harcanabilir: haftalığın %${pct(tank.spendable)}'i`;
      }
      const fcShort = hasShort ? QT.forecastInfo(tank.short_forecast) : null;
      const fcLong = QT.forecastInfo(tank.long_forecast);
      if (fcShort) tip += `\nTahmin (5 saatlik): ${fcShort.text}`;
      if (fcLong) tip += `\nTahmin (haftalık): ${fcLong.text}`;
      if (isBest) tip += `\n★ Şu an en uygun araç`;
    } else {
      tip += `\n${provider.error || "Henüz kota verisi yok"}`;
    }

    const shortLine = hasShort
      ? `<span class="w-short ${tank.short_remaining <= 15 ? "crit" : ""}">5s %${Math.round(tank.short_remaining)}</span>
         <span class="w-reset countdown" data-reset="${tank.short_resets_at || ""}">⟳${esc(shortCountdown(tank.short_resets_at))}</span>`
      : tank
        ? `<span class="w-short dim">haftalık</span>
           <span class="w-reset countdown" data-reset="${tank.long_resets_at || ""}">⟳${esc(shortCountdown(tank.long_resets_at))}</span>`
        : `<span class="w-short dim">veri yok</span>`;

    const col = document.createElement("section");
    const runsOut =
      tank &&
      [hasShort ? tank.short_forecast : null, tank.long_forecast].some((f) => f && f.runs_out);
    col.className =
      `tank-card w-col${low ? " low" : ""}${provider.status === "error" ? " err" : ""}` +
      `${isBest ? " best" : ""}${runsOut && !low ? " runs-out" : ""}`;
    col.dataset.id = provider.id;
    col.title = tip;
    col.innerHTML = `
      ${vessel(provider, tank)}
      ${isBest ? `<span class="w-best" aria-label="Şu an en uygun">★</span>` : ""}
      <div class="w-name"><span class="w-icon">${ICONS[provider.id] || ""}</span><span>${esc(name)}</span></div>
      <div class="w-meta mono">${shortLine}</div>`;
    return col;
  }

  // ---------------------------------------------------------------- pass-through toggle
  //
  // While clicks pass through, Python watches the cursor and makes the widget clickable
  // again only while it is over this button, so the button can always switch it off.

  function setPass(on) {
    on = !!on;
    el.pass.classList.toggle("on", on);
    el.root.classList.toggle("passing", on);
    el.pass.title = on
      ? "Tıklamalar şu an içinden geçiyor. Kapatmak için tıkla."
      : "Tıklamalar içinden geçsin (aç / kapat)";
  }
  window.__setClickThrough = setPass;

  function reportPassRect() {
    if (!api() || !api().widget_hot_rect) return;
    const r = el.pass.getBoundingClientRect();
    api().widget_hot_rect(r.left, r.top, r.width, r.height);
  }

  function render(data) {
    latestData = data;
    if (data.widget) setPass(data.widget.click_through);
    QT.applyUiOptions(data);
    const providers = QT.sortProviders(data.providers);
    el.grid.textContent = "";
    if (!providers.length) {
      el.grid.innerHTML = `<div class="w-empty">İzlenen araç yok</div>`;
    } else {
      const best = data.advice && data.advice.best;
      for (const p of providers) el.grid.appendChild(column(p, best));
    }
    if (window.QuotaLiquid) window.QuotaLiquid.sync(el.grid);

    const ts = data.generated_at_epoch ? new Date(data.generated_at_epoch * 1000) : new Date();
    el.sync.textContent = ts.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });

    requestAnimationFrame(reportPassRect);
    const n = Math.max(1, providers.length);
    if (n !== lastColumnCount && api() && api().widget_fit) {
      lastColumnCount = n;
      api().widget_fit(n);
    }
  }

  async function refresh() {
    if (busy || !api()) return;
    busy = true;
    el.refresh.classList.add("spinning");
    try {
      render(await api().refresh_now());
    } finally {
      busy = false;
      el.refresh.classList.remove("spinning");
    }
  }

  // Buttons live inside the drag region: keep their mousedown from starting a drag.
  for (const b of [el.pass, el.refresh, el.expand, el.hide, el.quit]) {
    b.addEventListener("mousedown", (e) => e.stopPropagation());
  }
  el.refresh.addEventListener("click", refresh);
  el.pass.addEventListener("click", async () => {
    if (!api()) return;
    try {
      setPass(await api().toggle_click_through());
    } catch (_) {
      /* state arrives with the next update */
    }
  });
  window.addEventListener("resize", () => requestAnimationFrame(reportPassRect));
  el.expand.addEventListener("click", () => api() && api().show_panel());
  el.hide.addEventListener("click", () => api() && api().minimize_to_tray());
  el.quit.addEventListener("click", () => api() && api().quit_app());
  el.root.addEventListener("dblclick", (e) => {
    if (!e.target.closest(".w-btn") && api()) api().show_panel();
  });

  // ---------------------------------------------------------------- resize grips
  //
  // Frameless windows have no native resize border, so the grips drive it:
  // pointer capture keeps events flowing outside the window, and bounds go to
  // Python one call at a time (latest wins) so the window never lags behind.

  const LIM = { minCol: 74, maxCol: 320, minH: 170, maxH: 900, minW: 258, pad: 10, gap: 8 }; // = app/main.py
  const widthFor = (n, col) => Math.max(LIM.minW, 2 * LIM.pad + n * col + (n - 1) * LIM.gap);
  const clampN = (v, a, b) => Math.min(b, Math.max(a, v));

  let rs = null; // active resize: { edge, sx, sy, x, y, w, h, last }
  let inflight = false;
  let queued = null;

  async function flushBounds() {
    if (inflight || !queued || !api()) return;
    const b = queued;
    queued = null;
    inflight = true;
    try {
      await api().widget_set_bounds(b.x, b.y, b.w, b.h);
    } catch (_) {
      /* ignore: next move retries */
    } finally {
      inflight = false;
      if (queued) flushBounds();
    }
  }

  function onGripMove(e) {
    if (!rs) return;
    const dx = e.screenX - rs.sx;
    const dy = e.screenY - rs.sy;
    const n = Math.max(1, lastColumnCount);
    let { x, y, w, h } = rs;
    if (rs.edge.includes("e")) w = clampN(rs.w + dx, widthFor(n, LIM.minCol), widthFor(n, LIM.maxCol));
    if (rs.edge.includes("w")) {
      w = clampN(rs.w - dx, widthFor(n, LIM.minCol), widthFor(n, LIM.maxCol));
      x = rs.x + rs.w - w; // right edge stays put
    }
    if (rs.edge.includes("s")) h = clampN(rs.h + dy, LIM.minH, LIM.maxH);
    if (rs.edge.includes("n")) {
      h = clampN(rs.h - dy, LIM.minH, LIM.maxH);
      y = rs.y + rs.h - h; // bottom edge stays put
    }
    rs.last = { x, y, w, h };
    queued = rs.last;
    flushBounds();
  }

  async function onGripEnd(e) {
    if (!rs) return;
    const grip = e.currentTarget;
    try {
      grip.releasePointerCapture(e.pointerId);
    } catch (_) {
      /* already released */
    }
    const last = rs.last;
    rs = null;
    document.body.classList.remove("resizing");
    document.body.style.cursor = "";
    if (!last || !api()) return;
    while (inflight || queued) await new Promise((r) => setTimeout(r, 16));
    api().widget_resize_done(last.w, last.h);
  }

  document.querySelectorAll(".rz").forEach((grip) => {
    grip.addEventListener("mousedown", (e) => e.stopPropagation());
    grip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      try {
        grip.setPointerCapture(e.pointerId);
      } catch (_) {
        /* synthetic/stale pointer: events still arrive while inside the window */
      }
      // window origin from the event itself (frameless: client (0,0) is the window corner)
      rs = {
        edge: grip.dataset.edge,
        sx: e.screenX,
        sy: e.screenY,
        x: e.screenX - e.clientX,
        y: e.screenY - e.clientY,
        w: window.innerWidth,
        h: window.innerHeight,
        last: null,
      };
      document.body.classList.add("resizing");
      document.body.style.cursor = getComputedStyle(grip).cursor;
    });
    grip.addEventListener("pointermove", onGripMove);
    grip.addEventListener("pointerup", onGripEnd);
    grip.addEventListener("pointercancel", onGripEnd);
  });

  // Tank selection may change in the main panel (shared localStorage).
  window.addEventListener("storage", (e) => {
    if (e.key === "quotatank.selectedTank" && latestData) render(latestData);
  });

  setInterval(() => {
    if (QT.isShown && !QT.isShown()) return;
    document.querySelectorAll(".countdown[data-reset]").forEach((node) => {
      const epoch = Number(node.dataset.reset);
      if (epoch) node.textContent = "⟳" + shortCountdown(epoch);
    });
  }, 30000);

  QT.installMotionGlue();
  QT.installVisibility();
  QT.installActivity();
  // Small tanks: a calmer idle ripple is indistinguishable and costs a third less.
  if (window.QuotaLiquid) window.QuotaLiquid.setOptions({ idleFps: 20 });

  window.__onDashboardUpdate = render;
  window.addEventListener("pywebviewready", async () => {
    try {
      render(await api().get_dashboard());
    } catch (_) {
      /* next push will fill it */
    }
  });
})();
