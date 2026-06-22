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
      limit: 200,
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
      const res = await fetch(`/api/props/search?${params.toString()}`);
      if (res.status === 401) {
        if (metaEl) metaEl.textContent = "Sign in required";
        if (window.renderPropsAuthGate) {
          window.renderPropsAuthGate(resultsEl, "/mlb/props");
        } else {
          renderPropExplorerList(resultsEl, [], { emptyMessage: "Sign in to view player props." });
        }
        return;
      }
      if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`);
      const data = await res.json();
      const hint = data.hint ? ` ${data.hint}` : "";
      if (metaEl) {
        const coverage =
          data.games_on_slate && data.games_with_props != null
            ? ` · ${data.games_with_props}/${data.games_on_slate} games`
            : "";
        metaEl.textContent = `${data.total_matched || 0} props · ${data.total_very_strong || 0} very strong · ${data.bookmaker_label || "Consensus"}${coverage}${hint}`;
      }
      renderPropExplorerList(resultsEl, data.props || [], {
        emptyMessage: data.hint || "No props match these filters. Try a different book or refresh lines.",
      });
    } catch (e) {
      if (metaEl) metaEl.textContent = "Could not load props.";
      renderPropExplorerList(resultsEl, [], { emptyMessage: e.message || "Search failed." });
    }
  }

  function fmtOdds(value) {
    if (typeof window.fmtAmericanOdds === "function") return window.fmtAmericanOdds(value);
    if (value == null || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    return n > 0 ? `+${n}` : String(n);
  }

  function renderParlayBuilderResults(container, { legs, props, evalData, legCount, targetDelta }) {
    if (!container) return;
    const modalProps = (props || []).map((row, i) =>
      typeof window.propFromParlayRow === "function"
        ? window.propFromParlayRow(legs?.[i], row)
        : row
    );
    if (!modalProps.length && legs?.length) {
      legs.forEach((leg) => {
        const normalized =
          typeof window.propFromParlayRow === "function"
            ? window.propFromParlayRow(leg, leg)
            : leg;
        if (normalized) modalProps.push(normalized);
      });
    }
    if (!modalProps.length) {
      container.innerHTML = "";
      return;
    }

    const american = fmtOdds(evalData?.american_payout);
    const delta =
      targetDelta != null
        ? ` (${targetDelta >= 0 ? "+" : ""}${targetDelta} vs target)`
        : "";

    const legHtml = modalProps
      .map((prop, i) => {
        const leg = legs?.[i] || prop;
        const sideRaw = prop.recommended_side || leg.side || "over";
        const side = sideRaw === "under" ? "U" : "O";
        const odds = fmtOdds(prop.recommended_odds ?? leg.american_odds);
        const photo = prop.photo_url
          ? `<img class="dash-player-photo" src="${prop.photo_url}" alt="" width="36" height="36" loading="lazy" />`
          : "";
        const formRow =
          typeof window.propFormRowCompact === "function"
            ? window.propFormRowCompact(prop, sideRaw)
            : "";
        return `<button type="button" class="dash-parlay-leg-card parlay-builder-leg-card" data-open-parlay-prop="${i}" aria-label="View ${prop.player} stats">
          ${photo}
          <strong>${prop.player || leg.player}</strong>
          <span class="parlay-leg-line">${prop.market_label || leg.market_label || prop.market_type || leg.market_type} ${side}${prop.line ?? leg.line}</span>
          ${formRow}
          <span class="parlay-leg-odds">${odds}</span>
        </button>${i < modalProps.length - 1 ? '<span class="dash-parlay-plus">+</span>' : ""}`;
      })
      .join("");

    container.innerHTML = `
      <p class="dash-parlay-sublabel">Built from best L5 · L10 · season form · ${legCount || modalProps.length} legs${delta} · tap a player for stats</p>
      <div class="dash-parlay-legs parlay-builder-legs">${legHtml}</div>
      <div class="dash-parlay-foot parlay-builder-actions">
        <div class="dash-parlay-odds">
          <span class="dash-parlay-odds-lbl">Parlay odds</span>
          <strong>${american}</strong>
        </div>
        <div class="parlay-builder-actions">
          <button type="button" id="parlay-add-slip" class="home-props-fill-btn dash-btn dash-btn-primary">Add to prop slip</button>
          <a class="home-props-fill-btn home-props-fill-btn-ghost" href="/prop_slip.html">Open slip</a>
        </div>
      </div>`;

    if (typeof window.wireParlayLegModals === "function") {
      window.wireParlayLegModals(container, modalProps);
    }

    document.getElementById("parlay-add-slip")?.addEventListener("click", () => {
      const slipLegs = legs || [];
      if (typeof window.savePropSlipLegs === "function") {
        window.savePropSlipLegs(slipLegs);
        if (typeof window.renderPropSlipPanel === "function") window.renderPropSlipPanel();
        document.getElementById("prop-slip-panel")?.classList.add("prop-slip-panel--open");
      }
    });
  }

  async function loadTracker() {
    const el = document.getElementById("props-tracker-stats");
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

  async function buildParlay(e) {
    e?.preventDefault();
    const meta = document.getElementById("parlay-builder-meta");
    const results = document.getElementById("parlay-builder-results");
    const legCount = Math.max(2, Math.min(25, Number(document.getElementById("parlay-leg-count")?.value) || 5));
    const targetRaw = document.getElementById("parlay-target-odds")?.value;
    const targetAmerican = targetRaw !== "" && targetRaw != null ? Number(targetRaw) : null;
    const bookmaker = bookEl?.value || "draftkings";

    if (meta) meta.textContent = "Scanning slate and building parlay…";
    if (results) results.innerHTML = "";

    try {
      const res = await fetch("/api/parlay/props/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          leg_count: legCount,
          target_american: targetAmerican,
          bookmaker,
        }),
      });
      if (res.status === 401) {
        if (meta) meta.textContent = "Sign in required to build parlays.";
        return;
      }
      const data = await res.json();
      if (data.status !== "ok") {
        if (meta) meta.textContent = data.message || "Could not build parlay.";
        return;
      }

      const evalData = data.eval || {};
      const delta = data.target_delta;
      if (meta) {
        const americanPreview = fmtOdds(evalData.american_payout);
        const deltaText =
          delta != null ? ` (${delta >= 0 ? "+" : ""}${delta} vs target)` : "";
        meta.textContent = `${data.leg_count} legs · ${americanPreview}${deltaText} · pool ${data.pool_size || "—"} · ${data.games_with_props || "?"}/${data.games_on_slate || "?"} games`;
      }

      if (results) {
        renderParlayBuilderResults(results, {
          legs: data.legs || [],
          props: data.props || [],
          evalData,
          legCount: data.leg_count,
          targetDelta: delta,
        });
      }
    } catch (err) {
      if (meta) meta.textContent = err.message || "Build failed.";
    }
  }

  async function init() {
    if (typeof window.ensureAppReady === "function") {
      await window.ensureAppReady();
    } else {
      await loadPublicFeatures();
      initPropSlipUi();
    }
    initSiteChrome();
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

    document.getElementById("parlay-builder-form")?.addEventListener("submit", buildParlay);

    await loadTracker();
    await runSearch(false);
  }

  init().catch(() => {
    renderPropExplorerList(resultsEl, [], { emptyMessage: "Could not initialize props search." });
  });
})();
