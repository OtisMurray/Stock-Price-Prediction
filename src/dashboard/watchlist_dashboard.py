from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.runners.collect_watchlist_snapshot import build_watchlist_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Serve a simple local dashboard for the watchlist news pipeline with a manual "
            "update button and a server-side cooldown."
        )
    )
    parser.add_argument(
        "--watchlist-file",
        default="data/watchlists/sample_watchlist.json",
        help="JSON watchlist file containing ticker, company, and keyword entries.",
    )
    parser.add_argument(
        "--snapshot-file",
        default="data/cache/watchlist_snapshot.json",
        help="Path to the latest watchlist snapshot JSON file.",
    )
    parser.add_argument(
        "--dashboard-state-file",
        default="tmp/dashboard_state.json",
        help="Path to the dashboard state JSON file.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface for the dashboard server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the dashboard server.",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=int,
        default=120,
        help="Minimum number of seconds between manual refreshes.",
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
        "--skip-rss",
        action="store_true",
        help="Skip the baseline RSS sources.",
    )
    parser.add_argument(
        "--skip-structured",
        action="store_true",
        help="Skip the structured sources.",
    )
    return parser.parse_args()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


EASTERN_TZ = ZoneInfo("America/New_York")


def _format_eastern_time(iso_text: str) -> str:
    if not iso_text:
        return ""
    normalized = iso_text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return iso_text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    eastern = dt.astimezone(EASTERN_TZ)
    return eastern.strftime("%Y-%m-%d %I:%M:%S %p ET")


