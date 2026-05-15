from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from collections import Counter
import re

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prototype wrapper that collects baseline RSS articles and public structured-news "
            "headlines, then filters the combined results for one ticker."
        )
    )
    parser.add_argument("--ticker", required=True, help="Ticker symbol such as AAPL or META.")
    parser.add_argument(
        "--company",
        default="",
        help="Company name to include in the keyword set, such as Apple.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Extra keyword. Repeat for multiple values.",
    )
    parser.add_argument(
        "--rss-limit",
        type=int,
        default=10,
        help="Maximum RSS entries to inspect per baseline source. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--structured-limit",
        type=int,
        default=10,
        help="Maximum structured headlines to inspect per public source. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--skip-rss",
        action="store_true",
        help="Skip the baseline RSS collectors.",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="Skip the structured-source collectors.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional JSON output path for combined filtered results.",
    )
    parser.add_argument(
        "--debug-json-out",
        default="",
        help="Optional JSON output path for preprocessing debug data, including rejected rows.",
    )
    parser.add_argument(
        "--state-file",
        default="tmp/seen_structured_headlines_today.json",
        help="JSON file used to remember which structured links were already processed today.",
    )
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="Include structured links already processed today.",
    )
    return parser.parse_args()


NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_keyword_text(text: str) -> str:
    return " ".join(NON_ALNUM_RE.sub(" ", text.lower()).split())


def _compact_keyword_text(text: str) -> str:
    return "".join(NON_ALNUM_RE.sub("", text.lower()).split())


def matches_keywords(text_parts: list[str], keywords: list[str]) -> bool:
    haystack = " ".join(part for part in text_parts if part)
    normalized_haystack = _normalize_keyword_text(haystack)
    compact_haystack = _compact_keyword_text(haystack)

    for keyword in keywords:
        normalized_keyword = _normalize_keyword_text(keyword)
        compact_keyword = _compact_keyword_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_haystack:
            return True
        if compact_keyword and compact_keyword in compact_haystack:
            return True
    return False


