const DEMO_DATE = "2024-11-30";

let boardMode = "live";
let boardData = null;

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
  parlays: document.getElementById("parlays-list"),
  footer: document.getElementById("status-footer"),
  refresh: document.getElementById("refresh-btn"),
  runLive: document.getElementById("run-live-btn"),
  runDemo: document.getElementById("run-demo-btn"),
  minEdgeInput: document.getElementById("min-edge-input"),
  singlesThresholdLabel: document.getElementById("singles-threshold-label"),
  loadingMessage: document.getElementById("loading-message"),
  teamFilter: document.getElementById("team-filter"),
  divisionFilter: document.getElementById("division-filter"),
  conferenceFilter: document.getElementById("conference-filter"),
  rankingFilter: document.getElementById("ranking-filter"),
  filterCount: document.getElementById("filter-count"),
};

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function minEdgeFraction() {
  const pctValue = Number(els.minEdgeInput?.value ?? 8);
  if (!Number.isFinite(pctValue) || pctValue < 0) return 0.08;
  return pctValue / 100;
}

function edgePctLabel(fraction) {
  return String(Math.round(fraction * 1000) / 10) + "%";
}

function updateThresholdLabels(edgeFraction) {
  if (els.singlesThresholdLabel) {
    els.singlesThresholdLabel.textContent = "(≥" + edgePctLabel(edgeFraction) + " edge)";
  }
}

function pct(value) {
  if (value == null) return "—";
  return (value * 100).toFixed(1) + "%";
}

function fmtEdge(value) {
  if (value == null) return "—";
  const sign = value >= 0 ? "+" : "";
  return sign + (value * 100).toFixed(1) + "%";
}

function fmtAmerican(odds) {
  if (odds == null) return "—";
  return odds > 0 ? "+" + odds : String(odds);
}

function fmtSpread(point) {
  if (point == null) return "—";
  return point > 0 ? "+" + point : String(point);
}

function confidenceClass(label) {
  switch (label) {
    case "Toss-up":
    case "Low":
      return "conf-low";
    case "Soft":
    case "Medium":
      return "conf-medium";
    case "Hard":
    case "High":
      return "conf-high";
    case "Lock":
    case "Extremely high":
      return "conf-extreme";
    default:
      return "";
  }
}

function buildApiUrl(refresh = false) {
  const url = new URL("/api/cfb/daily", window.location.origin);
  if (boardMode === "demo") {
    url.searchParams.set("date", DEMO_DATE);
    url.searchParams.set("use_cache", "true");
  }
  url.searchParams.set("min_edge", String(minEdgeFraction()));
  if (refresh) url.searchParams.set("refresh", "true");
  return url.toString();
}

function setSelectOptions(select, options, allLabel) {
  if (!select) return;
  const previous = select.value || "all";
  select.innerHTML =
    '<option value="all">' + escapeHtml(allLabel) + "</option>" +
    options.map((option) => {
      const key = typeof option === "string" ? option : option.key;
      const label = typeof option === "string" ? option : option.label;
      return '<option value="' + escapeHtml(key) + '">' + escapeHtml(label) + "</option>";
    }).join("");
  const values = Array.from(select.options).map((option) => option.value);
  select.value = values.includes(previous) ? previous : "all";
}

function populateFilters(filters) {
  setSelectOptions(els.teamFilter, filters?.teams || [], "All teams");
  setSelectOptions(els.divisionFilter, filters?.divisions || [], "All divisions");
  setSelectOptions(els.conferenceFilter, filters?.conferences || [], "All conferences");
}

function top25Rank(value) {
  const rank = Number(value);
  return Number.isInteger(rank) && rank >= 1 && rank <= 25;
}

function passesFilters(game) {
  const team = els.teamFilter?.value || "all";
  if (team !== "all" && game.home_team !== team && game.away_team !== team) return false;

  const division = els.divisionFilter?.value || "all";
  const divisions = Array.isArray(game.divisions) && game.divisions.length
    ? game.divisions.map(String)
    : [String(game.division || "fbs")];
  if (division !== "all" && !divisions.includes(division)) return false;

  const conference = els.conferenceFilter?.value || "all";
  if (
    conference !== "all" &&
    String(game.home_conference || "") !== conference &&
    String(game.away_conference || "") !== conference
  ) {
    return false;
  }

  const ranking = els.rankingFilter?.value || "all";
  const homeRanked = top25Rank(game.home_rank);
  const awayRanked = top25Rank(game.away_rank);
  if (ranking === "top25" && !homeRanked && !awayRanked) return false;
  if (ranking === "ranked-v-ranked" && !(homeRanked && awayRanked)) return false;

  return true;
}

function visibleSlate() {
  return (boardData?.slate || []).filter(passesFilters);
}

