/**
 * NTG Sports — player prop detail modal (MLB v1).
 */
(function (global) {
  let _overlay = null;
  let _lastFocus = null;

  function fmtPct(v) {
    if (v == null || Number.isNaN(v)) return "—";
    return `${Math.round(v * 100)}%`;
  }

  function fmtOdds(odds) {
    if (odds == null) return "—";
    return odds > 0 ? `+${odds}` : `${odds}`;
  }

  function fmtCell(v) {
    if (v == null || v === "") return "—";
    return v;
  }

  function renderGameLogTable(gameLog, options = {}) {
    const log = gameLog || {};
    const columns = log.columns || [];
    const games = log.games || [];
    const highlight = options.highlightColumn || log.highlight_column || null;
    const showHitCol = options.showHitColumn === true;

    if (!columns.length) {
      return `<p class="prop-modal-empty-log">No game log data for this season.</p>`;
    }

    const head = columns
      .map((c) => {
        const cls = c.key === highlight ? "prop-log-stat-col" : "";
        return `<th class="${cls}">${c.label}</th>`;
      })
      .join("");

    const rows = games
      .map((g) => {
        let rowClass = "";
        if (showHitCol && g.prop_hit === true) rowClass = "prop-log-hit";
        else if (showHitCol && g.prop_hit === false) rowClass = "prop-log-miss";
        const statCells = columns
          .map((c) => {
            const cls = c.key === highlight ? "prop-log-stat-col" : "";
            return `<td class="${cls}">${fmtCell(g.stats?.[c.key])}</td>`;
          })
          .join("");
        const hitCell = showHitCol
          ? `<td class="prop-log-hit-col">${g.prop_hit === true ? "✓" : g.prop_hit === false ? "✗" : "—"}</td>`
          : "";
        return `<tr class="${rowClass}">
          <td>${fmtCell(g.date)}</td>
          <td>${fmtCell(g.opponent)}</td>
          ${statCells}
          ${hitCell}
        </tr>`;
      })
      .join("");

    const hitHeader = showHitCol ? `<th>Hit</th>` : "";

    return `
      <div class="prop-modal-table-wrap prop-modal-table-wrap--wide">
        <table class="prop-modal-table prop-modal-table--stats">
          <thead>
            <tr>
              <th>Date</th>
              <th>Opp</th>
              ${head}
              ${hitHeader}
            </tr>
          </thead>
          <tbody>${rows || `<tr><td colspan="${columns.length + 2 + (showHitCol ? 1 : 0)}">No games</td></tr>`}</tbody>
        </table>
      </div>`;
  }

  function renderSeasonTotals(totals) {
    const entries = Object.entries(totals || {});
    if (!entries.length) return "";
    return `
      <div class="prop-modal-season-totals">
        ${entries
          .map(
            ([label, val]) =>
              `<span class="prop-modal-total-chip"><strong>${label}</strong> ${fmtCell(val)}</span>`
          )
          .join("")}
      </div>`;
  }

  function normalizePropForModal(prop) {
    if (!prop || !prop.player || !prop.market_type) return null;
    const side = prop.recommended_side || prop.side || "over";
    return {
      ...prop,
      recommended_side: side,
      recommended_odds: prop.recommended_odds ?? prop.american_odds,
      line: prop.line,
    };
  }

  function propFromParlayRow(leg, row) {
    const base = row && typeof row === "object" ? row : {};
    const slip = leg && typeof leg === "object" ? leg : {};
    return normalizePropForModal({
      ...base,
      ...slip,
      player: slip.player || base.player,
      market_type: slip.market_type || base.market_type,
      market_label: slip.market_label || base.market_label,
      line: slip.line ?? base.line,
      game_id: slip.game_id || base.game_id,
      matchup: slip.matchup || base.matchup,
      recommended_side: slip.side || base.recommended_side,
      recommended_odds: slip.american_odds ?? base.recommended_odds,
      player_id: base.player_id ?? slip.player_id,
      photo_url: base.photo_url,
      factors: base.factors,
      line_insight: base.line_insight,
      rank_score: base.rank_score ?? base.score ?? slip.score,
      actionable: base.actionable ?? true,
    });
  }

  function wireParlayLegModals(container, propsList) {
    if (!container || !propsList?.length) return;
    container.querySelectorAll("[data-open-parlay-prop]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = Number(el.dataset.openParlayProp);
        const prop = propsList[idx];
        if (prop) openPropModal(prop, "mlb");
      });
    });
  }
    if (_overlay) return _overlay;
    _overlay = document.createElement("div");
    _overlay.id = "player-prop-modal";
    _overlay.className = "player-prop-modal hidden";
    _overlay.setAttribute("role", "dialog");
    _overlay.setAttribute("aria-modal", "true");
    _overlay.setAttribute("aria-labelledby", "player-prop-modal-title");
    _overlay.innerHTML = `
      <div class="player-prop-modal__backdrop" data-close-modal="1"></div>
      <div class="player-prop-modal__panel ntg-card">
        <button type="button" class="player-prop-modal__close" data-close-modal="1" aria-label="Close">×</button>
        <div class="player-prop-modal__body"></div>
      </div>`;
    document.body.appendChild(_overlay);

    _overlay.addEventListener("click", (e) => {
      if (e.target.closest("[data-close-modal]")) closePropModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && _overlay && !_overlay.classList.contains("hidden")) {
        closePropModal();
      }
    });
    return _overlay;
  }

  function skeletonHtml() {
    return `
      <div class="player-prop-modal__skeleton">
        <div class="skeleton-row" style="height:2rem;width:60%"></div>
        <div class="skeleton-row" style="height:1rem;width:40%;margin-top:0.75rem"></div>
        <div class="skeleton-row" style="height:8rem;margin-top:1rem"></div>
      </div>`;
  }

  function renderWhyPickCard(prop, data) {
    const factors = (prop.factors || []).slice(0, 6);
    const insight = prop.line_insight || data?.line_insight || "";
    if (!factors.length && !insight) return "";
    const list = factors.length
      ? `<ul class="why-pick-card__factors">${factors.map((f) => `<li>${f}</li>`).join("")}</ul>`
      : "";
    return `
      <section class="why-pick-card ntg-card" aria-label="Why this pick">
        <h3 class="why-pick-card__title">Why this pick</h3>
        ${insight ? `<p class="why-pick-card__insight">${insight}</p>` : ""}
        ${list}
      </section>`;
  }

  function renderDepthBadges(depth) {
    const d = depth || {};
    const badges = d.badges || [];
    const split = d.splits?.platoon;
    let html = "";
    if (badges.length) {
      html += `<div class="prop-depth-badges">${badges
        .map((b) => `<span class="hero-chip hero-chip-muted prop-depth-badge prop-depth-badge--${b.type}">${b.label}</span>`)
        .join("")}</div>`;
    }
    if (split) {
      html += `<p class="prop-depth-split"><strong>${split.label}</strong> AVG ${split.avg || "—"} · OPS ${split.ops || "—"} · ${split.homeRuns || 0} HR</p>`;
    }
    if (d.opposing_pitcher) {
      html += `<p class="prop-depth-split">Opposing SP: <strong>${d.opposing_pitcher}</strong>${d.opposing_pitcher_era != null ? ` (${d.opposing_pitcher_era} ERA)` : ""}</p>`;
    }
    return html ? `<section class="prop-modal-depth">${html}</section>` : "";
  }

  function renderModalContent(prop, data) {
    const side = prop.recommended_side || prop.side || "over";
    const sideLabel = side === "under" ? "Under" : "Over";
    const edge =
      prop.rank_score != null
        ? `<span class="prop-modal-edge">Model ${prop.rank_score}</span>`
        : prop.recommended_hit_rate != null
          ? `<span class="prop-modal-edge">L10 ${fmtPct(prop.recommended_hit_rate)}</span>`
          : "";
    const whyCard = renderWhyPickCard(prop, data);
    const depthBlock = renderDepthBadges(data.depth);
    const rates = data.hit_rates || {};
    const photo = data.photo_url
      ? `<img class="prop-modal-photo" src="${data.photo_url}" alt="" width="64" height="64">`
      : "";

    const gameLogTable = renderGameLogTable(data.game_log, {
      highlightColumn: data.prop_stat_key,
      showHitColumn: true,
    });

    return `
      <header class="prop-modal-head">
        ${photo}
        <div>
          <h2 id="player-prop-modal-title">${data.player_name || prop.player}</h2>
          <p class="prop-modal-market">${data.market_label || prop.market_label}: ${sideLabel} ${prop.line}</p>
          <p class="prop-modal-odds">${fmtOdds(prop.recommended_odds)} ${edge}</p>
        </div>
      </header>
      ${whyCard}
      ${depthBlock}
      <div class="prop-modal-rates">
        <span class="hero-chip">L5 ${fmtPct(rates.l5)}</span>
        <span class="hero-chip">L10 ${fmtPct(rates.l10)}</span>
        <span class="hero-chip">Season ${fmtPct(rates.season)}</span>
        <span class="hero-chip hero-chip-muted">${data.sample_games || 0} games</span>
      </div>
      <section class="prop-modal-log">
        <h3>${data.season || ""} season game log</h3>
        <p class="prop-modal-log-note">Highlighted column is the prop stat · ✓/✗ vs ${sideLabel} ${prop.line}</p>
        ${gameLogTable}
      </section>
      <div class="prop-modal-actions">
        ${
          typeof global.addPropToSlip === "function" && typeof global.propSlipLegFromProp === "function"
            ? `<button type="button" class="home-props-fill-btn" id="prop-modal-add-slip">Add to prop slip</button>`
            : ""
        }
      </div>`;
  }

  function renderProfileContent(data, playerName) {
    const photo = data.photo_url
      ? `<img class="prop-modal-photo" src="${data.photo_url}" alt="" width="64" height="64">`
      : "";
    const gameLogTable = renderGameLogTable(data.game_log);
    const totals = renderSeasonTotals(data.season_totals);
    const props = (data.available_props || [])
      .map((p) => {
        const side = p.recommended_side === "under" ? "U" : "O";
        return `<li><button type="button" class="prop-modal-prop-link">${p.market_label}: ${side}${p.line} (${fmtOdds(p.recommended_odds)})</button></li>`;
      })
      .join("");

    return `
      <header class="prop-modal-head">
        ${photo}
        <div>
          <h2 id="player-prop-modal-title">${data.name || playerName}</h2>
          <p class="prop-modal-market">${data.position || ""} · ${data.season || ""} season</p>
        </div>
      </header>
      ${totals}
      <section class="prop-modal-log">
        <h3>${data.season || ""} season game log</h3>
        ${gameLogTable}
      </section>
      <section class="prop-modal-log">
        <h3>Today's props</h3>
        <ul class="prop-modal-props-list" id="prop-modal-props-list">${props || "<li>No props posted today</li>"}</ul>
      </section>`;
  }

  function wireProfilePropLinks(container, props) {
    const buttons = container?.querySelectorAll("#prop-modal-props-list .prop-modal-prop-link");
    if (!buttons?.length) return;
    buttons.forEach((btn, i) => {
      btn.addEventListener("click", () => {
        const p = props[i];
        if (p) openPropModal(p, "mlb");
      });
    });
  }

  async function resolvePlayerId(sport, prop) {
    if (prop.player_id != null && String(prop.player_id).trim() !== "") {
      return String(prop.player_id);
    }
    const name = String(prop.player || "").trim();
    if (!name) return null;
    try {
      const qs = new URLSearchParams({ name });
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/lookup?${qs.toString()}`
      );
      if (res.ok) {
        const data = await res.json();
        return data.player_id != null ? String(data.player_id) : null;
      }
      const legacy = await fetch(
        `/api/players/${encodeURIComponent(sport)}/by-name/${encodeURIComponent(name)}/id`
      );
      if (!legacy.ok) return null;
      const data = await legacy.json();
      return data.player_id != null ? String(data.player_id) : null;
    } catch {
      return null;
    }
  }

  async function openPropModal(prop, sport = "mlb") {
    const normalized = normalizePropForModal(prop);
    if (!normalized) return;
    prop = normalized;
    const overlay = ensureOverlay();
    _lastFocus = document.activeElement;
    overlay.classList.remove("hidden");
    document.body.classList.add("player-prop-modal-open");
    const body = overlay.querySelector(".player-prop-modal__body");
    body.innerHTML = skeletonHtml();
    overlay.querySelector(".player-prop-modal__close")?.focus();

    const playerId = await resolvePlayerId(sport, prop);
    if (!playerId) {
      body.innerHTML = `<div class="empty-state-card">${global.emptyStateIcon?.("no-bets") || ""}<p>Could not match player to stats.</p></div>`;
      return;
    }

    const side = prop.recommended_side || prop.side || "over";
    const qs = new URLSearchParams({
      market_type: prop.market_type,
      line: String(prop.line),
      side,
    });
    if (prop.game_id) qs.set("game_id", String(prop.game_id));
    try {
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/${encodeURIComponent(playerId)}/prop-context?${qs}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      body.innerHTML = renderModalContent(prop, data);
      body.querySelector("#prop-modal-add-slip")?.addEventListener("click", () => {
        const leg =
          global.propSlipLegFromProp?.(prop, { requireActionable: false }) ||
          global.propSlipLegFromProp?.(prop);
        if (leg && global.addPropToSlip?.(leg)) {
          body.querySelector("#prop-modal-add-slip").textContent = "Added ✓";
        }
      });
    } catch {
      body.innerHTML = `<div class="empty-state-card">${global.emptyStateIcon?.("no-bets") || ""}<p>Could not load prop context.</p><button type="button" class="empty-state-retry" data-retry="1">Try again</button></div>`;
      body.querySelector("[data-retry]")?.addEventListener("click", () => openPropModal(prop, sport));
    }
  }

  function closePropModal() {
    if (!_overlay) return;
    _overlay.classList.add("hidden");
    document.body.classList.remove("player-prop-modal-open");
    _lastFocus?.focus?.();
  }

  async function openPlayerProfileModal(sport, playerId, playerName) {
    const overlay = ensureOverlay();
    _lastFocus = document.activeElement;
    overlay.classList.remove("hidden");
    document.body.classList.add("player-prop-modal-open");
    const body = overlay.querySelector(".player-prop-modal__body");
    body.innerHTML = skeletonHtml();

    try {
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/${encodeURIComponent(playerId)}/profile`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.status === "unsupported") {
        body.innerHTML = `<div class="empty-state-card"><p>${data.message || "Coming soon for this sport."}</p></div>`;
        return;
      }
      body.innerHTML = renderProfileContent(data, playerName);
      wireProfilePropLinks(body, data.available_props || []);
    } catch {
      body.innerHTML = `<div class="empty-state-card"><p>Could not load player profile.</p></div>`;
    }
  }

  global.openPropModal = openPropModal;
  global.closePropModal = closePropModal;
  global.openPlayerProfileModal = openPlayerProfileModal;
  global.renderPlayerGameLogTable = renderGameLogTable;
  global.normalizePropForModal = normalizePropForModal;
  global.propFromParlayRow = propFromParlayRow;
  global.wireParlayLegModals = wireParlayLegModals;
})(window);
