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

  function formatApiDetail(detail) {
    if (!detail) return "";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => d.msg || d.message || JSON.stringify(d)).join("; ");
    }
    return String(detail);
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

  function canonicalMarketType(marketType) {
    const mt = String(marketType || "").trim();
    if (!mt) return mt;
    return mt.endsWith("_alternate") ? mt.slice(0, -"_alternate".length) : mt;
  }

  function normalizePropForModal(prop) {
    if (!prop || !prop.player) return null;
    const market_type = canonicalMarketType(prop.market_type);
    if (!market_type) return null;
    const line = prop.line;
    if (line == null || Number.isNaN(Number(line))) return null;
    const side = prop.recommended_side || prop.side || "over";
    return {
      ...prop,
      market_type,
      recommended_side: side,
      recommended_odds: prop.recommended_odds ?? prop.american_odds,
      line: Number(line),
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

  function ensureOverlay() {
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

  function projectionVsLineHtml(prop) {
    const line = prop.line;
    const proj = prop.model_projection;
    if (line == null || proj == null || Number.isNaN(Number(line)) || Number.isNaN(Number(proj))) return "";
    const a = Number(line);
    const b = Number(proj);
    const min = Math.min(a, b);
    const max = Math.max(a, b);
    const span = max - min || 1;
    const linePct = ((a - min) / span) * 80 + 10;
    const projPct = ((b - min) / span) * 80 + 10;
    return `
      <section class="why-pick-card ntg-card" aria-label="Projection vs line">
        <h3 class="why-pick-card__title">Model vs line</h3>
        <p class="why-pick-card__insight">Sportsbook ${a} · Model projection ${b}</p>
        <div class="ntg-proj-track" aria-hidden="true">
          <span class="ntg-proj-dot ntg-proj-dot--line" style="left:${linePct}%"></span>
          <span class="ntg-proj-dot ntg-proj-dot--model" style="left:${projPct}%"></span>
        </div>
      </section>`;
  }

  function analysisHeroHtml(prop) {
    const side = prop.recommended_side || prop.side || "over";
    const modelPct =
      prop.model_probability ??
      prop.recommended_probability ??
      (side === "over" ? prop.model_probability_over : prop.model_probability_under);
    const mktPct = prop.market_probability ?? (side === "over" ? prop.market_probability_over : prop.market_probability_under);
    const edge = prop.edge ?? prop.edge_pct;
    const edgeLabel =
      edge == null
        ? "—"
        : `${Number(edge) <= 1 && Number(edge) >= -1 ? (Number(edge) * 100).toFixed(1) : Number(edge).toFixed(1)}%`;
    const conf = prop.line_strength_label || prop.grade_label || prop.line_strength || "";
    const book = prop.bookmaker_label || prop.bookmaker || "";
    return `
      <div class="pp-analysis-stats">
        <div class="pp-stat"><span class="pp-stat-label">NTG Projection</span><span class="pp-stat-value">${prop.model_projection ?? "—"}</span></div>
        <div class="pp-stat pp-stat--prob"><span class="pp-stat-label">Win Probability</span><span class="pp-stat-value">${fmtPct(modelPct)}</span></div>
        <div class="pp-stat"><span class="pp-stat-label">Market Probability</span><span class="pp-stat-value">${fmtPct(mktPct)}</span></div>
        <div class="pp-stat pp-stat--edge"><span class="pp-stat-label">Edge</span><span class="pp-stat-value${edge != null && Number(edge) > 0 ? " is-pos" : ""}">${edgeLabel === "—" ? "—" : (Number(edge) > 0 ? "+" : "") + edgeLabel.replace("+", "")}</span></div>
        ${book ? `<div class="pp-stat"><span class="pp-stat-label">Sportsbook</span><span class="pp-stat-value">${book}</span></div>` : ""}
        ${conf ? `<div class="pp-stat"><span class="pp-stat-label">Confidence</span><span class="pp-stat-value">${conf}</span></div>` : ""}
      </div>`;
  }

  function whyChipsHtml(prop, data) {
    const chips = [];
    const usage = prop.analysis?.usage || {};
    if (usage.l3_avg != null && usage.season_avg != null) {
      const l3 = Number(usage.l3_avg);
      const season = Number(usage.season_avg) || 1;
      const delta = (l3 - season) / Math.abs(season);
      chips.push({
        label: "Usage",
        value: delta > 0.12 ? "Elevated" : delta < -0.12 ? "Down" : "Stable",
      });
    }
    if (usage.role_shift != null) {
      const shift = Number(usage.role_shift);
      chips.push({
        label: "Role",
        value: Math.abs(shift) < 0.1 ? "Stable" : shift > 0 ? "Increasing" : "Decreasing",
      });
    }
    const edge = prop.edge ?? prop.edge_pct;
    if (edge != null) {
      chips.push({ label: "Market Edge", value: Number(edge) > 0 ? "Positive" : "Negative" });
    }
    if (data?.depth?.opposing_pitcher) {
      chips.push({ label: "Matchup", value: data.depth.opposing_pitcher });
    }
    const risk = prop.risk_flag || (Array.isArray(prop.analysis?.risks) ? prop.analysis.risks[0] : null);
    if (risk) chips.push({ label: "Risk", value: risk });
    if (!chips.length) return "";
    return `<div class="pp-why-chips">${chips
      .map((c) => `<div class="pp-why-chip"><span>${c.label}</span><strong>${c.value}</strong></div>`)
      .join("")}</div>`;
  }

  function whyNtgLikesHtml(prop, data) {
    const reasons = [];
    const side = prop.recommended_side || prop.side || "over";
    const modelPct =
      prop.model_probability ??
      prop.recommended_probability ??
      (side === "over" ? prop.model_probability_over : prop.model_probability_under);
    const mktPct = prop.market_probability ?? (side === "over" ? prop.market_probability_over : prop.market_probability_under);
    const edge = prop.edge ?? prop.edge_pct;
    if (modelPct != null && mktPct != null) {
      reasons.push({
        label: "Market",
        tone: "up",
        text: `Model ${fmtPct(modelPct)} vs market ${fmtPct(mktPct)}${
          edge != null ? ` · edge ${Number(edge) <= 1 && Number(edge) >= -1 ? (Number(edge) * 100).toFixed(1) : Number(edge).toFixed(1)}%` : ""
        }.`,
      });
    }
    const usage = prop.analysis?.usage || {};
    if (usage.l3_avg != null || usage.season_avg != null) {
      reasons.push({
        label: "Usage",
        tone: "up",
        text: `L3 ${usage.l3_avg ?? "—"} · Season ${usage.season_avg ?? "—"}${
          usage.sample_games != null ? ` · ${usage.sample_games} games` : ""
        }.`,
      });
    }
    const env = prop.analysis?.environment || {};
    if (env.team_spread != null || env.game_total != null) {
      reasons.push({
        label: "Game environment",
        tone: "neutral",
        text: `Spread ${env.team_spread ?? "—"} · Total ${env.game_total ?? "—"}${
          env.team_implied_total != null ? ` · Implied ${env.team_implied_total}` : ""
        }.`,
      });
    }
    const rates = data?.hit_rates || {};
    if (rates.l10 != null) {
      reasons.push({
        label: "Recent form",
        tone: "up",
        text: `This ${side} hit in ${fmtPct(rates.l10)} of the last 10 logged games.`,
      });
    }
    if (data?.depth?.opposing_pitcher) {
      reasons.push({
        label: "Matchup",
        tone: "neutral",
        text: `Opposing starter ${data.depth.opposing_pitcher}${
          data.depth.opposing_pitcher_era != null ? ` (${data.depth.opposing_pitcher_era} ERA)` : ""
        }.`,
      });
    }
    (prop.factors || []).slice(0, 3).forEach((f) => {
      reasons.push({ label: "Factor", tone: "neutral", text: f });
    });
    const insight = prop.line_insight || data?.line_insight;
    if (insight) reasons.push({ label: "Line", tone: "neutral", text: insight });
    const risks = prop.analysis?.risks || prop.risk_flags || [];
    if (prop.risk_flag) risks.push(prop.risk_flag);
    if (risks.length) {
      reasons.push({ label: "Risk", tone: "risk", text: risks.filter(Boolean).join(" · ") });
    }
    if (!reasons.length && !whyChipsHtml(prop, data)) return "";
    return `
      <section class="why-pick-card ntg-card" aria-label="Why NTG likes this">
        <h3 class="why-pick-card__title">Why NTG likes this</h3>
        ${whyChipsHtml(prop, data)}
        ${
          reasons.length
            ? `<ul class="why-pick-card__factors">${reasons
                .map((r) => `<li><strong>${r.label}</strong> ${r.text}</li>`)
                .join("")}</ul>`
            : ""
        }
      </section>`;
  }

  function renderWhyPickCard(prop, data) {
    return `${whyNtgLikesHtml(prop, data)}${projectionVsLineHtml(prop)}`;
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
    if (!data || data.status === "error") {
      const raw = String(data?.message || "");
      const safe = /http|traceback|undefined|failed/i.test(raw) ? "We couldn't load this analysis." : raw;
      return `<div class="empty-state-card"><p>${safe || "Could not load player stats."}</p></div>`;
    }
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
      ${analysisHeroHtml(prop)}
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
        <button type="button" class="ntg-btn ntg-btn-ghost" id="prop-modal-save">☆ Save</button>
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
        `/api/players/${encodeURIComponent(sport)}/lookup?${qs.toString()}`,
        { credentials: "same-origin" }
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

  function renderNflModalContent(prop) {
    const side = prop.recommended_side || "over";
    const sideLabel = side === "under" ? "Under" : "Over";
    const analysis = prop.analysis || {};
    const usage = analysis.usage || {};
    const env = analysis.environment || {};
    const risks = analysis.risks || prop.risk_flags || [];
    const sides = (prop.sides || [])
      .map(
        (s) =>
          `<span class="hero-chip">${s.side === "under" ? "Under" : "Over"} ${fmtOdds(s.odds)} · model ${
            s.model_probability != null ? fmtPct(s.model_probability) : "—"
          } · edge ${
            s.edge != null ? `${s.edge >= 0 ? "+" : ""}${Math.round(s.edge * 1000) / 10}%` : "—"
          }</span>`
      )
      .join("");
    const why = renderWhyPickCard(prop, {});
    const gameHref = prop.game_id ? `/nfl/game/${encodeURIComponent(prop.game_id)}` : "/nfl";
    return `
      <header class="prop-modal-head">
        <div>
          <h2 id="player-prop-modal-title">${prop.player}</h2>
          <p class="prop-modal-market">${prop.team || ""} ${prop.position ? "• " + prop.position : ""} vs ${prop.opponent || ""}</p>
          <p class="prop-modal-market">${prop.market_label || prop.market_type}: ${sideLabel} ${prop.line}</p>
          <p class="prop-modal-odds">${fmtOdds(prop.recommended_odds)}</p>
        </div>
      </header>
      ${analysisHeroHtml(prop)}
      ${why}
      ${projectionVsLineHtml(prop)}
      <section class="why-pick-card ntg-card">
        <h3 class="why-pick-card__title">Projection</h3>
        <p>Model ${prop.model_projection ?? "—"} · P(${sideLabel}) ${
          prop.model_probability != null ? fmtPct(prop.model_probability) : "—"
        } · Market ${prop.market_probability != null ? fmtPct(prop.market_probability) : "—"} · Edge ${
          prop.edge != null ? `${prop.edge >= 0 ? "+" : ""}${(prop.edge * 100).toFixed(1)}%` : "—"
        }</p>
      </section>
      <section class="why-pick-card ntg-card">
        <h3 class="why-pick-card__title">Usage</h3>
        <p>L3 ${usage.l3_avg ?? "—"} · Season ${usage.season_avg ?? "—"} · Sample ${usage.sample_games ?? 0} games${
          usage.role_shift != null ? ` · Role shift ${(usage.role_shift * 100).toFixed(0)}%` : ""
        }</p>
      </section>
      <section class="why-pick-card ntg-card">
        <h3 class="why-pick-card__title">Game environment</h3>
        <p>Spread ${env.team_spread ?? "—"} · Total ${env.game_total ?? "—"} · Implied ${env.team_implied_total ?? "—"}</p>
      </section>
      ${
        risks.length
          ? `<section class="why-pick-card ntg-card"><h3 class="why-pick-card__title">Risk</h3><p>${risks.join(" · ")}</p>${
              analysis.injury ? `<p>${analysis.injury}</p>` : ""
            }</section>`
          : ""
      }
      <div class="prop-modal-rates">${sides}</div>
      <div class="prop-modal-actions">
        <a class="ntg-btn ntg-btn-ghost" href="${gameHref}">Open game</a>
        <a class="ntg-btn ntg-btn-ghost" href="/players/nfl/${encodeURIComponent(prop.player_id || prop.player || "")}">Player</a>
        <button type="button" class="ntg-btn ntg-btn-ghost" id="prop-modal-save">Save</button>
        <button type="button" class="ntg-btn ntg-btn-primary" id="prop-modal-add-slip">Add to slip</button>
      </div>
    `;
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
    const resolvedSport = String(prop.sport || sport || "mlb").toLowerCase();
    if (resolvedSport === "nfl") {
      body.innerHTML = renderNflModalContent(prop);
      body.querySelector("#prop-modal-add-slip")?.addEventListener("click", () => {
        const leg =
          global.propSlipLegFromProp?.(prop, { requireActionable: false }) ||
          global.propSlipLegFromProp?.(prop);
        if (leg && global.addPropToSlip?.(leg)) {
          body.querySelector("#prop-modal-add-slip").textContent = "Added ✓";
        }
      });
      body.querySelector("#prop-modal-save")?.addEventListener("click", async () => {
        const btn = body.querySelector("#prop-modal-save");
        const out = await global.savePropToWatchlist?.(prop, "nfl");
        if (out?.ok && btn) btn.textContent = "Saved";
      });
      overlay.querySelector(".player-prop-modal__close")?.focus();
      return;
    }
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
        `/api/players/${encodeURIComponent(sport)}/${encodeURIComponent(playerId)}/prop-context?${qs}`,
        { credentials: "same-origin" }
      );
      let detail = "";
      if (!res.ok) {
        try {
          const errBody = await res.json();
          detail = formatApiDetail(errBody.detail || errBody.message);
        } catch (_) {
          detail = await res.text().catch(() => "");
        }
        throw new Error(detail && !/^HTTP\s*\d+/i.test(detail) ? detail : "We couldn't load this analysis.");
      }
      const data = await res.json();
      try {
        body.innerHTML = renderModalContent(prop, data);
      } catch (renderErr) {
        throw new Error(renderErr?.message || "Could not render player stats.");
      }
      body.querySelector("#prop-modal-add-slip")?.addEventListener("click", () => {
        const leg =
          global.propSlipLegFromProp?.(prop, { requireActionable: false }) ||
          global.propSlipLegFromProp?.(prop);
        if (leg && global.addPropToSlip?.(leg)) {
          body.querySelector("#prop-modal-add-slip").textContent = "Added ✓";
        }
      });
      body.querySelector("#prop-modal-save")?.addEventListener("click", async () => {
        const btn = body.querySelector("#prop-modal-save");
        const out = await global.savePropToWatchlist?.(prop, sport);
        if (out?.ok && btn) btn.textContent = "★ Saved";
      });
    } catch (err) {
      const msg = err?.message || "Could not load prop context.";
      body.innerHTML = `<div class="empty-state-card">${global.emptyStateIcon?.("no-bets") || ""}<p>${msg}</p><button type="button" class="empty-state-retry" data-retry="1">Try again</button></div>`;
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
      if (!res.ok) throw new Error("We couldn't load this player profile.");
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