function rankedTeam(game, side) {
  const team = game[side + "_team"] || "";
  const rank = game[side + "_rank"];
  return (top25Rank(rank) ? "#" + rank + " " : "") + team;
}

function matchupMarkup(game) {
  const away = escapeHtml(rankedTeam(game, "away"));
  const home = escapeHtml(rankedTeam(game, "home"));
  const title = away + " @ " + home;
  const linkedTitle = game.game_id && game.prediction_available
    ? '<a href="/cfb/game/' + encodeURIComponent(game.game_id) + '">' + title + "</a>"
    : title;

  const meta = [];
  if (game.division_label) meta.push('<span class="meta-chip">' + escapeHtml(game.division_label) + "</span>");
  if (game.experimental) meta.push('<span class="meta-chip">' + escapeHtml(game.public_tier_label ? "FCS Beta · " + game.public_tier_label : game.tier_status_label || "FCS Beta · directional") + '</span>');
  const conferences = Array.from(new Set([game.away_conference, game.home_conference].filter(Boolean)));
  conferences.forEach((conference) => {
    meta.push('<span class="meta-chip">' + escapeHtml(conference) + "</span>");
  });
  if (game.network) meta.push('<span class="meta-chip">' + escapeHtml(game.network) + "</span>");

  return '<div class="matchup-title">' + linkedTitle + '</div><div class="game-meta">' + meta.join("") + "</div>";
}

function renderSlate(slate, edgeFraction = 0.08) {
  els.slateBody.innerHTML = "";
  const colSpan = 11;
  if (!slate.length) {
    els.slateBody.innerHTML =
      '<tr><td colspan="' + colSpan + '" class="empty">No games match these filters.</td></tr>';
    return;
  }

  for (const game of slate) {
    const tr = document.createElement("tr");
    const predictionAvailable = game.prediction_available !== false;
    if (!predictionAvailable) tr.classList.add("coverage-only");
    if (game.plus_ev_single) tr.classList.add("plus-ev");

    const edge = predictionAvailable ? (game.ml_edge_best ?? game.edge_home) : null;
    const mlConf = predictionAvailable
      ? (game.model_family === "fcs_moneyline" ? (game.public_tier_label || game.tier_status_label || "FCS Beta") : (game.model_category_label || game.ml_confidence || "—"))
      : (game.model_eligible ? "Unavailable" : "Schedule only");

    let bestPick = "—";
    if (predictionAvailable && game.best_pick) {
      bestPick = escapeHtml(game.best_pick.team) + " " + fmtAmerican(game.best_pick.american_odds);
    } else if (predictionAvailable && game.model_pick) {
      const probability = game.model_pick_side === "home"
        ? game.model_prob_home
        : game.model_prob_away;
      bestPick = escapeHtml(game.model_pick) + (probability != null ? " (" + pct(probability) + ")" : "");
    }

    const spreadLine = predictionAvailable && game.home_spread_point != null
      ? escapeHtml(game.home_team) + " " + fmtSpread(game.home_spread_point)
      : "—";

    tr.innerHTML =
      '<td class="matchup-cell">' + matchupMarkup(game) + "</td>" +
      "<td>" + (predictionAvailable ? pct(game.model_prob_home) : "—") + "</td>" +
      "<td>" + (predictionAvailable ? pct(game.market_prob_home) : "—") + "</td>" +
      '<td class="' + (edge != null && edge >= edgeFraction ? "edge-pos" : "") + '">' + fmtEdge(edge) + "</td>" +
      '<td class="' + confidenceClass(mlConf) + '">' + escapeHtml(mlConf) + "</td>" +
      "<td>" + (predictionAvailable && game.plus_ev_single ? "Yes" : "—") + "</td>" +
      "<td>" + bestPick + "</td>" +
      "<td>" + spreadLine + (predictionAvailable && game.spread_line_source === "proxy" ? " (proxy)" : "") + "</td>" +
      "<td>" + (predictionAvailable ? escapeHtml(game.spread_pick || "—") : "—") + "</td>" +
      "<td>" + (predictionAvailable && game.ou_line != null ? escapeHtml(game.ou_line) : "—") + "</td>" +
      "<td>" + (predictionAvailable ? escapeHtml(game.totals_pick || "—") : "—") + "</td>";
    els.slateBody.appendChild(tr);
  }
}

function renderSingles(slate, edgeFraction = 0.08) {
  const singles = slate.filter((game) => game.plus_ev_single && game.best_pick);
  if (!singles.length) {
    els.singles.innerHTML =
      '<p class="empty">No visible singles met the ' + edgePctLabel(edgeFraction) + " edge threshold.</p>";
    return;
  }
  els.singles.innerHTML = singles.map((game) => {
    const pick = game.best_pick;
    return '<div class="card"><strong>' + escapeHtml(game.matchup) + "</strong><p>" +
      escapeHtml(pick.team) + " " + fmtAmerican(pick.american_odds) +
      " — edge " + fmtEdge(pick.edge) + "</p></div>";
  }).join("");
}

