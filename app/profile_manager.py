"""Save / apply Dota profiles (global template applied to any Steam account)."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from paths import (
    BACKUP_DIR,
    PROFILES_DIR,
    autoexec_path,
    dota_cfg_dir,
    userdata_570,
    userdata_local,
    userdata_remote,
)

GLOBAL_ID = "_global"
LARGO_AUTOEXEC_MARK = "// Largo Auto Config - do not remove"
LARGO_INLINE_BEGIN = "// Largo Auto Config INLINE BEGIN"
LARGO_INLINE_END = "// Largo Auto Config INLINE END"
EXEC_RE = re.compile(r"^\s*exec\s+(\S+)", re.IGNORECASE)


def profile_dir(account_id: str) -> Path:
    return PROFILES_DIR / str(account_id)


def meta_path(account_id: str) -> Path:
    return profile_dir(account_id) / "meta.json"


def load_meta(account_id: str = GLOBAL_ID) -> dict:
    path = meta_path(account_id)
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_meta(account_id: str, meta: dict) -> None:
    p = profile_dir(account_id)
    p.mkdir(parents=True, exist_ok=True)
    meta_path(account_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_profile(account_id: str = GLOBAL_ID) -> Path:
    p = profile_dir(account_id)
    p.mkdir(parents=True, exist_ok=True)
    (p / "remote").mkdir(exist_ok=True)
    (p / "local").mkdir(exist_ok=True)
    (p / "cfg").mkdir(exist_ok=True)
    return p


def _wipe_dir(path: Path) -> None:
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError:
            pass
    path.mkdir(parents=True, exist_ok=True)


def _replace_snapshot_dirs(profile_id: str) -> Path:
    """Clear saved remote/local so the next copy is a full snapshot."""
    p = ensure_profile(profile_id)
    _wipe_dir(p / "remote")
    _wipe_dir(p / "local")
    return p


def profile_cfg_file(account_id: str = GLOBAL_ID) -> Path | None:
    meta = load_meta(account_id)
    name = (meta.get("cfg_name") or "").strip()
    p = ensure_profile(account_id)
    if name:
        candidate = p / "cfg" / name
        if candidate.is_file():
            return candidate
    cfg_dir = p / "cfg"
    if cfg_dir.is_dir():
        files = sorted(cfg_dir.glob("*.cfg"))
        if files:
            return files[0]
    legacy = p / "autoexec.cfg"
    if legacy.is_file():
        return legacy
    return None


def profile_ready(account_id: str = GLOBAL_ID) -> bool:
    meta = load_meta(account_id)
    if meta.get("saved"):
        return True
    if profile_cfg_file(account_id) is not None:
        return True
    p = profile_dir(account_id)
    remote = p / "remote"
    local = p / "local"
    return (remote.is_dir() and any(remote.iterdir())) or (local.is_dir() and any(local.iterdir()))


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_file_safe(src, dst)
        return
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except OSError:
            pass
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        _copy_file_safe(item, target)


def _copy_file_safe(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        # WinError 32: file locked by Dota/Steam — skip, caller may warn to relaunch
        winerr = getattr(exc, "winerror", None)
        if winerr == 32 or getattr(exc, "errno", None) in (13, 11):
            return
        raise


def _ingest_settings_folder(src: Path, profile_id: str) -> None:
    """Accept remote/, local/, or full 570/ folder into the profile."""
    p = ensure_profile(profile_id)
    name = src.name.lower()

    if name == "570":
        if (src / "remote").exists():
            _copy_tree(src / "remote", p / "remote")
        if (src / "local").exists():
            _copy_tree(src / "local", p / "local")
        return
    if name == "remote":
        _copy_tree(src, p / "remote")
        return
    if name == "local":
        _copy_tree(src, p / "local")
        return
    # Unknown folder name: if it looks like 570 contents
    if (src / "remote").is_dir() or (src / "local").is_dir():
        if (src / "remote").exists():
            _copy_tree(src / "remote", p / "remote")
        if (src / "local").exists():
            _copy_tree(src / "local", p / "local")
        return
    # Fallback: treat as remote-like dump
    _copy_tree(src, p / "remote")


def _safe_cfg_name(src: Path) -> str:
    name = src.name
    if not name.lower().endswith(".cfg"):
        name = f"{src.stem}.cfg"
    stem = Path(name).stem
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_") or "largo"
    return f"{stem}.cfg"


def _exec_stem(cfg_filename: str) -> str:
    return Path(cfg_filename).stem


def _store_user_cfg(profile_id: str, src: Path) -> tuple[str, Path]:
    p = ensure_profile(profile_id)
    cfg_name = _safe_cfg_name(src)
    dest = p / "cfg" / cfg_name
    for old in (p / "cfg").glob("*.cfg"):
        try:
            old.unlink()
        except OSError:
            pass
    shutil.copy2(src, dest)
    return cfg_name, dest


def build_autoexec_text(cfg_filename: str, cfg_body: str = "", existing: str = "") -> str:
    """Write exec + inline CFG body so it runs even if nested exec fails."""
    stem = _exec_stem(cfg_filename)
    lines_out: list[str] = [
        LARGO_AUTOEXEC_MARK,
        f"exec {stem}",
        f"exec {stem}.cfg",
        "",
    ]
    body = (cfg_body or "").strip("\n")
    if body:
        lines_out.extend([LARGO_INLINE_BEGIN, body, LARGO_INLINE_END, ""])

    if existing:
        skip = False
        for raw in existing.splitlines():
            s = raw.strip()
            if s.startswith(LARGO_INLINE_BEGIN):
                skip = True
                continue
            if s.startswith(LARGO_INLINE_END):
                skip = False
                continue
            if skip:
                continue
            if not s:
                continue
            if s.startswith("// Largo Auto Config"):
                continue
            m = EXEC_RE.match(s)
            if m:
                target = m.group(1).strip("\"'").lower().removesuffix(".cfg")
                if target == stem.lower():
                    continue
            lines_out.append(raw.rstrip())
        lines_out.append("")
    return "\n".join(lines_out)


def backup_live(steam: Path, account_id: str, cfg_name: str = "") -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{account_id}_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    ae = autoexec_path(steam)
    if ae.is_file():
        shutil.copy2(ae, dest / "autoexec.cfg")
    if cfg_name:
        live_cfg = dota_cfg_dir(steam) / cfg_name
        if live_cfg.is_file():
            shutil.copy2(live_cfg, dest / cfg_name)
    remote = userdata_remote(steam, account_id)
    if remote.is_dir():
        _copy_tree(remote, dest / "remote")
    local = userdata_local(steam, account_id)
    if local.is_dir():
        _copy_tree(local, dest / "local")
    return dest


def save_from_sources(
    account_id: str = GLOBAL_ID,
    cfg_source: str = "",
    settings_source: str = "",
) -> Path:
    p = ensure_profile(account_id)
    meta = load_meta(account_id)

    if cfg_source:
        src = Path(cfg_source)
        if not src.is_file():
            raise FileNotFoundError(f"CFG не найден: {src}")
        cfg_name, _ = _store_user_cfg(account_id, src)
        meta["cfg_source"] = str(src)
        meta["cfg_name"] = cfg_name

    if settings_source:
        src = Path(settings_source)
        if not src.is_dir():
            raise NotADirectoryError(f"Папка настроек не найдена: {src}")
        _ingest_settings_folder(src, account_id)
        meta["settings_source"] = str(Path(settings_source))

    meta["saved"] = True
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_meta(account_id, meta)
    return p


def copy_from_current_account(
    steam: Path,
    account_id: str,
    cfg_source: str = "",
    profile_id: str = GLOBAL_ID,
) -> Path:
    """Snapshot live Steam settings (remote+local) + optional UI CFG."""
    p = _replace_snapshot_dirs(profile_id)
    meta = load_meta(profile_id)

    if cfg_source:
        src = Path(cfg_source)
        if not src.is_file():
            raise FileNotFoundError(f"CFG не найден: {src}")
        cfg_name, _ = _store_user_cfg(profile_id, src)
        meta["cfg_source"] = str(src)
        meta["cfg_name"] = cfg_name

    # Full 570 snapshot: keys (remote) + graphics/sound (local)
    base = userdata_570(steam, account_id)
    remote = base / "remote"
    local = base / "local"
    if remote.is_dir():
        _copy_tree(remote, p / "remote")
    if local.is_dir():
        _copy_tree(local, p / "local")
    meta["settings_source"] = str(base)

    meta["saved"] = True
    meta["source_account"] = account_id
    meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_meta(profile_id, meta)
    return p


def apply_profile(steam: Path, account_id: str, profile_id: str = GLOBAL_ID) -> str:
    if not profile_ready(profile_id):
        return "нет сохранённого профиля"

    p = ensure_profile(profile_id)
    meta = load_meta(profile_id)
    cfg_file = profile_cfg_file(profile_id)
    cfg_name = meta.get("cfg_name") or (cfg_file.name if cfg_file else "")

    backup_live(steam, account_id, cfg_name)

    cfg_dir = dota_cfg_dir(steam)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    if cfg_file and cfg_file.is_file():
        if cfg_name and cfg_name.lower() != "autoexec.cfg":
            live_cfg = cfg_dir / cfg_name
            shutil.copy2(cfg_file, live_cfg)
            body = ""
            try:
                body = cfg_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                body = ""
            existing = ""
            ae = autoexec_path(steam)
            if ae.is_file():
                try:
                    existing = ae.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    existing = ""
            ae.write_text(build_autoexec_text(cfg_name, body, existing), encoding="utf-8")
        else:
            shutil.copy2(cfg_file, autoexec_path(steam))

    # Keys / cloud settings
    src_remote = p / "remote"
    live_remote = userdata_remote(steam, account_id)
    if src_remote.is_dir() and any(src_remote.iterdir()):
        live_remote.parent.mkdir(parents=True, exist_ok=True)
        _copy_tree(src_remote, live_remote)

    # Graphics / sound / machine + local convars
    src_local = p / "local"
    live_local = userdata_local(steam, account_id)
    if src_local.is_dir() and any(src_local.iterdir()):
        live_local.parent.mkdir(parents=True, exist_ok=True)
        _copy_tree(src_local, live_local)

    has_cfg = bool(cfg_name)
    has_settings = (src_remote.is_dir() and any(src_remote.iterdir())) or (
        src_local.is_dir() and any(src_local.iterdir())
    )
    if has_cfg and has_settings:
        return "настройки + CFG применены"
    if has_cfg:
        return "CFG применён"
    if has_settings:
        return "настройки применены"
    return "применено"
