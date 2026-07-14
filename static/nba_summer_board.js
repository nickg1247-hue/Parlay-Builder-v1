/** NBA Summer League predictions board — market-implied leans. */

(function () {
  const loading = document.getElementById("board-loading");
  const errEl = document.getElementById("board-error");
  const wrap = document.getElementById("board-table-wrap");
  const body = document.getElementById("board-body");
  const dateEl = document.getElementById("board-date");
  const warningsEl = document.getElementById("board-warnings");
  const disclaimerEl = document.getElementById("board-disclaimer");
  const refreshBtn = document.getElementById("refresh-board");
  const dateParam = qs("date");

  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });

  function pct(p) {
    if (p == null) return "—";
    return `${(Number(p) * 100).toFixed(1)}%`;
  }

  function fmtOdds(v) {
    if (v == null) return "—";
    const n = Number(v);
    return n > 0 ? `+${n}` : String(n);
  }

  function render(data) {
    loading.classList.add("hidden");
    errEl.classList.add("hidden");
    dateEl.textContent = data.date || "";
    disclaimerEl.textContent = data.disclaimer || "";
    warningsEl.innerHTML = (data.warnings || [])
      .map((w) => `<div class="warning-item">${w}</div>`)
      .join("");

    const slate = data.slate || [];
    if (!slate.length) {
      wrap.classList.add("hidden");
      loading.classList.remove("hidden");
      loading.textContent = "No Summer League games on this date.";
      return;
    }

    wrap.classList.remove("hidden");
    body.innerHTML = slate
      .map((g) => {
        const href = `/nba-summer/game/${encodeURIComponent(g.game_id)}?date=${encodeURIComponent(data.date || "")}`;
        const spread =
          g.home_spread_point != null
            ? `${g.home_team?.split(" ").pop() || "Home"} ${g.home_spread_point > 0 ? "+" : ""}${g.home_spread_point}`
            : "—";
        const ou = g.ou_line != null ? g.ou_line : "—";
        return `<tr>
          <td><a href="${href}">${g.matchup || `${g.away_team} @ ${g.home_team}`}</a></td>
          <td>${g.model_pick_team || "—"}</td>
          <td>${pct(g.model_pick_prob)}</td>
          <td>${fmtOdds(g.home_ml)}</td>
          <td>${fmtOdds(g.away_ml)}</td>
          <td>${spread}</td>
          <td>${ou}</td>
        </tr>`;
      })
      .join("");
  }

  async function load(refresh) {
    loading.classList.remove("hidden");
    loading.textContent = "Loading board…";
    wrap.classList.add("hidden");
    errEl.classList.add("hidden");
    const params = new URLSearchParams();
    if (dateParam) params.set("date", dateParam);
    if (refresh) params.set("refresh", "true");
    const url = `/api/nba-summer/daily?${params}`;
    try {
      const data = await fetchJSON(url);
      render(data);
    } catch (e) {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = e.message || "Failed to load Summer League board";
    }
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => load(true));
  }
  load(false);
})();
