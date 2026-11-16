"""Unit tests for quota normalization and snapshot parsing."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, service, tanks  # noqa: E402
from app.collectors import antigravity, claude, codex  # noqa: E402


class TestCollectors(unittest.TestCase):
    def test_codex_normalize_window(self) -> None:
        w = codex._normalize_window(
            "5 Saatlik Pencere",
            "5h",
            {"usedPercent": 22.5, "resetsAt": 1794592184, "windowDurationMins": 300},
        )
        assert w is not None
        self.assertEqual(w["remaining_percent"], 77.5)
        self.assertEqual(w["used_percent"], 22.5)
        self.assertEqual(w["resets_at"], 1794592184)

    def test_antigravity_snapshot_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            snap_file = Path(td) / "agy.json"
            models_file = Path(td) / "agy_models.json"
            models_file.write_text(
                json.dumps({"fetched_at_epoch": 9999999999, "models": []}),
                encoding="utf-8",
            )
            snap_file.write_text(
                json.dumps(
                    {
                        "captured_at_epoch": 1794580000,
                        "plan_tier": "Pro",
                        "model": {"display_name": "Gemini 4 Hydrogen (High)"},
                        "quota": {
                            "gemini-5h": {"remaining_fraction": 0.72, "reset_in_seconds": 3600},
                            "3p-weekly": {"remaining_fraction": 0.15, "reset_time": "2026-11-15T12:00:00Z"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            def fake_path(name: str) -> Path:
                return models_file if name == "agy_models" else snap_file

            with patch("app.collectors.antigravity.get_snapshot_path", side_effect=fake_path):
                res = antigravity.collect_antigravity(force_models_refresh=False)

            self.assertEqual(res["plan"], "Pro")
            self.assertEqual(len(res["windows"]), 2)
            self.assertEqual(res["windows"][0]["remaining_percent"], 72.0)
            self.assertEqual(res["windows"][1]["remaining_percent"], 15.0)

    def test_claude_snapshot_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            snap_file = Path(td) / "claude.json"
            snap_file.write_text(
                json.dumps(
                    {
                        "captured_at_epoch": 1794580000,
                        "model": {"display_name": "Claude Sonnet 5.5"},
                        "rate_limits": {
                            "five_hour": {"used_percentage": 34.0, "resets_at": 1794590000},
                            "seven_day": {"used_percentage": 12.0, "resets_at": 1794800000},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("app.collectors.claude.get_snapshot_path", return_value=snap_file), patch(
                "app.collectors.claude.fetch_live_usage", return_value=None
            ):
                res = claude.collect_claude()

            self.assertEqual(res["active_model"], "Claude Sonnet 5.5")
            self.assertEqual(len(res["windows"]), 2)
            self.assertEqual(res["windows"][0]["remaining_percent"], 66.0)
            self.assertEqual(res["windows"][1]["remaining_percent"], 88.0)


    def test_claude_live_usage_overrides_stale_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            snap_file = Path(td) / "claude.json"
            snap_file.write_text(
                json.dumps(
                    {
                        "captured_at_epoch": 1794580000,
                        "rate_limits": {
                            "five_hour": {"used_percentage": 0.0, "resets_at": 1794590000},
                            "seven_day": {"used_percentage": 29.0, "resets_at": 1795176000},
                        },
                    }
                ),
                encoding="utf-8",
            )
            api = {
                "five_hour": {"utilization": 15.0, "resets_at": "2026-11-16T14:00:00.45+00:00"},
                "seven_day": {"utilization": 32.0, "resets_at": "2026-11-20T12:00:00+00:00"},
                "seven_day_opus": None,
                "extra_usage": {"is_enabled": False, "utilization": None},
            }
            live = {"captured_at_epoch": 1794836000, "rate_limits": claude._usage_to_rate_limits(api)}
            self.assertEqual(set(live["rate_limits"]), {"five_hour", "seven_day"})
            with patch("app.collectors.claude.get_snapshot_path", return_value=snap_file), patch(
                "app.collectors.claude.fetch_live_usage", return_value=live
            ):
                res = claude.collect_claude()

            self.assertEqual(res["status_label"].endswith("(API)"), True)
            self.assertEqual(res["windows"][0]["remaining_percent"], 85.0)
            self.assertEqual(res["windows"][0]["resets_at"], 1794837600)
            self.assertEqual(res["windows"][1]["remaining_percent"], 68.0)


class TestConfigAndFiltering(unittest.TestCase):
    def test_normalize_defaults_and_garbage(self) -> None:
        cfg = config.normalize_config({"providers": {"codex": {"enabled": False, "windows": "bad"}}})
        self.assertFalse(cfg["providers"]["codex"]["enabled"])
        self.assertIsNone(cfg["providers"]["codex"]["windows"])
        self.assertTrue(cfg["providers"]["claude"]["enabled"])

    def test_ui_options_default_on_and_normalized(self) -> None:
        self.assertEqual(config.normalize_config({})["ui"], {"idle_waves": True, "slosh": True, "minimize_to_widget": True, "waves_only_active": True})
        cfg = config.normalize_config({"ui": {"idle_waves": 0, "unknown": True}})
        self.assertEqual(cfg["ui"], {"idle_waves": False, "slosh": True, "minimize_to_widget": True, "waves_only_active": True})

    def test_filter_windows_and_models(self) -> None:
        provider = {
            "windows": [{"key": "5h", "label": "a"}, {"key": "7d", "label": "b"}],
            "models": [{"id": "m1", "name": "M1"}, {"id": "m2", "name": "M2"}],
        }
        out = config.filter_provider(provider, {"enabled": True, "windows": ["7d"], "models": ["m2"]})
        self.assertEqual([w["key"] for w in out["windows"]], ["7d"])
        self.assertEqual([m["id"] for m in out["models"]], ["m2"])
        self.assertEqual(out["hidden_window_count"], 1)
        everything = config.filter_provider(provider, {"enabled": True, "windows": None, "models": None})
        self.assertEqual(len(everything["windows"]), 2)
        self.assertEqual(len(everything["models"]), 2)

    def test_disabled_provider_is_never_collected(self) -> None:
        cfg = config.normalize_config({"providers": {"codex": {"enabled": False}}})
        with patch("app.service.load_config", return_value=cfg), patch(
            "app.service.collect_codex", side_effect=AssertionError("codex must not run")
        ), patch("app.service.collect_antigravity", return_value={"id": "antigravity", "windows": [], "models": []}), patch(
            "app.service.collect_claude", return_value={"id": "claude", "windows": [], "models": []}
        ):
            data = service.collect_dashboard()
        self.assertEqual([p["id"] for p in data["providers"]], ["antigravity", "claude"])
        self.assertEqual(data["enabled_providers"], ["antigravity", "claude"])


class TestTanks(unittest.TestCase):
    def _payload(self, short_used: float, long_used: float, short_reset: int = 1794600000) -> dict:
        return {
            "claude": {
                "windows": [
                    {"key": "5h", "used_percent": short_used, "remaining_percent": 100 - short_used, "resets_at": short_reset},
                    {"key": "7d", "used_percent": long_used, "remaining_percent": 100 - long_used, "resets_at": 1795176000},
                ]
            }
        }

    def test_ratio_is_learned_and_spendable_computed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tank_ratios.json"
            with patch("app.tanks._state_path", return_value=state):
                tanks.observe(self._payload(10, 40))
                # not enough movement yet -> still learning
                t = tanks.build_tanks("claude", self._payload(12, 40.2)["claude"]["windows"], {"5h", "7d"})[0]
                self.assertIsNone(t["spendable"])
                tanks.observe(self._payload(30, 42))  # 20 pts of 5h moved 2 pts of weekly -> ratio 0.1
                wins = self._payload(30, 42)["claude"]["windows"]
                t = tanks.build_tanks("claude", wins, {"5h", "7d"})[0]
                self.assertAlmostEqual(t["ratio"], 0.1)
                self.assertEqual(t["full_short_cost"], 10.0)
                self.assertEqual(t["spendable"], 7.0)  # 70 % of 5h left * 0.1

    def test_spendable_capped_by_weekly(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tank_ratios.json"
            with patch("app.tanks._state_path", return_value=state):
                tanks.observe(self._payload(0, 90))
                tanks.observe(self._payload(20, 98))  # ratio 0.4
                t = tanks.build_tanks("claude", self._payload(20, 98)["claude"]["windows"], {"5h", "7d"})[0]
                self.assertEqual(t["spendable"], 2.0)  # 80*0.4=32 but only 2 % weekly left

    def test_hidden_weekly_has_no_tank(self) -> None:
        wins = self._payload(10, 10)["claude"]["windows"]
        self.assertEqual(tanks.build_tanks("claude", wins, {"5h"}), [])

    def test_unused_window_with_rolling_reset_is_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tank_ratios.json"
            with patch("app.tanks._state_path", return_value=state):
                # unused 5h window: reset time moves with the clock on every poll
                for i in range(50):
                    tanks.observe(self._payload(0, 50, short_reset=1794600000 + i * 900))
                saved = json.loads(state.read_text(encoding="utf-8"))["claude:5h:7d"]
                self.assertEqual(saved["windows"], {})
                # first use starts the window from the remembered 0 / weekly level
                tanks.observe(self._payload(4, 51, short_reset=1794700000))
                saved = json.loads(state.read_text(encoding="utf-8"))["claude:5h:7d"]
                (rec,) = saved["windows"].values()
                self.assertEqual((rec["start_short"], rec["start_long"]), (0.0, 50.0))

    def test_ratio_range_narrows_with_more_windows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tank_ratios.json"
            with patch("app.tanks._state_path", return_value=state):
                widths = []
                long_used = 10
                for n in range(6):
                    reset = 1794600000 + n * 20000
                    tanks.observe(self._payload(0, long_used, short_reset=reset))
                    long_used += 4
                    tanks.observe(self._payload(40, long_used, short_reset=reset))  # 40 pts -> 4 pts
                    t = tanks.build_tanks("claude", self._payload(40, long_used, reset)["claude"]["windows"], {"5h", "7d"})[0]
                    self.assertAlmostEqual(t["full_short_cost"], 10.0)
                    self.assertLessEqual(t["full_short_cost_low"], 10.0)
                    self.assertGreaterEqual(t["full_short_cost_high"], 10.0)
                    widths.append(t["full_short_cost_high"] - t["full_short_cost_low"])
                self.assertLess(widths[-1], widths[0])
                self.assertEqual(t["learned_windows"], 6)


class TestInsights(unittest.TestCase):
    def test_forecast_runs_out_before_reset(self) -> None:
        from app import insights
        now = 1_000_000.0
        # weekly window, 2 days in, 50 % used -> runs out after 4 days, reset in 5
        w = {"key": "7d", "used_percent": 50.0, "resets_at": now + 5 * 86400}
        f = insights.forecast(w, now)
        self.assertTrue(f["runs_out"])
        self.assertAlmostEqual(f["depletes_at"], now + 2 * 86400, delta=60)

    def test_forecast_enough_and_too_early(self) -> None:
        from app import insights
        now = 1_000_000.0
        ok = insights.forecast({"key": "5h", "used_percent": 10.0, "resets_at": now + 3 * 3600}, now)
        self.assertFalse(ok["runs_out"])
        self.assertIsNone(insights.forecast({"key": "5h", "used_percent": 10.0, "resets_at": now + 5 * 3600 - 60}, now))

    def test_advice_prefers_tool_with_room(self) -> None:
        from app import insights
        now = 1_000_000.0
        providers = [
            {"id": "claude", "tanks": [{"long_remaining": 80, "short_remaining": 5, "full_short_cost": 10,
                                        "long_resets_at": now + 1e5, "short_resets_at": now + 3600}]},
            {"id": "codex", "tanks": [{"long_remaining": 60, "short_remaining": 100, "full_short_cost": 10,
                                       "long_resets_at": now + 1e5, "short_resets_at": now + 3600}]},
        ]
        adv = insights.advice(providers, now)
        self.assertEqual(adv["best"], "codex")
        self.assertEqual(adv["items"][1]["limiter"], "short")


class TestNotifier(unittest.TestCase):
    def _dash(self, rem: float, resets_at: float, threshold: int = 20, critical: int = 10) -> dict:
        return {
            "alerts": {"enabled": True, "threshold": threshold, "critical": critical, "reset": True},
            "providers": [{"id": "claude", "windows": [
                {"key": "5h", "label": "5 Saatlik Oturum", "remaining_percent": rem, "resets_at": resets_at}
            ]}],
        }

    def test_warns_once_per_level_and_reports_reset(self) -> None:
        from app import notifier
        sent = []
        with tempfile.TemporaryDirectory() as td:
            with patch("app.notifier._state_path", return_value=Path(td) / "n.json"):
                n = notifier.Notifier(lambda t, m: sent.append(t))
                now = 1_000_000.0
                reset = now + 3600
                n.check(self._dash(50, reset), now)
                n.check(self._dash(18, reset), now)
                n.check(self._dash(17, reset), now)
                n.check(self._dash(8, reset), now)
                self.assertEqual(len(sent), 2)
                self.assertIn("azaldı", sent[0])
                self.assertIn("bitmek üzere", sent[1])
                # a fresh app instance remembers what was already sent
                n2 = notifier.Notifier(lambda t, m: sent.append(t))
                n2.check(self._dash(8, reset), now)
                self.assertEqual(len(sent), 2)
                # reset time passes -> "renewed"
                self.assertTrue(n2.check_resets({"enabled": True, "reset": True}, reset + 10))
                self.assertIn("yenilendi", sent[-1])
                self.assertFalse(n2.check_resets({"enabled": True, "reset": True}, reset + 20))
                self.assertEqual(len(sent), 3)


class TestHotkeyAndConfig(unittest.TestCase):
    def test_hotkey_parse(self) -> None:
        from app import hotkey
        self.assertEqual(hotkey.parse("ctrl+shift+q"), (hotkey.MOD_CONTROL | hotkey.MOD_SHIFT, ord("Q")))
        self.assertEqual(hotkey.parse("alt+f2"), (hotkey.MOD_ALT, 0x71))
        self.assertIsNone(hotkey.parse("q"))
        self.assertIsNone(hotkey.parse("ctrl+ş"))

    def test_new_sections_normalized(self) -> None:
        cfg = config.normalize_config({"alerts": {"threshold": 500, "critical": 40}, "widget": {"opacity": 5},
                                       "system": {"start_mode": "bogus", "hotkey": " Ctrl+Alt+W "}})
        self.assertEqual(cfg["alerts"]["threshold"], 90)
        self.assertEqual(cfg["alerts"]["critical"], 40)
        self.assertEqual(cfg["widget"]["opacity"], 30)
        self.assertEqual(cfg["system"]["start_mode"], "widget")
        self.assertEqual(cfg["system"]["hotkey"], "ctrl+alt+w")

    def test_widget_style_normalized(self) -> None:
        self.assertEqual(config.normalize_config({})["widget"]["style"], "tank")
        self.assertEqual(config.normalize_config({"widget": {"style": "compact"}})["widget"]["style"], "compact")
        self.assertEqual(config.normalize_config({"widget": {"style": "bogus"}})["widget"]["style"], "tank")


if __name__ == "__main__":
    unittest.main()
