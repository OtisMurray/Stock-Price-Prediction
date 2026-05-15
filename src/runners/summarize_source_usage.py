from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize source usage for a single-ticker debug file or a multi-ticker watchlist snapshot."
    )
    parser.add_argument(
        "--json-file",
        required=True,
        help="Path to a ticker debug JSON file or watchlist snapshot JSON file.",
    )
    return parser.parse_args()


def print_counts(title: str, counts: dict[str, int]) -> None:
    print(title)
    if not counts:
        print("  none")
        return
    for source, count in counts.items():
        print(f"  {source}: {count}")


def derive_counts(rows: list[dict], field: str = "source_name") -> dict[str, int]:
    counts = Counter(str(row.get(field, "")) for row in rows if row.get(field))
    return dict(counts.most_common())


def derive_coverage_counts(rows: list[dict]) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        counts.update(row.get("coverage_sources", []))
    return dict(counts.most_common())


def summarize_ticker_payload(payload: dict, heading: str | None = None) -> None:
    source_usage = payload.get("source_usage", {})
    stories = payload.get("stories", [])
    related_context = payload.get("related_context", [])
    review_candidates = payload.get("review_candidates", [])
    rejections = payload.get("rejections", [])

    raw_counts = source_usage.get("raw_matches", {})
    primary_counts = source_usage.get("primary_stories", {}) or derive_counts(stories)
    related_counts = source_usage.get("related_context", {}) or derive_counts(related_context)
    review_counts = source_usage.get("review_candidates", {}) or derive_counts(review_candidates)
    rejection_counts = source_usage.get("rejections", {}) or derive_counts(rejections)
    coverage_counts = source_usage.get("primary_story_coverage", {}) or derive_coverage_counts(stories)

    if heading:
        print(heading)
    else:
        print(f"{payload.get('ticker', 'UNKNOWN')}")
    print("-" * 70)
    if not raw_counts:
        print("Raw matches by source")
        print("  unavailable in this saved file; rerun the collector/watchlist snapshot with the latest code to populate it")
    else:
        print_counts("Raw matches by source", raw_counts)
    print_counts("Primary clusters by representative source", primary_counts)
    print_counts("Related context by source", related_counts)
    print_counts("Review candidates by source", review_counts)
    print_counts("Rejections by source", rejection_counts)
    print_counts("Primary cluster coverage by all contributing sources", coverage_counts)
    print()


def main() -> None:
    args = parse_args()
    payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))

    if "tickers" in payload and isinstance(payload["tickers"], list):
        print("Watchlist Source Usage Summary")
        print("=" * 70)
        for item in payload["tickers"]:
            summarize_ticker_payload(item, heading=f"{item.get('ticker', 'UNKNOWN')} ({item.get('company', '')})")
    else:
        print("Ticker Source Usage Summary")
        print("=" * 70)
        summarize_ticker_payload(payload)


if __name__ == "__main__":
    main()
