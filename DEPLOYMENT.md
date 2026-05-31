# Deployment Notes

## What "persistent storage" means

This dashboard stores its working state in local files:

- SQLite database
- dashboard state JSON
- seen-story state JSON
- generated snapshot JSON

If a host gives you only an **ephemeral filesystem**, those files can be lost when the app restarts, redeploys, or the container is replaced. That means:

- article history can disappear
- translations can disappear
- source-health history can disappear
- the app still runs, but it behaves more like a stateless demo

If a host gives you a **persistent volume**, these files survive restarts and deployments.

## Cheapest realistic setup

For a public demo link, there are two practical modes:

### 1. Cheapest short-term demo

- deploy the app without a persistent disk
- acceptable for class/demo use
- not ideal for long-running history

### 2. Better hosted app

- deploy the app with a persistent disk / mounted volume
- set `STOCK_DASHBOARD_DATA_ROOT` to that mounted path
- SQLite and cached dashboard state then survive restarts

## Start command

```bash
gunicorn wsgi:app
```

## Useful environment variables

- `HOST`
- `PORT`
- `STOCK_DASHBOARD_DATA_ROOT`
- `STOCK_DASHBOARD_WATCHLIST_FILE`
- `STOCK_DASHBOARD_COOLDOWN_SECONDS`
- `STOCK_DASHBOARD_RSS_LIMIT`
- `STOCK_DASHBOARD_STRUCTURED_LIMIT`
- `STOCK_DASHBOARD_SKIP_RSS`
- `STOCK_DASHBOARD_SKIP_STRUCTURED`

## Example hosted environment

See:

- `.env.hosted.example`

## Efficiency notes

The app is already set up to stay relatively cheap by:

- using a shared-source collection model
- caching ticker-specific source decisions
- separating structured and unstructured views
- pruning old pipeline history
- limiting the live feed to a recent article window

If you want the cheapest hosted demo, keep:

- refreshes manual rather than always polling
- a smaller watchlist than the current 100-ticker demo universe if cost matters more than breadth
- SQLite on a single mounted volume
- translation on-demand only
