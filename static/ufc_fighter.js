/** UFC fighter profile page. */

(function () {
  const loading = document.getElementById("fighter-loading");
  const errEl = document.getElementById("fighter-error");
  const content = document.getElementById("fighter-content");
  const heroEl = document.getElementById("fighter-hero-inner");
  const recentEl = document.getElementById("fighter-recent");
  const weightEl = document.getElementById("fighter-weight-history");
  const nextEl = document.getElementById("fighter-next");

  const parts = window.location.pathname.split("/").filter(Boolean);
  const fighterIdx = parts.indexOf("fighter");
  const slug = fighterIdx >= 0 ? parts[fighterIdx + 1] : null;

  if (!slug) {
    loading.classList.add("hidden");
    errEl.classList.remove("hidden");
    errEl.textContent = "Missing fighter slug in URL";
    return;
  }

  initLiveTicker("live-ticker", { sport: "ufc" });
  if (typeof initSiteChrome === "function") initSiteChrome();
  if (typeof initDesignSystem === "function") initDesignSystem();
  if (typeof initHeadlineTicker === "function") initHeadlineTicker("headline-ticker");

  function fmtOdds(am) {
    if (am == null) return "—";
    return am > 0 ? `+${am}` : String(am);
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function portraitHtml(portrait, name) {
    const headshot = portrait?.headshot_url;
    const flagBg = portrait?.flag_backdrop_url
      ? `<div class="ufc-fighter-flag-bg" style="background-image:url('${portrait.flag_backdrop_url}')"></div>`
      : "";
    const img = headshot
      ? `<img class="ufc-fighter-headshot" src="${headshot}" alt="${name}" width="120" height="120" loading="lazy" onerror="this.classList.add('is-fallback')">`
      : `<div class="ufc-fighter-headshot ufc-fighter-headshot-fallback" aria-hidden="true"></div>`;
    return `<div class="ufc-fighter-portrait ufc-fighter-portrait--profile">${flagBg}${img}</div>`;
  }

  function renderHero(profile) {
    if (!heroEl) return;
    const p = profile.portrait || {};
    const country = p.country ? `<p class="ufc-fighter-country">${p.country}</p>` : "";
    const wc = profile.current_weight_class
      ? `<p class="ufc-fighter-meta">${profile.current_weight_class}</p>`
      : "";
    const elo =
      profile.elo_rating != null
        ? `<div class="ufc-fighter-stat-pill"><span class="label">Elo</span><span class="value">${Math.round(profile.elo_rating)}</span></div>`
        : "";
    document.title = `${profile.name} — UFC — NTG Sports`;
    heroEl.innerHTML = `
      <div class="ufc-fighter-hero-grid">
        ${portraitHtml(p, profile.name)}
        <div class="ufc-fighter-hero-copy">
          <h1>${profile.name}</h1>
          ${country}
          ${wc}
          <div class="ufc-fighter-record-row">
            <div class="ufc-fighter-stat-pill"><span class="label">Record</span><span class="value">${profile.career_record || "—"}</span></div>
            <div class="ufc-fighter-stat-pill"><span class="label">Last 5</span><span class="value">${profile.last5_record || "—"}</span></div>
            ${elo}
          </div>
        </div>
      </div>`;
  }

  function renderRecent(fights) {
    if (!recentEl) return;
    const rows = fights || [];
    if (!rows.length) {
      recentEl.innerHTML = `<li class="model-empty">No completed fights on file.</li>`;
      return;
    }
    recentEl.innerHTML = rows
      .map((f) => {
        const oppHref =
          typeof ufcFighterHref === "function"
            ? ufcFighterHref(f.opponent)
            : `/ufc/fighter/${encodeURIComponent(f.opponent_slug || "")}`;
        const resultCls = f.result === "W" ? "ufc-result-win" : "ufc-result-loss";
        return `<li class="ufc-fighter-recent-row">
          <span class="ufc-fighter-recent-date">${fmtDate(f.date)}</span>
          <span class="${resultCls}">${f.result}</span>
          <a href="${oppHref}" class="ufc-fighter-link">${f.opponent}</a>
          <span class="ufc-fighter-recent-wc">${f.weight_class || ""}</span>
        </li>`;
      })
      .join("");
  }

  function renderWeightHistory(history) {
    if (!weightEl) return;
    const rows = history || [];
    if (!rows.length) {
      weightEl.innerHTML = `<li class="model-empty">No weight-class history on file.</li>`;
      return;
    }
    weightEl.innerHTML = rows
      .map(
        (row) => `<li class="ufc-weight-history-row">
          <span>${row.weight_class}</span>
          <span class="ufc-fighter-recent-date">${fmtDate(row.date)}</span>
        </li>`
      )
      .join("");
  }

  function renderNextFight(next) {
    if (!nextEl) return;
    if (!next) {
      nextEl.innerHTML = `<p class="model-empty">No upcoming fight scheduled on the next card.</p>`;
      return;
    }
    const oppHref =
      typeof ufcFighterHref === "function"
        ? ufcFighterHref(next.opponent)
        : `/ufc/fighter/${encodeURIComponent(next.opponent_slug || "")}`;
    nextEl.innerHTML = `<a class="ufc-fighter-next-card" href="${next.href || "#"}">
      <span class="ufc-fighter-next-label">${next.event_name || "Upcoming bout"}</span>
      <span class="ufc-fighter-next-matchup">vs <strong>${next.opponent}</strong></span>
      <span class="ufc-fighter-next-meta">${fmtDate(next.card_date)} · ${next.weight_class || "—"}</span>
      <span class="ufc-fighter-next-cta">View fight →</span>
    </a>
    <p class="ufc-fighter-next-opp">Opponent profile: <a href="${oppHref}" class="ufc-fighter-link">${next.opponent}</a></p>`;
  }

  async function load() {
    try {
      const res = await fetch(`/api/ufc/fighter/${encodeURIComponent(slug)}`);
      if (!res.ok) {
        throw new Error(res.status === 404 ? "Fighter not found" : `HTTP ${res.status}`);
      }
      const profile = await res.json();
      loading.classList.add("hidden");
      content.classList.remove("hidden");
      renderHero(profile);
      renderRecent(profile.recent_fights);
      renderWeightHistory(profile.weight_class_history);
      renderNextFight(profile.next_fight);
    } catch (err) {
      loading.classList.add("hidden");
      errEl.classList.remove("hidden");
      errEl.textContent = err.message || "Failed to load fighter profile";
    }
  }

  load();
})();
