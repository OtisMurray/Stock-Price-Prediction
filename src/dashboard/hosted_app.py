from __future__ import annotations

import os
from pathlib import Path

from src.dashboard.dashboard_state import DashboardState
from src.dashboard.watchlist_dashboard import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _storage_root() -> Path:
    explicit_root = os.environ.get("STOCK_DASHBOARD_DATA_ROOT", "").strip()
    railway_mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    base = explicit_root or railway_mount
    if base:
        return Path(base).expanduser()
    return REPO_ROOT


def _default_storage_path(relative_path: str) -> str:
    return str((_storage_root() / relative_path).resolve())


def _default_repo_path(relative_path: str) -> str:
    return str((REPO_ROOT / relative_path).resolve())


def build_dashboard_state() -> DashboardState:
    snapshot_file = _env(
        "STOCK_DASHBOARD_SNAPSHOT_FILE",
        _default_storage_path("data/cache/watchlist_snapshot_100_latest.json"),
    )
    dashboard_state_file = _env(
        "STOCK_DASHBOARD_STATE_FILE",
        _default_storage_path("tmp/dashboard_state.json"),
    )
    state_file = _env(
        "STOCK_DASHBOARD_SEEN_STATE_FILE",
        _default_storage_path("tmp/seen_structured_headlines_today.json"),
    )
    sqlite_db = _env(
        "STOCK_DASHBOARD_SQLITE_DB",
        _default_storage_path("data/cache/watchlist_pipeline.db"),
    )

    for path_text in (snapshot_file, dashboard_state_file, state_file, sqlite_db):
        parent = Path(path_text).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)

    return DashboardState(
        watchlist_file=_env(
            "STOCK_DASHBOARD_WATCHLIST_FILE",
            _default_repo_path("data/watchlists/us_market_watchlist_100.json"),
        ),
        snapshot_file=snapshot_file,
        dashboard_state_file=dashboard_state_file,
        cooldown_seconds=_env_int("STOCK_DASHBOARD_COOLDOWN_SECONDS", 90),
        rss_limit=_env_int("STOCK_DASHBOARD_RSS_LIMIT", 0),
        structured_limit=_env_int("STOCK_DASHBOARD_STRUCTURED_LIMIT", 0),
        state_file=state_file,
        skip_rss=_env("STOCK_DASHBOARD_SKIP_RSS", "0") == "1",
        skip_structured=_env("STOCK_DASHBOARD_SKIP_STRUCTURED", "0") == "1",
        sqlite_db=sqlite_db,
    )


dashboard_state = build_dashboard_state()
app = create_app(dashboard_state)


if __name__ == "__main__":
    host = _env("HOST", "0.0.0.0")
    port = _env_int("PORT", 8000)
    app.run(host=host, port=port, debug=False, use_reloader=False)
