from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.runners.collect_all_for_ticker import collect_for_ticker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a multi-ticker watchlist snapshot using the same baseline RSS and "
            "structured-news pipeline as the single-ticker collector."
        )
    )
    parser.add_argument(
        "--watchlist-file",
        required=True,
        help="JSON file containing a list of watchlist entries with ticker, company, and keywords.",
    )
    parser.add_argument(
        "--json-out",
        required=True,
        help="Path to save the combined watchlist snapshot JSON.",
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
        help="Skip the baseline RSS sources for the watchlist run.",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="Skip the structured sources for the watchlist run.",
    )
    return parser.parse_args()


def load_watchlist_entries(path: str) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Watchlist file must contain a top-level JSON list.")
    entries: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each watchlist entry must be a JSON object.")
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("Each watchlist entry must include a ticker.")
        company = str(item.get("company", "")).strip()
        keywords = item.get("keywords", [])
        if keywords is None:
            keywords = []
        if not isinstance(keywords, list):
            raise ValueError(f"Watchlist keywords for {ticker} must be a list.")
        entries.append(
            {
                "ticker": ticker,
                "company": company,
                "keywords": [str(keyword).strip() for keyword in keywords if str(keyword).strip()],
            }
        )
    return entries


def build_watchlist_snapshot(
    *,
    watchlist_file: str,
    rss_limit: int = 0,
    structured_limit: int = 0,
    state_file: str = "tmp/seen_structured_headlines_today.json",
    include_seen: bool = False,
    skip_rss: bool = False,
    skip_structured: bool = False,
) -> dict[str, Any]:
    entries = load_watchlist_entries(watchlist_file)
    ticker_results: list[dict[str, Any]] = []
    for entry in entries:
        result = collect_for_ticker(
            ticker=entry["ticker"],
            company=entry["company"],
            extra_keywords=entry["keywords"],
            rss_limit=rss_limit,
            structured_limit=structured_limit,
            skip_rss=skip_rss,
            skip_structured=skip_structured,
            state_file=state_file,
            include_seen=include_seen,
        )
        ticker_results.append(
            {
                "ticker": result["ticker"],
                "company": result["company"],
                "keywords": result["keywords"],
                "failures": result["failures"],
                "source_usage": result["source_usage"],
                "stats": result["preprocessing"]["stats"],
                "stories": result["preprocessing"]["stories"],
                "related_context": result["preprocessing"]["related_context"],
                "review_candidates": result["preprocessing"]["review_candidates"],
                "rejections": result["preprocessing"]["rejections"],
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "watchlist_file": watchlist_file,
        "rss_limit": rss_limit,
        "structured_limit": structured_limit,
        "include_seen": include_seen,
        "tickers": ticker_results,
    }


def print_watchlist_summary(snapshot: dict[str, Any]) -> None:
    print("Collect Watchlist Snapshot")
    print("=" * 70)
    print(f"Watchlist entries: {len(snapshot['tickers'])}")
    for item in snapshot["tickers"]:
        print(
            f"{item['ticker']}: "
            f"{item['stats']['clustered_story_count']} primary, "
            f"{item['stats']['related_context_rows']} related, "
            f"{item['stats']['review_candidate_rows']} review, "
            f"{item['stats']['rejected_rows']} rejected"
        )
    print("=" * 70)


def main() -> None:
    args = parse_args()
    snapshot = build_watchlist_snapshot(
        watchlist_file=args.watchlist_file,
        rss_limit=args.rss_limit,
        structured_limit=args.structured_limit,
        state_file=args.state_file,
        include_seen=args.include_seen,
        skip_rss=args.skip_rss,
        skip_structured=args.skip_structured,
    )

    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    print_watchlist_summary(snapshot)
    print(f"Saved watchlist snapshot to {output_path}")


if __name__ == "__main__":
    main()
