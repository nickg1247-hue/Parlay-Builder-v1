const DEMO_DATE = "2024-11-30";

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

function fmtSpread(point) {
  if (point == null) return "—";
  return point > 0 ? `+${point}` : `${point}`;
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

function buildApiUrl(refresh = false) {
  const url = new URL("/api/cfb/daily", window.location.origin);
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

function renderSlate(slate, edgeFraction = 0.08) {
  els.slateBody.innerHTML = "";
  const colSpan = 11;
  if (!slate.length) {
    els.slateBody.innerHTML =
      `<tr><td colspan="${colSpan}" class="empty">No games on slate</td></tr>`;
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    if (game.plus_ev_single) {
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
          : game.model_prob_away;
      bestPick = prob != null ? `${game.model_pick} (${pct(prob)})` : game.model_pick;
    }
    const evFlag = game.plus_ev_single ? "Yes" : "—";
    const spreadLine =
      game.home_spread_point != null
        ? `${game.home_team} ${fmtSpread(game.home_spread_point)}`
        : "—";
    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td>${pct(game.model_prob_home)}</td>
      <td>${pct(game.market_prob_home)}</td>
      <td class="${edge != null && edge >= edgeFraction ? "edge-pos" : ""}">${fmtEdge(edge)}</td>
      <td class="${confidenceClass(mlConf)}">${mlConf}</td>
      <td>${evFlag}</td>
      <td>${bestPick}</td>
      <td>${spreadLine}${game.spread_line_source === "proxy" ? " (proxy)" : ""}</td>
      <td>${game.spread_pick || "—"}</td>
      <td>${game.ou_line != null ? game.ou_line : "—"}</td>
      <td>${game.totals_pick || "—"}</td>
    `;
    els.slateBody.appendChild(tr);
  }
}

function renderSingles(slate, edgeFraction = 0.08) {
  const singles = slate.filter((g) => g.plus_ev_single && g.best_pick);
  if (!singles.length) {
    els.singles.innerHTML =
      `<p class="empty">No singles met the ${edgePctLabel(edgeFraction)} edge threshold.</p>`;
    return;
  }
  els.singles.innerHTML = singles
    .map((g) => {
      const bp = g.best_pick;
      return `
      <div class="card">
        <strong>${g.matchup}</strong>
        <p>${bp.team} ${fmtAmerican(bp.american_odds)} — edge ${fmtEdge(bp.edge)}</p>
      </div>`;
    })
    .join("");
}

function renderFooter(data) {
  const model = data.active_moneyline_model || {};
  const parts = [
    `Mode: ${data.mode || "—"}`,
    `Model: ${model.model_version || "—"}`,
    `Feature set: ${model.feature_set || "—"}`,
    `+EV singles: ${data.plus_ev_count ?? 0}`,
    `Games: ${(data.slate || []).length}`,
  ];
  els.footer.textContent = parts.join(" · ");
}

async function loadBoard(refresh = false) {
  const edgeFraction = minEdgeFraction();
  updateThresholdLabels(edgeFraction);
  els.error.textContent = "";
  els.warnings.innerHTML = "";
  els.disclaimer.classList.add("hidden");
  els.content.classList.add("hidden");
  els.footer.classList.add("hidden");
  els.loading.classList.remove("hidden");
  els.loadingSpinner.classList.remove("hidden");
  els.loadingMessage.textContent =
    boardMode === "demo" ? "Loading demo CFB board…" : "Loading CFB board…";

  try {
    const resp = await fetch(buildApiUrl(refresh));
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || resp.statusText);
    }

    els.loading.classList.add("hidden");
    els.loadingSpinner.classList.add("hidden");
    els.content.classList.remove("hidden");
    els.footer.classList.remove("hidden");
    els.refresh.classList.remove("hidden");

    if (data.disclaimer) {
      els.disclaimer.textContent = data.disclaimer;
      els.disclaimer.classList.remove("hidden");
    }
    if (data.message) {
      els.boardDate.textContent = data.message;
    } else {
      els.boardDate.textContent = `Slate date: ${data.date}`;
    }
    if (data.warnings?.length) {
      els.warnings.innerHTML = data.warnings.map((w) => `<p>${w}</p>`).join("");
    }
    if (data.error) {
      els.error.textContent = data.error;
    }

    renderSlate(data.slate || [], edgeFraction);
    renderSingles(data.slate || [], edgeFraction);
    renderFooter(data);
  } catch (err) {
    els.loading.classList.add("hidden");
    els.loadingSpinner.classList.add("hidden");
    els.error.textContent = err.message || String(err);
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
els.minEdgeInput?.addEventListener("change", () => {
  if (boardMode) loadBoard(false);
});

boardMode = "live";
loadBoard(false);