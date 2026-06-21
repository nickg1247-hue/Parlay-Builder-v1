/**
 * NTG Sports — player prop detail modal (MLB v1).
 */
(function (global) {
  let _overlay = null;
  let _lastFocus = null;

  function fmtPct(v) {
    if (v == null || Number.isNaN(v)) return "—";
    return `${Math.round(v * 100)}%`;
  }

  function fmtOdds(odds) {
    if (odds == null) return "—";
    return odds > 0 ? `+${odds}` : `${odds}`;
  }

  function ensureOverlay() {
    if (_overlay) return _overlay;
    _overlay = document.createElement("div");
    _overlay.id = "player-prop-modal";
    _overlay.className = "player-prop-modal hidden";
    _overlay.setAttribute("role", "dialog");
    _overlay.setAttribute("aria-modal", "true");
    _overlay.setAttribute("aria-labelledby", "player-prop-modal-title");
    _overlay.innerHTML = `
      <div class="player-prop-modal__backdrop" data-close-modal="1"></div>
      <div class="player-prop-modal__panel ntg-card">
        <button type="button" class="player-prop-modal__close" data-close-modal="1" aria-label="Close">×</button>
        <div class="player-prop-modal__body"></div>
      </div>`;
    document.body.appendChild(_overlay);

    _overlay.addEventListener("click", (e) => {
      if (e.target.closest("[data-close-modal]")) closePropModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && _overlay && !_overlay.classList.contains("hidden")) {
        closePropModal();
      }
    });
    return _overlay;
  }

  function skeletonHtml() {
    return `
      <div class="player-prop-modal__skeleton">
        <div class="skeleton-row" style="height:2rem;width:60%"></div>
        <div class="skeleton-row" style="height:1rem;width:40%;margin-top:0.75rem"></div>
        <div class="skeleton-row" style="height:6rem;margin-top:1rem"></div>
      </div>`;
  }

  function renderModalContent(prop, data) {
    const side = prop.recommended_side || prop.side || "over";
    const sideLabel = side === "under" ? "Under" : "Over";
    const edge =
      prop.rank_score != null
        ? `<span class="prop-modal-edge">Model ${prop.rank_score}</span>`
        : prop.recommended_hit_rate != null
          ? `<span class="prop-modal-edge">L10 ${fmtPct(prop.recommended_hit_rate)}</span>`
          : "";
    const why = prop.line_insight
      ? `<p class="prop-modal-why">${prop.line_insight}</p>`
      : "";

    const rates = data.hit_rates || {};
    const games = data.recent_games || [];
    const tableRows = games
      .map((g) => {
        const hitClass =
          g.hit === true ? "prop-log-hit" : g.hit === false ? "prop-log-miss" : "prop-log-push";
        return `<tr class="${hitClass}">
          <td>${g.date || "—"}</td>
          <td>${g.opponent || "—"}</td>
          <td>${g.stat_value != null ? g.stat_value : "—"}</td>
          <td>${g.hit === true ? "✓" : g.hit === false ? "✗" : "—"}</td>
        </tr>`;
      })
      .join("");

    const photo = data.photo_url
      ? `<img class="prop-modal-photo" src="${data.photo_url}" alt="" width="64" height="64">`
      : "";

    return `
      <header class="prop-modal-head">
        ${photo}
        <div>
          <h2 id="player-prop-modal-title">${data.player_name || prop.player}</h2>
          <p class="prop-modal-market">${data.market_label || prop.market_label}: ${sideLabel} ${prop.line}</p>
          <p class="prop-modal-odds">${fmtOdds(prop.recommended_odds)} ${edge}</p>
          ${why}
        </div>
      </header>
      <div class="prop-modal-rates">
        <span class="hero-chip">L5 ${fmtPct(rates.l5)}</span>
        <span class="hero-chip">L10 ${fmtPct(rates.l10)}</span>
        <span class="hero-chip">Season ${fmtPct(rates.season)}</span>
      </div>
      <section class="prop-modal-log">
        <h3>Recent games vs line</h3>
        <div class="prop-modal-table-wrap">
          <table class="prop-modal-table">
            <thead><tr><th>Date</th><th>Opp</th><th>Stat</th><th>Hit</th></tr></thead>
            <tbody>${tableRows || '<tr><td colspan="4">No game log data</td></tr>'}</tbody>
          </table>
        </div>
      </section>
      <div class="prop-modal-actions">
        ${
          typeof global.addPropToSlip === "function" && typeof global.propSlipLegFromProp === "function"
            ? `<button type="button" class="home-props-fill-btn" id="prop-modal-add-slip">Add to prop slip</button>`
            : ""
        }
      </div>`;
  }

  async function resolvePlayerId(sport, prop) {
    if (prop.player_id) return String(prop.player_id);
    try {
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/by-name/${encodeURIComponent(prop.player)}/id`
      );
      if (!res.ok) return null;
      const data = await res.json();
      return data.player_id != null ? String(data.player_id) : null;
    } catch {
      return null;
    }
  }

  async function openPropModal(prop, sport = "mlb") {
    if (!prop || !prop.player || !prop.market_type) return;
    const overlay = ensureOverlay();
    _lastFocus = document.activeElement;
    overlay.classList.remove("hidden");
    document.body.classList.add("player-prop-modal-open");
    const body = overlay.querySelector(".player-prop-modal__body");
    body.innerHTML = skeletonHtml();
    overlay.querySelector(".player-prop-modal__close")?.focus();

    const playerId = await resolvePlayerId(sport, prop);
    if (!playerId) {
      body.innerHTML = `<div class="empty-state-card">${global.emptyStateIcon?.("no-bets") || ""}<p>Could not match player to stats.</p></div>`;
      return;
    }

    const side = prop.recommended_side || prop.side || "over";
    const qs = new URLSearchParams({
      market_type: prop.market_type,
      line: String(prop.line),
      side,
    });
    try {
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/${encodeURIComponent(playerId)}/prop-context?${qs}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      body.innerHTML = renderModalContent(prop, data);
      body.querySelector("#prop-modal-add-slip")?.addEventListener("click", () => {
        const leg = global.propSlipLegFromProp?.(prop);
        if (leg && global.addPropToSlip?.(leg)) {
          body.querySelector("#prop-modal-add-slip").textContent = "Added ✓";
        }
      });
    } catch {
      body.innerHTML = `<div class="empty-state-card">${global.emptyStateIcon?.("no-bets") || ""}<p>Could not load prop context.</p><button type="button" class="empty-state-retry" data-retry="1">Try again</button></div>`;
      body.querySelector("[data-retry]")?.addEventListener("click", () => openPropModal(prop, sport));
    }
  }

  function closePropModal() {
    if (!_overlay) return;
    _overlay.classList.add("hidden");
    document.body.classList.remove("player-prop-modal-open");
    _lastFocus?.focus?.();
  }

  /** Player profile modal (team page) — shares overlay shell. */
  async function openPlayerProfileModal(sport, playerId, playerName) {
    const overlay = ensureOverlay();
    _lastFocus = document.activeElement;
    overlay.classList.remove("hidden");
    document.body.classList.add("player-prop-modal-open");
    const body = overlay.querySelector(".player-prop-modal__body");
    body.innerHTML = skeletonHtml();

    try {
      const res = await fetch(
        `/api/players/${encodeURIComponent(sport)}/${encodeURIComponent(playerId)}/profile`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.status === "unsupported") {
        body.innerHTML = `<div class="empty-state-card"><p>${data.message || "Coming soon for this sport."}</p></div>`;
        return;
      }
      const photo = data.photo_url
        ? `<img class="prop-modal-photo" src="${data.photo_url}" alt="" width="64" height="64">`
        : "";
      const recent = (data.recent_games || [])
        .map(
          (g) =>
            `<tr><td>${g.date || "—"}</td><td>${g.opponent || "—"}</td><td>${g.summary || g.stat_value || "—"}</td></tr>`
        )
        .join("");
      const props = (data.available_props || [])
        .map((p) => {
          const side = p.recommended_side === "under" ? "U" : "O";
          return `<li>${p.market_label}: ${side}${p.line} (${fmtOdds(p.recommended_odds)})</li>`;
        })
        .join("");
      body.innerHTML = `
        <header class="prop-modal-head">
          ${photo}
          <div>
            <h2 id="player-prop-modal-title">${data.name || playerName}</h2>
            <p class="prop-modal-market">${data.position || ""} · ${data.season || ""} season</p>
          </div>
        </header>
        <section class="prop-modal-log">
          <h3>Recent games</h3>
          <table class="prop-modal-table"><thead><tr><th>Date</th><th>Opp</th><th>Line</th></tr></thead>
          <tbody>${recent || '<tr><td colspan="3">No recent games</td></tr>'}</tbody></table>
        </section>
        <section class="prop-modal-log">
          <h3>Today's props</h3>
          <ul class="prop-modal-props-list">${props || "<li>No props posted today</li>"}</ul>
        </section>`;
    } catch {
      body.innerHTML = `<div class="empty-state-card"><p>Could not load player profile.</p></div>`;
    }
  }

  global.openPropModal = openPropModal;
  global.closePropModal = closePropModal;
  global.openPlayerProfileModal = openPlayerProfileModal;
})(window);
