"""Detect current Steam account from loginusers.vdf."""

from __future__ import annotations

import re
import winreg
from dataclasses import dataclass
from pathlib import Path

STEAMID64_BASE = 76561197960265728


@dataclass(frozen=True)
class SteamAccount:
    steam_id64: str
    account_id: str
    persona_name: str
    account_name: str


def steamid64_to_account_id(steam_id64: str | int) -> str:
    return str(int(steam_id64) - STEAMID64_BASE)


def _parse_vdf_users(text: str) -> list[dict]:
    """Lightweight parse of loginusers.vdf users block."""
    users: list[dict] = []
    # Blocks look like: "7656..." { "AccountName" "x" ... }
    for m in re.finditer(
        r'"(\d{17})"\s*\{([^}]*)\}',
        text,
        flags=re.DOTALL,
    ):
        sid = m.group(1)
        body = m.group(2)
        fields = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', body))
        fields["SteamID64"] = sid
        users.append(fields)
    return users


def read_login_users(steam: Path) -> list[SteamAccount]:
    path = steam / "config" / "loginusers.vdf"
    if not path.is_file():
        # fallback older layout
        path = steam / "loginusers.vdf"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    accounts: list[SteamAccount] = []
    for u in _parse_vdf_users(text):
        sid = u.get("SteamID64", "")
        if not sid.isdigit():
            continue
        accounts.append(
            SteamAccount(
                steam_id64=sid,
                account_id=steamid64_to_account_id(sid),
                persona_name=u.get("PersonaName") or u.get("AccountName") or sid,
                account_name=u.get("AccountName") or "",
            )
        )
    return accounts


def active_account_id() -> str | None:
    """AccountID of the user logged in to the running Steam client (None if logged out)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            value, _ = winreg.QueryValueEx(key, "ActiveUser")
    except OSError:
        return None
    return str(int(value)) if value else None


def current_account(steam: Path) -> SteamAccount | None:
    """Logged-in account (registry), falling back to MostRecent in loginusers.vdf."""
    users = read_login_users(steam)
    active = active_account_id()
    if active:
        for acc in users:
            if acc.account_id == active:
                return acc
        sid = str(int(active) + STEAMID64_BASE)
        return SteamAccount(sid, active, sid, "")

    path = steam / "config" / "loginusers.vdf"
    if not path.is_file():
        path = steam / "loginusers.vdf"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return users[0] if users else None
    recent = {u.get("SteamID64") for u in _parse_vdf_users(text) if u.get("MostRecent") == "1"}
    for acc in users:
        if acc.steam_id64 in recent:
            return acc
    return users[0] if users else None


def loginusers_mtime(steam: Path) -> float:
    path = steam / "config" / "loginusers.vdf"
    if not path.is_file():
        path = steam / "loginusers.vdf"
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