def _story_id(ticker: str, row: dict[str, Any]) -> str:
    base = (
        str(row.get("canonical_link", "")),
        str(row.get("normalized_title_key", "")),
        str(row.get("link", "")),
        str(row.get("title", "")),
    )
    compact = next((part for part in base if part), "")
    return f"{ticker}::{compact}"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class DashboardState:
    def __init__(
        self,
        *,
        watchlist_file: str,
        snapshot_file: str,
        dashboard_state_file: str,
        cooldown_seconds: int,
        rss_limit: int,
        structured_limit: int,
        state_file: str,
        skip_rss: bool,
        skip_structured: bool,
    ) -> None:
        self.watchlist_file = watchlist_file
        self.snapshot_path = Path(snapshot_file)
        self.dashboard_state_path = Path(dashboard_state_file)
        self.cooldown_seconds = cooldown_seconds
        self.rss_limit = rss_limit
        self.structured_limit = structured_limit
        self.state_file = state_file
        self.skip_rss = skip_rss
        self.skip_structured = skip_structured
        self.lock = threading.Lock()
        self.update_in_progress = False

        self.snapshot: dict[str, Any] = _load_json(self.snapshot_path, {})
        persisted = _load_json(
            self.dashboard_state_path,
            {
                "last_refresh_epoch": 0.0,
                "last_refresh_iso": "",
                "seen_story_ids": [],
                "last_status": "Dashboard initialized.",
            },
        )
        self.last_refresh_epoch = float(persisted.get("last_refresh_epoch", 0.0) or 0.0)
        self.last_refresh_iso = str(persisted.get("last_refresh_iso", ""))
        self.seen_story_ids = set(str(item) for item in persisted.get("seen_story_ids", []))
        self.last_status = str(persisted.get("last_status", "Dashboard initialized."))

        if not self.snapshot:
            self.snapshot = self._run_snapshot_update(mark_all_seen=False)
            self.last_status = "Initial snapshot created for dashboard startup."
            self._persist_state()

    def _persist_state(self) -> None:
        self.dashboard_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_refresh_epoch": self.last_refresh_epoch,
            "last_refresh_iso": self.last_refresh_iso,
            "seen_story_ids": sorted(self.seen_story_ids),
            "last_status": self.last_status,
        }
        self.dashboard_state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _annotate_snapshot(self, snapshot: dict[str, Any], *, mark_all_seen: bool) -> dict[str, Any]:
        snapshot = dict(snapshot)
        annotated_tickers: list[dict[str, Any]] = []
        newly_seen_ids: set[str] = set()

        for ticker_payload in snapshot.get("tickers", []):
            ticker_copy = dict(ticker_payload)
            ticker = str(ticker_copy.get("ticker", ""))
            new_primary_count = 0

            annotated_stories = []
            for row in ticker_copy.get("stories", []):
                story_copy = dict(row)
                story_id = _story_id(ticker, story_copy)
                is_new = story_id not in self.seen_story_ids
                story_copy["story_id"] = story_id
                story_copy["is_new"] = is_new
                annotated_stories.append(story_copy)
                if is_new:
                    new_primary_count += 1
                    newly_seen_ids.add(story_id)

            annotated_related = []
            for row in ticker_copy.get("related_context", []):
                row_copy = dict(row)
                story_id = _story_id(ticker, row_copy)
                is_new = story_id not in self.seen_story_ids
                row_copy["story_id"] = story_id
                row_copy["is_new"] = is_new
                annotated_related.append(row_copy)
                if is_new:
                    newly_seen_ids.add(story_id)

            annotated_review = []
            for row in ticker_copy.get("review_candidates", []):
                row_copy = dict(row)
                story_id = _story_id(ticker, row_copy)
                is_new = story_id not in self.seen_story_ids
                row_copy["story_id"] = story_id
                row_copy["is_new"] = is_new
                annotated_review.append(row_copy)
                if is_new:
                    newly_seen_ids.add(story_id)

            ticker_copy["stories"] = annotated_stories
            ticker_copy["related_context"] = annotated_related
            ticker_copy["review_candidates"] = annotated_review
            ticker_copy["new_primary_count"] = new_primary_count
            annotated_tickers.append(ticker_copy)

        snapshot["tickers"] = annotated_tickers
        if mark_all_seen:
            for ticker_payload in annotated_tickers:
                ticker = str(ticker_payload.get("ticker", ""))
                for bucket in ("stories", "related_context", "review_candidates"):
                    for row in ticker_payload.get(bucket, []):
                        self.seen_story_ids.add(_story_id(ticker, row))
        else:
            self.seen_story_ids.update(newly_seen_ids)
        return snapshot

    def _run_snapshot_update(self, *, mark_all_seen: bool) -> dict[str, Any]:
        snapshot = build_watchlist_snapshot(
            watchlist_file=self.watchlist_file,
            rss_limit=self.rss_limit,
            structured_limit=self.structured_limit,
            state_file=self.state_file,
            include_seen=False,
            skip_rss=self.skip_rss,
            skip_structured=self.skip_structured,
        )
        annotated = self._annotate_snapshot(snapshot, mark_all_seen=mark_all_seen)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(annotated, indent=2), encoding="utf-8")
        return annotated

    def cooldown_remaining(self) -> int:
        elapsed = time.time() - self.last_refresh_epoch
        remaining = self.cooldown_seconds - int(elapsed)
        return max(0, remaining)

    def state_payload(self) -> dict[str, Any]:
        snapshot = self.snapshot or {"tickers": []}
        return {
            "generated_at": snapshot.get("generated_at", ""),
            "generated_at_display": _format_eastern_time(str(snapshot.get("generated_at", ""))),
            "last_refresh_iso": self.last_refresh_iso,
            "last_refresh_display": _format_eastern_time(self.last_refresh_iso),
            "last_status": self.last_status,
            "cooldown_seconds": self.cooldown_seconds,
            "cooldown_remaining": self.cooldown_remaining(),
            "update_in_progress": self.update_in_progress,
            "tickers": snapshot.get("tickers", []),
        }

    def trigger_update(self) -> tuple[bool, str]:
        with self.lock:
            if self.update_in_progress:
                return False, "Update already in progress."
            remaining = self.cooldown_remaining()
            if remaining > 0:
                return False, f"Update locked for {remaining} more seconds."
            self.update_in_progress = True

        try:
            updated = self._run_snapshot_update(mark_all_seen=False)
            self.snapshot = updated
            self.last_refresh_epoch = time.time()
            self.last_refresh_iso = _iso_now()
            new_primary_total = sum(int(item.get("new_primary_count", 0)) for item in updated.get("tickers", []))
            self.last_status = (
                f"Watchlist refreshed successfully at {_format_eastern_time(self.last_refresh_iso)}. "
                f"New primary stories found: {new_primary_total}."
            )
            self._persist_state()
            return True, self.last_status
        except Exception as exc:
            self.last_status = f"Update failed: {exc}"
            self._persist_state()
            return False, self.last_status
        finally:
            with self.lock:
                self.update_in_progress = False


