(function () {
  const sport = document.body.dataset.sport || "nfl";
  const els = {
    loading: document.getElementById("pr-loading"),
    error: document.getElementById("pr-error"),
    content: document.getElementById("pr-content"),
    meta: document.getElementById("pr-meta"),
    summary: document.getElementById("pr-summary"),
    legend: document.getElementById("pr-legend"),
    search: document.getElementById("pr-search"),
    group: document.getElementById("pr-group"),
    count: document.getElementById("pr-count"),
    body: document.querySelector("#pr-table tbody"),
  };

  let payload = null;
  let openTeam = "";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function signed(value) {
    if (value == null || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const text = `${number > 0 ? "+" : ""}${number.toFixed(1)}`;
    const tone = number > 0 ? "pr-pos" : number < 0 ? "pr-neg" : "pr-muted";
    return `<span class="${tone}">${text}</span>`;
  }

  function rankLine(rank, count, rating) {
    if (rank == null) return "—";
    const rated = payload.ranked_count || count;
    return `<span class="pr-rank-cell">${rank} / ${rated}${signed(rating).replace("<span", "<small").replace("</span>", "</small>")}</span>`;
  }

  function teamMatches(team) {
    const query = els.search.value.trim().toLowerCase();
    const group = els.group.value;
    if (group && group !== "all" && team.group !== group) return false;
    if (!query) return true;
    return `${team.team} ${team.abbr} ${team.group}`.toLowerCase().includes(query);
  }

  function subpointHtml(point) {
    const tag = point.counted ? "" : " pr-unscored";
    const mark = point.counted ? "" : " <small>already inside efficiency</small>";
    const parts = (point.parts || [])
      .map((part) => `<li>${escapeHtml(part.label)} ${signed(part.points)} <small>${escapeHtml(part.detail)}</small></li>`)
      .join("");
    return `
      <div class="pr-line${tag}">
        <strong>${escapeHtml(point.label)}${mark}</strong>
        ${signed(point.points)}
        <small>${escapeHtml(point.detail)}</small>
        ${parts ? `<ul class="pr-parts">${parts}</ul>` : ""}
      </div>`;
  }

  function breakdownHtml(team) {
    const unavailable = (payload.unavailable || [])
      .map((item) => `<li><strong>${escapeHtml(item.label)}.</strong> ${escapeHtml(item.detail)}</li>`)
      .join("");
    return `
      <tr class="pr-break">
          <td colspan="6">
          <div class="pr-break-grid">
            <div>
              <h3>Sub-points</h3>
              <p class="pr-note">Counted lines add up to ${signed(team.power)}. Schedule strength and recent performance are shown because they already moved the efficiency numbers.</p>
              ${(team.subpoints || []).map(subpointHtml).join("")}
            </div>
            <div>
              <h3>Side ranks</h3>
              <p>Offense ${team.offense_rank ?? "—"} / ${payload.ranked_count || payload.team_count}</p>
              <p>Defense ${team.defense_rank ?? "—"} / ${payload.ranked_count || payload.team_count}</p>
              <p class="pr-note">1 is the best on that side of the ball this season. These ranks use opponent-adjusted efficiency only. They are not a second copy of the overall rank.</p>
              <h3>Not scored yet</h3>
              <ul class="pr-missing-list">${unavailable}</ul>
            </div>
          </div>
        </td>
      </tr>`;
  }

  function render() {
    const visible = (payload.teams || []).filter(teamMatches);
    els.count.textContent = `${visible.length} of ${payload.team_count} teams`;
    const rows = [];
    visible.forEach((team) => {
      const key = team.abbr || team.team;
      const open = openTeam === key;
      const logo = team.logo_url
        ? `<img class="pr-logo" alt="" src="${escapeHtml(team.logo_url)}" />`
        : "";
      rows.push(`
        <tr class="pr-row" data-team="${escapeHtml(key)}" aria-expanded="${open ? "true" : "false"}">
          <td>${team.rank ?? "—"}</td>
          <td><span class="pr-team">${logo}<span>${escapeHtml(team.team)}${team.group ? `<small class="pr-record">${escapeHtml(team.group)}</small>` : ""}</span></span></td>
          <td>${signed(team.power)}</td>
          <td>${rankLine(team.offense_rank, payload.ranked_count, team.offense_rating)}</td>
          <td>${rankLine(team.defense_rank, payload.ranked_count, team.defense_rating)}</td>
          <td>${team.games_played ? `${team.wins}-${team.losses}` : "—"}</td>
        </tr>`);
      if (open) rows.push(breakdownHtml(team));
    });
    els.body.innerHTML = rows.join("");
  }

  function fillGroups() {
    const groups = payload.groups || [];
    const field = els.group.closest(".pr-field");
    if (!groups.length) {
      if (field) field.classList.add("hidden");
      return;
    }
    if (field) field.classList.remove("hidden");
    const label = payload.group_label || "Group";
    els.group.setAttribute("aria-label", label);
    els.group.innerHTML = `<option value="all">All ${escapeHtml(label.toLowerCase())}s</option>` +
      groups.map((group) => `<option value="${escapeHtml(group)}">${escapeHtml(group)}</option>`).join("");
  }

  function fillLegend() {
    const items = (payload.unavailable || [])
      .map((item) => `<li><strong>${escapeHtml(item.label)}.</strong> ${escapeHtml(item.detail)}</li>`)
      .join("");
    els.legend.innerHTML = `<h2>Not in the rating yet</h2><ul>${items}</ul>`;
  }

  els.body.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-team]");
    if (!row) return;
    const key = row.getAttribute("data-team");
    openTeam = openTeam === key ? "" : key;
    render();
  });
  els.search.addEventListener("input", render);
  els.group.addEventListener("change", render);

  function loadRankings() {
    els.loading.classList.remove("hidden");
    els.error.classList.add("hidden");
    fetch(`/api/${sport}/power-rankings`)
      .then((response) => {
        if (!response.ok) throw new Error("Rankings are unavailable right now.");
        return response.json();
      })
      .then((data) => {
        payload = data;
        const through = data.through ? ` through ${data.through}` : "";
        els.meta.textContent = `${data.season} season${through} · ${data.team_count} teams · ${data.unit}`;
        els.summary.textContent = data.summary || "";
        fillGroups();
        fillLegend();
        els.loading.classList.add("hidden");
        els.content.classList.remove("hidden");
        render();
      })
      .catch((error) => {
        els.loading.classList.add("hidden");
        els.error.textContent = error.message || "Rankings are unavailable right now.";
        els.error.classList.remove("hidden");
      });
  }

  loadRankings();
  if (typeof initModelRelearn === "function") {
    initModelRelearn(sport, { onDone: loadRankings });
  }
})();
