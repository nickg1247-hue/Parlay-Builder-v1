const params = new URLSearchParams(window.location.search);
const demoDate = params.get("date");
const useCache = params.get("use_cache") === "true";

const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  warnings: document.getElementById("warnings"),
  error: document.getElementById("error"),
  boardDate: document.getElementById("board-date"),
  simpleBody: document.querySelector("#simple-table tbody"),
  slateBody: document.querySelector("#slate-table tbody"),
  singles: document.getElementById("singles-list"),
  parlays: document.getElementById("parlays-list"),
  totals: document.getElementById("totals-list"),
  totalsNote: document.getElementById("totals-note"),
  footer: document.getElementById("status-footer"),
  refresh: document.getElementById("refresh-btn"),
};

function pct(value) {
  if (value == null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function buildApiUrl(refresh = false) {
  const url = new URL("/api/daily", window.location.origin);
  if (demoDate) url.searchParams.set("date", demoDate);
  if (useCache) url.searchParams.set("use_cache", "true");
  if (refresh) url.searchParams.set("refresh", "true");
  return url.toString();
}

function winPct(probHome, isHome) {
  const pct = (isHome ? probHome : 1 - probHome) * 100;
  return `${pct.toFixed(1)}%`;
}

function renderSimpleSlate(slate, meta) {
  els.simpleBody.innerHTML = "";
  const note = document.getElementById("simple-note");
  if (note) {
    note.textContent =
      meta?.display_note ||
      "50% model + 50% market when odds available; model-only otherwise. Not betting advice.";
  }
  if (!slate.length) {
    els.simpleBody.innerHTML =
      '<tr><td colspan="4" class="empty">No games on slate</td></tr>';
    return;
  }
  for (const game of slate) {
    const probHome = game.display_prob_home ?? game.model_prob_home;
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

function renderSlate(slate) {
  els.slateBody.innerHTML = "";
  if (!slate.length) {
    els.slateBody.innerHTML =
      '<tr><td colspan="8" class="empty">No games on slate</td></tr>';
    return;
  }
  for (const game of slate) {
    const tr = document.createElement("tr");
    if (game.plus_ev_single || game.plus_ev_total) tr.classList.add("plus-ev");
    const ou = game.ou_line != null ? game.ou_line : "—";
    const runs = game.expected_total_runs != null ? game.expected_total_runs : "—";
    const pick = game.totals_pick || "—";
    const tEdge =
      game.total_edge != null ? `${(game.total_edge * 100).toFixed(1)}%` : "—";
    tr.innerHTML = `
      <td>${game.matchup}</td>
      <td>${ou}</td>
      <td>${runs}</td>
      <td>${pick}</td>
      <td class="${game.total_edge != null && game.total_edge >= 0.08 ? "edge-pos" : ""}">${tEdge}</td>
      <td>${pct(game.model_prob_home)}</td>
      <td>${pct(game.market_prob_home)}</td>
      <td class="${game.edge_home != null && game.edge_home >= 0.08 ? "edge-pos" : ""}">
        ${game.edge_home != null ? pct(game.edge_home) : "—"}
      </td>
    `;
    els.slateBody.appendChild(tr);
  }
}

function renderSingles(singles) {
  if (!singles.length) {
    els.singles.innerHTML =
      '<p class="empty">No singles met the 8% edge threshold.</p>';
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

function renderTotals(totals, note) {
  if (els.totalsNote) {
    els.totalsNote.textContent =
      note || "Separate from moneyline model. Not betting advice.";
  }
  if (!totals.length) {
    els.totals.innerHTML =
      '<p class="empty">No totals met the 8% edge threshold.</p>';
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

function renderParlays(parlays) {
  if (!parlays.length) {
    els.parlays.innerHTML =
      '<p class="empty">No parlays met the 8% EV threshold.</p>';
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

function renderFooter(data) {
  const s = data.status || {};
  const oddsLabel =
    data.odds_source === "the_odds_api"
      ? "live (Odds API)"
      : data.odds_source === "historical_cache"
        ? "historical cache"
        : "none";
  els.footer.innerHTML = `
    <span>MLB games in DB: ${s.mlb_games_count ?? "—"}</span>
    <span>Market eval: ${s.market_eval_status ?? "—"}</span>
    <span>Parlays cached: ${s.parlay_count ?? "—"}</span>
    <span>Odds: ${oddsLabel}</span>
    <span>Mode: ${data.mode ?? "—"}</span>
    <span>Totals model: ${s.totals_model_version ?? "—"}</span>
  `;
}

async function loadBoard(refresh = false) {
  els.loading.classList.remove("hidden");
  els.content.classList.add("hidden");
  els.error.textContent = "";

  try {
    const res = await fetch(buildApiUrl(refresh));
    if (!res.ok) throw new Error(`API error ${res.status}`);
    const data = await res.json();

    els.disclaimer.textContent = data.disclaimer || "";
    els.boardDate.textContent = `Board date: ${data.date} · ${data.mode === "demo" ? "Demo" : "Live"}`;

    els.warnings.innerHTML = (data.warnings || [])
      .map((w) => `<div class="warning-item">${w}</div>`)
      .join("");

    if (data.error) {
      els.error.textContent = data.error;
    }

    renderSimpleSlate(data.slate || [], {
      display_note: data.display_note,
    });
    renderSlate(data.slate || []);
    renderTotals(data.top_totals || [], data.totals_disclaimer);
    renderSingles(data.top_singles || []);
    renderParlays(data.top_parlays || []);
    renderFooter(data);

    els.loading.classList.add("hidden");
    els.content.classList.remove("hidden");
  } catch (err) {
    els.loading.classList.add("hidden");
    els.error.textContent = `Failed to load board: ${err.message}`;
  }
}

els.refresh.addEventListener("click", () => loadBoard(true));
loadBoard(false);
