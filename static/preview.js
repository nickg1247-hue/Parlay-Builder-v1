async function bootPreviewPage() {
  const loading = document.getElementById("preview-loading");
  const errEl = document.getElementById("preview-error");
  const content = document.getElementById("preview-content");
  const parts = window.location.pathname.split("/").filter(Boolean);
  const gameId = parts[2];
  if (!gameId) {
    loading?.classList.add("hidden");
    errEl?.classList.remove("hidden");
    errEl.textContent = "Invalid preview URL";
    return;
  }

  try {
    const data = await fetchJSON(`/api/games/mlb/${encodeURIComponent(gameId)}/preview`);
    if (data.status === "error" || data.status === "unsupported") {
      throw new Error(data.message || "Preview unavailable");
    }
    loading?.classList.add("hidden");
    content?.classList.remove("hidden");
    document.title = `${data.game?.away_team || "Away"} @ ${data.game?.home_team || "Home"} — NTG Sports`;
    content.innerHTML = renderPreview(data);
  } catch (e) {
    loading?.classList.add("hidden");
    errEl?.classList.remove("hidden");
    errEl.innerHTML = `${emptyStateIcon?.("no-games") || ""}<p>${e.message || "Could not load preview"}</p>`;
  }
}

function renderPreview(data) {
  const g = data.game || {};
  const model = data.model || {};
  const cards = data.market_cards || {};
  const expl = data.explanation || {};
  const factors = (expl.factors || expl.comparison_factors || [])
    .slice(0, 8)
    .map((f) => `<li>${typeof f === "string" ? f : f.label || f.text || ""}</li>`)
    .join("");
  const lineup = data.lineup || {};
  const spHome = lineup.home?.starting_pitcher;
  const spAway = lineup.away?.starting_pitcher;
  const spBlock =
    spAway || spHome
      ? `<div class="preview-sp-row">
          ${spAway ? `<div class="preview-sp"><strong>${g.away_team}</strong> ${spAway.fullName || spAway.name} ${spAway.stats?.era ? `(${spAway.stats.era} ERA)` : ""}</div>` : ""}
          ${spHome ? `<div class="preview-sp"><strong>${g.home_team}</strong> ${spHome.fullName || spHome.name} ${spHome.stats?.era ? `(${spHome.stats.era} ERA)` : ""}</div>` : ""}
        </div>`
      : `<p class="text-muted">Starters not confirmed yet.</p>`;

  const badges = [];
  if (data.venue?.name) badges.push(data.venue.name);
  if (data.umpires?.length) badges.push(`HP: ${data.umpires[0].name || "TBD"}`);
  const badgeHtml = badges.length
    ? `<div class="preview-badges">${badges.map((b) => `<span class="hero-chip hero-chip-muted">${b}</span>`).join("")}</div>`
    : "";

  const recent = (data.recent_games || [])
    .slice(0, 5)
    .map(
      (r) =>
        `<li>${r.date || ""} · ${r.home_team || ""} ${r.home_score ?? "—"} – ${r.away_score ?? "—"} ${r.away_team || ""}</li>`
    )
    .join("");

  return `
    <header class="preview-head">
      <p class="preview-sport">MLB · ${data.date || ""}</p>
      <h1>${g.away_team || "Away"} @ ${g.home_team || "Home"}</h1>
      ${badgeHtml}
      <p class="preview-actions">
        <a class="home-props-fill-btn" href="${data.game_url || "#"}">Full game page</a>
        <a class="btn-ghost" href="/my-team">My Team</a>
      </p>
    </header>

    <section class="ntg-card preview-section">
      <h2>Model lean</h2>
      <p>${model.pick ? `<strong>${model.pick}</strong> ${model.win_pct != null ? `${model.win_pct}%` : ""}` : "No model pick yet"}</p>
      ${model.expected_runs != null ? `<p class="text-muted">Expected total runs: ${Number(model.expected_runs).toFixed(1)}</p>` : ""}
      ${model.totals_pick ? `<p class="text-muted">Totals: ${model.totals_pick}</p>` : ""}
    </section>

    <section class="ntg-card preview-section">
      <h2>Probable pitchers</h2>
      ${spBlock}
    </section>

    ${
      factors
        ? `<section class="ntg-card preview-section why-pick-card"><h2>Matchup factors</h2><ul class="why-pick-card__factors">${factors}</ul></section>`
        : ""
    }

    ${
      recent
        ? `<section class="ntg-card preview-section"><h2>Recent meetings</h2><ul class="preview-recent">${recent}</ul></section>`
        : ""
    }`;
}

window.bootPreviewPage = bootPreviewPage;
