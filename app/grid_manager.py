"""Hero grids: a library of grids, one chosen as default, carried between Steam accounts.

The chosen grid lives in our own store. It is written into every account's
remote/cfg/hero_grid_config.json; whatever the player changes in it (heroes moved
between categories) is read back when they leave the account and travels on.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from paths import BACKUP_DIR, BUNDLE_DIR, PROFILES_DIR, userdata_570

GRIDS_DIR = PROFILES_DIR / "grids"
BUNDLED_DIR = BUNDLE_DIR / "assets" / "grids"  # ready-made grids that ship with the app
GRID_FILE_REL = Path("remote/cfg/hero_grid_config.json")
FILE_VERSION = 3


def _slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")[:60] or "grid"


def _store_path(name: str) -> Path:
    return GRIDS_DIR / f"{_slug(name)}.json"


def fingerprint(config: dict) -> str:
    raw = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid_config(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("config_name"), str)
        and bool(item["config_name"].strip())
        and isinstance(item.get("categories"), list)
        and len(item["categories"]) > 0
    )


def _names_in(folder: Path) -> list[str]:
    names: list[str] = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            data = _read_json(path)
            if data and _valid_config(data):
                names.append(data["config_name"])
    return names


def list_grids() -> list[str]:
    """Names of the built-in grids plus the ones the user added, alphabetical."""
    return sorted(set(_names_in(BUNDLED_DIR)) | set(_names_in(GRIDS_DIR)), key=str.lower)


def _render_preview(config: dict, dest: Path) -> Path | None:
    """Draw the grid's dots on a dark background (for grids that come without a screenshot)."""
    from PIL import Image, ImageDraw

    cats = [c for c in config.get("categories", []) if isinstance(c, dict)]
    xs = [c.get("x_position") for c in cats if isinstance(c.get("x_position"), (int, float))]
    ys = [c.get("y_position") for c in cats if isinstance(c.get("y_position"), (int, float))]
    if not xs or not ys:
        return None
    x0, x1, y0, y1 = min(xs), max(xs) + 30, min(ys), max(ys) + 30
    scale = 640 / max(1.0, x1 - x0)
    w, h = 640, max(120, min(420, int((y1 - y0) * scale)))
    img = Image.new("RGB", (w, h), (13, 17, 19))
    draw = ImageDraw.Draw(img)
    from PIL import ImageFont

    def load(names: tuple[str, ...]):
        for fname in names:
            try:
                return ImageFont.truetype(fname, 7)
            except OSError:
                continue
        return None

    font = load(("consola.ttf", "arial.ttf"))
    font_cjk = load(("msgothic.ttc", "YuGothM.ttc", "meiryo.ttc")) or font
    for c in cats:
        x, y = c.get("x_position"), c.get("y_position")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            px, py = (x - x0) * scale, (y - y0) * scale
            label = str(c.get("category_name") or "").strip()
            if label and label != "." and len(label) <= 80:  # text art: draw the characters themselves
                draw.text((px, py), label, fill=(150, 160, 185), font=font_cjk if any(ord(ch) > 0x2E7F for ch in label) else font)
            else:
                r = 1.1
                draw.ellipse((px - r, py - r, px + r, py + r), fill=(150, 160, 185))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=88)
    return dest


def preview_path(name: str) -> Path | None:
    """Picture for a grid: the screenshot shipped with a built-in one, else a drawing made from its dots."""
    if BUNDLED_DIR.is_dir():
        for path in BUNDLED_DIR.glob("*.json"):
            data = _read_json(path)
            if data and data.get("config_name") == name:
                shot = BUNDLED_DIR / "previews" / (path.stem + ".jpg")
                if shot.is_file():
                    return shot
    cached = GRIDS_DIR / "previews" / (_slug(name) + ".jpg")
    if cached.is_file():
        return cached
    config = load_grid(name)
    return _render_preview(config, cached) if config else None


def has_user_copy(name: str) -> bool:
    return _store_path(name).is_file()


def is_removable(name: str) -> bool:
    """A grid the user added can be removed; a built-in one only reset (if the user changed it)."""
    return (not is_bundled(name)) or has_user_copy(name)


def is_bundled(name: str) -> bool:
    return name in _names_in(BUNDLED_DIR)


def load_grid(name: str) -> dict | None:
    """The user's copy (with the heroes they placed) first, else the built-in grid."""
    data = _read_json(_store_path(name))
    if data and _valid_config(data):
        return data
    if BUNDLED_DIR.is_dir():
        for path in BUNDLED_DIR.glob("*.json"):
            data = _read_json(path)
            if data and _valid_config(data) and data["config_name"] == name:
                return data
    return None


def save_grid(config: dict) -> None:
    GRIDS_DIR.mkdir(parents=True, exist_ok=True)
    _store_path(config["config_name"]).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def delete_grid(name: str) -> None:
    _store_path(name).unlink(missing_ok=True)


def import_file(path: Path) -> list[str]:
    """Add every non-empty grid of a hero_grid_config.json file to the library. Returns their names."""
    data = _read_json(path)
    if not data or not isinstance(data.get("configs"), list):
        raise ValueError("это не файл сетки героев (hero_grid_config.json)")
    added: list[str] = []
    for item in data["configs"]:
        if _valid_config(item):
            save_grid({"config_name": item["config_name"], "categories": item["categories"]})
            added.append(item["config_name"])
    if not added:
        raise ValueError("в файле нет ни одной заполненной сетки")
    return added


def _account_file(steam: Path, account_id: str) -> Path:
    return userdata_570(steam, account_id) / GRID_FILE_REL


def _find(doc: dict, name: str) -> dict | None:
    for item in doc.get("configs") or []:
        if isinstance(item, dict) and item.get("config_name") == name:
            return item
    return None


def read_account_grid(steam: Path, account_id: str, name: str) -> dict | None:
    doc = _read_json(_account_file(steam, account_id))
    if not doc:
        return None
    item = _find(doc, name)
    return {"config_name": name, "categories": item.get("categories", [])} if item else None


def apply_grid(steam: Path, account_id: str, name: str) -> str | None:
    """Put the stored grid into the account's file. Returns its fingerprint, or None if nothing to apply."""
    config = load_grid(name)
    if not config:
        return None
    path = _account_file(steam, account_id)
    doc = _read_json(path) or {}
    configs = doc.get("configs")
    if not isinstance(configs, list):
        configs = []
    doc["configs"] = configs
    doc.setdefault("version", FILE_VERSION)

    current = _find(doc, name)
    if current is not None and fingerprint({"config_name": name, "categories": current.get("categories", [])}) == fingerprint(config):
        return fingerprint(config)

    if path.is_file():
        dest = BACKUP_DIR / f"{account_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}" / GRID_FILE_REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)

    if current is not None:
        current["categories"] = config["categories"]
    else:
        configs.insert(0, {"config_name": name, "categories": config["categories"]})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # fresh mtime: Steam Cloud takes this version as the newest
    return fingerprint(config)


def harvest_grid(steam: Path, account_id: str, name: str, applied: str | None) -> str | None:
    """Read back the player's edits. Returns the new fingerprint if the store was updated.

    Only accounts where we put the grid ourselves (`applied` is its fingerprint then) are read,
    so an old copy on an account we never touched can't overwrite the store.
    """
    if not applied:
        return None
    found = read_account_grid(steam, account_id, name)
    if not found or not _valid_config(found):
        return None
    now = fingerprint(found)
    if now == applied:
        return None
    save_grid(found)
    return now
