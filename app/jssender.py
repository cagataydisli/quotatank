"""One background worker for every Python -> JS call (window.evaluate_js).

Why: pywebview's evaluate_js blocks the caller until the page answers, with no
timeout. Called on the UI thread it deadlocks (the answer is delivered on that same
thread); called from the refresh loop, a stuck page would stall data collection.
Funnelling the calls through here means:

* callers never block (fire-and-forget),
* only the newest script per (window, channel) is kept — a burst of dashboard
  updates collapses into one render,
* if a call hangs, a watchdog starts a fresh worker so later updates still go out.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

STUCK_AFTER_SEC = 10.0


class JsSender:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._pending: "OrderedDict[tuple[str, str], tuple[Any, str]]" = OrderedDict()
        self._stopped = False
        self._generation = 0
        self._busy_since: float | None = None
        self._start_worker()

    def _start_worker(self) -> None:
        self._generation += 1
        self._busy_since = None
        threading.Thread(target=self._run, args=(self._generation,), daemon=True).start()

    def send(self, window: Any, channel: str, script: str) -> None:
        if window is None:
            return
        with self._cv:
            if self._stopped:
                return
            key = (str(getattr(window, "uid", id(window))), channel)
            self._pending.pop(key, None)          # newest wins, goes to the back
            self._pending[key] = (window, script)
            if self._busy_since is not None and time.monotonic() - self._busy_since > STUCK_AFTER_SEC:
                self._start_worker()              # abandon the hung worker
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._pending.clear()
            self._cv.notify_all()

    def _run(self, generation: int) -> None:
        while True:
            with self._cv:
                while not self._pending and not self._stopped and generation == self._generation:
                    self._cv.wait()
                if self._stopped or generation != self._generation:
                    return
                _key, (window, script) = self._pending.popitem(last=False)
                self._busy_since = time.monotonic()
            try:
                window.evaluate_js(script)
            except Exception:
                pass
            with self._cv:
                if generation == self._generation:
                    self._busy_since = None
