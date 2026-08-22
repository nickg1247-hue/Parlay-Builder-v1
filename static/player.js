(async function () {
  if (typeof loadPublicFeatures === "function") await loadPublicFeatures();
  if (typeof initSiteChrome === "function") initSiteChrome();

  const parts = window.location.pathname.split("/").filter(Boolean);
  const sport = (parts[1] || "mlb").toLowerCase();
  const playerKey = decodeURIComponent(parts[2] || "");
  const nameEl = document.getElementById("player-name");
  const metaEl = document.getElementById("player-meta");
  const listEl = document.getElementById("player-props");
  const logWrap = document.getElementById("player-log-wrap");
  const logEl = document.getElementById("player-log");
  const back = document.getElementById("player-back-props");
  if (back) back.href = sport === "nfl" ? "/props?sport=nfl" : "/props?sport=mlb";

  if (!playerKey) {
    if (metaEl) metaEl.textContent = "Player not found.";
    return;
  }

  if (nameEl) nameEl.textContent = playerKey.replace(/-/g, " ");

  const search = new URLSearchParams({
    sport,
    player: playerKey,
    limit: "80",
    line_kind: "main",
  });
  let props = [];
  try {
    const res = await fetch(`/api/props/search?${search}`, { credentials: "same-origin" });
    if (res.ok) {
      const data = await res.json();
      props = data.props || [];
    }
  } catch (_) {
    props = [];
  }

  if (props[0]?.player && nameEl) nameEl.textContent = props[0].player;
  if (metaEl) {
    const first = props[0] || {};
    const bits = [sport.toUpperCase(), first.team, first.position, first.matchup || first.opponent]
      .filter(Boolean);
    metaEl.textContent = bits.join(" · ") || "Available posted props for this player.";
  }

  if (typeof renderPropExplorerList === "function") {
    renderPropExplorerList(listEl, props, {
      sport,
      emptyMessage: "No posted props for this player right now.",
      emptyTitle: "NO PLAYER PROPS POSTED",
      clearHref: sport === "nfl" ? "/props?sport=nfl" : "/props?sport=mlb",
    });
  }

  if (sport === "mlb" && /^\d+$/.test(playerKey)) {
    try {
      const res = await fetch(`/api/players/mlb/${encodeURIComponent(playerKey)}/profile`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.status === "unsupported" || !data.game_log) return;
      const rows = (data.game_log || []).slice(0, 8);
      if (!rows.length || !logEl) return;
      logWrap.hidden = false;
      logEl.innerHTML = `<div class="perf-table-wrap"><table class="perf-table"><thead><tr><th>Date</th><th>Opp</th><th>Stat</th></tr></thead><tbody>${rows
        .map((r) => `<tr><td>${r.date || ""}</td><td>${r.opponent || ""}</td><td>${r.stat ?? r.value ?? ""}</td></tr>`)
        .join("")}</tbody></table></div>`;
    } catch (_) {
      /* hide log when unavailable */
    }
  }
})();
