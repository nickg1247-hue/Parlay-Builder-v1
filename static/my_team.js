(function () {
  const grid = document.getElementById("teams-grid");
  const searchInput = document.getElementById("team-search");
  const tabsEl = document.getElementById("sport-tabs");
  let activeSport = "mlb";
  let searchTimer = null;

  initLiveTicker("live-ticker", { sport: "all", intervalMs: 45000 });

  const sports = [
    { id: "mlb", label: "MLB" },
    { id: "nba", label: "NBA" },
    { id: "cfb", label: "CFB" },
  ];

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
    return `
      <a class="team-card" href="/teams/${team.sport}/${encodeURIComponent(team.id)}">
        ${logo}
        <span class="team-card-name">${team.name}</span>
        <span class="team-card-sport">${team.sport.toUpperCase()}</span>
      </a>`;
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
        grid.innerHTML = `<div class="empty-state">${q ? "No teams match your search." : "No teams found."}</div>`;
        return;
      }
      grid.innerHTML = teams.map(teamCardHtml).join("");
    } catch {
      grid.innerHTML = `<div class="error-state">Could not load teams.</div>`;
    }
  }

  searchInput?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadTeams, 250);
  });

  renderTabs();
  loadTeams();
})();
