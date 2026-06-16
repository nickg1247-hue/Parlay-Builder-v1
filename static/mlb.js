const DEMO_DATE = "2025-08-15";

let boardMode = null;

const els = {
  loading: document.getElementById("loading"),
  loadingSpinner: document.getElementById("loading-spinner"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  warnings: document.getElementById("warnings"),
  error: document.getElementById("error"),
  boardDate: document.getElementById("board-date"),
  simpleBody: document.querySelector("#simple-table tbody"),
  modelPicksBody: document.querySelector("#model-picks-table tbody"),
  slateBody: document.querySelector("#slate-table tbody"),
  singles: document.getElementById("singles-list"),
  parlays: document.getElementById("parlays-list"),
  totals: document.getElementById("totals-list"),
  totalsNote: document.getElementById("totals-note"),
  spreadNote: document.getElementById("spread-note"),
  confidenceNote: document.getElementById("confidence-note"),
  footer: document.getElementById("status-footer"),
  refresh: document.getElementById("refresh-btn"),
  runLive: document.getElementById("run-live-btn"),
  runDemo: document.getElementById("run-demo-btn"),
  ouCheckbox: document.getElementById("ou-checkbox"),
  minEdgeInput: document.getElementById("min-edge-input"),
  maxParlaysInput: document.getElementById("max-parlays-input"),
  singlesThresholdLabel: document.getElementById("singles-threshold-label"),
  parlaysThresholdLabel: document.getElementById("parlays-threshold-label"),
  totalsThresholdLabel: document.getElementById("totals-threshold-label"),
  loadingMessage: document.getElementById("loading-message"),
  loadSavedBacktest: document.getElementById("load-saved-backtest"),
  runBacktest: document.getElementById("run-backtest-btn"),
  backtestIdle: document.getElementById("backtest-idle"),
  backtestLoading: document.getElementById("backtest-loading"),
  backtestLoadingMessage: document.getElementById("backtest-loading-message"),
  backtestPanel: document.getElementById("backtest-panel"),
};

function includeTotals() {
  return els.ouCheckbox && els.ouCheckbox.checked;
}

function minEdgeFraction() {
  const pct = Number(els.minEdgeInput?.value ?? 8);
  if (!Number.isFinite(pct) || pct < 0) return 0.08;
  return pct / 100;
}

function maxParlaysCount() {
  const n = Number(els.maxParlaysInput?.value ?? 5);
  if (!Number.isFinite(n) || n < 1) return 5;
  return Math.min(20, Math.round(n));
}

function edgePctLabel(fraction) {
  return `${Math.round(fraction * 1000) / 10}%`;
}

function updateThresholdLabels(edgeFraction) {
  const label = edgePctLabel(edgeFraction);
  if (els.singlesThresholdLabel) {
    els.singlesThresholdLabel.textContent = `(≥${label} edge)`;
  }
  if (els.parlaysThresholdLabel) {
    els.parlaysThresholdLabel.textContent = `(cross-game, ≥${label} EV)`;
  }
  if (els.totalsThresholdLabel) {
    els.totalsThresholdLabel.textContent = `(≥${label} edge, experimental)`;
  }
}

function loadingHint() {
  if (boardMode === "demo") {
    return includeTotals()
      ? "Loading demo board (cached odds + totals)…"
      : "Loading demo board (moneyline)…";
  }
  if (boardMode === "live") {
    return "Pulling live odds and syncing main-site game pages… May take 2–3 minutes.";
  }
  return "Click Run live or Demo to load the board.";
}

function pct(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function buildApiUrl(refresh = false) {
  const url = new URL("/api/daily", window.location.origin);
  if (boardMode === "demo") {
    url.searchParams.set("date", DEMO_DATE);
    url.searchParams.set("use_cache", "true");
  }
  url.searchParams.set("skip_totals", includeTotals() ? "false" : "true");
  url.searchParams.set("min_edge", String(minEdgeFraction()));
  url.searchParams.set("max_parlays", String(maxParlaysCount()));
  if (refresh) {
    url.searchParams.set("refresh", "true");
    if (boardMode === "live") {
      url.searchParams.set("live_test", "true");
    }
  }
  return url.toString();
}

function winPct(probHome, isHome) {
  const p = (isHome ? probHome : 1 - probHome) * 100;
  return `${p.toFixed(1)}%`;
}

function marketFavoritePct(game) {
  const home = game.market_prob_home;
  if (home == null) return "—";
  const away = 1 - home;
  if (home >= away) {
    return `${game.home_team} ${pct(home)}`;
  }
  return `${game.away_team} ${pct(away)}`;
}

function renderModelPicks(slate) {
  if (!els.modelPicksBody) return;
  els.modelPicksBody.innerHTML = "";
  if (!slate.length) {
    els.modelPicksBody.innerHTML =
      '<tr><td colspan="8" class="empty">No games on slate</td></tr>';
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    const hasEv = Boolean(game.ev_pick_team);
    if (game.ml_picks_disagree) tr.classList.add("pick-disagree-row");
    if (hasEv && !game.ml_picks_disagree) tr.classList.add("pick-agree-row");
    if (game.model_pick_action === "lean_only") tr.classList.add("lean-only-row");

    const confLabel = game.model_confidence || "—";
    const pickTeam =
      game.model_pick_action === "lean_only"
        ? `<span class="muted-label">${game.model_pick_team || "—"}</span>`
        : game.model_pick_team || "—";

    const evLabel = hasEv
      ? game.ev_pick_team
      : '<span class="muted-label">No +EV</span>';
    const edgeLabel = hasEv
      ? `<span class="edge-pos">+${(game.ev_pick_edge * 100).toFixed(1)}%</span>`
      : "—";
    const agreeLabel = !hasEv
      ? "—"
      : game.ml_picks_disagree
        ? '<span class="disagree-badge">No</span>'
        : '<span class="agree-badge">Yes</span>';

    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td class="model-winner-cell">${pickTeam}</td>
      <td>${pct(game.model_pick_prob)}</td>
      <td class="${modelConfidenceClass(confLabel)}">${confLabel}</td>
      <td>${marketFavoritePct(game)}</td>
      <td class="${hasEv ? "ev-pick-cell" : ""}">${evLabel}</td>
      <td>${edgeLabel}</td>
      <td>${agreeLabel}</td>
    `;
    els.modelPicksBody.appendChild(tr);
  }
}

function renderSimpleSlate(slate) {
  els.simpleBody.innerHTML = "";
  const note = document.getElementById("simple-note");
  if (note) {
    note.textContent =
      "Pure model win probability (not blended with market).";
  }
  if (!slate.length) {
    els.simpleBody.innerHTML =
      '<tr><td colspan="4" class="empty">No games on slate</td></tr>';
    return;
  }
  for (const game of slate) {
    const probHome = game.model_prob_home;
    const homePct = probHome * 100;
    const awayPct = (1 - probHome) * 100;
    const homeFav = homePct >= awayPct;
    const tr = document.createElement("tr");
    if (game.model_disagrees_heavy_favorite) {
      tr.classList.add("disagree-row");
    }
    const warn = game.model_disagrees_heavy_favorite
      ? '<div class="row-warn">Model disagrees with heavy favorite</div>'
      : "";
    tr.innerHTML = `
      <td>${game.matchup}${warn}</td>
      <td>${game.home_team}</td>
      <td class="${homeFav ? "favorite-pct" : "not-favorite-pct"}">${winPct(probHome, true)}</td>
      <td class="${!homeFav ? "favorite-pct" : "not-favorite-pct"}">${winPct(probHome, false)}</td>
    `;
    els.simpleBody.appendChild(tr);
  }
}

function fmtRuns(value) {
  if (value == null) return "—";
  return Number(value).toFixed(1);
}

function confidenceClass(label) {
  switch (label) {
    case "Low":
      return "conf-low";
    case "Medium":
      return "conf-medium";
    case "High":
      return "conf-high";
    case "Extremely high":
      return "conf-extreme";
    default:
      return "";
  }
}

function modelConfidenceClass(label) {
  switch (label) {
    case "Lean only":
    case "Blocked (stale data)":
      return "conf-lean";
    case "Low":
      return "conf-low";
    case "Moderate":
      return "conf-medium";
    case "High":
      return "conf-high";
    case "Very high":
      return "conf-extreme";
    default:
      return "";
  }
}

function fmtSpreadLine(team, point, american) {
  if (point == null || american == null) return "—";
  const sign = point > 0 ? "+" : "";
  const am = american > 0 ? `+${american}` : `${american}`;
  return `${team} ${sign}${point} (${am})`;
}

function spreadCoverPct(game, side) {
  if (side === "home") return game.model_prob_home_cover;
  if (side === "away") return game.model_prob_away_cover;
  return null;
}

function spreadMarketPct(game, side) {
  if (side === "home") return game.market_prob_home_cover;
  if (side === "away") return game.market_prob_away_cover;
  return null;
}

function modelCoverSide(game) {
  if (game.model_cover_side) return game.model_cover_side;
  const mhc = game.model_prob_home_cover;
  const mac = game.model_prob_away_cover;
  if (mhc == null || mac == null) return null;
  return mhc >= mac ? "home" : "away";
}

function modelCoverTeam(game) {
  if (game.model_cover_team) return game.model_cover_team;
  const side = modelCoverSide(game);
  if (side === "home") return game.home_team;
  if (side === "away") return game.away_team;
  return null;
}

function renderSlate(slate, edgeFraction = 0.08) {
  els.slateBody.innerHTML = "";
  if (!slate.length) {
    els.slateBody.innerHTML =
      '<tr><td colspan="20" class="empty">No games on slate</td></tr>';
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    if (game.ml_picks_disagree) tr.classList.add("pick-disagree-row");
    if (game.plus_ev_single || game.plus_ev_total || game.plus_ev_spread) {
      tr.classList.add("plus-ev");
    }
    const ou = game.ou_line != null ? game.ou_line : "—";
    const runs = fmtRuns(game.expected_total_runs);
    const pick = game.totals_pick || "—";
    const tEdge =
      game.total_edge != null ? `${(game.total_edge * 100).toFixed(1)}%` : "—";
    const modelConf = game.model_confidence || "—";
    const modelPickLabel =
      game.model_pick_action === "lean_only"
        ? `<span class="muted-label">${game.model_pick_team || "—"}</span>`
        : game.model_pick_team || "—";
    if (game.model_pick_action === "lean_only") tr.classList.add("lean-only-row");
    const mlConf = game.ml_confidence || "—";
    const totalsConf = game.totals_confidence || "—";
    const spreadPick = game.spread_best_pick;
    const runLine =
      game.home_spread_point != null
        ? fmtSpreadLine(
            game.home_team,
            game.home_spread_point,
            game.home_spread_american
          )
        : "—";
    const coverSide = modelCoverSide(game) || "home";
    const modelCoverTeamLabel = modelCoverTeam(game) || "—";
    const modelCover =
      spreadCoverPct(game, coverSide) != null
        ? pct(spreadCoverPct(game, coverSide))
        : "—";
    const marketCover =
      spreadMarketPct(game, coverSide) != null
        ? pct(spreadMarketPct(game, coverSide))
        : "—";
    const evSpreadLabel = spreadPick
      ? `${spreadPick.team} ${spreadPick.spread_point > 0 ? "+" : ""}${spreadPick.spread_point}`
      : "—";
    const spreadEdge =
      spreadPick != null
        ? `${(spreadPick.edge * 100).toFixed(1)}%`
        : "—";
    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td>${ou}</td>
      <td>${runs}</td>
      <td>${pick}</td>
      <td>${pct(game.model_prob_over)}</td>
      <td>${pct(game.market_prob_over)}</td>
      <td class="${game.total_edge != null && game.total_edge >= edgeFraction ? "edge-pos" : ""}">${tEdge}</td>
      <td class="${confidenceClass(totalsConf)}">${totalsConf}</td>
      <td class="model-winner-cell">${modelPickLabel}</td>
      <td>${pct(game.model_pick_prob)}</td>
      <td class="${modelConfidenceClass(modelConf)}">${modelConf}</td>
      <td>${pct(game.model_prob_home)}</td>
      <td>${pct(game.market_prob_home)}</td>
      <td class="${game.edge_home != null && game.edge_home >= edgeFraction ? "edge-pos" : ""}">
        ${game.edge_home != null ? pct(game.edge_home) : "—"}
      </td>
      <td class="${confidenceClass(mlConf)}">${mlConf}</td>
      <td>${runLine}</td>
      <td class="model-winner-cell">${modelCoverTeamLabel}</td>
      <td>${modelCover}</td>
      <td>${marketCover}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${evSpreadLabel}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${spreadEdge}</td>
    `;
    els.slateBody.appendChild(tr);
  }
}

function renderSingles(singles, edgeFraction = 0.08) {
  if (!singles.length) {
    els.singles.innerHTML =
      `<p class="empty">No singles met the ${edgePctLabel(edgeFraction)} edge threshold.</p>`;
    return;
  }
  els.singles.innerHTML = singles
    .map(
      (s) => `
    <div class="card">
      <div class="card-title">${s.team} <span class="edge-pos">+${(s.edge * 100).toFixed(1)}%</span></div>
      <div class="card-meta">${s.matchup} · ${s.side} · Model ${pct(s.model_prob)} · ${s.american_odds > 0 ? "+" : ""}${s.american_odds}</div>
    </div>`
    )
    .join("");
}

function renderTotals(totals, note, edgeFraction = 0.08) {
  if (els.totalsNote) {
    els.totalsNote.textContent =
      note || "Separate from moneyline model. Not betting advice.";
  }
  if (!totals.length) {
    els.totals.innerHTML =
      `<p class="empty">No totals met the ${edgePctLabel(edgeFraction)} edge threshold.</p>`;
    return;
  }
  els.totals.innerHTML = totals
    .map(
      (t) => `
    <div class="card">
      <div class="card-title">${t.pick} <span class="edge-pos">+${(t.edge * 100).toFixed(1)}%</span></div>
      <div class="card-meta">${t.matchup} · Line ${t.ou_line} · Model ${t.expected_total_runs} runs</div>
    </div>`
    )
    .join("");
}

function renderParlays(parlays, edgeFraction = 0.08) {
  if (!parlays.length) {
    els.parlays.innerHTML =
      `<p class="empty">No parlays met the ${edgePctLabel(edgeFraction)} EV threshold.</p>`;
    return;
  }
  els.parlays.innerHTML = parlays
    .map(
      (p, i) => `
    <div class="card">
      <div class="card-title">#${i + 1} · ${p.num_legs} legs · <span class="edge-pos">EV ${p.ev_pct || pct(p.ev)}</span></div>
      <div class="card-meta">Model joint ${pct(p.model_joint_prob)} · Market ${pct(p.market_joint_prob)} · Payout ${p.decimal_payout}x</div>
      <div class="card-legs">
        ${p.legs
          .map(
            (leg) =>
              `<div class="leg">${leg.team} (${leg.american_odds > 0 ? "+" : ""}${leg.american_odds})</div>`
          )
          .join("")}
      </div>
    </div>`
    )
    .join("");
}

async function loadClvSummary() {
  try {
    const res = await fetch("/api/clv/summary?days=30");
    if (!res.ok) return;
    const s = await res.json();
    if (!s.picks_logged) return;
    const pct =
      s.pct_positive_clv != null
        ? `${(s.pct_positive_clv * 100).toFixed(0)}% positive CLV`
        : "CLV pending";
    const line = document.createElement("span");
    line.textContent = `Forward CLV (30d): ${s.picks_logged} picks · ${pct}`;
    els.footer.appendChild(line);
  } catch (_err) {
    /* optional */
  }
}

function renderFooter(data) {
  const s = data.status || {};
  const oddsLabel =
    data.odds_source === "the_odds_api"
      ? "live (Odds API)"
      : data.odds_source === "historical_cache"
        ? "historical cache"
        : data.odds_source === "model_only"
          ? "model only (free)"
          : "none";
  els.footer.innerHTML = `
    <span>MLB games in DB: ${s.mlb_games_count ?? "—"}</span>
    <span>Market eval: ${s.market_eval_status ?? "—"}</span>
    <span>Parlays cached: ${s.parlay_count ?? "—"}</span>
    <span>Odds: ${oddsLabel}</span>
    <span>Mode: ${data.mode ?? "—"}</span>
    <span>Totals model: ${data.active_totals_model?.model_version ?? s.totals_model_version ?? "—"}</span>
    <span>Active ML: ${data.active_moneyline_model?.run_id ?? "—"}</span>
  `;
  els.footer.classList.remove("hidden");
}

async function loadBoard(refresh = false) {
  if (!boardMode) return;

  els.loading.classList.remove("hidden");
  els.loadingSpinner.classList.remove("hidden");
  els.content.classList.add("hidden");
  els.disclaimer.classList.add("hidden");
  els.error.textContent = "";
  els.loadingMessage.textContent = loadingHint();
  els.refresh.classList.remove("hidden");

  try {
    const res = await fetch(buildApiUrl(refresh));
    if (!res.ok) throw new Error(`API error ${res.status}`);
    const data = await res.json();

    const disclaimer = data.disclaimer || "";
    if (disclaimer) {
      els.disclaimer.textContent = disclaimer;
      els.disclaimer.classList.remove("hidden");
    } else {
      els.disclaimer.classList.add("hidden");
    }

    const mlModel = data.active_moneyline_model;
    const modelLabel = mlModel
      ? ` · ML: ${mlModel.run_id} (${mlModel.model_version || "—"})`
      : "";
    els.boardDate.textContent = `Board date: ${data.date} · ${data.mode === "demo" ? "Demo" : "Live"}${modelLabel}`;

    const syncNote =
      data.board_live_test && data.synced_to_main
        ? '<div class="warning-item sync-ok">Live test complete — odds repository and daily board updated. Main-site game pages will pick up new lines within ~60s.</div>'
        : "";
    const staleNote =
      data.prediction_freshness?.block_strong_picks
        ? '<div class="warning-item stale-picks">Strong model picks suppressed — refresh MLB ingest, pitcher log, or odds before trusting high-confidence picks.</div>'
        : "";
    els.warnings.innerHTML =
      syncNote +
      staleNote +
      (data.warnings || [])
        .map((w) => `<div class="warning-item">${w}</div>`)
        .join("");

    if (data.error) {
      els.error.textContent = data.error;
    }

    const edgeFraction =
      typeof data.edge_threshold === "number" ? data.edge_threshold : minEdgeFraction();
    updateThresholdLabels(edgeFraction);

    if (els.confidenceNote && data.confidence_disclaimer) {
      els.confidenceNote.textContent = data.confidence_disclaimer;
    }
    if (els.spreadNote && data.spread_disclaimer) {
      els.spreadNote.textContent = data.spread_disclaimer;
    }

    renderSimpleSlate(data.slate || []);
    renderModelPicks(data.slate || []);
    renderSlate(data.slate || [], edgeFraction);
    renderTotals(data.top_totals || [], data.totals_disclaimer, edgeFraction);
    renderSingles(data.top_singles || [], edgeFraction);
    renderParlays(data.top_parlays || [], edgeFraction);
    renderFooter(data);
    loadClvSummary();

    els.loading.classList.add("hidden");
    els.content.classList.remove("hidden");
  } catch (err) {
    els.loadingSpinner.classList.add("hidden");
    els.loadingMessage.textContent = "Click Run live or Demo to try again.";
    els.error.textContent = `Failed to load board: ${err.message}`;
  }
}

function renderBacktest(data) {
  const ml = data.moneyline || {};
  const tot = data.totals || {};
  const beats = ml.model_beats_market ? "yes" : "no";
  const range =
    data.start_date && data.end_date
      ? `${data.start_date} → ${data.end_date}`
      : "—";
  const note = data.error
    ? `<p class="accuracy-note">${data.error}</p>`
    : "";

  els.backtestPanel.innerHTML = `
    ${note}
    <p class="accuracy-meta">Generated ${data.generated_at || "—"} · ${data.games_in_window ?? 0} games · ${range}</p>
    <div class="accuracy-grid">
      <div class="accuracy-block">
        <h3>Moneyline</h3>
        <dl>
          <dt>Games w/ odds</dt><dd>${ml.games_with_odds ?? 0}</dd>
          <dt>Winner accuracy</dt><dd>${ml.winner_accuracy_pct ?? 0}%</dd>
          <dt>+EV picks (≥8%)</dt><dd>${ml.plus_ev_picks ?? 0}</dd>
          <dt>+EV accuracy</dt><dd>${ml.plus_ev_accuracy_pct ?? 0}%</dd>
          <dt>Log loss (model)</dt><dd>${ml.log_loss_model ?? "—"}</dd>
          <dt>Log loss (market)</dt><dd>${ml.log_loss_market ?? "—"}</dd>
          <dt>Beats market</dt><dd>${beats}</dd>
        </dl>
      </div>
      <div class="accuracy-block">
        <h3>Totals</h3>
        <dl>
          <dt>Games w/ O/U</dt><dd>${tot.games_with_ou_line ?? 0}</dd>
          <dt>O/U pick accuracy</dt><dd>${tot.ou_pick_accuracy_pct ?? 0}%</dd>
          <dt>+EV O/U picks</dt><dd>${tot.plus_ev_ou_picks ?? 0}</dd>
          <dt>+EV O/U accuracy</dt><dd>${tot.plus_ev_ou_accuracy_pct ?? 0}%</dd>
          <dt>Runs MAE</dt><dd>${tot.total_runs_mae ?? "—"}</dd>
          <dt>Runs bias</dt><dd>${tot.total_runs_bias ?? "—"}</dd>
        </dl>
      </div>
    </div>
  `;
  els.backtestPanel.classList.remove("hidden");
  els.backtestIdle.classList.add("hidden");
}

async function fetchBacktest(url, message) {
  els.backtestLoading.classList.remove("hidden");
  els.backtestLoadingMessage.textContent = message;
  els.backtestPanel.classList.add("hidden");
  els.error.textContent = "";

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Backtest API error ${res.status}`);
    const data = await res.json();
    renderBacktest(data);
  } catch (err) {
    els.error.textContent = `Backtest failed: ${err.message}`;
    els.backtestIdle.classList.remove("hidden");
  } finally {
    els.backtestLoading.classList.add("hidden");
  }
}

els.runLive.addEventListener("click", () => {
  boardMode = "live";
  loadBoard(true);
});

els.runDemo.addEventListener("click", () => {
  boardMode = "demo";
  loadBoard(false);
});

els.refresh.addEventListener("click", () => loadBoard(true));

els.loadSavedBacktest.addEventListener("click", () => {
  fetchBacktest("/api/backtest/saved", "Loading saved report…");
});

els.runBacktest.addEventListener("click", () => {
  fetchBacktest("/api/backtest?days=30", "Running 30-day backtest (may take ~15s)…");
});
