# Stock Price Prediction

Stock Price Prediction is a project focused on forecasting stock price movement by combining historical market data with financial news sentiment.

The project is being built as a modular pipeline rather than a single script. The goal is to collect price data and financial news, normalize those inputs, score relevant articles with sentiment analysis, engineer useful predictive features, and compare price-only predictions against price-plus-sentiment predictions inside a dashboard.

## Goals

- collect historical stock price data
- collect and normalize financial news from multiple sources
- filter articles by ticker relevance
- transform news coverage into sentiment-based features
- combine price and sentiment data in a prediction model
- present forecasts and supporting insights in a clear dashboard

## Intended Product

The intended final product is a stock-focused dashboard that can refresh on a short cycle and compare multiple views of the same ticker.

The long-term target is to support:

1. a defined watchlist of stocks
2. continuous collection of new articles and recent market data
3. sentiment scoring of relevant articles
4. feature generation from both prices and news
5. at least two predictive paths:
   - price-only prediction
   - price-plus-sentiment prediction
6. dashboard outputs that show recent articles, sentiment state, and model outputs together

## Current Development Path

The project is being built in layers so each part can be tested independently before the full system is connected.

The current intended path is:

1. build the ingestion layer first
2. make ingestion save structured article outputs
3. add deduplication and short-term caching
4. turn sentiment scoring into its own reusable module
5. add historical price ingestion
6. engineer combined price and sentiment features
7. build prediction scripts
8. connect the outputs to a dashboard

This means the system is not being treated as one large file. Each script is intended to own one clear responsibility.

## Current Repository Structure

```text
Stock-Price-Prediction/
├── README.md
├── requirements.txt
├── .gitignore
├── assets/
├── data/
│   ├── cache/
│   └── watchlists/
├── src/
│   ├── __init__.py
│   ├── ingestion/
│   ├── preprocessing/
│   ├── runners/
│   ├── sentiment/
│   └── other/
└── tmp/
```

Main package roles:

- `src/ingestion/`
  - source definitions and collectors
- `src/preprocessing/`
  - relevance scoring, filtering, duplicate clustering, event tagging
- `src/runners/`
  - real CLI-style scripts for ticker collection, watchlist snapshots, polling, and reporting
- `src/sentiment/`
  - early sentiment demo code only; sentiment is not the current focus
- `src/other/`
  - compatibility wrappers so older commands still work

## Environment Setup

Before running any project scripts, create and activate a virtual environment in the project root and install the current requirements.

Create the virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install the current requirements:

```bash
pip install -r requirements.txt
```

Quick startup sequence from the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

After the environment is active, the current main commands are:

```bash
python3 src/other/collect_all_for_ticker.py --ticker AAPL --company Apple --keyword iphone --keyword mac --structured-limit 0 --rss-limit 0 --include-seen
python3 src/other/collect_watchlist_snapshot.py --watchlist-file data/watchlists/sample_watchlist.json --json-out data/cache/watchlist_snapshot.json --structured-limit 0 --rss-limit 0 --include-seen
python3 src/other/summarize_source_usage.py --json-file data/cache/watchlist_snapshot.json
```

## Current Scripts And How They Connect

This section documents the current working process rather than the original early prototype plan.

### Current Data Sources

The current source layer is split into two groups.

Baseline RSS sources:
- Yahoo Finance Headlines
- MarketWatch Top Stories
- MarketWatch MarketPulse
- SEC Press Releases
- PR Newswire All News Releases

Structured public news sources:
- PR Newswire
- GlobeNewswire
- ACCESS Newswire
- MT Newswires
- Finviz

Current source-status notes:
- `Yahoo Finance Headlines`
  - strongest ticker-specific baseline source for many large-cap names
- `Finviz`
  - useful as a broad aggregator and discovery source
  - can be noisy, so preprocessing is stricter
- `PR Newswire`
  - validated with direct company-specific results such as `AEE`
- `GlobeNewswire`
  - validated with company-specific results such as `LFST` and `SLXN`
- `ACCESS Newswire`
  - validated with direct company-specific results such as `HLPN`
- `MT Newswires`
  - public site is limited compared with the premium feed
  - currently treated as a low-confidence public source rather than a core validated source

### Current Ingestion Layer

Important current ingestion files:
- `src/ingestion/models.py`
  - normalized article structures
- `src/ingestion/rss_sources.py`
  - baseline RSS source registry
- `src/ingestion/rss_collectors.py`
  - RSS collection helpers and keyword builder
- `src/ingestion/structured_sources.py`
  - structured/public source registry
- `src/ingestion/structured_collectors.py`
  - PR/Globe/ACCESS/MT/Finviz collection logic
- `src/ingestion/seen_cache.py`
  - remembers structured links already seen today

Important implementation notes:
- `ACCESS Newswire` uses the public newsroom/API behavior behind the public page
- raw keyword matching now tolerates punctuation and formatting differences such as:
  - `SA` vs `S.A.`
  - similar spacing/punctuation variants in company names

### Current Preprocessing Layer

Main preprocessing file:
- `src/preprocessing/news_preprocessor.py`

This layer is now doing much more than simple keyword filtering.

Current preprocessing responsibilities:
- build ticker profiles with:
  - identity terms
  - specific context terms
  - generic context terms
- assign relevance scores
- reject obvious false positives
- separate:
  - `stories` = primary
  - `related_context`
  - `review_candidates`
  - `rejections`
- cluster duplicates across sources
- preserve:
  - `coverage_count`
  - `coverage_sources`
- classify event types
- calculate:
  - `event_importance_weight`
  - `signal_strength`

