/** UFC fight detail — professional bettor-facing layout. */



(function () {

  const loading = document.getElementById("game-loading");

  const errEl = document.getElementById("game-error");

  const content = document.getElementById("game-content");

  const heroEl = document.getElementById("ufc-fight-hero");

  const headerEl = document.getElementById("ufc-matchup-header");

  const previewEl = document.getElementById("ufc-preview-grid");

  const cardMetaEl = document.getElementById("ufc-card-meta");

  const disclaimerEl = document.getElementById("game-disclaimer");

  const fighterStatsEl = document.getElementById("fighter-stats");

  const betsEl = document.getElementById("fight-bets");

  const cardFightsEl = document.getElementById("card-fights");

  const cardFightsWrap = document.getElementById("card-fights-wrap");



  const parts = window.location.pathname.split("/").filter(Boolean);

  const gameIdx = parts.indexOf("game");

  const fightId = gameIdx >= 0 ? parts[gameIdx + 1] : null;



  if (!fightId) {

    loading.classList.add("hidden");

    errEl.classList.remove("hidden");

    errEl.textContent = "Missing fight id in URL";

    return;

  }



  const dateParam = qs("date");

  const useCache = qs("use_cache") === "true";



  initLiveTicker("live-ticker", { date: dateParam, sport: "ufc" });

  if (typeof initSiteChrome === "function") initSiteChrome();

  if (typeof initDesignSystem === "function") initDesignSystem();

  if (typeof initHeadlineTicker === "function") initHeadlineTicker("headline-ticker");



  const scoresUrl = dateParam

    ? `/api/scores/today?sport=ufc&date=${encodeURIComponent(dateParam)}`

    : "/api/scores/today?sport=ufc";



  let scorePollerStarted = false;

  let lastGame = null;

  let lastInsights = null;



  function boardRowFromInsights(data) {

    if (!data) return null;

    const preview = data.fight_preview || {};

    const ml = data.moneyline || {};

    const probHome = preview.home_win_pct ?? ml.model_prob_home;

    if (probHome == null) return null;

    const probAway = preview.away_win_pct ?? ml.model_prob_away ?? 1 - Number(probHome);

    return { model_prob_home: probHome, model_prob_away: probAway };

  }



  function insightsUrl(refresh) {

    const params = new URLSearchParams();

    if (dateParam) params.set("date", dateParam);

    if (useCache) params.set("use_cache", "true");

    if (refresh) params.set("refresh", "true");

    const q = params.toString();

    return `/api/games/ufc/${encodeURIComponent(fightId)}/insights${q ? `?${q}` : ""}`;

  }



  function fmtPct(prob) {

    if (prob == null) return "—";

    return `${(prob * 100).toFixed(0)}%`;

  }



  function fmtOdds(am) {

    if (am == null) return "—";

    return am > 0 ? `+${am}` : String(am);

  }



  function statusLabel(game) {

    if (typeof gameStatusText === "function") return gameStatusText(game);

    return game?.status || "Scheduled";

  }



  function timeLabel(game) {

    if (isGameLive && isGameLive(game.status) && game.period_label) {

      return game.period_label;

    }

    return typeof formatLocalTime === "function"

      ? formatLocalTime(game.start_time_utc)

      : "";

  }



  function weightClassLabel(wc) {

    if (!wc) return "";

    const base = String(wc).replace(/\s+bout$/i, "").trim();

    return base ? `${base.toUpperCase()} BOUT` : "";

  }



  function boutLabel(game, stats) {

    const wc = weightClassLabel(stats?.weight_class || game.weight_class);

    if (wc) return wc;

    const seg = String(stats?.card_segment || game.card_segment || "").toLowerCase();

    if (seg.includes("main")) return "MAIN EVENT";

    if (seg) return seg.toUpperCase();

    return "";

  }



  function formatFightDay(iso) {

    if (!iso) return "";

    const d = new Date(iso);

    if (Number.isNaN(d.getTime())) return "";

    return d

      .toLocaleDateString(undefined, {

        weekday: "short",

        month: "short",

        day: "numeric",

      })

      .toUpperCase();

  }



  function formatFightClock(iso) {

    if (!iso) return "";

    const d = new Date(iso);

    if (Number.isNaN(d.getTime())) return "";

    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

  }



  function renderHero(game, stats, boardRow) {

    if (!heroEl || !game) return;



    if (typeof applyGamePageWash === "function") applyGamePageWash(game);



    const weightClass = boutLabel(game, stats);

    const eventName = stats?.event_name || game.event_name || "";

    const fightDay = formatFightDay(game.start_time_utc);

    const fightClock = formatFightClock(game.start_time_utc);

    const metaBits = [weightClass, fightDay, fightClock, eventName].filter(Boolean);



    let metaEl = heroEl.querySelector(".ufc-fight-event-meta");

    if (!metaEl) {

      metaEl = document.createElement("p");

      metaEl.className = "ufc-fight-event-meta";

      if (headerEl) heroEl.insertBefore(metaEl, headerEl);

      else heroEl.appendChild(metaEl);

    }

    if (metaBits.length) {

      metaEl.textContent = metaBits.join(" · ");

      metaEl.classList.remove("hidden");

    } else {

      metaEl.classList.add("hidden");

    }



    const gameWithProbs = { ...game, sport: "ufc", ...(boardRow || {}) };

    const portraitOpts = { noFlag: true, portraitSize: 104 };



    if (headerEl && typeof renderMatchupHeader === "function") {

      renderMatchupHeader(headerEl, gameWithProbs, boardRow, portraitOpts);

      return;

    }



    const headerInner =

      typeof matchupHeaderHtml === "function"

        ? matchupHeaderHtml(gameWithProbs, boardRow, portraitOpts)

        : `<p>${game.away_team} vs ${game.home_team}</p>`;



    heroEl.innerHTML = `

      <h1 class="sr-only">${game.away_team} vs ${game.home_team}</h1>

      ${metaBits.length ? `<p class="ufc-fight-event-meta">${metaBits.join(" · ")}</p>` : ""}

      <header class="matchup-header broadcast-header">${headerInner}</header>`;



    const btn = heroEl.querySelector(".watch-btn");

    if (btn && typeof toggleWatch === "function") {

      btn.onclick = (e) => {

        e.preventDefault();

        const on = toggleWatch(btn.dataset.watchId);

        btn.classList.toggle("watched", on);

      };

    }

  }



  function statLine(label, value) {

    return `<div class="ufc-stat-line"><span class="ufc-stat-k">${label}</span><span class="ufc-stat-v">${value}</span></div>`;

  }



  function renderPreview(data) {

    if (!previewEl) return;

    const game = data.game || {};

    const ml = data.moneyline || {};

    const preview = data.fight_preview || {};

    const rounds = preview.rounds_expected || {};

    const stats = data.fighter_stats || {};

    const away = stats.away || {};

    const home = stats.home || {};



    const awayWin =

      preview.away_win_pct != null

        ? fmtPct(preview.away_win_pct)

        : ml.model_prob_away != null

          ? fmtPct(ml.model_prob_away)

          : "—";

    const homeWin =

      preview.home_win_pct != null

        ? fmtPct(preview.home_win_pct)

        : ml.model_prob_home != null

          ? fmtPct(ml.model_prob_home)

          : "—";

    const roundsLabel = rounds.display || rounds.label || "—";



    const pickName = preview.pick || ml.model_pick || "—";

    const pickPct = preview.pick_win_pct != null ? fmtPct(preview.pick_win_pct) : "—";



    previewEl.innerHTML = `

      <div class="ufc-preview-col away">

        ${statLine("Win chance", awayWin)}

        ${statLine("Last 5", away.last5_record || "—")}

        ${statLine("Layoff", away.layoff_label || "—")}

      </div>

      <div class="ufc-preview-pick">

        <span class="ufc-preview-pick-label">Our lean</span>

        <span class="ufc-preview-pick-name">${pickName}</span>

        <span class="ufc-preview-pick-pct">${pickPct}</span>

        <div class="ufc-preview-rounds">

          ${statLine("Rounds expected", roundsLabel)}

        </div>

      </div>

      <div class="ufc-preview-col home">

        ${statLine("Win chance", homeWin)}

        ${statLine("Last 5", home.last5_record || "—")}

        ${statLine("Layoff", home.layoff_label || "—")}

      </div>`;

  }



  function renderFighterStats(stats) {

    if (!fighterStatsEl) return;

    if (!stats) {

      fighterStatsEl.innerHTML = "<p class=\"model-empty\">Fighter info unavailable.</p>";

      return;

    }



    function card(label, corner) {

      if (!corner) return "";

      return `

        <article class="ufc-fighter-card">

          <h3>${label}</h3>

          <dl>

            <div><dt>Record</dt><dd>${corner.record || "—"}</dd></div>

            <div><dt>Country</dt><dd>${corner.country || "—"}</dd></div>

            <div><dt>Last 5</dt><dd>${corner.last5_record || "—"}</dd></div>

            <div><dt>Layoff</dt><dd>${corner.layoff_label || "—"}</dd></div>

          </dl>

        </article>`;

    }



    const meta = [];

    if (stats.weight_class) meta.push(stats.weight_class);

    if (stats.card_segment) meta.push(stats.card_segment);



    if (cardMetaEl) {

      if (meta.length) {

        cardMetaEl.textContent = meta.join(" · ");

        cardMetaEl.classList.remove("hidden");

      } else {

        cardMetaEl.classList.add("hidden");

      }

    }



    fighterStatsEl.innerHTML = `${card("Away", stats.away)}${card("Home", stats.home)}`;

  }



  function renderBets(bets) {

    if (!betsEl) return;

    const singles = bets?.singles || [];

    const props = bets?.props || [];

    let html = "";



    if (singles.length) {

      html += singles

        .map((s) => {

          const tag = s.plus_ev ? `<span class="ufc-lean-tag">Value</span>` : "";

          return `

          <div class="ufc-lean-row">

            <span><strong>${s.fighter}</strong> to win ${fmtOdds(s.american_odds)}${tag}</span>

          </div>`;

        })

        .join("");

    }



    if (props.length) {

      html += props

        .map((p) => {

          const over = p.over_odds != null ? `Over ${fmtOdds(p.over_odds)}` : "";

          const under = p.under_odds != null ? `Under ${fmtOdds(p.under_odds)}` : "";

          const prices = [over, under].filter(Boolean).join(" · ");

          return `

          <div class="ufc-lean-row">

            <span><strong>${p.label || p.market}</strong></span>

            <span>${prices || "—"}</span>

          </div>`;

        })

        .join("");

    }



    if (!html) {

      html = `<p class="model-empty" style="margin:0">No strong leans on this fight right now.</p>`;

    }

    betsEl.innerHTML = html;

  }



  function renderCardFights(fights) {

    if (!cardFightsEl) return;

    if (!fights?.length) {

      if (cardFightsWrap) cardFightsWrap.classList.add("hidden");

      return;

    }

    if (cardFightsWrap) cardFightsWrap.classList.remove("hidden");

    cardFightsEl.innerHTML = fights

      .map((f) => {

        const pick = f.model_pick ? ` <span class="ufc-lean-tag">${f.model_pick}</span>` : "";

        return `<li><a href="${f.href}">${f.matchup || f.fight_id}</a>${pick}</li>`;

      })

      .join("");

  }



  function renderInsights(data) {

    lastGame = { ...data.game, sport: "ufc" };

    lastInsights = data;

    renderHero(lastGame, data.fighter_stats, boardRowFromInsights(data));

    renderPreview(data);

    renderFighterStats(data.fighter_stats);

    renderBets(data.bets);

    renderCardFights(data.card_fights);

    if (disclaimerEl && data.disclaimer) {

      disclaimerEl.textContent = data.disclaimer;

    }

  }



  async function refreshLiveScore() {

    try {

      const data = await fetchJSON(scoresUrl);

      const live = (data.games || []).find(

        (g) => String(g.game_id) === String(fightId)

      );

      if (live && lastGame) {

        lastGame = { ...lastGame, ...live, sport: "ufc" };

        renderHero(lastGame, null, boardRowFromInsights(lastInsights));

      }

    } catch (_) {

      /* keep last hero */

    }

  }



  async function loadInsights(refresh) {

    const data = await fetchJSON(insightsUrl(refresh));

    loading.classList.add("hidden");

    content.classList.remove("hidden");

    renderInsights(data);



    if (!dateParam && !scorePollerStarted) {

      scorePollerStarted = true;

      setInterval(refreshLiveScore, 60000);

    }

  }



  loadInsights(false).catch((e) => {

    loading.classList.add("hidden");

    errEl.classList.remove("hidden");

    errEl.textContent = e.message || "Fight not found";

  });

})();

