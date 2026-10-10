"""Largo Auto Config — tray app that swaps Dota cfg per Steam account."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_ROOT = _APP_DIR.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from PIL import Image, ImageDraw
import pystray

import autostart
from paths import APP_NAME, ensure_dirs, load_config, save_config, detect_steam_path
from profile_manager import (
    apply_autoexec,
    clear_autoexec,
    has_autoexec,
    store_autoexec,
    sync_from_master,
)
from steam_detect import SteamAccount, current_account, read_login_users
from ui import AppWindow
from watcher import AccountWatcher, is_dota_running
from win_shell import claim_single_instance, start_activate_watcher, stop_activate_watcher


class App:
    def __init__(self) -> None:
        ensure_dirs()
        self.cfg = load_config()
        self.steam = detect_steam_path(self.cfg)
        self.auto_apply = bool(self.cfg.get("auto_apply", True))
        self.master_id = str(self.cfg.get("master_account_id") or "")
        self._account: SteamAccount | None = None
        self._pending_sync = False
        self._last_status = ("", True)
        self._tray: pystray.Icon | None = None
        self._watcher: AccountWatcher | None = None

        self.window = AppWindow(
            on_toggle_auto=self.on_toggle_auto,
            on_toggle_autostart=self.on_toggle_autostart,
            on_set_master=self.on_set_master,
            on_pick_autoexec=self.on_pick_autoexec,
            on_clear_autoexec=self.on_clear_autoexec,
            on_apply=lambda: self._sync_current("manual"),
            on_close=self.on_window_close,
            on_minimize=self.hide_to_tray,
        )
        self.window.set_auto(self.auto_apply)
        self.window.set_autostart(autostart.is_enabled())
        self.window.set_autoexec(str(self.cfg.get("autoexec_name") or "") if has_autoexec() else "")
        start_activate_watcher(lambda: self.window.after(0, self.window.show))

    def start(self) -> None:
        if not self.steam:
            self.window.set_account("Steam не найден")
            self.window.set_status("Steam не найден", ok=False)
        else:
            self._account = current_account(self.steam)
            self._show_accounts()
            self._sync_current("start")
            self._watcher = AccountWatcher(
                self.steam,
                on_change=lambda a: self.window.after(0, lambda: self._on_account_change(a)),
                on_dota_stop=lambda: self.window.after(0, self._on_dota_stop),
                interval=float(self.cfg.get("poll_seconds", 1.0)),
            )
            self._watcher.start()

        self._start_tray()
        if self.cfg.get("start_minimized"):
            self.window.hide()
        self.window.run()

    def _make_icon(self) -> Image.Image:
        for candidate in (
            _ROOT / "assets" / "brand_icon.png",
            _ROOT / "assets" / "brand.png",
            Path(getattr(sys, "_MEIPASS", _ROOT)) / "assets" / "brand_icon.png",
            Path(getattr(sys, "_MEIPASS", _ROOT)) / "assets" / "brand.png",
        ):
            if candidate.is_file():
                img = Image.open(candidate).convert("RGBA")
                img.thumbnail((64, 64), Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                canvas.paste(img, ((64 - img.width) // 2, (64 - img.height) // 2), img)
                return canvas
        # Fallback purple mark if assets missing
        img = Image.new("RGBA", (64, 64), (18, 18, 18, 255))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(28, 28, 28, 255))
        d.ellipse((22, 22, 42, 42), fill=(183, 148, 246, 255))
        return img

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Открыть", self._tray_show, default=True),
            pystray.MenuItem("Применить сейчас", self._tray_sync),
            pystray.MenuItem(
                "Автозапуск с Windows",
                self._tray_toggle_autostart,
                checked=lambda item: autostart.is_enabled(),
            ),
            pystray.MenuItem("Выход", self._tray_quit),
        )
        self._tray = pystray.Icon("largo_auto_config", self._make_icon(), APP_NAME, menu)
        threading.Thread(target=self._tray.run, name="largo-tray", daemon=True).start()

    def _tray_show(self, icon=None, item=None) -> None:
        self.window.after(0, self.window.show)

    def _tray_sync(self, icon=None, item=None) -> None:
        self.window.after(0, lambda: self._sync_current("manual"))

    def _tray_toggle_autostart(self, icon=None, item=None) -> None:
        try:
            autostart.set_enabled(not autostart.is_enabled())
            self.window.after(0, lambda: self.window.set_autostart(autostart.is_enabled()))
        except OSError as exc:
            self.window.after(0, lambda: self.window.set_status(f"автозапуск: {exc}", ok=False))

    def _tray_quit(self, icon=None, item=None) -> None:
        self.window.after(0, self.quit)

    def hide_to_tray(self) -> None:
        self.window.hide()

    def on_window_close(self) -> None:
        if self.cfg.get("close_to_tray", True):
            self.hide_to_tray()
        else:
            self.quit()

    def quit(self) -> None:
        stop_activate_watcher()
        if self._watcher:
            self._watcher.stop()
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.window.destroy()

    def on_toggle_auto(self, enabled: bool) -> None:
        self.auto_apply = enabled
        self.cfg["auto_apply"] = enabled
        save_config(self.cfg)
        if enabled:
            self._sync_current("auto")
        else:
            self.window.set_status("")

    def on_toggle_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            self.window.set_status(f"автозапуск: {exc}", ok=False)
        self.window.set_autostart(autostart.is_enabled())

    def on_set_master(self) -> None:
        acc = current_account(self.steam) if self.steam else None
        if not acc:
            self.window.set_status("аккаунт не определён", ok=False)
            return
        self.master_id = acc.account_id
        self.cfg["master_account_id"] = self.master_id
        save_config(self.cfg)
        self._account = acc
        self._show_accounts()
        self.window.set_status("главный аккаунт выбран")

    def _login(self, acc: SteamAccount) -> str:
        return acc.account_name or acc.persona_name or acc.steam_id64

    def _show_accounts(self) -> None:
        acc = self._account
        if acc:
            self.window.set_account(self._login(acc), acc.account_id)
        else:
            self.window.set_account("нет аккаунта")
        master = ""
        if self.master_id:
            known = [a for a in read_login_users(self.steam) if a.account_id == self.master_id] if self.steam else []
            master = self._login(known[0]) if known else self.master_id
        self.window.set_master(master)

    def _on_account_change(self, acc: SteamAccount | None) -> None:
        self._account = acc
        self._show_accounts()
        if acc:
            self._sync_current("account")
        else:
            self.window.set_status("нет аккаунта", ok=False)

    def _on_dota_stop(self) -> None:
        if self._pending_sync:
            self._sync_current("dota-stop")

    def _say(self, text: str, ok: bool = True) -> None:
        self._last_status = (text, ok)
        self.window.set_status(text, ok)

    def _sync_current(self, reason: str) -> None:
        """Apply autoexec and pull hotkeys/settings from the master into the logged-in account."""
        manual = reason == "manual"
        if not self.steam or (not manual and not self.auto_apply):
            return
        ae = self._apply_autoexec()
        self._last_status = ("", True)
        if self._account:
            self._sync_settings(self._account, reason)
        text, ok = self._last_status
        if ae:
            self.window.set_status(f"{ae} · {text}" if text else ae, ok)

    def _apply_autoexec(self) -> str:
        """Returns a short note if autoexec was written, '' otherwise."""
        if not has_autoexec():
            return ""
        try:
            return "autoexec применён" if apply_autoexec(self.steam, self.cfg) else ""
        except Exception as exc:
            self._say(f"autoexec: {exc}", ok=False)
            return ""

    def on_pick_autoexec(self, path: str) -> None:
        try:
            store_autoexec(Path(path))
        except OSError as exc:
            self.window.set_status(f"ошибка: {exc}", ok=False)
            return
        self.cfg["autoexec_name"] = Path(path).name
        save_config(self.cfg)
        self.window.set_autoexec(self.cfg["autoexec_name"])
        if self.steam:
            note = self._apply_autoexec()
            self.window.set_status(note or "autoexec сохранён")

    def on_clear_autoexec(self) -> None:
        clear_autoexec()
        self.cfg["autoexec_name"] = ""
        save_config(self.cfg)
        self.window.set_autoexec("")
        self.window.set_status("autoexec убран (файл в Dota не тронут)")

    def _sync_settings(self, acc: SteamAccount, reason: str) -> None:
        manual = reason == "manual"
        if not self.master_id:
            if manual or reason == "account":
                self._say("выбери главный аккаунт", ok=False)
            return
        if acc.account_id == self.master_id:
            self._pending_sync = False
            self._say("это главный аккаунт")
            return
        if is_dota_running():
            # Dota would overwrite the files on exit; wait until it is closed.
            self._pending_sync = True
            self._say("ждёт закрытия Dota", ok=False)
            return
        self._pending_sync = False
        try:
            res = sync_from_master(self.steam, self.master_id, acc.account_id)
        except Exception as exc:
            self._say(f"ошибка: {exc}", ok=False)
            return
        if res.locked:
            self._pending_sync = True
            self._say(f"файлы заняты: {len(res.locked)}", ok=False)
        elif res.changed:
            self._say(f"применено файлов: {len(res.changed)}")
        else:
            self._say("уже актуально")


def main() -> None:
    if not claim_single_instance():
        return
    App().start()


if __name__ == "__main__":
    main()
