/*
 * Compact horizontal widget: one bar, a mini tank per tool, a detail card on click.
 *
 * The page sizes its own window: .cq-root is measured and Python resizes the window to
 * match (bottom edge fixed), so opening the card grows the window upwards.
 */
(function () {
  "use strict";

  const { SHORT_TITLES, TITLES, VENDORS, esc, pct, formatCountdown } = window.QT;

  const el = {
    root: document.getElementById("cqRoot"),
    bar: document.getElementById("cqBar"),
    mods: document.getElementById("cqMods"),
    pop: document.getElementById("cqPop"),
    pass: document.getElementById("cqPass"),
    refresh: document.getElementById("cqRefresh"),
    expand: document.getElementById("cqExpand"),
    hide: document.getElementById("cqHide"),
  };

  let latestData = null;
  let openId = null;        // provider whose detail card is open
  let busy = false;
  const modules = new Map(); // provider id -> module element (kept, so levels animate)

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  // "2:10", "45dk", "3g 4s"
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

  const has = (v) => v !== null && v !== undefined;

  /** Everything both the module and the card need about one provider. */
  function describe(provider, best) {
    const tank = QT.pickTank(provider);
    const hasShort = !!tank && has(tank.short_remaining);
    const level = tank ? Math.max(0, Math.min(100, tank.long_remaining)) : 0;
    const slice = tank && has(tank.spendable) && level > 0 ? Math.min(level, tank.spendable) : 0;
    const multi = provider.tanks && provider.tanks.length > 1;
    const runsOut =
      !!tank && [hasShort ? tank.short_forecast : null, tank.long_forecast].some((f) => f && f.runs_out);
    const low = !!tank && (tank.long_remaining <= 25 || (hasShort && tank.short_remaining <= 20));
    return {
      tank,
      hasShort,
      level,
      slice,
      name: (tank && multi && tank.label) || SHORT_TITLES[provider.id] || provider.id,
      isBest: !!best && best === provider.id,
      low,
      runsOut: runsOut && !low,
    };
  }

  function tipFor(provider, d) {
    const { tank, hasShort } = d;
    let tip = TITLES[provider.id] || provider.id;
    if (!tank) return `${tip}\n${provider.error || "Henüz kota verisi yok"}`;
    tip += `\nHaftalık kalan: %${pct(tank.long_remaining)} · yenilenme ${formatCountdown(tank.long_resets_at)}`;
    if (hasShort) tip += `\n5 saatlik kalan: %${pct(tank.short_remaining)} · yenilenme ${formatCountdown(tank.short_resets_at)}`;
    if (d.isBest) tip += `\n★ Şu an en uygun araç`;
    return tip + "\nAyrıntılar için tıkla";
  }

  // ---------------------------------------------------------------- bar modules

  function buildModule(id) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cq-mod";
    b.dataset.id = id;
    b.innerHTML = `
      <div class="cq-tube">
        <div class="cq-glare"></div>
        <div class="cq-liquid"><div class="cq-slice"></div><div class="cq-weekly"></div></div>
      </div>
      <div class="cq-txt">
        <div class="cq-name"><span class="cq-label"></span><span class="cq-flags"></span></div>
        <div class="cq-nums"></div>
      </div>`;
    // A press that turned into a window drag must not also toggle the card.
    let down = null;
    b.addEventListener("mousedown", (e) => { down = [e.screenX, e.screenY]; });
    b.addEventListener("click", (e) => {
      if (down && Math.hypot(e.screenX - down[0], e.screenY - down[1]) > 4) return;
      openId = openId === id ? null : id;
      render(latestData);
    });
    return b;
  }

  function updateModule(node, provider, d) {
    const { tank, hasShort, level, slice } = d;
    node.classList.toggle("active", openId === provider.id);
    node.classList.toggle("err", provider.status === "error");
    node.title = tipFor(provider, d);

    const tube = node.querySelector(".cq-tube");
    const liquid = node.querySelector(".cq-liquid");
    const sliceEl = node.querySelector(".cq-slice");
    liquid.style.height = `${level}%`;
    liquid.classList.toggle("filled", level > 0);
    // the 5h share sits on top of the weekly liquid, as a part of it
    sliceEl.style.height = level > 0 ? `${(slice / level) * 100}%` : "0";
    sliceEl.classList.toggle("on", slice > 0 && slice < level);
    let none = tube.querySelector(".cq-tube-none");
    if (!tank && !none) {
      none = document.createElement("span");
      none.className = "cq-tube-none";
      none.textContent = "—";
      tube.appendChild(none);
    } else if (tank && none) {
      none.remove();
    }

    node.querySelector(".cq-label").textContent = d.name;
    node.querySelector(".cq-flags").innerHTML =
      (d.isBest ? `<span class="cq-star" aria-label="Şu an en uygun">★</span>` : "") +
      (d.low ? `<span class="cq-dot low"></span>` : d.runsOut ? `<span class="cq-dot soon"></span>` : "");

    const nums = node.querySelector(".cq-nums");
    if (!tank) {
      nums.innerHTML = `<span class="cq-short dim">veri yok</span>`;
    } else if (hasShort) {
      const crit = tank.short_remaining <= 15 ? " crit" : "";
      nums.innerHTML =
        `<span title="Haftalık kalan">H %${Math.round(tank.long_remaining)}</span>` +
        `<span class="cq-short${crit}" title="5 saatlik kalan">5s %${Math.round(tank.short_remaining)}</span>`;
    } else {
      nums.innerHTML =
        `<span title="Haftalık kalan">H %${Math.round(tank.long_remaining)}</span>` +
        `<span class="cq-short dim countdown" data-reset="${tank.long_resets_at || ""}">⟳${esc(shortCountdown(tank.long_resets_at))}</span>`;
    }
  }

  // ---------------------------------------------------------------- detail card

  function renderPop(provider, d) {
    if (!provider) {
      el.pop.classList.add("hidden");
      el.pop.textContent = "";
      return;
    }
    const { tank, hasShort } = d;
    el.pop.dataset.id = provider.id;
    el.pop.classList.remove("hidden");

    const sub = [VENDORS[provider.id], tank && provider.tanks.length > 1 ? tank.label : ""]
      .filter(Boolean)
      .join(" · ");

    let stats = "";
    let notes = [];
    if (tank) {
      const longCls = tank.long_remaining <= 15 ? "crit" : "";
      const shortCls = hasShort && tank.short_remaining <= 15 ? "crit" : "accent";
      stats = `
        <div class="cq-stats">
          <div class="cq-stat">
            <div class="cq-stat-lbl">Haftalık kalan</div>
            <div class="cq-stat-val ${longCls}">%${pct(tank.long_remaining)}</div>
            <div class="cq-stat-sub">Yenilenme: <span class="cd-long" data-reset="${tank.long_resets_at || ""}">${esc(formatCountdown(tank.long_resets_at))}</span></div>
          </div>
          <div class="cq-stat">
            <div class="cq-stat-lbl">5 saatlik kalan</div>
            ${hasShort
              ? `<div class="cq-stat-val ${shortCls}">%${pct(tank.short_remaining)}</div>
                 <div class="cq-stat-sub">Yenilenme: <span class="cd-long" data-reset="${tank.short_resets_at || ""}">${esc(formatCountdown(tank.short_resets_at))}</span></div>`
              : `<div class="cq-stat-val" style="color:var(--text-dim)">—</div>
                 <div class="cq-stat-sub">Bu araçta yok</div>`}
          </div>
        </div>`;
      if (has(tank.spendable)) {
        notes.push(`<span>Bu 5 saatte harcanabilir: <strong>haftalığın %${pct(tank.spendable)}'i</strong></span>`);
      }
      const fc = (hasShort ? QT.forecastInfo(tank.short_forecast) : null) || QT.forecastInfo(tank.long_forecast);
      if (fc) notes.push(`<span class="cq-note ${fc.cls}" title="${esc(fc.tip)}">${esc(fc.text)}</span>`);
      if (d.isBest) notes.push(`<span>★ Şu an en uygun araç</span>`);
    } else {
      notes.push(`<span class="cq-note err">${esc(provider.error || "Henüz kota verisi yok")}</span>`);
    }

    el.pop.innerHTML = `
      <div class="cq-pop-head">
        <div>
          <div class="cq-pop-title">${esc(TITLES[provider.id] || provider.id)}</div>
          ${sub ? `<div class="cq-pop-sub">${esc(sub)}</div>` : ""}
        </div>
        <button class="cq-x" type="button" title="Kapat">✕</button>
      </div>
      ${stats}
      <div class="cq-pop-foot">
        <div class="cq-notes">${notes.join("")}</div>
        <button class="cq-open" type="button">Paneli aç</button>
      </div>`;
    for (const b of el.pop.querySelectorAll("button")) {
      b.addEventListener("mousedown", (e) => e.stopPropagation());
    }
    el.pop.querySelector(".cq-x").addEventListener("click", closePop);
    el.pop.querySelector(".cq-open").addEventListener("click", () => api() && api().show_panel());
  }

  function closePop() {
    if (openId === null) return;
    openId = null;
    render(latestData);
  }

  // ---------------------------------------------------------------- render

  function render(data) {
    if (!data) return;
    latestData = data;
    if (data.widget) setPass(data.widget.click_through);
    applyAnim(data.ui);

    const providers = QT.sortProviders(data.providers);
    const best = data.advice && data.advice.best;
    if (openId && !providers.some((p) => p.id === openId)) openId = null;

    const seen = new Set();
    let openProvider = null;
    let openDesc = null;
    for (const p of providers) {
      let node = modules.get(p.id);
      if (!node) {
        node = buildModule(p.id);
        modules.set(p.id, node);
      }
      const d = describe(p, best);
      updateModule(node, p, d);
      el.mods.appendChild(node); // keeps display order
      seen.add(p.id);
      if (p.id === openId) {
        openProvider = p;
        openDesc = d;
      }
    }
    for (const [id, node] of modules) {
      if (!seen.has(id)) {
        node.remove();
        modules.delete(id);
      }
    }
    let empty = el.mods.querySelector(".cq-empty");
    if (!providers.length && !empty) {
      empty = document.createElement("div");
      empty.className = "cq-empty";
      empty.textContent = "İzlenen araç yok";
      el.mods.appendChild(empty);
    } else if (providers.length && empty) {
      empty.remove();
    }

    renderPop(openProvider, openDesc);
    requestAnimationFrame(() => {
      fit();
      reportPassRect();
    });
  }

  // ---------------------------------------------------------------- window size

  let lastFit = "";
  function fit() {
    const r = el.root.getBoundingClientRect();
    const w = Math.ceil(r.width);
    const h = Math.ceil(r.height);
    const open = openId !== null;
    const key = `${w}x${h}:${open}`;
    if (key === lastFit || !api() || !api().widget_fit_content) return;
    lastFit = key;
    api().widget_fit_content(w, h, open);
  }
  if (window.ResizeObserver) new ResizeObserver(() => fit()).observe(el.root);

  // ---------------------------------------------------------------- taskbar docking
  //
  // Docked, the bar is exactly as thick as the taskbar and slides only sideways: the
  // pywebview drag region is switched off and a horizontal drag is sent to Python
  // (one call at a time, latest wins, like the tank widget's resize grips).

  let docked = false;

  window.__setDock = function (info) {
    docked = !!(info && info.docked);
    const h = info && info.height ? Math.max(28, Math.min(96, Number(info.height))) : 48;
    document.body.classList.toggle("docked", docked);
    document.body.style.setProperty("--dock-h", `${h}px`);
    if (info && /^#[0-9a-f]{6}$/i.test(info.bg || "")) document.body.style.setProperty("--dock-bg", info.bg);
    el.root.classList.toggle("pywebview-drag-region", !docked);
    el.bar.title = docked
      ? "Görev çubuğu boyunca sürükleyerek kaydır · çift tıkla paneli aç"
      : "Sürükleyerek taşı · çift tıkla paneli aç";
    lastFit = "";
    requestAnimationFrame(() => {
      fit();
      reportPassRect();
    });
  };

  let slide = null; // { id, sx, dragging }
  let slideBusy = false;
  let slideQueued = null;

  async function flushSlide() {
    if (slideBusy || !slideQueued || !api() || !api().widget_dock_drag) return;
    const q = slideQueued;
    slideQueued = null;
    slideBusy = true;
    try {
      await api().widget_dock_drag(q.dx, q.done);
    } catch (_) {
      /* next move retries */
    } finally {
      slideBusy = false;
      if (slideQueued) flushSlide();
    }
  }

  el.bar.addEventListener("pointerdown", (e) => {
    if (!docked || e.button !== 0 || e.target.closest(".cq-btn")) return;
    slide = { id: e.pointerId, sx: e.screenX, dragging: false };
  });
  el.bar.addEventListener("pointermove", (e) => {
    if (!slide || e.pointerId !== slide.id) return;
    const dx = e.screenX - slide.sx;
    if (!slide.dragging) {
      if (Math.abs(dx) < 4) return;
      slide.dragging = true;
      try {
        el.bar.setPointerCapture(e.pointerId); // keep getting moves outside the window
      } catch (_) {
        /* events still arrive while inside */
      }
    }
    slideQueued = { dx, done: false };
    flushSlide();
  });
  function endSlide(e) {
    if (!slide || e.pointerId !== slide.id) return;
    if (slide.dragging) {
      slideQueued = { dx: e.screenX - slide.sx, done: true };
      flushSlide();
    }
    slide = null;
  }
  el.bar.addEventListener("pointerup", endSlide);
  el.bar.addEventListener("pointercancel", endSlide);

  // ---------------------------------------------------------------- shimmer on/off
  //
  // The surface shimmer is the only continuous animation: it follows the same
  // "idle waves" settings as the liquid tanks (off while hidden / while not in use).

  let uiOpts = { idle: true, onlyActive: true };
  let shown = true;
  let active = document.hasFocus();

  function applyAnim(ui) {
    if (ui) uiOpts = { idle: ui.idle_waves !== false, onlyActive: ui.waves_only_active !== false };
    const on = uiOpts.idle && shown && !document.hidden && (active || !uiOpts.onlyActive);
    el.root.classList.toggle("anim", on);
  }

  QT.installVisibility();
  const setVisible = window.__setAppVisible;
  window.__setAppVisible = function (v) {
    setVisible(v);
    shown = !!v;
    applyAnim();
  };
  document.addEventListener("visibilitychange", () => applyAnim());
  const setActive = (v) => {
    if (active !== v) {
      active = v;
      applyAnim();
    }
  };
  let focused = active;
  let hovered = false;
  window.addEventListener("focus", () => { focused = true; setActive(true); });
  window.addEventListener("blur", () => {
    focused = false;
    setActive(hovered);
    closePop(); // clicked somewhere else: fold the card away
  });
  document.documentElement.addEventListener("mouseenter", () => { hovered = true; setActive(true); });
  document.documentElement.addEventListener("mouseleave", () => { hovered = false; setActive(focused); });
  document.documentElement.addEventListener("pointermove", () => { if (!hovered) { hovered = true; setActive(true); } }, { passive: true });

  // ---------------------------------------------------------------- pass-through toggle
  //
  // While clicks pass through, Python makes the widget clickable again only while the
  // cursor is over this button, so it can always be switched off.

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
  // the bar moves inside the window when the card opens/closes
  window.addEventListener("resize", () => requestAnimationFrame(reportPassRect));

  // ---------------------------------------------------------------- buttons

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
  for (const b of [el.pass, el.refresh, el.expand, el.hide]) {
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
  el.expand.addEventListener("click", () => api() && api().show_panel());
  el.hide.addEventListener("click", () => api() && api().minimize_to_tray());
  el.bar.addEventListener("dblclick", (e) => {
    if (!e.target.closest(".cq-btn, .cq-mod") && api()) api().show_panel();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePop();
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
    document.querySelectorAll(".cd-long[data-reset]").forEach((node) => {
      const epoch = Number(node.dataset.reset);
      if (epoch) node.textContent = formatCountdown(epoch);
    });
  }, 30000);

  window.__onDashboardUpdate = render;
  window.addEventListener("pywebviewready", async () => {
    try {
      if (api().widget_dock_info) window.__setDock(await api().widget_dock_info());
    } catch (_) {
      /* Python pushes it when it changes */
    }
    try {
      render(await api().get_dashboard());
    } catch (_) {
      /* next push will fill it */
    }
  });
})();
