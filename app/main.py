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

from paths import (
    APP_NAME,
    detect_dota_root,
    detect_steam_path,
    ensure_dirs,
    load_config,
    save_config,
)
from profile_manager import (
    GLOBAL_ID,
    apply_profile,
    copy_from_current_account,
    load_meta,
    profile_ready,
    save_from_sources,
)
from steam_detect import SteamAccount, current_account
from ui import AppWindow
from watcher import AccountWatcher, dota_pid, is_dota_running
from win_shell import claim_single_instance, start_activate_watcher, stop_activate_watcher


class _ApplyState:
    """One inject per (account, profile snapshot, Dota process)."""

    def __init__(self) -> None:
        self._key: str | None = None
        self._dota_pid: int | None = None

    def reset(self) -> None:
        self._key = None
        self._dota_pid = None

    def reset_dota(self) -> None:
        self._dota_pid = None

    def _make_key(self, acc: SteamAccount, stamp: str) -> str:
        return f"{acc.steam_id64}|{stamp}"

    def injection_done(self, acc: SteamAccount, stamp: str) -> bool:
        key = self._make_key(acc, stamp)
        if self._key != key:
            return False
        pid = dota_pid()
        if pid is not None:
            return self._dota_pid == pid
        return self._dota_pid is None

    def mark(self, acc: SteamAccount, stamp: str) -> None:
        self._key = self._make_key(acc, stamp)
        self._dota_pid = dota_pid()


