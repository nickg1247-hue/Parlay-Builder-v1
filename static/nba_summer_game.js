/** NBA Summer League game page — market lean display. */

(function () {
  const loading = document.getElementById("game-loading");
  const errEl = document.getElementById("game-error");
  const content = document.getElementById("game-content");
  const header = document.getElementById("matchup-header");
  const boardEl = document.getElementById("game-matchup-board");
  const warningsEl = document.getElementById("insights-warnings");
  const disclaimerEl = document.getElementById("game-disclaimer");
  const refreshLink = document.getElementById("insights-refresh");

  const parts = location.pathname.split("/").filter(Boolean);
  const gameId = parts[parts.length - 1];
  const dateParam = qs("date");

  if (!gameId) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Missing game id";
    return;
  }

  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });

  function fmtOdds(v) {
    if (v == null) return "—";
    const n = Number(v);
    return n > 0 ? `+${n}` : String(n);
  }

  function render(data) {
    loading.classList.add("hidden");
    content.classList.remove("hidden");
    const g = data.game || {};
    const row = data.board_row || {};
    const model = data.model || {};

    header.innerHTML = `
      <p class="matchup-sub">${g.series_summary || "NBA Summer League"}</p>
      <h1>${g.away_team || "Away"} @ ${g.home_team || "Home"}</h1>
      <p class="matchup-meta">${g.status || ""} ${g.period_label ? "· " + g.period_label : ""}</p>
    `;

    warningsEl.innerHTML = (data.warnings || [])
      .map((w) => `<div class="warning-item">${w}</div>`)
      .join("");
    disclaimerEl.textContent = data.disclaimer || "";

    const winPct = model.win_pct != null ? `${model.win_pct}%` : "—";
    boardEl.innerHTML = `
      <div class="team-market-col away">
        <p class="team-market-name">${g.away_team || "Away"}</p>
        <p>ML ${fmtOdds(row.away_ml)}</p>
        <p>Score ${g.away_score != null ? g.away_score : "—"}</p>
      </div>
      <div class="model-center-col ntg-card">
        <p class="model-center-label">Market lean</p>
        <p class="model-pick">${model.pick || "—"}</p>
        <p class="model-win">${winPct} implied</p>
        <p class="model-edge">${model.note || "Sportsbook-implied favorite"}</p>
        <p>O/U ${row.ou_line != null ? row.ou_line : "—"}</p>
      </div>
      <div class="team-market-col home">
        <p class="team-market-name">${g.home_team || "Home"}</p>
        <p>ML ${fmtOdds(row.home_ml)}</p>
        <p>Score ${g.home_score != null ? g.home_score : "—"}</p>
      </div>
    `;
  }

  async function load(refresh) {
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    if (refresh) params.set("refresh", "true");
    const url = `/api/games/nba-summer/${encodeURIComponent(gameId)}/insights?${params}`;
    try {
      const data = await fetchJSON(url);
      render(data);
    } catch (e) {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = e.message || "Failed to load game";
    }
  }

  if (refreshLink) {
    refreshLink.addEventListener("click", (e) => {
      e.preventDefault();
      load(true);
    });
  }
  load(false);
})();
