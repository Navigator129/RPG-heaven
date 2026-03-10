"""
Save slot utilities — per-save isolated data directories.
Each save name maps to data/<sanitized_name>/rpg.db; different saves
do not share story, entities, or world state.
"""

from __future__ import annotations

import os
import re

DATA_DIR = "data"


def sanitize_save_name(name: str) -> str:
    """Allow alphanumeric, CJK, underscore, hyphen. Remove path traversal."""
    s = name.strip()
    if not s:
        return "default"
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = s[:64]
    return s or "default"


def get_db_path(save_name: str) -> str:
    """Return the SQLite db path for a given save name."""
    safe = sanitize_save_name(save_name)
    return os.path.join(DATA_DIR, safe, "rpg.db")


def ensure_save_dir(save_name: str) -> str:
    """Create save directory if needed; return db_path."""
    db_path = get_db_path(save_name)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return db_path


def list_saves() -> list[str]:
    """List existing save names (subdirs of data/ that contain rpg.db)."""
    if not os.path.isdir(DATA_DIR):
        return []
    names: list[str] = []
    for entry in os.listdir(DATA_DIR):
        sub = os.path.join(DATA_DIR, entry)
        if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "rpg.db")):
            names.append(entry)
    return sorted(names)


def save_exists(save_name: str) -> bool:
    """Check if a save with the given name exists."""
    db_path = get_db_path(save_name)
    return os.path.isfile(db_path)
