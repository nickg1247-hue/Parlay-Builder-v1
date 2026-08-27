const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  error: document.getElementById("error"),
  meta: document.getElementById("futures-meta"),
  seeds: document.getElementById("cfp-seeds"),
  firstRound: document.getElementById("cfp-first-round"),
  playoffOddsBody: document.querySelector("#playoff-odds-table tbody"),
  pills: document.getElementById("conf-pills"),
  standingsBody: document.querySelector("#standings-table tbody"),
  overallBody: document.querySelector("#overall-table tbody"),
  overallSearch: document.getElementById("overall-search"),
  refresh: document.getElementById("refresh-btn"),
};

let futures = null;
let selectedConf = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function pct(value) {
  if (value == null) return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  if (number > 0 && number < 0.001) return "<0.1%";
  return `${(number * 100).toFixed(1)}%`;
}

function expectedRecord(row) {
  const wins = Number(row.expected_wins);
  const losses = Number(row.expected_losses);
  if (!Number.isFinite(wins) || !Number.isFinite(losses)) return "";
  return `${wins.toFixed(1)}-${losses.toFixed(1)}`;
}

function winRange(row) {
  if (row.win_range_low == null || row.win_range_high == null) return "";
  return `${row.win_range_low}${row.win_range_high} wins`;
}

function teamCell(row, badge = "") {
  const logo = row.logo_url
    ? `<img class="futures-logo" src="${escapeHtml(row.logo_url)}" alt="" width="28" height="28" />`
    : "";
  const badgeHtml = badge ? `<span class="champ-badge">${escapeHtml(badge)}</span>` : "";
  return `<span class="futures-team">${logo}<span>${escapeHtml(row.team)}</span>${badgeHtml}</span>`;
}

function seedCard(row) {
  const logo = row.logo_url
    ? `<img class="futures-logo" src="${escapeHtml(row.logo_url)}" alt="" width="32" height="32" />`
    : "";
  const tags = [
    row.bye ? "Bye" : null,
    row.conference_champ ? `${row.conference} champ` : row.conference,
    row.auto_bid ? "Auto" : "At-large",
  ]
    .filter(Boolean)
    .map(escapeHtml)
    .join("  ");
  return `<li class="cfp-seed${row.bye ? " cfp-seed-bye" : ""}">
    <span class="cfp-seed-num">${row.seed}</span>
    ${logo}
    <span class="cfp-seed-copy">
      <strong>${escapeHtml(row.team)}</strong>
      <span>${tags}</span>
      <span class="cfp-seed-odds">${pct(row.playoff_pct)} CFP  ${pct(row.national_title_pct)} title</span>
    </span>
  </li>`;
}

function renderPlayoff(playoff) {
  const seeds = playoff?.seeds || [];
  const odds = (playoff?.odds || [])
    .filter((row) => Number(row.playoff_pct) >= 0.01 || Number(row.national_title_pct) > 0 || Number(row.rank) <= 25)
    .slice(0, 30);

  if (!odds.length) {
    els.playoffOddsBody.innerHTML =
      '<tr><td colspan="8" class="empty">No playoff odds yet.</td></tr>';
  } else {
    els.playoffOddsBody.innerHTML = odds
      .map(
        (row) => `<tr>
          <td>${row.rank}</td>
          <td>${teamCell(row)}</td>
          <td>${escapeHtml(row.likely_record || "")}</td>
          <td>${pct(row.playoff_pct)}</td>
          <td>${pct(row.bye_pct)}</td>
          <td>${pct(row.semifinal_pct)}</td>
          <td>${pct(row.final_pct)}</td>
          <td class="title-odds">${pct(row.national_title_pct)}</td>
        </tr>`,
      )
      .join("");
  }

  if (!seeds.length) {
    els.seeds.innerHTML = '<li class="empty">No projected field yet.</li>';
    els.firstRound.innerHTML = "";
    return;
  }
  els.seeds.innerHTML = seeds.map(seedCard).join("");
  els.firstRound.innerHTML = (playoff.first_round || [])
    .map((pair) => {
      const home = pair.home || {};
      const away = pair.away || {};
      return `<div class="cfp-matchup">
        <span><strong>(${home.seed}) ${escapeHtml(home.team || "")}</strong> <small>${pct(pair.home_win_pct)}</small></span>
        <span class="cfp-vs">hosts</span>
        <span><strong>(${away.seed}) ${escapeHtml(away.team || "")}</strong> <small>${pct(pair.away_win_pct)}</small></span>
      </div>`;
    })
    .join("");
}

