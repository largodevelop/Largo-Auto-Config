"""Auto-update from GitHub releases (public repo): check, download, swap the exe and restart."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import version

API = f"https://api.github.com/repos/{version.UPDATE_REPO}/releases/latest"
TIMEOUT = 15
APPLY_FLAG = "--apply-update"
_DETACHED = 0x00000008
_NEW_GROUP = 0x00000200
_BREAKAWAY = 0x01000000
_SYNCHRONIZE = 0x00100000
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "LargoAutoConfig-Updater"}


def parse_version(text: str) -> tuple[int, int, int]:
    text = str(text or "").strip().lower().lstrip("v")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def is_newer(remote_tag: str, local: str | None = None) -> bool:
    return parse_version(remote_tag) > parse_version(local or version.APP_VERSION)


def check_for_update() -> dict:
    """{'available': bool, 'latest': 'v1.3.1', 'url': asset url, 'size': int} or {'error': text}."""
    try:
        request = urllib.request.Request(API, headers=_HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"available": False, "error": str(error)}
    tag = str(release.get("tag_name") or "")
    if release.get("draft") or release.get("prerelease") or not tag:
        return {"available": False}
    asset = next((a for a in release.get("assets") or [] if a.get("name") == version.UPDATE_ASSET_NAME), None)
    if asset is None or not is_newer(tag):
        return {"available": False, "latest": tag}
    return {
        "available": True,
        "latest": tag,
        "url": asset.get("browser_download_url"),
        "size": int(asset.get("size") or 0),
    }


def download_asset(url: str, dest: Path, size: int = 0, on_progress=None) -> Path:
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": _HEADERS["User-Agent"]})
    written = 0
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response, part.open("wb") as handle:
        total = size or int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(1 << 16)
            if not chunk:
                break
            handle.write(chunk)
            written += len(chunk)
            if on_progress:
                on_progress(written, total)
    if size and part.stat().st_size != size:
        part.unlink(missing_ok=True)
        raise RuntimeError("Файл обновления скачался не полностью.")
    part.replace(dest)
    return dest


def _child_env() -> dict:
    # onefile-exe, запущенный из другого onefile-exe, иначе берёт его временную папку распаковки
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _spawn(args: list[str], cwd: Path) -> subprocess.Popen:
    kwargs = {"cwd": str(cwd), "env": _child_env(), "close_fds": True}
    try:
        return subprocess.Popen(args, creationflags=_DETACHED | _NEW_GROUP | _BREAKAWAY, **kwargs)
    except OSError:
        return subprocess.Popen(args, creationflags=_DETACHED | _NEW_GROUP, **kwargs)


def staged_path() -> Path:
    exe = Path(sys.executable).resolve()
    return exe.with_name(exe.stem + ".update" + exe.suffix)


def stage_restart(staged_exe: Path) -> None:
    """Start the downloaded exe in swap mode. The caller must exit right after."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление работает только в собранном приложении.")
    current = Path(sys.executable).resolve()
    staged = Path(staged_exe).resolve()
    _spawn([str(staged), APPLY_FLAG, str(os.getpid()), str(current)], staged.parent)
    time.sleep(0.2)


def cleanup_staged() -> None:
    if not getattr(sys, "frozen", False):
        return
    exe = Path(sys.executable).resolve()
    if ".update" in exe.stem:
        return
    for name in (exe.stem + ".update" + exe.suffix, exe.name + ".bak", exe.name + ".new"):
        try:
            (exe.parent / name).unlink(missing_ok=True)
        except OSError:
            pass


def _wait_pid_exit(pid: int, timeout: float) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
    if not handle:
        return
    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class _Splash:
    def __init__(self) -> None:
        self.root = None
        try:
            import tkinter as tk
            from tkinter import ttk

            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            width, height = 340, 110
            root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+{(root.winfo_screenheight() - height) // 2}")
            root.configure(bg="#18181b")
            tk.Label(root, text="Largo Auto Config", bg="#18181b", fg="#f4f4f5", font=("Segoe UI Semibold", 12)).pack(pady=(20, 2))
            tk.Label(root, text="Устанавливаю обновление…", bg="#18181b", fg="#a1a1aa", font=("Segoe UI", 9)).pack()
            style = ttk.Style(root)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("L.Horizontal.TProgressbar", troughcolor="#2a2a2e", background="#a58cf8", bordercolor="#18181b", lightcolor="#a58cf8", darkcolor="#a58cf8")
            bar = ttk.Progressbar(root, style="L.Horizontal.TProgressbar", mode="indeterminate", length=260)
            bar.pack(pady=12)
            bar.start(18)
            root.update()
            self.root = root
        except Exception:
            self.root = None

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self.root is not None:
                try:
                    self.root.update()
                except Exception:
                    self.root = None
            time.sleep(0.03)

    def close(self) -> None:
        if self.root is not None:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None


def apply_mode(argv: list[str]) -> bool:
    """Swap mode: the downloaded exe was started with the flag. True if this process was it."""
    if APPLY_FLAG not in argv:
        return False
    index = argv.index(APPLY_FLAG)
    try:
        pid = int(argv[index + 1])
        target = Path(argv[index + 2])
    except (IndexError, ValueError):
        return True
    splash = _Splash()
    temporary = target.with_name(target.name + ".new")
    try:
        _wait_pid_exit(pid, 30)
        me = Path(sys.executable).resolve()
        for _ in range(80):
            try:
                if me != target.resolve():
                    import shutil

                    shutil.copyfile(me, temporary)
                    os.replace(temporary, target)
                break
            except OSError:
                splash.pump(0.5)
        if target.is_file():
            _spawn([str(target)], target.parent)
            splash.pump(2.5)
    finally:
        splash.close()
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass
    return True
