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
  const alternatesEl = document.getElementById("filter-alternates");
  const refreshBtn = document.getElementById("props-search-refresh");

  function readFilters(refresh) {
    return {
      bookmaker: bookEl?.value || "consensus",
      market_type: marketEl?.value || "",
      min_odds: minOddsEl?.value ?? "",
      line_kind: lineKindEl?.value || "both",
      line_value: lineValueEl?.value ?? "",
      actionable_only: !!actionableEl?.checked,
      include_alternates: !!alternatesEl?.checked || lineKindEl?.value === "alternate",
      limit: 100,
      scan: !!refresh,
      refresh: !!refresh,
    };
  }

  async function runSearch(refresh = false) {
    if (metaEl) metaEl.textContent = refresh ? "Refreshing props from sportsbooks…" : "Searching props…";
    const params = buildPropSearchQuery(readFilters(refresh));
    try {
      const data = await fetchJSON(`/api/props/search?${params.toString()}`);
      const hint = data.hint ? ` ${data.hint}` : "";
      if (metaEl) {
        metaEl.textContent = `${data.total_matched || 0} props · ${data.bookmaker_label || "Consensus"}${hint}`;
      }
      renderPropExplorerList(resultsEl, data.props || [], {
        emptyMessage: data.hint || "No props match these filters. Try a different book or refresh lines.",
      });
    } catch (e) {
      if (metaEl) metaEl.textContent = "Could not load props.";
      renderPropExplorerList(resultsEl, [], { emptyMessage: e.message || "Search failed." });
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

    await runSearch(false);
  }

  init().catch(() => {
    renderPropExplorerList(resultsEl, [], { emptyMessage: "Could not initialize props search." });
  });
})();
