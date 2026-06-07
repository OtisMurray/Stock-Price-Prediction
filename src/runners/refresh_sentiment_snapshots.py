from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rescore stored article observations into SQLite sentiment snapshots. "
            "This keeps model work outside the dashboard request path."
        )
    )
    parser.add_argument(
        "--sqlite-db",
        default="data/cache/watchlist_pipeline.db",
        help="Path to the SQLite pipeline database.",
    )
    parser.add_argument(
        "--refresh-run-id",
        type=int,
        default=0,
        help="Refresh run id to rescore. Defaults to the latest refresh run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=250,
        help="Maximum story observations to rescore.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Score candidates and print a summary without writing SQLite rows.",
    )
    parser.add_argument(
        "--rule-only",
        action="store_true",
        help="Disable local FinBERT and write rule-based sentiment only.",
    )
    parser.add_argument(
        "--enable-finbert-download",
        action="store_true",
        help="Allow Transformers to download ProsusAI/finbert if it is not already cached.",
    )
    parser.add_argument(
        "--allow-railway-finbert",
        action="store_true",
        help="Allow FinBERT inference on Railway. Leave off unless running a controlled worker/job.",
    )
    parser.add_argument(
        "--enrich-missing-text",
        action="store_true",
        help="Fetch and parse article pages when stored title/summary text is missing or too short for FinBERT.",
    )
    parser.add_argument(
        "--translate-non-english",
        action="store_true",
        help="Translate likely non-English article text to English before FinBERT readiness/scoring.",
    )
    parser.add_argument(
        "--force-finbert-ready",
        action="store_true",
        help="Apply FinBERT to every ready article instead of using the conservative hybrid gate.",
    )
    return parser.parse_args()


def configure_sentiment_environment(args: argparse.Namespace) -> None:
    os.environ["FINBERT_INFERENCE_CONTEXT"] = "backfill"
    if args.rule_only:
        os.environ["DISABLE_LOCAL_FINBERT"] = "1"
    if args.enable_finbert_download:
        os.environ["FINBERT_ALLOW_DOWNLOAD"] = "1"
    else:
        os.environ.setdefault("FINBERT_ALLOW_DOWNLOAD", "0")
    if args.allow_railway_finbert:
        os.environ["ALLOW_RAILWAY_FINBERT"] = "1"


def main() -> None:
    args = parse_args()
    configure_sentiment_environment(args)

    from src.storage import refresh_story_sentiment_snapshots

    result = refresh_story_sentiment_snapshots(
        args.sqlite_db,
        refresh_run_id=args.refresh_run_id or None,
        limit=args.limit,
        dry_run=args.dry_run,
        enrich_missing_text=args.enrich_missing_text,
        translate_non_english=args.translate_non_english,
        force_finbert_ready=args.force_finbert_ready,
    )

    print("Sentiment Snapshot Refresh")
    print("=" * 74)
    print(f"SQLite DB: {args.sqlite_db}")
    print(f"Refresh run id: {result.get('selected_refresh_run_id', 0)}")
    print(f"Dry run: {result.get('dry_run')}")
    print(f"Candidates: {result.get('candidate_count', 0)}")
    print(f"Rescored: {result.get('rescored_count', 0)}")
    print(f"Written: {result.get('written_count', 0)}")
    print(f"FinBERT-ready: {result.get('finbert_ready_count', 0)}")
    print(f"FinBERT-applied: {result.get('finbert_applied_count', 0)}")
    print(f"Article text enriched: {result.get('enriched_text_count', 0)}")
    print(f"Translated: {result.get('translated_text_count', 0)}")
    print(f"Remaining blockers: {result.get('remaining_blockers', {})}")
    print("-" * 74)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
