(function () {
  const form = document.getElementById("props-search-form");
  const resultsEl = document.getElementById("props-search-results");
  const metaEl = document.getElementById("props-search-meta");
  const bookEl = document.getElementById("filter-book");
  const marketEl = document.getElementById("filter-market");
  const minOddsEl = document.getElementById("filter-min-odds");
  const lineKindEl = document.getElementById("filter-line-kind");
  const lineValueEl = document.getElementById("filter-line-value");
  const actionableEl = document.getElementById("filter-actionable");
  const veryStrongEl = document.getElementById("filter-very-strong");
  const alternatesEl = document.getElementById("filter-alternates");
  const refreshBtn = document.getElementById("props-search-refresh");

  function readFilters(refresh) {
    return {
      bookmaker: bookEl?.value || "draftkings",
      market_type: marketEl?.value || "",
      min_odds: minOddsEl?.value ?? "",
      line_kind: lineKindEl?.value || "both",
      line_value: lineValueEl?.value ?? "",
      actionable_only: !!actionableEl?.checked,
      very_strong_only: !!veryStrongEl?.checked,
      include_alternates: !!alternatesEl?.checked || lineKindEl?.value === "alternate",
      limit: 100,
      scan: !!refresh,
      refresh: !!refresh,
    };
  }

  async function runSearch(refresh = false) {
    if (metaEl) metaEl.textContent = refresh ? "Refreshing props from sportsbooks…" : "Searching props…";
    const filters = readFilters(refresh);
    if (!refresh) {
      try {
        const cacheMeta = await fetchJSON("/api/props/cache-meta");
        if (cacheMeta.requires_refresh) {
          filters.scan = true;
          filters.refresh = true;
        }
      } catch (_) {}
    }
    const params = buildPropSearchQuery(filters);
    try {
      const data = await fetchJSON(`/api/props/search?${params.toString()}`);
      const hint = data.hint ? ` ${data.hint}` : "";
      if (metaEl) {
        metaEl.textContent = `${data.total_matched || 0} props · ${data.total_very_strong || 0} very strong · ${data.bookmaker_label || "Consensus"}${hint}`;
      }
      renderPropExplorerList(resultsEl, data.props || [], {
        emptyMessage: data.hint || "No props match these filters. Try a different book or refresh lines.",
      });
    } catch (e) {
      if (metaEl) metaEl.textContent = "Could not load props.";
      renderPropExplorerList(resultsEl, [], { emptyMessage: e.message || "Search failed." });
    }
  }

  async function loadTracker() {
    const el = document.getElementById("props-tracker-stats");
    if (!el) return;
    try {
      const data = await fetchJSON("/api/props/tracker/summary?days=30");
      const buckets = data.line_strength || {};
      const fmtRate = (rate) =>
        rate != null ? `${(rate * 100).toFixed(0)}% hit` : "—";
      const cards = ["strong", "moderate", "weak"].map((key) => {
        const b = buckets[key] || {};
        const label = key.charAt(0).toUpperCase() + key.slice(1);
        return `<div class="props-tracker-stat"><strong>${fmtRate(b.hit_rate)}</strong><span>${label} · ${b.settled || 0} graded / ${b.offered || 0} offered</span></div>`;
      });
      const overall =
        data.overall_hit_rate != null
          ? `${(data.overall_hit_rate * 100).toFixed(0)}% overall (${data.props_settled || 0} graded)`
          : `${data.props_logged || 0} logged — grading starts after games finish`;
      el.innerHTML = `<p class="props-tracker-note">${overall}</p>${cards.join("")}`;
    } catch (_) {
      el.textContent = "Tracker unavailable.";
    }
  }

  async function init() {
    initLiveTicker("live-ticker", { sport: "mlb" });
    await initPropBookSelect(bookEl);

    try {
      const markets = await fetchJSON("/api/props/markets");
      (markets.markets || []).forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.key;
        opt.textContent = m.label;
        marketEl.appendChild(opt);
      });
    } catch (_) {
      /* keep All types */
    }

    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      runSearch(false);
    });

    refreshBtn?.addEventListener("click", () => runSearch(true));

    await loadTracker();
    await runSearch(false);
  }

  init().catch(() => {
    renderPropExplorerList(resultsEl, [], { emptyMessage: "Could not initialize props search." });
  });
})();
