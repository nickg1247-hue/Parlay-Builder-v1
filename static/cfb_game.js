/** CFB game detail — live scores + ML / spread / O/U model and markets. */

(function () {
  const loading = document.getElementById("game-loading");
  const errEl = document.getElementById("game-error");
  const content = document.getElementById("game-content");
  const header = document.getElementById("matchup-header");
  const boardEl = document.getElementById("game-matchup-board");
  const warningsEl = document.getElementById("insights-warnings");
  const disclaimerEl = document.getElementById("game-disclaimer");
  const modelBadge = document.getElementById("model-badge");
  const featuresEl = document.getElementById("feature-snapshot");

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
    ? `/api/scores/today?sport=cfb&date=${encodeURIComponent(dateParam)}`
    : "/api/scores/today?sport=cfb";

  let scorePollerStarted = false;

  function insightsUrl(refresh) {
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    if (useCache) params.set("use_cache", "true");
    if (refresh) params.set("refresh", "true");
    const q = params.toString();
    return `/api/games/cfb/${encodeURIComponent(gameId)}/insights${q ? `?${q}` : ""}`;
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

  function proxyTag(source) {
    return source === "proxy" ? " (proxy)" : "";
  }

  function teamColumnHtml(side, game, board, spread, totals) {
    const isAway = side === "away";
    const team = isAway ? game.away_team : game.home_team;
    const col = board[side] || {};
    const hi = board.highlights || {};

    const mlTier = hi.moneyline_side === side ? hi.moneyline_tier : null;
    const spreadTier = hi.spread_side === side ? hi.spread_tier : null;
    const ouTier =
      (isAway && hi.total_side === "over") || (!isAway && hi.total_side === "under")
        ? hi.total_tier
        : null;

    const mlValue = fmtOdds(col.moneyline);
    const spreadValue = col.spread != null ? fmtPoint(col.spread) : "—";

    const ouLine = isAway ? col.total_over : col.total_under;
    const ouValue =
      ouLine != null
        ? isAway
          ? `Over ${ouLine}`
          : `Under ${ouLine}`
        : "—";

    const spreadCard = statCard(
      "Spread",
      spreadValue + proxyTag(spread.spread_line_source),
      spreadTier
    );
    const ouCard = statCard(
      "O/U",
      ouValue + proxyTag(totals.ou_line_source),
      ouTier
    );

    return `
      <div class="team-market-col ${side}">
        <img class="team-logo team-market-logo" src="${logoForGame(game, side)}" alt="" width="48" height="48" loading="lazy">
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
    return `<p class="model-margin">Pred. margin: ${favored} by ${by}</p>`;
  }

  function modelCenterHtml(data) {
    const ml = data.moneyline || {};
    const spread = data.spread || {};
    const totals = data.totals || {};
    const game = data.game || {};

    if (!ml.model_pick) {
      return `<div class="model-center-col"><p class="model-empty">No model data for this game.</p></div>`;
    }

    const side = ml.model_pick_side || (ml.model_prob_home >= 0.5 ? "home" : "away");
    const winProb =
      side === "home" ? ml.model_prob_home : ml.model_prob_away;
    const winPct = winProb != null ? `${(winProb * 100).toFixed(1)}%` : "—";

    const edgeHome = ml.ev_home;
    const edgeAway = ml.ev_away;
    const edge =
      edgeHome != null && edgeAway != null
        ? Math.max(edgeHome, edgeAway)
        : edgeHome ?? edgeAway;
    const edgeStr = edge != null ? `${(edge * 100).toFixed(1)}%` : "—";

    const evBadge = ml.plus_ev_ml
      ? `<p class="model-ev-badge">+EV ML (≥8% edge)</p>`
      : "";

    const ouLine = totals.ou_line != null ? totals.ou_line : "—";
    const totalsBlock = totals.totals_pick
      ? `<p><strong>O/U pick:</strong> ${totals.totals_pick}${ouLine !== "—" ? ` @ ${ouLine}` : ""}</p>`
      : totals.expected_total_pts != null
        ? `<p class="model-runs">Est. total pts: <strong>${totals.expected_total_pts}</strong></p>`
        : "";

    const marginBlock =
      spread.model_margin != null ? marginLabel(game, spread.model_margin) : "";

    const spreadPickBlock = spread.spread_pick
      ? `<p><strong>Spread pick:</strong> ${spread.spread_pick}${proxyTag(spread.spread_line_source)}</p>`
      : "";

    const marketBlock =
      ml.market_prob_home != null
        ? `<p class="model-market">Market P(home): ${(ml.market_prob_home * 100).toFixed(1)}%</p>`
        : "";

    return `
      <div class="model-center-col">
        <p class="model-center-label">Model</p>
        <p class="model-pick">${ml.model_pick}</p>
        <p class="model-win">${winPct} win</p>
        ${marketBlock}
        ${totalsBlock}
        ${marginBlock}
        ${spreadPickBlock}
        <p class="model-edge">Edge ${edgeStr} · ${ml.ml_confidence || "—"}</p>
        ${evBadge}
      </div>
    `;
  }

  function renderMatchupBoard(data) {
    const board = data.matchup_board || {};
    boardEl.innerHTML = [
      teamColumnHtml("away", data.game, board, data.spread || {}, data.totals || {}),
      modelCenterHtml(data),
      teamColumnHtml("home", data.game, board, data.spread || {}, data.totals || {}),
    ].join("");
  }

  function renderFeatureSnapshot(features) {
    if (!featuresEl) return;
    if (!features || !features.length) {
      featuresEl.innerHTML = "<p class=\"model-empty\">Feature snapshot unavailable.</p>";
      return;
    }
    const rows = features
      .map(
        (f) =>
          `<tr><td>${f.name}</td><td>${f.value != null ? f.value : "—"}</td><td>${f.note || ""}</td></tr>`
      )
      .join("");
    featuresEl.innerHTML = `
      <table class="feature-table">
        <thead><tr><th>Feature</th><th>Value</th><th>Note</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
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
    renderMatchupHeader(header, { ...data.game, sport: "cfb" });
    renderMatchupBoard(data);
    renderFeatureSnapshot(data.feature_snapshot);
    renderWarnings(data.warnings);

    if (disclaimerEl && data.disclaimer) {
      disclaimerEl.textContent = data.disclaimer;
    }

    const active = data.active_model || {};
    if (modelBadge && active.model_version) {
      modelBadge.classList.remove("hidden");
      modelBadge.textContent = `${active.model_version}${active.feature_set ? ` · ${active.feature_set}` : ""}`;
    }
  }

  async function refreshLiveScore() {
    try {
      const data = await fetchJSON(scoresUrl);
      const live = (data.games || []).find(
        (g) => String(g.game_id) === String(gameId)
      );
      if (live) renderMatchupHeader(header, { ...live, sport: "cfb" });
    } catch (_) {
      /* keep last header */
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
