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
  const sportEl = document.getElementById("filter-sport");
  const positionEl = document.getElementById("filter-position");
  const dateHiddenEl = document.getElementById("filter-date");
  const dateInputEl = document.getElementById("props-date-input");

  const EMPTY_FILTER_MESSAGE =
    "Try removing one or more filters.";

  let _allProps = [];
  let _pageSport = "mlb";
  let _oppMode = "all";
  let _tableSort = "";

  function currentSport() {
    const data = pageData();
    return String(data?.sport || sportEl?.value || "mlb").toLowerCase();
  }

  function propsBasePath() {
    return currentSport() === "mlb" ? "/mlb/props" : "/props";
  }

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
    if (filters.position) setSelectValue(positionEl, filters.position);
    if (filters.min_hit_l10 != null) {
      setSelectValue(minHitL10El, hitSelectValue(filters.min_hit_l10) || "");
    }
    if (filters.min_hit_l5 != null) {
      setSelectValue(minHitL5El, hitSelectValue(filters.min_hit_l5) || "");
    }
  }

  function syncSportUi(sport) {
    const key = String(sport || "mlb").toLowerCase();
    if (sportEl) sportEl.value = key;
    document.querySelectorAll("[data-prop-sport]").forEach((el) => {
      el.classList.toggle("sport-pill-active", el.getAttribute("data-prop-sport") === key);
      if (el.tagName === "A") {
        const sportKey = el.getAttribute("data-prop-sport");
        const params = new URLSearchParams();
        params.set("sport", sportKey);
        if (dateInputEl?.value) params.set("date", dateInputEl.value);
        el.href = `/props?${params.toString()}`;
      }
    });
    document.querySelectorAll("[data-sport-filter]").forEach((el) => {
      el.hidden = el.getAttribute("data-sport-filter") !== key;
    });
    document.querySelectorAll("[data-sport-panel]").forEach((el) => {
      el.hidden = el.getAttribute("data-sport-panel") !== key;
    });
    const inlinePos = document.getElementById("pp-inline-position");
    if (inlinePos) inlinePos.hidden = key !== "nfl";
    const wrap = document.getElementById("props-quick-filters");
    if (wrap && key === "nfl" && !wrap.querySelector("[data-quick=\"qb\"]")) {
      wrap.insertAdjacentHTML(
        "beforeend",
        `<button type="button" class="sport-pill" data-quick="qb">QB</button>
         <button type="button" class="sport-pill" data-quick="rb">RB</button>
         <button type="button" class="sport-pill" data-quick="wr">WR/TE</button>`
      );
    }
  }

  function populateMarkets(markets) {
    if (!markets?.length) return;
    const targets = [marketEl, document.getElementById("pp-inline-market")].filter(Boolean);
    targets.forEach((el) => {
      const keepFirst = el.options[0];
      el.replaceChildren(keepFirst);
      markets.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.key;
        opt.textContent = m.label;
        el.appendChild(opt);
      });
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
    if (data?.empty_reason === "no_offers" || /haven't posted/i.test(data?.message || "")) {
      return data.message || "Sportsbooks have not posted eligible props for the selected sport/date yet.";
    }
    if (hasTightFilters(filters)) return data?.message || EMPTY_FILTER_MESSAGE;
    return data?.message || data?.hint || "Sportsbooks have not posted eligible props for the selected sport/date yet.";
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

  function fmtOdds(value) {
    if (typeof window.fmtAmericanOdds === "function") return window.fmtAmericanOdds(value);
    if (value == null || Number.isNaN(Number(value))) return "—";
    const n = Number(value);
    return n > 0 ? `+${n}` : String(n);
  }

  function playerInitials(name) {
    return String(name || "?")
      .split(/\s+/)
      .map((w) => w[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
  }

  function playerPhotoHtml(prop, size) {
    const initials = playerInitials(prop?.player);
    const src = prop?.photo_url;
    return `<span class="pp-avatar-wrap" style="width:${size}px;height:${size}px">
      <span class="pp-avatar" aria-hidden="true">${initials}</span>
      ${src ? `<img class="pp-avatar" src="${src}" alt="" width="${size}" height="${size}" loading="lazy" onerror="this.hidden=true" />` : ""}
    </span>`;
  }

  function propSide(p) {
    return p?.recommended_side || p?.side || "over";
  }

  function propWinProb(p) {
    const side = propSide(p);
    const raw =
      p?.model_probability ??
      p?.recommended_probability ??
      (side === "under" ? p?.model_probability_under : p?.model_probability_over);
    if (raw == null || Number.isNaN(Number(raw))) return null;
    return Number(raw);
  }

  function propEdgeFraction(p) {
    if (p?.edge != null && !Number.isNaN(Number(p.edge))) return Number(p.edge);
    if (p?.edge_pct != null && !Number.isNaN(Number(p.edge_pct))) {
      const n = Number(p.edge_pct);
      return Math.abs(n) > 1 ? n / 100 : n;
    }
    return null;
  }

  function fmtWinProb(p) {
    const v = propWinProb(p);
    return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
  }

  function fmtEdge(p) {
    const v = propEdgeFraction(p);
    if (v == null) return "—";
    const pct = v * 100;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
  }

  function fmtProjection(p) {
    if (p?.model_projection == null || Number.isNaN(Number(p.model_projection))) return "—";
    const n = Number(p.model_projection);
    return n % 1 === 0 ? String(n) : n.toFixed(1);
  }

  function propConfidence(p) {
    return p?.line_strength_label || p?.grade_label || p?.line_strength || p?.confidence || "—";
  }

  function bookLabel(p) {
    return p?.bookmaker_label || p?.bookmaker || "Consensus";
  }

  function playerMeta(p, sport) {
    if (sport === "nfl" && (p.position || p.team || p.opponent)) {
      const bits = [p.team, p.position].filter(Boolean).join(" · ");
      return p.opponent ? `${bits} vs ${p.opponent}` : bits;
    }
    return p.matchup || "";
  }

  function marketLabel(p) {
    return p.market_label || p.market_type || "Market";
  }

  function sideLine(p) {
    const side = propSide(p);
    return `${side === "under" ? "UNDER" : "OVER"} ${p.line ?? ""}`.trim();
  }

  function recommendedOdds(p) {
    const side = propSide(p);
    return p.recommended_odds ?? (side === "under" ? p.under_odds : p.over_odds);
  }

  function openAnalysis(prop, sport) {
    if (typeof window.openPropModal === "function" && prop) {
      window.openPropModal(prop, prop.sport || sport || "mlb");
    }
  }

  function wireSaveButton(btn, prop, sport) {
    if (!btn) return;
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      const out = await window.savePropToWatchlist?.(prop, sport);
      btn.disabled = false;
      if (out?.ok) btn.textContent = "Saved";
    });
  }

  function emptyStateHtml(title, message, showClear) {
    const sport = currentSport();
    return `<div class="empty-state-card branded-error-state">
      <h3>${title}</h3>
      <p>${message}</p>
      ${showClear ? `<a class="ntg-btn ntg-btn-primary" href="${sport === "nfl" ? "/props?sport=nfl" : "/props?sport=mlb"}">Clear filters</a>` : `<button type="button" class="empty-state-retry ntg-btn ntg-btn-primary" onclick="location.reload()">Retry</button>`}
    </div>`;
  }

  function renderFeatured(prop, sport) {
    const slot = document.getElementById("pp-featured-slot");
    const section = document.getElementById("pp-top-edge");
    if (!slot || !section) return;
    if (!prop) {
      section.hidden = true;
      slot.innerHTML = "";
      return;
    }
    section.hidden = false;
    const win = propWinProb(prop);
    const edge = propEdgeFraction(prop);
    const winPct = win == null ? 0 : Math.max(0, Math.min(100, win * 100));
    slot.innerHTML = `
      <article class="pp-card pp-featured" data-featured="1">
        <div class="pp-featured-player">
          ${playerPhotoHtml(prop, 88)}
          <div>
            <h3 class="pp-featured-name">${prop.player || "Player"}</h3>
            <p class="pp-featured-meta">${playerMeta(prop, sport)}</p>
          </div>
        </div>
        <div class="pp-featured-pick">
          <p class="pp-featured-market">${marketLabel(prop)}</p>
          <p class="pp-featured-line">${sideLine(prop)}</p>
        </div>
        <div class="pp-featured-stats">
          <div class="pp-stat">
            <span class="pp-stat-label">NTG Projection</span>
            <span class="pp-stat-value">${fmtProjection(prop)}</span>
          </div>
          <div class="pp-stat pp-stat--prob">
            <span class="pp-stat-label">Win Probability</span>
            <span class="pp-stat-value">${fmtWinProb(prop)}</span>
            <span class="pp-prob-bar" aria-hidden="true"><span style="--pp-prob:${winPct}%"></span></span>
          </div>
          <div class="pp-stat pp-stat--edge">
            <span class="pp-stat-label">Edge</span>
            <span class="pp-stat-value${edge != null && edge > 0 ? " is-pos" : ""}">${fmtEdge(prop)}</span>
          </div>
          <div class="pp-stat">
            <span class="pp-stat-label">Best Line</span>
            <span class="pp-stat-value">${fmtOdds(recommendedOdds(prop))}</span>
            <span class="pp-stat-sub">${bookLabel(prop)}</span>
          </div>
          <div class="pp-stat">
            <span class="pp-stat-label">Confidence</span>
            <span class="pp-stat-value">${propConfidence(prop)}</span>
          </div>
        </div>
        <div class="pp-featured-actions">
          <button type="button" class="ntg-btn ntg-btn-ghost" data-save-featured>Save</button>
          <button type="button" class="ntg-btn ntg-btn-primary" data-open-featured>View Full Analysis</button>
        </div>
      </article>`;
    slot.querySelector("[data-open-featured]")?.addEventListener("click", () => openAnalysis(prop, sport));
    slot.querySelector("[data-featured]")?.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openAnalysis(prop, sport);
    });
    wireSaveButton(slot.querySelector("[data-save-featured]"), prop, sport);
  }

  function opportunityPool(props, featured) {
    const rest = (props || []).filter((p) => p !== featured);
    if (_oppMode === "over") return rest.filter((p) => propSide(p) === "over");
    if (_oppMode === "under") return rest.filter((p) => propSide(p) === "under");
    if (_oppMode === "edges") {
      return rest.filter((p) => p.actionable || ["very_strong", "elite", "strong"].includes(String(p.line_strength || "")));
    }
    return rest;
  }

  function renderOpportunities(props, featured, sport) {
    const grid = document.getElementById("pp-opp-grid");
    const section = document.getElementById("pp-opportunities");
    if (!grid || !section) return;
    const rows = opportunityPool(props, featured).slice(0, 4);
    document.querySelectorAll("#pp-opp-filters [data-opp]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.opp === _oppMode);
    });
    if (!(props || []).length) {
      section.hidden = true;
      grid.innerHTML = "";
      return;
    }
    section.hidden = false;
    if (!rows.length) {
      grid.innerHTML = `<div class="empty-state-card"><p>No additional opportunities in this slice.</p></div>`;
      return;
    }
    grid.innerHTML = rows
      .map((p, i) => {
        const edge = propEdgeFraction(p);
        const win = propWinProb(p);
        const winPct = win == null ? 0 : Math.max(0, Math.min(100, win * 100));
        return `<article class="pp-card pp-opp-card" data-opp-idx="${i}" role="button" tabindex="0">
          <div class="pp-opp-top">
            ${playerPhotoHtml(p, 40)}
            <div class="pp-opp-identity">
              <p class="pp-opp-name">${p.player || ""}</p>
              <p class="pp-opp-meta">${playerMeta(p, sport)}</p>
            </div>
            <button type="button" class="ntg-btn ntg-btn-ghost pp-opp-save" data-save-opp="${i}">Save</button>
          </div>
          <p class="pp-opp-market">${marketLabel(p)}</p>
          <p class="pp-opp-line">${sideLine(p)}</p>
          <div class="pp-opp-metrics">
            <div>
              <div class="pp-opp-prob">${fmtWinProb(p)}</div>
              <span class="pp-prob-bar" aria-hidden="true"><span style="--pp-prob:${winPct}%"></span></span>
            </div>
            <span class="pp-opp-edge${edge != null && edge > 0 ? " is-pos" : ""}">${fmtEdge(p)}</span>
          </div>
          <p class="pp-opp-book">${bookLabel(p)} ${fmtOdds(recommendedOdds(p))}</p>
        </article>`;
      })
      .join("");
    grid.querySelectorAll("[data-opp-idx]").forEach((card) => {
      const idx = Number(card.dataset.oppIdx);
      const open = () => openAnalysis(rows[idx], sport);
      card.addEventListener("click", (e) => {
        if (e.target.closest("[data-save-opp]")) return;
        open();
      });
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
    });
    grid.querySelectorAll("[data-save-opp]").forEach((btn) => {
      wireSaveButton(btn, rows[Number(btn.dataset.saveOpp)], sport);
    });
  }

  function sortPropsForTable(props) {
    const rows = props.slice();
    if (_tableSort === "player") {
      rows.sort((a, b) => String(a.player || "").localeCompare(String(b.player || "")));
    } else if (_tableSort === "line") {
      rows.sort((a, b) => Number(a.line || 0) - Number(b.line || 0));
    } else if (_tableSort === "prob") {
      rows.sort((a, b) => (propWinProb(b) || 0) - (propWinProb(a) || 0));
    } else if (_tableSort === "edge") {
      rows.sort((a, b) => (propEdgeFraction(b) || 0) - (propEdgeFraction(a) || 0));
    }
    return rows;
  }

  function renderAllProps(props, options) {
    const sport = options.sport || "mlb";
    const countEl = document.getElementById("pp-count");
    if (countEl) countEl.textContent = `${props.length} Prop${props.length === 1 ? "" : "s"}`;
    if (!resultsEl) return;
    if (!props.length) {
      resultsEl.innerHTML = emptyStateHtml(
        options.emptyTitle || "NO PROPS MATCH YOUR FILTERS",
        options.emptyMessage || EMPTY_FILTER_MESSAGE,
        true
      );
      return;
    }
    const rows = sortPropsForTable(props);
    const tableRows = rows
      .map((p, i) => {
        const edge = propEdgeFraction(p);
        const win = propWinProb(p);
        const winPct = win == null ? 0 : Math.max(0, Math.min(100, win * 100));
        return `<tr data-prop-row="${i}" data-search="${`${p.player || ""} ${marketLabel(p)}`.toLowerCase()}">
          <td>
            <div class="pp-table-player">
              ${playerPhotoHtml(p, 32)}
              <div>
                <div class="pp-table-name">${p.player || ""}</div>
                <div class="pp-table-sub">${playerMeta(p, sport)}</div>
              </div>
            </div>
          </td>
          <td>${marketLabel(p)}</td>
          <td class="pp-table-line">${sideLine(p)}</td>
          <td>${fmtProjection(p)}</td>
          <td class="pp-table-prob">
            <strong>${fmtWinProb(p)}</strong>
            <span class="pp-prob-bar" aria-hidden="true"><span style="--pp-prob:${winPct}%"></span></span>
          </td>
          <td class="pp-table-edge${edge != null && edge > 0 ? " is-pos" : ""}">${fmtEdge(p)}</td>
          <td>${fmtOdds(recommendedOdds(p))}</td>
          <td class="pp-table-book">${bookLabel(p)}</td>
          <td class="pp-table-save"><button type="button" class="ntg-btn ntg-btn-ghost" data-save-row="${i}">Save</button></td>
        </tr>`;
      })
      .join("");
    const mobileCards = rows
      .map((p, i) => {
        const edge = propEdgeFraction(p);
        return `<button type="button" class="pp-card pp-mobile-card" data-mobile-row="${i}" data-search="${`${p.player || ""} ${marketLabel(p)}`.toLowerCase()}">
          <div>
            <p class="pp-mobile-name">${p.player || ""}</p>
            <p class="pp-mobile-market">${marketLabel(p)}</p>
            <p class="pp-mobile-market">${bookLabel(p)}</p>
          </div>
          <div>
            <p class="pp-mobile-line">${sideLine(p)}</p>
            <div class="pp-mobile-metrics">
              <span>${fmtWinProb(p)}</span>
              <span class="pp-table-edge${edge != null && edge > 0 ? " is-pos" : ""}">${fmtEdge(p)}</span>
            </div>
          </div>
        </button>`;
      })
      .join("");
    resultsEl.innerHTML = `
      <div class="pp-table-wrap">
        <table class="pp-table">
          <thead>
            <tr>
              <th data-sort="player">Player</th>
              <th>Market</th>
              <th data-sort="line">Line</th>
              <th>NTG Projection</th>
              <th data-sort="prob">Win Probability</th>
              <th data-sort="edge">Edge</th>
              <th>Best Line</th>
              <th>Book</th>
              <th></th>
            </tr>
          </thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
      <div class="pp-mobile-list">${mobileCards}</div>`;
    resultsEl.querySelectorAll(".pp-table th[data-sort]").forEach((th) => {
      th.classList.toggle("is-sorted", th.dataset.sort === _tableSort);
      th.addEventListener("click", () => {
        _tableSort = th.dataset.sort;
        renderAllProps(_allProps, options);
        applyPlayerSearch();
      });
    });
    resultsEl.querySelectorAll("[data-prop-row]").forEach((row) => {
      const idx = Number(row.dataset.propRow);
      row.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        openAnalysis(rows[idx], sport);
      });
    });
    resultsEl.querySelectorAll("[data-mobile-row]").forEach((card) => {
      const idx = Number(card.dataset.mobileRow);
      card.addEventListener("click", () => openAnalysis(rows[idx], sport));
    });
    resultsEl.querySelectorAll("[data-save-row]").forEach((btn) => {
      wireSaveButton(btn, rows[Number(btn.dataset.saveRow)], sport);
    });
  }

  function applyPlayerSearch() {
    const q = (document.getElementById("props-player-search")?.value || "").trim().toLowerCase();
    document.querySelectorAll("[data-search]").forEach((el) => {
      el.hidden = Boolean(q) && !el.getAttribute("data-search").includes(q);
    });
    const visible = Array.from(document.querySelectorAll("[data-prop-row]")).filter((el) => !el.hidden).length;
    const countEl = document.getElementById("pp-count");
    if (countEl && _allProps.length) {
      countEl.textContent = `${q ? visible : _allProps.length} Prop${(q ? visible : _allProps.length) === 1 ? "" : "s"}`;
    }
  }

  function renderFromPageData(data) {
    if (!data || (data.kind !== "mlb_props" && data.kind !== "player_props")) {
      if (typeof brandedErrorState === "function" && resultsEl) {
        brandedErrorState(resultsEl, {
          title: "PLAYER PROPS UNAVAILABLE",
          message: "We couldn't retrieve the latest prop data.",
          onRetry: () => location.reload(),
        });
      }
      return;
    }

    const sport = data.sport || "mlb";
    _pageSport = sport;
    syncSportUi(sport);
    populateMarkets(data.markets || []);
    applyFilterDefaults(data.filters || {});

    const books = data.bookmakers || data.propsSearch?.bookmakers;
    if (typeof initPropBookSelect === "function") {
      Promise.resolve(initPropBookSelect(bookEl, null, books)).then(() => {
        applyFilterDefaults(data.filters || {});
        const inlineBook = document.getElementById("pp-inline-book");
        if (inlineBook) {
          Promise.resolve(initPropBookSelect(inlineBook, null, books)).then(() => {
            if (bookEl?.value) inlineBook.value = bookEl.value;
            syncInlineFilters();
          });
        } else {
          syncInlineFilters();
        }
      });
    } else {
      syncInlineFilters();
    }

    const search = data.propsSearch || {};
    const filters = data.filters || {};
    const slateDate = data.date || search.date || "";
    if (dateInputEl && slateDate) dateInputEl.value = String(slateDate).slice(0, 10);
    if (dateHiddenEl && slateDate) dateHiddenEl.value = String(slateDate).slice(0, 10);

    const updatedEl = document.getElementById("props-updated-line");
    if (updatedEl) {
      const stamp = search.lines_fetched_at || data.status?.ran_at;
      updatedEl.textContent = stamp
        ? `Updated ${new Date(stamp).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
        : "";
    }
    if (metaEl) {
      metaEl.textContent =
        typeof formatPropsSearchMeta === "function"
          ? formatPropsSearchMeta(search, filters)
          : `${search.total_matched || 0} props · ${search.bookmaker_label || "Consensus"}`;
    }

    const props = search.props || [];
    _allProps = props;
    const noOffers = search.empty_reason === "no_offers" || /haven't posted/i.test(search.message || "");
    const emptyOpts = {
      emptyMessage: emptyMessageFor(search, filters),
      emptyTitle: noOffers ? "NO PROPS AVAILABLE" : "NO PROPS MATCH YOUR FILTERS",
      sport,
    };
    renderFeatured(props[0] || null, sport);
    renderOpportunities(props, props[0] || null, sport);
    renderAllProps(props, emptyOpts);
    if (sport === "mlb") renderTracker(data.tracker);
  }

  function propsFilterParams() {
    const params = new URLSearchParams();
    if (!form) return params;
    const sport = currentSport();
    for (const [key, value] of new FormData(form).entries()) {
      if (value == null || String(value).trim() === "") continue;
      if (sport !== "nfl" && (key === "position" || key === "min_edge")) continue;
      if (sport !== "mlb" && (key === "min_hit_l5" || key === "min_hit_l10")) continue;
      params.append(key, String(value));
    }
    params.set("sport", sport);
    if (dateInputEl?.value) params.set("date", dateInputEl.value);
    return params;
  }

  function buildRefreshUrl() {
    const params = propsFilterParams();
    params.set("refresh", "true");
    const base = params.get("sport") === "mlb" ? "/mlb/props" : "/props";
    return `${base}?${params.toString()}`;
  }

  function applyPropsFilters() {
    const params = propsFilterParams();
    const sport = params.get("sport") || "mlb";
    const base = sport === "mlb" ? "/mlb/props" : "/props";
    const qs = params.toString();
    window.location.href = qs ? `${base}?${qs}` : base;
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
      if (meta) meta.textContent = "Could not build parlay. Try again.";
    }
  }

  function countActiveFilters() {
    if (!form) return 0;
    const data = new FormData(form);
    let n = 0;
    for (const [key, value] of data.entries()) {
      if (!value) continue;
      if (key === "sport" || key === "date") continue;
      if (key === "sort" && value === "score") continue;
      if (key === "side" && value === "both") continue;
      if (key === "line_kind" && value === "main") continue;
      if (key === "min_score" && value === "65") continue;
      if (key === "min_hit_l10" && value === "55") continue;
      n += 1;
    }
    return n;
  }

  function setFilterDrawerOpen(open) {
    const drawer = document.getElementById("props-filter-drawer");
    const scrim = document.getElementById("props-filter-scrim");
    const openBtn = document.getElementById("props-open-filters");
    if (drawer) {
      drawer.classList.toggle("is-open", open);
      drawer.setAttribute("aria-hidden", open ? "false" : "true");
    }
    if (scrim) {
      scrim.classList.toggle("is-open", open);
    }
    document.body.classList.toggle("ntg-filters-open", open);
    openBtn?.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function refreshFilterCount() {
    const openBtn = document.getElementById("props-open-filters");
    if (!openBtn) return;
    const n = countActiveFilters();
    openBtn.textContent = n ? `Filters (${n})` : "Filters";
  }

  function syncQuickFilters() {
    const wrap = document.getElementById("props-quick-filters");
    if (!wrap) return;
    const mode = veryStrongEl?.checked
      ? "strong"
      : actionableEl?.checked
        ? "edges"
        : positionEl?.value === "QB"
          ? "qb"
          : positionEl?.value === "RB"
            ? "rb"
            : positionEl?.value === "WR" || positionEl?.value === "TE"
              ? "wr"
              : "all";
    wrap.querySelectorAll("[data-quick]").forEach((btn) => {
      btn.classList.toggle("sport-pill-active", btn.dataset.quick === mode);
    });
  }

  function syncInlineFilters() {
    const map = [
      ["pp-inline-position", positionEl],
      ["pp-inline-market", marketEl],
      ["pp-inline-side", sideEl],
      ["pp-inline-book", bookEl],
      ["pp-inline-sort", sortEl],
    ];
    map.forEach(([id, source]) => {
      const el = document.getElementById(id);
      if (el && source && source.value != null) el.value = source.value;
    });
  }

  function bindInlineFilters() {
    const pairs = [
      ["pp-inline-position", positionEl],
      ["pp-inline-market", marketEl],
      ["pp-inline-side", sideEl],
      ["pp-inline-book", bookEl],
      ["pp-inline-sort", sortEl],
    ];
    pairs.forEach(([id, target]) => {
      document.getElementById(id)?.addEventListener("change", (e) => {
        if (target) target.value = e.target.value;
        applyPropsFilters();
      });
    });
    dateInputEl?.addEventListener("change", () => {
      if (dateHiddenEl) dateHiddenEl.value = dateInputEl.value;
      applyPropsFilters();
    });
  }

  function initPropsFilterDrawer() {
    if (document.documentElement.dataset.propsFiltersWired === "1") {
      refreshFilterCount();
      syncQuickFilters();
      return;
    }
    document.documentElement.dataset.propsFiltersWired = "1";

    document.addEventListener("click", (e) => {
      const el = e.target instanceof Element ? e.target : e.target.parentElement;
      if (!el) return;
      if (el.closest("#props-open-filters")) {
        e.preventDefault();
        setFilterDrawerOpen(true);
        return;
      }
      if (el.closest("#props-close-filters") || el.closest("#props-filter-scrim")) {
        e.preventDefault();
        setFilterDrawerOpen(false);
        return;
      }
      if (el.closest("#props-clear-filters") || el.closest("#props-drawer-clear")) {
        e.preventDefault();
        window.location.href = currentSport() === "nfl" ? "/props?sport=nfl" : "/mlb/props";
      }
    });
    document.getElementById("props-quick-filters")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-quick]");
      if (!btn || !form) return;
      const mode = btn.dataset.quick;
      if (actionableEl) actionableEl.checked = mode === "edges";
      if (veryStrongEl) veryStrongEl.checked = mode === "strong";
      if (positionEl) {
        if (mode === "qb") positionEl.value = "QB";
        else if (mode === "rb") positionEl.value = "RB";
        else if (mode === "wr") positionEl.value = "WR";
        else if (mode === "all" || mode === "edges" || mode === "strong") positionEl.value = "";
      }
      applyPropsFilters();
    });
    document.getElementById("pp-opp-filters")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-opp]");
      if (!btn) return;
      _oppMode = btn.dataset.opp || "all";
      renderOpportunities(_allProps, _allProps[0] || null, _pageSport);
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setFilterDrawerOpen(false);
    });
    form?.addEventListener("submit", (e) => {
      e.preventDefault();
      applyPropsFilters();
    });
    refreshFilterCount();
    syncQuickFilters();
  }

  function initPropsPlayerSearch() {
    const input = document.getElementById("props-player-search");
    if (!input) return;
    input.addEventListener("input", applyPlayerSearch);
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
    initPropsFilterDrawer();
    initPropsPlayerSearch();
    bindInlineFilters();

    refreshBtn?.addEventListener("click", () => {
      if (!form) return;
      window.location.href = buildRefreshUrl();
    });

    document.getElementById("parlay-builder-form")?.addEventListener("submit", buildParlay);
  }

  initPropsFilterDrawer();
  init().catch(() => {
    if (metaEl) metaEl.textContent = "Unable to load props.";
    const target = resultsEl || document.getElementById("pp-featured-slot");
    if (typeof brandedErrorState === "function" && target) {
      brandedErrorState(target, {
        title: "PLAYER PROPS UNAVAILABLE",
        message: "We couldn't retrieve the latest prop data.",
        onRetry: () => location.reload(),
      });
      return;
    }
    if (resultsEl) {
      resultsEl.innerHTML = emptyStateHtml(
        "PLAYER PROPS UNAVAILABLE",
        "We couldn't retrieve the latest prop data.",
        false
      );
    }
  });
})();
