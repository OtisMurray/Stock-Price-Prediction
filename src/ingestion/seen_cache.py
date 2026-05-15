from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def today_key() -> str:
    return date.today().isoformat()


def load_seen_links(state_file: str) -> set[str]:
    path = Path(state_file)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return set(data.get(today_key(), []))


def save_seen_links(state_file: str, links: set[str]) -> None:
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, list[str]] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing[today_key()] = sorted(links)
    # prune older days to keep memory small
    existing = {today_key(): existing[today_key()]}
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
