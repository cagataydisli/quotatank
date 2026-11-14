(function () {
  "use strict";

  const { PROVIDER_ORDER, VENDORS, TITLES, ICONS, esc, pct, formatCountdown, formatAge } = window.QT;
  let alwaysOnTop = false;
  let isRefreshing = false;
  let latestData = null;
  const selectedTank = QT.loadSelectedTanks(); // provider id -> long window key

  const el = {
    grid: document.getElementById("tanksGrid"),
    hookBadge: document.getElementById("hookBadge"),
    btnInstallHooks: document.getElementById("btnInstallHooks"),
    btnRefresh: document.getElementById("btnRefresh"),
    btnPin: document.getElementById("btnPin"),
    btnTray: document.getElementById("btnTray"),
    btnWidget: document.getElementById("btnWidget"),
    btnQuit: document.getElementById("btnQuit"),
    lastSync: document.getElementById("lastSync"),
    footerProviders: document.getElementById("footerProviders"),
    footerLowest: document.getElementById("footerLowest"),
    advice: document.getElementById("adviceStrip"),
    adviceText: document.getElementById("adviceText"),
    btnSettings: document.getElementById("btnSettings"),
    settingsOverlay: document.getElementById("settingsOverlay"),
    settingsBody: document.getElementById("settingsBody"),
    settingsStatus: document.getElementById("settingsStatus"),
    btnSettingsClose: document.getElementById("btnSettingsClose"),
    btnSettingsCancel: document.getElementById("btnSettingsCancel"),
    btnSettingsSave: document.getElementById("btnSettingsSave"),
  };

  // ---------------------------------------------------------------- helpers

  function elCreate(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatTokens(n) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    const num = Number(n);
    if (num >= 1e9) return (num / 1e9).toFixed(2) + "B";
    if (num >= 1e6) return (num / 1e6).toFixed(2) + "M";
    if (num >= 1e3) return (num / 1e3).toFixed(1) + "K";
    return String(num);
  }

  // ---------------------------------------------------------------- tank pieces

  function pickTank(provider) {
    return QT.pickTank(provider, selectedTank);
  }

  function badgeFor(provider, tank) {
    if (provider.status === "error") return { cls: "error", text: "Hata" };
    if (tank) {
      const low =
        tank.long_remaining <= 25 ||
        (tank.short_remaining !== null && tank.short_remaining <= 20);
      if (low) return { cls: "low", text: "Düşük Kota" };
    }
    if (provider.status === "live") return { cls: "", text: "Aktif" };
    if (provider.status === "stale") return { cls: "stale", text: "Önbellek" };
    if (provider.status === "cached") return { cls: "cached", text: "Son Oturum" };
    return { cls: "waiting", text: "Bekleniyor" };
  }

  function vesselHtml(provider, tank) {
    const ticks = [75, 50, 25]
      .map(
        (v) =>
          `<div class="tick" style="bottom:${v}%"><span class="tick-label">%${v}</span><span class="tick-line"></span></div>`
      )
      .join("");

    let liquid = "";
    let center = "";

    if (!tank) {
      const msg =
        provider.error ||
        (provider.hidden_window_count > 0
          ? "Bu araç için izlenecek haftalık kota seçilmedi. Ayarlar'dan ekleyebilirsin."
          : (provider.extras && provider.extras.hint) || "Henüz kota verisi yakalanmadı.");
      center = `<div class="vessel-center"><span class="vessel-msg">${esc(msg)}</span></div>`;
    } else {
      const level = Math.max(0, Math.min(100, tank.long_remaining));
      const hasSlice = tank.spendable !== null && tank.spendable !== undefined && level > 0;
      const slicePct = hasSlice ? Math.min(level, tank.spendable) : 0;
      const shortLabel =
        tank.short_remaining !== null && tank.short_remaining !== undefined
          ? `5s: %${pct(tank.short_remaining)}`
          : "";

      // Label sits in the middle of the bright slice if it is tall enough, otherwise
      // just above the surface (or just below it when the tank is nearly full).
      let labelHtml = "";
      if (shortLabel && level > 0) {
        if (slicePct >= 9) {
          labelHtml = `<span class="tank-label" style="bottom:${level - slicePct / 2}cqh">${esc(shortLabel)}</span>`;
        } else if (level >= 88) {
          labelHtml = `<span class="tank-label" style="bottom:${level - 7}cqh">${esc(shortLabel)}</span>`;
        } else {
          labelHtml = `<span class="tank-label" style="bottom:${level + 6}cqh">${esc(shortLabel)}</span>`;
        }
      }

      liquid = `
        <canvas class="liquid-canvas" data-tank-key="${esc(provider.id + ":" + tank.long_key)}"
                data-theme="${esc(provider.id)}" data-level="${level}" data-slice="${slicePct}"></canvas>
        ${labelHtml}`;

      if (level < 15) {
        center = `<div class="vessel-center"><span class="critical-chip">Kritik: %${pct(level)}</span></div>`;
      }
    }

    return `
      <div class="vessel-wrap">
        <div class="tank-cap-top"></div>
        <div class="tank-vessel">
          <div class="glare"></div>
          <div class="glare-right"></div>
          <div class="ticks">${ticks}</div>
          ${liquid}
          ${center}
        </div>
        <div class="tank-cap-bottom"></div>
      </div>`;
  }

  function statsHtml(tank) {
    if (!tank) return "";
    const longVal = tank.long_remaining;
    const longCls = longVal <= 15 ? "crit" : "";
    const hasShort = tank.short_remaining !== null && tank.short_remaining !== undefined;
    const shortCls = !hasShort ? "none" : tank.short_remaining <= 15 ? "crit" : "accent";

    let share;
    if (!hasShort) {
      share = `<div class="share-row"><span>5s Pencere / Haftalık Pay:</span><span class="mono learning">bu havuzda 5s limiti yok</span></div>`;
    } else if (tank.full_short_cost === null || tank.full_short_cost === undefined) {
      const p = Math.round((tank.learn_progress || 0) * 100);
      share = `<div class="share-row" title="Uygulama 5 saatlik kullanım ilerledikçe haftalığın ne kadar düştüğünü ölçerek bu oranı öğrenir."><span>5s Pencere / Haftalık Pay:</span><span class="mono learning">öğreniliyor %${p}</span></div>`;
    } else {
      const lo = tank.full_short_cost_low;
      const hi = tank.full_short_cost_high;
      const range =
        lo !== null && lo !== undefined && hi !== null && hi !== undefined
          ? ` <span class="share-range">(%${Math.round(lo)}–${Math.round(hi)} arası)</span>`
          : "";
      const how =
        `Ölçüm: 5 saatlik kota azalırken haftalık kotanın ne kadar azaldığına bakılır. ` +
        `Şu ana kadar ${tank.learned_windows || 0} ayrı 5 saatlik dönemden öğrenildi; ` +
        `yeni kullanım geldikçe aralık daralır.`;
      share = `
        <div class="share-row" title="Tam bir 5 saatlik pencere, haftalık kotanın bu kadarını tüketir.\n${esc(how)}"><span>5s Pencere / Haftalık Pay:</span><span class="mono">%${pct(Math.round(tank.full_short_cost))}${range}</span></div>
        <div class="share-row" title="Bu 5 saatlik pencerede kalan hakkın, haftalık kotada karşılığı."><span>Bu 5 saatte harcanabilir (haftalıktan):</span><span class="mono">%${pct(tank.spendable)}</span></div>`;
    }

    const fcLine = (fc) => {
      const info = QT.forecastInfo(fc);
      return info ? `<div class="stat-fc ${info.cls}" title="${esc(info.tip)}">${info.html}</div>` : "";
    };

    return `
      <div class="stat-grid">
        <div class="stat">
          <div class="stat-label">Haftalık Kalan</div>
          <div class="stat-value ${longCls}">%${pct(longVal)}</div>
          <div class="stat-sub">Yenilenme: <span class="countdown" data-reset="${tank.long_resets_at || ""}">${esc(formatCountdown(tank.long_resets_at))}</span></div>
          ${fcLine(tank.long_forecast)}
        </div>
        <div class="stat">
          <div class="stat-label">5 Saatlik Kalan</div>
          <div class="stat-value ${shortCls}">${hasShort ? "%" + pct(tank.short_remaining) : "—"}</div>
          <div class="stat-sub">${
            hasShort
              ? `Yenilenme: <span class="countdown" data-reset="${tank.short_resets_at || ""}">${esc(formatCountdown(tank.short_resets_at))}</span>`
              : "Bu havuzda yok"
          }</div>
          ${hasShort ? fcLine(tank.short_forecast) : ""}
        </div>
      </div>
      ${share}`;
  }

  function detailsHtml(provider, tank) {
    const ex = provider.extras || {};
    const parts = [];

    // Visible windows that are not part of the shown tank (e.g. other pools, extra limits).
    const shownKeys = new Set(tank ? [tank.long_key, tank.short_key] : []);
    const others = (provider.windows || []).filter((w) => !shownKeys.has(w.key));
    if (others.length) {
      parts.push(
        `<div class="section-title">Diğer Kotalar</div>` +
          others
            .map(
              (w) =>
                `<div class="extra-window"><span>${esc(w.label)}</span><span class="mono">%${pct(w.remaining_percent)} · <span class="countdown" data-reset="${w.resets_at || ""}">${esc(formatCountdown(w.resets_at))}</span></span></div>`
            )
            .join("")
      );
    }

    const kv = (items) =>
      `<div class="kv-grid">${items
        .map(([k, v]) => `<div class="kv"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`)
        .join("")}</div>`;

    if (provider.id === "codex") {
      parts.push(
        `<div class="section-title">Kullanım &amp; Krediler</div>` +
          kv([
            ["Plan", provider.plan || "—"],
            ["Reset Kredisi", ex.reset_credits_available !== undefined ? `${ex.reset_credits_available} adet` : "—"],
            ["Bugün Token", formatTokens(ex.today_tokens)],
            ["Son 7 Gün", formatTokens(ex.week_tokens)],
            ["Toplam Token", formatTokens(ex.lifetime_tokens)],
            ["Seri", `${ex.streak_days ?? 0} gün`],
          ])
      );
    } else if (provider.id === "claude") {
      parts.push(
        `<div class="section-title">Oturum İstatistikleri</div>` +
          kv([
            ["Toplam Oturum", ex.total_sessions ?? "—"],
            ["Toplam Mesaj", ex.total_messages ?? "—"],
          ])
      );
      const models = provider.models || [];
      if (models.length) {
        parts.push(
          `<div class="tag-wrap">${models
            .map(
              (m) =>
                `<span class="tag">${esc(m.name)}: ${formatTokens((m.input_tokens || 0) + (m.output_tokens || 0))} tok</span>`
            )
            .join("")}</div>`
        );
      }
    } else if (provider.id === "antigravity") {
      const models = provider.models || [];
      if (models.length) {
        parts.push(
          `<div class="section-title">Modeller (${models.length})</div><div class="tag-wrap">${models
            .map((m) => {
              const active = provider.active_model && m.name === provider.active_model;
              return `<span class="tag${active ? " active" : ""}">${esc(m.name)} · ${esc(m.pool || "Gemini")}</span>`;
            })
            .join("")}</div>`
        );
      }
    }

    if (!parts.length) return "";
    return `<details class="card-details"><summary>Ayrıntılar</summary><div class="details-body">${parts.join("")}</div></details>`;
  }

  function selectorHtml(provider, tank) {
    const tanks = provider.tanks || [];
    if (tanks.length > 1) {
      const opts = tanks
        .map(
          (t) =>
            `<option value="${esc(t.long_key)}"${tank && t.long_key === tank.long_key ? " selected" : ""}>${esc(t.label)} havuzu · haftalık %${pct(t.long_remaining)}</option>`
        )
        .join("");
      return `<div class="selector-row"><select class="pool-select" data-provider="${esc(provider.id)}" aria-label="Kota havuzu">${opts}</select></div>`;
    }
    const model = provider.active_model || provider.plan || "";
    if (!model) return "";
    return `<div class="selector-row"><div class="model-chip"><span>${esc(model)}</span><span>${esc(tank ? tank.label + " havuzu" : "")}</span></div></div>`;
  }

  function renderCard(provider) {
    const tank = pickTank(provider);
    const badge = badgeFor(provider, tank);
    const card = document.createElement("article");
    card.className = "tank-card";
    card.dataset.id = provider.id;
    card.innerHTML = `
      <div class="card-head">
        <div class="tool-id">
          <div class="tool-icon">${ICONS[provider.id] || ""}</div>
          <div>
            <h2>${esc(TITLES[provider.id] || provider.name)}</h2>
            <span class="vendor">${esc(VENDORS[provider.id] || "")} · <span class="age" data-epoch="${provider.captured_at_epoch || ""}">${esc(formatAge(provider.captured_at_epoch))}</span></span>
          </div>
        </div>
        <span class="badge ${badge.cls}" title="${esc(provider.status_label || "")}">${esc(badge.text)}</span>
      </div>
      ${selectorHtml(provider, tank)}
      ${vesselHtml(provider, tank)}
      <div class="metrics">
        ${statsHtml(tank)}
        ${provider.error && tank ? `<div class="error-note">${esc(provider.error)}</div>` : ""}
        ${detailsHtml(provider, tank)}
      </div>`;
    return card;
  }

  // ---------------------------------------------------------------- page

  function updateHooksBanner(hooks) {
    if (!hooks) return;
    const missing = [];
    if (!hooks.agy) missing.push("agy");
    if (!hooks.claude) missing.push("claude");
    if (!missing.length) {
      el.hookBadge.className = "hook-badge ok";
      el.hookBadge.textContent = "statusLine hook'ları aktif";
      el.btnInstallHooks.classList.add("hidden");
    } else {
      el.hookBadge.className = "hook-badge warn";
      el.hookBadge.textContent = `Eksik hook: ${missing.join(", ")}`;
      el.btnInstallHooks.classList.remove("hidden");
    }
  }

  function updateFooter(data) {
    const providers = data.providers || [];
    const enabled = (data.enabled_providers || providers.map((p) => p.id)).length;
    el.footerProviders.textContent = `${enabled} / 3 CLI aracı izleniyor`;

    let lowest = null;
    for (const p of providers) {
      for (const w of p.windows || []) {
        if (w.disabled) continue;
        if (!lowest || w.remaining_percent < lowest.value) {
          lowest = { value: w.remaining_percent, label: `${TITLES[p.id] || p.name} · ${w.label}` };
        }
      }
    }
    el.footerLowest.innerHTML = lowest
      ? `En kısıtlı kota: <strong>%${esc(pct(lowest.value))}</strong> — ${esc(lowest.label)}`
      : "En kısıtlı kota: —";

    el.lastSync.textContent = new Date(
      (data.generated_at_epoch || Date.now() / 1000) * 1000
    ).toLocaleTimeString("tr-TR");
  }

  function updateAdvice(data) {
    const html = QT.adviceHtml(data.advice);
    el.advice.classList.toggle("hidden", !html);
    el.adviceText.innerHTML = html;
  }

  function render(data) {
    latestData = data;
    QT.applyUiOptions(data);
    updateHooksBanner(data.hooks);
    updateFooter(data);
    updateAdvice(data);

    const providers = QT.sortProviders(data.providers);

    el.grid.textContent = "";
    el.grid.className = `tanks-grid count-${Math.min(3, providers.length)}`;
    if (!providers.length) {
      el.grid.appendChild(
        elCreate("div", "grid-empty", "Hiçbir araç izlenmiyor. Ayarlar'dan en az bir araç seç.")
      );
      return;
    }
    for (const p of providers) el.grid.appendChild(renderCard(p));
    if (window.QuotaLiquid) window.QuotaLiquid.sync(el.grid); // simulations survive re-renders
  }

  el.grid.addEventListener("change", (e) => {
    const sel = e.target.closest(".pool-select");
    if (!sel) return;
    selectedTank[sel.dataset.provider] = sel.value;
    QT.saveSelectedTanks(selectedTank);
    if (latestData) render(latestData);
  });

  function tick() {
    if (QT.isShown && !QT.isShown()) return; // nothing to update while hidden
    document.querySelectorAll(".countdown[data-reset]").forEach((node) => {
      const epoch = Number(node.dataset.reset);
      if (epoch) node.textContent = formatCountdown(epoch);
    });
    document.querySelectorAll(".age[data-epoch]").forEach((node) => {
      node.textContent = formatAge(Number(node.dataset.epoch) || null);
    });
    document.querySelectorAll("[data-when]").forEach((node) => {
      node.textContent = QT.formatWhen(Number(node.dataset.when));
    });
  }

  function setRefreshing(flag) {
    isRefreshing = flag;
    el.btnRefresh.disabled = flag;
    el.btnRefresh.classList.toggle("spinning", flag);
  }

  async function callAndRender(fn) {
    if (isRefreshing || !window.pywebview || !window.pywebview.api) return;
    setRefreshing(true);
    try {
      render(await fn(window.pywebview.api));
    } finally {
      setRefreshing(false);
    }
  }

  el.btnRefresh.addEventListener("click", () => callAndRender((api) => api.refresh_now()));
  el.btnInstallHooks.addEventListener("click", () => callAndRender((api) => api.install_cli_hooks()));

  el.btnPin.addEventListener("click", async () => {
    if (!window.pywebview?.api) return;
    alwaysOnTop = !alwaysOnTop;
    await window.pywebview.api.toggle_always_on_top(alwaysOnTop);
    el.btnPin.classList.toggle("active", alwaysOnTop);
  });
  el.btnTray.addEventListener("click", () => window.pywebview?.api?.minimize_to_tray());
  el.btnWidget.addEventListener("click", () => window.pywebview?.api?.show_widget());
  el.btnQuit.addEventListener("click", () => window.pywebview?.api?.quit_app());

  // ---------------------------------------------------------------- Settings

  let settingsDraft = null; // { catalog, config }

  function selectionSet(list, allItems) {
    // null in config means "everything"
    return new Set(list === null || list === undefined ? allItems : list);
  }

  function buildCheckboxList(items, selected, groupKey, sectionTitle, emptyText) {
    const section = elCreate("div", "settings-section");
    const head = elCreate("div", "settings-section-head");
    head.appendChild(elCreate("span", "section-title", sectionTitle));

    const quick = elCreate("div", "settings-quick");
    const btnAll = elCreate("button", "link-btn", "Tümü");
    const btnNone = elCreate("button", "link-btn", "Hiçbiri");
    btnAll.type = "button";
    btnNone.type = "button";
    quick.appendChild(btnAll);
    quick.appendChild(btnNone);
    head.appendChild(quick);
    section.appendChild(head);

    if (!items.length) {
      section.appendChild(elCreate("div", "settings-empty", emptyText));
      quick.classList.add("hidden");
      return section;
    }

    const groups = new Map();
    for (const it of items) {
      const g = it.group || "";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(it);
    }

    const boxes = [];
    for (const [g, groupItems] of groups) {
      if (g) section.appendChild(elCreate("div", "settings-group-label", `${g} havuzu`));
      const list = elCreate("div", "check-grid");
      for (const it of groupItems) {
        const label = elCreate("label", "check-item");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = it.key;
        cb.dataset.group = groupKey;
        cb.checked = selected.has(it.key);
        boxes.push(cb);
        label.appendChild(cb);
        label.appendChild(elCreate("span", "", it.label));
        list.appendChild(label);
      }
      section.appendChild(list);
    }

    btnAll.addEventListener("click", () => boxes.forEach((b) => (b.checked = true)));
    btnNone.addEventListener("click", () => boxes.forEach((b) => (b.checked = false)));
    return section;
  }

  const UI_OPTIONS = [
    {
      key: "idle_waves",
      title: "Boşta dalga animasyonu",
      desc: "Su dururken hafifçe dalgalanır. Kapalıyken su durgun kalır ve boştayken neredeyse hiç işlemci harcanmaz.",
    },
    {
      key: "slosh",
      title: "Pencere hareketiyle çalkalanma",
      desc: "Pencereyi sürükleyip salladığında su gerçek bir sıvı gibi çalkalanır, duvarlara çarpıp sıçrar.",
    },
    {
      key: "waves_only_active",
      title: "Sadece pencereyle ilgilenirken dalgalan",
      desc: "Başka bir programdayken su durgun kalır ve neredeyse hiç işlemci harcanmaz. Pencereye tıklayınca ya da fareyi üzerine getirince dalgalanma geri gelir.",
    },
    {
      key: "minimize_to_widget",
      title: "Simge durumuna küçültünce widget aç",
      desc: "Pencerenin küçült (—) tuşuna basınca panel gizlenir, sağ altta her zaman üstte duran kompakt widget açılır.",
    },
  ];

  function buildAppearanceSection(ui) {
    const block = elCreate("section", "settings-provider settings-appearance");
    const head = elCreate("div", "settings-provider-head static");
    head.appendChild(elCreate("strong", "", "Görünüm"));
    head.appendChild(elCreate("span", "settings-provider-note", "Tank animasyonları"));
    block.appendChild(head);

    const body = elCreate("div", "settings-provider-body");
    for (const opt of UI_OPTIONS) {
      const row = elCreate("label", "toggle-row");
      const text = elCreate("div", "toggle-text");
      text.appendChild(elCreate("strong", "", opt.title));
      text.appendChild(elCreate("span", "", opt.desc));
      const sw = document.createElement("input");
      sw.type = "checkbox";
      sw.className = "switch";
      sw.dataset.uiKey = opt.key;
      sw.checked = ui[opt.key] !== false;
      row.appendChild(text);
      row.appendChild(sw);
      body.appendChild(row);
    }
    block.appendChild(body);
    return block;
  }

  // Generic rows for the extra sections; every input carries data-cfg="section.key".

  function settingsBlock(title, note, cls) {
    const block = elCreate("section", `settings-provider ${cls}`);
    const head = elCreate("div", "settings-provider-head static");
    head.appendChild(elCreate("strong", "", title));
    head.appendChild(elCreate("span", "settings-provider-note", note));
    block.appendChild(head);
    const body = elCreate("div", "settings-provider-body");
    block.appendChild(body);
    return { block, body };
  }

  function rowShell(title, desc, tag = "label") {
    const row = elCreate(tag, "toggle-row");
    const text = elCreate("div", "toggle-text");
    text.appendChild(elCreate("strong", "", title));
    if (desc) text.appendChild(elCreate("span", "", desc));
    row.appendChild(text);
    return row;
  }

  function switchRow(title, desc, path, checked) {
    const row = rowShell(title, desc);
    const sw = document.createElement("input");
    sw.type = "checkbox";
    sw.className = "switch";
    sw.dataset.cfg = path;
    sw.checked = !!checked;
    row.appendChild(sw);
    return row;
  }

  function numberRow(title, desc, path, value, min, max) {
    const row = rowShell(title, desc);
    const wrap = elCreate("span", "num-wrap");
    wrap.appendChild(elCreate("span", "", "%"));
    const inp = document.createElement("input");
    inp.type = "number";
    inp.className = "num-input";
    inp.min = String(min);
    inp.max = String(max);
    inp.step = "1";
    inp.value = String(value);
    inp.dataset.cfg = path;
    wrap.appendChild(inp);
    row.appendChild(wrap);
    return row;
  }

  function rangeRow(title, desc, path, value, min, max) {
    const row = rowShell(title, desc);
    const wrap = elCreate("span", "range-wrap");
    const inp = document.createElement("input");
    inp.type = "range";
    inp.className = "range-input";
    inp.min = String(min);
    inp.max = String(max);
    inp.step = "5";
    inp.value = String(value);
    inp.dataset.cfg = path;
    const out = elCreate("span", "range-val mono", `%${value}`);
    inp.addEventListener("input", () => (out.textContent = `%${inp.value}`));
    wrap.appendChild(inp);
    wrap.appendChild(out);
    row.appendChild(wrap);
    return row;
  }

  function selectRow(title, desc, path, value, options) {
    const row = rowShell(title, desc);
    const sel = document.createElement("select");
    sel.className = "pool-select small-select";
    sel.dataset.cfg = path;
    for (const [v, label] of options) {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = label;
      o.selected = v === value;
      sel.appendChild(o);
    }
    row.appendChild(sel);
    return row;
  }

  const KEY_LABELS = { ctrl: "Ctrl", alt: "Alt", shift: "Shift", win: "Win", space: "Boşluk" };
  const prettyHotkey = (spec) =>
    String(spec || "")
      .split("+")
      .map((p) => KEY_LABELS[p] || p.toUpperCase())
      .join(" + ");

  /** Click, then press the shortcut: the field records it (layout-independent key codes). */
  function hotkeyRow(path, value, ok) {
    const row = rowShell(
      "Kısayol tuşu",
      ok === false
        ? "Bu kısayol şu an çalışmıyor: başka bir program kullanıyor olabilir. Farklı bir tane seç."
        : "Kutuya tıkla, sonra istediğin tuşlara birlikte bas. Ctrl+Alt kullanma: Türkçe klavyede AltGr ile (@, € gibi) çakışır.",
      "div"
    );
    if (ok === false) row.classList.add("warn-row");
    const inp = document.createElement("input");
    inp.type = "text";
    inp.readOnly = true;
    inp.className = "hotkey-input mono";
    inp.dataset.cfg = path;
    inp.dataset.value = value;
    inp.value = prettyHotkey(value);
    inp.addEventListener("focus", () => (inp.value = "Tuşlara bas…"));
    inp.addEventListener("blur", () => (inp.value = prettyHotkey(inp.dataset.value)));
    inp.addEventListener("keydown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") return inp.blur();
      let key = null;
      if (/^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
      else if (/^Digit[0-9]$/.test(e.code)) key = e.code.slice(5);
      else if (/^F([1-9]|1[0-2])$/.test(e.code)) key = e.code.toLowerCase();
      else if (e.code === "Space") key = "space";
      if (!key) return; // only a modifier so far
      const mods = [];
      if (e.ctrlKey) mods.push("ctrl");
      if (e.altKey) mods.push("alt");
      if (e.shiftKey) mods.push("shift");
      if (e.metaKey) mods.push("win");
      if (!mods.length) {
        inp.value = "En az bir Ctrl / Alt / Shift / Win ile";
        return;
      }
      inp.dataset.value = [...mods, key].join("+");
      inp.blur();
    });
    row.appendChild(inp);
    return row;
  }

  function buildAlertsSection(alerts) {
    const { block, body } = settingsBlock("Bildirimler", "Windows bildirimi olarak gelir", "settings-alerts");
    body.appendChild(
      switchRow(
        "Kota azalınca uyar",
        "Takip ettiğin bir kota aşağıdaki seviyelere düşünce sağ altta bildirim çıkar. Her seviye için bir kez uyarır.",
        "alerts.enabled",
        alerts.enabled
      )
    );
    body.appendChild(numberRow("İlk uyarı", "Kalan bu yüzdeye inince (tepsi simgesi sarı olur)", "alerts.threshold", alerts.threshold, 5, 90));
    body.appendChild(numberRow("Son uyarı", "Kalan bu yüzdeye inince (tepsi simgesi kırmızı olur)", "alerts.critical", alerts.critical, 1, 50));
    body.appendChild(
      switchRow(
        "Yenilenince haber ver",
        "Yarıdan fazlası kullanılmış bir kota yenilendiğinde \"tekrar kullanabilirsin\" bildirimi gelir.",
        "alerts.reset",
        alerts.reset
      )
    );
    return block;
  }

  function buildWidgetSection(widget) {
    const { block, body } = settingsBlock("Küçük Pencere (Widget)", "Sağ alttaki kompakt görünüm", "settings-widget");
    body.appendChild(rangeRow("Saydamlık", "Düşük değer = arkası daha çok görünür", "widget.opacity", widget.opacity, 30, 100));
    body.appendChild(
      switchRow(
        "Tıklamalar içinden geçsin",
        "Widget ekranda durur ama fareyle tıkladığın şey arkasındaki pencereye gider. Kapatmak için: tepsi simgesine sağ tık → \"Widget tıklamaları geçirsin\".",
        "widget.click_through",
        widget.click_through
      )
    );
    return block;
  }

  function buildSystemSection(system, autostartOn, hotkeyOk) {
    const { block, body } = settingsBlock("Sistem", "Windows ile çalışma", "settings-system");
    body.appendChild(
      switchRow("Windows açılınca başlat", "Bilgisayarı açtığında QuotaTank kendiliğinden çalışır.", "system.autostart", autostartOn)
    );
    body.appendChild(
      selectRow("Otomatik başlayınca", "Windows ile açıldığında ne görünsün", "system.start_mode", system.start_mode, [
        ["widget", "Küçük pencere"],
        ["tray", "Sadece tepsi simgesi"],
      ])
    );
    body.appendChild(
      switchRow(
        "Kısayolla widget'ı aç / kapat",
        "Hangi programda olursan ol, kısayola basınca küçük pencere açılır ya da gizlenir.",
        "system.hotkey_enabled",
        system.hotkey_enabled
      )
    );
    body.appendChild(hotkeyRow("system.hotkey", system.hotkey, system.hotkey_enabled ? hotkeyOk : null));
    return block;
  }

  function renderSettings() {
    const { catalog, config } = settingsDraft;
    el.settingsBody.textContent = "";

    el.settingsBody.appendChild(buildAlertsSection(config.alerts || {}));
    el.settingsBody.appendChild(buildWidgetSection(config.widget || {}));
    el.settingsBody.appendChild(
      buildSystemSection(config.system || {}, settingsDraft.autostart, settingsDraft.hotkey_ok)
    );
    el.settingsBody.appendChild(buildAppearanceSection(config.ui || {}));

    const byId = new Map((catalog.providers || []).map((p) => [p.id, p]));
    for (const pid of PROVIDER_ORDER) {
      const cat = byId.get(pid);
      if (!cat) continue;
      const pcfg = (config.providers || {})[pid] || { enabled: true, windows: null, models: null };

      const block = elCreate("section", "settings-provider");
      block.dataset.id = pid;

      const head = elCreate("label", "settings-provider-head");
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.className = "provider-toggle";
      toggle.checked = pcfg.enabled !== false;
      head.appendChild(toggle);
      head.appendChild(elCreate("strong", "", cat.name));
      const note = elCreate("span", "settings-provider-note", "");
      head.appendChild(note);
      block.appendChild(head);

      const inner = elCreate("div", "settings-provider-body");
      const winItems = (cat.windows || []).map((w) => ({ key: w.key, label: w.label }));
      inner.appendChild(
        buildCheckboxList(
          winItems,
          selectionSet(pcfg.windows, winItems.map((w) => w.key)),
          "windows",
          "Kota Pencereleri",
          "Bilinen kota penceresi yok."
        )
      );

      if (pid !== "codex") {
        const modelItems = (cat.models || []).map((m) => ({
          key: m.id,
          label: m.name || m.id,
          group: m.group,
        }));
        inner.appendChild(
          buildCheckboxList(
            modelItems,
            selectionSet(pcfg.models, modelItems.map((m) => m.key)),
            "models",
            "Gösterilecek Modeller",
            pid === "antigravity"
              ? "Model listesi henüz alınmadı (agy kuruluyken bir kez yenile)."
              : "Henüz model istatistiği yok."
          )
        );
      }
      block.appendChild(inner);

      const syncDisabled = () => {
        inner.classList.toggle("is-disabled", !toggle.checked);
        note.textContent = toggle.checked ? "İzleniyor" : "Kapalı · arka planda sorgulanmaz";
      };
      toggle.addEventListener("change", syncDisabled);
      syncDisabled();

      el.settingsBody.appendChild(block);
    }
  }

  function readSettingsForm() {
    const providers = {};
    el.settingsBody.querySelectorAll(".settings-provider[data-id]").forEach((block) => {
      const pid = block.dataset.id;
      const prev = (settingsDraft.config.providers || {})[pid] || {};
      const out = { enabled: block.querySelector(".provider-toggle").checked };
      for (const groupKey of ["windows", "models"]) {
        const boxes = Array.from(block.querySelectorAll(`input[data-group="${groupKey}"]`));
        if (!boxes.length) {
          out[groupKey] = prev[groupKey] === undefined ? null : prev[groupKey];
          continue;
        }
        const checked = boxes.filter((b) => b.checked).map((b) => b.value);
        // All selected -> null, so newly appearing windows/models show up automatically.
        out[groupKey] = checked.length === boxes.length ? null : checked;
      }
      providers[pid] = out;
    });
    const ui = {};
    el.settingsBody.querySelectorAll("input[data-ui-key]").forEach((sw) => {
      ui[sw.dataset.uiKey] = sw.checked;
    });
    const out = { providers, ui, alerts: {}, widget: {}, system: {} };
    el.settingsBody.querySelectorAll("[data-cfg]").forEach((node) => {
      const [section, key] = node.dataset.cfg.split(".");
      let v;
      if (node.type === "checkbox") v = node.checked;
      else if (node.type === "number" || node.type === "range") v = Number(node.value);
      else if (node.classList.contains("hotkey-input")) v = node.dataset.value;
      else v = node.value;
      out[section][key] = v;
    });
    return out;
  }

  async function openSettings() {
    if (!window.pywebview?.api) return;
    el.settingsStatus.textContent = "";
    settingsDraft = await window.pywebview.api.get_settings();
    renderSettings();
    el.settingsOverlay.classList.remove("hidden");
  }

  function closeSettings() {
    el.settingsOverlay.classList.add("hidden");
    settingsDraft = null;
  }

  el.btnSettings.addEventListener("click", openSettings);
  el.btnSettingsClose.addEventListener("click", closeSettings);
  el.btnSettingsCancel.addEventListener("click", closeSettings);
  el.settingsOverlay.addEventListener("click", (e) => {
    if (e.target === el.settingsOverlay) closeSettings();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !el.settingsOverlay.classList.contains("hidden")) closeSettings();
  });

  el.btnSettingsSave.addEventListener("click", async () => {
    if (!window.pywebview?.api || !settingsDraft) return;
    el.btnSettingsSave.disabled = true;
    el.settingsStatus.textContent = "Kaydediliyor...";
    try {
      const form = readSettingsForm();
      const data = await window.pywebview.api.save_settings(form);
      render(data);
      if (form.system.hotkey_enabled && data.hotkey_ok === false) {
        // saved, but the shortcut could not be claimed: say so and let them pick another
        settingsDraft = await window.pywebview.api.get_settings();
        renderSettings();
        el.settingsStatus.textContent = "Kaydedildi, ama bu kısayol kullanılamıyor. Başka bir tane seç.";
        return;
      }
      closeSettings();
    } catch (err) {
      el.settingsStatus.textContent = `Kaydedilemedi: ${err}`;
    } finally {
      el.btnSettingsSave.disabled = false;
    }
  });

  // ---------------------------------------------------------------- boot

  QT.installMotionGlue();
  QT.installVisibility();
  QT.installActivity();

  window.__onDashboardUpdate = function (data) {
    render(data);
  };

  window.addEventListener("pywebviewready", () =>
    callAndRender((api) => api.get_dashboard())
  );
  setInterval(tick, 1000);
})();
