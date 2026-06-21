(function () {
  const grid = document.getElementById("teams-grid");
  const searchInput = document.getElementById("team-search");
  const tabsEl = document.getElementById("sport-tabs");
  const followedSection = document.getElementById("followed-teams-section");
  const followedList = document.getElementById("followed-teams-list");
  const playersSection = document.getElementById("followed-players-section");
  const playersFeed = document.getElementById("followed-players-feed");
  const alertSection = document.getElementById("alert-prefs-section");
  const digestToggle = document.getElementById("daily-digest-toggle");
  const FOLLOWS_KEY = "ntg_team_follows";
  const PLAYER_FOLLOWS_KEY = "ntg_player_follows";
  let activeSport = "mlb";
  let searchTimer = null;
  let serverFollows = [];

  initLiveTicker("live-ticker", { sport: "all", intervalMs: 45000 });

  const sports = [
    { id: "mlb", label: "MLB" },
    { id: "nba", label: "NBA" },
    { id: "cfb", label: "CFB" },
  ];

  function localFollows() {
    try {
      return JSON.parse(localStorage.getItem(FOLLOWS_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function saveLocalFollows(list) {
    localStorage.setItem(FOLLOWS_KEY, JSON.stringify(list));
  }

  function isFollowed(sport, teamId) {
    const key = `${sport}:${teamId}`;
    if (serverFollows.some((f) => `${f.sport}:${f.team_id}` === key)) return true;
    return localFollows().some((f) => `${f.sport}:${f.team_id}` === key);
  }

  function renderTabs() {
    tabsEl.replaceChildren();
    sports.forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "my-team-tab" + (s.id === activeSport ? " active" : "");
      btn.textContent = s.label;
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", s.id === activeSport ? "true" : "false");
      btn.onclick = () => {
        activeSport = s.id;
        renderTabs();
        loadTeams();
      };
      tabsEl.appendChild(btn);
    });
  }

  function teamCardHtml(team) {
    const logo = team.logo_url
      ? `<img class="team-card-logo" src="${team.logo_url}" alt="" width="48" height="48" loading="lazy">`
      : `<span class="team-card-logo team-card-logo-fallback">${(team.short_name || "?").slice(0, 3)}</span>`;
    const starred = isFollowed(team.sport, team.id);
    return `
      <div class="team-card-wrap">
        <a class="team-card" href="/teams/${team.sport}/${encodeURIComponent(team.id)}">
          ${logo}
          <span class="team-card-name">${team.name}</span>
          <span class="team-card-sport">${team.sport.toUpperCase()}</span>
        </a>
        <button type="button" class="team-follow-btn${starred ? " team-follow-btn--active" : ""}" data-follow-sport="${team.sport}" data-follow-id="${team.id}" aria-label="Follow ${team.name}">${starred ? "★" : "☆"}</button>
      </div>`;
  }

  async function toggleFollow(sport, teamId, btn) {
    const list = localFollows();
    const key = `${sport}:${teamId}`;
    const exists = list.some((f) => `${f.sport}:${f.team_id}` === key);
    if (exists) {
      saveLocalFollows(list.filter((f) => `${f.sport}:${f.team_id}` !== key));
    } else {
      list.push({ sport, team_id: teamId });
      saveLocalFollows(list);
    }
    if (window.pbUserAuth?.signed_in) {
      try {
        if (exists) {
          await fetch(`/api/user/teams/follow?sport=${encodeURIComponent(sport)}&team_id=${encodeURIComponent(teamId)}`, { method: "DELETE" });
        } else {
          await fetch("/api/user/teams/follow", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sport, team_id: teamId }),
          });
        }
        await loadServerFollows();
      } catch (_) {}
    }
    btn.classList.toggle("team-follow-btn--active", !exists);
    btn.textContent = exists ? "☆" : "★";
    renderFollowedTeams();
  }

  function renderPlayerFeed(feed) {
    const players = feed?.players || [];
    if (!players.length) {
      playersSection?.classList.add("hidden");
      return;
    }
    playersSection?.classList.remove("hidden");
    playersFeed.innerHTML = players
      .map((p) => {
        const ng = p.next_game;
        const gameBlock = ng?.game_id
          ? `<p class="player-feed-game"><a href="/preview/mlb/${encodeURIComponent(ng.game_id)}">${ng.matchup || "Matchup preview"}</a> · ${ng.status || "Scheduled"} · <a class="btn-ghost btn-ghost-sm" href="/mlb/game/${encodeURIComponent(ng.game_id)}">Game</a></p>`
          : `<p class="player-feed-game text-muted">No upcoming game today</p>`;
        const news = (p.news || [])
          .slice(0, 3)
          .map(
            (n) =>
              `<li><a href="${n.link}" target="_blank" rel="noopener">${n.title}</a><span class="player-feed-news-src">${n.source || ""}</span></li>`
          )
          .join("");
        return `
        <article class="player-feed-card ntg-card">
          <header class="player-feed-head">
            <button type="button" class="player-feed-name" data-player-sport="${p.sport}" data-player-id="${p.player_id}" data-player-name="${p.player_name}">${p.player_name}</button>
            <button type="button" class="player-unwatch-btn" data-unwatch-sport="${p.sport}" data-unwatch-id="${p.player_id}" aria-label="Unwatch">×</button>
          </header>
          ${gameBlock}
          ${news ? `<ul class="player-feed-news">${news}</ul>` : `<p class="text-muted">No recent headlines mentioning this player.</p>`}
        </article>`;
      })
      .join("");

    playersFeed.querySelectorAll(".player-feed-name").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (typeof openPlayerProfileModal === "function") {
          openPlayerProfileModal(btn.dataset.playerSport || "mlb", btn.dataset.playerId, btn.dataset.playerName);
      btn.addEventListener("click", () => unwatchPlayer(btn.dataset.unwatchSport, btn.dataset.unwatchId));
    });
  }

  async function unwatchPlayer(sport, playerId) {
    if (window.pbUserAuth?.signed_in) {
      try {
        await fetch(
          `/api/user/players/follow?sport=${encodeURIComponent(sport)}&player_id=${encodeURIComponent(playerId)}`,
          { method: "DELETE" }
        );
      } catch (_) {}
    }
    const list = JSON.parse(localStorage.getItem(PLAYER_FOLLOWS_KEY) || "[]").filter(
      (f) => !(f.sport === sport && f.player_id === playerId)
    );
    localStorage.setItem(PLAYER_FOLLOWS_KEY, JSON.stringify(list));
    loadPlayerFeed();
  }

  async function loadPlayerFeed() {
    if (window.pbUserAuth?.signed_in) {
      try {
        const data = await fetchJSON("/api/user/players/feed");
        renderPlayerFeed(data);
        return;
      } catch (_) {}
    }
    const local = JSON.parse(localStorage.getItem(PLAYER_FOLLOWS_KEY) || "[]");
    if (!local.length) {
      playersSection?.classList.add("hidden");
      return;
    }
    renderPlayerFeed({
      players: local.map((p) => ({
        ...p,
        next_game: null,
        news: [],
      })),
    });
  }

  window.ntgWatchPlayer = async function (sport, playerId, playerName, teamId) {
    const entry = { sport, player_id: playerId, player_name: playerName, team_id: teamId || null };
    const list = JSON.parse(localStorage.getItem(PLAYER_FOLLOWS_KEY) || "[]");
    if (!list.some((f) => f.sport === sport && f.player_id === playerId)) {
      list.push(entry);
      localStorage.setItem(PLAYER_FOLLOWS_KEY, JSON.stringify(list));
    }
    if (window.pbUserAuth?.signed_in) {
      try {
        await fetch("/api/user/players/follow", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(entry),
        });
      } catch (_) {}
    }
  };

  async function loadServerFollows() {
    if (!window.pbUserAuth?.signed_in) return;
    try {
      const data = await fetchJSON("/api/user/teams/follows");
      serverFollows = data.follows || [];
      renderFollowedTeams();
    } catch (_) {}
  }

  function renderFollowedTeams() {
    const merged = [...localFollows()];
    serverFollows.forEach((f) => {
      if (!merged.some((m) => m.sport === f.sport && m.team_id === f.team_id)) merged.push(f);
    });
    if (!merged.length) {
      followedSection?.classList.add("hidden");
      return;
    }
    followedSection?.classList.remove("hidden");
    followedList.innerHTML = merged
      .map(
        (f) =>
          `<a class="team-card ntg-card" href="/teams/${f.sport}/${encodeURIComponent(f.team_id)}">${f.sport.toUpperCase()} · Team ${f.team_id}</a>`
      )
      .join("");
  }

  async function loadTeams() {
    grid.innerHTML = `<div class="skeleton-card" style="height: 5rem; grid-column: 1 / -1;"></div>`;
    const q = (searchInput?.value || "").trim();
    const params = new URLSearchParams({ sport: activeSport });
    if (q) params.set("q", q);
    try {
      const data = await fetchJSON(`/api/teams?${params}`);
      const teams = data.teams || [];
      if (!teams.length) {
        grid.innerHTML = `<div class="empty-state-card">${emptyStateIcon("no-games")}<p>${q ? "No teams match your search." : "No teams found."}</p></div>`;
        return;
      }
      grid.innerHTML = teams.map(teamCardHtml).join("");
      grid.querySelectorAll(".team-follow-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          toggleFollow(btn.dataset.followSport, btn.dataset.followId, btn);
        });
      });
    } catch {
      brandedErrorState(grid, {
        title: "Teams unavailable",
        message: "Could not load the team directory.",
        onRetry: loadTeams,
      });
    }
  }

  async function loadAlertPrefs() {
    if (!window.pbUserAuth?.signed_in) return;
    alertSection?.classList.remove("hidden");
    try {
      const prefs = await fetchJSON("/api/user/alerts");
      if (digestToggle) digestToggle.checked = Boolean(prefs.daily_digest);
    } catch (_) {}
  }

  digestToggle?.addEventListener("change", async () => {
    if (!window.pbUserAuth?.signed_in) {
      digestToggle.checked = false;
      window.location.href = "/signin?next=/my-team";
      return;
    }
    try {
      await fetch("/api/user/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ daily_digest: digestToggle.checked, digest_hour_et: 8 }),
      });
    } catch (_) {}
  });

  searchInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadTeams, 250);
  });

  loadPublicFeatures().then(async () => {
    initSiteChrome();
    renderTabs();
    renderFollowedTeams();
    await loadServerFollows();
    await loadPlayerFeed();
    await loadAlertPrefs();
    loadTeams();
  });
})();
