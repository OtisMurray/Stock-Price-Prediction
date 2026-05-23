from __future__ import annotations

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from flask import Flask, jsonify, Response, request

from src.dashboard.dashboard_state import DashboardState, parse_args


APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock News Dashboard</title>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
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
      grid-template-columns: repeat(3, minmax(0, 1fr));
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
  <div id="root"></div>
  <script type="text/babel">
    const { useEffect, useMemo, useState } = React;
    const FEED_ROW_LIMIT = 20;

    function formatPipelineMode(value) {
      if (!value) return "Shared Source Pool";
      return value
        .split("_")
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }

    function formatMetaTimestamp(value) {
      if (!value) return "Not recorded";
      const normalized = String(value);
      const parsed = Date.parse(normalized);
      if (Number.isNaN(parsed)) {
        return normalized;
      }
      return new Intl.DateTimeFormat("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        timeZone: "America/New_York",
        timeZoneName: "short",
      }).format(new Date(parsed));
    }

    function getTickerStoryRows(tickerDetail) {
      if (!tickerDetail?.ticker) return [];
      const rows = [];
      const groups = [
        ["stories", "Primary"],
        ["related_context", "Related"],
        ["review_candidates", "Review"],
      ];
      groups.forEach(([bucketKey, bucketLabel]) => {
        (tickerDetail.ticker[bucketKey] || []).forEach((row, index) => {
          rows.push({
            ...row,
            bucketLabel,
            sortSignal: Number(row.signal_strength || 0),
            _key: `${bucketKey}-${row.link || row.title}-${index}`,
          });
        });
      });
      rows.sort((left, right) => (
        right.sortSignal - left.sortSignal
        || Number(right.is_new) - Number(left.is_new)
        || String(left.title || "").localeCompare(String(right.title || ""))
      ));
      return rows;
    }

    function MatchedTickerChips({ row, onSelectTicker }) {
      const matchedTickers = row.matched_tickers || String(row.ticker || "").split(", ").filter(Boolean);
      if (!matchedTickers.length) {
        return <div className="ticker-chip">-</div>;
      }
      return (
        <div className="ticker-chip-row">
          {matchedTickers.slice(0, 4).map((ticker) => (
            <button
              key={ticker}
              type="button"
              className="ticker-chip"
              onClick={() => onSelectTicker(ticker)}
            >
              {ticker}
            </button>
          ))}
          {matchedTickers.length > 4 ? <span className="ticker-chip">+{matchedTickers.length - 4}</span> : null}
        </div>
      );
    }

    function FeedTable({ rows, onSelectTicker }) {
      if (!rows.length) {
        return <p className="empty">No article rows match the current filters.</p>;
      }
      return (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Matched Tickers</th>
                <th>Headline</th>
                <th>Source</th>
                <th>Bucket</th>
                <th>Event</th>
                <th>Ticker Count</th>
                <th>Signal</th>
                <th>Published</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.ticker}-${row.link}-${index}`}>
                  <td className="col-ticker">
                    <MatchedTickerChips row={row} onSelectTicker={onSelectTicker} />
                    <div className="company-name">{row.company || `${row.matched_ticker_count || 0} matched tickers`}</div>
                  </td>
                  <td className="col-headline">
                    <div className="headline-line">
                      <a href={row.link} target="_blank" rel="noreferrer">{row.title}</a>
                      {row.is_new ? <span className="badge new">NEW</span> : null}
                    </div>
                    <div className="headline-sub">{row.summary || ""}</div>
                    <div className="headline-meta">
                      <span><strong>First captured:</strong> {formatMetaTimestamp(row.first_seen_at)}</span>
                      <span><strong>Last observed:</strong> {formatMetaTimestamp(row.last_seen_at)}</span>
                      <span><strong>Latest refresh capture:</strong> {formatMetaTimestamp(row.collected_at)}</span>
                    </div>
                  </td>
                  <td>{row.source_name}</td>
                  <td>{row.bucket_label}</td>
                  <td>{row.event_type || "general"}</td>
                  <td>{row.matched_ticker_count || 0}</td>
                  <td>{row.signal_display}</td>
                  <td>{row.published || "No timestamp available"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    function summarizeSourceHealthRows(rows) {
      const grouped = new Map();
      rows.forEach((row) => {
        const key = `${row.source_group || ""}::${row.source_key || ""}::${row.source_name || ""}`;
        if (!grouped.has(key)) {
          grouped.set(key, {
            source_group: row.source_group || "",
            source_key: row.source_key || "",
            source_name: row.source_name || "",
            runs_seen: 0,
            ok_runs: 0,
            fetched_count: 0,
            matched_count: 0,
            elapsed_total: 0,
            elapsed_max: 0,
            last_collected_at: "",
            scopes: new Set(),
            errors: new Set(),
          });
        }
        const item = grouped.get(key);
        item.runs_seen += 1;
        item.ok_runs += row.ok ? 1 : 0;
        item.fetched_count += Number(row.fetched_count || 0);
        item.matched_count += Number(row.matched_count || 0);
        item.elapsed_total += Number(row.elapsed_seconds || 0);
        item.elapsed_max = Math.max(item.elapsed_max, Number(row.elapsed_seconds || 0));
        if (row.collected_at && row.collected_at > item.last_collected_at) {
          item.last_collected_at = row.collected_at;
        }
        item.scopes.add(row.ticker ? row.ticker : "Shared pool");
        if (row.error && row.error !== "-") {
          item.errors.add(row.error);
        }
      });

      return Array.from(grouped.values())
        .map((item) => {
          const scopeValues = Array.from(item.scopes);
          const tickerScopedCount = scopeValues.filter((value) => value !== "Shared pool").length;
          let modeLabel = "Shared pool";
          if (tickerScopedCount && scopeValues.includes("Shared pool")) {
            modeLabel = "Mixed";
          } else if (tickerScopedCount) {
            modeLabel = tickerScopedCount === 1 ? "Ticker specific" : `${tickerScopedCount} ticker runs`;
          }
          return {
            ...item,
            modeLabel,
            avg_elapsed_seconds: item.runs_seen ? item.elapsed_total / item.runs_seen : 0,
            statusLabel: item.ok_runs === item.runs_seen ? "Healthy" : `${item.ok_runs}/${item.runs_seen} OK`,
            errorPreview: Array.from(item.errors).slice(0, 2).join(" | "),
          };
        })
        .sort((left, right) => (
          right.matched_count - left.matched_count
          || right.fetched_count - left.fetched_count
          || left.source_name.localeCompare(right.source_name)
        ));
    }

    function SourceHealthTable({ rows }) {
      if (!rows.length) {
        return <p className="empty">No source health rows are available for the latest refresh.</p>;
      }
      const summarizedRows = summarizeSourceHealthRows(rows);
      return (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Collection Mode</th>
                <th>Health</th>
                <th>Runs</th>
                <th>Fetched</th>
                <th>Matched</th>
                <th>Avg Elapsed</th>
                <th>Last Collected</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {summarizedRows.map((row, index) => (
                <tr key={`${row.source_key}-${index}`}>
                  <td>{row.source_name}</td>
                  <td>{row.modeLabel}</td>
                  <td>{row.statusLabel}</td>
                  <td>{row.runs_seen}</td>
                  <td>{row.fetched_count}</td>
                  <td>{row.matched_count}</td>
                  <td>{Number(row.avg_elapsed_seconds || 0).toFixed(3)}s</td>
                  <td>{row.last_collected_at || "-"}</td>
                  <td>{row.errorPreview || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    function TickerSourceHistoryTable({ rows }) {
      if (!rows.length) {
        return <p className="empty">No source history is available for this ticker yet.</p>;
      }
      return (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Runs Seen</th>
                <th>OK Runs</th>
                <th>Total Fetched</th>
                <th>Total Matched</th>
                <th>Avg Elapsed</th>
                <th>Last Collected</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.source_key}-${index}`}>
                  <td>{row.source_name}</td>
                  <td>{row.runs_seen}</td>
                  <td>{row.ok_runs}</td>
                  <td>{row.total_fetched_count}</td>
                  <td>{row.total_matched_count}</td>
                  <td>{Number(row.avg_elapsed_seconds || 0).toFixed(3)}s</td>
                  <td>{row.last_collected_at || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    function TickerCoverageTable({ tickers, onSelectTicker }) {
      return (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Company</th>
                <th>Sector</th>
                <th>Industry</th>
                <th>Primary</th>
                <th>Related</th>
                <th>Review</th>
                <th>New Primary</th>
              </tr>
            </thead>
            <tbody>
              {tickers.map((item) => (
                <tr key={item.ticker}>
                  <td>
                    <button type="button" className="ticker-chip" onClick={() => onSelectTicker(item.ticker)}>
                      {item.ticker}
                    </button>
                  </td>
                  <td>{item.company}</td>
                  <td>{item.sector || "-"}</td>
                  <td>{item.industry || "-"}</td>
                  <td>{item.stats?.clustered_story_count || 0}</td>
                  <td>{item.stats?.related_context_rows || 0}</td>
                  <td>{item.stats?.review_candidate_rows || 0}</td>
                  <td>{item.new_primary_count || 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    function TickerStoryList({ rows }) {
      if (!rows.length) {
        return <p className="empty">No relevant rows are available for this ticker yet.</p>;
      }
      return (
        <div className="story-list">
          {rows.slice(0, 6).map((row) => (
            <article className="story-item" key={row._key}>
              <div className="headline-line">
                <a href={row.link} target="_blank" rel="noreferrer">{row.title}</a>
                {row.is_new ? <span className="badge new">NEW</span> : null}
              </div>
              <div className="story-meta">
                <span><strong>Bucket:</strong> {row.bucketLabel}</span>
                <span><strong>Source:</strong> {row.source_name || "Unknown"}</span>
                <span><strong>Signal:</strong> {row.signal_strength ?? 0}</span>
                <span><strong>Published:</strong> {row.published || "Not recorded"}</span>
              </div>
            </article>
          ))}
        </div>
      );
    }

    function TickerWorkspace({ tickerDetail }) {
      if (!tickerDetail?.ticker) {
        return (
        <div className="sidebar-card">
          <div className="eyebrow">Ticker workspace</div>
          <h3>Select a ticker</h3>
          <p className="panel-subtitle">
            Click any ticker chip in the feed or ticker universe table to open a focused workspace
            for relevant news, source history, and the future prediction layer.
          </p>
        </div>
      );
      }

      const storyRows = getTickerStoryRows(tickerDetail);
      const ticker = tickerDetail.ticker;
      const summary = tickerDetail.summary || {};
      const freshness = summary.freshness || {};
      const predictionReadiness = (
        Number(summary.primary_count || 0) * 2
        + Number(summary.related_count || 0)
        + Number(summary.source_count || 0)
      );

      return (
        <div className="stack">
          <section className="sidebar-card">
            <div className="eyebrow">Selected ticker</div>
            <h3>{ticker.ticker}</h3>
            <p className="panel-subtitle">
              Focused analysis space for relevant coverage, source confirmation, and the later
              prediction workflow.
            </p>
            <div className="hero-meta">
              {ticker.company ? <span><strong>Company:</strong> {ticker.company}</span> : null}
              {ticker.sector ? <span><strong>Sector:</strong> {ticker.sector}</span> : null}
              {ticker.industry ? <span><strong>Industry:</strong> {ticker.industry}</span> : null}
            </div>

            <div className="mini-grid">
              <article className="mini-card">
                <div className="mini-label">Primary stories</div>
                <div className="mini-value">{summary.primary_count || 0}</div>
                <div className="mini-sub">Strongest rows currently attached to this ticker.</div>
              </article>
              <article className="mini-card">
                <div className="mini-label">Source coverage</div>
                <div className="mini-value">{summary.source_count || 0}</div>
                <div className="mini-sub">Distinct sources represented in the latest stored run.</div>
              </article>
              <article className="mini-card">
                <div className="mini-label">Freshness window</div>
                <div className="mini-value" style={{ fontSize: 18 }}>{freshness.last_seen_latest || "Not recorded"}</div>
                <div className="mini-sub">Latest observed article timing for this ticker.</div>
              </article>
              <article className="mini-card">
                <div className="mini-label">Prediction baseline</div>
                <div className="mini-value">{predictionReadiness}</div>
                <div className="mini-sub">
                  Readiness placeholder combining article volume and source confirmation before the
                  later sentiment and prediction layer is attached.
                </div>
              </article>
            </div>
          </section>

          <section className="sidebar-card">
            <div className="section-block" style={{ marginTop: 0 }}>
              <h3>Relevant News</h3>
              <p className="panel-subtitle">Top stored rows for the selected ticker, ordered by current signal strength.</p>
            </div>
            <TickerStoryList rows={storyRows} />
          </section>

          <section className="sidebar-card">
            <div className="section-block" style={{ marginTop: 0 }}>
              <h3>Source History</h3>
              <p className="panel-subtitle">SQLite-backed source history across refreshes for this selected ticker.</p>
            </div>
            <TickerSourceHistoryTable rows={tickerDetail.source_history || []} />
          </section>
        </div>
      );
    }

    function App() {
      const [state, setState] = useState(null);
      const [articlePool, setArticlePool] = useState(null);
      const [tickerDetail, setTickerDetail] = useState(null);
      const [filters, setFilters] = useState({
        search: "",
        ticker: "",
        sector: "",
        industry: "",
        source: "",
        bucket: "",
        eventType: "",
        sortBy: "signal",
      });
      const [updateError, setUpdateError] = useState("");

      useEffect(() => {
        let mounted = true;

        const fetchState = async () => {
          try {
            const response = await fetch("/api/state");
            if (!response.ok) return;
            const payload = await response.json();
            if (mounted) setState(payload);
          } catch (error) {
            if (mounted) setUpdateError(`State refresh failed: ${error}`);
          }
        };

        const fetchArticlePool = async () => {
          try {
            const response = await fetch("/api/articles");
            if (!response.ok) return;
            const payload = await response.json();
            if (mounted) setArticlePool(payload);
          } catch (error) {
            if (mounted) setUpdateError(`Article pool refresh failed: ${error}`);
          }
        };

        fetchState();
        fetchArticlePool();
        const interval = setInterval(fetchState, 10000);
        const cooldown = setInterval(() => {
          setState((current) => {
            if (!current || current.update_in_progress || current.cooldown_remaining <= 0) {
              return current;
            }
            return { ...current, cooldown_remaining: current.cooldown_remaining - 1 };
          });
        }, 1000);

        return () => {
          mounted = false;
          clearInterval(interval);
          clearInterval(cooldown);
        };
      }, []);

      useEffect(() => {
        let mounted = true;
        const fetchTickerDetail = async () => {
          if (!filters.ticker) {
            if (mounted) setTickerDetail(null);
            return;
          }
          try {
            const response = await fetch(`/api/ticker/${filters.ticker}`);
            const payload = await response.json();
            if (mounted) {
              setTickerDetail(response.ok ? payload : null);
            }
          } catch (error) {
            if (mounted) setUpdateError(`Ticker detail failed: ${error}`);
          }
        };
        fetchTickerDetail();
        return () => {
          mounted = false;
        };
      }, [filters.ticker]);

      const visibleRows = useMemo(() => {
        if (!state) return [];
        const tickerMeta = Object.fromEntries((state.tickers || []).map((item) => [item.ticker, item]));
        const marketRows = (articlePool?.articles || []).map((row) => {
          const matchedTickers = row.matched_tickers || [];
          const primaryTicker = tickerMeta[matchedTickers[0]] || {};
          return {
            ticker: matchedTickers.join(", "),
            company: matchedTickers.length === 1 ? (primaryTicker.company || "") : `${matchedTickers.length} matched tickers`,
            sector: matchedTickers.length === 1 ? (primaryTicker.sector || "") : "",
            industry: matchedTickers.length === 1 ? (primaryTicker.industry || "") : "",
            bucket_label: (row.buckets || []).join(", ") || "Primary",
            title: row.title,
            link: row.link,
            source_name: row.source_name,
            event_type: (row.event_types || [])[0] || "",
            signal_strength: Number(row.signal_strength || 0),
            signal_display: String(row.signal_strength ?? 0),
            published: row.published_display || row.published_raw || row.published_at || "",
            published_raw: row.published_raw || "",
            published_at: row.published_at || "",
            first_seen_at: row.first_seen_at || "",
            last_seen_at: row.last_seen_at || "",
            collected_at: row.collected_at || "",
            coverage_count: row.coverage_count || 0,
            summary: matchedTickers.length ? `Matched tickers: ${matchedTickers.join(", ")}` : "",
            is_new: Boolean(row.is_new),
            matched_tickers: matchedTickers,
            matched_ticker_count: row.matched_ticker_count || 0,
          };
        });

        const sourceRows = marketRows.length ? marketRows : (state.feed_rows || []);
        const filteredRows = sourceRows.filter((row) => {
          const searchBlob = [
            row.ticker,
            row.company,
            row.industry,
            row.source_name,
            row.event_type,
            row.title,
          ].join(" ").toLowerCase();
          const matchedTickers = row.matched_tickers || String(row.ticker || "").split(", ").filter(Boolean);
          return (
            (!filters.search || searchBlob.includes(filters.search.toLowerCase())) &&
            (!filters.ticker || matchedTickers.includes(filters.ticker)) &&
            (!filters.sector || row.sector === filters.sector) &&
            (!filters.industry || row.industry === filters.industry) &&
            (!filters.source || row.source_name === filters.source) &&
            (!filters.bucket || row.bucket_label === filters.bucket) &&
            (!filters.eventType || row.event_type === filters.eventType)
          );
        });
        const rows = [...filteredRows];
        rows.sort((left, right) => {
          if (filters.sortBy === "ticker") {
            return left.ticker.localeCompare(right.ticker) || right.signal_strength - left.signal_strength;
          }
          if (filters.sortBy === "source") {
            return left.source_name.localeCompare(right.source_name) || right.signal_strength - left.signal_strength;
          }
          if (filters.sortBy === "new") {
            return Number(right.is_new) - Number(left.is_new) || right.signal_strength - left.signal_strength;
          }
          return right.signal_strength - left.signal_strength
            || Number(right.is_new) - Number(left.is_new)
            || left.ticker.localeCompare(right.ticker);
        });
        return rows;
      }, [state, filters]);

      const articleSummary = useMemo(() => {
        const rows = visibleRows;
        return {
          totalRows: rows.length,
          newRows: rows.filter((row) => row.is_new).length,
          sourceCount: new Set(rows.map((row) => row.source_name).filter(Boolean)).size,
        };
      }, [visibleRows]);

      const displayedRows = useMemo(
        () => visibleRows.slice(0, FEED_ROW_LIMIT),
        [visibleRows]
      );

      const triggerUpdate = async () => {
        if (!state || state.update_in_progress || state.cooldown_remaining > 0) return;
        setUpdateError("");
        setState({ ...state, update_in_progress: true });
        try {
          const response = await fetch("/api/update", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          const payload = await response.json();
          if (!response.ok) {
            setUpdateError(payload.message || "Refresh failed.");
            setState((current) => current ? {
              ...current,
              update_in_progress: false,
              cooldown_remaining: payload.cooldown_remaining || current.cooldown_remaining || 0,
              last_status: payload.message || current.last_status,
            } : current);
            return;
          }
          const [nextState, nextArticles] = await Promise.all([
            fetch("/api/state").then((res) => res.json()),
            fetch("/api/articles").then((res) => res.json()),
          ]);
          setState(nextState);
          setArticlePool(nextArticles);
          if (filters.ticker) {
            const nextTickerDetail = await fetch(`/api/ticker/${filters.ticker}`).then((res) => res.json());
            setTickerDetail(nextTickerDetail.ok ? nextTickerDetail : null);
          }
          window.setTimeout(() => {
            window.location.reload();
          }, 150);
        } catch (error) {
          setUpdateError(`Refresh failed: ${error}`);
          setState((current) => current ? { ...current, update_in_progress: false } : current);
        }
      };

      if (!state) {
        return (
          <div className="wrap">
            <section className="hero">
              <h1>Stock News Dashboard</h1>
              <p className="subtitle">Loading Flask + React dashboard state...</p>
            </section>
          </div>
        );
      }

      const buttonDisabled = state.update_in_progress || state.cooldown_remaining > 0;
      const buttonText = state.update_in_progress
        ? "Updating..."
        : state.cooldown_remaining > 0
          ? `Update locked (${state.cooldown_remaining}s)`
          : "Update Watchlist";

      const setFilter = (name, value) => setFilters((current) => ({ ...current, [name]: value }));
      const selectTicker = (ticker) => setFilters((current) => ({ ...current, ticker: ticker || "" }));
      const clearFilters = () => setFilters({
        search: "",
        ticker: "",
        sector: "",
        industry: "",
        source: "",
        bucket: "",
        eventType: "",
        sortBy: "signal",
      });

      return (
        <div className="wrap">
          <section className="hero">
            <h1>Stock News Dashboard</h1>
            <p className="subtitle">Live stock-news feed powered by Flask, React, and SQLite.</p>
            <div className="toolbar">
              <button onClick={triggerUpdate} disabled={buttonDisabled}>{buttonText}</button>
              <div className="meta">
                <span><strong>Snapshot:</strong> {state.generated_at_display || "Not generated yet"}</span>
                <span><strong>Last manual refresh:</strong> {state.last_refresh_display || "Not refreshed yet"}</span>
              </div>
            </div>
            <p className="status">{updateError || state.last_status}</p>
            <section className="detail-grid">
              <article className="detail-card">
                <div className="detail-label">Pipeline Mode</div>
                <div className="detail-value">{formatPipelineMode(state.pipeline_mode)}</div>
                <div className="detail-sub">Collection architecture used for the latest refresh.</div>
              </article>
              <article className="detail-card">
                <div className="detail-label">Collection Elapsed</div>
                <div className="detail-value">{Number(state.collection_elapsed_seconds || 0).toFixed(3)}s</div>
                <div className="detail-sub">Time recorded for the latest pipeline refresh.</div>
              </article>
              <article className="detail-card">
                <div className="detail-label">Source Health</div>
                <div className="detail-value">{state.summary?.source_health_ok || 0}/{state.summary?.source_health_total || 0}</div>
                <div className="detail-sub">Successful source checks recorded in the latest refresh.</div>
              </article>
            </section>
          </section>

          <section className="summary-grid">
            <article className="summary-card">
              <div className="label">Tracked Tickers</div>
              <div className="value">{state.summary?.ticker_count || 0}</div>
            </article>
            <article className="summary-card">
              <div className="label">Visible Articles</div>
              <div className="value">{articleSummary.totalRows || 0}</div>
            </article>
            <article className="summary-card">
              <div className="label">New Since Refresh</div>
              <div className="value">{articleSummary.newRows || 0}</div>
            </article>
            <article className="summary-card">
              <div className="label">Visible Sources</div>
              <div className="value">{articleSummary.sourceCount || 0}</div>
            </article>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Article Feed</h2>
                <p className="panel-subtitle">Deduped market article stream built from the latest SQLite-backed article pool, with ticker filters layered on top.</p>
              </div>
            </div>

            <div className="filter-grid">
              <div className="filter-field">
                <label>Headline Search</label>
                <input value={filters.search} onChange={(e) => setFilter("search", e.target.value)} placeholder="Search ticker, company, source, or event" />
              </div>
              <div className="filter-field">
                <label>Ticker</label>
                <select value={filters.ticker} onChange={(e) => setFilter("ticker", e.target.value)}>
                  <option value="">All tickers</option>
                  {(state.filters?.tickers || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-field">
                <label>Sector</label>
                <select value={filters.sector} onChange={(e) => setFilter("sector", e.target.value)}>
                  <option value="">All sectors</option>
                  {(state.filters?.sectors || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-field">
                <label>Industry</label>
                <select value={filters.industry} onChange={(e) => setFilter("industry", e.target.value)}>
                  <option value="">All industries</option>
                  {(state.filters?.industries || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-field">
                <label>Source</label>
                <select value={filters.source} onChange={(e) => setFilter("source", e.target.value)}>
                  <option value="">All sources</option>
                  {(state.filters?.sources || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-field">
                <label>Bucket</label>
                <select value={filters.bucket} onChange={(e) => setFilter("bucket", e.target.value)}>
                  <option value="">All buckets</option>
                  {(state.filters?.buckets || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
            </div>

            <div className="filter-grid" style={{ marginTop: 12, gridTemplateColumns: "1fr 1fr 1fr auto" }}>
              <div className="filter-field">
                <label>Event Type</label>
                <select value={filters.eventType} onChange={(e) => setFilter("eventType", e.target.value)}>
                  <option value="">All event types</option>
                  {(state.filters?.event_types || []).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </div>
              <div className="filter-field">
                <label>Sort By</label>
                <select value={filters.sortBy} onChange={(e) => setFilter("sortBy", e.target.value)}>
                  <option value="signal">Signal (high to low)</option>
                  <option value="new">New first</option>
                  <option value="ticker">Ticker A-Z</option>
                  <option value="source">Source A-Z</option>
                </select>
              </div>
              <div className="table-note">
                Showing top {Math.min(displayedRows.length, FEED_ROW_LIMIT)} of {visibleRows.length} rows by current sort order
              </div>
              <div className="filter-actions">
                <button className="secondary" type="button" onClick={clearFilters}>Clear Filters</button>
              </div>
            </div>

            <FeedTable rows={displayedRows} onSelectTicker={selectTicker} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Ticker Workspace</h2>
                <p className="panel-subtitle">Focused analysis area for the selected ticker, built to hold relevant news, source history, and the later prediction layer.</p>
              </div>
            </div>
            <TickerWorkspace tickerDetail={tickerDetail} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Ticker Universe</h2>
                <p className="panel-subtitle">Click any tracked ticker to open a focused workspace for relevant stories, source history, and later prediction output.</p>
              </div>
            </div>
            <TickerCoverageTable tickers={state.tickers || []} onSelectTicker={selectTicker} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>System Monitoring</h2>
                <p className="panel-subtitle">High-level source monitoring summary for the latest refresh, grouped by source so ticker-specific and shared-pool collectors are easier to compare.</p>
              </div>
            </div>
            <SourceHealthTable rows={state.source_health || []} />
          </section>

        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById("root")).render(<App />);
  </script>
</body>
</html>
"""


def create_app(app_state: DashboardState) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index() -> Response:
        return Response(APP_HTML, mimetype="text/html")

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
