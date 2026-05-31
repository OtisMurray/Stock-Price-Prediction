# Stock Price Prediction

Real-time stock-news intelligence dashboard for collecting, organizing, and monitoring market-related articles at scale.

The project has moved beyond the original small watchlist prototype. It now uses a Flask + React dashboard, a SQLite-backed storage layer, shared-source collection, article-pool-first feed logic, and a 50-ticker stress-test setup for backend optimization.

## Current Progress

The current phase is focused on data-source quality, ingestion reliability, deduplication, timestamp handling, and scalable backend structure before moving into sentiment and prediction layers.

What is working now:

- Flask + React dashboard for live article monitoring
- SQLite as the main backend data layer for dashboard state
- Shared-source-pool collection to reduce repeated source fetching
- 50-ticker stress-test watchlist for scaling and optimization work
- Source-health tracking and pipeline monitoring
- Article-pool-first feed with ticker filters layered on top
- Ticker workspace with relevant news, source history, and a prediction placeholder
- Timestamp tracking for:
  - `published`
  - `first captured`
  - `last observed`
  - `latest refresh capture`

What is intentionally being saved for later:

- full sentiment analysis layer
- prediction models
- true market-wide collection beyond the tracked ticker universe

## Current Architecture

The current flow is:

1. Collect shared and ticker-specific public stock-news sources
2. Normalize timestamps and article fields during ingestion
3. Match articles to tracked tickers
4. Deduplicate and cluster similar stories
5. Store refreshes, stories, observations, and source-health data in SQLite
6. Serve dashboard state through Flask API routes
7. Render the article feed and ticker workspace in the dashboard frontend

### Key Backend Ideas

- **Shared Source Pool**
  Shared sources are fetched once per refresh, then matched across the tracked ticker universe. This is the main speed improvement over the older per-ticker refetch model.

- **SQLite-Backed Dashboard**
  The dashboard no longer depends primarily on a static snapshot JSON file. SQLite is the main source of truth for the live app, while JSON can still be written as a backup/export artifact.

- **Article-Pool-First Feed**
  The main feed is organized around deduplicated articles, not repeated ticker rows. Ticker filters now act as drilldowns on top of the broader article pool.

- **Ticker Workspace**
  Clicking into a ticker opens a focused workspace for relevant coverage, source history, and a later prediction layer.

## Current Data Sources

### Shared Sources

- MarketWatch Top Stories
- MarketWatch MarketPulse
- SEC Press Releases
- PR Newswire All News Releases
- PR Newswire
- GlobeNewswire
- ACCESS Newswire
- MT Newswires
- Finviz

### Ticker-Specific Sources

- TradingView News Flow
- Stocktwits News

### Supplemental / Background Sources

- Yahoo Finance Headlines

## Repository Structure

```text
src/
  dashboard/        Flask app, dashboard state, and UI logic
  ingestion/        RSS collectors, structured collectors, source pipeline, timestamp utils
  preprocessing/    Deduplication, bucketing, event tagging, story clustering
  runners/          Main executable workflows
  storage/          SQLite read/write helpers
  other/            Convenience wrappers for common scripts
  sentiment/        Reserved for later sentiment work

data/
  watchlists/       Sample and stress-test ticker lists

assets/             Charts and supporting visuals
tmp/                Temporary working files
```

## Important Files

- `/Users/otismurray/Stock-Price-Prediction/src/dashboard/watchlist_dashboard.py`
- `/Users/otismurray/Stock-Price-Prediction/src/dashboard/dashboard_state.py`
- `/Users/otismurray/Stock-Price-Prediction/src/storage/sqlite_store.py`
- `/Users/otismurray/Stock-Price-Prediction/src/ingestion/source_pipeline.py`
- `/Users/otismurray/Stock-Price-Prediction/src/ingestion/timestamp_utils.py`
- `/Users/otismurray/Stock-Price-Prediction/src/preprocessing/news_preprocessor.py`
- `/Users/otismurray/Stock-Price-Prediction/data/watchlists/sample_watchlist.json`
- `/Users/otismurray/Stock-Price-Prediction/data/watchlists/stress_watchlist_50.json`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Optional Sentiment Dependencies