function renderPills(conferences) {
  els.pills.innerHTML = "";
  conferences.forEach((conf) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conf-pill";
    btn.textContent = conf.name;
    btn.dataset.key = conf.key;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", String(conf.key === selectedConf));
    if (conf.key === selectedConf) btn.classList.add("conf-pill-active");
    btn.addEventListener("click", () => {
      selectedConf = conf.key;
      renderPills(conferences);
      renderStandings(conferences);
    });
    els.pills.appendChild(btn);
  });
}

function renderStandings(conferences) {
  const conf = conferences.find((item) => item.key === selectedConf) || conferences[0];
  const rows = conf?.teams || [];
  if (!rows.length) {
    els.standingsBody.innerHTML =
      '<tr><td colspan="8" class="empty">No conference projection yet.</td></tr>';
    return;
  }
  els.standingsBody.innerHTML = rows
    .map(
      (row) => `<tr class="${row.place === 1 ? "champ-row" : ""}">
        <td>${row.place}</td>
        <td>${teamCell(row, row.place === 1 ? "Favorite" : "")}</td>
        <td>${expectedRecord(row)}</td>
        <td>${winRange(row)}</td>
        <td>${Number(row.expected_conf_wins).toFixed(1)}</td>
        <td>${pct(row.title_game_pct)}</td>
        <td>${pct(row.title_pct)}</td>
        <td>${pct(row.playoff_pct)}</td>
      </tr>`,
    )
    .join("");
}

function renderOverall() {
  const query = String(els.overallSearch?.value || "").trim().toLowerCase();
  const rows = (futures?.overall || []).filter((row) =>
    !query || String(row.team || "").toLowerCase().includes(query),
  );
  if (!rows.length) {
    els.overallBody.innerHTML =
      '<tr><td colspan="9" class="empty">No teams match this search.</td></tr>';
    return;
  }
  const confNames = new Map(
    (futures?.conferences || []).map((conf) => [conf.key, conf.name]),
  );
  els.overallBody.innerHTML = rows
    .map((row) => {
      const conference =
        row.conference_key === "independent"
          ? "Independent"
          : confNames.get(row.conference_key) || row.conference_key || "";
      return `<tr>
        <td>${row.rank}</td>
        <td>${teamCell(row)}</td>
        <td>${escapeHtml(conference)}</td>
        <td>${expectedRecord(row)}</td>
        <td>${escapeHtml(row.likely_record || "")}</td>
        <td>${winRange(row)}</td>
        <td>${pct(row.ten_win_pct)}</td>
        <td>${pct(row.playoff_pct)}</td>
        <td class="title-odds">${pct(row.national_title_pct)}</td>
      </tr>`;
    })
    .join("");
}

function pickDefaultConf(conferences) {
  const preferred = ["big_ten", "sec", "big_12", "acc"];
  for (const key of preferred) {
    if (conferences.some((conf) => conf.key === key)) return key;
  }
  return conferences[0]?.key || null;
}

function render(data) {
  futures = data;
  const conferences = data.conferences || [];
  selectedConf = selectedConf || pickDefaultConf(conferences);
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
    data.n_sims ? `${Number(data.n_sims).toLocaleString()} simulations` : null,
    data.games_remaining != null ? `${data.games_remaining} games left` : null,
  ].filter(Boolean);
  els.meta.textContent = bits.join("  ") || "Sunday snapshot";
  renderPlayoff(data.playoff || {});
  renderPills(conferences);
  renderStandings(conferences);
  renderOverall();
}

async function loadFutures(refresh = false) {
  els.loading.classList.remove("hidden");
  els.content.classList.add("hidden");
  els.error.classList.add("hidden");
  try {
    const url = new URL("/api/cfb/futures", window.location.origin);
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
els.overallSearch?.addEventListener("input", renderOverall);
loadFutures(false);
