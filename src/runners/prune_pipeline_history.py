from __future__ import annotations

import argparse
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.storage import prune_pipeline_history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prune old SQLite pipeline history while keeping recent live-feed and translation data."
    )
    parser.add_argument(
        "--sqlite-db",
        default="data/cache/watchlist_pipeline.db",
        help="Path to the SQLite pipeline database.",
    )
    parser.add_argument(
        "--keep-refresh-runs",
        type=int,
        default=250,
        help="Number of most recent refresh runs to keep before deleting older runs.",
    )
    parser.add_argument(
        "--observation-days",
        type=int,
        default=60,
        help="Retention window in days for story observations.",
    )
    parser.add_argument(
        "--source-health-days",
        type=int,
        default=30,
        help="Retention window in days for source health observations.",
    )
    parser.add_argument(
        "--translation-days",
        type=int,
        default=180,
        help="Retention window in days for saved translations.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = prune_pipeline_history(
        args.sqlite_db,
        keep_refresh_runs=args.keep_refresh_runs,
        observation_retention_days=args.observation_days,
        source_health_retention_days=args.source_health_days,
        translation_retention_days=args.translation_days,
    )

    print("Pipeline History Prune")
    print("=" * 74)
    print(f"SQLite DB: {args.sqlite_db}")
    print(f"Keep refresh runs: {result['keep_refresh_runs']}")
    print(f"Story observation retention: {result['observation_retention_days']} days")
    print(f"Source health retention: {result['source_health_retention_days']} days")
    print(f"Translation retention: {result['translation_retention_days']} days")
    print("-" * 74)
    print(f"Refresh runs deleted: {result['refresh_runs_deleted']}")
    print(f"Story observations deleted: {result['story_observations_deleted']}")
    print(f"Orphan stories deleted: {result['orphan_stories_deleted']}")
    print(f"Source health rows deleted: {result['source_health_deleted']}")
    print(f"Saved translations deleted: {result['story_translations_deleted']}")


if __name__ == "__main__":
    main()
