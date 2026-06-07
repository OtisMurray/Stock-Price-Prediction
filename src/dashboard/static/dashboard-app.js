const {
  useEffect,
  useMemo,
  useRef,
  useState
} = React;
const DEFAULT_FEED_ROW_LIMIT = 20;
const THEME_STORAGE_KEY = "stockDashboardTheme";
const DENSITY_STORAGE_KEY = "stockDashboardCompact";
const SOURCE_LEGEND_STORAGE_KEY = "stockDashboardShowSourceLegend";
const FEED_LIMIT_STORAGE_KEY = "stockDashboardFeedLimit";
function readStoredString(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value || fallback;
  } catch (error) {
    return fallback;
  }
}
function readStoredBoolean(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    if (value === null) return fallback;
    return value === "1";
  } catch (error) {
    return fallback;
  }
}
function readStoredNumber(key, fallback, allowedValues) {
  try {
    const value = Number(window.localStorage.getItem(key) || fallback);
    return allowedValues.includes(value) ? value : fallback;
  } catch (error) {
    return fallback;
  }
}
function resolveThemePreference(themePreference) {
  if (themePreference === "light" || themePreference === "dark") {
    return themePreference;
  }
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch (error) {
    return "light";
  }
}
async function parseApiResponse(response, fallbackMessage) {
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  const cleaned = text.replace(/\s+/g, " ").trim();
  const snippet = cleaned.slice(0, 180) || fallbackMessage;
  throw new Error(`${fallbackMessage}: ${response.status} ${snippet}`);
}
function formatPipelineMode(value) {
  if (!value) return "Shared Source Pool";
  return value.split("_").filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}
function formatSentimentRuntime(value) {
  const mode = String(value?.finbert_runtime_mode || "");
  if (mode === "hosted_backfill_enabled") return "FinBERT backfill enabled";
  if (mode === "hosted_backfill_only") return "FinBERT backfill only";
  if (mode === "cache_or_download") return "FinBERT cache/download enabled";
  if (mode === "local_cache_only") return "FinBERT local cache only";
  if (mode === "hosted_disabled") return "FinBERT disabled on hosted runtime";
  if (mode === "disabled") return "FinBERT disabled";
  return "Rule-based baseline";
}
function formatQuoteServiceHealth(value) {
  const provider = String(value?.provider || "");
  const quoted = Number(value?.quoted_ticker_count || 0);
  const tracked = Number(value?.tracked_ticker_count || 0);
  const fresh = Number(value?.fresh_quote_count || 0);
  const stale = Number(value?.stale_quote_count || 0);
  if (!tracked) return "Quotes: not warmed yet";
  const providerLabel = provider ? provider.replace(/_/g, " ") : "quote service";
  const suffix = fresh ? `${fresh} fresh` : stale ? `${stale} stale-cache` : "no live quotes";
  return `Quotes: ${quoted}/${tracked} via ${providerLabel} (${suffix})`;
}
function quoteCoverageSummary(value) {
  const quoted = Number(value?.quoted_ticker_count || 0);
  const tracked = Number(value?.tracked_ticker_count || 0);
  if (!tracked) return "Not warmed";
  return `${quoted}/${tracked}`;
}
function quoteCoverageNote(value) {
  const fresh = Number(value?.fresh_quote_count || 0);
  const stale = Number(value?.stale_quote_count || 0);
  if (fresh) return `${fresh} fresh quotes available`;
  if (stale) return `${stale} stale cached quotes available`;
  return "No usable quotes yet";
}
function formatWorkerStatusLabel(value) {
  const status = String(value?.status || "").toLowerCase();
  if (!status) return "Idle";
  return status.split("_").filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}
