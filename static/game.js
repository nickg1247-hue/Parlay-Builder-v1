/** Game detail: live scores + per-team market board + center model. */



(function () {

  const loading = document.getElementById("game-loading");

  const errEl = document.getElementById("game-error");

  const content = document.getElementById("game-content");

  const header = document.getElementById("matchup-header");

  const boardEl = document.getElementById("game-matchup-board");

  const refreshLink = document.getElementById("insights-refresh");

  const warningsEl = document.getElementById("insights-warnings");

  const linesUpdatedEl = document.getElementById("lines-updated");



  const gameId = gameIdFromPath();

  if (!gameId) {

    loading.classList.add("hidden");

    errEl.classList.remove("hidden");

    errEl.textContent = "Missing game id in URL";

    return;

  }



  const dateParam = qs("date");

  const useCache = qs("use_cache") === "true";

  const scoresUrl = dateParam

    ? `/api/scores/today?sport=mlb&date=${encodeURIComponent(dateParam)}`

    : "/api/scores/today?sport=mlb";



  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });
  initHeadlineTicker("headline-ticker");

  let scorePollerStarted = false;

  let lastInsightsData = null;

  let lastOddsFetchedAt = null;

  let lastBoardGeneratedAt = null;

  let lastManualInsightsRefreshAt = 0;

  const INSIGHTS_REFRESH_COOLDOWN_MS = 300000;



  function insightsUrl(refresh) {

    const params = new URLSearchParams();

    if (dateParam) params.set("date", dateParam);

    if (useCache) params.set("use_cache", "true");

    if (refresh) params.set("refresh", "true");

    const q = params.toString();

    return `/api/games/mlb/${encodeURIComponent(gameId)}/insights${q ? `?${q}` : ""}`;

  }



  function fmtOdds(am) {

    if (am == null) return "—";

    return am > 0 ? `+${am}` : String(am);

  }



  function fmtPoint(pt) {

    if (pt == null) return "—";

    return pt > 0 ? `+${pt}` : String(pt);

  }



  function normTeam(name) {

    return (name || "").trim().toLowerCase();

  }



  function statCard(label, value, tier) {

    const pickCls =

      tier === "low" || tier === "medium" || tier === "high"

        ? `market-pick-${tier}`

        : "";

    const cls = pickCls ? `market-stat-card ${pickCls}` : "market-stat-card";

    return `<div class="${cls}"><span class="stat-label">${label}</span><span class="stat-value">${value}</span></div>`;

  }



  function teamColumnHtml(side, game, cards, highlights) {

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

    const spreadValue = spread.point != null
      ? `${fmtPoint(spread.point)} (${fmtOdds(spread.american)})`
      : "—";

    const line = total.line != null ? total.line : null;
    const ouValue = isAway
      ? line != null || total.over_american != null
        ? `Over ${line != null ? line : "—"} (${fmtOdds(total.over_american)})`
        : "—"
      : line != null || total.under_american != null
        ? `Under ${line != null ? line : "—"} (${fmtOdds(total.under_american)})`
        : "—";
    return `
      <div class="team-market-col ${side}">
        <img class="team-logo team-market-logo" src="${game[isAway ? "away_logo_url" : "home_logo_url"] || teamLogoUrl(teamId)}" alt="" width="48" height="48" loading="lazy">
        <p class="team-market-name">${team}</p>
        ${statCard("Moneyline", mlValue, mlTier)}
        ${statCard("Over/Under", ouValue, ouTier)}
        ${statCard("Spread", spreadValue, spreadTier)}
      </div>
    `;

  }



  function modelCenterHtml(model, cards) {

    if (!model || !model.pick) {

      return `<div class="model-center-col"><p class="model-empty">No model data for this game.</p></div>`;

    }

    const edge = model.edge != null ? `${(model.edge * 100).toFixed(1)}%` : "—";

    const runs = model.expected_runs != null ? model.expected_runs : "—";

    const winPct = model.win_pct != null ? `${model.win_pct}%` : "—";

    const ouLine = cards.total?.line != null ? cards.total.line : "—";

    const totalsLine = model.totals_pick

      ? `<p><strong>O/U pick:</strong> ${model.totals_pick} ${ouLine} · edge ${model.total_edge != null ? (model.total_edge * 100).toFixed(1) + "%" : "—"}</p>`

      : "";

    const evLine =
      model.ev_pick && model.ev_pick !== model.pick
        ? `<p class="model-ev-line"><strong>+EV value:</strong> ${model.ev_pick}${model.ev_edge != null ? ` · +${(model.ev_edge * 100).toFixed(1)}%` : ""}</p>`
        : "";

    return `

      <div class="model-center-col">

        <p class="model-center-label">Model</p>

        <p class="model-pick">${model.pick}</p>

        <p class="model-win">${winPct} win</p>

        ${evLine}

        <p class="model-runs">Est. total runs: <strong>${runs}</strong></p>

        <p class="model-edge">Edge ${edge} · ${model.confidence || "—"}</p>

        ${totalsLine}

      </div>

    `;

  }



  function renderMatchupBoard(data) {

    const game = data.game;

    const cards = data.market_cards || {};

    const highlights = data.highlights || {};

    boardEl.innerHTML = [

      teamColumnHtml("away", game, cards, highlights),

      modelCenterHtml(data.model, cards),

      teamColumnHtml("home", game, cards, highlights),

    ].join("");

  }



  function marketCardsFromRepoRow(row, source) {

    const src = source === "the_odds_api_live" || source === "the_odds_api_historical"

      ? "the_odds_api"

      : source;

    return {

      source: src,

      away: {

        moneyline_american: row.away_ml,

        spread: { point: row.away_spread_point, american: row.away_spread_american },

      },

      home: {

        moneyline_american: row.home_ml,

        spread: { point: row.home_spread_point, american: row.home_spread_american },

      },

      total: {

        line: row.ou_line,

        over_american: row.over_odds,

        under_american: row.under_odds,

      },

    };

  }



  function findRepoGame(snap, game) {

    const home = normTeam(game.home_team);

    const away = normTeam(game.away_team);

    return (snap.games || []).find(

      (g) => normTeam(g.home_team) === home && normTeam(g.away_team) === away

    );

  }



  function formatFetchedAt(iso) {

    if (!iso) return null;

    try {

      const d = new Date(iso);

      return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

    } catch (_) {

      return null;

    }

  }



  function renderParlays(parlays) {

    const el = document.getElementById("parlays-body");

    if (!parlays || !parlays.length) {

      el.innerHTML = "<p>No ranked parlays include this game at the current edge threshold.</p>";

      return;

    }

    el.innerHTML = parlays

      .map((p) => {

        const legs = (p.legs || [])

          .map((l) => `${l.team} (${l.side}) ${fmtOdds(l.american_odds)}`)

          .join(" · ");

        return `<div class="parlay-card"><strong>${p.num_legs}-leg · EV ${p.ev_pct || (p.ev * 100).toFixed(1) + "%"}</strong><p>${legs}</p></div>`;

      })

      .join("");

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

    lastInsightsData = data;

    renderMatchupHeader(header, data.game);

    renderMatchupBoard(data);

    renderParlays(data.parlays);

    renderWarnings(data.warnings);

    const hasLiveLines = data.market_cards?.source === "the_odds_api";

    if (refreshLink) {

      if (hasLiveLines) {

        refreshLink.classList.remove("hidden");

        refreshLink.onclick = (e) => {

          e.preventDefault();

          const now = Date.now();

          if (now - lastManualInsightsRefreshAt < INSIGHTS_REFRESH_COOLDOWN_MS) {

            const waitSec = Math.ceil(

              (INSIGHTS_REFRESH_COOLDOWN_MS - (now - lastManualInsightsRefreshAt)) / 1000

            );

            if (warningsEl) {

              const msg = `Lines were refreshed recently — wait ${waitSec}s before pulling again.`;

              if (!warningsEl.textContent.includes(msg)) {

                warningsEl.textContent = warningsEl.textContent

                  ? `${warningsEl.textContent} ${msg}`

                  : msg;

              }

            }

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

      const live = (data.games || []).find((g) => String(g.game_id) === String(gameId));

      if (live) renderMatchupHeader(header, live);

    } catch (_) {

      /* keep last good header */

    }

  }



  async function pollTodayOdds() {

    if (!lastInsightsData || dateParam || useCache) return;

    try {

      const snap = await fetchJSON("/api/odds/today");

      const oddsChanged =

        snap.fetched_at && snap.fetched_at !== lastOddsFetchedAt;

      const boardChanged =

        snap.board_generated_at &&

        snap.board_generated_at !== lastBoardGeneratedAt;

      if (oddsChanged || boardChanged) {

        lastOddsFetchedAt = snap.fetched_at || lastOddsFetchedAt;

        lastBoardGeneratedAt = snap.board_generated_at || lastBoardGeneratedAt;

        await loadInsights(false);

        if (linesUpdatedEl && snap.fetched_at) {

          const t = formatFetchedAt(snap.fetched_at);

          linesUpdatedEl.textContent = t ? `Lines updated ${t}` : "";

          linesUpdatedEl.classList.remove("hidden");

        }

        return;

      }

    } catch (_) {

      /* keep last board */

    }

  }



  async function loadInsights(refresh) {

    const data = await fetchJSON(insightsUrl(refresh));

    loading.classList.add("hidden");

    content.classList.remove("hidden");

    renderInsights(data);

    if (!dateParam && !useCache) {

      try {

        const snap = await fetchJSON("/api/odds/today");

        lastOddsFetchedAt = snap.fetched_at || null;

        lastBoardGeneratedAt = snap.board_generated_at || null;

      } catch (_) {

        /* optional seed for poll diff */

      }

    }

    if (!dateParam && !scorePollerStarted) {

      scorePollerStarted = true;

      setInterval(refreshLiveScore, 60000);

      if (!useCache) {

        setInterval(pollTodayOdds, 60000);

        pollTodayOdds();

      }

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


