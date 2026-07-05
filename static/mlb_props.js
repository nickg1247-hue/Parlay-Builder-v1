(function () {
  const form = document.getElementById("props-search-form");
  const resultsEl = document.getElementById("props-search-results");
  const metaEl = document.getElementById("props-search-meta");
  const bookEl = document.getElementById("filter-book");
  const marketEl = document.getElementById("filter-market");
  const minOddsEl = document.getElementById("filter-min-odds");
  const lineKindEl = document.getElementById("filter-line-kind");
  const sideEl = document.getElementById("filter-side");
  const lineValueEl = document.getElementById("filter-line-value");
  const actionableEl = document.getElementById("filter-actionable");
  const veryStrongEl = document.getElementById("filter-very-strong");
  const alternatesEl = document.getElementById("filter-alternates");
  const sortEl = document.getElementById("filter-sort");
  const riskEl = document.getElementById("filter-risk");
  const minScoreEl = document.getElementById("filter-min-score");
  const minHitL10El = document.getElementById("filter-min-hit-l10");
  const minHitL5El = document.getElementById("filter-min-hit-l5");
  const refreshBtn = document.getElementById("props-search-refresh");

  const EMPTY_FILTER_MESSAGE =
    "No props match — try lowering min score or hit rate, or choose Any to include weaker lines.";

  function pageData() {
    return typeof getPageData === "function" ? getPageData() : null;
  }

  function setSelectValue(el, value) {
    if (!el || value == null || value === "") return;
    el.value = String(value);
  }

  function setCheckbox(el, checked) {
    if (el) el.checked = !!checked;
  }

  function hitSelectValue(rate) {
    if (rate == null) return "";
    const pct = Math.round(Number(rate) * 100);
    const opt = Array.from(minHitL10El?.options || []).find((o) => Number(o.value) === pct);
    return opt ? String(pct) : "";
  }

  function applyFilterDefaults(filters) {
    if (!filters) return;
    setSelectValue(bookEl, filters.bookmaker);
    setSelectValue(marketEl, filters.market_type);
    if (filters.min_odds != null) minOddsEl.value = filters.min_odds;
    setSelectValue(lineKindEl, filters.line_kind || "main");
    setSelectValue(sideEl, filters.side || "both");
    if (filters.line_value != null) lineValueEl.value = filters.line_value;
    setCheckbox(actionableEl, filters.actionable_only);
    setCheckbox(veryStrongEl, filters.very_strong_only);
    setCheckbox(alternatesEl, filters.include_alternates);
    setSelectValue(sortEl, filters.sort || "score");
    setSelectValue(riskEl, filters.risk);
    if (filters.min_score != null) setSelectValue(minScoreEl, filters.min_score);
    if (filters.min_hit_l10 != null) {
      setSelectValue(minHitL10El, hitSelectValue(filters.min_hit_l10) || "");
    }
    if (filters.min_hit_l5 != null) {
      setSelectValue(minHitL5El, hitSelectValue(filters.min_hit_l5) || "");
    }
  }

  function populateMarkets(markets) {
    if (!marketEl || !markets?.length) return;
    markets.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.key;
      opt.textContent = m.label;
      marketEl.appendChild(opt);
    });
  }

  function hasTightFilters(filters) {
    return Boolean(
      filters?.risk ||
        filters?.min_score ||
        filters?.min_hit_l5 != null ||
        filters?.min_hit_l10 != null ||
        filters?.actionable_only ||
        filters?.very_strong_only ||
        filters?.market_type ||
        (filters?.min_odds !== "" && filters?.min_odds != null) ||
        filters?.line_value
    );
  }

  function emptyMessageFor(data, filters) {
    if ((data?.total_matched || 0) > 0) return data.hint || "";
    if (hasTightFilters(filters)) return EMPTY_FILTER_MESSAGE;
    return data?.hint || "No props match these filters. Try a different book or refresh lines.";
  }

  function renderTracker(tracker) {
    const el = document.getElementById("props-tracker-stats");
    if (!el || !tracker) return;
    const buckets = tracker.line_strength || {};
    const fmtRate = (rate) => (rate != null ? `${(rate * 100).toFixed(0)}% hit` : "—");
    const cards = ["strong", "moderate", "weak"].map((key) => {
      const b = buckets[key] || {};
      const label = key.charAt(0).toUpperCase() + key.slice(1);
      return `<div class="props-tracker-stat"><strong>${fmtRate(b.hit_rate)}</strong><span>${label} · ${b.settled || 0} graded / ${b.offered || 0} offered</span></div>`;
    });
    const overall =
      tracker.overall_hit_rate != null
        ? `${(tracker.overall_hit_rate * 100).toFixed(0)}% overall (${tracker.props_settled || 0} graded)`
        : `${tracker.props_logged || 0} logged — grading starts after games finish`;
    el.innerHTML = `<p class="props-tracker-note">${overall}</p>${cards.join("")}`;
  }

  function renderFromPageData(data) {
    if (!data || data.kind !== "mlb_props") {
      renderPropExplorerList(resultsEl, [], {
        emptyMessage: "Page data missing — reload from the server.",
      });
      return;
    }

    populateMarkets(data.markets || []);
    applyFilterDefaults(data.filters || {});

    if (typeof initPropBookSelect === "function") {
      initPropBookSelect(bookEl, null, data.bookmakers || data.propsSearch?.bookmakers);
    }

    const search = data.propsSearch || {};
    const filters = data.filters || {};
    if (metaEl) {
      metaEl.textContent =
        typeof formatPropsSearchMeta === "function"
          ? formatPropsSearchMeta(search, filters)
          : `${search.total_matched || 0} props · ${search.bookmaker_label || "Consensus"}`;
    }
    renderPropExplorerList(resultsEl, search.props || [], {
      emptyMessage: emptyMessageFor(search, filters),
    });
    renderTracker(data.tracker);
  }

  function buildRefreshUrl() {
    const params = new URLSearchParams(new FormData(form));
    params.set("refresh", "true");
    return `/mlb/props?${params.toString()}`;
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
      targetDelta != null ? ` (${targetDelta >= 0 ? "+" : ""}${targetDelta} vs target)` : "";

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
    initLiveTicker("live-ticker", { sport: "all" });

    renderFromPageData(pageData());

    refreshBtn?.addEventListener("click", () => {
      if (!form) return;
      window.location.href = buildRefreshUrl();
    });

    document.getElementById("parlay-builder-form")?.addEventListener("submit", buildParlay);
  }

  init().catch(() => {
    renderPropExplorerList(resultsEl, [], { emptyMessage: "Could not initialize props page." });
  });
})();