class App:
    def __init__(self) -> None:
        ensure_dirs()
        self.cfg = load_config()
        self.steam = detect_steam_path(self.cfg)
        self.auto_apply = bool(self.cfg.get("auto_apply", True))
        self._apply_state = _ApplyState()
        self._seen_account: str | None = None
        self._tray: pystray.Icon | None = None
        self._watcher: AccountWatcher | None = None
        self._closing = False

        self.window = AppWindow(
            on_toggle_auto=self.on_toggle_auto,
            on_copy=self.on_copy_click,
            on_paths_changed=self.on_paths_changed,
            on_close=self.on_window_close,
            on_minimize=self.hide_to_tray,
        )
        self.window.set_auto(self.auto_apply)
        start_activate_watcher(lambda: self.window.after(0, self.window.show))

    def _profile_stamp(self) -> str:
        return str(load_meta(GLOBAL_ID).get("updated_at") or "")

    def start(self) -> None:
        if not self.steam:
            self.window.set_account("Steam не найден")
            self.window.set_status("Steam не найден", ok=False)
        else:
            acc = current_account(self.steam)
            self._refresh_account(acc, apply=False)
            if acc:
                self._seen_account = acc.steam_id64
            self._watcher = AccountWatcher(
                self.steam,
                on_change=lambda a: self.window.after(0, lambda: self._on_account_change(a)),
                on_dota_start=lambda: self.window.after(0, self._on_dota_start),
                on_dota_stop=lambda: self.window.after(0, self._on_dota_stop),
                interval=float(self.cfg.get("poll_seconds", 2.0)),
            )
            self._watcher.start()
            if not detect_dota_root(self.steam, self.cfg):
                self.window.set_status("Dota 2 не найдена", ok=False)

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
            pystray.MenuItem("Выход", self._tray_quit),
        )
        self._tray = pystray.Icon("largo_auto_config", self._make_icon(), APP_NAME, menu)
        threading.Thread(target=self._tray.run, name="largo-tray", daemon=True).start()

    def _tray_show(self, icon=None, item=None) -> None:
        self.window.after(0, self.window.show)

    def _tray_quit(self, icon=None, item=None) -> None:
        self.window.after(0, self.quit)

    def hide_to_tray(self) -> None:
        self.window.hide()

    def on_window_close(self) -> None:
        # X = full quit; minimize (—) goes to tray
        self.quit()

    def quit(self) -> None:
        self._closing = True
        stop_activate_watcher()
        if self._watcher:
            self._watcher.stop()
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass
        self.cfg["auto_apply"] = self.auto_apply
        save_config(self.cfg)
        self.window.destroy()

    def on_toggle_auto(self, enabled: bool) -> None:
        self.auto_apply = enabled
        self.cfg["auto_apply"] = enabled
        save_config(self.cfg)
        self.window.set_note("")
        self.window.set_status("")

    def on_copy_click(self) -> None:
        if not self.steam:
            self.window.set_status("Steam не найден", ok=False)
            return
        acc = current_account(self.steam)
        if not acc:
            self.window.set_status("аккаунт не определён", ok=False)
            return
        cfg_path = self.window.get_cfg_path()
        try:
            copy_from_current_account(self.steam, acc.account_id, cfg_path)
            self._apply_state.reset()
            meta = load_meta(GLOBAL_ID)
            self.window.set_paths(meta.get("cfg_source", cfg_path), meta.get("settings_source", ""))
            self.window.set_note("")
            self.window.set_status("скопировано")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 32:
                self.window.set_status("файл занят", ok=False)
            else:
                self.window.set_status(f"ошибка: {exc}", ok=False)
        except Exception as exc:
            self.window.set_status(f"ошибка: {exc}", ok=False)

    def on_paths_changed(self, cfg_path: str, settings_path: str) -> None:
        if not cfg_path and not settings_path:
            return
        try:
            save_from_sources(GLOBAL_ID, cfg_path, settings_path)
            self._apply_state.reset()
            meta = load_meta(GLOBAL_ID)
            self.window.set_paths(meta.get("cfg_source", cfg_path), meta.get("settings_source", settings_path))
            self.window.set_note("")
            if cfg_path and settings_path:
                self.window.set_status("CFG + папка сохранены")
            elif cfg_path:
                self.window.set_status("CFG сохранён")
            else:
                self.window.set_status("папка сохранена")
        except Exception as exc:
            self.window.set_status(f"ошибка: {exc}", ok=False)

    def _login(self, acc: SteamAccount) -> str:
        return acc.account_name or acc.steam_id64

    def _on_dota_stop(self) -> None:
        self._apply_state.reset_dota()

    def _on_dota_start(self) -> None:
        if not self.auto_apply or not self.steam:
            return
        acc = current_account(self.steam)
        if not acc or not profile_ready():
            return
        self._try_auto_apply(acc, reason="dota")

    def _on_account_change(self, acc: SteamAccount | None) -> None:
        new_id = acc.steam_id64 if acc else None
        if new_id and new_id == self._seen_account:
            self._refresh_account(acc, apply=False)
            return
        prev = self._seen_account
        self._seen_account = new_id
        if not acc:
            self._refresh_account(None, apply=False)
            return
        account_switched = bool(prev and new_id and prev != new_id)
        self._refresh_account(acc, apply=False)
        if account_switched and self.auto_apply and profile_ready():
            self._try_auto_apply(acc, reason="account")

    def _try_auto_apply(self, acc: SteamAccount, reason: str) -> None:
        stamp = self._profile_stamp()
        if not stamp and not profile_ready():
            return
        if self._apply_state.injection_done(acc, stamp):
            return
        if reason == "dota" and not is_dota_running():
            return
        self._apply(acc, warn_if_dota=True)
        self._apply_state.mark(acc, stamp)

    def _refresh_account(self, acc: SteamAccount | None, apply: bool) -> None:
        if not acc:
            self.window.set_account("нет аккаунта")
            self.window.set_paths()
            self.window.set_status("нет аккаунта", ok=False)
            return

        self.window.set_account(self._login(acc), acc.account_id)
        meta = load_meta(GLOBAL_ID)
        self.window.set_paths(meta.get("cfg_source", ""), meta.get("settings_source", ""))

        if apply:
            self._try_auto_apply(acc, reason="manual")
        else:
            self.window.set_status("")

    def _apply(self, acc: SteamAccount, warn_if_dota: bool = False) -> None:
        if not self.steam:
            return
        try:
            msg = apply_profile(self.steam, acc.account_id)
            ok = msg != "нет сохранённого профиля"
            dota_on = is_dota_running()
            if ok and (warn_if_dota or dota_on) and dota_on:
                self.window.warn_relaunch_dota()
            else:
                self.window.set_note("")
                self.window.set_status(msg, ok=ok)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 32 or is_dota_running():
                self.window.warn_relaunch_dota()
            else:
                self.window.set_status(f"ошибка: {exc}", ok=False)
        except Exception as exc:
            text = str(exc)
            if "WinError 32" in text:
                self.window.warn_relaunch_dota()
            else:
                self.window.set_status(f"ошибка: {exc}", ok=False)


def main() -> None:
    if not claim_single_instance():
        return
    App().start()


if __name__ == "__main__":
    main()
