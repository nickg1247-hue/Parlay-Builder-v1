// Demo date: 2026-04-10 — 15 games on 2025-26 holdout regular-season slate.
// Ingest max date is 2026-06-05 (1 game); we use 2026-04-10 for a richer demo board.
const DEMO_DATE = "2026-04-10";

let boardMode = null;

const els = {
  loading: document.getElementById("loading"),
  loadingSpinner: document.getElementById("loading-spinner"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  warnings: document.getElementById("warnings"),
  error: document.getElementById("error"),
  boardDate: document.getElementById("board-date"),
  slateBody: document.querySelector("#slate-table tbody"),
  singles: document.getElementById("singles-list"),
  confidenceNote: document.getElementById("confidence-note"),
  spreadNote: document.getElementById("spread-note"),
  totalsNote: document.getElementById("totals-note"),
  totalsSection: document.getElementById("totals-section"),
  totalsList: document.getElementById("totals-list"),
  totalsThresholdLabel: document.getElementById("totals-threshold-label"),
  footer: document.getElementById("status-footer"),
  refresh: document.getElementById("refresh-btn"),
  runLive: document.getElementById("run-live-btn"),
  runDemo: document.getElementById("run-demo-btn"),
  minEdgeInput: document.getElementById("min-edge-input"),
  singlesThresholdLabel: document.getElementById("singles-threshold-label"),
  loadingMessage: document.getElementById("loading-message"),
};

function minEdgeFraction() {
  const pct = Number(els.minEdgeInput?.value ?? 8);
  if (!Number.isFinite(pct) || pct < 0) return 0.08;
  return pct / 100;
}

function edgePctLabel(fraction) {
  return `${Math.round(fraction * 1000) / 10}%`;
}

function updateThresholdLabels(edgeFraction) {
  const label = edgePctLabel(edgeFraction);
  if (els.singlesThresholdLabel) {
    els.singlesThresholdLabel.textContent = `(≥${label} edge)`;
  }
}

function loadingHint() {
  if (boardMode === "demo") {
    return "Loading demo board (cached odds, no API)…";
  }
  if (boardMode === "live") {
    return "Pulling live NBA odds… May take a moment if quota allows.";
  }
  return "Click Run live or Demo to load the board.";
}

function pct(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function fmtEdge(value) {
  if (value == null) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(1)}%`;
}

function fmtAmerican(odds) {
  if (odds == null) return "—";
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function fmtSpreadLine(team, point, american) {
  if (point == null) return "—";
  const pt = point > 0 ? `+${point}` : `${point}`;
  const odds = american != null ? ` (${fmtAmerican(american)})` : "";
  return `${team} ${pt}${odds}`;
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

function buildApiUrl(refresh = false) {
  const url = new URL("/api/nba/daily", window.location.origin);
  if (boardMode === "demo") {
    url.searchParams.set("date", DEMO_DATE);
    url.searchParams.set("use_cache", "true");
  }
  url.searchParams.set("min_edge", String(minEdgeFraction()));
  url.searchParams.set("skip_totals", "false");
  if (refresh) {
    url.searchParams.set("refresh", "true");
  }
  return url.toString();
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

function updateOddsColumnHeaders(oddsSource) {
  const marketHeader = document.querySelector("#slate-header-row th:nth-child(3)");
  const mktCoverHeaders = document.querySelectorAll("#slate-header-row .spread-col:nth-child(10), #slate-header-row .totals-col:nth-child(17)");
  const benchmark = oddsSource === "demo_benchmark";
  if (marketHeader) {
    marketHeader.textContent = benchmark ? "Bench P(home)" : "Market P(home)";
    marketHeader.title = benchmark
      ? "Fixed demo benchmark (54% home) — not the model and not a sportsbook"
      : "Implied home win probability from available lines";
  }
  mktCoverHeaders.forEach((th) => {
    if (benchmark && th.textContent.includes("mkt")) {
      th.textContent = th.textContent.replace("mkt", "bench");
    }
  });
}

function renderSlate(slate, edgeFraction = 0.08, spreadEnabled = false, totalsEnabled = false, evalMode = false) {
  els.slateBody.innerHTML = "";
  document.querySelectorAll(".spread-col").forEach((el) => {
    el.classList.toggle("hidden", !spreadEnabled);
  });
  document.querySelectorAll(".totals-col").forEach((el) => {
    el.classList.toggle("hidden", !totalsEnabled);
  });
  document.querySelectorAll(".eval-col").forEach((el) => {
    el.classList.toggle("hidden", !evalMode);
  });
  const showMl = slate.some((g) => g.ml_prob_home != null);
  document.querySelectorAll(".ml-col").forEach((el) => {
    el.classList.toggle("hidden", !showMl);
  });
  const colSpan = 7 + (showMl ? 1 : 0) + (spreadEnabled ? 5 : 0) + (totalsEnabled ? 6 : 0) + (evalMode ? 3 : 0);
  if (!slate.length) {
    els.slateBody.innerHTML =
      `<tr><td colspan="${colSpan}" class="empty">No games on slate</td></tr>`;
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    if (game.plus_ev_single || game.plus_ev_spread || game.plus_ev_total) {
      tr.classList.add("plus-ev");
    }
    const edge = game.ml_edge_best ?? game.edge_home;
    const mlConf = game.ml_confidence || "—";
    let bestPick = "—";
    if (game.best_pick) {
      const bp = game.best_pick;
      bestPick = `${bp.team} ${fmtAmerican(bp.american_odds)}`;
    } else if (game.model_pick) {
      const prob =
        game.model_pick_side === "home"
          ? game.model_prob_home
          : game.model_prob_away ?? (game.model_prob_home != null ? 1 - game.model_prob_home : null);
      bestPick =
        prob != null
          ? `${game.model_pick} (${pct(prob)} model)`
          : `${game.model_pick} (model)`;
    }
    const evFlag = game.plus_ev_single ? "Yes" : "—";
    const spreadPick = game.spread_best_pick;
    const runLine =
      spreadPick != null
        ? fmtSpreadLine(spreadPick.team, spreadPick.spread_point, spreadPick.american_odds)
        : game.home_spread_point != null
          ? fmtSpreadLine(game.home_team, game.home_spread_point, game.home_spread_american)
          : "—";
    const coverSide = spreadPick ? spreadPick.side : "home";
    const modelCover =
      spreadCoverPct(game, coverSide) != null
        ? pct(spreadCoverPct(game, coverSide))
        : "—";
    const marketCover =
      spreadMarketPct(game, coverSide) != null
        ? pct(spreadMarketPct(game, coverSide))
        : "—";
    const spreadPickLabel = spreadPick
      ? `${spreadPick.team} ${spreadPick.spread_point > 0 ? "+" : ""}${spreadPick.spread_point}`
      : game.model_margin != null
        ? `${game.model_margin >= 0 ? game.home_team : game.away_team} by ${Math.abs(game.model_margin).toFixed(1)}`
        : "—";
    const spreadEdge =
      spreadPick != null ? `${(spreadPick.edge * 100).toFixed(1)}%` : "—";
    const ou = game.ou_line != null ? game.ou_line : "—";
    const estPts = game.expected_total_pts != null ? game.expected_total_pts : "—";
    const totalsPick = game.totals_pick || "—";
    const modelOver =
      game.model_prob_over != null ? pct(game.model_prob_over) : "—";
    const marketOver =
      game.market_prob_over != null ? pct(game.market_prob_over) : "—";
    const totalEdge =
      game.total_edge != null ? `${(game.total_edge * 100).toFixed(1)}%` : "—";
    const spreadCells = spreadEnabled
      ? `
      <td>${runLine}</td>
      <td>${modelCover}</td>
      <td>${marketCover}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${spreadPickLabel}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${spreadEdge}</td>`
      : "";
    const totalsCells = totalsEnabled
      ? `
      <td>${ou}</td>
      <td>${estPts}</td>
      <td class="${game.plus_ev_total ? "edge-pos" : ""}">${totalsPick}</td>
      <td>${modelOver}</td>
      <td>${marketOver}</td>
      <td class="${game.plus_ev_total ? "edge-pos" : ""}">${totalEdge}</td>`
      : "";
    const evalCells = evalMode
      ? `
      <td>${game.actual_total_pts != null ? game.actual_total_pts : "—"}</td>
      <td>${game.model_ml_correct === true ? "✓" : game.model_ml_correct === false ? "✗" : "—"}</td>
      <td>${game.model_ou_correct === true ? "✓" : game.model_ou_correct === false ? "✗" : "—"}</td>`
      : "";
    tr.innerHTML = `
      <td>${game.matchup}${game.is_summer ? ' <span class="badge-summer" title="Summer League">Summer</span>' : ""}</td>
      <td>${pct(game.model_prob_home)}</td>
      ${showMl ? `<td class="ml-col">${pct(game.ml_prob_home)}</td>` : ""}
      <td>${pct(game.market_prob_home)}</td>
      <td class="${edge != null && edge >= edgeFraction ? "edge-pos" : ""}">${fmtEdge(edge)}</td>
      <td class="${confidenceClass(mlConf)}">${mlConf}</td>
      <td>${evFlag}</td>
      <td>${bestPick}</td>
      ${spreadCells}
      ${totalsCells}
      ${evalCells}
    `;
    els.slateBody.appendChild(tr);
  }
}

function topSinglesFromSlate(slate) {
  return slate
    .filter((g) => g.plus_ev_single && g.best_pick)
    .map((g) => ({
      matchup: g.matchup,
      team: g.best_pick.team,
      side: g.best_pick.side,
      edge: g.best_pick.edge,
      american_odds: g.best_pick.american_odds,
      model_prob:
        g.best_pick.side === "home"
          ? g.model_prob_home
          : 1 - g.model_prob_home,
    }));
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
      <div class="card-meta">${s.matchup} · ${s.side} · Model ${pct(s.model_prob)} · ${fmtAmerican(s.american_odds)}</div>
    </div>`
    )
    .join("");
}

