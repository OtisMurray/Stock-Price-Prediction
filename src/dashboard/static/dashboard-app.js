const {
  useEffect,
  useMemo,
  useState
} = React;
const FEED_ROW_LIMIT = 20;
function formatPipelineMode(value) {
  if (!value) return "Shared Source Pool";
  return value.split("_").filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
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
        sortSignal: Number(row.signal_strength || 0),
        _key: `${bucketKey}-${row.link || row.title}-${index}`
      });
    });
  });
  rows.sort((left, right) => right.sortSignal - left.sortSignal || Number(right.is_new) - Number(left.is_new) || String(left.title || "").localeCompare(String(right.title || "")));
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
  }, /*#__PURE__*/React.createElement("table", null, /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, /*#__PURE__*/React.createElement("th", null, "Matched Tickers"), /*#__PURE__*/React.createElement("th", null, "Headline"), /*#__PURE__*/React.createElement("th", null, "Source"), /*#__PURE__*/React.createElement("th", null, "Bucket"), /*#__PURE__*/React.createElement("th", null, "Event"), /*#__PURE__*/React.createElement("th", null, "Ticker Count"), /*#__PURE__*/React.createElement("th", null, "Signal"), /*#__PURE__*/React.createElement("th", null, "Published"))), /*#__PURE__*/React.createElement("tbody", null, rows.map((row, index) => {
    const sentimentMarkers = [...(row.sentiment_positive_markers || []), ...(row.sentiment_negative_markers || [])].slice(0, 3).join(", ");
    const relevanceMarkers = tickerRelevanceMarkersText(row);
    const modelSummary = `${row.sentiment_model_used || "rule_based"}${row.finbert_label ? ` / FinBERT ${row.finbert_label} (${Math.round(Number(row.finbert_confidence || 0) * 100)}%)` : ""}`;
    const sentimentPercent = Math.round(Number(row.sentiment_confidence || 0) * 100);
    const signalPercent = Math.round(Number(row.signal_confidence || row.sentiment_confidence || 0) * 100);
    const relevancePercent = Math.round(Number(row.ticker_relevance_confidence || 0) * 100);
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
    }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "First captured:"), " ", formatMetaTimestamp(row.first_seen_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Last observed:"), " ", formatMetaTimestamp(row.last_seen_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Latest refresh capture:"), " ", formatMetaTimestamp(row.collected_at)), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Sentiment confidence:"), " ", sentimentPercent, "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Signal confidence:"), " ", signalPercent, "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Ticker relevance:"), " ", relevancePercent, "%"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Model:"), " ", modelSummary), row.market_impact_bias ? /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Market bias:"), " ", row.market_impact_bias) : null)), /*#__PURE__*/React.createElement("td", null, row.source_name), /*#__PURE__*/React.createElement("td", null, row.bucket_label), /*#__PURE__*/React.createElement("td", null, row.event_type || "general"), /*#__PURE__*/React.createElement("td", null, row.matched_ticker_count || 0), /*#__PURE__*/React.createElement("td", null, row.signal_display), /*#__PURE__*/React.createElement("td", null, row.published || "No timestamp available"));
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
  }, /*#__PURE__*/React.createElement("td", null, row.source_name), /*#__PURE__*/React.createElement("td", null, row.modeLabel), /*#__PURE__*/React.createElement("td", null, row.statusLabel), /*#__PURE__*/React.createElement("td", null, row.runs_seen), /*#__PURE__*/React.createElement("td", null, row.fetched_count), /*#__PURE__*/React.createElement("td", null, row.matched_count), /*#__PURE__*/React.createElement("td", null, row.visible_story_count || 0), /*#__PURE__*/React.createElement("td", null, row.visible_new_count || 0), /*#__PURE__*/React.createElement("td", null, Number(row.avg_elapsed_seconds || 0).toFixed(3), "s"), /*#__PURE__*/React.createElement("td", null, row.last_collected_at || "-"), /*#__PURE__*/React.createElement("td", null, row.errorPreview || "-"))))));
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
  }, /*#__PURE__*/React.createElement("td", null, row.source_name), /*#__PURE__*/React.createElement("td", null, row.runs_seen), /*#__PURE__*/React.createElement("td", null, row.ok_runs), /*#__PURE__*/React.createElement("td", null, row.total_fetched_count), /*#__PURE__*/React.createElement("td", null, row.total_matched_count), /*#__PURE__*/React.createElement("td", null, Number(row.avg_elapsed_seconds || 0).toFixed(3), "s"), /*#__PURE__*/React.createElement("td", null, row.last_collected_at || "-"))))));
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
  }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Bucket:"), " ", row.bucketLabel), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Source:"), " ", row.source_name || "Unknown"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Signal:"), " ", row.signal_strength ?? 0), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Published:"), " ", row.published || "Not recorded"), row.is_earnings ? /*#__PURE__*/React.createElement("span", {
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
  }, "Score ", Number(sentiment.score || 0).toFixed(2), " | Tone confidence ", Math.round(Number(sentiment.confidence || 0) * 100), "% | Relevance ", Math.round(Number(sentiment.avg_relevance_confidence || 0) * 100), "%", sentiment.signal_confidence ? ` | Signal ${Math.round(Number(sentiment.signal_confidence || 0) * 100)}%` : "", " from current ticker coverage.")), /*#__PURE__*/React.createElement("article", {
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
  }, "Top stored rows for the selected ticker, ordered by current signal strength.")), /*#__PURE__*/React.createElement(TickerStoryList, {
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
  const [filters, setFilters] = useState({
    search: "",
    ticker: "",
    sector: "",
    industry: "",
    source: "",
    bucket: "",
    eventType: "",
    sourceMode: "",
    sentimentLabel: "",
    sentimentConfidence: "",
    tickerRelevance: "",
    sortBy: "signal",
    timeWindowMinutes: ""
  });
  const [updateError, setUpdateError] = useState("");
  useEffect(() => {
    let mounted = true;
    const fetchDashboardData = async () => {
      try {
        const [stateResponse, articleResponse] = await Promise.all([fetch("/api/state"), fetch("/api/articles")]);
        if (!stateResponse.ok || !articleResponse.ok) return;
        const [statePayload, articlePayload] = await Promise.all([stateResponse.json(), articleResponse.json()]);
        if (mounted) {
          setState(statePayload);
          setArticlePool(articlePayload);
        }
      } catch (error) {
        if (mounted) setUpdateError(`Dashboard refresh failed: ${error}`);
      }
    };
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 10000);
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
        bucket_label: bucketLabels.join(", ") || "Primary",
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
        bucket_label: bucketLabels.join(", ") || row.bucket_label || "Primary",
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
      const searchBlob = [row.ticker, row.company, row.industry, row.source_name, row.event_type, row.title].join(" ").toLowerCase();
      const matchedTickers = row.matched_tickers || String(row.ticker || "").split(", ").filter(Boolean);
      const eventTypeMatches = !filters.eventType || (filters.eventType === "__earnings__" ? row.is_earnings : row.event_type === filters.eventType || (row.event_type_list || []).includes(filters.eventType));
      const bucketMatches = !filters.bucket || row.bucket_label === filters.bucket || (row.bucket_labels || []).includes(filters.bucket);
      const sourceModeMatches = !filters.sourceMode || (filters.sourceMode === "__unstructured__" ? row.is_unstructured : filters.sourceMode === "__structured__" ? isStructuredSource(row) : true);
      const sentimentLabelMatches = !filters.sentimentLabel || String(row.sentiment_label || "").toLowerCase() === filters.sentimentLabel;
      const freshnessMatches = withinFreshnessWindow(row, timeWindowMinutes, referenceTimestampMs);
      const sentimentConfidenceMatches = !sentimentConfidenceThreshold || Number(row.sentiment_confidence || 0) >= sentimentConfidenceThreshold;
      const tickerRelevanceMatches = !tickerRelevanceThreshold || Number(row.ticker_relevance_confidence || 0) >= tickerRelevanceThreshold;
      return (!filters.search || searchBlob.includes(filters.search.toLowerCase())) && (!filters.ticker || matchedTickers.includes(filters.ticker)) && (!filters.sector || row.sector === filters.sector) && (!filters.industry || row.industry === filters.industry) && (!filters.source || row.source_name === filters.source) && bucketMatches && eventTypeMatches && sourceModeMatches && sentimentLabelMatches && freshnessMatches && sentimentConfidenceMatches && tickerRelevanceMatches;
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
      if (filters.sortBy === "published") {
        return parseArticleTimestampMs(right) - parseArticleTimestampMs(left) || right.signal_strength - left.signal_strength;
      }
      if (filters.sortBy === "sentimentConfidence") {
        return Number(right.sentiment_confidence || 0) - Number(left.sentiment_confidence || 0) || Number(right.ticker_relevance_confidence || 0) - Number(left.ticker_relevance_confidence || 0) || right.signal_strength - left.signal_strength;
      }
      if (filters.sortBy === "tickerRelevance") {
        return Number(right.ticker_relevance_confidence || 0) - Number(left.ticker_relevance_confidence || 0) || Number(right.sentiment_confidence || 0) - Number(left.sentiment_confidence || 0) || right.signal_strength - left.signal_strength;
      }
      return left.source_tier_rank - right.source_tier_rank || right.signal_strength - left.signal_strength || Number(right.is_new) - Number(left.is_new) || left.ticker.localeCompare(right.ticker);
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
  const displayedRows = useMemo(() => visibleRows.slice(0, FEED_ROW_LIMIT), [visibleRows]);
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
      const payload = await response.json();
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
      const [nextState, nextArticles] = await Promise.all([fetch("/api/state").then(res => res.json()), fetch("/api/articles").then(res => res.json())]);
      setState(nextState);
      setArticlePool(nextArticles);
      if (filters.ticker) {
        const nextTickerDetail = await fetch(`/api/ticker/${filters.ticker}`).then(res => res.json());
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
    bucket: "",
    eventType: "",
    sourceMode: "",
    sentimentLabel: "",
    sentimentConfidence: "",
    tickerRelevance: "",
    sortBy: "signal",
    timeWindowMinutes: ""
  });
  return /*#__PURE__*/React.createElement("div", {
    className: "wrap"
  }, /*#__PURE__*/React.createElement("section", {
    className: "hero"
  }, /*#__PURE__*/React.createElement("h1", null, "Stock News Dashboard"), /*#__PURE__*/React.createElement("p", {
    className: "subtitle"
  }, "Live stock-news feed powered by Flask, React, and SQLite."), /*#__PURE__*/React.createElement("div", {
    className: "toolbar"
  }, /*#__PURE__*/React.createElement("button", {
    onClick: triggerUpdate,
    disabled: buttonDisabled
  }, buttonText), /*#__PURE__*/React.createElement("div", {
    className: "meta"
  }, /*#__PURE__*/React.createElement("span", {
    className: "market-pill"
  }, state.market_session || "Market Closed"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Snapshot:"), " ", state.generated_at_display || "Not generated yet"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, "Last manual refresh:"), " ", state.last_refresh_display || "Not refreshed yet"))), /*#__PURE__*/React.createElement("p", {
    className: "status"
  }, updateError || state.last_status), /*#__PURE__*/React.createElement("section", {
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
  }, "Source Health"), /*#__PURE__*/React.createElement("div", {
    className: "detail-value"
  }, state.summary?.source_health_ok || 0, "/", state.summary?.source_health_total || 0), /*#__PURE__*/React.createElement("div", {
    className: "detail-sub"
  }, "Successful source checks recorded in the latest refresh.")), /*#__PURE__*/React.createElement("article", {
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
  }, "Score ", Number(state.summary?.market_sentiment?.score || 0).toFixed(2), " | Tone confidence ", Math.round(Number(state.summary?.market_sentiment?.confidence || 0) * 100), "% | Relevance ", Math.round(Number(state.summary?.market_sentiment?.avg_relevance_confidence || 0) * 100), "%", state.summary?.market_sentiment?.signal_confidence ? ` | Signal ${Math.round(Number(state.summary.market_sentiment.signal_confidence || 0) * 100)}%` : "", " across the current visible article pool. FinBERT-ready: ", state.summary?.finbert_ready_count || 0, articleSummary.totalRows ? `/${articleSummary.totalRows}` : "", state.summary?.finbert_applied_count ? ` | FinBERT active: ${state.summary.finbert_applied_count}` : "", state.summary?.translation_pending_count ? ` | Translation pending: ${state.summary.translation_pending_count}` : "")))), /*#__PURE__*/React.createElement("section", {
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
  }, articleSummary.sourceCount || 0))), /*#__PURE__*/React.createElement("section", {
    className: "panel"
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
  }, /*#__PURE__*/React.createElement("label", null, "Bucket"), /*#__PURE__*/React.createElement("select", {
    value: filters.bucket,
    onChange: e => setFilter("bucket", e.target.value)
  }, /*#__PURE__*/React.createElement("option", {
    value: ""
  }, "All buckets"), (state.filters?.buckets || []).map(value => /*#__PURE__*/React.createElement("option", {
    key: value,
    value: value
  }, value))))), /*#__PURE__*/React.createElement("div", {
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
    value: "signal"
  }, "Signal (high to low)"), /*#__PURE__*/React.createElement("option", {
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
  }, "Showing top ", Math.min(displayedRows.length, FEED_ROW_LIMIT), " of ", visibleRows.length, " rows by current sort order"), /*#__PURE__*/React.createElement("div", {
    className: "filter-actions"
  }, /*#__PURE__*/React.createElement("button", {
    className: "secondary",
    type: "button",
    onClick: clearFilters
  }, "Clear Filters"))), /*#__PURE__*/React.createElement(FeedTable, {
    rows: displayedRows,
    onSelectTicker: selectTicker
  })), /*#__PURE__*/React.createElement("section", {
    className: "panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Ticker Workspace"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Focused analysis area for the selected ticker, built to hold relevant news, source history, and the later prediction layer."))), /*#__PURE__*/React.createElement(TickerWorkspace, {
    tickerDetail: tickerDetail
  })), /*#__PURE__*/React.createElement("section", {
    className: "panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "Ticker Universe"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "Click any tracked ticker to open a focused workspace for relevant stories, source history, and later prediction output."))), /*#__PURE__*/React.createElement(TickerCoverageTable, {
    tickers: state.tickers || [],
    onSelectTicker: selectTicker
  })), /*#__PURE__*/React.createElement("section", {
    className: "panel"
  }, /*#__PURE__*/React.createElement("div", {
    className: "panel-head"
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("h2", null, "System Monitoring"), /*#__PURE__*/React.createElement("p", {
    className: "panel-subtitle"
  }, "High-level source monitoring summary for the latest refresh, grouped by source so ticker-specific and shared-pool collectors are easier to compare."))), /*#__PURE__*/React.createElement(SourceHealthTable, {
    rows: state.source_health || []
  })));
}
ReactDOM.createRoot(document.getElementById("root")).render(/*#__PURE__*/React.createElement(App, null));
