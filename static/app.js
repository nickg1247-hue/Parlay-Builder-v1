/** Shared helpers for ESPN-style shell (Phase A). */

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

function teamLogoUrl(teamId, sport = "mlb") {
  if (sport === "nba") {
    return "";
  }
  return `https://www.mlbstatic.com/team-logos/team-cap-on-dark/${teamId}.svg`;
}

function gameSport(game) {
  return game?.sport || "mlb";
}

function gameDetailHref(game) {
  const sport = gameSport(game);
  return `/${sport}/game/${game.game_id}`;
}

function logoForGame(game, side) {
  const isAway = side === "away";
  const direct = isAway ? game.away_logo_url : game.home_logo_url;
  if (direct) return direct;
  const teamId = isAway ? game.away_team_id : game.home_team_id;
  return teamLogoUrl(teamId, gameSport(game));
}

function formatLocalTime(isoUtc) {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatLocalTimeShort(isoUtc) {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatRefreshStatus(status) {
  if (!status || !status.ran_at) {
    return status?.error || "Not refreshed yet";
  }
  const when = formatLocalTime(status.ran_at);
  if (status.ok) {
    const games = status.games_on_slate != null ? ` · ${status.games_on_slate} games` : "";
    return `Updated ${when}${games}`;
  }
  return `Refresh failed ${when}`;
}

function isGameLive(status) {
  const s = (status || "").toLowerCase();
  return s === "live" || s === "in progress";
}

function isGameFinal(status) {
  const s = (status || "").toLowerCase();
  return s === "final" || s === "game over";
}

function shouldShowScores(game) {
  if (!game) return false;
  if (game.home_score != null || game.away_score != null) {
    return isGameLive(game.status) || isGameFinal(game.status);
  }
  return false;
}

function statusBadgeClass(status) {
  const s = (status || "").toLowerCase();
  if (isGameLive(status)) return "badge-live";
  if (isGameFinal(status)) return "badge-final";
  if (s === "preview" || s === "pre-game" || s === "scheduled") return "badge-scheduled";
  return "badge-default";
}

function statusLabel(status) {
  if (!status) return "Scheduled";
  const s = status.toLowerCase();
  if (s === "in progress" || s === "live") return "Live";
  if (s === "preview" || s === "pre-game") return "Scheduled";
  if (s === "final" || s === "game over") return "Final";
  return status;
}

function gameStatusText(game) {
  if (!game) return "Scheduled";
  if (isGameLive(game.status) && game.period_label) return game.period_label;
  return statusLabel(game.status);
}

function sortGamesByStart(games) {
  return (games || []).slice().sort((a, b) => {
    const ta = new Date(a.start_time_utc || 0).getTime();
    const tb = new Date(b.start_time_utc || 0).getTime();
    return ta - tb;
  });
}

function tickerItemHtml(game) {
  const away = game.away_score != null ? game.away_score : "";
  const home = game.home_score != null ? game.home_score : "";
  const score =
    away !== "" && home !== ""
      ? `<span class="ticker-score">${away}–${home}</span>`
      : "";
  const meta = gameStatusText(game);
  const shortAway = game.away_team.split(" ").pop();
  const shortHome = game.home_team.split(" ").pop();
  const sport = gameSport(game).toUpperCase();
  return `
    <a class="ticker-item" href="${gameDetailHref(game)}">
      <span class="ticker-sport">${sport}</span>
      <span class="ticker-teams">${shortAway} @ ${shortHome}</span>
      ${score}
      <span class="ticker-meta">${meta}</span>
    </a>
  `;
}

function renderLiveTicker(el, games) {
  if (!el) return;
  const sorted = sortGamesByStart(games);
  if (!sorted.length) {
    el.innerHTML = '<span class="ticker-empty">No games today</span>';
    return;
  }
  el.innerHTML = `<div class="ticker-track">${sorted.map(tickerItemHtml).join("")}</div>`;
}

function initLiveTicker(elementId, options = {}) {
  const el = document.getElementById(elementId);
  if (!el) return null;
  el.classList.remove("ticker-placeholder");
  el.classList.add("live-ticker");

  const intervalMs = options.intervalMs || 60000;
  const dateParam = options.date || qs("date");
  const sport = options.sport || "all";
  const base = `/api/scores/today?sport=${encodeURIComponent(sport)}`;
  const url = dateParam ? `${base}&date=${encodeURIComponent(dateParam)}` : base;

  let timer = null;

  async function refresh() {
    try {
      const data = await fetchJSON(url);
      renderLiveTicker(el, data.games);
    } catch {
      if (!el.querySelector(".ticker-track")) {
        el.innerHTML = '<span class="ticker-empty">Scores unavailable</span>';
      }
    }
  }

  refresh();
  timer = setInterval(refresh, intervalMs);
  return () => clearInterval(timer);
}

function gameCardHtml(game) {
  const showScores = shouldShowScores(game);
  return `
    <div class="game-card-top">
      <span>${isGameLive(game.status) && game.period_label ? game.period_label : formatLocalTimeShort(game.start_time_utc)}</span>
      <span class="status-badge ${statusBadgeClass(game.status)}">${gameStatusText(game)}</span>
    </div>
    <div class="game-card-matchup">
      <div class="team-side away">
        <img class="team-logo" src="${logoForGame(game, "away")}" alt="" width="40" height="40" loading="lazy">
        <span class="team-name">${game.away_team}</span>
        ${showScores ? `<span class="team-score">${game.away_score ?? 0}</span>` : ""}
      </div>
      <span class="game-card-at">@</span>
      <div class="team-side home">
        <img class="team-logo" src="${logoForGame(game, "home")}" alt="" width="40" height="40" loading="lazy">
        <span class="team-name">${game.home_team}</span>
        ${showScores ? `<span class="team-score">${game.home_score ?? 0}</span>` : ""}
      </div>
    </div>
  `;
}

function renderGameList(listEl, games) {
  if (!listEl) return;
  listEl.innerHTML = "";
  sortGamesByStart(games).forEach((game) => {
    const card = document.createElement("a");
    card.className = "game-card";
    card.href = gameDetailHref(game);
    card.dataset.gameId = game.game_id;
    card.innerHTML = gameCardHtml(game);
    listEl.appendChild(card);
  });
}

function updateGameCards(listEl, games) {
  if (!listEl) return;
  const byId = Object.fromEntries((games || []).map((g) => [String(g.game_id), g]));
  listEl.querySelectorAll(".game-card").forEach((card) => {
    const game = byId[card.dataset.gameId];
    if (game) card.innerHTML = gameCardHtml(game);
  });
}

function renderMatchupHeader(el, game) {
  if (!el || !game) return;
  el.innerHTML = matchupHeaderHtml(game);
}

function matchupHeaderHtml(game) {
  const showScores = shouldShowScores(game);
  const centerText = isGameLive(game.status) && game.period_label
    ? game.period_label
    : formatLocalTime(game.start_time_utc);
  return `
    <div class="matchup-grid">
      <div class="matchup-team">
        <img class="team-logo" src="${logoForGame(game, "away")}" alt="${game.away_team}" width="56" height="56">
        <h2>${game.away_team}</h2>
        ${showScores ? `<span class="matchup-score">${game.away_score ?? 0}</span>` : ""}
      </div>
      <div class="matchup-center">
        <span class="status-badge ${statusBadgeClass(game.status)}">${gameStatusText(game)}</span>
        <p class="matchup-time">${centerText}</p>
      </div>
      <div class="matchup-team">
        <img class="team-logo" src="${logoForGame(game, "home")}" alt="${game.home_team}" width="56" height="56">
        <h2>${game.home_team}</h2>
        ${showScores ? `<span class="matchup-score">${game.home_score ?? 0}</span>` : ""}
      </div>
    </div>
  `;
}

function gameIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const idx = parts.indexOf("game");
  if (idx >= 0 && parts[idx + 1]) return parts[idx + 1];
  return null;
}

function qs(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function imgLogo(teamId, alt) {
  const img = document.createElement("img");
  img.className = "team-logo";
  img.src = teamLogoUrl(teamId);
  img.alt = alt || "Team logo";
  img.loading = "lazy";
  img.width = 40;
  img.height = 40;
  return img;
}
