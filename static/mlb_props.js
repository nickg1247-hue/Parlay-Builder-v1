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
  const applyBtn = document.getElementById("props-apply-filters");

  const EMPTY_FILTER_MESSAGE =
    "No props match — try lowering min score or hit rate.";

  let searchSeq = 0;
  let searchInFlight = false;

  function hitPctFromSelect(el) {
    if (!el?.value) return null;
    return Number(el.value) / 100;
  }

  function readFilters(refresh) {
    return {
      bookmaker: bookEl?.value || "draftkings",
      market_type: marketEl?.value || "",
      min_odds: minOddsEl?.value ?? "",
      line_kind: lineKindEl?.value || "main",
      side: sideEl?.value || "both",
      line_value: lineValueEl?.value ?? "",
      actionable_only: !!actionableEl?.checked,
      very_strong_only: !!veryStrongEl?.checked,
      include_alternates: !!alternatesEl?.checked || lineKindEl?.value === "alternate",
      sort: sortEl?.value || "score",
      risk: riskEl?.value || "",
      min_score: minScoreEl?.value || "",
      min_hit_l5: hitPctFromSelect(minHitL5El),
      min_hit_l10: hitPctFromSelect(minHitL10El),
      limit: 200,
      scan: !!refresh,
      refresh: !!refresh,
    };
  }

  function hasTightFilters(filters) {
    return Boolean(
      filters.risk ||
        filters.min_score ||
        filters.min_hit_l5 != null ||
        filters.min_hit_l10 != null ||
        filters.actionable_only ||
        filters.very_strong_only ||
        filters.market_type ||
        (filters.min_odds !== "" && filters.min_odds != null) ||
        filters.line_value
    );
  }

  function emptyMessageFor(data, filters) {
    if ((data?.total_matched || 0) > 0) return data.hint || "";
    if (hasTightFilters(filters)) return EMPTY_FILTER_MESSAGE;
    return data?.hint || "No props match these filters. Try a different book or refresh lines.";
  }

  function setSearchBusy(busy) {
    searchInFlight = busy;
    if (applyBtn) {
      applyBtn.disabled = busy;
      applyBtn.textContent = busy ? "Applying…" : "Apply filters";
    }
    if (refreshBtn) refreshBtn.disabled = busy;
  }

  async function runSearch(refresh = false) {
    if (typeof buildPropSearchQuery !== "function") {
      if (metaEl) metaEl.textContent = "Props search unavailable — reload the page.";
      return;
    }

    const seq = ++searchSeq;
    setSearchBusy(true);
    if (metaEl) {
      metaEl.textContent = refresh
        ? "Refreshing props from sportsbooks…"
        : "Applying filters…";
    }

    const filters = readFilters(refresh);
    const params = buildPropSearchQuery(filters);

    try {
      const res = await fetch(`/api/props/search?${params.toString()}`, {
        cache: "no-store",
      });
      if (seq !== searchSeq) return;

      if (res.status === 401) {
        if (metaEl) metaEl.textContent = "Sign in required";
        if (window.renderPropsAuthGate) {
          window.renderPropsAuthGate(resultsEl, "/mlb/props");
        } else {
          renderPropExplorerList(resultsEl, [], { emptyMessage: "Sign in to view player props." });
        }
        return;
      }
      if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);

      const data = await res.json();
      if (seq !== searchSeq) return;

      if (metaEl) {
        metaEl.textContent =
          typeof formatPropsSearchMeta === "function"
            ? formatPropsSearchMeta(data, filters)
            : `${data.total_matched || 0} props · ${data.bookmaker_label || "Consensus"}`;
      }
      renderPropExplorerList(resultsEl, data.props || [], {
        emptyMessage: emptyMessageFor(data, filters),
      });
    } catch (e) {
      if (seq !== searchSeq) return;
      if (metaEl) metaEl.textContent = "Could not load props.";
      renderPropExplorerList(resultsEl, [], { emptyMessage: e.message || "Search failed." });
    } finally {
      if (seq === searchSeq) setSearchBusy(false);
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

  function applyFilters(e) {
    e?.preventDefault();
    if (!searchInFlight) runSearch(false);
  }

  function wireFilterControls() {
    form?.addEventListener("submit", applyFilters);
    applyBtn?.addEventListener("click", applyFilters);
    refreshBtn?.addEventListener("click", () => runSearch(true));
  }

  async function init() {
    if (typeof window.ensureAppReady === "function") {
      await window.ensureAppReady();
    } else {
      await loadPublicFeatures();
      initPropSlipUi();
    }
    initSiteChrome();
    await initPropBookSelect(bookEl, () => {
      if (!searchInFlight) runSearch(false);
    });

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

    wireFilterControls();
    document.getElementById("parlay-builder-form")?.addEventListener("submit", buildParlay);

    loadTracker();
    await runSearch(false);
  }

  init().catch(() => {
    renderPropExplorerList(resultsEl, [], { emptyMessage: "Could not initialize props search." });
  });
})();
