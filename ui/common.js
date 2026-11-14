/*
 * Shared bits for both windows (main panel and compact widget):
 * provider metadata, brand icons, formatting, tank selection, window-motion glue.
 */
(function () {
  "use strict";

  const PROVIDER_ORDER = ["codex", "antigravity", "claude"];
  const DISPLAY_ORDER = ["claude", "codex", "antigravity"];
  const VENDORS = { claude: "Anthropic", codex: "OpenAI", antigravity: "Google" };
  const TITLES = { claude: "Claude Code", codex: "Codex CLI", antigravity: "Antigravity CLI" };
  const SHORT_TITLES = { claude: "Claude", codex: "Codex", antigravity: "Gemini" };

  // Brand marks from Simple Icons (CC0), https://simpleicons.org
  const ICONS = {
    claude:
      '<svg viewBox="0 0 24 24" fill="currentColor" role="img" aria-label="Anthropic"><path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z"/></svg>',
    codex:
      '<svg viewBox="0 0 24 24" fill="currentColor" role="img" aria-label="OpenAI"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>',
    antigravity:
      '<svg viewBox="0 0 24 24" role="img" aria-label="Google Gemini"><defs><linearGradient id="geminiGrad" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#1c7cff"/><stop offset="0.5" stop-color="#9b72cb"/><stop offset="1" stop-color="#f1767d"/></linearGradient></defs><path fill="url(#geminiGrad)" d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81"/></svg>',
  };

  const SELECT_KEY = "quotatank.selectedTank";

  function loadSelectedTanks() {
    try {
      return JSON.parse(localStorage.getItem(SELECT_KEY) || "{}") || {};
    } catch (_) {
      return {};
    }
  }

  function saveSelectedTanks(sel) {
    try {
      localStorage.setItem(SELECT_KEY, JSON.stringify(sel));
    } catch (_) {
      /* storage unavailable: selection just won't persist */
    }
  }

  function esc(v) {
    return String(v === null || v === undefined ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pct(n) {
    const v = Number(n);
    if (!Number.isFinite(v)) return "—";
    return Number.isInteger(v) ? String(v) : v.toFixed(1);
  }

  function formatCountdown(resetEpoch) {
    if (!resetEpoch) return "bilinmiyor";
    const diff = Math.floor(resetEpoch - Date.now() / 1000);
    if (diff <= 0) return "yenilendi (yeni mesajda güncellenir)";
    const d = Math.floor(diff / 86400);
    const h = Math.floor((diff % 86400) / 3600);
    const m = Math.floor((diff % 3600) / 60);
    if (d > 0) return `${d}g ${String(h).padStart(2, "0")}s`;
    if (h > 0) return `${h}s ${String(m).padStart(2, "0")}dk`;
    return `${m}dk`;
  }

  function formatAge(epoch) {
    if (!epoch) return "veri yok";
    const diff = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
    if (diff < 10) return "az önce";
    if (diff < 60) return `${diff} sn önce`;
    if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
    return `${Math.floor(diff / 86400)} gün önce`;
  }

  const DAY_NAMES = ["Paz", "Pzt", "Sal", "Çar", "Per", "Cum", "Cmt"];

  /** Absolute time in everyday words: "45 dk sonra", "bugün 14:30", "yarın 09:00", "Per 14:30". */
  function formatWhen(epoch) {
    if (!epoch) return "";
    const now = new Date();
    const at = new Date(epoch * 1000);
    const diff = Math.floor(epoch - now.getTime() / 1000);
    if (diff <= 0) return "şimdi";
    if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} dk sonra`;
    const hm = at.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
    const dayStart = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((dayStart(at) - dayStart(now)) / 86400000);
    if (days === 0) return `bugün ${hm}`;
    if (days === 1) return `yarın ${hm}`;
    if (days < 7) return `${DAY_NAMES[at.getDay()]} ${hm}`;
    return at.toLocaleDateString("tr-TR", { day: "numeric", month: "short" }) + " " + hm;
  }

  /**
   * Depletion forecast of one quota window (computed in Python from the pace so far).
   * Returns { cls: "good" | "bad", text, tip } or null when there is nothing to say.
   */
  function forecastInfo(fc) {
    if (!fc) return null;
    const tip = "Bu dönemde şimdiye kadarki ortalama kullanım hızına göre tahmin.";
    if (fc.runs_out) {
      const soon = !fc.depletes_at || fc.depletes_at <= Date.now() / 1000 + 60;
      if (soon) return { cls: "bad", text: "Bitti", html: "Bitti", tip };
      const when = formatWhen(fc.depletes_at);
      return {
        cls: "bad",
        text: `Bu hızla ${when} biter`,
        html: `Bu hızla <span data-when="${fc.depletes_at}">${esc(when)}</span> biter`,
        tip,
      };
    }
    const text = "Bu hızla yenilenmeye kadar yeter";
    return { cls: "good", text, html: text, tip };
  }

  /** "Which tool now?" sentence for the main panel. Returns HTML or "". */
  function adviceHtml(advice) {
    if (!advice || !advice.items || advice.items.length < 2) return "";
    const name = (it) => esc(it.name + (it.pool && it.pool !== it.name ? ` (${it.pool})` : ""));
    const which = (it) => (it.limiter === "long" ? "haftalık kotası" : "5 saatlik hakkı");
    const parts = [];
    const best = advice.items.find((it) => it.id === advice.best);
    if (best) {
      parts.push(`Şu an en uygun: <strong>${name(best)}</strong> <span class="adv-num">%${Math.round(best.room)} kullanılabilir</span>`);
      for (const it of advice.items) {
        if (it === best || it.room >= 25) continue;
        const when = it.resets_at ? `, <span data-when="${it.resets_at}">${esc(formatWhen(it.resets_at))}</span> yenilenir` : "";
        parts.push(`<span class="adv-low">${name(it)}: ${which(it)} azaldı (%${Math.round(it.room)})${when}</span>`);
      }
    } else {
      const soonest = advice.items
        .filter((it) => it.resets_at)
        .sort((a, b) => a.resets_at - b.resets_at)[0];
      parts.push(`<span class="adv-low">Tüm araçların hakkı azaldı.</span>`);
      if (soonest) {
        parts.push(`İlk açılan: <strong>${name(soonest)}</strong> — <span data-when="${soonest.resets_at}">${esc(formatWhen(soonest.resets_at))}</span>`);
      }
    }
    return parts.join('<span class="adv-sep">·</span>');
  }

  /** Tank the user picked for this provider (shared by both windows via localStorage). */
  function pickTank(provider, selected) {
    const tanks = provider.tanks || [];
    if (!tanks.length) return null;
    const wanted = (selected || loadSelectedTanks())[provider.id];
    return tanks.find((t) => t.long_key === wanted) || tanks[0];
  }

  function sortProviders(list) {
    return (list || [])
      .slice()
      .sort((a, b) => DISPLAY_ORDER.indexOf(a.id) - DISPLAY_ORDER.indexOf(b.id));
  }

  // ---------------------------------------------------------------- window motion -> liquid
  //
  // Python forwards window positions while the window is dragged; we turn them into
  // velocity changes and hand those to the liquid engine (ui/liquid.js).

  function installMotionGlue() {
    let lastMove = null; // { x, y, t, vx, vy, recvAt } — velocities in px/ms

    window.__onWindowMoved = function (samples) {
      if (!window.QuotaLiquid || !Array.isArray(samples)) return;
      const recvAt = performance.now();
      for (const [x, y, t] of samples) {
        if (!lastMove || recvAt - lastMove.recvAt > 250) {
          lastMove = { x, y, t, vx: 0, vy: 0, recvAt }; // window was at rest
          continue;
        }
        const dt = Math.max(4, t - lastMove.t);
        // light smoothing: OS move events are jittery
        const vx = lastMove.vx * 0.35 + ((x - lastMove.x) / dt) * 0.65;
        const vy = lastMove.vy * 0.35 + ((y - lastMove.y) / dt) * 0.65;
        window.QuotaLiquid.impulse((vx - lastMove.vx) * 1000, (vy - lastMove.vy) * 1000);
        lastMove = { x, y, t, vx, vy, recvAt };
      }
    };

    // Once per animation frame: a drag that stopped is a sudden deceleration.
    const stopHook = function () {
      if (lastMove && (lastMove.vx || lastMove.vy) && performance.now() - lastMove.recvAt > 70) {
        window.QuotaLiquid.impulse(-lastMove.vx * 1000, -lastMove.vy * 1000);
        lastMove.vx = 0;
        lastMove.vy = 0;
      }
    };
    stopHook.pending = () => !!(lastMove && (lastMove.vx || lastMove.vy));
    if (window.QuotaLiquid) window.QuotaLiquid.setFrameHook(stopHook);
  }

  /** Stop the liquid animation entirely while this window is not visible. */
  function installVisibility() {
    let appVisible = true;
    function sync() {
      if (window.QuotaLiquid) window.QuotaLiquid.setVisible(appVisible && !document.hidden);
    }
    document.addEventListener("visibilitychange", sync);
    window.__setAppVisible = function (v) {
      appVisible = !!v;
      sync();
    };
    QT.isShown = () => appVisible && !document.hidden;
  }

  function applyUiOptions(data) {
    if (data && data.ui && window.QuotaLiquid) {
      window.QuotaLiquid.setOptions({
        idleWaves: data.ui.idle_waves,
        slosh: data.ui.slosh,
        onlyActive: data.ui.waves_only_active !== false,
      });
    }
  }

  /**
   * "Active" = the window has focus or the mouse is over it. Used to pause the idle
   * ripple while the user works in another program (the biggest idle CPU cost).
   */
  function installActivity() {
    let focused = document.hasFocus();
    let hovered = false;
    const push = () => window.QuotaLiquid && window.QuotaLiquid.setActive(focused || hovered);
    window.addEventListener("focus", () => { focused = true; push(); });
    window.addEventListener("blur", () => { focused = false; push(); });
    const root = document.documentElement;
    root.addEventListener("mouseenter", () => { hovered = true; push(); });
    root.addEventListener("mouseleave", () => { hovered = false; push(); });
    // mouseenter can be missed when the window appears under the cursor
    root.addEventListener("pointermove", () => { if (!hovered) { hovered = true; push(); } }, { passive: true });
    push();
  }

  window.QT = {
    PROVIDER_ORDER,
    DISPLAY_ORDER,
    VENDORS,
    TITLES,
    SHORT_TITLES,
    ICONS,
    loadSelectedTanks,
    saveSelectedTanks,
    esc,
    pct,
    formatCountdown,
    formatAge,
    formatWhen,
    forecastInfo,
    adviceHtml,
    pickTank,
    sortProviders,
    installMotionGlue,
    installVisibility,
    applyUiOptions,
    installActivity,
  };
})();
