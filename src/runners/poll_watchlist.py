from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.runners.collect_watchlist_snapshot import build_watchlist_snapshot, print_watchlist_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Poll the current watchlist on a fixed interval, saving a latest snapshot each run "
            "and optionally archiving timestamped history files."
        )
    )
    parser.add_argument(
        "--watchlist-file",
        required=True,
        help="JSON watchlist file containing ticker, company, and keyword entries.",
    )
    parser.add_argument(
        "--latest-json-out",
        required=True,
        help="Path to overwrite with the most recent watchlist snapshot on every run.",
    )
    parser.add_argument(
        "--history-dir",
        default="",
        help="Optional directory for timestamped snapshot history files.",
    )
    parser.add_argument(
        "--history-keep",
        type=int,
        default=30,
        help="Maximum number of history snapshots to keep. Use 0 to keep all history files.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=120,
        help="Polling interval in seconds between runs. Default is 120.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Optional maximum number of polling runs. Use 0 to keep running until stopped.",
    )
    parser.add_argument(
        "--stop-file",
        default="tmp/watchlist_polling.stop",
        help="If this file exists, polling stops cleanly before the next run.",
    )
    parser.add_argument(
        "--clear-stop-file",
        action="store_true",
        help="Delete the stop file before starting, if it exists.",
    )
    parser.add_argument(
        "--rss-limit",
        type=int,
        default=0,
        help="Maximum RSS entries to inspect per source for each ticker. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--structured-limit",
        type=int,
        default=0,
        help="Maximum structured entries to inspect per source for each ticker. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--state-file",
        default="tmp/seen_structured_headlines_today.json",
        help="JSON file used to remember structured links already processed today.",
    )
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="Include structured links already processed today.",
    )
    parser.add_argument(
        "--skip-rss",
        action="store_true",
        help="Skip the baseline RSS sources during polling.",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="Skip the structured sources during polling.",
    )
    return parser.parse_args()


def _write_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def _history_name(snapshot: dict) -> str:
    generated_at = str(snapshot.get("generated_at", ""))
    stamp = generated_at.replace(":", "-").replace("Z", "")
    if not stamp:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    return f"watchlist_snapshot_{stamp}.json"


def _prune_history_dir(history_dir: Path, keep: int) -> None:
    if keep <= 0:
        return
    history_files = sorted(history_dir.glob("watchlist_snapshot_*.json"))
    overflow = len(history_files) - keep
    if overflow <= 0:
        return
    for path in history_files[:overflow]:
        path.unlink(missing_ok=True)


def main() -> None:
    args = parse_args()
    stop_path = Path(args.stop_file)
    latest_path = Path(args.latest_json_out)
    history_dir = Path(args.history_dir) if args.history_dir else None

    if args.clear_stop_file and stop_path.exists():
        stop_path.unlink()

    run_count = 0
    print("Watchlist Polling")
    print("=" * 70)
    print(f"Interval: {args.interval_seconds} seconds")
    print(f"Latest snapshot path: {latest_path}")
    if history_dir:
        print(f"History directory: {history_dir}")
        print(f"History retention: keep last {args.history_keep} snapshots")
    print(f"Stop file: {stop_path}")
    if args.max_runs:
        print(f"Max runs: {args.max_runs}")
    else:
        print("Max runs: until stop file exists")
    print("=" * 70)

    while True:
        if stop_path.exists():
            print(f"Stop file detected at {stop_path}. Exiting polling loop.")
            break

        run_count += 1
        print(f"Run {run_count} started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        snapshot = build_watchlist_snapshot(
            watchlist_file=args.watchlist_file,
            rss_limit=args.rss_limit,
            structured_limit=args.structured_limit,
            state_file=args.state_file,
            include_seen=args.include_seen,
            skip_rss=args.skip_rss,
            skip_structured=args.skip_structured,
        )
        _write_snapshot(latest_path, snapshot)
        if history_dir:
            history_path = history_dir / _history_name(snapshot)
            _write_snapshot(history_path, snapshot)
            _prune_history_dir(history_dir, args.history_keep)
            print(f"Saved history snapshot to {history_path}")

        print_watchlist_summary(snapshot)
        print(f"Saved latest snapshot to {latest_path}")

        if args.max_runs and run_count >= args.max_runs:
            print("Reached max runs. Exiting polling loop.")
            break

        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
