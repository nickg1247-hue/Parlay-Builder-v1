(function () {
  const loading = document.getElementById("team-loading");
  const errEl = document.getElementById("team-error");
  const content = document.getElementById("team-content");

  const parts = window.location.pathname.split("/").filter(Boolean);
  const sport = parts[1];
  const teamId = parts[2];

  initLiveTicker("live-ticker", { sport: "all", intervalMs: 45000 });

  if (!sport || !teamId) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Invalid team URL";
    return;
  }

  function playerInitials(name) {
    return (name || "?")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join("")
      .toUpperCase();
  }

  function playerPhotoHtml(p) {
    if (p.photo_url) {
      const initials = playerInitials(p.name);
      return `<img class="roster-player-photo" src="${p.photo_url}" alt="" width="72" height="72" loading="lazy" data-fallback="${initials}">`;
    }
    return `<span class="roster-player-fallback">${playerInitials(p.name)}</span>`;
  }

  function playerCardHtml(p) {
    const jersey = p.jersey != null ? `#${p.jersey}` : "";
    const pos = p.position || "";
    const pid = p.id || p.player_id || "";
    const safeName = (p.name || "").replace(/"/g, "&quot;");
    return `
      <button type="button" class="roster-player-card ntg-card roster-player-card--clickable" data-player-id="${pid}" data-player-name="${safeName}">
        ${playerPhotoHtml(p)}
        <div class="roster-player-meta">
          ${jersey ? `<span class="roster-player-jersey">${jersey}</span>` : ""}
          <h3 class="roster-player-name">${p.name}</h3>
          ${pos ? `<span class="roster-player-pos">${pos}</span>` : ""}
        </div>
      </button>`;
  }

  function rosterGroupsHtml(groups) {
    if (!groups || !groups.length) {
      return `<p class="section-sub">Roster unavailable.</p>`;
    }
    return groups
      .map(
        (group) => `
        <div class="roster-position-group">
          <h3 class="roster-group-title">${group.label}</h3>
          <div class="roster-player-grid">
            ${(group.players || []).map(playerCardHtml).join("")}
          </div>
        </div>`
      )
      .join("");
  }

  function gameRowHtml(g) {
    const score =
      g.team_score != null && g.opp_score != null
        ? `${g.team_score}–${g.opp_score}`
        : "—";
    const result = g.won ? "W" : "L";
    const when = g.date ? formatLocalTimeShort(g.date) : "";
    return `
      <li class="team-game-row ${g.won ? "team-game-win" : "team-game-loss"}">
        <span class="team-game-result">${result}</span>
        <span class="team-game-matchup">${g.home_away} ${g.opponent}</span>
        <span class="team-game-score">${score}</span>
        <span class="team-game-date">${when}</span>
      </li>`;
  }

  function renderTeam(data) {
    const logo = data.logo_url
      ? `<img class="team-detail-logo" src="${data.logo_url}" alt="" width="72" height="72">`
      : "";
    const groups = data.roster_groups || [];
    const games = data.recent_games || [];

    content.innerHTML = `
      <header class="team-detail-header">
        ${logo}
        <div class="team-detail-meta">
          <p class="team-detail-sport">${(data.sport || sport).toUpperCase()}</p>
          <h1>${data.name}</h1>
          <p class="team-detail-record">${data.record ? `Record ${data.record}` : ""}${data.standing ? ` · ${data.standing}` : ""}</p>
          <a class="team-detail-back" href="/my-team">← All teams</a>
        </div>
      </header>

      <section class="detail-section">
        <h2>Recent games</h2>
        ${games.length ? `<ul class="team-games-list">${games.map(gameRowHtml).join("")}</ul>` : `<p class="section-sub">No recent results on file.</p>`}
      </section>

      <section class="detail-section team-roster-section">
        <h2>Roster</h2>
        <p class="section-sub">Grouped by position — photos from ESPN / MLB.</p>
        ${rosterGroupsHtml(groups)}
      </section>
    `;
  }

  fetchJSON(`/api/teams/${encodeURIComponent(sport)}/${encodeURIComponent(teamId)}`)
    .then((data) => {
      loading.classList.add("hidden");
      content.classList.remove("hidden");
      document.title = `${data.name} — NTG Sports`;
      renderTeam(data);
      content.querySelectorAll(".roster-player-photo").forEach((img) => {
        img.addEventListener("error", () => {
          const fb = document.createElement("span");
          fb.className = "roster-player-fallback";
          fb.textContent = img.dataset.fallback || "?";
          img.replaceWith(fb);
        });
      });
      content.querySelectorAll(".roster-player-card--clickable").forEach((btn) => {
        btn.addEventListener("click", () => {
          const pid = btn.getAttribute("data-player-id");
          const name = btn.getAttribute("data-player-name");
          if (pid && typeof openPlayerProfileModal === "function") {
            openPlayerProfileModal(sport, pid, name);
          }
        });
      });
    })
    .catch((e) => {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = e.message || "Team not found";
    });
})();