Current event types include:
- `earnings_or_guidance`
- `analyst_rating_or_target`
- `executive_change`
- `regulatory_or_geopolitical`
- `product_or_strategy`
- `market_reaction`
- `comparison_or_context`
- `general_company_focus`

Design intent:
- `primary` stays relatively strict
- `related_context` keeps meaningful but less-direct stories
- `review_candidates` preserves borderline company-adjacent cases
- `rejections` should mostly contain true noise

### Current Runner Scripts

Real CLI-style runners live in `src/runners/`.

Current runner files:
- `src/runners/collect_all_for_ticker.py`
  - collect and preprocess one ticker
- `src/runners/collect_watchlist_snapshot.py`
  - collect and preprocess the full watchlist once
- `src/runners/summarize_source_usage.py`
  - summarize source usage from one ticker debug file or a watchlist snapshot
- `src/runners/poll_watchlist.py`
  - run the watchlist repeatedly on a fixed interval

Compatibility wrappers live in `src/other/` so older commands still work:
- `src/other/collect_all_for_ticker.py`
- `src/other/collect_watchlist_snapshot.py`
- `src/other/summarize_source_usage.py`
- `src/other/poll_watchlist.py`

### Current Commands

Collect one ticker:

```bash
python3 src/other/collect_all_for_ticker.py \
  --ticker AAPL \
  --company Apple \
  --keyword iphone \
  --keyword mac \
  --structured-limit 0 \
  --rss-limit 0 \
  --include-seen \
  --json-out data/cache/aapl_all_results.json \
  --debug-json-out data/cache/aapl_debug.json
```

Collect the whole watchlist once:

```bash
python3 src/other/collect_watchlist_snapshot.py \
  --watchlist-file data/watchlists/sample_watchlist.json \
  --json-out data/cache/watchlist_snapshot.json \
  --structured-limit 0 \
  --rss-limit 0 \
  --include-seen
```

Summarize source usage:

```bash
python3 src/other/summarize_source_usage.py \
  --json-file data/cache/watchlist_snapshot.json
```

Run the watchlist on a polling loop:

```bash
python3 src/other/poll_watchlist.py \
  --watchlist-file data/watchlists/sample_watchlist.json \
  --latest-json-out data/cache/watchlist_snapshot.json \
  --history-dir data/cache/watchlist_history \
  --history-keep 30 \
  --interval-seconds 120 \
  --include-seen \
  --clear-stop-file
```

Stop the polling loop cleanly:

```bash
touch tmp/watchlist_polling.stop
```

### Current Automation Behavior

The watchlist polling runner is intended for short-cycle testing and later light automation.

Current polling behavior:
- overwrites the latest snapshot file each run
- optionally writes timestamped history files
- keeps only the most recent history snapshots by default
- stops cleanly when the stop file exists

Current history behavior:
- `data/cache/watchlist_snapshot.json`
  - current latest snapshot, overwritten each run
- `data/cache/watchlist_history/`
  - optional rolling history snapshots
- `--history-keep 30`
  - default retention boundary for history files

This means the current setup can be used for:
- repeated short-cycle QA runs
- repeated watchlist refresh testing
- later feature/prediction integration

without allowing history files to grow forever.

### Current Output Philosophy

The current project intentionally separates:
- latest state
- rolling debug history
- future long-term memory

Right now:
- latest snapshots are JSON
- rolling short-term history can stay JSON during testing

Later:
- longer-term article memory, sentiment memory, and feature history should move into `SQLite`
- latest dashboard/prediction outputs should still be overwritten each cycle

### Current Development Focus

The project is still in the source-data and preprocessing stage.

Current priority:
- make sure the source layer is trustworthy
- validate that direct company-specific stories are being captured
- reduce false positives
- preserve useful borderline articles without flooding primary

Not the current priority:
- full sentiment productionization
- macro/political event ingestion
- full prediction modeling

Those are later layers once the company-news pipeline is stable.

## Current End-To-End Process

The current implemented process is:

```text
1. Source collection
   baseline RSS + public structured sources

2. Raw keyword pre-filter
   broad enough to catch candidate matches

3. Preprocessing
   relevance scoring, source-aware filtering, duplicate clustering, event tagging

4. Bucket assignment
   primary stories, related context, review candidates, true rejections

5. Output
   latest JSON snapshot, optional ticker debug JSON, optional rolling watchlist history
```

## Next Planned Layers

The next major layers are still expected to be:

1. better persistent storage
   - likely `SQLite`
2. sentiment integration
   - after source-layer confidence is high enough
3. historical price ingestion
4. combined feature engineering
5. prediction logic
6. dashboard presentation

7. Dashboard layer
   display the latest metrics and prediction outputs in a refreshable interface
```

## Why The Scripts Are Split This Way

Each script or package is being kept narrow on purpose.

This separation is intended to make the project:
- easier to test
- easier to debug
- easier to expand
- easier to benchmark
- more reliable when refresh cycles are added later

Examples:
- ingestion scripts should not contain model-training logic
- sentiment scripts should not contain hardcoded feed registries
- dashboard scripts should not scrape the web directly
- modeling scripts should consume structured features rather than raw RSS entries

That separation is what will allow the project to grow from a working prototype into a more complete pipeline.

## Immediate Next Steps

The next implementation steps are:

1. add a `data/` structure for cached and processed outputs
2. make ingestion save structured results automatically
3. add lightweight local storage and deduplication
4. move FinBERT scoring into a dedicated sentiment package
5. add price ingestion and feature engineering
6. begin the first prediction baseline

## Project Flowchart

![Stock Price Prediction Flowchart](assets/IST495_Internship_Flowchart.png)
