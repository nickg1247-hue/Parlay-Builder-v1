/** NBA game detail — live scores + ML / spread / O/U model and markets. */

(function () {
  const loading = document.getElementById("game-loading");
  const errEl = document.getElementById("game-error");
  const content = document.getElementById("game-content");
  const header = document.getElementById("matchup-header");
  const boardEl = document.getElementById("game-matchup-board");
  const refreshLink = document.getElementById("insights-refresh");
  const warningsEl = document.getElementById("insights-warnings");
  const disclaimerEl = document.getElementById("game-disclaimer");

  const parts = window.location.pathname.split("/").filter(Boolean);
  const gameIdx = parts.indexOf("game");
  const gameId = gameIdx >= 0 ? parts[gameIdx + 1] : null;

  if (!gameId) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Missing game id in URL";
    return;
  }

  const dateParam = qs("date");
  const useCache = qs("use_cache") === "true";

  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });

  const scoresUrl = dateParam
    ? `/api/scores/today?sport=nba&date=${encodeURIComponent(dateParam)}`
    : "/api/scores/today?sport=nba";

  let scorePollerStarted = false;
  let lastManualInsightsRefreshAt = 0;
  const INSIGHTS_REFRESH_COOLDOWN_MS = 300000;

  function insightsUrl(refresh) {
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    if (useCache) params.set("use_cache", "true");
    if (refresh) params.set("refresh", "true");
    const q = params.toString();
    return `/api/games/nba/${encodeURIComponent(gameId)}/insights${q ? `?${q}` : ""}`;
  }

  function fmtOdds(am) {
    if (am == null) return "—";
    return am > 0 ? `+${am}` : String(am);
  }

  function fmtPoint(pt) {
    if (pt == null) return "—";
    return pt > 0 ? `+${pt}` : String(pt);
  }

  function statCard(label, value, tier) {
    const pickCls =
      tier === "low" || tier === "medium" || tier === "high"
        ? `market-pick-${tier}`
        : "";
    const cls = pickCls ? `market-stat-card ${pickCls}` : "market-stat-card";
    return `<div class="${cls}"><span class="stat-label">${label}</span><span class="stat-value">${value}</span></div>`;
  }

  function teamColumnHtml(side, game, cards, highlights, data) {
    const isAway = side === "away";
    const team = isAway ? game.away_team : game.home_team;
    const teamId = isAway ? game.away_team_id : game.home_team_id;
    const col = cards[side] || {};
    const spread = col.spread || {};
    const total = cards.total || {};
    const overHi = isAway && highlights.total_side === "over";
    const underHi = !isAway && highlights.total_side === "under";
    const mlTier =
      highlights.moneyline_side === side ? highlights.moneyline_tier : null;
    const spreadTier =
      highlights.spread_side === side ? highlights.spread_tier : null;
    const ouTier = overHi || underHi ? highlights.total_tier : null;

    const mlValue = fmtOdds(col.moneyline_american);
    const spreadValue =
      spread.point != null
        ? `${fmtPoint(spread.point)} (${fmtOdds(spread.american)})`
        : "—";

    const line = total.line != null ? total.line : null;
    const ouValue =
      data.board_totals_enabled && isAway
        ? line != null || total.over_american != null
          ? `Over ${line != null ? line : "—"} (${fmtOdds(total.over_american)})`
          : "—"
        : data.board_totals_enabled && !isAway
          ? line != null || total.under_american != null
            ? `Under ${line != null ? line : "—"} (${fmtOdds(total.under_american)})`
            : "—"
          : "";

    const spreadCard = data.board_spread_enabled
      ? statCard("Spread", spreadValue, spreadTier)
      : "";
    const ouCard =
      data.board_totals_enabled && ouValue
        ? statCard("Over/Under", ouValue, ouTier)
        : data.board_totals_enabled
          ? statCard("Over/Under", "—", ouTier)
          : "";

    return `
      <div class="team-market-col ${side}">
        <img class="team-logo team-market-logo" src="${game[isAway ? "away_logo_url" : "home_logo_url"] || teamLogoUrl(teamId)}" alt="" width="48" height="48" loading="lazy">
        <p class="team-market-name">${team}</p>
        ${statCard("Moneyline", mlValue, mlTier)}
        ${ouCard}
        ${spreadCard}
      </div>
    `;
  }

  function marginLabel(game, modelMargin) {
    if (modelMargin == null) return "";
    const mm = Number(modelMargin);
    if (!Number.isFinite(mm)) return "";
    const favored = mm >= 0 ? game.home_team : game.away_team;
    const by = Math.abs(mm).toFixed(1);
    const side = mm >= 0 ? "H" : "A";
    return `<p class="model-margin">Pred. margin: ${side} by ${by} (${favored})</p>`;
  }

  function modelCenterHtml(model, cards, data) {
    if (!model || !model.pick) {
      return `<div class="model-center-col"><p class="model-empty">No model data for this game.</p></div>`;
    }

    const game = data.game || {};
    const edge = model.edge != null ? `${(model.edge * 100).toFixed(1)}%` : "—";
    const winPct = model.win_pct != null ? `${model.win_pct}%` : "—";
    const pts =
      model.model_total_pts != null ? model.model_total_pts : "—";
    const ouLine = cards.total?.line != null ? cards.total.line : "—";
    const evBadge = model.plus_ev_single
      ? `<p class="model-ev-badge">+EV single (≥8% edge)</p>`
      : "";

    const totalsBlock =
      data.board_totals_enabled && model.totals_pick
        ? `<p><strong>O/U pick:</strong> ${model.totals_pick} ${ouLine} · edge ${
            model.total_edge != null
              ? (model.total_edge * 100).toFixed(1) + "%"
              : "—"
          }</p>`
        : data.board_totals_enabled
          ? `<p class="model-runs">Est. total pts: <strong>${pts}</strong></p>`
          : "";

    const marginBlock =
      data.board_spread_enabled && model.model_margin != null
        ? marginLabel(game, model.model_margin)
        : "";

    const spreadPickBlock =
      data.board_spread_enabled && model.spread_pick
        ? `<p><strong>Spread pick:</strong> ${model.spread_pick} · edge ${
            model.spread_edge != null
              ? (model.spread_edge * 100).toFixed(1) + "%"
              : "—"
          }</p>`
        : "";

    return `
      <div class="model-center-col">
        <p class="model-center-label">Model</p>
        <p class="model-pick">${model.pick}</p>
        <p class="model-win">${winPct} win</p>
        ${totalsBlock}
        ${marginBlock}
        ${spreadPickBlock}
        <p class="model-edge">Edge ${edge} · ${model.confidence || "—"}</p>
        ${evBadge}
      </div>
    `;
  }

  function renderMatchupBoard(data) {
    const game = data.game;
    const cards = data.market_cards || {};
    const highlights = data.highlights || {};
    boardEl.innerHTML = [
      teamColumnHtml("away", game, cards, highlights, data),
      modelCenterHtml(data.model, cards, data),
      teamColumnHtml("home", game, cards, highlights, data),
    ].join("");
  }

  function renderWarnings(warnings) {
    if (!warningsEl) return;
    warningsEl.innerHTML = "";
    (warnings || []).forEach((w) => {
      const div = document.createElement("div");
      div.className = "warning-item";
      div.textContent = w;
      warningsEl.appendChild(div);
    });
  }

  function featureLabel(key) {
    return key.replace(/_/g, " ").replace(/\bhome\b/g, "Home").replace(/\baway\b/g, "Away");
  }

  function formatFeatureValue(key, val) {
    if (val == null || Number.isNaN(val)) return "—";
    if (key.includes("win_pct") || key.includes("prob")) return `${(Number(val) * 100).toFixed(1)}%`;
    if (key.includes("b2b")) return Number(val) === 1 ? "Yes" : "No";
    if (Number.isInteger(val)) return String(val);
    return Number(val).toFixed(2);
  }

  function renderPredictionDetail(data) {
    const section = document.getElementById("prediction-detail");
    const pred = data.prediction;
    if (!section || !pred) return;

    section.classList.remove("hidden");
    const label = document.getElementById("prediction-model-label");
    if (label) {
      label.textContent = `(${pred.feature_count || 22} features · ${pred.model_version || "model"})`;
    }
    const note = document.getElementById("prediction-data-note");
    if (note) {
      note.textContent =
        pred.note ||
        "Predictions use ingested game history plus today's matchup from ESPN.";
    }
    const driversEl = document.getElementById("prediction-drivers");
    if (driversEl) {
      driversEl.innerHTML = (pred.drivers || [])
        .map((d) => `<li>${d}</li>`)
        .join("");
    }
    const table = document.getElementById("prediction-features");
    if (table && pred.factors && pred.factors.length) {
      const header = `<tr><th>Factor</th><th>Weight</th><th>Home edge</th><th>Contribution</th></tr>`;
      const rows = pred.factors
        .map(
          (f) =>
            `<tr><th>${f.label}</th><td>${f.weight_pct}%</td><td>${(f.home_edge * 100).toFixed(1)}%</td><td>${(f.weighted_contribution * 100).toFixed(2)}%</td></tr>`
        )
        .join("");
      table.innerHTML = header + rows;
    } else if (table && pred.features) {
      const rows = Object.entries(pred.features)
        .map(
          ([k, v]) =>
            `<tr><th>${featureLabel(k)}</th><td colspan="3">${formatFeatureValue(k, v)}</td></tr>`
        )
        .join("");
      table.innerHTML = rows;
    }
    if (pred.ml_prob_home != null && note) {
      note.textContent += ` ML baseline (trained): ${(pred.ml_prob_home * 100).toFixed(1)}% home.`;
    }
  }

  function renderInsights(data) {
    renderMatchupHeader(header, data.game);
    renderMatchupBoard(data);
    renderPredictionDetail(data);
    renderWarnings(data.warnings);

    if (disclaimerEl && data.disclaimer) {
      disclaimerEl.textContent = data.disclaimer;
    }

    const hasLiveLines = data.market_cards?.source === "the_odds_api";
    if (refreshLink) {
      if (hasLiveLines && !dateParam && !useCache) {
        refreshLink.classList.remove("hidden");
        refreshLink.onclick = (e) => {
          e.preventDefault();
          const now = Date.now();
          if (now - lastManualInsightsRefreshAt < INSIGHTS_REFRESH_COOLDOWN_MS) {
            return;
          }
          lastManualInsightsRefreshAt = now;
          loadInsights(true);
        };
      } else {
        refreshLink.classList.add("hidden");
      }
    }
  }

  async function refreshLiveScore() {
    try {
      const data = await fetchJSON(scoresUrl);
      const live = (data.games || []).find(
        (g) => String(g.game_id) === String(gameId)
      );
      if (live) renderMatchupHeader(header, live);
    } catch (_) {
      /* keep last good header */
    }
  }

  async function loadInsights(refresh) {
    const data = await fetchJSON(insightsUrl(refresh));
    loading.classList.add("hidden");
    content.classList.remove("hidden");
    renderInsights(data);

    if (!dateParam && !scorePollerStarted) {
      scorePollerStarted = true;
      setInterval(refreshLiveScore, 60000);
    }
  }

  loadTeamColors()
    .then(() => loadInsights(false))
    .catch((e) => {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = e.message || "Game not found";
    });
})();
