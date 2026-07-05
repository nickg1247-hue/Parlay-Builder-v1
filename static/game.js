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

  const formVsModelEl = document.getElementById("form-vs-model-panel");



  const gameId = gameIdFromPath();

  if (!gameId) {

    loading.classList.add("hidden");

    errEl.classList.remove("hidden");

    errEl.textContent = "Missing game id in URL";

    return;

  }



  const dateParam = qs("date");

  const useCache = qs("use_cache") === "true";

  const ntgGamePageData = typeof getPageData === "function" ? getPageData() : null;

  let embeddedGameProps =
    ntgGamePageData?.kind === "mlb_game" ? ntgGamePageData.gameProps : null;

  const useEmbeddedPage = ntgGamePageData?.kind === "mlb_game";

  function buildGamePageUrl(extra = {}) {
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    if (useCache) params.set("use_cache", "true");
    const book = extra.bookmaker || ntgGamePageData?.bookmaker;
    if (book) params.set("bookmaker", book);
    if (extra.refresh) params.set("refresh", "true");
    const q = params.toString();
    return `/mlb/game/${encodeURIComponent(gameId)}${q ? `?${q}` : ""}`;
  }

  function reloadGamePage(extra = {}) {
    window.location.href = buildGamePageUrl(extra);
  }

  const scoresUrl = dateParam

    ? `/api/scores/today?sport=mlb&date=${encodeURIComponent(dateParam)}`

    : "/api/scores/today?sport=mlb";



  initSiteChrome();
  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });
  initHeadlineTicker("headline-ticker");

  let scorePollerStarted = false;

  let lastInsightsData = null;

  let lastOddsFetchedAt = null;

  let lastBoardGeneratedAt = null;

  let lastManualInsightsRefreshAt = 0;

  const INSIGHTS_REFRESH_COOLDOWN_MS = 300000;



  function insightsUrl(refresh, dateOverride) {

    const params = new URLSearchParams();

    const resolvedDate =
      dateOverride === null ? null : dateOverride || dateParam;
    if (resolvedDate) params.set("date", resolvedDate);

    if (useCache) params.set("use_cache", "true");

    if (refresh) params.set("refresh", "true");

    const q = params.toString();

    return `/api/games/mlb/${encodeURIComponent(gameId)}/insights${q ? `?${q}` : ""}`;

  }



  async function fetchInsights(refresh, dateOverride) {
    return fetchJSON(insightsUrl(refresh, dateOverride), { timeoutMs: 90000 });
  }



  async function loadInsightsWithFallback(refresh) {
    const notFound = (err) =>
      /not found/i.test(String(err?.message || ""));

    try {
      return await fetchInsights(refresh);
    } catch (err) {
      if (!notFound(err)) throw err;
    }

    if (dateParam) {
      try {
        return await fetchInsights(refresh, null);
      } catch (err) {
        if (!notFound(err)) throw err;
      }
    }

    try {
      const data = await fetchJSON(scoresUrl, { timeoutMs: 12000 });
      const live = (data.games || []).find(
        (g) => String(g.game_id) === String(gameId)
      );
      const scoreDate = live?.date || data?.date;
      if (scoreDate && scoreDate !== dateParam) {
        return await fetchInsights(refresh, scoreDate);
      }
    } catch (_) {
      /* fall through */
    }

    throw new Error("Game not found — it may have finished or moved off today's slate.");
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



  function lastFiveHtml(games) {
    if (!games || !games.length) {
      return `
        <div class="team-last5">
          <p class="team-last5-title">Last 5</p>
          <p class="team-last5-empty">No recent games</p>
        </div>
      `;
    }

    const rows = games
      .map(
        (g) => `
          <li class="team-last5-row ${g.won ? "last5-win" : "last5-loss"}">
            <span class="last5-result">${g.won ? "W" : "L"}</span>
            <span class="last5-score">${g.team_runs}-${g.opp_runs}</span>
            <span class="last5-opp">${g.at_vs} ${g.opponent_short || g.opponent}</span>
          </li>
        `
      )
      .join("");

    return `
      <div class="team-last5">
        <p class="team-last5-title">Last 5</p>
        <ul class="team-last5-list">${rows}</ul>
      </div>
    `;
  }

  function teamColumnHtml(side, game, cards, highlights, recentGames) {

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
        ${lastFiveHtml(recentGames)}
        ${typeof formSparklineHtml === "function" && recentGames && recentGames.length ? formSparklineHtml(recentGames) : ""}
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

    const winLine = `<p class="model-win">${winPct} win</p>`;

    const ouLine = cards.total?.line != null ? cards.total.line : "—";

    const totalsLine = model.totals_pick

      ? `<p><strong>O/U pick:</strong> ${model.totals_pick} ${ouLine} · edge ${model.total_edge != null ? (model.total_edge * 100).toFixed(1) + "%" : "—"}</p>`

      : "";

    const pickBadge = model.plus_ev_single
      ? `<span class="pick-edge-chip">Actionable</span>`
      : `<span class="hero-chip hero-chip-muted">Model lean</span>`;

    const evLine =
      model.plus_ev_single && model.ev_pick
        ? `<p class="model-ev-line"><strong>+EV pick:</strong> ${model.ev_pick || model.pick}${model.ev_edge != null ? ` · +${(model.ev_edge * 100).toFixed(1)}%` : model.edge != null ? ` · +${(model.edge * 100).toFixed(1)}%` : ""}</p>`
        : model.ev_pick && model.ev_pick !== model.pick
          ? `<p class="model-ev-line"><strong>+EV value:</strong> ${model.ev_pick}${model.ev_edge != null ? ` · +${(model.ev_edge * 100).toFixed(1)}%` : ""}</p>`
          : "";

    const confHtml =
      typeof confidenceMeterHtml === "function"
        ? confidenceMeterHtml({ model_confidence: model.win_confidence || model.confidence })
        : "";

    return `

      <div class="model-center-col ntg-card">

        <p class="model-center-label">Model ${pickBadge}</p>

        <p class="model-pick">${model.pick}</p>

        ${winLine}

        ${confHtml}

        ${evLine}

        <p class="model-runs">Est. total runs: <strong>${runs}</strong></p>

        <p class="model-edge">Win tier ${model.win_confidence || model.confidence || "Lean only"} · Edge ${edge}${model.ev_confidence && model.ev_confidence !== "—" ? ` · +EV ${model.ev_confidence}` : ""}</p>

        ${totalsLine}

      </div>

    `;

  }



  function renderMatchupBoard(data) {

    const game = data.game;

    const cards = data.market_cards || {};

    const highlights = data.highlights || {};
    const recent = data.recent_games || {};

    boardEl.innerHTML = [

      teamColumnHtml("away", game, cards, highlights, recent.away),

      modelCenterHtml(data.model, cards),

      teamColumnHtml("home", game, cards, highlights, recent.home),

    ].join("");

  }

  function fmtStatAvg(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return String(v);
    return n.toFixed(3).replace(/^0(?=\.)/, "");
  }

  function fmtStatNum(v, digits = 2) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return String(v);
    return n.toFixed(digits);
  }

  function pitcherCardHtml(p) {
    if (!p) {
      return `<div class="lineup-sp-empty"><p>Probable pitcher TBD</p></div>`;
    }
    const s = p.stats || {};
    return `
      <div class="lineup-sp-card">
        <img class="lineup-player-photo lineup-sp-photo" src="${p.photo_url}" alt="" width="72" height="72" loading="lazy">
        <div class="lineup-sp-meta">
          <p class="lineup-sp-label">Starting pitcher</p>
          <p class="lineup-sp-name">${p.name}</p>
          <p class="lineup-sp-stats">ERA ${fmtStatNum(s.era)} · ${s.wins ?? "—"}-${s.losses ?? "—"} · ${s.strikeOuts ?? "—"} K · WHIP ${fmtStatNum(s.whip)}</p>
        </div>
      </div>`;
  }

  function lineupTableHtml(lineup) {
    if (!lineup || !lineup.length) {
      return `<p class="lineup-empty">Batting order not posted yet.</p>`;
    }
    const rows = lineup
      .map(
        (r) => {
          const s = r.stats || {};
          return `<tr>
            <td>${r.order}</td>
            <td class="lineup-player-cell">
              <img class="lineup-player-photo" src="${r.photo_url}" alt="" width="40" height="40" loading="lazy">
              <span>${r.name}</span>
            </td>
            <td>${r.position || "—"}</td>
            <td>${fmtStatAvg(s.avg)}</td>
            <td>${s.homeRuns ?? "—"}</td>
            <td>${s.rbi ?? "—"}</td>
          </tr>`;
        }
      )
      .join("");
    return `
      <table class="lineup-table">
        <thead>
          <tr><th>#</th><th>Player</th><th>Pos</th><th>AVG</th><th>HR</th><th>RBI</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function teamLineupColumnHtml(side, game, payload) {
    const team = side === "away" ? game.away_team : game.home_team;
    const teamId = side === "away" ? game.away_team_id : game.home_team_id;
    const logo = game[side === "away" ? "away_logo_url" : "home_logo_url"] || teamLogoUrl(teamId);
    const block = payload[side] || {};
    return `
      <div class="lineup-team-col ${side}">
        <div class="lineup-team-head">
          <img class="team-logo" src="${logo}" alt="" width="40" height="40" loading="lazy">
          <h3>${team}</h3>
        </div>
        ${pitcherCardHtml(block.starting_pitcher)}
        ${lineupTableHtml(block.lineup)}
      </div>`;
  }

  function renderLineup(data, game) {
    const board = document.getElementById("lineup-board");
    const note = document.getElementById("lineup-note");
    if (!board || !game) return;
    if (data.message && note) note.textContent = data.message;
    board.innerHTML = `
      <div class="lineup-columns">
        ${teamLineupColumnHtml("away", game, data)}
        ${teamLineupColumnHtml("home", game, data)}
      </div>`;
  }

  async function loadLineup(game) {
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    const q = params.toString();
    try {
      const data = await fetchJSON(
        `/api/games/mlb/${encodeURIComponent(gameId)}/lineup${q ? `?${q}` : ""}`
      );
      renderLineup(data, game);
    } catch (_) {
      const board = document.getElementById("lineup-board");
      if (board) {
        board.innerHTML = `<p class="lineup-empty">Could not load lineups.</p>`;
      }
    }
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



  async function propsUrl(refresh, { extendedMarkets = false } = {}) {
    const build =
      typeof window.buildPropBookQueryWithRefresh === "function"
        ? window.buildPropBookQueryWithRefresh
        : async (extra) => window.buildPropBookQuery(extra);
    const params = await build({ refresh: !!refresh });
    if (extendedMarkets) params.set("include_all_markets", "true");
    if (dateParam) params.set("date", dateParam);
    const q = params.toString();
    return `/api/games/mlb/${encodeURIComponent(gameId)}/props${q ? `?${q}` : ""}`;
  }

  let _gameMarketTypes = null;

  async function loadGameMarketTypes() {
    if (_gameMarketTypes) return _gameMarketTypes;
    if (ntgGamePageData?.propMarkets?.length) {
      _gameMarketTypes = ntgGamePageData.propMarkets;
      return _gameMarketTypes;
    }
    try {
      const data = await fetchJSON("/api/props/markets");
      _gameMarketTypes = data.markets || [];
    } catch {
      _gameMarketTypes = [];
    }
    return _gameMarketTypes;
  }

  function marketLabel(key) {
    const found = (_gameMarketTypes || []).find((m) => m.key === key);
    return found?.label || String(key || "").replace(/_/g, " ");
  }



  function propLegId(prop, side) {

    return [gameId, prop.player, prop.market_type, prop.line, side].join("|");

  }



  function propFormAverage(prop) {
    if (typeof window.propFormComposite === "function") {
      return window.propFormComposite(prop);
    }
    if (prop?.form_average != null) return Number(prop.form_average);
    const side = prop?.recommended_side || "over";
    const over = side === "over";
    const vals = [
      over ? prop.hit_rate_over_l5 : prop.hit_rate_under_l5,
      over ? prop.hit_rate_over_l10 : prop.hit_rate_under_l10,
      over ? prop.hit_rate_over_season : prop.hit_rate_under_season,
    ].filter((r) => r != null);
    if (!vals.length) return prop?.recommended_hit_rate ?? 0;
    return vals.reduce((s, r) => s + Number(r), 0) / vals.length;
  }

  function propHitRatesHtml(prop, side) {
    if (typeof window.propHitRatesHtml === "function") {
      return window.propHitRatesHtml(prop, side);
    }
    const overKey = side === "over";
    const l5 = overKey ? prop.hit_rate_over_l5 : prop.hit_rate_under_l5;
    const l10 = overKey ? prop.hit_rate_over_l10 : prop.hit_rate_under_l10;
    const season = overKey ? prop.hit_rate_over_season : prop.hit_rate_under_season;
    const fmt = (v) => (v != null ? `${Math.round(v * 100)}%` : "—");
    return `L5 ${fmt(l5)} · L10 ${fmt(l10)} · Season ${fmt(season)}`;
  }

  function propLineStrengthHtml(prop) {
    if (typeof window.lineStrengthHtml === "function") {
      return window.lineStrengthHtml(prop);
    }
    return "";
  }



  function renderProps(data) {

    if (data) window._gamePropsData = data;
    data = data || window._gamePropsData;
    const loadingEl = document.getElementById("props-loading");
    const errEl = document.getElementById("props-error");

    const bodyEl = document.getElementById("props-body");

    const refreshBtn = document.getElementById("props-refresh");

    if (!bodyEl) {
      console.error("Player props UI missing — deploy latest game.html");
      return;
    }

    loadingEl?.classList.add("hidden");

    if (!data || data.status === "empty") {

      bodyEl.classList.remove("hidden");

      bodyEl.innerHTML = `<p class="props-empty">${data?.message || "No player props available for this game yet."}</p>`;

      return;

    }

    const top = data.top_picks || [];
    const veryStrong = data.very_strong_picks || [];

    let all = data.props || [];

    const filterActionable = document.getElementById("game-props-filter-actionable")?.checked;
    const filterVeryStrong = document.getElementById("game-props-filter-very-strong")?.checked;
    const filterMarket = document.getElementById("game-props-filter-market")?.value || "";
    if (filterActionable || filterVeryStrong || filterMarket) {
      all = all.filter((p) => {
        if (filterActionable && !p.actionable) return false;
        if (filterVeryStrong && p.line_strength !== "very_strong") return false;
        if (filterMarket && p.market_type !== filterMarket) return false;
        return true;
      });
    }

    const marketTypes = [...new Set((data.props || []).map((p) => p.market_type).filter(Boolean))].sort(
      (a, b) => marketLabel(a).localeCompare(marketLabel(b))
    );
    const marketOptions = marketTypes
      .map(
        (m) =>
          `<option value="${m}"${filterMarket === m ? " selected" : ""}>${marketLabel(m)}</option>`
      )
      .join("");

    all = all.slice().sort((a, b) => {
      const scoreDiff = (Number(b.prop_score ?? b.score ?? 0) - Number(a.prop_score ?? a.score ?? 0));
      if (scoreDiff !== 0) return scoreDiff;
      const ma = marketLabel(a.market_type);
      const mb = marketLabel(b.market_type);
      if (ma !== mb) return ma.localeCompare(mb);
      return String(a.player || "").localeCompare(String(b.player || ""));
    });

    let lastMarket = null;
    const tableRows = all
      .map((p, i) => {
        const side = p.recommended_side || "over";
        const sideLabel = side === "under" ? "Under" : "Over";
        const odds = p.recommended_odds ?? (side === "over" ? p.over_odds : p.under_odds);
        const score = p.prop_score != null ? `${Math.round(p.prop_score)}` : p.score != null ? `${Math.round(p.score)}` : "—";
        const hitRates = propHitRatesHtml(p, side);
        const lineStrength = propLineStrengthHtml(p);
        const modelMeta = typeof window.propModelMetaHtml === "function" ? window.propModelMetaHtml(p) : "";
        const actionable = p.actionable
          ? `<span class="prop-offer-tag">Top offer</span>`
          : "";
        let marketHeader = "";
        if (p.market_type !== lastMarket) {
          lastMarket = p.market_type;
          marketHeader = `<tr class="props-market-header"><td colspan="8"><strong>${marketLabel(p.market_type)}</strong></td></tr>`;
        }
        return `${marketHeader}<tr class="prop-row-clickable${propGradeClass(p)}" data-open-game-prop="${i}" data-open-game-prop-list="all" role="button" tabindex="0">
          <td>${p.player}</td>
          <td>${p.market_label || marketLabel(p.market_type)}</td>
          <td>${p.line}</td>
          <td>${sideLabel} ${fmtOdds(odds)} ${actionable}</td>
          <td>${score}</td>
          <td class="prop-hit-rates">${hitRates}</td>
          <td class="prop-line-strength">${lineStrength}${modelMeta}${p.line_insight && !modelMeta ? `<span class="prop-line-insight">${p.line_insight}</span>` : ""}</td>
          <td class="props-actions">${propActionButtons(p, i, "all")}</td>
        </tr>`;
      })
      .join("");

    const propsFilterBar = `
      <div class="game-props-filters ntg-card">
        <label><input type="checkbox" id="game-props-filter-actionable"${filterActionable ? " checked" : ""}> Actionable only</label>
        <label><input type="checkbox" id="game-props-filter-very-strong"${filterVeryStrong ? " checked" : ""}> Very strong</label>
        <label>Market
          <select id="game-props-filter-market">
            <option value="">All</option>
            ${marketOptions}
          </select>
        </label>
      </div>`;

    const bookLabel = data.bookmaker_label
      ? `<p class="props-book-label">Lines: ${data.bookmaker_label}</p>`
      : "";

    const veryStrongBlock = veryStrong.length
      ? `<div class="props-block props-block-very-strong">
          <h3>Very strong · 100% L5 / L10 / Season</h3>
          <div class="props-cards">${veryStrong.map((p, i) => propCardHtml(p, data, i, "veryStrong")).join("")}</div>
        </div>`
      : "";

    const topBlock = top.length

      ? `<div class="props-block">

          <h3>Top form plays</h3>

          <div class="props-cards">${top.map((p, i) => propCardHtml(p, data, i, "top")).join("")}</div>

        </div>`

      : "";

    bodyEl.classList.remove("hidden");

    bodyEl.innerHTML = `

      ${bookLabel}

      ${propsFilterBar}

      ${veryStrongBlock}

      ${topBlock}

      <details class="props-all-lines" ${top.length || veryStrong.length ? "open" : ""}>

        <summary>All lines (${all.length})</summary>

        <div class="props-table-wrap">

          <table class="props-table">

            <thead>

              <tr>

                <th>Player</th>

                <th>Market</th>

                <th>Line</th>

                <th>Lean / odds</th>

                <th>Score</th>

                <th>Hit rates (pick side)</th>

                <th>Line strength</th>

                <th></th>

              </tr>

            </thead>

            <tbody>${tableRows || `<tr><td colspan="8">No lines</td></tr>`}</tbody>

          </table>

        </div>

      </details>

    `;

    bodyEl.querySelectorAll("[data-add-prop]").forEach((btn) => {

      btn.addEventListener("click", (e) => {

        e.stopPropagation();

        const idx = Number(btn.dataset.propIndex);

        const listName = btn.dataset.list || "all";
        const list =
          listName === "veryStrong" ? veryStrong : listName === "top" ? top : all;

        const prop = list[idx];

        if (!prop || !prop.actionable || !window.addPropToSlip) return;

        const side = prop.recommended_side || btn.dataset.side;

        const odds = side === "over" ? prop.over_odds : prop.under_odds;

        if (odds == null) return;

        window.addPropToSlip(
          window.propSlipLegFromProp({
            ...prop,
            game_id: gameId,
            matchup: data.matchup,
            recommended_odds: odds,
          })
        );

        btn.textContent = "Added";

        btn.disabled = true;

      });

    });

    wireGamePropModals(bodyEl, { veryStrong, top, all }, data);

    ["game-props-filter-actionable", "game-props-filter-very-strong", "game-props-filter-market"].forEach((id) => {
      document.getElementById(id)?.addEventListener("change", () => renderProps());
    });

    if (refreshBtn) {
      refreshBtn.onclick = () => {
        if (useEmbeddedPage) {
          const sel = document.getElementById("prop-book-select");
          reloadGamePage({
            refresh: true,
            bookmaker: sel?.value || ntgGamePageData?.bookmaker,
          });
          return;
        }
        loadProps(true);
      };
    }
  }



  function wireGamePropModals(bodyEl, lists, data) {
    if (typeof openPropModal !== "function") return;
    const resolveProp = (listName, idx) => {
      const list = lists[listName];
      const prop = list?.[idx];
      if (!prop) return null;
      const side = prop.recommended_side || "over";
      const odds = side === "over" ? prop.over_odds : prop.under_odds;
      return {
        ...prop,
        game_id: gameId,
        matchup: data.matchup,
        recommended_odds: odds,
      };
    };
    bodyEl.querySelectorAll("[data-open-game-prop]").forEach((el) => {
      const listName = el.dataset.openGamePropList || "all";
      const idx = Number(el.dataset.openGameProp);
      const open = () => {
        const prop = resolveProp(listName, idx);
        const propForModal =
          typeof window.normalizePropForModal === "function"
            ? window.normalizePropForModal({
                ...prop,
                game_id: gameId,
                matchup: data.matchup,
                recommended_odds: odds,
              })
            : {
                ...prop,
                game_id: gameId,
                matchup: data.matchup,
                recommended_odds: odds,
              };
        if (propForModal) openPropModal(propForModal, "mlb");
      };
      el.addEventListener("click", (e) => {
        if (e.target.closest("button")) return;
        open();
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
    });
  }



  function propCardHtml(prop, data, index, listName = "all") {

    const side = prop.recommended_side || "over";

    const odds = side === "over" ? prop.over_odds : prop.under_odds;

    const factors = (prop.factors || []).slice(0, 3).map((f) => `<li>${f}</li>`).join("");
    const lineStrength = propLineStrengthHtml(prop);
    const strengthBlock = lineStrength || prop.line_insight
      ? `<p class="prop-card-strength">${lineStrength}${prop.line_insight ? `<span class="prop-line-insight">${prop.line_insight}</span>` : ""}</p>`
      : "";

    const veryStrong =
      typeof window.propVeryStrongClass === "function" ? window.propVeryStrongClass(prop) : "";
    const heatCls =
      typeof propHeatClass === "function" ? propHeatClass(prop) : "";

    return `<article class="prop-card ntg-card prop-card-clickable${veryStrong} ${heatCls}" data-open-game-prop="${index}" data-open-game-prop-list="${listName}" role="button" tabindex="0" aria-label="View ${prop.player} prop details">

      <div class="prop-card-head">

        <strong>${prop.player}</strong>

        <span class="prop-score">${prop.score != null ? Math.round(prop.score) : "—"}</span>

      </div>

      <p class="prop-card-line">${prop.market_label}: ${side} ${prop.line} (${fmtOdds(odds)})</p>

      <p class="prop-card-meta">${propHitRatesHtml(prop, side)}</p>

      ${strengthBlock}

      ${factors ? `<ul class="prop-card-factors">${factors}</ul>` : ""}

      <div class="prop-card-actions">${propActionButtons(prop, index, listName)}</div>

    </article>`;

  }



  function propActionButtons(prop, index, listName) {

    if (!prop.actionable) {
      return `<span class="prop-skip-note">${prop.actionable_reason || "Not recommended"}</span>`;
    }

    const list = listName === "veryStrong" ? "veryStrong" : listName === "top" ? "top" : "all";

    const side = prop.recommended_side;

    if (!side) return "";

    const odds = side === "over" ? prop.over_odds : prop.under_odds;

    if (odds == null) return "";

    if (!window.propSlipEnabled) return "";

    const label = side === "over" ? "+ Over" : "+ Under";

    return `<button type="button" class="btn-add-prop" data-add-prop="1" data-side="${side}" data-prop-index="${index}" data-list="${list}">${label}</button>`;

  }



  async function loadProps(refresh) {

    const loadingEl = document.getElementById("props-loading");

    const errEl = document.getElementById("props-error");

    const bodyEl = document.getElementById("props-body");

    try {

      loadingEl?.classList.remove("hidden");

      bodyEl?.classList.add("hidden");

      errEl?.classList.add("hidden");

      if (!refresh && embeddedGameProps) {
        const payload = embeddedGameProps;
        embeddedGameProps = null;
        renderProps(payload);
        return;
      }

      const res = await fetch(await propsUrl(refresh, { extendedMarkets: !!refresh }));
      if (res.status === 401) {
        loadingEl?.classList.add("hidden");
        bodyEl?.classList.remove("hidden");
        if (bodyEl && window.renderPropsAuthGate) {
          window.renderPropsAuthGate(bodyEl, window.location.pathname);
        } else if (errEl) {
          errEl.classList.remove("hidden");
          errEl.textContent = "Sign in to view player props.";
        }
        return;
      }
      if (!res.ok) {
        throw new Error(await res.text() || `HTTP ${res.status}`);
      }
      const data = await res.json();

      renderProps(data);

    } catch (e) {

      loadingEl?.classList.add("hidden");

      if (errEl) {

        errEl.classList.remove("hidden");

        errEl.textContent = e.message || "Could not load props";

      }

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



  function explanationList(title, items) {

    if (!items || !items.length) {

      return `<div class="explain-block"><h3>${title}</h3><p class="muted-label">No clear edge on available factors.</p></div>`;

    }

    return `

      <div class="explain-block">

        <h3>${title}</h3>

        <ul class="explain-list">

          ${items.map((t) => `<li>${t}</li>`).join("")}

        </ul>

      </div>`;

  }



  function pctFmt(rate) {
    if (rate == null || Number.isNaN(Number(rate))) return "—";
    return `${Math.round(Number(rate) * 100)}%`;
  }

  function renderFormVsModel(form, model, game) {
    if (!formVsModelEl || !form) {
      if (formVsModelEl) formVsModelEl.classList.add("hidden");
      return;
    }
    const home = form.home || {};
    const away = form.away || {};
    const homeName = form.home_team || game?.home_team || "Home";
    const awayName = form.away_team || game?.away_team || "Away";
    const modelPick = model?.pick || "—";
    const formPick = form.form_pick_team || "Even";
    const agrees = form.model_agrees_with_form;
    let note = "";
    if (form.form_favors_side === "even") {
      note = "Recent form (L5 / L10 / season) is roughly even — model may lean on starting pitching and Elo.";
    } else if (agrees === true) {
      note = `Model pick aligns with hotter recent form (${formPick}).`;
    } else if (agrees === false) {
      note = `Model favors ${modelPick} but recent form leans ${formPick} — check pitching and factor table below.`;
    }
    const noteClass =
      agrees === false ? "form-vs-model-note form-mismatch" : "form-vs-model-note";
    formVsModelEl.classList.remove("hidden");
    formVsModelEl.innerHTML = `
      <h3>Model pick vs team form</h3>
      <div class="form-vs-model-grid">
        <div class="form-vs-model-card">
          <p class="form-team-name">Model pick</p>
          <p><strong>${modelPick}</strong></p>
        </div>
        <div class="form-vs-model-card">
          <p class="form-team-name">Form lean (L5 / L10 / season)</p>
          <p><strong>${formPick}</strong></p>
        </div>
        <div class="form-vs-model-card">
          <p class="form-team-name">${awayName}</p>
          <p>L5 ${pctFmt(away.win_rate_l5)} · L10 ${pctFmt(away.win_rate_l10)} · Season ${pctFmt(away.win_rate_season)}</p>
        </div>
        <div class="form-vs-model-card">
          <p class="form-team-name">${homeName}</p>
          <p>L5 ${pctFmt(home.win_rate_l5)} · L10 ${pctFmt(home.win_rate_l10)} · Season ${pctFmt(home.win_rate_season)}</p>
        </div>
      </div>
      ${note ? `<p class="${noteClass}">${note}</p>` : ""}
    `;
  }



  function renderExplanation(explanation) {

    const el = document.getElementById("model-explanation-body");

    const section = document.getElementById("model-explanation-section");

    if (!el || !section) return;

    if (!explanation) {

      el.innerHTML = "<p class=\"muted-label\">Explanation unavailable — reload board or try demo date with cache.</p>";

      return;

    }

    const totals = explanation.totals;

    const factorRows = (explanation.factor_comparison || [])

      .map(

        (row) => `

        <tr>

          <td>${row.factor}</td>

          <td>${row.home}</td>

          <td>${row.away}</td>

          <td class="explain-edge-${row.edge}">${

            row.edge === "home"

              ? explanation.home_team

              : row.edge === "away"

                ? explanation.away_team

                : "Even"

          }</td>

        </tr>`

      )

      .join("");

    const homeCol = explanation.home_team || "Home";

    const awayCol = explanation.away_team || "Away";

    const totalsHtml = totals

      ? `

      <div class="explain-block">

        <h3>Runs / O-U</h3>

        ${totals.summary ? `<p class="explain-summary">${totals.summary}</p>` : ""}

        <ul class="explain-list">

          ${(totals.bullets || []).map((t) => `<li>${t}</li>`).join("")}

        </ul>

      </div>`

      : "";

    const factorVoteSummary = explanation.factor_majority_team && explanation.factor_votes
      ? `<p class="explain-summary explain-alignment">${explanation.away_team} ${explanation.factor_votes.away} · ${explanation.home_team} ${explanation.factor_votes.home} factor edges${explanation.factor_votes.neutral ? ` (${explanation.factor_votes.neutral} even)` : ""}</p>`
      : "";

    const alignmentHtml = explanation.alignment_note
      ? `<p class="explain-alignment ${explanation.pick_reconciled ? "explain-aligned" : "explain-mismatch"}">${explanation.alignment_note}</p>`
      : "";

    const ens = explanation.ensemble_components;
    const ensembleHtml = ens
      ? `<div class="explain-block"><h3>Ensemble breakdown</h3><ul class="explain-list">
          <li>Logistic: ${(ens.logistic * 100).toFixed(1)}% home</li>
          <li>Gradient boost: ${(ens.gbc * 100).toFixed(1)}% home</li>
          <li>Elo: ${(ens.elo * 100).toFixed(1)}% home</li>
          <li>Blended (pre-calibration): ${(ens.ensemble_raw * 100).toFixed(1)}% home</li>
          <li>Final ensemble: ${(ens.ensemble * 100).toFixed(1)}% home</li>
        </ul></div>`
      : "";

    el.innerHTML = `

      ${explanation.summary ? `<p class="explain-summary">${explanation.summary}</p>` : ""}

      ${factorVoteSummary}

      ${alignmentHtml}

      <div class="explain-prob-row">

        <span><strong>${explanation.home_team}</strong> ${explanation.home_win_pct != null ? explanation.home_win_pct + "%" : "—"} model win</span>

        <span><strong>${explanation.away_team}</strong> ${explanation.away_win_pct != null ? explanation.away_win_pct + "%" : "—"} model win</span>

      </div>

      <div class="explain-columns">

        ${explanationList(`Why ${explanation.home_team} could win`, explanation.why_home)}

        ${explanationList(`Why ${explanation.away_team} could win`, explanation.why_away)}

      </div>

      ${totalsHtml}

      ${factorRows ? `<div class="explain-block"><h3>Factor snapshot</h3><p class="explain-legend"><strong>${homeCol}</strong> is the home team in this table; <strong>${awayCol}</strong> is the away team.</p><table class="explain-table"><thead><tr><th>Factor</th><th>${homeCol}</th><th>${awayCol}</th><th>Edge</th></tr></thead><tbody>${factorRows}</tbody></table></div>` : ""}

      ${ensembleHtml}

      ${explanation.disclaimer ? `<p class="explain-footnote">${explanation.disclaimer}</p>` : ""}

    `;

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

    renderMatchupHeader(header, data.game, data.board_row);

    renderMatchupBoard(data);

    renderFormVsModel(data.form_comparison, data.model, data.game);

    renderExplanation(data.explanation);

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

          if (useEmbeddedPage) {
            reloadGamePage({ refresh: true });
            return;
          }

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

      if (live) renderMatchupHeader(header, live, lastInsightsData?.board_row);

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



  async function prefetchMatchupHeader() {
    try {
      const data = await fetchJSON(scoresUrl, { timeoutMs: 12000 });
      const live = (data.games || []).find((g) => String(g.game_id) === String(gameId));
      if (live && header) {
        renderMatchupHeader(header, live, null);
        if (loading && !loading.classList.contains("hidden")) {
          loading.textContent = "Loading lines and model…";
        }
      }
    } catch (_) {
      /* insights will populate header */
    }
  }



  async function loadInsights(refresh) {

    const data = await loadInsightsWithFallback(refresh);

    loading.classList.add("hidden");

    content.classList.remove("hidden");

    renderInsights(data);
    loadLineup(data.game);

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



  function schedulePropsLoad(refresh = false) {
    const run = () => {
      const propBookSelect = document.getElementById("prop-book-select");
      (async () => {
        try {
          if (typeof initPropBookSelect === "function") {
            const onBookChange = useEmbeddedPage
              ? () => {
                  reloadGamePage({ bookmaker: propBookSelect?.value });
                }
              : () => loadProps(false);
            await initPropBookSelect(
              propBookSelect,
              onBookChange,
              ntgGamePageData?.bookmakers
            );
            if (useEmbeddedPage && ntgGamePageData?.bookmaker && propBookSelect) {
              propBookSelect.value = ntgGamePageData.bookmaker;
            }
          }
          await loadGameMarketTypes();
          await loadProps(refresh);
        } catch (_) {
          /* loadProps handles its own error UI */
        }
      })();
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(run);
    } else {
      window.setTimeout(run, 0);
    }
  }

  function applyEmbeddedGamePage(pageData) {
    loading.classList.add("hidden");
    content.classList.remove("hidden");
    renderInsights(pageData.insights);
    loadLineup(pageData.insights?.game);
    if (pageData.gameProps) embeddedGameProps = pageData.gameProps;
    if (pageData.propMarkets?.length) _gameMarketTypes = pageData.propMarkets;
    schedulePropsLoad(false);
  }

  loadTeamColors()
    .then(async () => {
      if (typeof window.ensureAppReady === "function") {
        await window.ensureAppReady();
      }
      if (typeof initDesignSystem === "function") initDesignSystem();
      if (typeof initGameStickyNav === "function") initGameStickyNav();

      if (ntgGamePageData?.kind === "mlb_game" && ntgGamePageData.insights) {
        applyEmbeddedGamePage(ntgGamePageData);
        return;
      }

      prefetchMatchupHeader();
      try {
        await loadInsights(false);
      } catch (e) {
        loading.classList.add("hidden");
        if (errEl) {
          errEl.classList.remove("hidden");
          const msg = e.message || "Could not load game insights";
          if (typeof brandedErrorState === "function") {
            brandedErrorState(errEl, {
              title: "Game insights unavailable",
              message: msg,
              onRetry: () => window.location.reload(),
            });
          } else {
            errEl.textContent = msg;
          }
        }
        return;
      }
      schedulePropsLoad(false);
    })
    .catch((e) => {

      loading.classList.add("hidden");

      errEl.classList.remove("hidden");

      errEl.textContent = e.message || "Game not found";

    });

})();


