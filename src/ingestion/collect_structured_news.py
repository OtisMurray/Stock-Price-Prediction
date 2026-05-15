from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

try:
    from .structured_collectors import collect_structured_headlines
    from .seen_cache import load_seen_links, save_seen_links
    from .structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, STRUCTURED_SOURCES
except ImportError:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.ingestion.structured_collectors import collect_structured_headlines  # type: ignore
    from src.ingestion.seen_cache import load_seen_links, save_seen_links  # type: ignore
    from src.ingestion.structured_sources import PUBLIC_STRUCTURED_SOURCE_KEYS, STRUCTURED_SOURCES  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect public structured-news headlines from selected sources."
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=sorted(STRUCTURED_SOURCES.keys()),
        help="Specific source key to collect. Repeat for multiple sources.",
    )
    parser.add_argument(
        "--all-public",
        action="store_true",
        help="Collect from all currently configured public structured sources.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum headlines to return per source. Use 0 for no explicit cap.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--state-file",
        default="tmp/seen_structured_headlines_today.json",
        help="JSON file used to remember which links were already processed today.",
    )
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="Include links already processed today instead of filtering them out.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_keys = args.source or (PUBLIC_STRUCTURED_SOURCE_KEYS if args.all_public else [])
    if not source_keys:
        raise SystemExit("Please provide --source or use --all-public.")

    seen_links = set() if args.include_seen else load_seen_links(args.state_file)
    newly_seen = set(seen_links)
    all_rows = []
    for source_key in source_keys:
        print("=" * 70)
        print(f"Source: {STRUCTURED_SOURCES[source_key].name} ({source_key})")
        try:
            limit = None if args.limit == 0 else args.limit
            headlines = collect_structured_headlines(source_key, limit=limit)
        except Exception as exc:
            print(f"Collection failed: {exc}")
            continue

        fresh_headlines = [headline for headline in headlines if args.include_seen or headline.link not in seen_links]
        print(f"Headlines collected: {len(fresh_headlines)}")
        for idx, headline in enumerate(fresh_headlines, start=1):
            print("-" * 70)
            print(f"{idx}. {headline.title}")
            print(f"Link: {headline.link}")
            if headline.published:
                print(f"Published: {headline.published}")
            if headline.summary:
                print(f"Summary Preview: {headline.summary[:220]}")
            print(f"Method: {headline.collection_method}")
            newly_seen.add(headline.link)
            all_rows.append(headline.to_dict())

    if not args.include_seen:
        save_seen_links(args.state_file, newly_seen)

    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
        print("=" * 70)
        print(f"Saved {len(all_rows)} headlines to {output_path}")


if __name__ == "__main__":
    main()
