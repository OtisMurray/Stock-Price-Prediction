from __future__ import annotations

import json
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from flask import Flask, jsonify, Response, request

from src.dashboard.dashboard_state import DashboardState, parse_args
from src.dashboard.translation_utils import likely_non_english, translate_text_to_english
from src.runners.collect_all_for_ticker import collect_for_ticker
from src.storage import fetch_saved_translation, save_story_translation

import re

VALID_LOOKUP_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
NON_EQUITY_LOOKUP_SUFFIXES = ("USD", "USDT", "BTC", "ETH", "EUR", "JPY", "GBP")


APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock News Dashboard</title>
  <script src="/static/react.development.js"></script>
  <script src="/static/react-dom.development.js"></script>
  <style>
    :root {
      --bg: #f3f5ef;
      --panel: #fffdf8;
      --ink: #13233a;
      --muted: #5e6d7f;
      --line: #dbe4d2;
      --accent: #0f766e;
      --new: #166534;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.08), transparent 28%),
        linear-gradient(180deg, #f9faf7 0%, var(--bg) 100%);
      color: var(--ink);
    }
    .wrap {
      max-width: 1380px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    .site-nav {
      padding: 16px 20px 0;
    }
    .site-nav-inner {
      max-width: 1380px;
      margin: 0 auto;
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }
    .site-link {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 10px 14px;
      background: rgba(255,255,255,0.88);
      border: 1px solid var(--line);
      box-shadow: 0 8px 18px rgba(19, 35, 58, 0.06);
      color: var(--ink);
      text-decoration: none;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .site-link:hover {
      text-decoration: none;
      border-color: rgba(15,118,110,0.4);
      color: #0c5c58;
    }
    .hero, .summary-card, .panel {
      background: rgba(255,255,255,0.9);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(19, 35, 58, 0.06);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 42px;
      line-height: 1;
      letter-spacing: -0.03em;
    }
    h2 {
      margin: 0 0 10px;
      font-size: 24px;
      letter-spacing: -0.03em;
    }
    .subtitle, .panel-subtitle, .table-note {
      margin: 0;
      color: var(--muted);
      font-size: 15px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .panel-subtitle {
      font-size: 13px;
      line-height: 1.35;
      max-width: 760px;
    }
    .toolbar {
      margin-top: 22px;
      display: flex;
      gap: 16px;
      align-items: center;
      flex-wrap: wrap;
    }
    button {
      border: none;
      border-radius: 999px;
      padding: 14px 22px;
      background: linear-gradient(135deg, var(--accent), #0b4f69);
      color: white;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      background: white;
      color: var(--ink);
      border: 1px solid var(--line);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.65;
    }
    .meta {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 14px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .market-pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 8px 12px;
      background: rgba(15,118,110,0.1);
      color: #0f5c57;
      border: 1px solid rgba(15,118,110,0.18);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      font-family: Arial, Helvetica, sans-serif;
    }
    .status {
      margin-top: 14px;
      font-size: 15px;
      color: #27445d;
    }
    .summary-grid {
      margin-top: 24px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .summary-card .label {
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .summary-card .value {
      margin-top: 8px;
      font-size: 34px;
      font-weight: 700;
      letter-spacing: -0.04em;
    }
    .panel {
      margin-top: 18px;
    }
    .stack {
      display: grid;
      gap: 18px;
    }
    .detail-grid {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
    }
    .detail-card {
      background: rgba(255,255,255,0.82);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
    }
    .detail-card .detail-label {
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .detail-card .detail-value {
      margin-top: 8px;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    .detail-card .detail-sub {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      font-family: Arial, Helvetica, sans-serif;
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 14px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .filter-grid {
      display: grid;
      grid-template-columns: 1.2fr repeat(5, minmax(0, 1fr));
      gap: 12px;
      align-items: end;
    }
    .filter-field label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .filter-field input,
    .filter-field select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      background: white;
      color: var(--ink);
      font-size: 14px;
    }
    .filter-field input::placeholder {
      font-size: 13px;
    }
    .filter-actions {
      display: flex;
      justify-content: flex-end;
    }
    .table-wrap {
      overflow-x: auto;
      border-radius: 16px;
      border: 1px solid #e5ebdf;
      background: white;
      margin-top: 16px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1120px;
    }
    th, td {
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid #edf1e7;
      font-size: 14px;
    }
    th {
      position: sticky;
      top: 0;
      background: #f8faf6;
      color: #2d4257;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      z-index: 1;
    }
    .col-ticker { min-width: 120px; }
    .col-headline { min-width: 360px; }
    .ticker-chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 10px;
      background: rgba(15,118,110,0.09);
      color: #0c5c58;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    button.ticker-chip {
      border: 1px solid rgba(15,118,110,0.15);
      cursor: pointer;
    }
    .ticker-chip-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .company-name {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .headline-line {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .headline-line a {
      color: #173d6d;
      text-decoration: none;
      font-weight: 700;
      line-height: 1.35;
    }
    .headline-line a:hover { text-decoration: underline; }
    .headline-sub {
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }
    .headline-meta {
      margin-top: 8px;
      display: flex;
      gap: 8px 14px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 4px 8px;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .badge.new {
      background: rgba(22, 101, 52, 0.12);
      color: var(--new);
    }
    .badge.earnings {
      background: rgba(245, 158, 11, 0.16);
      color: #9a5b00;
    }
    .badge.unstructured {
      background: rgba(148, 163, 184, 0.18);
      color: #475569;
    }
    .badge.bullish {
      background: rgba(16, 185, 129, 0.16);
      color: #047857;
    }
    .badge.bearish {
      background: rgba(239, 68, 68, 0.14);
      color: #b91c1c;
    }
    .badge.mixed {
      background: rgba(245, 158, 11, 0.16);
      color: #9a5b00;
    }
    .translate-button {
      border: 1px solid rgba(23, 61, 109, 0.18);
      border-radius: 999px;
      padding: 6px 10px;
      background: white;
      color: #173d6d;
      font-size: 11px;
      font-weight: 700;
      font-family: Arial, Helvetica, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .translate-output {
      margin-top: 10px;
      border-left: 3px solid rgba(23, 61, 109, 0.18);
      padding-left: 10px;
      color: #38506d;
      font-size: 13px;
      line-height: 1.45;
      font-family: Arial, Helvetica, sans-serif;
    }
    .translate-meta {
      margin-top: 4px;
      color: var(--muted);
      font-size: 11px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .quick-filter-row {
      margin-top: 12px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .quick-filter-label {
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .quick-filter-chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      background: white;
      color: var(--ink);
      font-size: 12px;
      font-weight: 700;
      font-family: Arial, Helvetica, sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      cursor: pointer;
    }
    .quick-filter-chip.active {
      background: rgba(245, 158, 11, 0.16);
      border-color: rgba(245, 158, 11, 0.45);
      color: #9a5b00;
    }
    .sidebar-card {
      background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(247,250,245,0.94));
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(19, 35, 58, 0.06);
    }
    .sidebar-card h3 {
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1;
      letter-spacing: -0.03em;
    }
    .eyebrow {
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .hero-meta {
      margin-top: 10px;
      display: flex;
      gap: 8px 10px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .mini-grid {
      margin-top: 16px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .mini-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.86);
    }
    .mini-card .mini-label {
      color: var(--muted);
      font-family: Arial, Helvetica, sans-serif;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.07em;
    }
    .mini-card .mini-value {
      margin-top: 8px;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    .mini-card .mini-sub {
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.4;
      font-family: Arial, Helvetica, sans-serif;
    }
    .story-list {
      margin-top: 12px;
      display: grid;
      gap: 10px;
    }
    .story-item {
      border: 1px solid #e5ebdf;
      border-radius: 16px;
      padding: 12px 14px;
      background: white;
    }
    .story-item a {
      color: #173d6d;
      text-decoration: none;
      font-weight: 700;
      line-height: 1.35;
    }
    .story-item a:hover { text-decoration: underline; }
    .story-item .story-meta {
      margin-top: 8px;
      display: flex;
      gap: 8px 12px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 12px;
      font-family: Arial, Helvetica, sans-serif;
    }
    .section-block {
      margin-top: 16px;
    }
    .section-block h3 {
      margin: 0 0 8px;
      font-size: 20px;
      letter-spacing: -0.03em;
    }
    .empty {
      color: var(--muted);
      font-size: 14px;
      margin: 8px 0 0;
    }
    @media (max-width: 900px) {
      .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-grid { grid-template-columns: 1fr; }
      .filter-grid { grid-template-columns: 1fr; }
      .mini-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="site-nav">
    <div class="site-nav-inner">
      <a class="site-link" href="/">Live Dashboard</a>
      <a class="site-link" href="/lookup">Single Ticker Tracker</a>
      <a class="site-link" href="/future-modules">Future Modules</a>
    </div>
  </div>
  <div id="root"></div>
  <script src="/static/dashboard-app.js"></script>
</body>
</html>
"""


def _load_watchlist_lookup(path: str) -> dict[str, dict[str, object]]:
    payload_path = Path(path)
    if not payload_path.exists():
        return {}
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, list):
        return {}

    lookup: dict[str, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        lookup[ticker] = {
            "company": str(item.get("company", "")).strip(),
            "keywords": [str(value).strip() for value in item.get("keywords", []) if str(value).strip()],
        }
    return lookup


def _bucket_label(bucket: str) -> str:
    return {
        "stories": "Primary",
        "related_context": "Related",
        "review_candidates": "Review",
    }.get(bucket, bucket.replace("_", " ").title())


def _serialize_lookup_rows(rows: list[dict[str, object]], bucket: str) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for row in rows:
        title = str(row.get("title", "") or "")
        summary = str(row.get("summary", "") or "")
        serialized.append(
            {
                "story_key": str(
                    row.get("canonical_link")
                    or row.get("normalized_title_key")
                    or row.get("link")
                    or row.get("title")
                    or ""
                ),
                "title": title,
                "summary": summary,
                "link": str(row.get("link", "") or ""),
                "source_name": str(row.get("source_name", "") or ""),
                "source_group": str(row.get("source_group", "") or ""),
                "event_type": str(row.get("event_type", "") or ""),
                "signal_strength": row.get("signal_strength", 0),
                "published": str(row.get("published", "") or ""),
                "published_raw": str(row.get("published_raw", "") or ""),
                "published_at": str(row.get("published_at", "") or ""),
                "published_display": str(row.get("published_display", row.get("published_raw", "")) or ""),
                "bucket": bucket,
                "bucket_label": _bucket_label(bucket),
                "needs_translation": likely_non_english(f"{title} {summary}"),
            }
        )
    return serialized


def _is_supported_equity_lookup_ticker(ticker: str) -> bool:
    normalized = str(ticker).strip().upper()
    if not normalized or not VALID_LOOKUP_TICKER_RE.fullmatch(normalized):
        return False
    if "/" in normalized or normalized.startswith("^"):
        return False
    if len(normalized) >= 6 and any(normalized.endswith(suffix) for suffix in NON_EQUITY_LOOKUP_SUFFIXES):
        return False
    return True


def create_app(app_state: DashboardState) -> Flask:
    static_dir = Path(__file__).with_name("static")
    lookup_html_path = static_dir / "lookup.html"
    future_modules_html_path = static_dir / "future_modules.html"
    app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")

    @app.get("/")
    def index() -> Response:
        return Response(APP_HTML, mimetype="text/html")

    @app.get("/lookup")
    def lookup_page() -> Response:
        return Response(lookup_html_path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/future-modules")
    def future_modules_page() -> Response:
        return Response(future_modules_html_path.read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/api/state")
    def api_state():
        return jsonify(app_state.state_payload())

    @app.post("/api/update")
    def api_update():
        ok, message = app_state.trigger_update()
        payload = {
            "ok": ok,
            "message": message,
            "cooldown_remaining": app_state.cooldown_remaining(),
        }
        return jsonify(payload), (200 if ok else 429)

    @app.get("/api/ticker/<ticker>")
    def api_ticker(ticker: str):
        payload = app_state.ticker_detail(ticker)
        if not payload:
            return jsonify({"ok": False, "message": f"No ticker detail found for {ticker}."}), 404
        return jsonify({"ok": True, **payload})

    @app.get("/api/tickers")
    def api_tickers():
        payload = app_state.ticker_universe()
        return jsonify({"ok": True, **payload})

    @app.get("/api/articles")
    def api_articles():
        payload = app_state.market_article_pool()
        return jsonify({"ok": True, **payload})

    @app.post("/api/lookup-ticker")
    def api_lookup_ticker():
        payload = request.get_json(silent=True) or {}
        ticker = str(payload.get("ticker", "") or "").strip().upper()
        company = str(payload.get("company", "") or "").strip()
        keyword_text = str(payload.get("keywords", "") or "").strip()
        if not ticker:
            return jsonify({"ok": False, "message": "Ticker symbol is required."}), 400
        if not _is_supported_equity_lookup_ticker(ticker):
            return jsonify(
                {
                    "ok": False,
                    "message": (
                        "Enter a supported equity ticker symbol. "
                        "Crypto, forex-style pairs, and malformed symbols are not enabled here."
                    ),
                }
            ), 400

        watchlist_lookup = _load_watchlist_lookup(app_state.watchlist_file)
        watchlist_defaults = watchlist_lookup.get(ticker, {})
        company = company or str(watchlist_defaults.get("company", "") or "")

        extra_keywords = [part.strip() for part in keyword_text.split(",") if part.strip()]
        if not extra_keywords:
            extra_keywords = list(watchlist_defaults.get("keywords", []) or [])

        result = collect_for_ticker(
            ticker=ticker,
            company=company,
            extra_keywords=extra_keywords,
            rss_limit=app_state.rss_limit,
            structured_limit=app_state.structured_limit,
            skip_rss=app_state.skip_rss,
            skip_structured=app_state.skip_structured,
            state_file=app_state.state_file,
            include_seen=True,
        )

        preprocessing = dict(result.get("preprocessing", {}) or {})
        stories = list(preprocessing.get("stories", []) or [])
        related_context = list(preprocessing.get("related_context", []) or [])
        review_candidates = list(preprocessing.get("review_candidates", []) or [])
        stats = dict(preprocessing.get("stats", {}) or {})
        source_usage = dict(result.get("source_usage", {}) or {})

        source_names = set()
        for source_group in source_usage.values():
            if isinstance(source_group, dict):
                source_names.update(source_group.keys())

        return jsonify(
            {
                "ok": True,
                "ticker": result.get("ticker", ticker),
                "company": company,
                "keywords": result.get("keywords", []),
                "summary": {
                    "raw_match_count": len(result.get("raw_matches", []) or []),
                    "primary_count": len(stories),
                    "related_count": len(related_context),
                    "review_count": len(review_candidates),
                    "source_count": len(source_names),
                    "duplicates_merged": int(stats.get("duplicates_merged", 0) or 0),
                },
                "source_usage": source_usage,
                "failures": result.get("failures", []),
                "stories": _serialize_lookup_rows(stories, "stories"),
                "related_context": _serialize_lookup_rows(related_context, "related_context"),
                "review_candidates": _serialize_lookup_rows(review_candidates, "review_candidates"),
            }
        )

    @app.post("/api/translate")
    def api_translate():
        payload = request.get_json(silent=True) or {}
        story_key = str(payload.get("story_key", "") or "").strip()
        title = str(payload.get("title", "") or "").strip()
        summary = str(payload.get("summary", "") or "").strip()
        if not title and not summary:
            return jsonify({"ok": False, "message": "No text was provided for translation."}), 400
        try:
            if app_state.sqlite_db:
                saved = fetch_saved_translation(
                    app_state.sqlite_db,
                    story_key=story_key,
                    title=title,
                    summary=summary,
                    target_language="en",
                )
                if saved is not None:
                    return jsonify(
                        {
                            "ok": True,
                            "source_language": saved.get("source_language", ""),
                            "target_language": saved.get("target_language", "en"),
                            "title_translated": saved.get("translated_title", "") or title,
                            "summary_translated": saved.get("translated_summary", ""),
                            "story_key": saved.get("story_key", story_key),
                            "cached": True,
                        }
                    )
            title_result = translate_text_to_english(title) if title else {"source_language": "", "translated_text": ""}
            summary_result = translate_text_to_english(summary) if summary else {"translated_text": ""}
            source_language = title_result.get("source_language", "") or summary_result.get("source_language", "")
            if app_state.sqlite_db:
                save_story_translation(
                    app_state.sqlite_db,
                    story_key=story_key,
                    title=title,
                    summary=summary,
                    source_language=source_language,
                    target_language="en",
                    translated_title=title_result.get("translated_text", ""),
                    translated_summary=summary_result.get("translated_text", ""),
                )
            return jsonify(
                {
                    "ok": True,
                    "source_language": source_language,
                    "target_language": "en",
                    "title_translated": title_result.get("translated_text", ""),
                    "summary_translated": summary_result.get("translated_text", ""),
                    "story_key": story_key,
                    "cached": False,
                }
            )
        except Exception as exc:
            return jsonify({"ok": False, "message": f"Translation request failed: {exc}"}), 502

    return app


def main() -> None:
    args = parse_args()
    state = DashboardState(
        watchlist_file=args.watchlist_file,
        snapshot_file=args.snapshot_file,
        dashboard_state_file=args.dashboard_state_file,
        cooldown_seconds=args.cooldown_seconds,
        rss_limit=args.rss_limit,
        structured_limit=args.structured_limit,
        state_file=args.state_file,
        skip_rss=args.skip_rss,
        skip_structured=args.skip_structured,
        sqlite_db=args.sqlite_db,
    )

    app = create_app(state)
    dashboard_url = f"http://{args.host}:{args.port}"
    print("Watchlist Dashboard")
    print("=" * 70)
    print(f"Open dashboard: {dashboard_url}")
    print("Framework: Flask backend + React frontend")
    print(f"Cooldown: {args.cooldown_seconds} seconds")
    print(f"Snapshot file: {args.snapshot_file}")
    print(f"Dashboard state file: {args.dashboard_state_file}")
    print("=" * 70)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
