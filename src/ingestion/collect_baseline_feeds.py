from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

KNOWN_SOURCE_KEYS = [
    "yahoo_finance",
    "marketwatch_topstories",
    "marketwatch_marketpulse",
    "sec_press_releases",
    "prnewswire_all_news",
]


def load_ingestion_modules():
    try:
        from .rss_collectors import build_keywords, collect_baseline_articles, filter_articles
        from .rss_sources import DEFAULT_BASELINE_SOURCE_KEYS, RSS_SOURCES
    except ImportError:
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from src.ingestion.rss_collectors import (  # type: ignore
            build_keywords,
            collect_baseline_articles,
            filter_articles,
        )
        from src.ingestion.rss_sources import DEFAULT_BASELINE_SOURCE_KEYS, RSS_SOURCES  # type: ignore

    return build_keywords, collect_baseline_articles, filter_articles, DEFAULT_BASELINE_SOURCE_KEYS, RSS_SOURCES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect baseline RSS articles from easy-to-access sources."
    )
    parser.add_argument("--ticker", required=True, help="Ticker symbol such as AAPL or META.")
    parser.add_argument(
        "--company",
        default="",
        help="Company name to include in keyword filtering, such as Apple.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Extra keyword. Repeat for multiple terms.",
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=KNOWN_SOURCE_KEYS,
        help="Optional source key. Repeat to limit collection to selected sources.",
    )
    parser.add_argument(
        "--limit-per-source",
        type=int,
        default=10,
        help="Max entries to inspect from each source.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional file path to save filtered articles as JSON.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    (
        build_keywords,
        collect_baseline_articles,
        filter_articles,
        DEFAULT_BASELINE_SOURCE_KEYS,
        _RSS_SOURCES,
    ) = load_ingestion_modules()
    source_keys = args.source or DEFAULT_BASELINE_SOURCE_KEYS
    keywords = build_keywords(
        ticker=args.ticker,
        company_name=args.company or None,
        extra_keywords=args.keyword,
    )

    articles = collect_baseline_articles(
        ticker=args.ticker,
        source_keys=source_keys,
        limit_per_source=args.limit_per_source,
    )
    filtered = filter_articles(articles, keywords)

    print("Baseline RSS collection")
    print("=" * 60)
    print(f"Ticker: {args.ticker.upper()}")
    print(f"Keywords: {', '.join(keywords)}")
    print(f"Sources checked: {', '.join(source_keys)}")
    print(f"Articles collected: {len(articles)}")
    print(f"Articles matching keywords: {len(filtered)}")

    for article in filtered:
        print("-" * 60)
        print(f"Source: {article.source_name}")
        print(f"Title: {article.title}")
        print(f"Published: {article.published}")
        print(f"Link: {article.link}")
        print(f"Summary: {article.summary}")

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps([article.to_dict() for article in filtered], indent=2),
            encoding="utf-8",
        )
        print("-" * 60)
        print(f"Saved {len(filtered)} filtered articles to {output_path}")


if __name__ == "__main__":
    main()
