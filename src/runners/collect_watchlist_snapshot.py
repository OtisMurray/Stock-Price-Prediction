from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
from typing import Any

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.storage.sqlite_store import persist_watchlist_snapshot


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
    parser.add_argument(
        "--sqlite-db",
        default="",
        help="Optional SQLite database path for persisting watchlist refresh history.",
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
                "sector": str(item.get("sector", "")).strip(),
                "industry": str(item.get("industry", "")).strip(),
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
    sqlite_db: str = "",
) -> dict[str, Any]:
    from src.ingestion.rss_collectors import build_keywords
    from src.ingestion.source_pipeline import collect_watchlist_candidate_rows
    from src.preprocessing.news_preprocessor import build_ticker_profile, preprocess_ticker_news
    from src.runners.collect_all_for_ticker import build_source_usage, matches_keywords

    entries = load_watchlist_entries(watchlist_file)
    started = time.perf_counter()
    collection = collect_watchlist_candidate_rows(
        entries=entries,
        rss_limit=rss_limit,
        structured_limit=structured_limit,
        state_file=state_file,
        include_seen=include_seen,
        skip_rss=skip_rss,
        skip_structured=skip_structured,
        matcher=matches_keywords,
    )
    ticker_results: list[dict[str, Any]] = []
    for entry in entries:
        ticker = entry["ticker"]
        company = entry["company"]
        extra_keywords = entry["keywords"]
        raw_rows = collection.rows_by_ticker.get(ticker, [])
        profile = build_ticker_profile(
            ticker=ticker,
            company_name=company,
            extra_keywords=extra_keywords,
        )
        preprocessing_result = preprocess_ticker_news(raw_rows, profile)
        source_usage = build_source_usage(raw_rows, preprocessing_result)
        ticker_results.append(
            {
                "ticker": ticker,
                "company": company,
                "sector": entry["sector"],
                "industry": entry["industry"],
                "raw_match_count": len(raw_rows),
                "keywords": build_keywords(
                    ticker=ticker,
                    company_name=company or None,
                    extra_keywords=extra_keywords,
                ),
                "failures": collection.failures,
                "source_usage": source_usage,
                "stats": preprocessing_result["stats"],
                "stories": preprocessing_result["stories"],
                "related_context": preprocessing_result["related_context"],
                "review_candidates": preprocessing_result["review_candidates"],
                "rejections": preprocessing_result["rejections"],
            }
        )

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "collection_elapsed_seconds": round(time.perf_counter() - started, 3),
        "watchlist_file": watchlist_file,
        "rss_limit": rss_limit,
        "structured_limit": structured_limit,
        "include_seen": include_seen,
        "pipeline_mode": "shared_source_pool",
        "source_health": collection.source_health,
        "failures": collection.failures,
        "tickers": ticker_results,
    }
    if sqlite_db:
        persist_watchlist_snapshot(sqlite_db, snapshot)
    return snapshot


def print_watchlist_summary(snapshot: dict[str, Any]) -> None:
    print("Collect Watchlist Snapshot")
    print("=" * 70)
    print(f"Watchlist entries: {len(snapshot['tickers'])}")
    print(f"Pipeline mode: {snapshot.get('pipeline_mode', 'unknown')}")
    print(f"Collection elapsed: {snapshot.get('collection_elapsed_seconds', 0)} seconds")
    for item in snapshot["tickers"]:
        print(
            f"{item['ticker']}: "
            f"{item.get('raw_match_count', 0)} raw, "
            f"{item['stats']['clustered_story_count']} primary, "
            f"{item['stats']['related_context_rows']} related, "
            f"{item['stats']['review_candidate_rows']} review, "
            f"{item['stats']['rejected_rows']} rejected"
        )
    if snapshot.get("source_health"):
        healthy = sum(1 for row in snapshot["source_health"] if row.get("ok"))
        unhealthy = sum(1 for row in snapshot["source_health"] if not row.get("ok"))
        print(f"Source health rows: {len(snapshot['source_health'])} ({healthy} ok, {unhealthy} failed)")
        for row in snapshot["source_health"]:
            status = "OK" if row.get("ok") else "FAIL"
            cache_note = " cache" if row.get("cache_hit") else ""
            ticker_label = f" [{row.get('ticker')}]" if row.get("ticker") else ""
            print(
                f"  - {status}{cache_note} {row.get('source_name', row.get('source_key', 'source'))}{ticker_label}: "
                f"fetched={row.get('fetched_count', 0)} matched={row.get('matched_count', 0)} "
                f"time={row.get('elapsed_seconds', 0)}s"
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
        sqlite_db=args.sqlite_db,
    )

    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    print_watchlist_summary(snapshot)
    print(f"Saved watchlist snapshot to {output_path}")


if __name__ == "__main__":
    main()
