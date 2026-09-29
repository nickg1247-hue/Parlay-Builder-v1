/** NHL game detail — winner, puck line, and total from the score model. */
(function () {
  const loading = document.getElementById("game-loading");
  const errEl = document.getElementById("game-error");
  const content = document.getElementById("game-content");
  const parts = window.location.pathname.split("/").filter(Boolean);
  const gameIdx = parts.indexOf("game");
  const gameId = gameIdx >= 0 ? parts[gameIdx + 1] : null;
  const dateParam = typeof qs === "function" ? qs("date") : "";

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pct(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${Math.round(n * 100)}%`;
  }

  function odds(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return n > 0 ? `+${n}` : String(n);
  }

  if (!gameId) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Missing game id in URL";
    return;
  }

  if (typeof initLiveTicker === "function") {
    initLiveTicker("live-ticker", { date: dateParam, sport: "nhl" });
  }

  const params = new URLSearchParams();
  if (dateParam) params.set("date", dateParam);
  const q = params.toString();
  const url = `/api/games/nhl/${encodeURIComponent(gameId)}${q ? `?${q}` : ""}`;

  fetchJSON(url)
    .then((data) => {
      const game = data.game || {};
      const pred = data.prediction || {};
      loading.classList.add("hidden");
      if (!pred.model_pick) {
        errEl.classList.remove("hidden");
        errEl.textContent = "This game is on the slate, but the score model did not return a pick.";
        return;
      }
      const lines = (pred.scorelines || [])
        .map(
          (row) =>
            `<div class="nhl-scoreline ntg-card"><strong>${esc(game.away_team_abbr || "Away")} ${row.away_goals} – ${row.home_goals} ${esc(game.home_team_abbr || "Home")}</strong><div class="dash-page-kicker">${pct(row.prob)} · ${esc(row.period)}</div></div>`
        )
        .join("");
      const drivers = (pred.drivers || [])
        .map(
          (row) =>
            `<tr><th>${esc(row.label)}</th><td>${esc(row.value)}</td></tr>`
        )
        .join("");
      const score =
        game.status === "Final" || game.status === "Live"
          ? `${game.away_score ?? 0}–${game.home_score ?? 0}`
          : "";
      content.innerHTML = `
        <header class="matchup-header">
          <p class="dash-page-kicker">${esc(game.away_team)} @ ${esc(game.home_team)}${score ? ` · ${esc(score)} ${esc(game.status)}` : ""}</p>
          <h1>${esc(pred.model_pick)} to win · ${pct(pred.model_pick_side === "home" ? pred.model_prob_home : pred.model_prob_away)}</h1>
          <p class="dash-page-kicker">${esc(pred.model_name)}. ${esc(pred.ratings_note || "")}</p>
        </header>
        <div class="nhl-markets">
          <section class="nhl-market ntg-card">
            <h2>Game winner</h2>
            <p class="nhl-pick">${esc(pred.model_pick)}</p>
            <p>${esc(game.away_team_abbr)} ${pct(pred.model_prob_away)} · ${esc(game.home_team_abbr)} ${pct(pred.model_prob_home)}</p>
            <p class="dash-page-kicker">Confidence ${esc(pred.model_confidence)}. Market ${odds(pred.away_ml)} / ${odds(pred.home_ml)}.</p>
          </section>
          <section class="nhl-market ntg-card">
            <h2>Puck line</h2>
            <p class="nhl-pick">${esc(pred.spread_pick)}</p>
            <p>Home cover ${pct(pred.model_prob_home_cover)} · Away cover ${pct(pred.model_prob_away_cover)}</p>
            <p class="dash-page-kicker">Line ${esc(pred.home_spread_point)} (${esc(pred.spread_line_source)}). A −1.5 only cashes on a win by 2 or more. Overtime winners do not cover −1.5.</p>
          </section>
          <section class="nhl-market ntg-card">
            <h2>Over / under</h2>
            <p class="nhl-pick">${esc(pred.totals_pick)}</p>
            <p>Over ${pct(pred.model_prob_over)} · Under ${pct(pred.model_prob_under)}</p>
            <p class="dash-page-kicker">Expected final total ${esc(pred.expected_total_goals)} goals. Line ${esc(pred.ou_line)} (${esc(pred.ou_line_source)}).</p>
          </section>
        </div>
        <section class="dash-panel">
          <h2>Most likely scores</h2>
          <p class="dash-page-kicker">Regulation goals are independent Poisson draws from each team’s expected goals. Tied games are scored as a one-goal overtime or shootout.</p>
          <div class="nhl-scorelines">${lines}</div>
        </section>
        <details class="feature-table-wrap" open>
          <summary class="app-section-title">How the score was built</summary>
          <table class="feature-table"><tbody>${drivers}</tbody></table>
        </details>
        <p class="disclaimer-small">Experimental analytics — not betting advice. Goalie confirmations are not in this model yet.</p>`;
      content.classList.remove("hidden");
      document.title = `${game.away_team || "Away"} @ ${game.home_team || "Home"} — NTG Sports`;
    })
    .catch((err) => {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = err.message || "Failed to load this game";
    });
})();