function summarizeActiveFilters(filters) {
  const pills = [];
  if (filters.search) pills.push(`Search: ${filters.search}`);
  if (filters.ticker) pills.push(`Ticker: ${filters.ticker}`);
  if (filters.sector) pills.push(`Sector: ${filters.sector}`);
  if (filters.industry) pills.push(`Industry: ${filters.industry}`);
  if (filters.source) pills.push(`Source: ${filters.source}`);
  if (filters.category) pills.push(`Category: ${formatCategoryLabel(filters.category)}`);
  if (filters.bucket && filters.bucket !== "Primary") pills.push(`Bucket: ${filters.bucket}`);
  if (filters.eventType === "__earnings__") pills.push("Earnings");
  else if (filters.eventType) pills.push(`Event: ${filters.eventType}`);
  if (filters.sourceMode === "__structured__") pills.push("Structured only");
  if (filters.sourceMode === "__unstructured__") pills.push("Unstructured only");
  if (filters.sentimentLabel) pills.push(`Sentiment: ${filters.sentimentLabel}`);
  if (filters.sentimentConfidence) pills.push(`Confidence ≥ ${Math.round(Number(filters.sentimentConfidence) * 100)}%`);
  if (filters.tickerRelevance) pills.push(`Relevance ≥ ${Math.round(Number(filters.tickerRelevance) * 100)}%`);
  if (filters.timeWindowMinutes) pills.push(`Freshness: ${filters.timeWindowMinutes}m`);
  return pills;
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
    hour12: true,
    timeZone: "America/New_York"
  }).format(new Date(parsed)) + " ET";
}
function parseArticleTimestampMs(row) {
  const candidates = [row?.published_at, row?.last_seen_at, row?.collected_at, row?.first_seen_at];
  for (const value of candidates) {
    if (!value) continue;
    const parsed = Date.parse(String(value));
    if (!Number.isNaN(parsed)) return parsed;
  }
  return null;
}
function withinFreshnessWindow(row, minutes, referenceTimestampMs) {
  if (!minutes || minutes <= 0) return true;
  const articleTimestampMs = parseArticleTimestampMs(row);
  if (articleTimestampMs === null) return false;
  const delta = Math.max(referenceTimestampMs - articleTimestampMs, 0);
  return delta <= minutes * 60 * 1000;
}
function isEarningsEvent(eventType) {
  return String(eventType || "").toLowerCase().includes("earnings");
}
function isUnstructuredSource(row) {
  const sourceKey = String(row?.source_key || "").toLowerCase();
  const sourceName = String(row?.source_name || "").toLowerCase();
  return sourceKey === "stocktwits" || sourceName.includes("stocktwits");
}
function isStructuredSource(row) {
  return !isUnstructuredSource(row) && String(row?.source_family || "").toLowerCase() !== "unstructured";
}
function sourceTierRank(row) {
  const tier = String(row?.source_quality_tier || "");
  if (tier === "primary_structured") return 0;
  if (tier === "secondary_structured") return 1;
  if (tier === "supplemental_unstructured") return 2;
  if (tier === "monitor_only") return 3;
  return isUnstructuredSource(row) ? 2 : 1;
}
function bucketLabel(value) {
  const normalized = String(value || "").trim();
  if (normalized === "stories") return "Primary";
  if (normalized === "related_context") return "Related";
  if (normalized === "review_candidates") return "Review";
  return normalized || "Primary";
}
function likelyNeedsTranslation(row) {
  return Boolean(row?.needs_translation);
}
function parseConfidenceThreshold(value) {
  const threshold = Number(value || 0);
  return Number.isFinite(threshold) && threshold > 0 ? threshold : 0;
}
function tickerRelevanceMarkersText(row) {
  return (row?.ticker_relevance_markers || []).slice(0, 3).join(", ");
}
function sentimentBadgeLabel(row) {
  const label = String(row?.sentiment_label || "").toLowerCase();
  if (label === "bullish") return "Bullish";
  if (label === "bearish") return "Bearish";
  if (label === "mixed") return "Mixed";
  return "";
}
function formatCategoryLabel(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  return normalized.split("_").filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}
const SOURCE_THEME_FALLBACKS = [{
  match: "reuters",
  accent: "#2563eb",
  soft: "rgba(37, 99, 235, 0.14)"
}, {
  match: "benzinga",
  accent: "#ea580c",
  soft: "rgba(234, 88, 12, 0.14)"
}, {
  match: "tradingview",
  accent: "#1d4ed8",
  soft: "rgba(29, 78, 216, 0.14)"
}, {
  match: "stocktwits",
  accent: "#059669",
  soft: "rgba(5, 150, 105, 0.14)"
}, {
  match: "marketwatch",
  accent: "#7c3aed",
  soft: "rgba(124, 58, 237, 0.14)"
}, {
  match: "pr newswire",
  accent: "#b45309",
  soft: "rgba(180, 83, 9, 0.14)"
}, {
  match: "access newswire",
  accent: "#0f766e",
  soft: "rgba(15, 118, 110, 0.14)"
}, {
  match: "globenewswire",
  accent: "#be123c",
  soft: "rgba(190, 18, 60, 0.14)"
}, {
  match: "sec",
  accent: "#334155",
  soft: "rgba(51, 65, 85, 0.14)"
}, {
  match: "finviz",
  accent: "#0f766e",
  soft: "rgba(15, 118, 110, 0.14)"
}, {
  match: "mt newswires",
  accent: "#475569",
  soft: "rgba(71, 85, 105, 0.14)"
}];
function sourceTheme(value, fallbackAccent = "#173d6d") {
  const normalized = String(value || "").toLowerCase();
  const match = SOURCE_THEME_FALLBACKS.find(item => normalized.includes(item.match));
  const accent = match?.accent || fallbackAccent;
  const soft = match?.soft || "rgba(23, 61, 109, 0.12)";
  return {
    accent,
    soft,
    border: accent
  };
}
function SourceBadge({
  label
}) {
  if (!label) return null;
  const theme = sourceTheme(label);
  return /*#__PURE__*/React.createElement("span", {
    className: "source-badge",
    style: {
      "--source-accent": theme.accent,
      "--source-soft": theme.soft,
      "--source-border": theme.border
    }
  }, label);
}
function formatSignedScore(value) {
  const numeric = Number(value || 0);
  const prefix = numeric > 0 ? "+" : "";
  return `${prefix}${numeric.toFixed(2)}`;
}
function formatDensity(value) {
  return Number(value || 0).toFixed(1);
}
function scoreToneClass(value) {
  const numeric = Number(value || 0);
  if (numeric > 0.04) return "positive";
  if (numeric < -0.04) return "negative";
  return "mixed";
}
function chartToneClass(item) {
  if (item?.tone) return String(item.tone);
  return scoreToneClass(item?.value);
}
function TranslatePanel({
  row,
  scope = "feed"
}) {
  const [translation, setTranslation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  if (!likelyNeedsTranslation(row)) return null;
  const handleTranslate = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/translate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          story_key: row.story_key || "",
          title: row.title || "",
          summary: row.summary || ""
        })
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || "Translation failed.");
      }
      setTranslation(payload);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "translate-button",
    onClick: handleTranslate,
    disabled: loading
  }, loading ? "Translating..." : "Translate"), error ? /*#__PURE__*/React.createElement("div", {
    className: "translate-output"
  }, "Translation unavailable: ", error) : null, translation ? /*#__PURE__*/React.createElement("div", {
    className: "translate-output"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("strong", null, "English title:"), " ", translation.title_translated || row.title), translation.summary_translated ? /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 6
    }
  }, translation.summary_translated) : null, /*#__PURE__*/React.createElement("div", {
    className: "translate-meta"
  }, "Source language: ", translation.source_language || "auto", " | Sentiment should use this translated text later.")) : null);
}
function getTickerStoryRows(tickerDetail) {
  if (!tickerDetail?.ticker) return [];
  const rows = [];
  const groups = [["stories", "Primary"], ["related_context", "Related"], ["review_candidates", "Review"]];
  groups.forEach(([bucketKey, bucketLabel]) => {
    (tickerDetail.ticker[bucketKey] || []).forEach((row, index) => {
      rows.push({
        ...row,
        bucketLabel,
        is_earnings: isEarningsEvent(row.event_type),
        sortSentiment: Math.abs(Number(row.sentiment_score || 0)) * Number(row.sentiment_confidence || 0),
        _key: `${bucketKey}-${row.link || row.title}-${index}`
      });
    });
  });
  rows.sort((left, right) => right.sortSentiment - left.sortSentiment || Number(right.is_new) - Number(left.is_new) || String(left.title || "").localeCompare(String(right.title || "")));
  return rows;
}
function MatchedTickerChips({
  row,
  onSelectTicker
}) {
  const matchedTickers = row.matched_tickers || String(row.ticker || "").split(", ").filter(Boolean);
  if (!matchedTickers.length) {
    return /*#__PURE__*/React.createElement("div", {
      className: "ticker-chip"
    }, "-");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "ticker-chip-row"
  }, matchedTickers.slice(0, 4).map(ticker => /*#__PURE__*/React.createElement("button", {
    key: ticker,
    type: "button",
    className: "ticker-chip",
    onClick: () => onSelectTicker(ticker)
  }, ticker)), matchedTickers.length > 4 ? /*#__PURE__*/React.createElement("span", {
    className: "ticker-chip"
  }, "+", matchedTickers.length - 4) : null);
}
function SourceLegendCard({
  rows
}) {
  const sources = useMemo(() => {
    const counts = new Map();
    rows.forEach(row => {
      const sourceName = String(row?.source_name || "").trim();
      if (!sourceName) return;
      counts.set(sourceName, (counts.get(sourceName) || 0) + 1);
    });
    return Array.from(counts.entries()).map(([label, count]) => ({
      label,
      count
    })).sort((left, right) => right.count - left.count || left.label.localeCompare(right.label));
  }, [rows]);
  return /*#__PURE__*/React.createElement("aside", {
    className: "sidebar-card source-rail"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Visible sources"), /*#__PURE__*/React.createElement("h3", null, "Source Colors"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Consistent source colors make it easier to scan trust, density, and coverage at a glance."), /*#__PURE__*/React.createElement("div", {
    className: "source-legend-list"
  }, sources.length ? sources.map(item => /*#__PURE__*/React.createElement("div", {
    className: "source-legend-row",
    key: item.label
  }, /*#__PURE__*/React.createElement(SourceBadge, {
    label: item.label
  }), /*#__PURE__*/React.createElement("span", {
    className: "source-legend-count"
  }, item.count))) : /*#__PURE__*/React.createElement("p", {
    className: "empty"
  }, "No visible sources yet.")), /*#__PURE__*/React.createElement("div", {
    className: "chart-footnote"
  }, "Heavy AI work stays behind the final retained pool. Cached FinBERT results are reused so the open-source model does not rerun on every page load."));
}
function FeedTable({
  rows,
  onSelectTicker
}) {
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, "No article rows match the current filters.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Matched Tickers"), /*#__PURE__*/React.createElement("th", null, "Headline"), /*#__PURE__*/React.createElement("th", null, "Source"), /*#__PURE__*/React.createElement("th", null, "Bucket"), /*#__PURE__*/React.createElement("th", null, "Event"), /*#__PURE__*/React.createElement("th", null, "Ticker Count"), /*#__PURE__*/React.createElement("th", null, "Sentiment"), /*#__PURE__*/React.createElement("th", null, "Published"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((row, index) => {
    const sentimentMarkers = [...(row.sentiment_positive_markers || []), ...(row.sentiment_negative_markers || [])].slice(0, 3).join(", ");
    const relevanceMarkers = tickerRelevanceMarkersText(row);
    const modelSummary = `${row.sentiment_model_used || "rule_based"}${row.finbert_label ? ` / FinBERT ${row.finbert_label} (${Math.round(Number(row.finbert_confidence || 0) * 100)}%)` : ""}`;
    const sentimentPercent = Math.round(Number(row.sentiment_confidence || 0) * 100);
    const signalPercent = Math.round(Number(row.signal_confidence || row.sentiment_confidence || 0) * 100);
    const relevancePercent = Math.round(Number(row.ticker_relevance_confidence || 0) * 100);
    const predictionWeight = Number(row.prediction_weight || 0).toFixed(2);
    return /*#__PURE__*/React.createElement("tr", {
      key: `${row.ticker}-${row.link}-${index}`
    }, /*#__PURE__*/React.createElement("td", {
      className: "col-ticker"
    }, /*#__PURE__*/React.createElement(MatchedTickerChips, {
      row: row,
      onSelectTicker: onSelectTicker
    }), /*#__PURE__*/React.createElement("div", {
      className: "company-name"
    }, row.company || `${row.matched_ticker_count || 0} matched tickers`)), /*#__PURE__*/React.createElement("td", {
      className: "col-headline"
    }, /*#__PURE__*/React.createElement("div", {
      className: "headline-line"
    }, /*#__PURE__*/React.createElement("a", {
      href: row.link,
      target: "_blank",
      rel: "noreferrer"
    }, row.title), row.is_new ? /*#__PURE__*/React.createElement("span", {
      className: "badge new"
    }, "NEW") : null, row.is_earnings ? /*#__PURE__*/React.createElement("span", {
      className: "badge earnings"
    }, "Earnings") : null, row.is_unstructured ? /*#__PURE__*/React.createElement("span", {
      className: "badge unstructured"
    }, "Unstructured") : null, sentimentBadgeLabel(row) ? /*#__PURE__*/React.createElement("span", {
      className: `badge ${String(row.sentiment_label || "").toLowerCase()}`
    }, sentimentBadgeLabel(row)) : null, /*#__PURE__*/React.createElement(TranslatePanel, {
      row: row
    })), /*#__PURE__*/React.createElement("div", {
      className: "headline-sub"
    }, row.summary || "", sentimentMarkers ? /*#__PURE__*/React.createElement("span", null, " ", "\u2022", " Sentiment markers: ", sentimentMarkers) : null, relevanceMarkers ? /*#__PURE__*/React.createElement("span", null, " ", "\u2022", " Relevance markers: ", relevanceMarkers) : null), /*#__PURE__*/React.createElement("div", {
      className: "headline-meta"
    }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "First captured:"), " ", formatMetaTimestamp(row.first_seen_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Last observed:"), " ", formatMetaTimestamp(row.last_seen_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Latest refresh capture:"), " ", formatMetaTimestamp(row.collected_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Sentiment confidence:"), " ", sentimentPercent, "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Prediction weight:"), " ", predictionWeight), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Ticker relevance:"), " ", relevancePercent, "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Support signal:"), " ", signalPercent, "%"), row.primary_category ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Category:"), " ", formatCategoryLabel(row.primary_category)) : null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Model:"), " ", modelSummary), row.market_impact_bias ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Market bias:"), " ", row.market_impact_bias) : null)), /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(SourceBadge, {
      label: row.source_name
    })), /*#__PURE__*/React.createElement("td", null, row.bucket_label), /*#__PURE__*/React.createElement("td", null, row.event_type || "general"), /*#__PURE__*/React.createElement("td", null, row.matched_ticker_count || 0), /*#__PURE__*/React.createElement("td", {
      className: `leader-score ${scoreToneClass(row.sentiment_score)}`
    }, formatSignedScore(row.sentiment_score)), /*#__PURE__*/React.createElement("td", null, row.published || "No timestamp available"));
  }))));
}
function summarizeSourceHealthRows(rows) {
  const grouped = new Map();
  rows.forEach(row => {
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
        visible_story_count: 0,
        visible_primary_count: 0,
        visible_new_count: 0,
        elapsed_total: 0,
        elapsed_max: 0,
        last_collected_at: "",
        scopes: new Set(),
        errors: new Set()
      });
    }
    const item = grouped.get(key);
    item.runs_seen += 1;
    item.ok_runs += row.ok ? 1 : 0;
    item.fetched_count += Number(row.fetched_count || 0);
    item.matched_count += Number(row.matched_count || 0);
    item.visible_story_count = Math.max(item.visible_story_count, Number(row.visible_story_count || 0));
    item.visible_primary_count = Math.max(item.visible_primary_count, Number(row.visible_primary_count || 0));
    item.visible_new_count = Math.max(item.visible_new_count, Number(row.visible_new_count || 0));
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
  return Array.from(grouped.values()).map(item => {
    const scopeValues = Array.from(item.scopes);
    const tickerScopedCount = scopeValues.filter(value => value !== "Shared pool").length;
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
      errorPreview: Array.from(item.errors).slice(0, 2).join(" | ")
    };
  }).sort((left, right) => right.visible_story_count - left.visible_story_count || right.matched_count - left.matched_count || right.fetched_count - left.fetched_count || left.source_name.localeCompare(right.source_name));
}
function SourceHealthTable({
  rows
}) {
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, "No source health rows are available for the latest refresh.");
  }
  const summarizedRows = summarizeSourceHealthRows(rows);
  return /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Source"), /*#__PURE__*/React.createElement("th", null, "Collection Mode"), /*#__PURE__*/React.createElement("th", null, "Health"), /*#__PURE__*/React.createElement("th", null, "Runs"), /*#__PURE__*/React.createElement("th", null, "Fetched"), /*#__PURE__*/React.createElement("th", null, "Matched"), /*#__PURE__*/React.createElement("th", null, "Visible"), /*#__PURE__*/React.createElement("th", null, "New Visible"), /*#__PURE__*/React.createElement("th", null, "Avg Elapsed"), /*#__PURE__*/React.createElement("th", null, "Last Collected"), /*#__PURE__*/React.createElement("th", null, "Notes"))), /*#__PURE__*/React.createElement("tbody", null, summarizedRows.map((row, index) => /*#__PURE__*/React.createElement("tr", {
    key: `${row.source_key}-${index}`
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(SourceBadge, {
    label: row.source_name
  })), /*#__PURE__*/React.createElement("td", null, row.modeLabel), /*#__PURE__*/React.createElement("td", null, row.statusLabel), /*#__PURE__*/React.createElement("td", null, row.runs_seen), /*#__PURE__*/React.createElement("td", null, row.fetched_count), /*#__PURE__*/React.createElement("td", null, row.matched_count), /*#__PURE__*/React.createElement("td", null, row.visible_story_count || 0), /*#__PURE__*/React.createElement("td", null, row.visible_new_count || 0), /*#__PURE__*/React.createElement("td", null, Number(row.avg_elapsed_seconds || 0).toFixed(3), "s"), /*#__PURE__*/React.createElement("td", null, row.last_collected_at || "-"), /*#__PURE__*/React.createElement("td", null, row.errorPreview || "-"))))));
}
function TickerSourceHistoryTable({
  rows
}) {
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, "No source history is available for this ticker yet.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Source"), /*#__PURE__*/React.createElement("th", null, "Runs Seen"), /*#__PURE__*/React.createElement("th", null, "OK Runs"), /*#__PURE__*/React.createElement("th", null, "Total Fetched"), /*#__PURE__*/React.createElement("th", null, "Total Matched"), /*#__PURE__*/React.createElement("th", null, "Avg Elapsed"), /*#__PURE__*/React.createElement("th", null, "Last Collected"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((row, index) => /*#__PURE__*/React.createElement("tr", {
    key: `${row.source_key}-${index}`
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement(SourceBadge, {
    label: row.source_name
  })), /*#__PURE__*/React.createElement("td", null, row.runs_seen), /*#__PURE__*/React.createElement("td", null, row.ok_runs), /*#__PURE__*/React.createElement("td", null, row.total_fetched_count), /*#__PURE__*/React.createElement("td", null, row.total_matched_count), /*#__PURE__*/React.createElement("td", null, Number(row.avg_elapsed_seconds || 0).toFixed(3), "s"), /*#__PURE__*/React.createElement("td", null, row.last_collected_at || "-"))))));
}
function TickerCoverageTable({
  tickers,
  onSelectTicker
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "table-wrap"
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Ticker"), /*#__PURE__*/React.createElement("th", null, "Company"), /*#__PURE__*/React.createElement("th", null, "Sector"), /*#__PURE__*/React.createElement("th", null, "Industry"), /*#__PURE__*/React.createElement("th", null, "Primary"), /*#__PURE__*/React.createElement("th", null, "Related"), /*#__PURE__*/React.createElement("th", null, "Review"), /*#__PURE__*/React.createElement("th", null, "New Primary"))), /*#__PURE__*/React.createElement("tbody", null, tickers.map(item => /*#__PURE__*/React.createElement("tr", {
    key: item.ticker
  }, /*#__PURE__*/React.createElement("td", null, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "ticker-chip",
    onClick: () => onSelectTicker(item.ticker)
  }, item.ticker)), /*#__PURE__*/React.createElement("td", null, item.company), /*#__PURE__*/React.createElement("td", null, item.sector || "-"), /*#__PURE__*/React.createElement("td", null, item.industry || "-"), /*#__PURE__*/React.createElement("td", null, item.stats?.clustered_story_count || 0), /*#__PURE__*/React.createElement("td", null, item.stats?.related_context_rows || 0), /*#__PURE__*/React.createElement("td", null, item.stats?.review_candidate_rows || 0), /*#__PURE__*/React.createElement("td", null, item.new_primary_count || 0))))));
}
function LeaderList({
  rows,
  onSelectTicker,
  emptyLabel
}) {
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, emptyLabel);
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "leader-list"
  }, rows.map(item => {
    return /*#__PURE__*/React.createElement("div", {
      className: "leader-row",
      key: `${item.ticker}-${item.label}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "leader-main"
    }, /*#__PURE__*/React.createElement("div", {
      className: "leader-title"
    }, /*#__PURE__*/React.createElement("button", {
      type: "button",
      className: "ticker-chip",
      onClick: () => onSelectTicker(item.ticker)
    }, item.ticker), /*#__PURE__*/React.createElement("span", {
      className: "leader-company"
    }, item.company || item.label || "")), /*#__PURE__*/React.createElement("div", {
      className: "leader-meta"
    }, /*#__PURE__*/React.createElement("span", null, item.label || "Mixed"), /*#__PURE__*/React.createElement("span", null, item.article_count || 0, " articles"), /*#__PURE__*/React.createElement("span", null, item.source_count || 0, " sources"), /*#__PURE__*/React.createElement("span", null, formatDensity(item.message_density_score), " density"))), /*#__PURE__*/React.createElement("div", {
      className: `leader-score ${scoreToneClass(item.momentum_score)}`
    }, formatSignedScore(item.momentum_score)));
  }));
}
function MomentumPanel({
  momentum,
  onSelectTicker
}) {
  const positiveRows = momentum?.top_positive || [];
  const negativeRows = momentum?.top_negative || [];
  const leaderRows = momentum?.leaders || [];
  if (!positiveRows.length && !negativeRows.length && !leaderRows.length) {
    return null;
  }
  return /*#__PURE__*/React.createElement("section", {
    className: "panel panel-momentum"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Momentum Ranking"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Ticker momentum is sentiment-led, then shaped by relevance, confidence support, exposure weight, and recency so building sentiment over time rises to the top.")), /*#__PURE__*/React.createElement("div", {
    className: "table-note"
  }, "Short windows carry the most weight, while message density shows how quickly sentiment-bearing coverage is building.")), /*#__PURE__*/React.createElement("div", {
    className: "momentum-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "momentum-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Bullish build"), /*#__PURE__*/React.createElement("h3", null, "Top Positive Momentum"), /*#__PURE__*/React.createElement(LeaderList, {
    rows: positiveRows.slice(0, 5),
    onSelectTicker: onSelectTicker,
    emptyLabel: "No bullish momentum leaders yet."
  })), /*#__PURE__*/React.createElement("article", {
    className: "momentum-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Bearish build"), /*#__PURE__*/React.createElement("h3", null, "Top Negative Momentum"), /*#__PURE__*/React.createElement(LeaderList, {
    rows: negativeRows.slice(0, 5),
    onSelectTicker: onSelectTicker,
    emptyLabel: "No bearish momentum leaders yet."
  })), /*#__PURE__*/React.createElement("article", {
    className: "momentum-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Overall leaders"), /*#__PURE__*/React.createElement("h3", null, "Highest Absolute Momentum"), /*#__PURE__*/React.createElement(LeaderList, {
    rows: leaderRows.slice(0, 5),
    onSelectTicker: onSelectTicker,
    emptyLabel: "No momentum leaders are available yet."
  }))));
}
function BarChartCard({
  title,
  eyebrow,
  items,
  formatter = value => String(value),
  footnote = ""
}) {
  const values = items.map(item => Math.abs(Number(item.value || 0)));
  const maxValue = Math.max(...values, 1);
  return /*#__PURE__*/React.createElement("article", {
    className: "chart-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, eyebrow), /*#__PURE__*/React.createElement("h3", null, title), /*#__PURE__*/React.createElement("div", {
    className: "bar-list"
  }, items.map(item => {
    const width = Math.max(8, Math.round(Math.abs(Number(item.value || 0)) / maxValue * 100));
    return /*#__PURE__*/React.createElement("div", {
      className: "bar-row",
      key: `${title}-${item.label}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "bar-label"
    }, item.label), /*#__PURE__*/React.createElement("div", {
      className: "bar-track"
    }, /*#__PURE__*/React.createElement("div", {
      className: `bar-fill ${chartToneClass(item)}`,
      style: {
        width: `${width}%`
      }
    })), /*#__PURE__*/React.createElement("div", {
      className: "bar-value"
    }, formatter(item.value)));
  })), footnote ? /*#__PURE__*/React.createElement("div", {
    className: "chart-footnote"
  }, footnote) : null);
}
function CategoryListCard({
  categories
}) {
  if (!categories.length) {
    return /*#__PURE__*/React.createElement("article", {
      className: "chart-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "eyebrow"
    }, "Article topics"), /*#__PURE__*/React.createElement("h3", null, "Categories"), /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, "No category counts are available yet."));
  }
  return /*#__PURE__*/React.createElement("article", {
    className: "chart-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Article topics"), /*#__PURE__*/React.createElement("h3", null, "Categories"), /*#__PURE__*/React.createElement("div", {
    className: "category-list"
  }, categories.map(item => /*#__PURE__*/React.createElement("div", {
    className: "category-row",
    key: item.key || item.label
  }, /*#__PURE__*/React.createElement("span", {
    className: "category-name"
  }, item.label || formatCategoryLabel(item.key)), /*#__PURE__*/React.createElement("span", {
    className: "category-count"
  }, item.count || 0)))), /*#__PURE__*/React.createElement("div", {
    className: "chart-footnote"
  }, "Categories help separate broad markets, economy, filings, press releases, and ticker-focused equity stories."));
}
function ChartsPanel({
  charts,
  categories
}) {
  const sentimentItems = charts?.sentiment_distribution || [];
  const sourceItems = charts?.source_visibility || [];
  const pulseItems = charts?.market_pulse || [];
  const densityItems = charts?.message_density || [];
  const categoryItems = categories || [];
  if (!sentimentItems.length && !sourceItems.length && !pulseItems.length && !densityItems.length && !categoryItems.length) {
    return null;
  }
  return /*#__PURE__*/React.createElement("section", {
    className: "panel panel-charts"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Charts"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Quick visual summaries for sentiment mix, visible source coverage, message density, and current market pulse windows.")), /*#__PURE__*/React.createElement("div", {
    className: "table-note"
  }, "Designed for quick readouts before you add fuller chart modules later.")), /*#__PURE__*/React.createElement("div", {
    className: "chart-grid"
  }, /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Sentiment Distribution",
    eyebrow: "Article mix",
    items: sentimentItems,
    formatter: value => String(value),
    footnote: "Counts reflect the currently visible pooled article set."
  }), /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Source Visibility",
    eyebrow: "Visible feed rows",
    items: sourceItems.map(item => ({
      ...item,
      tone: "accent"
    })),
    formatter: value => String(value),
    footnote: "Top sources by current visible row count after deduping."
  }), /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Market Pulse",
    eyebrow: "Momentum windows",
    items: pulseItems,
    formatter: value => formatSignedScore(value),
    footnote: "Net momentum contribution across the last 1, 6, and 24 hours."
  }), /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Message Density",
    eyebrow: "Attention build",
    items: densityItems.map(item => ({
      ...item,
      tone: "accent"
    })),
    formatter: value => formatDensity(value),
    footnote: "Weighted count of recent sentiment-bearing article activity across the latest windows."
  }), /*#__PURE__*/React.createElement(CategoryListCard, {
    categories: categoryItems
  })));
}
function SentimentAuditPanel({
  audit
}) {
  if (!audit || !Number(audit.total_rows || 0)) {
    return null;
  }
  const readinessItems = audit.readiness_reasons || [];
  const labelItems = audit.label_counts || [];
  const activeExamples = audit.active_examples || [];
  const reviewExamples = audit.review_examples || [];
  const recommendedActions = audit.recommended_next_actions || [];
  const coveragePercent = Number(audit.total_rows || 0) ? Math.round(Number(audit.finbert_ready_count || 0) / Number(audit.total_rows || 1) * 100) : 0;
  const activePercent = Number(audit.total_rows || 0) ? Math.round(Number(audit.finbert_active_count || 0) / Number(audit.total_rows || 1) * 100) : 0;
  const renderExample = row => /*#__PURE__*/React.createElement("article", {
    className: "story-item",
    key: `${row.link || row.title}-${row.ticker || ""}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "headline-line"
  }, row.link ? /*#__PURE__*/React.createElement("a", {
    href: row.link,
    target: "_blank",
    rel: "noreferrer"
  }, row.title) : /*#__PURE__*/React.createElement("span", null, row.title), row.sentiment_label ? /*#__PURE__*/React.createElement("span", {
    className: `badge ${String(row.sentiment_label || "").toLowerCase()}`
  }, sentimentBadgeLabel(row) || row.sentiment_label) : null), /*#__PURE__*/React.createElement("div", {
    className: "headline-meta"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Tickers:"), " ", (row.matched_tickers || []).slice(0, 4).join(", ") || row.ticker || "-"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Source:"), " ", /*#__PURE__*/React.createElement(SourceBadge, {
    label: row.source_name || "-"
  })), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Model:"), " ", row.sentiment_model_used || "rule_based"), row.finbert_label ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "FinBERT:"), " ", row.finbert_label, " ", Math.round(Number(row.finbert_confidence || 0) * 100), "%") : null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Tone:"), " ", Math.round(Number(row.sentiment_confidence || 0) * 100), "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Relevance:"), " ", Math.round(Number(row.ticker_relevance_confidence || 0) * 100), "%")));
  return /*#__PURE__*/React.createElement("section", {
    className: "panel panel-charts"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Sentiment Model QA"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Operational readout for model readiness, stored FinBERT coverage, rule fallback, and rows that deserve manual review.")), /*#__PURE__*/React.createElement("div", {
    className: "table-note"
  }, "Dashboard reads stored model results; transformer inference runs through the separate sentiment refresh job.")), /*#__PURE__*/React.createElement("div", {
    className: "mini-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "FinBERT-ready"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, audit.finbert_ready_count || 0, "/", audit.total_rows || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, coveragePercent, "% of visible rows have enough clean text for model scoring.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "FinBERT active"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, audit.finbert_active_count || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, activePercent, "% are currently using stored hybrid FinBERT/rule outputs.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Rule fallback"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, audit.rule_based_count || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Fast baseline or high-confidence rule path used outside active hybrid scoring.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Review queue"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, audit.high_relevance_low_confidence_count || audit.low_confidence_count || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "High-relevance or low-confidence rows to inspect during calibration."))), /*#__PURE__*/React.createElement("div", {
    className: "chart-grid",
    style: {
      marginTop: 16
    }
  }, /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Readiness Reasons",
    eyebrow: "Model input status",
    items: readinessItems.map(item => ({
      label: formatCategoryLabel(item.reason || "not_scored"),
      value: item.count || 0,
      tone: item.reason === "ready" ? "bullish" : "mixed"
    })),
    formatter: value => String(value),
    footnote: "Rows marked ready can be scored by the separate FinBERT refresh job."
  }), /*#__PURE__*/React.createElement(BarChartCard, {
    title: "Label Mix",
    eyebrow: "Current sentiment",
    items: labelItems.map(item => ({
      label: item.label,
      value: item.count || 0,
      tone: String(item.label || "").toLowerCase()
    })),
    formatter: value => String(value),
    footnote: "Label counts reflect the visible article pool after source dedupe."
  })), recommendedActions.length ? /*#__PURE__*/React.createElement("div", {
    className: "section-block"
  }, /*#__PURE__*/React.createElement("h3", null, "Next Improvements"), /*#__PURE__*/React.createElement("div", {
    className: "mini-grid"
  }, recommendedActions.slice(0, 4).map(item => /*#__PURE__*/React.createElement("article", {
    className: "mini-card",
    key: `${item.area}-${item.priority}`
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, item.area || "Improvement"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value",
    style: {
      fontSize: 24
    }
  }, item.priority || "ready"), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, item.count ? `${item.count} rows. ` : "", item.action || ""))))) : null, activeExamples.length ? /*#__PURE__*/React.createElement("div", {
    className: "section-block"
  }, /*#__PURE__*/React.createElement("h3", null, "Stored FinBERT Examples"), /*#__PURE__*/React.createElement("div", {
    className: "story-list"
  }, activeExamples.map(renderExample))) : null, reviewExamples.length ? /*#__PURE__*/React.createElement("div", {
    className: "section-block"
  }, /*#__PURE__*/React.createElement("h3", null, "Calibration Review"), /*#__PURE__*/React.createElement("div", {
    className: "story-list"
  }, reviewExamples.map(renderExample))) : null);
}
function TickerStoryList({
  rows
}) {
  if (!rows.length) {
    return /*#__PURE__*/React.createElement("p", {
      className: "empty"
    }, "No relevant rows are available for this ticker yet.");
  }
  return /*#__PURE__*/React.createElement("div", {
    className: "story-list"
  }, rows.slice(0, 6).map(row => {
    const sentimentMarkers = [...(row.sentiment_positive_markers || []), ...(row.sentiment_negative_markers || [])].slice(0, 3).join(", ");
    const relevanceMarkers = tickerRelevanceMarkersText(row);
    return /*#__PURE__*/React.createElement("article", {
      className: "story-item",
      key: row._key
    }, /*#__PURE__*/React.createElement("div", {
    className: "headline-line"
  }, /*#__PURE__*/React.createElement("a", {
    href: row.link,
    target: "_blank",
    rel: "noreferrer"
  }, row.title), row.is_new ? /*#__PURE__*/React.createElement("span", {
    className: "badge new"
  }, "NEW") : null, sentimentBadgeLabel(row) ? /*#__PURE__*/React.createElement("span", {
    className: `badge ${String(row.sentiment_label || "").toLowerCase()}`
  }, sentimentBadgeLabel(row)) : null), /*#__PURE__*/React.createElement("div", {
    className: "story-meta"
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Bucket:"), " ", row.bucketLabel), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Source:"), " ", /*#__PURE__*/React.createElement(SourceBadge, {
    label: row.source_name || "Unknown"
  })), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Sentiment score:"), " ", formatSignedScore(row.sentiment_score || 0)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Published:"), " ", row.published || "Not recorded"), row.is_earnings ? /*#__PURE__*/React.createElement("span", {
    className: "badge earnings"
  }, "Earnings") : null, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Sentiment:"), " ", sentimentBadgeLabel(row) || "Neutral", " (", Math.round(Number(row.sentiment_confidence || 0) * 100), "% tone)")), /*#__PURE__*/React.createElement("div", {
    style: {
      marginTop: 8
    }
  }, sentimentMarkers ? /*#__PURE__*/React.createElement("div", {
    className: "headline-sub"
  }, "Sentiment markers: ", sentimentMarkers) : null, relevanceMarkers ? /*#__PURE__*/React.createElement("div", {
    className: "headline-sub"
  }, "Relevance markers: ", relevanceMarkers) : null, /*#__PURE__*/React.createElement("div", {
    className: "headline-sub"
  }, "Model: ", row.sentiment_model_used || "rule_based", row.finbert_label ? ` | FinBERT: ${row.finbert_label} (${Math.round(Number(row.finbert_confidence || 0) * 100)}%)` : "", row.market_impact_bias ? ` | Market bias: ${row.market_impact_bias}` : ""), /*#__PURE__*/React.createElement(TranslatePanel, {
    row: row,
    scope: "ticker"
  })));
  }));
}
function TickerWorkspace({
  tickerDetail
}) {
  if (!tickerDetail?.ticker) {
    return /*#__PURE__*/React.createElement("div", {
      className: "sidebar-card"
    }, /*#__PURE__*/React.createElement("div", {
      className: "eyebrow"
    }, "Ticker workspace"), /*#__PURE__*/React.createElement("h3", null, "Select a ticker"), /*#__PURE__*/React.createElement("p", {
      className: "panel-subtitle"
    }, "Click any ticker chip in the feed or ticker universe table to open a focused workspace for relevant news, source history, and the future prediction layer."));
  }
  const storyRows = getTickerStoryRows(tickerDetail);
  const ticker = tickerDetail.ticker;
  const summary = tickerDetail.summary || {};
  const freshness = summary.freshness || {};
  const sentiment = summary.sentiment || {};
  const strongestPositive = summary.strongest_positive_title || "Not enough conviction yet";
  const strongestNegative = summary.strongest_negative_title || "Not enough conviction yet";
  return /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement("section", {
    className: "sidebar-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "eyebrow"
  }, "Selected ticker"), /*#__PURE__*/React.createElement("h3", null, ticker.ticker), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Focused analysis space for relevant coverage, source confirmation, and the later prediction workflow."), /*#__PURE__*/React.createElement("div", {
    className: "hero-meta"
  }, ticker.company ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Company:"), " ", ticker.company) : null, ticker.sector ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Sector:"), " ", ticker.sector) : null, ticker.industry ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Industry:"), " ", ticker.industry) : null), /*#__PURE__*/React.createElement("div", {
    className: "mini-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Primary stories"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, summary.primary_count || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Strongest rows currently attached to this ticker.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Source coverage"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, summary.source_count || 0), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Distinct sources represented in the latest stored run.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Freshness window"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value",
    style: {
      fontSize: 18
    }
  }, freshness.last_seen_latest || "Not recorded"), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Latest observed article timing for this ticker.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Sentiment snapshot"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value"
  }, sentiment.label || "Neutral"), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Score ", Number(sentiment.score || 0).toFixed(2), " | Tone confidence ", Math.round(Number(sentiment.confidence || 0) * 100), "% | Relevance ", Math.round(Number(sentiment.avg_relevance_confidence || 0) * 100), "%", sentiment.signal_confidence ? ` | Support signal ${Math.round(Number(sentiment.signal_confidence || 0) * 100)}%` : "", " from current ticker coverage.")), /*#__PURE__*/React.createElement("article", {
    className: "mini-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "mini-label"
  }, "Sentiment balance"), /*#__PURE__*/React.createElement("div", {
    className: "mini-value",
    style: {
      fontSize: 18
    }
  }, `${sentiment.bullish_count || 0} B / ${sentiment.bearish_count || 0} Br`), /*#__PURE__*/React.createElement("div", {
    className: "mini-sub"
  }, "Strongest positive: ", strongestPositive, ". Strongest negative: ", strongestNegative, ".")))), /*#__PURE__*/React.createElement("section", {
    className: "sidebar-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "section-block",
    style: {
      marginTop: 0
    }
  }, /*#__PURE__*/React.createElement("h3", null, "Relevant News"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Top stored rows for the selected ticker, ordered by current sentiment strength.")), /*#__PURE__*/React.createElement(TickerStoryList, {
    rows: storyRows
  })), /*#__PURE__*/React.createElement("section", {
    className: "sidebar-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "section-block",
    style: {
      marginTop: 0
    }
  }, /*#__PURE__*/React.createElement("h3", null, "Source History"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "SQLite-backed source history across refreshes for this selected ticker.")), /*#__PURE__*/React.createElement(TickerSourceHistoryTable, {
    rows: tickerDetail.source_history || []
  })));
}
function App() {
  const [state, setState] = useState(null);
  const [articlePool, setArticlePool] = useState(null);
  const [tickerDetail, setTickerDetail] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [themePreference, setThemePreference] = useState(() => readStoredString(THEME_STORAGE_KEY, "light"));
  const [compactMode, setCompactMode] = useState(() => readStoredBoolean(DENSITY_STORAGE_KEY, false));
  const [showSourceLegend, setShowSourceLegend] = useState(() => readStoredBoolean(SOURCE_LEGEND_STORAGE_KEY, true));
  const [feedRowLimit, setFeedRowLimit] = useState(() => readStoredNumber(FEED_LIMIT_STORAGE_KEY, DEFAULT_FEED_ROW_LIMIT, [10, 20, 30, 50]));
  const [filters, setFilters] = useState({
    search: "",
    ticker: "",
    sector: "",
    industry: "",
    source: "",
    bucket: "Primary",
    category: "",
    eventType: "",
    sourceMode: "",
    sentimentLabel: "",
    sentimentConfidence: "",
    tickerRelevance: "",
    sortBy: "sentimentStrength",
    timeWindowMinutes: ""
  });
  const [updateError, setUpdateError] = useState("");
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(false);
  const [autoRefreshSeconds, setAutoRefreshSeconds] = useState("90");
  const lastAutoRefreshAttemptRef = useRef(0);
  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, themePreference);
    } catch (error) {}
  }, [themePreference]);
  useEffect(() => {
    try {
      window.localStorage.setItem(DENSITY_STORAGE_KEY, compactMode ? "1" : "0");
    } catch (error) {}
  }, [compactMode]);
  useEffect(() => {
    try {
      window.localStorage.setItem(SOURCE_LEGEND_STORAGE_KEY, showSourceLegend ? "1" : "0");
    } catch (error) {}
  }, [showSourceLegend]);
  useEffect(() => {
    try {
      window.localStorage.setItem(FEED_LIMIT_STORAGE_KEY, String(feedRowLimit));
    } catch (error) {}
  }, [feedRowLimit]);
  useEffect(() => {
    const applyDocumentAppearance = () => {
      document.body.dataset.theme = resolveThemePreference(themePreference);
      document.body.dataset.density = compactMode ? "compact" : "comfortable";
    };
    applyDocumentAppearance();
    if (themePreference !== "system") {
      return undefined;
    }
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => applyDocumentAppearance();
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", handleChange);
      return () => mediaQuery.removeEventListener("change", handleChange);
    }
    mediaQuery.addListener(handleChange);
    return () => mediaQuery.removeListener(handleChange);
  }, [themePreference, compactMode]);
  const fetchDashboardData = async mounted => {
    try {
      const [stateResponse, articleResponse] = await Promise.all([fetch("/api/state"), fetch("/api/articles")]);
      const [statePayload, articlePayload] = await Promise.all([parseApiResponse(stateResponse, "Dashboard state request failed"), parseApiResponse(articleResponse, "Dashboard articles request failed")]);
      if (!mounted || mounted.current) {
        setState(statePayload);
        setArticlePool(articlePayload);
        setUpdateError("");
      }
    } catch (error) {
      if (!mounted || mounted.current) {
        setUpdateError(`Dashboard refresh failed: ${error}`);
      }
    }
  };
  useEffect(() => {
    const mounted = {
      current: true
    };
    fetchDashboardData(mounted);
    const interval = setInterval(() => fetchDashboardData(mounted), 10000);
    const cooldown = setInterval(() => {
      setState(current => {
        if (!current || current.update_in_progress || current.cooldown_remaining <= 0) {
          return current;
        }
        return {
          ...current,
          cooldown_remaining: current.cooldown_remaining - 1
        };
      });
    }, 1000);
    return () => {
      mounted.current = false;
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
    const referenceTimestampMs = Date.parse(state.generated_at || articlePool?.generated_at || "") || Date.now();
    const timeWindowMinutes = Number(filters.timeWindowMinutes || 0);
    const sentimentConfidenceThreshold = parseConfidenceThreshold(filters.sentimentConfidence);
    const tickerRelevanceThreshold = parseConfidenceThreshold(filters.tickerRelevance);
    const tickerMeta = Object.fromEntries((state.tickers || []).map(item => [item.ticker, item]));
    const marketRows = (articlePool?.articles || []).map(row => {
      const matchedTickers = row.matched_tickers || [];
      const primaryTicker = tickerMeta[matchedTickers[0]] || {};
      const bucketKeys = row.buckets || [];
      const bucketLabels = bucketKeys.map(value => bucketLabel(value));
      return {
        ticker: matchedTickers.join(", "),
        company: matchedTickers.length === 1 ? primaryTicker.company || "" : `${matchedTickers.length} matched tickers`,
        sector: matchedTickers.length === 1 ? primaryTicker.sector || "" : "",
        industry: matchedTickers.length === 1 ? primaryTicker.industry || "" : "",
        bucket_keys: bucketKeys,
        bucket_labels: bucketLabels,
        bucket_label: bucketLabels.includes("Primary") ? "Primary" : bucketLabels.includes("Related") ? "Related" : bucketLabels.includes("Review") ? "Review" : "Primary",
        has_rejection: bucketKeys.includes("rejections"),
        primary_category: row.primary_category || "",
        category_tags: row.category_tags || [],
        title: row.title,
        link: row.link,
        source_name: row.source_name,
        source_key: row.source_key || "",
        source_family: row.source_family || "",
        source_quality_tier: row.source_quality_tier || "",
        source_tier_rank: Number(row.source_tier_rank ?? sourceTierRank(row)),
        event_type: (row.event_types || [])[0] || "",
        event_type_list: row.event_types || [],
        is_earnings: (row.event_types || []).some(value => isEarningsEvent(value)),
        is_unstructured: isUnstructuredSource(row),
        signal_strength: Number(row.signal_strength || 0),
        signal_display: String(row.signal_strength ?? 0),
        published: row.published_at ? formatMetaTimestamp(row.published_at) : row.published_display || row.published_raw || row.published_at || "",
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
        needs_translation: Boolean(row.needs_translation),
        sentiment_label: row.sentiment_label || "",
        sentiment_score: Number(row.sentiment_score || 0),
        sentiment_confidence: Number(row.sentiment_confidence || 0),
        raw_sentiment_confidence: Number(row.raw_sentiment_confidence || 0),
        signal_confidence: Number(row.signal_confidence || 0),
        prediction_weight: Number(row.prediction_weight || 0),
        ticker_relevance_confidence: Number(row.ticker_relevance_confidence || 0),
        ticker_relevance_markers: row.ticker_relevance_markers || [],
        sentiment_pipeline_stage: row.sentiment_pipeline_stage || "",
        sentiment_model_used: row.sentiment_model_used || "",
        future_model_target: row.future_model_target || "",
        finbert_ready: Boolean(row.finbert_ready),
        finbert_readiness_reason: row.finbert_readiness_reason || "",
        finbert_model_name: row.finbert_model_name || "",
        finbert_model_available: Boolean(row.finbert_model_available),
        finbert_label: row.finbert_label || "",
        finbert_score: Number(row.finbert_score || 0),
        finbert_confidence: Number(row.finbert_confidence || 0),
        finbert_input_length: Number(row.finbert_input_length || 0),
        finbert_uses_translation: Boolean(row.finbert_uses_translation),
        market_impact_bias: row.market_impact_bias || "",
        sentiment_positive_markers: row.sentiment_positive_markers || [],
        sentiment_negative_markers: row.sentiment_negative_markers || []
      };
    });
    const sourceRows = marketRows.length ? marketRows : state.feed_rows || [];
    const normalizedRows = sourceRows.map(row => {
      const eventTypeList = row.event_type_list || (row.event_type ? [row.event_type] : []);
      const bucketKeys = row.bucket_keys || [];
      const bucketLabels = row.bucket_labels || (row.bucket_label ? [row.bucket_label] : []);
      return {
        ...row,
        published: row.published_at ? formatMetaTimestamp(row.published_at) : row.published || row.published_raw || "",
        bucket_keys: bucketKeys,
        bucket_labels: bucketLabels,
        bucket_label: bucketLabels.includes("Primary") ? "Primary" : bucketLabels.includes("Related") ? "Related" : bucketLabels.includes("Review") ? "Review" : row.bucket_label || "Primary",
        has_rejection: Boolean(row.has_rejection) || bucketKeys.includes("rejections"),
        primary_category: row.primary_category || "",
        category_tags: row.category_tags || [],
        event_type_list: eventTypeList,
        is_earnings: Boolean(row.is_earnings) || eventTypeList.some(value => isEarningsEvent(value)),
        is_unstructured: Boolean(row.is_unstructured) || isUnstructuredSource(row),
        source_tier_rank: Number(row.source_tier_rank ?? sourceTierRank(row)),
        needs_translation: Boolean(row.needs_translation),
        sentiment_label: row.sentiment_label || "",
        sentiment_score: Number(row.sentiment_score || 0),
        sentiment_confidence: Number(row.sentiment_confidence || 0),
        raw_sentiment_confidence: Number(row.raw_sentiment_confidence || 0),
        signal_confidence: Number(row.signal_confidence || 0),
        prediction_weight: Number(row.prediction_weight || 0),
        ticker_relevance_confidence: Number(row.ticker_relevance_confidence || 0),
        ticker_relevance_markers: row.ticker_relevance_markers || [],
        sentiment_pipeline_stage: row.sentiment_pipeline_stage || "",
        sentiment_model_used: row.sentiment_model_used || "",
        future_model_target: row.future_model_target || "",
        finbert_ready: Boolean(row.finbert_ready),
        finbert_readiness_reason: row.finbert_readiness_reason || "",
        finbert_model_name: row.finbert_model_name || "",
        finbert_model_available: Boolean(row.finbert_model_available),
        finbert_label: row.finbert_label || "",
        finbert_score: Number(row.finbert_score || 0),
        finbert_confidence: Number(row.finbert_confidence || 0),
        finbert_input_length: Number(row.finbert_input_length || 0),
        finbert_uses_translation: Boolean(row.finbert_uses_translation),
        market_impact_bias: row.market_impact_bias || "",
        sentiment_positive_markers: row.sentiment_positive_markers || [],
        sentiment_negative_markers: row.sentiment_negative_markers || []
      };
    });
    const filteredRows = normalizedRows.filter(row => {
      const searchBlob = [row.ticker, row.company, row.industry, row.source_name, row.event_type, row.title, row.primary_category, ...(row.category_tags || [])].join(" ").toLowerCase();
      const matchedTickers = row.matched_tickers || String(row.ticker || "").split(", ").filter(Boolean);
      const eventTypeMatches = !filters.eventType || (filters.eventType === "__earnings__" ? row.is_earnings : row.event_type === filters.eventType || (row.event_type_list || []).includes(filters.eventType));
      const bucketMatches = !filters.bucket ? row.bucket_label !== "Review" : filters.bucket === "Rejections" ? row.has_rejection : row.bucket_label === filters.bucket || (row.bucket_labels || []).includes(filters.bucket);
      const categoryMatches = !filters.category || row.primary_category === filters.category || (row.category_tags || []).includes(filters.category);
      const sourceModeMatches = !filters.sourceMode || (filters.sourceMode === "__unstructured__" ? row.is_unstructured : filters.sourceMode === "__structured__" ? isStructuredSource(row) : true);
      const sentimentLabelMatches = !filters.sentimentLabel || String(row.sentiment_label || "").toLowerCase() === filters.sentimentLabel;
      const freshnessMatches = withinFreshnessWindow(row, timeWindowMinutes, referenceTimestampMs);
      const sentimentConfidenceMatches = !sentimentConfidenceThreshold || Number(row.sentiment_confidence || 0) >= sentimentConfidenceThreshold;
      const tickerRelevanceMatches = !tickerRelevanceThreshold || Number(row.ticker_relevance_confidence || 0) >= tickerRelevanceThreshold;
      const rejectionVisibilityMatches = filters.bucket === "Rejections" ? row.has_rejection : !row.has_rejection;
      return (!filters.search || searchBlob.includes(filters.search.toLowerCase())) && (!filters.ticker || matchedTickers.includes(filters.ticker)) && (!filters.sector || row.sector === filters.sector) && (!filters.industry || row.industry === filters.industry) && (!filters.source || row.source_name === filters.source) && bucketMatches && categoryMatches && eventTypeMatches && sourceModeMatches && sentimentLabelMatches && freshnessMatches && sentimentConfidenceMatches && tickerRelevanceMatches && rejectionVisibilityMatches;
    });
    const rows = [...filteredRows];
    const sentimentStrength = row => Math.abs(Number(row.sentiment_score || 0)) * Number(row.sentiment_confidence || 0);
    rows.sort((left, right) => {
      if (filters.sortBy === "sentimentStrength") {
        return sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0) || Number(right.is_new) - Number(left.is_new) || left.ticker.localeCompare(right.ticker);
      }
      if (filters.sortBy === "predictionWeight") {
        return Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0) || sentimentStrength(right) - sentimentStrength(left) || Number(right.is_new) - Number(left.is_new) || left.ticker.localeCompare(right.ticker);
      }
      if (filters.sortBy === "ticker") {
        return left.ticker.localeCompare(right.ticker) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      if (filters.sortBy === "source") {
        return left.source_name.localeCompare(right.source_name) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      if (filters.sortBy === "new") {
        return Number(right.is_new) - Number(left.is_new) || sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      if (filters.sortBy === "published") {
        return parseArticleTimestampMs(right) - parseArticleTimestampMs(left) || sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      if (filters.sortBy === "sentimentConfidence") {
        return Number(right.sentiment_confidence || 0) - Number(left.sentiment_confidence || 0) || sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      if (filters.sortBy === "tickerRelevance") {
        return Number(right.ticker_relevance_confidence || 0) - Number(left.ticker_relevance_confidence || 0) || sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0);
      }
      return left.source_tier_rank - right.source_tier_rank || sentimentStrength(right) - sentimentStrength(left) || Number(right.prediction_weight || 0) - Number(left.prediction_weight || 0) || Number(right.is_new) - Number(left.is_new) || left.ticker.localeCompare(right.ticker);
    });
    return rows;
  }, [state, articlePool, filters]);
  const articleSummary = useMemo(() => {
    const rows = visibleRows;
    return {
      totalRows: rows.length,
      newRows: rows.filter(row => row.is_new).length,
      sourceCount: new Set(rows.map(row => row.source_name).filter(Boolean)).size
    };
  }, [visibleRows]);
  const sourceOptions = useMemo(() => {
    const options = new Set(state?.filters?.sources || []);
    (articlePool?.articles || []).forEach(row => {
      if (row?.source_name) options.add(row.source_name);
    });
    return Array.from(options).sort((left, right) => left.localeCompare(right));
  }, [state, articlePool]);
  const categoryOptions = useMemo(() => {
    const options = new Set(state?.filters?.categories || []);
    (articlePool?.articles || []).forEach(row => {
      if (row?.primary_category) options.add(row.primary_category);
      (row?.category_tags || []).forEach(value => {
        if (value) options.add(value);
      });
    });
    return Array.from(options).sort((left, right) => formatCategoryLabel(left).localeCompare(formatCategoryLabel(right)));
  }, [state, articlePool]);
  const displayedRows = useMemo(() => visibleRows.slice(0, feedRowLimit), [visibleRows, feedRowLimit]);
  const triggerUpdate = async () => {
    if (!state || state.update_in_progress || state.cooldown_remaining > 0) return;
    setUpdateError("");
    setState({
      ...state,
      update_in_progress: true
    });
    try {
      const response = await fetch("/api/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({})
      });
      const payload = await parseApiResponse(response, "Update request failed");
      if (!response.ok) {
        setUpdateError(payload.message || "Refresh failed.");
        setState(current => current ? {
          ...current,
          update_in_progress: false,
          cooldown_remaining: payload.cooldown_remaining || current.cooldown_remaining || 0,
          last_status: payload.message || current.last_status
        } : current);
        return;
      }
      const [nextStateResponse, nextArticlesResponse] = await Promise.all([fetch("/api/state"), fetch("/api/articles")]);
      const [nextState, nextArticles] = await Promise.all([parseApiResponse(nextStateResponse, "State reload failed after update"), parseApiResponse(nextArticlesResponse, "Article reload failed after update")]);
      setState(nextState);
      setArticlePool(nextArticles);
      setUpdateError("");
      if (filters.ticker) {
        const nextTickerResponse = await fetch(`/api/ticker/${filters.ticker}`);
        const nextTickerDetail = await parseApiResponse(nextTickerResponse, "Ticker detail reload failed after update");
        setTickerDetail(nextTickerDetail.ok ? nextTickerDetail : null);
      }
    } catch (error) {
      setUpdateError(`Refresh failed: ${error}`);
      setState(current => current ? {
        ...current,
        update_in_progress: false
      } : current);
    }
  };
  useEffect(() => {
    if (!autoRefreshEnabled) return;
    const refreshIntervalMs = Math.max(90000, Number(autoRefreshSeconds || 90) * 1000);
    const interval = setInterval(() => {
      if (!state || state.update_in_progress || state.cooldown_remaining > 0) {
        return;
      }
      const now = Date.now();
      if (now - lastAutoRefreshAttemptRef.current < refreshIntervalMs) {
        return;
      }
      lastAutoRefreshAttemptRef.current = now;
      triggerUpdate();
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefreshEnabled, autoRefreshSeconds, state, filters.ticker]);
  if (!state) {
    return /*#__PURE__*/React.createElement("div", {
      className: "wrap"
    }, /*#__PURE__*/React.createElement("section", {
      className: "hero"
    }, /*#__PURE__*/React.createElement("h1", null, "Stock News Dashboard"), /*#__PURE__*/React.createElement("p", {
      className: "subtitle"
    }, "Loading Flask + React dashboard state...")));
  }
  const buttonDisabled = state.update_in_progress || state.cooldown_remaining > 0;
  const buttonText = state.update_in_progress ? "Updating..." : state.cooldown_remaining > 0 ? `Update locked (${state.cooldown_remaining}s)` : "Update Watchlist";
  const momentumSummary = state.summary?.momentum || {};
  const chartSummary = state.summary?.charts || {};
  const categorySummary = state.summary?.categories || [];
  const quoteWorkerHealth = state.summary?.worker_health?.quote_service || {};
  const finbertWorkerHealth = state.summary?.worker_health?.finbert_backfill || {};
  const autoRefreshLabel = autoRefreshEnabled ? `Auto ${Number(autoRefreshSeconds || 90) === 90 ? "1m 30s" : `${Math.round(Number(autoRefreshSeconds || 90) / 60)}m`}` : "Auto Off";
  const setFilter = (name, value) => setFilters(current => ({
    ...current,
    [name]: value
  }));
  const selectTicker = ticker => setFilters(current => ({
    ...current,
    ticker: ticker || ""
  }));
  const clearFilters = () => setFilters({
    search: "",
    ticker: "",
    sector: "",
    industry: "",
    source: "",
    bucket: "Primary",
    category: "",
    eventType: "",
    sourceMode: "",
    sentimentLabel: "",
    sentimentConfidence: "",
    tickerRelevance: "",
    sortBy: "sentimentStrength",
    timeWindowMinutes: ""
  });
  const applyViewPreset = preset => {
    if (preset === "primaryFocus") {
      clearFilters();
      return;
    }
    if (preset === "bullishHighRelevance") {
      setFilters(current => ({
        ...current,
        bucket: "Primary",
        sentimentLabel: "bullish",
        tickerRelevance: "0.66",
        eventType: "",
        sourceMode: "",
        timeWindowMinutes: ""
      }));
      return;
    }
    if (preset === "earningsWatch") {
      setFilters(current => ({
        ...current,
        bucket: "Primary",
        eventType: "__earnings__",
        sentimentLabel: "",
        sourceMode: "",
        timeWindowMinutes: ""
      }));
      return;
    }
    if (preset === "recentMovers") {
      setFilters(current => ({
        ...current,
        bucket: "Primary",
        timeWindowMinutes: "60",
        sentimentConfidence: "0.4",
        sentimentLabel: "",
        sourceMode: "",
        eventType: ""
      }));
    }
  };
  const resetDisplaySettings = () => {
    setThemePreference("light");
    setCompactMode(false);
    setShowSourceLegend(true);
    setFeedRowLimit(DEFAULT_FEED_ROW_LIMIT);
  };
  const activeFilterPills = summarizeActiveFilters(filters);
  return /*#__PURE__*/React.createElement("div", {
    className: "wrap"
  }, /*#__PURE__*/React.createElement("section", {
    className: "hero"
  }, /*#__PURE__*/React.createElement("h1", null, "Stock News Dashboard"), /*#__PURE__*/React.createElement("p", {
    className: "subtitle"
  }, "Live stock-news feed powered by Flask, React, and SQLite."), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("div", {
    className: "toolbar-controls"
  }, /*#__PURE__*/React.createElement("button", {
    onClick: triggerUpdate,
    disabled: buttonDisabled
  }, buttonText), /*#__PURE__*/React.createElement("select", {
    className: "toolbar-select",
    value: autoRefreshSeconds,
    onChange: e => setAutoRefreshSeconds(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "90"
  }, "1m 30s"), /*#__PURE__*/React.createElement("option", {
    value: "180"
  }, "3m"), /*#__PURE__*/React.createElement("option", {
    value: "300"
  }, "5m")), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `secondary toggle-button ${autoRefreshEnabled ? "active" : "inactive"}`,
    onClick: () => setAutoRefreshEnabled(current => !current)
  }, autoRefreshLabel), /*#__PURE__*/React.createElement("span", {
    className: "toolbar-note"
  }, autoRefreshEnabled ? "Auto mode uses the same safe update path as the manual refresh button." : "Manual mode only refreshes when you click Update Watchlist."), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `secondary toolbar-utility ${settingsOpen ? "active" : ""}`,
    onClick: () => setSettingsOpen(current => !current)
  }, settingsOpen ? "Close Settings" : "Settings")), /*#__PURE__*/React.createElement("div", {
    className: "meta"
  }, /*#__PURE__*/React.createElement("span", {
    className: "market-pill"
  }, state.market_session || "Market Closed"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Snapshot:"), " ", state.generated_at_display || "Not generated yet"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Last manual refresh:"), " ", state.last_refresh_display || "Not refreshed yet"))), /*#__PURE__*/React.createElement("p", {
    className: "status"
  }, updateError || state.last_status), settingsOpen ? /*#__PURE__*/React.createElement("section", {
    className: "settings-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "settings-panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h3", null, "Display Settings"), /*#__PURE__*/React.createElement("p", {
    className: "settings-hint"
  }, "Theme and layout preferences are saved in this browser only.")), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      gap: 8,
      flexWrap: "wrap"
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "secondary settings-reset",
    onClick: clearFilters
  }, "Reset Filters"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "secondary settings-reset",
    onClick: resetDisplaySettings
  }, "Reset Display"))), /*#__PURE__*/React.createElement("div", {
    className: "settings-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "settings-field"
  }, /*#__PURE__*/React.createElement("label", null, "Theme"), /*#__PURE__*/React.createElement("select", {
    value: themePreference,
    onChange: e => setThemePreference(e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "light"
  }, "Light"), /*#__PURE__*/React.createElement("option", {
    value: "dark"
  }, "Dark"), /*#__PURE__*/React.createElement("option", {
    value: "system"
  }, "System"))), /*#__PURE__*/React.createElement("div", {
    className: "settings-field"
  }, /*#__PURE__*/React.createElement("label", null, "Visible Rows"), /*#__PURE__*/React.createElement("select", {
    value: String(feedRowLimit),
    onChange: e => setFeedRowLimit(Number(e.target.value))
  }, /*#__PURE__*/React.createElement("option", {
    value: "10"
  }, "10 rows"), /*#__PURE__*/React.createElement("option", {
    value: "20"
  }, "20 rows"), /*#__PURE__*/React.createElement("option", {
    value: "30"
  }, "30 rows"), /*#__PURE__*/React.createElement("option", {
    value: "50"
  }, "50 rows"))), /*#__PURE__*/React.createElement("label", {
    className: "settings-toggle"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: compactMode,
    onChange: e => setCompactMode(e.target.checked)
  }), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Compact density"), /*#__PURE__*/React.createElement("small", null, "Tighten spacing in cards, filters, and rows."))), /*#__PURE__*/React.createElement("label", {
    className: "settings-toggle"
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: showSourceLegend,
    onChange: e => setShowSourceLegend(e.target.checked)
  }), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Show source color key"), /*#__PURE__*/React.createElement("small", null, "Keep the left-side legend visible in the main feed."))))) : null, /*#__PURE__*/React.createElement("section", {
    className: "system-status-panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "system-status-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h3", null, "System Status"), /*#__PURE__*/React.createElement("p", {
    className: "settings-hint"
  }, "Quick read on model readiness, quote coverage, and live collector health.")), /*#__PURE__*/React.createElement("div", {
    className: "active-filter-pill"
  }, quoteCoverageSummary(quoteWorkerHealth), " quotes")), /*#__PURE__*/React.createElement("div", {
    className: "system-status-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "system-status-item"
  }, /*#__PURE__*/React.createElement("div", {
    className: "system-status-label"
  }, "FinBERT Runtime"), /*#__PURE__*/React.createElement("div", {
    className: "system-status-value"
  }, formatSentimentRuntime(state.summary?.sentiment_runtime)), /*#__PURE__*/React.createElement("div", {
    className: "system-status-note"
  }, "Model: ", state.summary?.sentiment_runtime?.finbert_model_name || "ProsusAI/finbert")), /*#__PURE__*/React.createElement("article", {
    className: "system-status-item"
  }, /*#__PURE__*/React.createElement("div", {
    className: "system-status-label"
  }, "Backfill Worker"), /*#__PURE__*/React.createElement("div", {
    className: "system-status-value"
  }, formatWorkerStatusLabel(finbertWorkerHealth)), /*#__PURE__*/React.createElement("div", {
    className: "system-status-note"
  }, state.summary?.finbert_backfill_status || "Backfill status will appear after the next refresh.")), /*#__PURE__*/React.createElement("article", {
    className: "system-status-item"
  }, /*#__PURE__*/React.createElement("div", {
    className: "system-status-label"
  }, "Quote Coverage"), /*#__PURE__*/React.createElement("div", {
    className: "system-status-value"
  }, quoteCoverageSummary(quoteWorkerHealth)), /*#__PURE__*/React.createElement("div", {
    className: "system-status-note"
  }, quoteCoverageNote(quoteWorkerHealth))), /*#__PURE__*/React.createElement("article", {
    className: "system-status-item"
  }, /*#__PURE__*/React.createElement("div", {
    className: "system-status-label"
  }, "Collector Health"), /*#__PURE__*/React.createElement("div", {
    className: "system-status-value"
  }, state.summary?.source_health_ok || 0, "/", state.summary?.source_health_total || 0), /*#__PURE__*/React.createElement("div", {
    className: "system-status-note"
  }, "Successful checks across active dashboard collectors."))), /*#__PURE__*/React.createElement("section", {
    className: "detail-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "detail-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "detail-label"
  }, "Pipeline Mode"), /*#__PURE__*/React.createElement("div", {
    className: "detail-value"
  }, formatPipelineMode(state.pipeline_mode)), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, "Collection architecture used for the latest refresh.")), /*#__PURE__*/React.createElement("article", {
    className: "detail-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "detail-label"
  }, "Collection Elapsed"), /*#__PURE__*/React.createElement("div", {
    className: "detail-value"
  }, Number(state.collection_elapsed_seconds || 0).toFixed(3), "s"), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, "Time recorded for the latest pipeline refresh.")), /*#__PURE__*/React.createElement("article", {
    className: "detail-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "detail-label"
  }, "Active Source Health"), /*#__PURE__*/React.createElement("div", {
    className: "detail-value"
  }, state.summary?.source_health_ok || 0, "/", state.summary?.source_health_total || 0), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, "Successful checks across active dashboard collectors.")), /*#__PURE__*/React.createElement("article", {
    className: "detail-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "detail-label"
  }, "Market Sentiment"), /*#__PURE__*/React.createElement("div", {
    className: "detail-value",
    style: {
      fontSize: 26
    }
  }, state.summary?.market_sentiment?.label || "Neutral"), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, "Score ", Number(state.summary?.market_sentiment?.score || 0).toFixed(2), " | Tone confidence ", Math.round(Number(state.summary?.market_sentiment?.confidence || 0) * 100), "% | Relevance ", Math.round(Number(state.summary?.market_sentiment?.avg_relevance_confidence || 0) * 100), "%", state.summary?.market_sentiment?.signal_confidence ? ` | Support signal ${Math.round(Number(state.summary.market_sentiment.signal_confidence || 0) * 100)}%` : "", " across the current visible article pool. FinBERT-ready: ", state.summary?.finbert_ready_count || 0, state.summary?.total_rows ? `/${state.summary.total_rows}` : "", state.summary?.finbert_applied_count ? ` | FinBERT active: ${state.summary.finbert_applied_count}` : "", state.summary?.translation_pending_count ? ` | Translation pending: ${state.summary.translation_pending_count}` : ""), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub",
    style: {
      marginTop: 6
    }
  }, "Runtime: ", formatSentimentRuntime(state.summary?.sentiment_runtime), " | Model: ", state.summary?.sentiment_runtime?.finbert_model_name || "ProsusAI/finbert"), state.summary?.finbert_backfill_status ? /*#__PURE__*/React.createElement("div", {
    className: "detail-sub",
    style: {
      marginTop: 6
    }
  }, "Backfill: ", state.summary.finbert_backfill_status) : null, state.summary?.worker_health?.quote_service ? /*#__PURE__*/React.createElement("div", {
    className: "detail-sub",
    style: {
      marginTop: 6
    }
  }, formatQuoteServiceHealth(state.summary.worker_health.quote_service)) : null))), /*#__PURE__*/React.createElement("section", {
    className: "summary-grid"
  }, /*#__PURE__*/React.createElement("article", {
    className: "summary-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "Tracked Tickers"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, state.summary?.ticker_count || 0)), /*#__PURE__*/React.createElement("article", {
    className: "summary-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "Visible Articles"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, articleSummary.totalRows || 0)), /*#__PURE__*/React.createElement("article", {
    className: "summary-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "New Since Refresh"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, articleSummary.newRows || 0)), /*#__PURE__*/React.createElement("article", {
    className: "summary-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "Visible Sources"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, articleSummary.sourceCount || 0)), /*#__PURE__*/React.createElement("article", {
    className: "summary-card"
  }, /*#__PURE__*/React.createElement("div", {
    className: "label"
  }, "Quote Coverage"), /*#__PURE__*/React.createElement("div", {
    className: "value"
  }, quoteCoverageSummary(quoteWorkerHealth)), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, quoteCoverageNote(quoteWorkerHealth)))), /*#__PURE__*/React.createElement("section", {
    className: "panel panel-feed"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Article Feed"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Deduped market article stream built from the latest SQLite-backed article pool, with ticker filters layered on top."), articlePool?.is_fallback ? /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle",
    style: {
      marginTop: 6
    }
  }, "No articles from the last 48 hours were found in the latest live window, so the feed is temporarily showing the most recent qualifying recent set from the last 72 hours.") : null)), /*#__PURE__*/React.createElement("div", {
    className: `feed-layout ${showSourceLegend ? "" : "feed-layout-wide"}`
  }, showSourceLegend ? /*#__PURE__*/React.createElement(SourceLegendCard, {
    rows: visibleRows
  }) : null, /*#__PURE__*/React.createElement("div", {
    className: "feed-main"
  }, /*#__PURE__*/React.createElement("div", {
    className: "filter-grid"
  }, /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Headline Search"), /*#__PURE__*/React.createElement("input", {
    value: filters.search,
    onChange: e => setFilter("search", e.target.value),
    placeholder: "Search ticker, company, source, or event"
  })), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Ticker"), /*#__PURE__*/React.createElement("select", {
    value: filters.ticker,
    onChange: e => setFilter("ticker", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All tickers"), (state.filters?.tickers || []).map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Sector"), /*#__PURE__*/React.createElement("select", {
    value: filters.sector,
    onChange: e => setFilter("sector", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All sectors"), (state.filters?.sectors || []).map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Industry"), /*#__PURE__*/React.createElement("select", {
    value: filters.industry,
    onChange: e => setFilter("industry", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All industries"), (state.filters?.industries || []).map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Source"), /*#__PURE__*/React.createElement("select", {
    value: filters.source,
    onChange: e => setFilter("source", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All sources"), sourceOptions.map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Category"), /*#__PURE__*/React.createElement("select", {
    value: filters.category,
    onChange: e => setFilter("category", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All categories"), categoryOptions.map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, formatCategoryLabel(value))))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Bucket"), /*#__PURE__*/React.createElement("select", {
    value: filters.bucket,
    onChange: e => setFilter("bucket", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "Primary"
  }, "Primary only"), /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All non-rejection buckets"), (state.filters?.buckets || []).filter(value => value !== "Primary").map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)), /*#__PURE__*/React.createElement("option", {
    value: "Rejections"
  }, "Rejections only")))), /*#__PURE__*/React.createElement("div", {
    className: "quick-filter-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "quick-filter-label"
  }, "Quick Views"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.eventType === "__earnings__" ? "active" : ""}`,
    onClick: () => setFilter("eventType", filters.eventType === "__earnings__" ? "" : "__earnings__")
  }, "Earnings News"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.sourceMode === "__structured__" ? "active" : ""}`,
    onClick: () => setFilter("sourceMode", filters.sourceMode === "__structured__" ? "" : "__structured__")
  }, "Structured Only"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.sourceMode === "__unstructured__" ? "active" : ""}`,
    onClick: () => setFilter("sourceMode", filters.sourceMode === "__unstructured__" ? "" : "__unstructured__")
  }, "Unstructured News"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.sentimentLabel === "bullish" ? "active" : ""}`,
    onClick: () => setFilter("sentimentLabel", filters.sentimentLabel === "bullish" ? "" : "bullish")
  }, "Bullish"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.sentimentLabel === "bearish" ? "active" : ""}`,
    onClick: () => setFilter("sentimentLabel", filters.sentimentLabel === "bearish" ? "" : "bearish")
  }, "Bearish"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: `quick-filter-chip ${filters.tickerRelevance === "0.66" ? "active" : ""}`,
    onClick: () => setFilter("tickerRelevance", filters.tickerRelevance === "0.66" ? "" : "0.66")
  }, "High Relevance")), /*#__PURE__*/React.createElement("div", {
    className: "preset-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "preset-label"
  }, "Presets"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "quick-filter-chip",
    onClick: () => applyViewPreset("primaryFocus")
  }, "Primary Focus"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "quick-filter-chip",
    onClick: () => applyViewPreset("bullishHighRelevance")
  }, "Bullish High Relevance"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "quick-filter-chip",
    onClick: () => applyViewPreset("earningsWatch")
  }, "Earnings Watch"), /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "quick-filter-chip",
    onClick: () => applyViewPreset("recentMovers")
  }, "Recent Movers")), activeFilterPills.length ? /*#__PURE__*/React.createElement("div", {
    className: "active-filter-row"
  }, /*#__PURE__*/React.createElement("span", {
    className: "active-filter-label"
  }, "Active Filters"), activeFilterPills.map(value => /*#__PURE__*/React.createElement("span", {
    key: value,
    className: "active-filter-pill"
  }, value))) : null, /*#__PURE__*/React.createElement("div", {
    className: "filter-grid",
    style: {
      marginTop: 12,
      gridTemplateColumns: "1fr 1fr 1fr 1fr 1fr 1fr auto"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Event Type"), /*#__PURE__*/React.createElement("select", {
    value: filters.eventType,
    onChange: e => setFilter("eventType", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All event types"), /*#__PURE__*/React.createElement("option", {
    value: "__earnings__"
  }, "All earnings news"), (state.filters?.event_types || []).map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value)))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Sentiment Confidence"), /*#__PURE__*/React.createElement("select", {
    value: filters.sentimentConfidence,
    onChange: e => setFilter("sentimentConfidence", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All confidence levels"), /*#__PURE__*/React.createElement("option", {
    value: "0.25"
  }, "25% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.4"
  }, "40% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.6"
  }, "60% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.75"
  }, "75% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.85"
  }, "85% and higher"))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Sentiment Direction"), /*#__PURE__*/React.createElement("select", {
    value: filters.sentimentLabel,
    onChange: e => setFilter("sentimentLabel", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All sentiment labels"), /*#__PURE__*/React.createElement("option", {
    value: "bullish"
  }, "Bullish"), /*#__PURE__*/React.createElement("option", {
    value: "bearish"
  }, "Bearish"), /*#__PURE__*/React.createElement("option", {
    value: "mixed"
  }, "Mixed"), /*#__PURE__*/React.createElement("option", {
    value: "neutral"
  }, "Neutral"))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Ticker Relevance"), /*#__PURE__*/React.createElement("select", {
    value: filters.tickerRelevance,
    onChange: e => setFilter("tickerRelevance", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All relevance levels"), /*#__PURE__*/React.createElement("option", {
    value: "0.4"
  }, "40% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.55"
  }, "55% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.66"
  }, "66% and higher"), /*#__PURE__*/React.createElement("option", {
    value: "0.75"
  }, "75% and higher"))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Sort By"), /*#__PURE__*/React.createElement("select", {
    value: filters.sortBy,
    onChange: e => setFilter("sortBy", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: "sentimentStrength"
  }, "Sentiment strength"), /*#__PURE__*/React.createElement("option", {
    value: "predictionWeight"
  }, "Prediction weight"), /*#__PURE__*/React.createElement("option", {
    value: "new"
  }, "New badge first"), /*#__PURE__*/React.createElement("option", {
    value: "published"
  }, "Published (newest first)"), /*#__PURE__*/React.createElement("option", {
    value: "sentimentConfidence"
  }, "Sentiment confidence"), /*#__PURE__*/React.createElement("option", {
    value: "tickerRelevance"
  }, "Ticker relevance"), /*#__PURE__*/React.createElement("option", {
    value: "ticker"
  }, "Ticker A-Z"), /*#__PURE__*/React.createElement("option", {
    value: "source"
  }, "Source A-Z"))), /*#__PURE__*/React.createElement("div", {
    className: "filter-field"
  }, /*#__PURE__*/React.createElement("label", null, "Freshness"), /*#__PURE__*/React.createElement("select", {
    value: filters.timeWindowMinutes,
    onChange: e => setFilter("timeWindowMinutes", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "Current live window"), /*#__PURE__*/React.createElement("option", {
    value: "3"
  }, "Last 3 minutes"), /*#__PURE__*/React.createElement("option", {
    value: "5"
  }, "Last 5 minutes"), /*#__PURE__*/React.createElement("option", {
    value: "10"
  }, "Last 10 minutes"), /*#__PURE__*/React.createElement("option", {
    value: "30"
  }, "Last 30 minutes"), /*#__PURE__*/React.createElement("option", {
    value: "60"
  }, "Last 60 minutes"), /*#__PURE__*/React.createElement("option", {
    value: "180"
  }, "Last 3 hours"), /*#__PURE__*/React.createElement("option", {
    value: "360"
  }, "Last 6 hours"), /*#__PURE__*/React.createElement("option", {
    value: "1440"
  }, "Last 24 hours"), /*#__PURE__*/React.createElement("option", {
    value: "2880"
  }, "Last 48 hours"))), /*#__PURE__*/React.createElement("div", {
    className: "table-note"
  }, "Showing top ", Math.min(displayedRows.length, feedRowLimit), " of ", visibleRows.length, " rows by current sort order"), /*#__PURE__*/React.createElement("div", {
    className: "filter-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "secondary",
    type: "button",
    onClick: clearFilters
  }, "Clear Filters"))), /*#__PURE__*/React.createElement(FeedTable, {
    rows: displayedRows,
    onSelectTicker: selectTicker
  })))), /*#__PURE__*/React.createElement(ChartsPanel, {
    charts: chartSummary,
    categories: categorySummary
  }), /*#__PURE__*/React.createElement(SentimentAuditPanel, {
    audit: state.summary?.sentiment_audit
  }), /*#__PURE__*/React.createElement("section", {
    className: "panel panel-workspace"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Ticker Workspace"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Focused analysis area for the selected ticker, built to hold relevant news, source history, and the later prediction layer."))), /*#__PURE__*/React.createElement(TickerWorkspace, {
    tickerDetail: tickerDetail
  })), /*#__PURE__*/React.createElement("section", {
    className: "panel panel-universe"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Ticker Universe"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Click any tracked ticker to open a focused workspace for relevant stories, source history, and later prediction output."))), /*#__PURE__*/React.createElement(TickerCoverageTable, {
    tickers: state.tickers || [],
    onSelectTicker: selectTicker
  })), /*#__PURE__*/React.createElement("section", {
    className: "panel panel-monitoring"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "System Monitoring"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Active-source monitoring summary for the latest refresh, grouped by source so ticker-specific and shared-pool collectors are easier to compare."))), /*#__PURE__*/React.createElement(SourceHealthTable, {
    rows: state.source_health || []
  }))));
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(App, null));
