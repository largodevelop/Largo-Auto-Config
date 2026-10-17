"""Largo Auto Config — tray app that swaps Dota cfg per Steam account."""

from __future__ import annotations

import os
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
import grid_manager
import updater
import version
from paths import APP_NAME, ensure_dirs, load_config, save_config, detect_steam_path
from launch_options import apply_launch_options
from profile_manager import (
    apply_autoexec,
    clear_autoexec,
    has_autoexec,
    store_autoexec,
    sync_from_master,
)
from steam_detect import SteamAccount, current_account, read_login_users
from ui import AppWindow
from watcher import AccountWatcher, is_dota_running, is_steam_running
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
            on_launch_options=self.on_launch_options,
            on_clear_autoexec=self.on_clear_autoexec,
            on_apply=lambda: self._sync_current("manual"),
            on_close=self.on_window_close,
            on_minimize=self.hide_to_tray,
            on_update=self.install_update,
            on_pick_grid=self.on_pick_grid,
            on_add_grid=self.on_add_grid,
            on_delete_grid=self.on_delete_grid,
            on_launch_dota=self.on_launch_dota,
        )
        self._update_info: dict | None = None
        self._updating = False
        self.window.set_auto(self.auto_apply)
        self.window.set_autostart(autostart.is_enabled())
        self.window.set_autoexec(str(self.cfg.get("autoexec_name") or "") if has_autoexec() else "")
        self.window.set_launch_options(str(self.cfg.get("launch_options") or ""))
        self._show_grids()
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
        threading.Thread(target=self._update_loop, name="largo-update", daemon=True).start()
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
                lambda item: f"Обновить до {(self._update_info or {}).get('latest', '')}",
                lambda icon, item: self.window.after(0, self.install_update),
                visible=lambda item: bool(self._update_info),
            ),
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

    # ---- auto-update ------------------------------------------------------

    def _update_loop(self) -> None:
        import time

        self.window.after(0, lambda: self.window.set_idle("Проверяю обновления…"))
        time.sleep(5)
        while True:
            try:
                info = updater.check_for_update()
                if info.get("error"):
                    self.window.after(0, lambda: self.window.set_idle("Не удалось проверить обновления"))
                elif info.get("available"):
                    self.window.after(0, lambda: self.window.set_idle(f"Доступно обновление {info.get('latest')}"))
                else:
                    self.window.after(0, lambda: self.window.set_idle("Последняя версия установлена, обновления не найдены"))
                if info.get("available"):
                    self._update_info = info
                    self.window.after(0, lambda: self.window.set_update(info.get("latest")))
                elif not self._updating:
                    self._update_info = None
                    self.window.after(0, lambda: self.window.set_update(None))
            except Exception:
                pass
            time.sleep(5 * 60)

    def install_update(self) -> None:
        info = self._update_info
        if not info or self._updating:
            return
        if not getattr(sys, "frozen", False):
            self._say("автообновление работает в собранном приложении", ok=False)
            return
        self._updating = True
        self._say(f"скачиваю {info.get('latest')}…")

        def work() -> None:
            try:
                path = updater.download_asset(
                    info["url"],
                    updater.staged_path(),
                    int(info.get("size") or 0),
                    on_progress=lambda done, total: self.window.after(
                        0, lambda: self._say(f"скачиваю {info.get('latest')}: {int(done * 100 / total) if total else 0}%")
                    ),
                )
                updater.stage_restart(path)
            except Exception as exc:
                self._updating = False
                self.window.after(0, lambda: self._say(f"обновление не удалось: {exc}", ok=False))
                return
            self.window.after(0, self.quit)

        threading.Thread(target=work, name="largo-install-update", daemon=True).start()

    def quit(self) -> None:
        self._harvest_grid(self._account)
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
        self._harvest_grid(self._account)  # take the grid edits from the account we are leaving
        self._account = acc
        self._show_accounts()
        if acc:
            self._sync_current("account")
        else:
            self.window.set_status("нет аккаунта", ok=False)

    def _on_dota_stop(self) -> None:
        self._harvest_grid(self._account)
        if self._pending_sync:
            self._sync_current("dota-stop")

    def _say(self, text: str, ok: bool = True) -> None:
        self._last_status = (text, ok)
        self.window.set_status(text, ok)

    def _sync_current(self, reason: str) -> None:
        """Apply autoexec + launch options and pull hotkeys/settings from the master account."""
        manual = reason == "manual"
        if not self.steam or (not manual and not self.auto_apply):
            return
        notes: list[str] = []
        errors: list[str] = []
        for note, err in (self._apply_autoexec(), self._apply_launch_options(), self._apply_grid()):
            if note:
                notes.append(note)
            if err:
                errors.append(err)
        self._last_status = ("", True)
        if self._account:
            self._sync_settings(self._account, reason)
        text, ok = self._last_status
        parts = errors + notes + ([text] if text else [])
        if parts:
            self.window.set_status(" · ".join(parts), ok and not errors)

    # ---- hero grids --------------------------------------------------------

    def _grid_name(self) -> str:
        name = str(self.cfg.get("grid_name") or "")
        return name if name and grid_manager.load_grid(name) else ""

    def _show_grids(self) -> None:
        self.window.set_grids(grid_manager.list_grids(), self._grid_name())

    def _harvest_grid(self, acc: SteamAccount | None) -> None:
        """Save the heroes the player moved in the grid on this account into our copy."""
        name = self._grid_name()
        if not (name and acc and self.steam) or is_dota_running():
            return
        applied = self.cfg.setdefault("grid_applied", {})
        try:
            fresh = grid_manager.harvest_grid(self.steam, acc.account_id, name, applied.get(acc.account_id))
        except Exception:
            return
        if fresh:
            applied[acc.account_id] = fresh
            save_config(self.cfg)

    def _apply_grid(self) -> tuple[str, str]:
        """Put the chosen grid into the current account. Returns (note, error)."""
        name = self._grid_name()
        if not (name and self._account and self.steam):
            return "", ""
        if is_dota_running():
            self._pending_sync = True
            return "", ""
        applied = self.cfg.setdefault("grid_applied", {})
        try:
            fresh = grid_manager.apply_grid(self.steam, self._account.account_id, name)
        except Exception as exc:
            return "", f"сетка: {exc}"
        if fresh and applied.get(self._account.account_id) != fresh:
            applied[self._account.account_id] = fresh
            save_config(self.cfg)
            return f"сетка «{name}» применена", ""
        return "", ""

    def on_pick_grid(self, name: str) -> None:
        self.cfg["grid_name"] = name
        save_config(self.cfg)
        self._show_grids()
        if not name:
            self.window.set_status("сетка не меняется (в играх остаётся как есть)")
            return
        note, err = self._apply_grid()
        if is_dota_running():
            self.window.set_status(f"сетка «{name}» применится после закрытия Dota", ok=False)
        else:
            self.window.set_status(err or note or f"сетка «{name}» выбрана", ok=not err)

    def on_add_grid(self, path: str) -> None:
        try:
            names = grid_manager.import_file(Path(path))
        except (OSError, ValueError) as exc:
            self.window.set_status(f"сетка: {exc}", ok=False)
            return
        # a re-imported grid is a fresh copy: accounts must take it again
        for key in [k for k in (self.cfg.get("grid_applied") or {})]:
            self.cfg["grid_applied"].pop(key, None)
        self.cfg["grid_name"] = names[0]
        save_config(self.cfg)
        self._show_grids()
        note, err = self._apply_grid()
        extra = f" (+{len(names) - 1} в списке)" if len(names) > 1 else ""
        if is_dota_running():
            self.window.set_status(f"сетка «{names[0]}» добавлена, применится после закрытия Dota{extra}")
        else:
            self.window.set_status(err or f"сетка «{names[0]}» добавлена и выбрана{extra}", ok=not err)

    def on_launch_dota(self) -> None:
        if is_dota_running():
            self.window.set_status("Dota уже запущена", ok=False)
            return
        self._sync_current("manual")
        try:
            os.startfile("steam://rungameid/570")
        except OSError as exc:
            self.window.set_status(f"не удалось запустить Dota: {exc}", ok=False)
            return
        self.window.set_status("запускаю Dota 2…")

    def on_delete_grid(self, name: str) -> None:
        bundled = grid_manager.is_bundled(name)
        grid_manager.delete_grid(name)
        for path in (grid_manager.GRIDS_DIR / "previews").glob("*.jpg") if (grid_manager.GRIDS_DIR / "previews").is_dir() else []:
            path.unlink(missing_ok=True)
        if self.cfg.get("grid_name") == name and not bundled:
            self.cfg["grid_name"] = ""
        if bundled:
            (self.cfg.get("grid_applied") or {}).clear()  # accounts take the original again
        save_config(self.cfg)
        self._show_grids()
        if bundled:
            self._apply_grid()
            self.window.set_status(f"сетка «{name}» возвращена к исходной")
        else:
            self.window.set_status(f"сетка «{name}» убрана из списка (в играх не тронута)")

    def _apply_autoexec(self) -> tuple[str, str]:
        """Returns (note, error); note is set only if the live file was rewritten."""
        if not has_autoexec():
            return "", ""
        try:
            return ("autoexec применён" if apply_autoexec(self.steam, self.cfg) else ""), ""
        except Exception as exc:
            return "", f"autoexec: {exc}"

    def _apply_launch_options(self) -> tuple[str, str]:
        """Write Dota launch options into every account's localconfig.vdf Steam isn't holding."""
        value = str(self.cfg.get("launch_options") or "").strip()
        if not value or not self.steam:
            return "", ""
        try:
            active = None
            if is_steam_running():
                acc = current_account(self.steam)
                active = acc.account_id if acc else None
            ids = [a.account_id for a in read_login_users(self.steam)]
            changed, errors = apply_launch_options(self.steam, value, ids, skip=active)
        except Exception as exc:
            return "", f"параметры запуска: {exc}"
        if errors:
            return "", f"параметры запуска: {errors[0]}"
        return (f"параметры запуска: {changed} акк." if changed else ""), ""

    def on_launch_options(self, text: str) -> None:
        text = text.strip()
        if text == str(self.cfg.get("launch_options") or ""):
            return
        self.cfg["launch_options"] = text
        save_config(self.cfg)
        if not text:
            self.window.set_status("параметры запуска очищены (в Steam не тронуты)")
            return
        note, err = self._apply_launch_options()
        self.window.set_status(err or note or "параметры запуска сохранены", ok=not err)

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
            self._say("")  # the status line then shows the update-check result


def main() -> None:
    if updater.apply_mode(sys.argv):
        return
    updater.cleanup_staged()
    if not claim_single_instance():
        return
    App().start()


if __name__ == "__main__":
    main()
