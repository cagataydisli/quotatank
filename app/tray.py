"""Windows system tray icon for AI Quota Monitor.

The icon is the app logo with a coloured dot that says how things stand (green fine,
amber running low, red almost out). Hovering shows one line per tool.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw

STATUS_COLORS = {
    "ok": (34, 197, 94, 255),
    "warn": (245, 158, 11, 255),
    "crit": (239, 68, 68, 255),
    "none": (100, 116, 139, 255),
}
TOOLTIP_MAX = 127   # Windows limit for tray tooltips


LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "quotatank.png"
_logo_cache: Image.Image | None = None


def _logo(size: int) -> Image.Image | None:
    global _logo_cache
    if _logo_cache is None:
        try:
            _logo_cache = Image.open(LOGO_PATH).convert("RGBA")
        except Exception:
            return None
    return _logo_cache.resize((size, size), Image.LANCZOS)


def _fallback_tank(draw: ImageDraw.ImageDraw, level: float | None, color: tuple) -> None:
    """Old drawn tank, used only if the logo file is missing."""
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(15, 23, 42, 255))
    inner = (14, 10, 50, 54)
    draw.rounded_rectangle(inner, radius=7, fill=(30, 41, 59, 255))
    if level is not None and level > 0:
        top = inner[3] - max(5, round((inner[3] - inner[1]) * min(100.0, level) / 100.0))
        draw.rounded_rectangle((inner[0], top, inner[2], inner[3]), radius=7, fill=color)
    draw.rounded_rectangle(inner, radius=7, outline=(203, 213, 225, 255), width=3)


def build_tray_image(level: float | None = None, status: str = "none") -> Image.Image:
    """App logo with a status dot in the corner (green fine, amber low, red almost out)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = STATUS_COLORS.get(status, STATUS_COLORS["none"])
    logo = _logo(size)
    if logo is None:
        _fallback_tank(draw, level, color)
        return img
    img.alpha_composite(logo)
    if status != "none":
        # big enough to read at 16 px; dark ring separates it from the logo
        r = 15
        cx, cy = size - r - 1, size - r - 1
        draw.ellipse((cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3), fill=(8, 10, 15, 255))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return img


class TrayController:
    def __init__(
        self,
        on_toggle_window: Callable[[], None],
        on_show_widget: Callable[[], None],
        on_refresh: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle_click_through: Callable[[], None] | None = None,
        is_click_through: Callable[[], bool] | None = None,
        on_toggle_compact: Callable[[], None] | None = None,
        is_compact: Callable[[], bool] | None = None,
        on_toggle_dock: Callable[[], None] | None = None,
        is_docked: Callable[[], bool] | None = None,
    ) -> None:
        self._on_toggle = on_toggle_window
        self._on_widget = on_show_widget
        self._on_refresh = on_refresh
        self._on_quit = on_quit
        self._on_click_through = on_toggle_click_through
        self._is_click_through = is_click_through or (lambda: False)
        self._on_compact = on_toggle_compact
        self._is_compact = is_compact or (lambda: False)
        self._on_dock = on_toggle_dock
        self._is_docked = is_docked or (lambda: False)
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None
        self._look: tuple | None = None
        self._title = "QuotaTank — AI CLI Kota Monitörü"

    def start(self) -> None:
        items = [
            # default=True: what a left click on the tray icon does
            pystray.MenuItem("Widget'ı Göster / Gizle", lambda _icon, _item: self._on_widget(), default=True),
            pystray.MenuItem("Büyük Pencereyi Aç", lambda _icon, _item: self._on_toggle()),
            pystray.MenuItem("Şimdi Yenile", lambda _icon, _item: self._on_refresh()),
        ]
        if self._on_click_through is not None:
            items.append(
                pystray.MenuItem(
                    "Widget tıklamaları geçirsin",
                    lambda _icon, _item: self._on_click_through(),
                    checked=lambda _item: self._is_click_through(),
                )
            )
        if self._on_compact is not None:
            items.append(
                pystray.MenuItem(
                    "Kompakt widget (yatay şerit)",
                    lambda _icon, _item: self._on_compact(),
                    checked=lambda _item: self._is_compact(),
                )
            )
        if self._on_dock is not None:
            items.append(
                pystray.MenuItem(
                    "Görev çubuğuna yerleştir",
                    lambda _icon, _item: self._on_dock(),
                    checked=lambda _item: self._is_docked(),
                )
            )
        items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Çıkış", lambda _icon, _item: self._on_quit())]
        self._icon = pystray.Icon("AIQuotaMonitor", build_tray_image(), self._title, pystray.Menu(*items))
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def update(self, level: float | None, status: str, lines: list[str]) -> None:
        """Redraw the icon only when what it shows actually changed."""
        icon = self._icon
        if icon is None:
            return
        look = status   # the image only shows the status colour
        if look != self._look:
            self._look = look
            try:
                icon.icon = build_tray_image(level, status)
            except Exception:
                pass
        title = "QuotaTank\n" + "\n".join(lines) if lines else self._title
        if len(title) > TOOLTIP_MAX:
            title = title[: TOOLTIP_MAX - 1] + "…"
        if title != self._title:
            self._title = title
            try:
                icon.title = title
            except Exception:
                pass

    def refresh_menu(self) -> None:
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        """Windows notification (toast) from the tray icon."""
        if self._icon is None:
            return
        self._icon.notify(message[:255], title[:63])

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
