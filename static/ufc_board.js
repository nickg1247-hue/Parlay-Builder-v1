const DEMO_DATE = "2024-01-13";

let boardMode = null;
let boardCardDate = null;

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
  const url = new URL("/api/ufc/daily", window.location.origin);
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

function fightHref(fight) {
  const fid = fight.fight_id || fight.game_id;
  if (!fid) return "#";
  const params = new URLSearchParams();
  if (boardCardDate) params.set("date", boardCardDate);
  if (boardMode === "demo") params.set("use_cache", "true");
  const q = params.toString();
  return `/ufc/game/${encodeURIComponent(fid)}${q ? `?${q}` : ""}`;
}

function renderSlate(slate, edgeFraction = 0.08) {
  els.slateBody.innerHTML = "";
  const colSpan = 8;
  if (!slate.length) {
    els.slateBody.innerHTML =
      `<tr><td colspan="${colSpan}" class="empty">No fights on card</td></tr>`;
    return;
  }
  for (const fight of slate) {
    const tr = document.createElement("tr");
    if (fight.plus_ev_single) {
      tr.classList.add("plus-ev");
    }
    const edge = fight.ml_edge_best ?? fight.edge_home;
    const mlConf = fight.ml_confidence || "—";
    let bestPick = "—";
    if (fight.best_pick) {
      const bp = fight.best_pick;
      bestPick = `${bp.fighter} ${fmtAmerican(bp.american_odds)}`;
    } else if (fight.model_pick) {
      const prob =
        fight.model_pick_side === "home"
          ? fight.model_prob_home
          : fight.model_prob_away;
      bestPick = prob != null ? `${fight.model_pick} (${pct(prob)})` : fight.model_pick;
    }
    const evFlag = fight.plus_ev_single ? "Yes" : "—";
    const matchupLink = `<a href="${fightHref(fight)}">${fight.matchup}</a>`;
    tr.innerHTML = `
      <td>${matchupLink}</td>
      <td>${fight.weight_class || "—"}</td>
      <td>${pct(fight.model_prob_home)}</td>
      <td>${pct(fight.market_prob_home)}</td>
      <td class="${edge != null && edge >= edgeFraction ? "edge-pos" : ""}">${fmtEdge(edge)}</td>
      <td class="${confidenceClass(mlConf)}">${mlConf}</td>
      <td>${evFlag}</td>
      <td>${bestPick}</td>
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
      const href = fightHref(g);
      return `
      <div class="card">
        <strong><a href="${href}">${g.matchup}</a></strong>
        <p>${bp.fighter} ${fmtAmerican(bp.american_odds)} — edge ${fmtEdge(bp.edge)}</p>
      </div>`;
    })
    .join("");
}

async function loadMarketEval() {
  const section = document.getElementById("market-eval-section");
  const panel = document.getElementById("market-eval-panel");
  if (!section || !panel) return;
  try {
    const metrics = await fetch("/api/ufc/market").then((r) => r.json());
    section.classList.remove("hidden");
    if (metrics.status === "no_odds") {
      panel.innerHTML =
        "<p class=\"empty\">No holdout odds imported. Run <code>scripts/bootstrap_ufc.py</code> or import CSV.</p>";
      return;
    }
    const roi =
      metrics.paper_trade_roi != null
        ? `${(metrics.paper_trade_roi * 100).toFixed(1)}%`
        : "—";
    panel.innerHTML = `
      <p>Matched <strong>${metrics.matched_games}</strong> / ${metrics.holdout_games} holdout fights
        (${metrics.match_rate_pct ?? 0}%)</p>
      <p>Log loss — model: <strong>${metrics.log_loss_model ?? "—"}</strong>,
        market: <strong>${metrics.log_loss_market ?? "—"}</strong></p>
      <p>+EV picks: ${metrics.plus_ev_picks ?? 0} · Paper ROI: <strong>${roi}</strong></p>
      <p class="simple-note"><a href="/api/ufc/market?refresh=true">Refresh eval</a></p>`;
  } catch (_) {
    section.classList.add("hidden");
  }
}

function renderParlays(parlays) {
  const el = document.getElementById("parlays-list");
  if (!el) return;
  if (!parlays?.length) {
    el.innerHTML = "<p class=\"empty\">No cross-fight parlays met the edge threshold.</p>";
    return;
  }
  el.innerHTML = parlays
    .map((p) => {
      const legs = (p.legs || [])
        .map((leg) => {
          const fid = leg.game_id;
          const href = fid
            ? `/ufc/game/${encodeURIComponent(fid)}${boardCardDate ? `?date=${boardCardDate}` : ""}`
            : "#";
          const label = leg.matchup || leg.team;
          return `<li><a href="${href}">${label}</a>: ${leg.team} ${fmtAmerican(leg.american_odds)}</li>`;
        })
        .join("");
      return `
      <div class="card">
        <strong>${p.num_legs}-leg parlay</strong> — EV ${p.ev_pct || fmtEdge(p.ev)}
        <ul>${legs}</ul>
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
    `Fights: ${(data.slate || []).length}`,
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
    boardMode === "demo" ? "Loading demo UFC board…" : "Loading UFC board…";

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
      els.boardDate.textContent = `Card date: ${data.date}`;
    }
    if (data.warnings?.length) {
      els.warnings.innerHTML = data.warnings.map((w) => `<p>${w}</p>`).join("");
    }
    if (data.error) {
      els.error.textContent = data.error;
    }

    boardCardDate = data.date || null;

    renderSlate(data.slate || [], edgeFraction);
    renderSingles(data.slate || [], edgeFraction);
    renderParlays(data.top_parlays || []);
    renderFooter(data);
    loadMarketEval();
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
