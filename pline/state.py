"""Editions and bookkeeping kept in the repo under state/."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from . import DOCS, STATE

EDITIONS = STATE / "editions"
SHOWN = STATE / "shown.json"
THUMBS = DOCS / "thumbs"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_shown() -> dict[str, str]:
    return load_json(SHOWN, {})


def save_shown(shown: dict[str, str], today: date, keep_days: int = 90) -> None:
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    save_json(SHOWN, {k: v for k, v in sorted(shown.items()) if v >= cutoff})


def list_editions() -> list[dict]:
    """All stored editions, newest first."""
    out = []
    for p in sorted(EDITIONS.glob("*.json"), reverse=True):
        ed = load_json(p, None)
        if ed and ed.get("date"):
            out.append(ed)
    return out


def save_edition(edition: dict) -> None:
    save_json(EDITIONS / f"{edition['date']}.json", edition)


def prune(today: date, keep_days: int) -> None:
    """Delete editions past keep_days and thumbnails no remaining edition uses."""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    for p in EDITIONS.glob("*.json"):
        if p.stem < cutoff:
            p.unlink()
    for p in DOCS.glob("20??-??-??.html"):
        if p.stem < cutoff:
            p.unlink()
    used = {
        Path(s["image"]).name
        for ed in list_editions()
        for s in ed.get("stories", [])
        if s.get("image")
    }
    if THUMBS.exists():
        for p in THUMBS.glob("*.webp"):
            if p.name not in used:
                p.unlink()
