"""Set Dota 2 launch options in Steam's per-account localconfig.vdf.

Steam keeps the logged-in user's localconfig.vdf in memory and rewrites it on exit,
so callers must skip the active account while Steam is running.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from paths import PROFILES_DIR

DOTA_APP_PATH = ["userlocalconfigstore", "software", "valve", "steam", "apps", "570"]
APPS_PATH = DOTA_APP_PATH[:-1]
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
BACKUP_DIR = PROFILES_DIR / "_localconfig_backup"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), value)


@dataclass
class _Layout:
    value_span: tuple[int, int] | None = None  # span of current LaunchOptions value (inside quotes)
    app_close: int | None = None  # position of "}" closing the 570 block
    apps_close: int | None = None  # position of "}" closing the apps block


def _scan(text: str) -> _Layout:
    lay = _Layout()
    stack: list[str] = []
    pending: str | None = None
    for m in _TOKEN.finditer(text):
        if m.group(1) is not None:
            if pending is None:
                pending = m.group(1)
                continue
            if stack == DOTA_APP_PATH and pending.lower() == "launchoptions":
                lay.value_span = m.span(1)
            pending = None
        elif m.group(2) == "{":
            stack.append((pending or "").lower())
            pending = None
        else:
            if stack == DOTA_APP_PATH:
                lay.app_close = m.start()
            elif stack == APPS_PATH:
                lay.apps_close = m.start()
            if stack:
                stack.pop()
            pending = None
    return lay


def _line_indent(text: str, pos: int) -> tuple[int, str]:
    start = text.rfind("\n", 0, pos) + 1
    return start, text[start:pos]


def build_text(text: str, value: str) -> str | None:
    """Return text with LaunchOptions set to value, or None if the file has no apps block."""
    lay = _scan(text)
    esc = _escape(value)
    if lay.value_span:
        a, b = lay.value_span
        return text[:a] + esc + text[b:]
    if lay.app_close is not None:
        start, indent = _line_indent(text, lay.app_close)
        if indent.strip():
            return None
        return text[:start] + f'{indent}\t"LaunchOptions"\t\t"{esc}"\n' + text[start:]
    if lay.apps_close is not None:
        start, indent = _line_indent(text, lay.apps_close)
        if indent.strip():
            return None
        block = (
            f'{indent}\t"570"\n{indent}\t{{\n'
            f'{indent}\t\t"LaunchOptions"\t\t"{esc}"\n{indent}\t}}\n'
        )
        return text[:start] + block + text[start:]
    return None


def current_value(text: str) -> str | None:
    lay = _scan(text)
    return _unescape(text[lay.value_span[0] : lay.value_span[1]]) if lay.value_span else None


def localconfig_path(steam: Path, account_id: str) -> Path:
    return steam / "userdata" / str(account_id) / "config" / "localconfig.vdf"


def set_launch_options(path: Path, value: str, account_id: str = "") -> bool:
    """Write value into one localconfig.vdf. True if the file changed."""
    if not path.is_file():
        return False
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    if current_value(text) == value:
        return False
    new = build_text(text, value)
    if new is None or new == text:
        return False
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{account_id or path.parent.parent.name}.vdf"
    if not backup.exists():  # keep the untouched original once
        shutil.copyfile(path, backup)
    tmp = path.with_suffix(".vdf.largo-tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    os.replace(tmp, path)
    return True


def apply_launch_options(
    steam: Path, value: str, account_ids: list[str], skip: str | None = None
) -> tuple[int, list[str]]:
    """Apply to every given account except `skip`. Returns (changed count, error messages)."""
    changed = 0
    errors: list[str] = []
    for acc_id in account_ids:
        if skip and acc_id == skip:
            continue
        try:
            if set_launch_options(localconfig_path(steam, acc_id), value, acc_id):
                changed += 1
        except OSError as exc:
            errors.append(f"{acc_id}: {exc}")
    return changed, errors
