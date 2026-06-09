/** NBA game detail — live scores + moneyline model/markets. */

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
    const mlTier =
      highlights.moneyline_side === side ? highlights.moneyline_tier : null;
    const mlValue = fmtOdds(col.moneyline_american);
    const spread = col.spread || {};
    const spreadTier =
      highlights.spread_side === side ? highlights.spread_tier : null;
    const spreadValue =
      spread.point != null
        ? `${fmtPoint(spread.point)} (${fmtOdds(spread.american)})`
        : "—";
    const spreadCard = data.board_spread_enabled
      ? statCard("Spread", spreadValue, spreadTier)
      : "";

    return `
      <div class="team-market-col ${side}">
        <img class="team-logo team-market-logo" src="${game[isAway ? "away_logo_url" : "home_logo_url"] || teamLogoUrl(teamId)}" alt="" width="48" height="48" loading="lazy">
        <p class="team-market-name">${team}</p>
        ${statCard("Moneyline", mlValue, mlTier)}
        ${spreadCard}
      </div>
    `;
  }

  function modelCenterHtml(model) {
    if (!model || !model.pick) {
      return `<div class="model-center-col"><p class="model-empty">No model data for this game.</p></div>`;
    }

    const edge = model.edge != null ? `${(model.edge * 100).toFixed(1)}%` : "—";
    const winPct = model.win_pct != null ? `${model.win_pct}%` : "—";
    const evBadge = model.plus_ev_single
      ? `<p class="model-ev-badge">+EV single (≥8% edge)</p>`
      : "";

    return `
      <div class="model-center-col">
        <p class="model-center-label">Model</p>
        <p class="model-pick">${model.pick}</p>
        <p class="model-win">${winPct} win</p>
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
      modelCenterHtml(data.model),
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

  function renderInsights(data) {
    renderMatchupHeader(header, data.game);
    renderMatchupBoard(data);
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
