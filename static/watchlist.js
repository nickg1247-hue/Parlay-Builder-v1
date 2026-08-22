(async function () {
  if (typeof loadPublicFeatures === "function") await loadPublicFeatures();
  if (typeof initSiteChrome === "function") initSiteChrome();

  const body = document.getElementById("watchlist-body");
  const user = window.pbUserAuth || {};

  function signInPrompt() {
    const next = encodeURIComponent("/watchlist");
    if (!body) return;
    body.innerHTML = `
      <div class="empty-state-card ntg-card">
        <h3>Sign in to save picks</h3>
        <p>Your watchlist stores the original line, sportsbook, and side so you can compare movement later.</p>
        <a class="ntg-btn ntg-btn-primary" href="/signin?next=${next}">Sign in</a>
      </div>`;
  }

  function gameHref(item) {
    if (!item.game_id) return item.sport === "nfl" ? "/nfl" : "/mlb";
    return item.sport === "nfl"
      ? `/nfl/game/${encodeURIComponent(item.game_id)}`
      : `/mlb/game/${encodeURIComponent(item.game_id)}`;
  }

  function playerHref(item) {
    const key = item.player_id || item.player;
    if (!key) return "/props";
    return `/players/${encodeURIComponent(item.sport || "mlb")}/${encodeURIComponent(key)}`;
  }

  function render(data) {
    const props = data.props || [];
    const games = data.games || [];
    if (!props.length && !games.length) {
      body.innerHTML = `
        <div class="empty-state-card ntg-card">
          <h3>Nothing saved yet</h3>
          <p>Open Player Props and tap Save on a card to keep the original line.</p>
          <a class="ntg-btn ntg-btn-primary" href="/props">Browse player props</a>
        </div>`;
      return;
    }
    const propCards = props
      .map((p) => {
        const side = (p.side || "over").toUpperCase();
        const odds = typeof fmtAmericanOdds === "function" ? fmtAmericanOdds(p.saved_odds) : p.saved_odds;
        const current =
          p.current_line != null
            ? `<p class="sandbox-card-desc">Current ${p.current_line}${
                p.movement != null ? ` · Movement ${p.movement > 0 ? "+" : ""}${p.movement}` : ""
              }</p>`
            : `<p class="sandbox-card-desc">Current line not stored yet</p>`;
        const result = p.result
          ? `<span class="ntg-badge ${p.result === "WIN" ? "ntg-badge-positive" : ""}">${p.result}</span>`
          : `<span class="ntg-badge">${p.status || "SAVED"}</span>`;
        return `
        <article class="sandbox-card ntg-card">
          <div>
            <p class="sandbox-card-title"><a href="${playerHref(p)}">${p.player}</a></p>
            <p class="sandbox-card-desc">${(p.sport || "").toUpperCase()} · ${p.market_label || p.market_type} · ${side} ${p.saved_line} · ${odds || ""}</p>
            <p class="sandbox-card-desc">Saved at ${p.saved_line}${p.sportsbook ? ` · ${p.sportsbook}` : ""}</p>
            ${current}
            ${result}
          </div>
          <div>
            <a class="ntg-btn ntg-btn-ghost" href="${gameHref(p)}">Game</a>
            <button type="button" class="ntg-btn ntg-btn-ghost" data-remove-prop="${p.id}">Remove</button>
          </div>
        </article>`;
      })
      .join("");
    const gameCards = games
      .map(
        (g) => `
        <article class="sandbox-card ntg-card">
          <div>
            <p class="sandbox-card-title">${g.matchup || g.game_id}</p>
            <p class="sandbox-card-desc">${(g.sport || "").toUpperCase()}</p>
          </div>
          <div>
            <a class="ntg-btn ntg-btn-ghost" href="${gameHref(g)}">Open</a>
            <button type="button" class="ntg-btn ntg-btn-ghost" data-remove-game="${g.id}">Remove</button>
          </div>
        </article>`
      )
      .join("");
    body.innerHTML = `
      <h2 class="app-section-title">Saved props</h2>
      <div class="sandbox-hub">${propCards || "<p class='text-muted'>None</p>"}</div>
      <h2 class="app-section-title">Saved games</h2>
      <div class="sandbox-hub">${gameCards || "<p class='text-muted'>None</p>"}</div>`;
  }

  async function load() {
    if (!user.signed_in) {
      signInPrompt();
      return;
    }
    const res = await fetch("/api/watchlist", { credentials: "same-origin" });
    if (res.status === 401) {
      signInPrompt();
      return;
    }
    if (!res.ok) {
      body.innerHTML = `<div class="empty-state-card ntg-card"><h3>Could not load watchlist</h3><button type="button" class="ntg-btn ntg-btn-primary" id="watchlist-retry">Retry</button></div>`;
      body.querySelector("#watchlist-retry")?.addEventListener("click", load);
      return;
    }
    render(await res.json());
  }

  body?.addEventListener("click", async (e) => {
    const propBtn = e.target.closest("[data-remove-prop]");
    const gameBtn = e.target.closest("[data-remove-game]");
    if (propBtn) {
      await fetch(`/api/watchlist/props/${propBtn.getAttribute("data-remove-prop")}`, {
        method: "DELETE",
        credentials: "same-origin",
      });
      await load();
    }
    if (gameBtn) {
      await fetch(`/api/watchlist/games/${gameBtn.getAttribute("data-remove-game")}`, {
        method: "DELETE",
        credentials: "same-origin",
      });
      await load();
    }
  });

  await load();
})();
