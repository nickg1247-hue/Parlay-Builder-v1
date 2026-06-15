const DEMO_DATE = "2025-08-15";
const MIN_EDGE = 0.08;

const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
  error: document.getElementById("error"),
  warnings: document.getElementById("warnings"),
  boardDate: document.getElementById("board-date"),
  pickStats: document.getElementById("pick-stats"),
  pickBody: document.querySelector("#pick-table tbody"),
  slateBody: document.querySelector("#slate-table tbody"),
  singles: document.getElementById("singles-list"),
  footer: document.getElementById("status-footer"),
  singlesLabel: document.getElementById("singles-label"),
};

function pct(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function buildDemoUrl() {
  const url = new URL("/api/daily", window.location.origin);
  url.searchParams.set("date", DEMO_DATE);
  url.searchParams.set("use_cache", "true");
  url.searchParams.set("skip_totals", "false");
  url.searchParams.set("min_edge", String(MIN_EDGE));
  return url.toString();
}

function renderPickComparison(slate) {
  els.pickBody.innerHTML = "";
  if (!slate.length) {
    els.pickBody.innerHTML =
      '<tr><td colspan="6" class="empty">No games on demo slate</td></tr>';
    return;
  }

  let agree = 0;
  let disagree = 0;
  let evCount = 0;

  for (const game of slate) {
    const tr = document.createElement("tr");
    const hasEv = Boolean(game.ev_pick_team);
    if (hasEv) {
      evCount += 1;
      if (game.ml_picks_disagree) disagree += 1;
      else agree += 1;
    }
    if (game.ml_picks_disagree) tr.classList.add("pick-disagree-row");
    if (hasEv && !game.ml_picks_disagree) tr.classList.add("pick-agree-row");

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
      <td class="model-winner-cell">${game.model_pick_team}</td>
      <td>${pct(game.model_pick_prob)}</td>
      <td class="${hasEv ? "ev-pick-cell" : ""}">${evLabel}</td>
      <td>${edgeLabel}</td>
      <td>${agreeLabel}</td>
    `;
    els.pickBody.appendChild(tr);
  }

  els.pickStats.innerHTML = `
    <div class="pick-stat">
      <span class="pick-stat-value">${slate.length}</span>
      <span class="pick-stat-label">Games</span>
    </div>
    <div class="pick-stat">
      <span class="pick-stat-value">${evCount}</span>
      <span class="pick-stat-label">+EV picks (≥8%)</span>
    </div>
    <div class="pick-stat">
      <span class="pick-stat-value">${agree}</span>
      <span class="pick-stat-label">Model &amp; +EV agree</span>
    </div>
    <div class="pick-stat">
      <span class="pick-stat-value">${disagree}</span>
      <span class="pick-stat-label">Disagree</span>
    </div>
  `;
}

function renderSlateSummary(slate) {
  els.slateBody.innerHTML = "";
  for (const game of slate) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td>${pct(game.model_prob_home)}</td>
      <td>${pct(game.market_prob_home)}</td>
      <td>${game.edge_home != null ? pct(game.edge_home) : "—"}</td>
      <td>${game.ou_line != null ? game.ou_line : "—"}</td>
      <td>${game.expected_total_runs != null ? Number(game.expected_total_runs).toFixed(1) : "—"}</td>
    `;
    els.slateBody.appendChild(tr);
  }
}

function renderSingles(singles) {
  if (!singles.length) {
    els.singles.innerHTML =
      '<p class="empty">No singles met the 8% edge threshold on this demo date.</p>';
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

function renderFooter(data) {
  const s = data.status || {};
  els.footer.innerHTML = `
    <span>Demo date: ${data.date}</span>
    <span>Odds: ${data.odds_source ?? "—"}</span>
    <span>Games: ${data.games_on_slate ?? "—"}</span>
    <span>ML model: ${data.active_moneyline_model?.run_id ?? "—"}</span>
  `;
  els.footer.classList.remove("hidden");
}

async function loadDemoBoard() {
  els.loading.classList.remove("hidden");
  els.content.classList.add("hidden");
  els.error.textContent = "";

  try {
    const res = await fetch(buildDemoUrl());
    if (!res.ok) throw new Error(`API error ${res.status}`);
    const data = await res.json();

    els.boardDate.textContent = `Demo board · ${data.date} · cached historical odds`;

    els.warnings.innerHTML = (data.warnings || [])
      .map((w) => `<div class="warning-item">${w}</div>`)
      .join("");

    if (data.error) {
      els.error.textContent = data.error;
    }

    const slate = data.slate || [];
    renderPickComparison(slate);
    renderSlateSummary(slate);
    renderSingles(data.top_singles || []);
    renderFooter(data);

    if (els.singlesLabel) {
      els.singlesLabel.textContent = `(≥${(MIN_EDGE * 100).toFixed(0)}% edge — same as +EV column)`;
    }

    els.loading.classList.add("hidden");
    els.content.classList.remove("hidden");
  } catch (err) {
    els.loading.classList.add("hidden");
    els.error.textContent = `Failed to load demo: ${err.message}`;
  }
}

loadDemoBoard();