The hosted dashboard does not require the heavy sentiment stack. If you want to run later FinBERT or transformer-based sentiment experiments locally, install:

```bash
pip install -r requirements-sentiment.txt
```

## Main Commands

### Run the Current Dashboard

```bash
python3 src/other/watchlist_dashboard.py
```

Then open:

- `http://127.0.0.1:8000`

### Open It Like a Local App

Double-click:

- `/Users/otismurray/Stock-Price-Prediction/scripts/open_dashboard.command`

That launcher starts the dashboard if needed and opens it in your browser.

### Deployment-Ready Entry Point

The repo now includes a production entry point and process file:

- `/Users/otismurray/Stock-Price-Prediction/app.py`
- `/Users/otismurray/Stock-Price-Prediction/Procfile`

For platforms like Railway or Render, the app can be started with:

```bash
gunicorn app:app
```

Useful environment variables:

- `PORT`
- `HOST`
- `STOCK_DASHBOARD_DATA_ROOT`
- `STOCK_DASHBOARD_WATCHLIST_FILE`
- `STOCK_DASHBOARD_SQLITE_DB`
- `STOCK_DASHBOARD_SNAPSHOT_FILE`
- `STOCK_DASHBOARD_STATE_FILE`

For hosted deployment notes, see:

- `/Users/otismurray/Stock-Price-Prediction/DEPLOYMENT.md`

### Run the Legacy Dashboard

```bash
python3 src/other/watchlist_dashboard_legacy.py --port 8001
```

### Run a 50-Ticker Collection Snapshot

```bash
python3 src/other/collect_watchlist_snapshot.py \
  --watchlist-file data/watchlists/stress_watchlist_50.json \
  --json-out data/cache/watchlist_snapshot_50_latest.json \
  --rss-limit 0 \
  --structured-limit 0 \
  --include-seen \
  --sqlite-db data/cache/watchlist_pipeline.db
```

### Run a Single-Ticker Collection

```bash
python3 src/other/collect_all_for_ticker.py \
  --ticker AAPL \
  --company Apple \
  --keyword iphone \
  --keyword mac \
  --rss-limit 0 \
  --structured-limit 0 \
  --include-seen
```

### Audit Current Sources

```bash
python3 src/other/audit_sources.py --ticker AAPL --limit 3
```

## Current Dashboard Behavior

The current dashboard supports:

- live refresh from within the UI
- source-health summaries
- tracked ticker counts
- visible article counts
- visible source counts
- top article feed sorted by current signal
- ticker filtering and drilldown
- selected ticker workspace
- source-history display

The feed is intentionally limited to a smaller top-board style view instead of rendering every stored article at once.

## Current Scaling Direction

The system is being optimized in this order:

1. source reliability
2. ingestion speed
3. timestamp quality
4. deduplication and story identity
5. backend structure for later sentiment/prediction

The immediate goal is not “full prediction now.” The immediate goal is a stronger ingestion and storage foundation that can later support sentiment and prediction cleanly.

## Current Limitations

- collection is still based on a tracked ticker universe rather than true market-wide entity extraction
- some sources do not always expose perfect timestamps
- source-history depth depends on how many refreshes have already been stored
- retention/pruning policy is not fully implemented yet
- sentiment and prediction are intentionally deferred to a later phase

## Next Development Focus

Planned near-term work:

- continue improving deduplication and story clustering
- keep tightening timestamp normalization
- refine source-tier scheduling for faster refreshes
- improve ticker workspace presentation
- continue backend preparation for later sentiment and prediction

Later work:

- market-wide article ingestion
- entity extraction beyond a fixed tracked list
- sentiment scoring
- on-demand per-ticker prediction workflow

## Notes

- `data/cache/` and `tmp/` are ignored in Git because they contain generated snapshots, temporary files, and the local SQLite database.
- The local dashboard is meant for development and testing on `127.0.0.1`. Sharing it publicly later would require deployment rather than just sending the localhost link.
