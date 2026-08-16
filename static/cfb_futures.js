const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
  disclaimer: document.getElementById("disclaimer"),
  error: document.getElementById("error"),
  meta: document.getElementById("futures-meta"),
  seeds: document.getElementById("cfp-seeds"),
  firstRound: document.getElementById("cfp-first-round"),
  pills: document.getElementById("conf-pills"),
  standingsBody: document.querySelector("#standings-table tbody"),
  refresh: document.getElementById("refresh-btn"),
};

let futures = null;
let selectedConf = null;

function pct(value) {
  if (value == null) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function teamCell(row) {
  const logo = row.logo_url
    ? `<img class="futures-logo" src="${row.logo_url}" alt="" width="28" height="28" />`
    : "";
  const badge = row.place === 1 ? '<span class="champ-badge">Champ</span>' : "";
  return `<span class="futures-team">${logo}<span>${row.team}</span>${badge}</span>`;
}

function seedCard(row) {
  const logo = row.logo_url
    ? `<img class="futures-logo" src="${row.logo_url}" alt="" width="32" height="32" />`
    : "";
  const tags = [
    row.bye ? "Bye" : null,
    row.conference_champ ? `${row.conference} champ` : row.conference,
    row.auto_bid ? "Auto" : "At-large",
  ]
    .filter(Boolean)
    .join(" · ");
  return `<li class="cfp-seed${row.bye ? " cfp-seed-bye" : ""}">
    <span class="cfp-seed-num">${row.seed}</span>
    ${logo}
    <span class="cfp-seed-copy">
      <strong>${row.team}</strong>
      <span>${tags}</span>
    </span>
  </li>`;
}

function renderPlayoff(playoff) {
  const seeds = playoff?.seeds || [];
  if (!seeds.length) {
    els.seeds.innerHTML = "<li class=\"empty\">No playoff field yet.</li>";
    els.firstRound.innerHTML = "";
    return;
  }
  els.seeds.innerHTML = seeds.map(seedCard).join("");
  const rounds = playoff.first_round || [];
  els.firstRound.innerHTML = rounds
    .map((pair) => {
      const home = pair.home || {};
      const away = pair.away || {};
      return `<div class="cfp-matchup">
        <span>(${home.seed}) ${home.team || "—"}</span>
        <span class="cfp-vs">vs</span>
        <span>(${away.seed}) ${away.team || "—"}</span>
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
  const conf = conferences.find((c) => c.key === selectedConf) || conferences[0];
  const rows = conf?.teams || [];
  if (!rows.length) {
    els.standingsBody.innerHTML =
      '<tr><td colspan="5" class="empty">No conference projection yet.</td></tr>';
    return;
  }
  els.standingsBody.innerHTML = rows
    .map((row) => {
      const confRecord = `${Number(row.expected_conf_wins).toFixed(1)} W`;
      return `<tr class="${row.place === 1 ? "champ-row" : ""}">
        <td>${row.place}</td>
        <td>${teamCell(row)}</td>
        <td>${confRecord}</td>
        <td>${pct(row.title_pct)}</td>
        <td>${pct(row.playoff_pct)}</td>
      </tr>`;
    })
    .join("");
}

function pickDefaultConf(conferences) {
  const preferred = ["big_ten", "sec", "big_12", "acc"];
  for (const key of preferred) {
    if (conferences.some((c) => c.key === key)) return key;
  }
  return conferences[0]?.key || null;
}

function render(data) {
  futures = data;
  const conferences = data.conferences || [];
  selectedConf = pickDefaultConf(conferences);
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
    data.games_remaining != null ? `${data.games_remaining} games left` : null,
  ].filter(Boolean);
  els.meta.textContent = bits.join(" · ") || "Sunday snapshot";
  renderPlayoff(data.playoff || {});
  renderPills(conferences);
  renderStandings(conferences);
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
loadFutures(false);
