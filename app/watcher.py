"""Poll Steam account changes and Dota 2 process start."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import psutil

from steam_detect import SteamAccount, current_account, loginusers_mtime


_DOTA_NAMES = {"dota2.exe", "dota2"}


def dota_pid() -> int | None:
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name in _DOTA_NAMES:
                pid = proc.info.get("pid")
                if isinstance(pid, int) and pid > 0:
                    return pid
    except (psutil.Error, OSError):
        pass
    return None


def _dota_running() -> bool:
    return dota_pid() is not None


def is_dota_running() -> bool:
    return _dota_running()


class AccountWatcher:
    def __init__(
        self,
        steam: Path,
        on_change: Callable[[SteamAccount | None], None],
        on_dota_start: Callable[[], None] | None = None,
        on_dota_stop: Callable[[], None] | None = None,
        interval: float = 2.0,
    ) -> None:
        self._steam = steam
        self._on_change = on_change
        self._on_dota_start = on_dota_start
        self._on_dota_stop = on_dota_stop
        self._interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_id: str | None = None
        self._pending_id: str | None = None
        self._pending_hits = 0
        self._last_mtime = 0.0
        self._dota_was_running = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_mtime = loginusers_mtime(self._steam)
        acc = current_account(self._steam)
        self._last_id = acc.steam_id64 if acc else None
        self._pending_id = None
        self._pending_hits = 0
        self._dota_was_running = _dota_running()
        self._thread = threading.Thread(target=self._loop, name="largo-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_steam(self, steam: Path) -> None:
        self._steam = steam
        self._last_id = None
        self._last_mtime = 0.0

    def set_interval(self, seconds: float) -> None:
        self._interval = max(1.0, float(seconds))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                mtime = loginusers_mtime(self._steam)
                if mtime != self._last_mtime:
                    self._last_mtime = mtime

                acc = current_account(self._steam)
                sid = acc.steam_id64 if acc else None
                if sid != self._last_id:
                    if sid == self._pending_id:
                        self._pending_hits += 1
                    else:
                        self._pending_id = sid
                        self._pending_hits = 1
                    if self._pending_hits >= 2:
                        self._last_id = sid
                        self._pending_id = None
                        self._pending_hits = 0
                        self._on_change(acc)
                else:
                    self._pending_id = None
                    self._pending_hits = 0

                running = _dota_running()
                if running and not self._dota_was_running:
                    if self._on_dota_start:
                        self._on_dota_start()
                elif not running and self._dota_was_running:
                    if self._on_dota_stop:
                        self._on_dota_stop()
                self._dota_was_running = running
            except Exception:
                pass
            self._stop.wait(self._interval)