def _render_story_list(rows: list[dict[str, Any]], *, empty_message: str) -> str:
    if not rows:
        return f"<p class='empty'>{html.escape(empty_message)}</p>"

    items = []
    for row in rows:
        title = html.escape(str(row.get("title", "")))
        source = html.escape(str(row.get("source_name", "")))
        event_type = html.escape(str(row.get("event_type", "")))
        published = html.escape(str(row.get("published", "")))
        link = html.escape(str(row.get("link", "")))
        badge = "<span class='badge new'>NEW</span>" if row.get("is_new") else ""
        signal = row.get("signal_strength", "")
        items.append(
            f"""
            <article class="story">
              <div class="story-top">
                <a href="{link}" target="_blank" rel="noreferrer">{title}</a>
                {badge}
              </div>
              <div class="story-meta">
                <span>{source}</span>
                <span>{event_type}</span>
                <span>Signal {signal}</span>
              </div>
              <div class="story-time">{published or "No timestamp available"}</div>
            </article>
            """
        )
    return "".join(items)


def render_dashboard_page(payload: dict[str, Any]) -> str:
    cards = []
    for item in payload.get("tickers", []):
        ticker = html.escape(str(item.get("ticker", "")))
        company = html.escape(str(item.get("company", "")))
        stats = item.get("stats", {})
        primary_count = stats.get("clustered_story_count", 0)
        related_count = stats.get("related_context_rows", 0)
        review_count = stats.get("review_candidate_rows", 0)
        new_primary_count = item.get("new_primary_count", 0)

        cards.append(
            f"""
            <section class="ticker-card">
              <div class="card-head">
                <div>
                  <h2>{ticker}</h2>
                  <p>{company}</p>
                </div>
                <div class="counts">
                  <span>{primary_count} primary</span>
                  <span>{related_count} related</span>
                  <span>{review_count} review</span>
                  <span>{new_primary_count} new</span>
                </div>
              </div>
              <div class="bucket">
                <h3>Primary Stories</h3>
                {_render_story_list(item.get("stories", []), empty_message="No primary stories right now.")}
              </div>
              <details class="bucket details">
                <summary>Related Context ({related_count})</summary>
                {_render_story_list(item.get("related_context", []), empty_message="No related-context stories right now.")}
              </details>
              <details class="bucket details">
                <summary>Review Candidates ({review_count})</summary>
                {_render_story_list(item.get("review_candidates", []), empty_message="No review candidates right now.")}
              </details>
            </section>
            """
        )

    generated_at = html.escape(str(payload.get("generated_at_display", "")))
    last_refresh_iso = html.escape(str(payload.get("last_refresh_display", "")))
    last_status = html.escape(str(payload.get("last_status", "")))
    cooldown_remaining = int(payload.get("cooldown_remaining", 0) or 0)
    disabled_attr = "disabled" if cooldown_remaining > 0 or payload.get("update_in_progress") else ""
    button_text = (
        f"Update locked ({cooldown_remaining}s)"
        if cooldown_remaining > 0
        else ("Updating..." if payload.get("update_in_progress") else "Update Watchlist")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock News Dashboard</title>
  <style>
    :root {{
      --bg: #f3f5ef;
      --panel: #fffdf8;
      --ink: #13233a;
      --muted: #5e6d7f;
      --line: #dbe4d2;
      --accent: #0f766e;
      --accent-2: #d97706;
      --new: #166534;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.08), transparent 28%),
        linear-gradient(180deg, #f9faf7 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .wrap {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }}
    .hero {{
      background: rgba(255,255,255,0.84);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 24px;
      box-shadow: 0 18px 40px rgba(19, 35, 58, 0.08);
      backdrop-filter: blur(6px);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 42px;
      line-height: 1;
      letter-spacing: -0.03em;
    }}
    .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: 18px;
    }}
    .toolbar {{
      margin-top: 22px;
      display: flex;
      gap: 16px;
      align-items: center;
      flex-wrap: wrap;
    }}
    button {{
      border: none;
      border-radius: 999px;
      padding: 14px 22px;
      background: linear-gradient(135deg, var(--accent), #0b4f69);
      color: white;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{
      cursor: not-allowed;
      opacity: 0.65;
    }}
    .meta {{
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 14px;
    }}
    .status {{
      margin-top: 14px;
      font-size: 15px;
      color: #27445d;
    }}
    .grid {{
      margin-top: 24px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }}
    .ticker-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(19, 35, 58, 0.06);
    }}
    .card-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
      margin-bottom: 14px;
    }}
    .card-head h2 {{
      margin: 0;
      font-size: 28px;
      letter-spacing: -0.03em;
    }}
    .card-head p {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 15px;
    }}
    .counts {{
      display: flex;
      flex-direction: column;
      gap: 6px;
      text-align: right;
      font-size: 13px;
      color: var(--muted);
    }}
    .bucket {{
      margin-top: 14px;
    }}
    .bucket h3, .details summary {{
      margin: 0 0 10px;
      font-size: 16px;
      font-family: Arial, Helvetica, sans-serif;
      color: #17324a;
    }}
    .details summary {{
      cursor: pointer;
    }}
    .story {{
      padding: 12px 0;
      border-top: 1px solid #edf1e7;
    }}
    .story:first-child {{
      border-top: none;
      padding-top: 0;
    }}
    .story-top {{
      display: flex;
      gap: 10px;
      align-items: start;
      flex-wrap: wrap;
    }}
    .story a {{
      color: #173d6d;
      text-decoration: none;
      font-weight: 700;
      line-height: 1.35;
    }}
    .story a:hover {{
      text-decoration: underline;
    }}
    .story-meta, .story-time {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 4px 8px;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    .badge.new {{
      background: rgba(22, 101, 52, 0.12);
      color: var(--new);
    }}
    .empty {{
      color: var(--muted);
      font-size: 14px;
      margin: 8px 0 0;
    }}
    @media (max-width: 720px) {{
      h1 {{ font-size: 34px; }}
      .card-head {{ flex-direction: column; }}
      .counts {{ text-align: left; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Stock News Dashboard</h1>
      <p class="subtitle">Watchlist headlines with manual refresh, 2-minute cooldown, and new-story tracking.</p>
      <div class="toolbar">
        <button id="updateButton" {disabled_attr}>{button_text}</button>
        <div class="meta">
          <span><strong>Snapshot:</strong> <span id="snapshotTime">{generated_at or "Not generated yet"}</span></span>
          <span><strong>Last manual refresh:</strong> <span id="lastRefreshTime">{last_refresh_iso or "Not refreshed yet"}</span></span>
        </div>
      </div>
      <p class="status" id="statusText">{last_status}</p>
    </section>
    <section class="grid">
      {''.join(cards)}
    </section>
  </div>
  <script>
    const dashboardState = {{
      cooldownRemaining: {cooldown_remaining},
      updateInProgress: {str(bool(payload.get("update_in_progress"))).lower()},
      generatedAtDisplay: {json.dumps(str(payload.get("generated_at_display", "")))},
      lastRefreshDisplay: {json.dumps(str(payload.get("last_refresh_display", "")))}
    }};

    function applyCooldownState() {{
      const button = document.getElementById("updateButton");
      if (dashboardState.updateInProgress) {{
        button.disabled = true;
        button.textContent = "Updating...";
        return;
      }}
      if (dashboardState.cooldownRemaining > 0) {{
        button.disabled = true;
        button.textContent = `Update locked (${{dashboardState.cooldownRemaining}}s)`;
        return;
      }}
      button.disabled = false;
      button.textContent = "Update Watchlist";
    }}

    function applyServerState(payload) {{
      dashboardState.cooldownRemaining = payload.cooldown_remaining || 0;
      dashboardState.updateInProgress = Boolean(payload.update_in_progress);
      dashboardState.generatedAtDisplay = payload.generated_at_display || dashboardState.generatedAtDisplay || "";
      dashboardState.lastRefreshDisplay = payload.last_refresh_display || dashboardState.lastRefreshDisplay || "";
      document.getElementById("statusText").textContent = payload.last_status || "";
      document.getElementById("snapshotTime").textContent = dashboardState.generatedAtDisplay || "Not generated yet";
      document.getElementById("lastRefreshTime").textContent = dashboardState.lastRefreshDisplay || "Not refreshed yet";
      applyCooldownState();
    }}

    async function fetchState() {{
      try {{
        const response = await fetch("/api/state");
        if (!response.ok) return;
        const payload = await response.json();
        applyServerState(payload);
      }} catch (error) {{
        // Ignore background polling errors and keep the last known state.
      }}
    }}

    async function triggerUpdate() {{
      const button = document.getElementById("updateButton");
      const status = document.getElementById("statusText");
      if (dashboardState.cooldownRemaining > 0 || dashboardState.updateInProgress) {{
        applyCooldownState();
        status.textContent = `Please wait ${{dashboardState.cooldownRemaining}}s before refreshing again.`;
        return;
      }}
      dashboardState.updateInProgress = true;
      button.disabled = true;
      button.textContent = "Updating...";
      status.textContent = "Refreshing watchlist...";

      try {{
        const response = await fetch("/api/update", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{}})
        }});
        const payload = await response.json();
        status.textContent = payload.message || "Refresh completed.";
        if (payload.ok) {{
          window.location.reload();
        }} else {{
          dashboardState.updateInProgress = false;
          dashboardState.cooldownRemaining = payload.cooldown_remaining || 0;
          applyCooldownState();
        }}
      }} catch (error) {{
        dashboardState.updateInProgress = false;
        status.textContent = "Refresh failed: " + error;
        applyCooldownState();
      }}
    }}
    document.getElementById("updateButton").addEventListener("click", triggerUpdate);
    applyCooldownState();
    setInterval(() => {{
      if (!dashboardState.updateInProgress && dashboardState.cooldownRemaining > 0) {{
        dashboardState.cooldownRemaining -= 1;
        applyCooldownState();
      }}
    }}, 1000);
    setInterval(fetchState, 10000);
  </script>
</body>
</html>
"""


def make_handler(app_state: DashboardState):
    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/state":
                self._send_json(app_state.state_payload())
                return

            if self.path != "/":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            body = render_dashboard_page(app_state.state_payload()).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/update":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            ok, message = app_state.trigger_update()
            status = HTTPStatus.OK if ok else HTTPStatus.TOO_MANY_REQUESTS
            self._send_json(
                {
                    "ok": ok,
                    "message": message,
                    "cooldown_remaining": app_state.cooldown_remaining(),
                },
                status=int(status),
            )

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


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
    )

    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    dashboard_url = f"http://{args.host}:{args.port}"
    print("Watchlist Dashboard")
    print("=" * 70)
    print(f"Open dashboard: {dashboard_url}")
    print(f"Cooldown: {args.cooldown_seconds} seconds")
    print(f"Snapshot file: {args.snapshot_file}")
    print(f"Dashboard state file: {args.dashboard_state_file}")
    print("=" * 70)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
