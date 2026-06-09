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

function renderSlate(slate, edgeFraction = 0.08, spreadEnabled = false) {
  els.slateBody.innerHTML = "";
  document.querySelectorAll(".spread-col").forEach((el) => {
    el.classList.toggle("hidden", !spreadEnabled);
  });
  const colSpan = spreadEnabled ? 12 : 7;
  if (!slate.length) {
    els.slateBody.innerHTML =
      `<tr><td colspan="${colSpan}" class="empty">No games on slate</td></tr>`;
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    if (game.plus_ev_single || game.plus_ev_spread) {
      tr.classList.add("plus-ev");
    }
    const edge = game.ml_edge_best ?? game.edge_home;
    const mlConf = game.ml_confidence || "—";
    let bestPick = "—";
    if (game.best_pick) {
      const bp = game.best_pick;
      bestPick = `${bp.team} ${fmtAmerican(bp.american_odds)}`;
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
      : "—";
    const spreadEdge =
      spreadPick != null ? `${(spreadPick.edge * 100).toFixed(1)}%` : "—";
    const spreadCells = spreadEnabled
      ? `
      <td>${runLine}</td>
      <td>${modelCover}</td>
      <td>${marketCover}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${spreadPickLabel}</td>
      <td class="${game.plus_ev_spread ? "edge-pos" : ""}">${spreadEdge}</td>`
      : "";
    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td>${pct(game.model_prob_home)}</td>
      <td>${pct(game.market_prob_home)}</td>
      <td class="${edge != null && edge >= edgeFraction ? "edge-pos" : ""}">${fmtEdge(edge)}</td>
      <td class="${confidenceClass(mlConf)}">${mlConf}</td>
      <td>${evFlag}</td>
      <td>${bestPick}</td>
      ${spreadCells}
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

function renderFooter(data) {
  const oddsLabel =
    data.odds_source === "the_odds_api" || data.odds_source === "the_odds_api_live"
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
    <span>ML model: ${data.active_moneyline_model?.model_version ?? data.active_moneyline_model?.run_id ?? "—"}</span>
    <span>Spread model: ${data.active_margin_model?.model_version ?? "—"} · production_ready: ${data.board_spread_enabled === true ? "true" : "false"}</span>
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

    renderSlate(data.slate || [], edgeFraction, data.board_spread_enabled === true);
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