def build_source_usage(
    raw_rows: list[dict[str, Any]],
    preprocessing_result: dict[str, Any],
) -> dict[str, dict[str, int]]:
    def count_source_names(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(str(row.get("source_name", "")) for row in rows if row.get("source_name"))
        return dict(counts.most_common())

    coverage_counts = Counter()
    for row in preprocessing_result.get("stories", []):
        coverage_counts.update(row.get("coverage_sources", []))

    return {
        "raw_matches": count_source_names(raw_rows),
        "primary_stories": count_source_names(preprocessing_result.get("stories", [])),
        "related_context": count_source_names(preprocessing_result.get("related_context", [])),
        "review_candidates": count_source_names(preprocessing_result.get("review_candidates", [])),
        "rejections": count_source_names(preprocessing_result.get("rejections", [])),
        "primary_story_coverage": dict(coverage_counts.most_common()),
    }


def collect_for_ticker(
    *,
    ticker: str,
    company: str = "",
    extra_keywords: list[str] | None = None,
    rss_limit: int = 10,
    structured_limit: int = 10,
    skip_rss: bool = False,
    skip_structured: bool = False,
    state_file: str = "tmp/seen_structured_headlines_today.json",
    include_seen: bool = False,
) -> dict[str, Any]:
    from src.ingestion.rss_collectors import build_keywords, collect_baseline_articles
    from src.ingestion.seen_cache import load_seen_links, save_seen_links
    from src.ingestion.structured_collectors import collect_structured_headlines
    from src.ingestion.structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, STRUCTURED_SOURCES
    from src.preprocessing.news_preprocessor import build_ticker_profile, preprocess_ticker_news

    keywords = build_keywords(
        ticker=ticker,
        company_name=company or None,
        extra_keywords=extra_keywords,
    )

    combined_rows: list[dict[str, str]] = []
    failures: list[str] = []
    seen_links = set() if include_seen else load_seen_links(state_file)
    newly_seen = set(seen_links)

    if not skip_rss:
        try:
            rss_articles = collect_baseline_articles(
                ticker=ticker,
                limit_per_source=None if rss_limit == 0 else rss_limit,
            )
            for article in rss_articles:
                if matches_keywords([article.title, article.summary, article.text], keywords):
                    combined_rows.append(
                        {
                            "source_group": "baseline_rss",
                            "source_key": article.source_key,
                            "source_name": article.source_name,
                            "title": article.title,
                            "link": article.link,
                            "published": article.published,
                            "summary": article.summary,
                            "collection_method": "rss",
                        }
                    )
        except Exception as exc:
            failures.append(f"baseline_rss: {exc}")

    if not skip_structured:
        for source_key in PUBLIC_STRUCTURED_SOURCE_KEYS:
            try:
                headlines = collect_structured_headlines(
                    source_key,
                    limit=None if structured_limit == 0 else structured_limit,
                )
                for headline in headlines:
                    if not include_seen and headline.link in seen_links:
                        continue
                    if matches_keywords([headline.title, headline.summary], keywords):
                        combined_rows.append(
                            {
                                "source_group": "structured_news",
                                "source_key": headline.source_key,
                                "source_name": headline.source_name,
                                "title": headline.title,
                                "link": headline.link,
                                "published": headline.published,
                                "summary": headline.summary,
                                "collection_method": headline.collection_method,
                            }
                        )
                        newly_seen.add(headline.link)
            except Exception as exc:
                failures.append(f"{STRUCTURED_SOURCES[source_key].name}: {exc}")

    if not include_seen:
        save_seen_links(state_file, newly_seen)

    profile = build_ticker_profile(
        ticker=ticker,
        company_name=company,
        extra_keywords=extra_keywords,
    )
    preprocessing_result = preprocess_ticker_news(combined_rows, profile)
    source_usage = build_source_usage(combined_rows, preprocessing_result)
    return {
        "ticker": ticker.upper(),
        "company": company,
        "keywords": keywords,
        "raw_matches": combined_rows,
        "failures": failures,
        "preprocessing": preprocessing_result,
        "source_usage": source_usage,
    }


def main() -> None:
    args = parse_args()
    result = collect_for_ticker(
        ticker=args.ticker,
        company=args.company,
        extra_keywords=args.keyword,
        rss_limit=args.rss_limit,
        structured_limit=args.structured_limit,
        skip_rss=args.skip_rss,
        skip_structured=args.skip_structured,
        state_file=args.state_file,
        include_seen=args.include_seen,
    )
    final_rows = result["preprocessing"]["stories"]
    stats = result["preprocessing"]["stats"]

    print("Collect All For Ticker")
    print("=" * 70)
    print(f"Ticker: {result['ticker']}")
    print(f"Keywords: {', '.join(result['keywords'])}")
    print(f"Raw matches before preprocessing: {len(result['raw_matches'])}")
    print(f"Accepted rows before dedupe: {stats['accepted_rows_before_dedupe']}")
    print(f"Final story clusters: {stats['clustered_story_count']}")
    print(f"Related context rows: {stats['related_context_rows']}")
    print(f"Review candidate rows: {stats['review_candidate_rows']}")
    print(f"Rejected rows: {stats['rejected_rows']}")
    print(f"Duplicates merged: {stats['duplicates_merged']}")
    if result["failures"]:
        print(f"Sources with errors: {len(result['failures'])}")
        for failure in result["failures"]:
            print(f"- {failure}")

    for idx, row in enumerate(final_rows, start=1):
        print("-" * 70)
        print(f"{idx}. [{row['source_group']}] {row['source_name']}")
        print(f"Title: {row['title']}")
        print(f"Link: {row['link']}")
        if row["published"]:
            print(f"Published: {row['published']}")
        if row["summary"]:
            print(f"Summary Preview: {row['summary'][:240]}")
        print(f"Method: {row['collection_method']}")
        print(f"Relevance Score: {row['relevance_score']}")
        print(f"Event Type: {row['event_type']}")
        print(f"Importance Weight: {row['event_importance_weight']}")
        print(f"Signal Strength: {row['signal_strength']}")
        print(f"Coverage Count: {row['coverage_count']}")
        if row["coverage_sources"]:
            print(f"Coverage Sources: {', '.join(row['coverage_sources'])}")
        if row["matched_identity_terms"] or row["matched_specific_terms"] or row["matched_generic_terms"]:
            print(
                "Matched Terms: "
                f"identity={row['matched_identity_terms']} "
                f"specific={row['matched_specific_terms']} "
                f"generic={row['matched_generic_terms']}"
            )

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(final_rows, indent=2), encoding="utf-8")
        print("=" * 70)
        print(f"Saved {len(final_rows)} filtered story clusters to {output_path}")

    if args.debug_json_out:
        debug_path = Path(args.debug_json_out)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_payload = {
            "ticker": result["ticker"],
            "company": result["company"],
            "keywords": result["keywords"],
            "failures": result["failures"],
            "source_usage": result["source_usage"],
            **result["preprocessing"],
        }
        debug_path.write_text(json.dumps(debug_payload, indent=2), encoding="utf-8")
        print(f"Saved preprocessing debug output to {debug_path}")


if __name__ == "__main__":
    main()
