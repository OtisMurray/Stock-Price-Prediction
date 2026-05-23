from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from typing import Any

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.dashboard.dashboard_state import DashboardState, parse_args


def _render_story_list(rows: list[dict[str, Any]], *, empty_message: str) -> str:
    if not rows:
        return f"<p class='empty'>{empty_message}</p>"

    items = []
    for row in rows:
        title = str(row.get("title", ""))
        source = str(row.get("source_name", ""))
        event_type = str(row.get("event_type", ""))
        published = str(row.get("published", ""))
        link = str(row.get("link", ""))
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


def render_dashboard_page_legacy(payload: dict[str, Any]) -> str:
    cards = []
    for item in payload.get("tickers", []):
        ticker = str(item.get("ticker", ""))
        company = str(item.get("company", ""))
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

    generated_at = str(payload.get("generated_at_display", ""))
    last_refresh_iso = str(payload.get("last_refresh_display", ""))
    last_status = str(payload.get("last_status", ""))
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
  <title>Stock News Dashboard - Legacy</title>
  <style>
    :root {{
      --bg: #f3f5ef;
      --panel: #fffdf8;
      --ink: #13233a;
      --muted: #5e6d7f;
      --line: #dbe4d2;
      --accent: #0f766e;
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
      <h1>Stock News Dashboard - Legacy</h1>
      <p class="subtitle">Original card-style watchlist view preserved for comparison.</p>
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

    async function fetchState() {{
      try {{
        const response = await fetch("/api/state");
        if (!response.ok) return;
        const payload = await response.json();
        dashboardState.cooldownRemaining = payload.cooldown_remaining || 0;
        dashboardState.updateInProgress = Boolean(payload.update_in_progress);
        dashboardState.generatedAtDisplay = payload.generated_at_display || "";
        dashboardState.lastRefreshDisplay = payload.last_refresh_display || "";
        document.getElementById("statusText").textContent = payload.last_status || "";
        document.getElementById("snapshotTime").textContent = dashboardState.generatedAtDisplay || "Not generated yet";
        document.getElementById("lastRefreshTime").textContent = dashboardState.lastRefreshDisplay || "Not refreshed yet";
        applyCooldownState();
      }} catch (error) {{
        // Keep last known state if background polling fails.
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


def make_handler_legacy(app_state: DashboardState):
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

            body = render_dashboard_page_legacy(app_state.state_payload()).encode("utf-8")
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
        sqlite_db=args.sqlite_db,
    )

    server = ThreadingHTTPServer((args.host, args.port), make_handler_legacy(state))
    dashboard_url = f"http://{args.host}:{args.port}"
    print("Watchlist Dashboard Legacy")
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
