/** NBA game detail — schedule + live scores only (Phase D). */

(function () {
  const loading = document.getElementById("game-loading");
  const errEl = document.getElementById("game-error");
  const content = document.getElementById("game-content");
  const header = document.getElementById("matchup-header");

  const parts = window.location.pathname.split("/").filter(Boolean);
  const gameIdx = parts.indexOf("game");
  const gameId = gameIdx >= 0 ? parts[gameIdx + 1] : null;

  if (!gameId) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Missing game id in URL";
    return;
  }

  const dateParam = qs("date");
  initLiveTicker("live-ticker", { date: dateParam, sport: "all" });

  const detailUrl = dateParam
    ? `/api/games/nba/${encodeURIComponent(gameId)}?date=${encodeURIComponent(dateParam)}`
    : `/api/games/nba/${encodeURIComponent(gameId)}`;

  const scoresUrl = dateParam
    ? `/api/scores/today?sport=nba&date=${encodeURIComponent(dateParam)}`
    : "/api/scores/today?sport=nba";

  async function refreshHeader(game) {
    const liveGame = { ...game, sport: "nba" };
    renderMatchupHeader(header, liveGame);
  }

  async function loadGame() {
    const data = await fetchJSON(detailUrl);
    loading.classList.add("hidden");
    content.classList.remove("hidden");
    await refreshHeader(data.game);

    if (!dateParam) {
      setInterval(async () => {
        try {
          const live = await fetchJSON(scoresUrl);
          const row = (live.games || []).find(
            (g) => String(g.game_id) === String(gameId)
          );
          if (row) await refreshHeader(row);
        } catch (_) {
          /* keep last good header */
        }
      }, 60000);
    }
  }

  loadTeamColors()
    .then(() => loadGame())
    .catch((e) => {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = e.message || "Game not found";
    });
})();
