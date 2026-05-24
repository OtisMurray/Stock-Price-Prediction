from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.ingestion.rss_collectors import fetch_rss_source
from src.ingestion.rss_sources import DEFAULT_BASELINE_SOURCE_KEYS, RSS_SOURCES
from src.ingestion.structured_collectors import collect_structured_headlines
from src.ingestion.structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, STRUCTURED_SOURCES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the currently configured public news sources and report whether each is returning headlines."
    )
    parser.add_argument(
        "--ticker",
        default="AAPL",
        help="Ticker used for ticker-specific source checks such as Yahoo Finance RSS.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum sample headlines to pull from each source.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional JSON output path for the audit results.",
    )
    return parser.parse_args()


def audit_baseline_sources(*, ticker: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_key in DEFAULT_BASELINE_SOURCE_KEYS:
        source = RSS_SOURCES[source_key]
        try:
            articles = fetch_rss_source(
                source_key=source_key,
                ticker=ticker if source.is_ticker_specific else None,
                limit_per_source=limit,
            )
            results.append(
                {
                    "source_group": "baseline_rss",
                    "source_key": source.key,
                    "source_name": source.name,
                    "ok": True,
                    "count": len(articles),
                    "notes": source.notes,
                    "sample_titles": [article.title for article in articles[:3]],
                    "sample_links": [article.link for article in articles[:3]],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "source_group": "baseline_rss",
                    "source_key": source.key,
                    "source_name": source.name,
                    "ok": False,
                    "count": 0,
                    "notes": source.notes,
                    "error": str(exc),
                    "sample_titles": [],
                    "sample_links": [],
                }
            )
    return results


def audit_structured_sources(*, ticker: str, limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_key in PUBLIC_STRUCTURED_SOURCE_KEYS:
        source = STRUCTURED_SOURCES[source_key]
        try:
            headlines = collect_structured_headlines(
                source_key,
                limit=limit,
                ticker=ticker if source.is_ticker_specific else "",
            )
            results.append(
                {
                    "source_group": "structured_news",
                    "source_key": source.key,
                    "source_name": source.name,
                    "ok": True,
                    "count": len(headlines),
                    "access_type": source.access_type,
                    "first_method": source.first_method,
                    "notes": source.notes,
                    "sample_titles": [headline.title for headline in headlines[:3]],
                    "sample_links": [headline.link for headline in headlines[:3]],
                    "sample_methods": sorted({headline.collection_method for headline in headlines}),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "source_group": "structured_news",
                    "source_key": source.key,
                    "source_name": source.name,
                    "ok": False,
                    "count": 0,
                    "access_type": source.access_type,
                    "first_method": source.first_method,
                    "notes": source.notes,
                    "error": str(exc),
                    "sample_titles": [],
                    "sample_links": [],
                    "sample_methods": [],
                }
            )
    return results


def print_results(rows: list[dict[str, Any]]) -> None:
    print("Source Audit")
    print("=" * 70)
    for row in rows:
        status = "OK" if row["ok"] else "ERROR"
        print(f"[{status}] {row['source_name']} ({row['source_group']})")
        print(f"Count: {row['count']}")
        if row.get("first_method"):
            print(f"Method: {row['first_method']}")
        if row.get("access_type"):
            print(f"Access: {row['access_type']}")
        if row.get("sample_methods"):
            print(f"Collected via: {', '.join(row['sample_methods'])}")
        if row.get("error"):
            print(f"Error: {row['error']}")
        if row["sample_titles"]:
            print(f"Sample: {row['sample_titles'][0]}")
        print("-" * 70)


def main() -> None:
    args = parse_args()
    rows = audit_baseline_sources(ticker=args.ticker.upper(), limit=args.limit)
    rows.extend(audit_structured_sources(ticker=args.ticker.upper(), limit=args.limit))
    print_results(rows)

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Saved source audit to {output_path}")


if __name__ == "__main__":
    main()
