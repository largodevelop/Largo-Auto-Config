"""Sync hotkeys + game settings from the master Steam account to another account."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from paths import BACKUP_DIR, PROFILES_DIR, detect_dota_root, userdata_570

# Relative to userdata/<id>/570. Hotkeys + game/video settings only:
# no guides, hero builds, chat, stats or replays.
SYNC_PATTERNS = (
    "remote/user.vcfg",
    "remote/user_convars.vcfg",
    "remote/user_keys.vcfg",
    "remote/cfg/dotakeys_personal.lst",
    "local/cfg/user_convars_*.vcfg",
    "local/cfg/user_keys_*.vcfg",
    "local/cfg/machine_convars.vcfg",
    "local/cfg/video.txt",
)
BACKUP_KEEP = 10
AUTOEXEC_STORE = PROFILES_DIR / "autoexec.cfg"


@dataclass
class SyncResult:
    changed: list[str] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _is_locked(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 32 or getattr(exc, "errno", None) in (13, 11)


def _backup(dst_base: Path, rels: list[Path], account_id: str) -> None:
    dest = BACKUP_DIR / f"{account_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    for rel in rels:
        live = dst_base / rel
        if live.is_file():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(live, dest / rel)


def _prune_backups() -> None:
    if not BACKUP_DIR.is_dir():
        return
    entries = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
    for old in entries[:-BACKUP_KEEP]:
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)


def list_synced_files(steam: Path, account_id: str) -> list[Path]:
    """Whitelisted files that exist for an account, relative to its 570 folder."""
    base = userdata_570(steam, account_id)
    found: list[Path] = []
    for pattern in SYNC_PATTERNS:
        for src in sorted(base.glob(pattern)):
            if src.is_file():
                found.append(src.relative_to(base))
    return found


def sync_from_master(steam: Path, master_id: str, target_id: str) -> SyncResult:
    """Copy changed hotkey/settings files from master into target. Raises if master is missing."""
    result = SyncResult()
    if master_id == target_id:
        return result

    src_base = userdata_570(steam, master_id)
    if not src_base.is_dir():
        raise FileNotFoundError(f"папка главного аккаунта не найдена: {src_base}")
    dst_base = userdata_570(steam, target_id)

    plan = [
        rel
        for rel in list_synced_files(steam, master_id)
        if not _same(src_base / rel, dst_base / rel)
    ]
    if not plan:
        return result

    _backup(dst_base, plan, target_id)
    for rel in plan:
        dst = dst_base / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # copyfile (not copy2): fresh mtime so Steam Cloud treats it as the newest version
            shutil.copyfile(src_base / rel, dst)
            result.changed.append(rel.as_posix())
        except OSError as exc:
            if not _is_locked(exc):
                raise
            result.locked.append(rel.as_posix())

    _prune_backups()
    return result


def has_autoexec() -> bool:
    return AUTOEXEC_STORE.is_file()


def store_autoexec(src: Path) -> None:
    """Keep our own copy so the app works even if the original file is moved or deleted."""
    if not src.is_file():
        raise FileNotFoundError(f"файл не найден: {src}")
    AUTOEXEC_STORE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, AUTOEXEC_STORE)


def clear_autoexec() -> None:
    AUTOEXEC_STORE.unlink(missing_ok=True)


def apply_autoexec(steam: Path, cfg: dict) -> bool:
    """Write the stored autoexec into Dota's cfg dir. True if the live file changed."""
    if not has_autoexec():
        return False
    root = detect_dota_root(steam, cfg)
    if not root:
        raise FileNotFoundError("Dota 2 не найдена")
    dst = root / "game" / "dota" / "cfg" / "autoexec.cfg"
    if dst.is_file() and _same(AUTOEXEC_STORE, dst):
        return False
    if dst.is_file():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dst, BACKUP_DIR / f"autoexec_{datetime.now().strftime('%Y%m%d_%H%M%S')}.cfg")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(AUTOEXEC_STORE, dst)
    return True
