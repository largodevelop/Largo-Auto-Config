"""Resolve Steam / Dota paths and app data dirs."""

from __future__ import annotations

import json
import re
import sys
import winreg
from pathlib import Path

APP_NAME = "Largo Auto Config"
# Bundled resources (assets/defaults inside the EXE)
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
# Writable data next to the EXE (or project root when running from source)
ROOT = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent

CONFIG_PATH = ROOT / "config.json"
PROFILES_DIR = ROOT / "profiles"
DEFAULTS_DIR = BUNDLE_DIR / "defaults"
if not DEFAULTS_DIR.is_dir():
    DEFAULTS_DIR = ROOT / "defaults"
BACKUP_DIR = PROFILES_DIR / "_backup"

DOTA_APP_ID = "570"
DEFAULT_DOTA_CFG_REL = Path("steamapps/common/dota 2 beta/game/dota/cfg")


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "steam_path": "",
        "dota_path": "",
        "auto_apply": True,
        "poll_seconds": 2.0,
        "start_minimized": False,
        "close_to_tray": False,
    }


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def detect_steam_path(cfg: dict | None = None) -> Path | None:
    cfg = cfg or load_config()
    manual = (cfg.get("steam_path") or "").strip()
    if manual:
        p = Path(manual)
        if p.is_dir():
            return p

    for hive, sub in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, _ = winreg.QueryValueEx(key, "SteamPath")
            p = Path(str(value).replace("/", "\\"))
            if p.is_dir():
                return p
        except OSError:
            continue

    for candidate in (
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path(r"D:\Steam"),
        Path(r"E:\Steam"),
    ):
        if candidate.is_dir():
            return candidate
    return None


def _steam_library_paths(steam: Path) -> list[Path]:
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return libs
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libs
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if p.is_dir() and p not in libs:
            libs.append(p)
    return libs


def detect_dota_root(steam: Path | None = None, cfg: dict | None = None) -> Path | None:
    """Find real 'dota 2 beta' install (may be on another Steam library drive)."""
    cfg = cfg or load_config()
    manual = (cfg.get("dota_path") or "").strip()
    if manual:
        p = Path(manual)
        if p.name.lower() == "cfg":
            # allow pointing at .../game/dota/cfg
            root = p.parents[2] if len(p.parents) >= 3 else p
            if root.is_dir():
                return root
        if (p / "game" / "dota").is_dir():
            return p
        if p.is_dir():
            return p

    steam = steam or detect_steam_path(cfg)
    if not steam:
        return None

    # Prefer library that lists app 570
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    preferred: list[Path] = []
    others = _steam_library_paths(steam)
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        blocks = re.findall(r'"\d+"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', text, flags=re.DOTALL)
        for block in blocks:
            path_m = re.search(r'"path"\s+"([^"]+)"', block)
            if not path_m:
                continue
            lib = Path(path_m.group(1).replace("\\\\", "\\"))
            if f'"{DOTA_APP_ID}"' in block and lib.is_dir():
                preferred.append(lib)

    for lib in preferred + others:
        candidate = lib / "steamapps" / "common" / "dota 2 beta"
        if (candidate / "game" / "dota").is_dir():
            return candidate
    return None


def dota_cfg_dir(steam: Path | None = None, cfg: dict | None = None) -> Path:
    root = detect_dota_root(steam, cfg)
    if root:
        return root / "game" / "dota" / "cfg"
    steam = steam or detect_steam_path(cfg) or Path(r"C:\Program Files (x86)\Steam")
    return steam / DEFAULT_DOTA_CFG_REL


def autoexec_path(steam: Path | None = None, cfg: dict | None = None) -> Path:
    return dota_cfg_dir(steam, cfg) / "autoexec.cfg"


def userdata_570(steam: Path, account_id: str) -> Path:
    return steam / "userdata" / str(account_id) / "570"


def userdata_remote(steam: Path, account_id: str) -> Path:
    return userdata_570(steam, account_id) / "remote"


def userdata_local(steam: Path, account_id: str) -> Path:
    return userdata_570(steam, account_id) / "local"


def ensure_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