function renderTotals(totals, note, edgeFraction = 0.08) {
  if (els.totalsNote) {
    els.totalsNote.textContent = note || "";
    els.totalsNote.classList.toggle("hidden", !note);
  }
  if (els.totalsSection) {
    els.totalsSection.classList.toggle("hidden", !totals || !totals.length);
  }
  if (!els.totalsList) return;
  if (!totals || !totals.length) {
    els.totalsList.innerHTML = "";
    return;
  }
  els.totalsList.innerHTML = totals
    .map(
      (t) => `
    <div class="card">
      <div class="card-title">${t.pick} <span class="edge-pos">+${(t.edge * 100).toFixed(1)}%</span></div>
      <div class="card-meta">${t.matchup} · Line ${t.ou_line} · Model ${t.expected_total_pts ?? "—"} pts</div>
    </div>`
    )
    .join("");
}

function renderFooter(data) {
    const oddsLabel =
    data.odds_source === "demo_benchmark"
      ? "demo (benchmark market)"
      : data.odds_source === "demo_synthetic"
      ? "demo (model-synthetic)"
      : data.odds_source === "the_odds_api" || data.odds_source === "the_odds_api_live"
      ? "live (Odds API)"
      : data.odds_source === "historical_cache"
        ? "historical cache"
        : data.odds_source === "repository" || data.odds_source === "repository_stale"
          ? "repository"
          : data.odds_source === "none"
            ? "none (model only)"
            : data.odds_source || "—";
  els.footer.innerHTML = `
    <span>Games with odds: ${data.games_with_odds ?? 0}</span>
    <span>+EV singles: ${data.plus_ev_count ?? 0}</span>
    <span>CLV logged: ${data.clv_logged_count ?? 0}</span>
    <span>Odds: ${oddsLabel}</span>
    <span>Mode: ${data.mode ?? "—"}</span>
    <span>betting_ready: ${data.betting_ready === true ? "true" : "false"}</span>
    <span>Weighted model: ${data.active_custom_model?.model_id ?? data.prediction_model ?? "custom_weighted"}</span>
    <span>ML baseline: ${data.active_moneyline_model?.model_version ?? data.active_moneyline_model?.run_id ?? "—"}</span>
    <span>Spread model: ${data.active_margin_model?.model_version ?? "—"} · production_ready: ${data.board_spread_enabled === true ? "true" : "false"}</span>
    <span>Totals model: ${data.active_totals_model?.model_version ?? "—"} · production_ready: ${data.board_totals_enabled === true ? "true" : "false"}</span>
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
      ? ` · ML: ${mlModel.model_version || mlModel.run_id || "—"}`
      : "";
    els.boardDate.textContent = `Board date: ${data.date} · ${data.mode === "demo" ? "Demo" : "Live"}${modelLabel}`;

    const messageHtml = data.message
      ? `<div class="warning-item">${data.message}</div>`
      : "";
    els.warnings.innerHTML =
      messageHtml +
      (data.warnings || [])
        .map((w) => `<div class="warning-item">${w}</div>`)
        .join("");

    if (data.error) {
      els.error.textContent = data.error;
    }

    const edgeFraction =
      typeof data.edge_threshold === "number" ? data.edge_threshold : minEdgeFraction();
    updateThresholdLabels(edgeFraction);

    if (els.spreadNote && data.spread_disclaimer) {
      els.spreadNote.textContent = data.spread_disclaimer;
      els.spreadNote.classList.remove("hidden");
    } else if (els.spreadNote) {
      els.spreadNote.classList.add("hidden");
    }

    if (els.totalsThresholdLabel) {
      els.totalsThresholdLabel.textContent = `(≥${edgePctLabel(edgeFraction)} edge, experimental)`;
    }

    updateOddsColumnHeaders(data.odds_source);

    renderSlate(
      data.slate || [],
      edgeFraction,
      data.board_spread_enabled === true,
      data.board_totals_enabled === true,
      data.board_eval_mode === true
    );
    renderTotals(
      data.top_totals || [],
      data.totals_disclaimer,
      edgeFraction
    );
    renderSingles(topSinglesFromSlate(data.slate || []), edgeFraction);
    renderFooter(data);

    els.loading.classList.add("hidden");
    els.content.classList.remove("hidden");
  } catch (err) {
    els.loadingSpinner.classList.add("hidden");
    els.loadingMessage.textContent = "Click Run live or Demo to try again.";
    els.error.textContent = `Failed to load board: ${err.message}`;
  }
}

els.runLive?.addEventListener("click", () => {
  boardMode = "live";
  loadBoard(true);
});

els.runDemo?.addEventListener("click", () => {
  boardMode = "demo";
  loadBoard(false);
});

els.refresh?.addEventListener("click", () => {
  loadBoard(true);
});

els.minEdgeInput?.addEventListener("change", () => {
  if (boardMode) loadBoard(false);
});

// Load today's live board immediately — matches MLB/ESPN-style open-on-arrival.
boardMode = "live";
loadBoard(false);