(async function () {
  if (typeof loadPublicFeatures === "function") await loadPublicFeatures();
  if (typeof initSiteChrome === "function") initSiteChrome();

  const body = document.getElementById("watchlist-body");
  const summaryEl = document.getElementById("picks-summary");
  const filtersEl = document.getElementById("picks-filters");
  const user = window.pbUserAuth || {};
  let cache = { props: [], games: [], counts: {} };
  let statusFilter = "all";
  let sportFilter = "all";

  function signInPrompt() {
    const next = encodeURIComponent("/watchlist");
    body.innerHTML = `
      <div class="empty-state-card ntg-card">
        <h3>Sign in to save this prediction</h3>
        <p>Track the original line, later movement, and the result after the game.</p>
        <div class="ntg-hero-actions">
          <a class="ntg-btn ntg-btn-primary" href="/signin?next=${next}">Sign in</a>
          <a class="ntg-btn ntg-btn-ghost" href="/signup?next=${next}">Create account</a>
        </div>
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

  function fmtOdds(v) {
    if (v == null) return "—";
    return typeof fmtAmericanOdds === "function" ? fmtAmericanOdds(v) : v;
  }

  function fmtPct(v) {
    if (v == null || Number.isNaN(Number(v))) return "—";
    const n = Number(v);
    return `${(n <= 1 ? n * 100 : n).toFixed(1)}%`;
  }

  function resultBadge(item) {
    if (item.result === "WIN") return `<span class="ntg-badge ntg-badge-positive">WIN</span>`;
    if (item.result === "LOSS") return `<span class="ntg-badge">LOSS</span>`;
    if (item.result === "PUSH") return `<span class="ntg-badge">PUSH</span>`;
    return `<span class="ntg-badge">${String(item.status || "upcoming").toUpperCase()}</span>`;
  }

  function movementHtml(item) {
    if (item.current_unavailable && item.current_line == null) {
      return `<p class="sandbox-card-desc">Current market unavailable. Your saved prediction is still here.</p>`;
    }
    if (item.current_line == null) return `<p class="sandbox-card-desc">Current line not stored yet</p>`;
    const move = item.movement;
    const moveStr = move == null ? "" : `${move > 0 ? "+" : ""}${Number(move).toFixed(1)}`;
    return `<p class="sandbox-card-desc">Saved ${item.saved_line} → Current ${item.current_line}${moveStr ? ` · ${moveStr}` : ""}</p>`;
  }

  function propCard(p) {
    const side = (p.side || "over").toUpperCase();
    return `
      <article class="sandbox-card ntg-card" data-status="${p.status || "upcoming"}" data-sport="${p.sport || ""}">
        <div>
          <p class="sandbox-card-title"><a href="${playerHref(p)}">${p.player}</a></p>
          <p class="sandbox-card-desc">${(p.sport || "").toUpperCase()} · ${p.market_label || p.market_type} · ${side} ${p.saved_line} · ${fmtOdds(p.saved_odds)}</p>
          ${movementHtml(p)}
          <p class="sandbox-card-desc">Model when saved ${fmtPct(p.saved_model_probability)} · Current ${fmtPct(p.current_model_probability)}</p>
          ${resultBadge(p)}
        </div>
        <div>
          <a class="ntg-btn ntg-btn-ghost" href="${gameHref(p)}">View analysis</a>
          <button type="button" class="ntg-btn ntg-btn-ghost" data-remove-prop="${p.id}">Remove</button>
        </div>
      </article>`;
  }

  function gameCard(g) {
    return `
      <article class="sandbox-card ntg-card" data-status="${g.status || "upcoming"}" data-sport="${g.sport || ""}">
        <div>
          <p class="sandbox-card-title">${g.current_matchup || g.matchup || g.game_id}</p>
          <p class="sandbox-card-desc">${(g.sport || "").toUpperCase()}${g.saved_model_lean ? ` · Lean ${g.saved_model_lean}` : ""}</p>
          <p class="sandbox-card-desc">Model when saved ${fmtPct(g.saved_model_probability)}</p>
          ${resultBadge(g)}
        </div>
        <div>
          <a class="ntg-btn ntg-btn-ghost" href="${gameHref(g)}">View analysis</a>
          <button type="button" class="ntg-btn ntg-btn-ghost" data-remove-game="${g.id}">Remove</button>
        </div>
      </article>`;
  }

  function applyFilters() {
    body.querySelectorAll("article[data-status]").forEach((card) => {
      const statusOk = statusFilter === "all" || card.getAttribute("data-status") === statusFilter;
      const sportOk = sportFilter === "all" || card.getAttribute("data-sport") === sportFilter;
      card.hidden = !(statusOk && sportOk);
    });
  }

  function render(data) {
    cache = data;
    const props = data.props || [];
    const games = data.games || [];
    const counts = data.counts || {};
    if (!props.length && !games.length) {
      filtersEl.hidden = true;
      summaryEl.hidden = true;
      body.innerHTML = `
        <div class="empty-state-card ntg-card">
          <h3>Nothing saved yet</h3>
          <p>Save games and player props to track them here.</p>
          <a class="ntg-btn ntg-btn-primary" href="/props">Explore player props</a>
        </div>`;
      return;
    }
    filtersEl.hidden = false;
    summaryEl.hidden = false;
    summaryEl.innerHTML = `
      <span class="hero-chip">Upcoming ${counts.upcoming || 0}</span>
      <span class="hero-chip">Live ${counts.live || 0}</span>
      <span class="hero-chip">Final ${counts.final || 0}</span>`;
    body.innerHTML = `
      <h2 class="app-section-title">Saved props</h2>
      <div class="sandbox-hub">${props.map(propCard).join("") || "<p class='text-muted'>None</p>"}</div>
      <h2 class="app-section-title">Saved games</h2>
      <div class="sandbox-hub">${games.map(gameCard).join("") || "<p class='text-muted'>None</p>"}</div>`;
    applyFilters();
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
      body.innerHTML = `<div class="empty-state-card ntg-card"><h3>Could not load My Picks</h3><button type="button" class="ntg-btn ntg-btn-primary" id="watchlist-retry">Retry</button></div>`;
      body.querySelector("#watchlist-retry")?.addEventListener("click", load);
      return;
    }
    render(await res.json());
  }

  filtersEl?.addEventListener("click", (e) => {
    const statusBtn = e.target.closest("[data-pick-status]");
    const sportBtn = e.target.closest("[data-pick-sport]");
    if (statusBtn) {
      statusFilter = statusBtn.getAttribute("data-pick-status");
      filtersEl.querySelectorAll("[data-pick-status]").forEach((b) => {
        b.classList.toggle("sport-pill-active", b === statusBtn);
      });
      applyFilters();
    }
    if (sportBtn) {
      sportFilter = sportBtn.getAttribute("data-pick-sport");
      filtersEl.querySelectorAll("[data-pick-sport]").forEach((b) => {
        b.classList.toggle("sport-pill-active", b === sportBtn);
      });
      applyFilters();
    }
  });

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
