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



  async function propsUrl(refresh) {
    const build =
      typeof window.buildPropBookQueryWithRefresh === "function"
        ? window.buildPropBookQueryWithRefresh
        : async (extra) => window.buildPropBookQuery(extra);
    const params = await build({ refresh: !!refresh });
    if (dateParam) params.set("date", dateParam);
    const q = params.toString();
    return `/api/games/mlb/${encodeURIComponent(gameId)}/props${q ? `?${q}` : ""}`;
  }



  function propLegId(prop, side) {

    return [gameId, prop.player, prop.market_type, prop.line, side].join("|");

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

    const all = data.props || [];

    const bookLabel = data.bookmaker_label
      ? `<p class="props-book-label">Lines: ${data.bookmaker_label}</p>`
      : "";

    const veryStrongBlock = veryStrong.length
      ? `<div class="props-block props-block-very-strong">
          <h3>Very strong · 100% L5 / L10 / Season</h3>
          <div class="props-cards">${veryStrong.map((p, i) => propCardHtml(p, data, i)).join("")}</div>
        </div>`
      : "";

    const topBlock = top.length

      ? `<div class="props-block">

          <h3>Top form plays</h3>

          <div class="props-cards">${top.map((p, i) => propCardHtml(p, data, i)).join("")}</div>

        </div>`

      : "";

    const tableRows = all

      .map((p, i) => {

        const side = p.recommended_side || "over";

        const odds = side === "over" ? p.over_odds : p.under_odds;

        const score = p.score != null ? `${Math.round(p.score)}` : "—";

        const hitRates = propHitRatesHtml(p, side);
        const lineStrength = propLineStrengthHtml(p);

        const actionable = p.actionable
          ? ""
          : `<span class="prop-skip-tag" title="${p.actionable_reason || "Not recommended"}">Skip</span>`;

        return `<tr class="${p.actionable ? "" : "prop-row-skip"}">

          <td>${p.player}</td>

          <td>${p.market_label || p.market_type}</td>

          <td>${p.line}</td>

          <td>${side} ${fmtOdds(odds)} ${actionable}</td>

          <td>${score}</td>

          <td class="prop-hit-rates">${hitRates}</td>

          <td class="prop-line-strength">${lineStrength}${p.line_insight ? `<span class="prop-line-insight">${p.line_insight}</span>` : ""}</td>

          <td class="props-actions">${propActionButtons(p, i, "all")}</td>

        </tr>`;

      })

      .join("");

    bodyEl.classList.remove("hidden");

    bodyEl.innerHTML = `

      ${bookLabel}

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

      btn.addEventListener("click", () => {

        const idx = Number(btn.dataset.propIndex);

        const list = btn.dataset.list === "top" ? top : all;

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

    if (refreshBtn) {
      refreshBtn.onclick = () => loadProps(true);
    }
  }



  function propCardHtml(prop, data, index) {

    const side = prop.recommended_side || "over";

    const odds = side === "over" ? prop.over_odds : prop.under_odds;

    const factors = (prop.factors || []).slice(0, 3).map((f) => `<li>${f}</li>`).join("");
    const lineStrength = propLineStrengthHtml(prop);
    const strengthBlock = lineStrength || prop.line_insight
      ? `<p class="prop-card-strength">${lineStrength}${prop.line_insight ? `<span class="prop-line-insight">${prop.line_insight}</span>` : ""}</p>`
      : "";

    const veryStrong =
      typeof window.propVeryStrongClass === "function" ? window.propVeryStrongClass(prop) : "";

    return `<article class="prop-card${veryStrong}">

      <div class="prop-card-head">

        <strong>${prop.player}</strong>

        <span class="prop-score">${prop.score != null ? Math.round(prop.score) : "—"}</span>

      </div>

      <p class="prop-card-line">${prop.market_label}: ${side} ${prop.line} (${fmtOdds(odds)})</p>

      <p class="prop-card-meta">${propHitRatesHtml(prop, side)}</p>

      ${strengthBlock}

      ${factors ? `<ul class="prop-card-factors">${factors}</ul>` : ""}

      <div class="prop-card-actions">${propActionButtons(prop, index, "top")}</div>

    </article>`;

  }



  function propActionButtons(prop, index, listName) {

    if (!prop.actionable) {
      return `<span class="prop-skip-note">${prop.actionable_reason || "Not recommended"}</span>`;
    }

    const list = listName === "top" ? "top" : "all";

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

      const res = await fetch(await propsUrl(refresh));
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

    el.innerHTML = `

      ${explanation.summary ? `<p class="explain-summary">${explanation.summary}</p>` : ""}

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

    renderMatchupHeader(header, data.game);

    renderMatchupBoard(data);

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
    .then(async () => {
      try {
        await loadInsights(false);
      } catch (e) {
        loading.classList.add("hidden");
        content.classList.remove("hidden");
        if (errEl) {
          errEl.classList.remove("hidden");
          errEl.textContent = e.message || "Could not load game insights";
        }
      }
      const propBookSelect = document.getElementById("prop-book-select");
      if (typeof initPropBookSelect === "function") {
        await initPropBookSelect(propBookSelect, () => loadProps(false));
      }
      try {
        await loadProps(false);
      } catch (_) {
        /* loadProps handles its own error UI */
      }
    })
    .catch((e) => {

      loading.classList.add("hidden");

      errEl.classList.remove("hidden");

      errEl.textContent = e.message || "Game not found";

    });

})();


