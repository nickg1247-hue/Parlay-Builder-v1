/**
 * NTG Sports design system — typography, stadium hero, data viz, density, motion.
 */
(function () {
  const DENSITY_KEY = "ntg-density";
  let _stadiumMap = null;
  let _stadiumIndex = 0;
  let _stadiumTimer = null;
  const _lastTickerScores = new Map();

  const SPORT_ACCENTS = {
    mlb: "#2563eb",
    nba: "#ea580c",
    cfb: "#dc2626",
    nfl: "#059669",
    nhl: "#0284c7",
  };

  function sportAccent(sport) {
    return SPORT_ACCENTS[(sport || "mlb").toLowerCase()] || SPORT_ACCENTS.mlb;
  }

  async function loadStadiumMap() {
    if (_stadiumMap) return _stadiumMap;
    try {
      _stadiumMap = await fetch("/static/stadiums.json").then((r) => r.json());
    } catch {
      _stadiumMap = { defaults: {}, mlb: {}, names: { mlb: {} } };
    }
    return _stadiumMap;
  }

  function stadiumSlideForGame(game) {
    const sport = (game.sport || "mlb").toLowerCase();
    const id = String(game.home_team_id || "");
    const map = _stadiumMap || { defaults: {}, mlb: {}, names: { mlb: {} } };
    const url =
      (map[sport] && map[sport][id]) ||
      map.defaults?.[sport] ||
      map.defaults?.mlb ||
      "";
    const venue =
      (map.names?.[sport] && map.names[sport][id]) ||
      `${game.home_team || "Home"} venue`;
    return { url, venue, sport, team: game.home_team };
  }

  function uniqueHomeVenues(games) {
    const seen = new Set();
    const slides = [];
    for (const g of games || []) {
      const key = `${g.sport || "mlb"}:${g.home_team_id || g.home_team}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const slide = stadiumSlideForGame(g);
      if (slide.url) slides.push(slide);
    }
    return slides;
  }

  async function initStadiumHero(games) {
    const hero = document.querySelector(".home-hero-stadium");
    if (!hero) return;
    await loadStadiumMap();
    const slides = uniqueHomeVenues(games);
    if (!slides.length) {
      hero.style.setProperty(
        "--hero-bg",
        `url("${(_stadiumMap?.defaults?.mlb || "").replace(/"/g, "%22")}")`
      );
      hero.querySelector(".home-hero-venue")?.replaceChildren();
      return;
    }

    const layers = hero.querySelector(".home-hero-stadium-layers");
    const caption =
      document.getElementById("hero-venue") || hero.querySelector(".home-hero-venue");
    if (!layers) return;

    function showSlide(i) {
      const slide = slides[i % slides.length];
      _stadiumIndex = i % slides.length;
      layers.querySelectorAll(".home-hero-stadium-layer").forEach((el, idx) => {
        el.classList.toggle("active", idx === _stadiumIndex % 2);
        if (idx === _stadiumIndex % 2) {
          el.style.backgroundImage = `url("${slide.url.replace(/"/g, "%22")}")`;
        }
      });
      if (caption) {
        caption.textContent = slide.venue;
        caption.style.setProperty("--venue-accent", sportAccent(slide.sport));
      }
      document.documentElement.style.setProperty("--hero-sport-accent", sportAccent(slide.sport));
    }

    if (_stadiumTimer) clearInterval(_stadiumTimer);
    showSlide(0);
    if (slides.length > 1) {
      _stadiumTimer = setInterval(() => showSlide(_stadiumIndex + 1), 9000);
    }
  }

  function initDensityMode() {
    const saved = localStorage.getItem(DENSITY_KEY) || "comfort";
    document.documentElement.dataset.density = saved;

    document.querySelectorAll(".density-toggle").forEach((btn) => {
      btn.textContent = saved === "compact" ? "Comfort" : "Compact";
      btn.setAttribute("aria-pressed", saved === "compact" ? "true" : "false");
      btn.onclick = () => {
        const next = document.documentElement.dataset.density === "compact" ? "comfort" : "compact";
        document.documentElement.dataset.density = next;
        localStorage.setItem(DENSITY_KEY, next);
        btn.textContent = next === "compact" ? "Comfort" : "Compact";
        btn.setAttribute("aria-pressed", next === "compact" ? "true" : "false");
      };
    });
  }

  const THEME_KEY = "ntg_theme";

  function initThemeMode() {
    const saved = localStorage.getItem(THEME_KEY) || "dark";
    document.documentElement.dataset.theme = saved;

    document.querySelectorAll(".theme-toggle").forEach((btn) => {
      const label = saved === "light" ? "Dark" : "Light";
      btn.textContent = label;
      btn.setAttribute("aria-pressed", saved === "light" ? "true" : "false");
      btn.onclick = () => {
        const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
        document.documentElement.dataset.theme = next;
        localStorage.setItem(THEME_KEY, next);
        btn.textContent = next === "light" ? "Dark" : "Light";
        btn.setAttribute("aria-pressed", next === "light" ? "true" : "false");
      };
    });
  }

  function initBrandMarks() {
    const src =
      window.NTG_LOGO_SRC || `/static/assets/ntg-logo.png?v=20260719`;
    document.querySelectorAll(".app-brand").forEach((brand) => {
      if (brand.dataset.brandInit === "1") return;
      brand.dataset.brandInit = "1";
      const dash = document.body.classList.contains("home-dashboard");
      if (dash) {
        brand.innerHTML = `<img class="app-brand-logo app-brand-logo--dash" src="${src}" alt="NTG Sports" width="56" height="56" />`;
      } else {
        const mark = document.createElement("img");
        mark.src = src;
        mark.alt = "";
        mark.className = "app-brand-logo";
        mark.width = 44;
        mark.height = 44;
        const text = document.createElement("span");
        text.className = "app-brand-text";
        text.innerHTML =
          '<span class="app-brand-ntg">NTG</span><span class="app-brand-sports">Sports</span>';
        brand.replaceChildren(mark, text);
      }
      brand.classList.add("app-brand--lockup");
    });
  }

  function applyActiveSportAccent() {
    const path = window.location.pathname || "/";
    let sport = "mlb";
    if (path.startsWith("/nba")) sport = "nba";
    else if (path.startsWith("/cfb")) sport = "cfb";
    else if (path.startsWith("/mlb")) sport = "mlb";
    document.documentElement.dataset.sport = sport;
    document.documentElement.style.setProperty("--sport-accent", sportAccent(sport));
  }

  function initPageTransition() {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    document.body.classList.add("ntg-page-enter");
    window.setTimeout(() => document.body.classList.remove("ntg-page-enter"), 280);
  }

  function resolveTeamColors(game, colors) {
    const map = colors || {};
    return {
      away:
        game.away_color ||
        game.away_team_color ||
        map[game.away_team] ||
        "#64748b",
      home:
        game.home_color ||
        game.home_team_color ||
        map[game.home_team] ||
        "#2563eb",
    };
  }

  function winProbPcts(boardRow) {
    if (!boardRow || boardRow.model_prob_home == null) return null;
    if (boardRow.prediction_data_stale) {
      return { homePct: 50, awayPct: 50, stale: true };
    }
    const homePct = Math.round(Number(boardRow.model_prob_home) * 100);
    return { homePct, awayPct: 100 - homePct, stale: false };
  }

  /** Top color band sized by each team's model win % (replaces separate prob slider). */
  function winProbBandHtml(boardRow, game, colors, extraClass) {
    const pcts = winProbPcts(boardRow);
    const { away: awayColor, home: homeColor } = resolveTeamColors(game, colors);
    const bandClass = [
      "game-card-color-band",
      pcts ? "win-prob-band" : "",
      pcts && pcts.stale ? "win-prob-band-stale" : "",
      extraClass || "",
    ]
      .filter(Boolean)
      .join(" ");
    const ariaHidden = pcts ? "" : ' aria-hidden="true"';

    if (!pcts) {
      return `<div class="${bandClass}"${ariaHidden}></div>`;
    }

    const awayShort = (game.away_team || "Away").split(" ").pop();
    const homeShort = (game.home_team || "Home").split(" ").pop();
    const staleNote = pcts.stale ? " (data stale)" : "";
    const awayLabel = pcts.stale ? "~50%" : `${pcts.awayPct}%`;
    const homeLabel = pcts.stale ? "~50%" : `${pcts.homePct}%`;
    return `
      <div class="${bandClass}"
           style="--away-color:${awayColor};--home-color:${homeColor};--away-pct:${pcts.awayPct}%;"
           aria-label="Model win probability${staleNote} ${pcts.stale ? "even" : pcts.awayPct + " percent " + awayShort + ", " + pcts.homePct + " percent " + homeShort}">
        <span class="win-prob-band-label win-prob-band-away">${awayShort} ${awayLabel}</span>
        <span class="win-prob-band-label win-prob-band-home">${homeShort} ${homeLabel}</span>
      </div>`;
  }

  /** @deprecated Use winProbBandHtml — kept for callers not yet updated. */
  function winProbBarHtml(boardRow, game, colors) {
    return winProbBandHtml(boardRow, game, colors);
  }

  function confidenceMeterHtml(boardRow) {
    if (!boardRow) return "";
    const tier = (
      boardRow.model_confidence ||
      boardRow.ml_confidence ||
      "Lean only"
    ).toLowerCase();
    const levels = [
      { key: "toss", label: "Toss-up", match: ["lean only", "blocked"] },
      { key: "lean", label: "Lean", match: ["low"] },
      { key: "strong", label: "Strong", match: ["moderate", "medium", "high"] },
      { key: "elite", label: "Elite", match: ["very high", "extremely high"] },
    ];
    let active = 0;
    levels.forEach((lv, i) => {
      if (lv.match.some((m) => tier.includes(m))) active = i;
    });
    const dots = levels
      .map(
        (lv, i) =>
          `<span class="conf-dot${i <= active ? " conf-dot-on" : ""}" title="${lv.label}"></span>`
      )
      .join("");
    return `<div class="confidence-meter" aria-label="Model confidence">${dots}<span class="conf-label">${levels[active].label}</span></div>`;
  }

  function formSparklineHtml(recentGames) {
    if (!recentGames || !recentGames.length) return "";
    const dots = recentGames
      .slice(0, 5)
      .map((g) => `<span class="form-dot ${g.won ? "form-win" : "form-loss"}" title="${g.won ? "W" : "L"}"></span>`)
      .join("");
    return `<div class="form-sparkline" aria-label="Last five games">${dots}</div>`;
  }

  function propHeatClass(prop) {
    if (!prop) return "";
    const side = prop.recommended_side === "under" ? "under" : "over";
    const l10 =
      side === "over" ? prop.hit_rate_over_l10 : prop.hit_rate_under_l10;
    if (l10 == null) return "prop-heat-neutral";
    if (l10 >= 0.7) return "prop-heat-hot";
    if (l10 >= 0.5) return "prop-heat-warm";
    return "prop-heat-cool";
  }

  function lineMoveArrowHtml(side, lineMove) {
    if (!lineMove) return "";
    const key = side === "away" ? "away_ml" : "home_ml";
    const dir = lineMove[key];
    if (!dir) return "";
    const sym = dir === "up" ? "↑" : "↓";
    return `<span class="line-move line-move-${dir}" title="Line moved ${dir}">${sym}</span>`;
  }

  function renderHomeScoresRail(games, el) {
    if (!el) return;
    const sorted = (games || []).slice().sort((a, b) => {
      const liveA = typeof isGameLive === "function" && isGameLive(a.status) ? 0 : 1;
      const liveB = typeof isGameLive === "function" && isGameLive(b.status) ? 0 : 1;
      if (liveA !== liveB) return liveA - liveB;
      return new Date(a.start_time_utc || 0) - new Date(b.start_time_utc || 0);
    });
    if (!sorted.length) {
      el.innerHTML = `<p class="rail-empty">No games on the board today.</p>`;
      return;
    }
    el.innerHTML = sorted
      .slice(0, 12)
      .map((g) => {
        const sport = (g.sport || "mlb").toUpperCase();
        const away = g.away_score != null ? g.away_score : "";
        const home = g.home_score != null ? g.home_score : "";
        const score =
          away !== "" && home !== "" ? `<span class="rail-score">${away}–${home}</span>` : "";
        const meta =
          typeof gameStatusText === "function" ? gameStatusText(g) : g.status || "";
        const href = typeof gameDetailHref === "function" ? gameDetailHref(g) : "#";
        const liveCls =
          typeof isGameLive === "function" && isGameLive(g.status) ? " rail-live" : "";
        return `
          <a class="scores-rail-item ntg-card${liveCls}" href="${href}" data-sport="${g.sport || "mlb"}">
            <span class="rail-sport">${sport}</span>
            <span class="rail-matchup">${(g.away_team || "").split(" ").pop()} @ ${(g.home_team || "").split(" ").pop()}</span>
            ${score}
            <span class="rail-meta">${meta}</span>
          </a>`;
      })
      .join("");
  }

  function flashTickerScores(games) {
    if (!games) return;
    games.forEach((g) => {
      const key = String(g.game_id);
      const prev = _lastTickerScores.get(key);
      const cur = `${g.away_score}:${g.home_score}`;
      if (prev && prev !== cur) {
        document.querySelectorAll(`.ticker-item[href*="${key}"] .ticker-score`).forEach((el) => {
          el.classList.add("score-flash");
          window.setTimeout(() => el.classList.remove("score-flash"), 700);
        });
      }
      _lastTickerScores.set(key, cur);
    });
  }

  function initGameStickyNav() {
    const nav = document.getElementById("game-sticky-nav");
    if (!nav) return;
    const links = nav.querySelectorAll("a[href^='#']");
    const sections = [...links]
      .map((a) => document.querySelector(a.getAttribute("href")))
      .filter(Boolean);

    links.forEach((link) => {
      link.addEventListener("click", (e) => {
        e.preventDefault();
        const target = document.querySelector(link.getAttribute("href"));
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });

    if (!sections.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const id = entry.target.id;
          links.forEach((l) => {
            l.classList.toggle("active", l.getAttribute("href") === `#${id}`);
          });
        });
      },
      { rootMargin: "-40% 0px -50% 0px", threshold: 0 }
    );
    sections.forEach((s) => observer.observe(s));
  }

  function enhanceBroadcastHeader(headerEl, game, boardRow) {
    if (!headerEl || !game) return;
    headerEl.classList.add("broadcast-header");
    const wrap = headerEl.querySelector(".matchup-header-wrap");
    if (wrap && boardRow && !wrap.querySelector(".confidence-meter")) {
      const meter = document.createElement("div");
      meter.className = "broadcast-confidence";
      meter.innerHTML = confidenceMeterHtml(boardRow);
      wrap.appendChild(meter);
    }
  }

  function initDesignSystem() {
    initDensityMode();
    initThemeMode();
    initBrandMarks();
    applyActiveSportAccent();
    initPageTransition();
    initGameStickyNav();
  }

  window.initDesignSystem = initDesignSystem;
  window.initDensityMode = initDensityMode;
  window.initThemeMode = initThemeMode;
  window.initStadiumHero = initStadiumHero;
  window.renderHomeScoresRail = renderHomeScoresRail;
  window.winProbBandHtml = winProbBandHtml;
  window.winProbBarHtml = winProbBarHtml;
  window.confidenceMeterHtml = confidenceMeterHtml;
  window.formSparklineHtml = formSparklineHtml;
  window.propHeatClass = propHeatClass;
  window.lineMoveArrowHtml = lineMoveArrowHtml;
  window.flashTickerScores = flashTickerScores;
  window.initGameStickyNav = initGameStickyNav;
  window.enhanceBroadcastHeader = enhanceBroadcastHeader;
  window.sportAccent = sportAccent;
})();