function renderParlays(parlays) {
  if (!els.parlays) return;
  if (!parlays?.length) {
    els.parlays.innerHTML = '<p class="empty">No visible cross-game parlays met the edge threshold.</p>';
    return;
  }
  els.parlays.innerHTML = parlays.map((parlay) => {
    const legs = (parlay.legs || []).map((leg) =>
      escapeHtml(leg.team || leg.side) + " (" + escapeHtml(leg.matchup || "") + ")"
    ).join(" · ");
    return '<div class="card"><strong>' + escapeHtml(parlay.num_legs) +
      "-leg parlay</strong> — EV " + escapeHtml(parlay.ev_pct || fmtEdge(parlay.ev)) +
      "<p>" + legs + "</p></div>";
  }).join("");
}

function visibleParlays(parlays, slate) {
  const visibleIds = new Set(slate.map((game) => String(game.game_id)));
  const filtersActive =
    (els.teamFilter?.value || "all") !== "all" ||
    (els.divisionFilter?.value || "all") !== "all" ||
    (els.conferenceFilter?.value || "all") !== "all" ||
    (els.rankingFilter?.value || "all") !== "all";
  if (!filtersActive) return parlays || [];
  return (parlays || []).filter((parlay) =>
    (parlay.legs || []).length > 0 &&
    parlay.legs.every((leg) => leg.game_id != null && visibleIds.has(String(leg.game_id)))
  );
}

function renderFooter(data, visibleCount) {
  const model = data.active_moneyline_model || {};
  const total = (data.slate || []).length;
  const parts = [
    "Mode: " + (data.mode || "—"),
    "Model: " + (model.model_version || "—"),
    "Feature set: " + (model.feature_set || "—"),
    "+EV singles: " + (data.plus_ev_count ?? 0),
    "Games: " + visibleCount + " of " + total,
  ];
  els.footer.textContent = parts.join(" · ");
}

function renderFilteredBoard() {
  if (!boardData) return;
  const edgeFraction = minEdgeFraction();
  const slate = visibleSlate();
  renderSlate(slate, edgeFraction);
  renderSingles(slate, edgeFraction);
  renderParlays(visibleParlays(boardData.parlays || [], slate));
  renderFooter(boardData, slate.length);
  if (els.filterCount) {
    els.filterCount.textContent = slate.length + " of " + (boardData.slate || []).length + " games";
  }
}

async function loadBoard(refresh = false) {
  const edgeFraction = minEdgeFraction();
  updateThresholdLabels(edgeFraction);
  els.error.textContent = "";
  els.error.classList.add("hidden");
  els.warnings.innerHTML = "";
  els.disclaimer.classList.add("hidden");
  els.content.classList.add("hidden");
  els.footer.classList.add("hidden");
  els.loading.classList.remove("hidden");
  els.loadingSpinner.classList.remove("hidden");
  els.loadingMessage.textContent =
    boardMode === "demo" ? "Loading demo CFB board…" : "Loading CFB board…";

  try {
    const response = await fetch(buildApiUrl(refresh));
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);

    boardData = data;
    populateFilters(data.filters || {});
    els.loading.classList.add("hidden");
    els.loadingSpinner.classList.add("hidden");
    els.content.classList.remove("hidden");
    els.footer.classList.remove("hidden");
    els.refresh.classList.remove("hidden");

    if (data.disclaimer) {
      els.disclaimer.textContent = data.disclaimer;
      els.disclaimer.classList.remove("hidden");
    }
    els.boardDate.textContent = data.message || ("Slate date: " + data.date);
    if (data.warnings?.length) {
      els.warnings.innerHTML = data.warnings.map((warning) =>
        "<p>" + escapeHtml(warning) + "</p>"
      ).join("");
    }
    if (data.error) {
      els.error.textContent = data.error;
      els.error.classList.remove("hidden");
    }

    renderFilteredBoard();
  } catch (error) {
    boardData = null;
    els.loading.classList.add("hidden");
    els.loadingSpinner.classList.add("hidden");
    els.error.textContent = error.message || String(error);
    els.error.classList.remove("hidden");
  }
}

els.runLive?.addEventListener("click", () => {
  boardMode = "live";
  loadBoard(false);
});
els.runDemo?.addEventListener("click", () => {
  boardMode = "demo";
  loadBoard(false);
});
els.refresh?.addEventListener("click", () => loadBoard(true));
els.minEdgeInput?.addEventListener("change", () => loadBoard(false));
[
  els.teamFilter,
  els.divisionFilter,
  els.conferenceFilter,
  els.rankingFilter,
].forEach((control) => control?.addEventListener("change", renderFilteredBoard));

loadBoard(false);
