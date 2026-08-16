const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  error: document.getElementById("error"),
  meta: document.getElementById("futures-meta"),
  winners: document.getElementById("division-winners"),
  pills: document.getElementById("div-pills"),
  standingsBody: document.querySelector("#standings-table tbody"),
  refresh: document.getElementById("refresh-btn"),
};

let selectedDiv = null;

function pct(value) {
  if (value == null) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function record(row) {
  const wins = Number(row.actual_wins || 0);
  const losses = Number(row.actual_losses || 0);
  const ties = Number(row.actual_ties || 0);
  return ties ? `${wins}-${losses}-${ties}` : `${wins}-${losses}`;
}

function teamCell(row) {
  const logo = row.logo_url
    ? `<img class="futures-logo" src="${row.logo_url}" alt="" width="28" height="28" />`
    : "";
  const badge = row.place === 1 ? '<span class="champ-badge">1st</span>' : "";
  return `<span class="futures-team">${logo}<span>${row.team}</span>${badge}</span>`;
}

function winnerCard(div) {
  const champ = (div.teams || [])[0] || {};
  const logo = champ.logo_url
    ? `<img class="futures-logo" src="${champ.logo_url}" alt="" width="32" height="32" />`
    : "";
  return `<li class="cfp-seed">
    <span class="cfp-seed-num">${div.conference}</span>
    ${logo}
    <span class="cfp-seed-copy">
      <strong>${champ.team || div.champion || "—"}</strong>
      <span>${div.name} · ${pct(champ.division_win_pct)} to win</span>
    </span>
  </li>`;
}

function renderWinners(divisions) {
  if (!divisions.length) {
    els.winners.innerHTML = '<li class="empty">No division projections yet.</li>';
    return;
  }
  els.winners.innerHTML = divisions.map(winnerCard).join("");
}

function renderPills(divisions) {
  els.pills.innerHTML = "";
  divisions.forEach((div) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conf-pill";
    btn.textContent = div.name;
    btn.dataset.key = div.key;
    if (div.key === selectedDiv) btn.classList.add("conf-pill-active");
    btn.addEventListener("click", () => {
      selectedDiv = div.key;
      renderPills(divisions);
      renderStandings(divisions);
    });
    els.pills.appendChild(btn);
  });
}

function renderStandings(divisions) {
  const div = divisions.find((d) => d.key === selectedDiv) || divisions[0];
  const rows = div?.teams || [];
  if (!rows.length) {
    els.standingsBody.innerHTML =
      '<tr><td colspan="5" class="empty">No division projection yet.</td></tr>';
    return;
  }
  els.standingsBody.innerHTML = rows
    .map((row) => {
      return `<tr class="${row.place === 1 ? "champ-row" : ""}">
        <td>${row.place_label || row.place}</td>
        <td>${teamCell(row)}</td>
        <td>${record(row)}</td>
        <td>${Number(row.expected_wins).toFixed(1)}</td>
        <td>${pct(row.division_win_pct)}</td>
      </tr>`;
    })
    .join("");
}

function pickDefaultDiv(divisions) {
  const preferred = ["AFC_NORTH", "NFC_NORTH", "AFC_EAST", "NFC_EAST"];
  for (const key of preferred) {
    if (divisions.some((d) => d.key === key)) return key;
  }
  return divisions[0]?.key || null;
}

function render(data) {
  const divisions = data.divisions || [];
  selectedDiv = pickDefaultDiv(divisions);
  if (data.disclaimer) {
    els.disclaimer.textContent = data.disclaimer;
    els.disclaimer.classList.remove("hidden");
  }
  if (data.error) {
    els.error.textContent = data.error;
    els.error.classList.remove("hidden");
  } else {
    els.error.classList.add("hidden");
  }
  const bits = [
    data.season ? `${data.season} season` : null,
    data.week_id ? `week of ${data.week_id}` : null,
    data.model ? `model ${data.model}` : null,
    data.games_completed != null ? `${data.games_completed} played` : null,
    data.games_remaining != null ? `${data.games_remaining} remaining` : null,
  ].filter(Boolean);
  els.meta.textContent = bits.join(" · ") || "Wednesday snapshot";
  renderWinners(divisions);
  renderPills(divisions);
  renderStandings(divisions);
}

async function loadFutures(refresh = false) {
  els.loading.classList.remove("hidden");
  els.content.classList.add("hidden");
  els.error.classList.add("hidden");
  try {
    const url = new URL("/api/nfl/futures", window.location.origin);
    if (refresh) url.searchParams.set("refresh", "true");
    const resp = await fetch(url.toString());
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.statusText);
    render(data);
    els.loading.classList.add("hidden");
    els.content.classList.remove("hidden");
  } catch (err) {
    els.loading.classList.add("hidden");
    els.error.textContent = err.message || String(err);
    els.error.classList.remove("hidden");
  }
}

els.refresh?.addEventListener("click", () => loadFutures(true));
loadFutures(false);
