/**

 * NTG Sports — Home dashboard rendering (matches production dashboard mockup).

 */

(function () {

  "use strict";



  const GOLD_STAR_SVG =

    '<svg class="dash-icon-star" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2l2.9 6.9L22 10l-5.5 4.7L18.5 22 12 18.3 5.5 22l2-7.3L2 10l7.1-1.1L12 2z"/></svg>';



  function fmtPct(rate) {

    if (rate == null || Number.isNaN(rate)) return "—";

    return `${Math.round(rate * 100)}%`;

  }



  function chartSeriesPolyline(values, width, height, pad = 4) {

    const nums = (values || [])

      .filter((v) => v != null && !Number.isNaN(Number(v)))

      .map(Number);

    if (!nums.length) return null;

    const min = Math.min(...nums);

    const max = Math.max(...nums);

    const span = max - min || 1;

    const innerW = width - pad * 2;

    const innerH = height - pad * 2;

    const step = nums.length > 1 ? innerW / (nums.length - 1) : 0;

    const points = nums

      .map((v, i) => {

        const x = pad + i * step;

        const y = pad + innerH * (1 - (v - min) / span);

        return `${x.toFixed(1)},${y.toFixed(1)}`;

      })

      .join(" ");

    return { points, latest: nums[nums.length - 1] };

  }



  function avgEdge(singles) {

    const rows = (singles || []).filter((p) => p.edge != null);

    if (!rows.length) return null;

    return rows.reduce((s, p) => s + Number(p.edge), 0) / rows.length;

  }



  function findGameById(games, gameId) {

    if (!gameId) return null;

    return (games || []).find((g) => String(g.game_id) === String(gameId)) || null;

  }



  function teamAbbr(game, side) {

    if (!game) return "—";

    const abbr = side === "away" ? game.away_team_abbr : game.home_team_abbr;

    if (abbr) return String(abbr).toUpperCase();

    const name = side === "away" ? game.away_team : game.home_team;

    return (name || "—").split(" ").pop().toUpperCase();

  }



  function gameLogo(game, side) {

    if (!game || typeof logoForGame !== "function") return "";

    return logoForGame(game, side);

  }



  function teamLogoChip(game, side, size) {

    const px = size || 28;

    const logo = gameLogo(game, side);

    const abbr = teamAbbr(game, side);

    return `<span class="dash-logo-chip" title="${side === "away" ? game.away_team : game.home_team}">

      <img src="${logo}" alt="" width="${px}" height="${px}" loading="lazy" />

      <span class="dash-logo-abbr">${abbr}</span>

    </span>`;

  }



  function matchupLogoPair(game, size) {

    if (!game) return "";

    return `<span class="dash-logo-pair">${teamLogoChip(game, "away", size)}${teamLogoChip(game, "home", size)}</span>`;

  }



  function pickSideLogo(pick, games, size) {

    const game = findGameById(games, pick?.game_id);

    if (!game) {

      const fallback = (pick?.team || "?").split(" ").pop().toUpperCase();

      return `<span class="dash-logo-chip dash-logo-chip-fallback"><span class="dash-logo-abbr">${fallback}</span></span>`;

    }

    const side = pick.side === "away" ? "away" : "home";

    return teamLogoChip(game, side, size || 28);

  }



  function propTeamLogo(prop, games, size) {

    const game = findGameById(games, prop?.game_id);

    if (!game) return "";

    const px = size || 32;

    const teamName = (prop.team || "").toLowerCase();

    let side = "home";

    if (teamName && game.away_team && game.away_team.toLowerCase().includes(teamName.split(" ").pop())) {

      side = "away";

    }

    return `<img class="dash-prop-team-logo" src="${gameLogo(game, side)}" alt="" width="${px}" height="${px}" loading="lazy" />`;

  }



  function playerPhotoHtml(prop, size) {

    const px = size || 40;

    if (prop?.photo_url) {

      const initials = (prop.player || "?")

        .split(" ")

        .map((w) => w[0])

        .join("")

        .slice(0, 2)

        .toUpperCase();

      return `<img class="dash-player-photo" src="${prop.photo_url}" alt="" width="${px}" height="${px}" loading="lazy" data-fallback="${initials}" />`;

    }

    return propTeamLogo(prop, null, px);

  }



  function propSideFormRates(prop, side) {

    const over = (side || prop?.recommended_side || "over") === "over";

    return {

      l5: over ? prop.hit_rate_over_l5 : prop.hit_rate_under_l5,

      l10: over ? prop.hit_rate_over_l10 : prop.hit_rate_under_l10,

      season: over ? prop.hit_rate_over_season : prop.hit_rate_under_season,

    };

  }



  function propFormComposite(prop) {

    const { l5, l10, season } = propSideFormRates(prop, prop.recommended_side);

    const vals = [l5, l10, season].filter((r) => r != null);

    if (!vals.length) return prop.recommended_hit_rate ?? 0;

    return vals.reduce((s, r) => s + Number(r), 0) / vals.length;

  }



  function pickFormComposite(pick) {

    const vals = [pick.win_rate_l5, pick.win_rate_l10, pick.win_rate_season].filter(

      (r) => r != null

    );

    if (!vals.length) return pick.model_prob ?? 0;

    return vals.reduce((s, r) => s + Number(r), 0) / vals.length;

  }



  function formChip(label, rate) {

    return `<span class="dash-form-chip"><span class="dash-form-chip-lbl">${label}</span> ${fmtPct(rate)}</span>`;

  }



  function pickFormRow(pick) {

    return `<span class="dash-form-row">${formChip("L5", pick.win_rate_l5)}${formChip("L10", pick.win_rate_l10)}${formChip("Szn", pick.win_rate_season)}</span>`;

  }



  function propFormRow(prop) {

    const { l5, l10, season } = propSideFormRates(prop, prop.recommended_side);

    return `<span class="dash-form-row">${formChip("L5", l5)}${formChip("L10", l10)}${formChip("Szn", season)}</span>`;

  }



  function dedupeProps(props) {

    const seen = new Set();

    const out = [];

    for (const p of props || []) {

      const key = `${p.player}|${p.market_type}|${p.line}|${p.recommended_side}`;

      if (seen.has(key)) continue;

      seen.add(key);

      out.push(p);

    }

    return out;

  }



  function allRankableProps(propsData) {

    return dedupeProps([

      ...(propsData?.very_strong_props || []),

      ...(propsData?.top_props || []),

    ]).filter((p) => p.recommended_side && p.recommended_odds != null);

  }



  function topPropsByForm(propsData, limit) {

    return allRankableProps(propsData)

      .slice()

      .sort((a, b) => propFormComposite(b) - propFormComposite(a))

      .slice(0, limit);

  }



  function parseGameClock(game) {

    const live = typeof isGameLive === "function" && isGameLive(game?.status);

    if (!live) {

      const status =

        typeof gameStatusText === "function" ? gameStatusText(game) : game?.status || "—";

      return { period: status, clock: "", showLive: false };

    }

    const label = game.period_label || "";

    const clockMatch = label.match(/\d{1,2}:\d{2}/);

    const clock = clockMatch ? clockMatch[0] : "";

    let period = label.replace(clock, "").replace(/^\s*[-·,]\s*/, "").trim();

    if (!period && label) period = label;

    return { period: period.toUpperCase(), clock, showLive: true };

  }



  function updateDashboardLiveBadge(games) {

    const badge = document.getElementById("dash-live-head-badge");

    if (!badge) return;

    const anyLive = (games || []).some(

      (g) => typeof isGameLive === "function" && isGameLive(g.status)

    );

    badge.classList.toggle("dash-live-head-badge--active", anyLive);

    badge.classList.toggle("dash-live-head-badge--idle", !anyLive);

  }



  function renderDashboardMetrics(el, summary, scoreCounts, extras) {

    if (!el) return;

    const mlb = scoreCounts?.mlb ?? 0;

    const nba = scoreCounts?.nba ?? 0;

    const cfb = scoreCounts?.cfb ?? 0;

    const gamesToday = summary?.board_available

      ? summary.games_on_slate ?? mlb + nba + cfb

      : mlb + nba + cfb;

    const evCount = summary?.board_available ? summary.plus_ev_singles ?? 0 : "—";

    const formAvg =

      summary?.top_singles?.length > 0

        ? pickFormComposite(summary.top_singles[0])

        : null;

    const edgeLabel =

      formAvg != null ? `Top form: ${fmtPct(formAvg)} avg` : "Today's slate";

    const veryStrong = extras?.veryStrongCount ?? "—";

    const hitRate = extras?.hitRate != null ? fmtPct(extras.hitRate) : "—";



    el.innerHTML = `

      <div class="dash-metrics-grid">

        <div class="dash-metric">

          <span class="dash-metric-icon" aria-hidden="true">📅</span>

          <div class="dash-metric-body">

            <span class="dash-metric-num">${gamesToday}</span>

            <span class="dash-metric-lbl">Games</span>

            <span class="dash-metric-sub">Today's Slate</span>

          </div>

        </div>

        <div class="dash-metric">

          <span class="dash-metric-icon dash-metric-icon-ev" aria-hidden="true">📈</span>

          <div class="dash-metric-body">

            <span class="dash-metric-num dash-metric-num-ev">${evCount}</span>

            <span class="dash-metric-lbl">+EV Picks</span>

            <span class="dash-metric-sub">${edgeLabel}</span>

          </div>

        </div>

        <div class="dash-metric dash-metric-gold">

          <span class="dash-metric-icon dash-metric-icon-star">${GOLD_STAR_SVG}</span>

          <div class="dash-metric-body">

            <span class="dash-metric-num">${veryStrong}</span>

            <span class="dash-metric-lbl">Very Strong</span>

            <span class="dash-metric-sub">100% Form</span>

          </div>

        </div>

        <div class="dash-metric">

          <span class="dash-metric-icon" aria-hidden="true">◎</span>

          <div class="dash-metric-body">

            <span class="dash-metric-num">${hitRate}</span>

            <span class="dash-metric-lbl">Hit Rate</span>

            <span class="dash-metric-sub">Last 30 Days</span>

          </div>

        </div>

      </div>`;

  }



  function renderDashboardHeroWidgets(el, { games, summary, propsData, charts }) {

    if (!el) return;

    const gameList = games || [];

    const liveGame = gameList.find((g) => typeof isGameLive === "function" && isGameLive(g.status));

    const featured =

      liveGame ||

      gameList.find(

        (g) =>

          typeof isGameFinal !== "function" ||

          (!isGameFinal(g.status) && !(typeof isGameLive === "function" && isGameLive(g.status)))

      );



    let liveHtml = `<div class="dash-widget dash-widget-live">

      <span class="dash-widget-label">Live Score</span>

      <p class="dash-widget-title">No live games</p>

      <span class="dash-widget-meta">Check the slate</span>

    </div>`;

    if (featured) {

      const href = typeof gameDetailHref === "function" ? gameDetailHref(featured) : "#";

      const score =

        featured.away_score != null && featured.home_score != null

          ? `${featured.away_score} – ${featured.home_score}`

          : typeof formatLocalTimeShort === "function"

            ? formatLocalTimeShort(featured.start_time_utc)

            : "—";

      const clock = parseGameClock(featured);

      const liveCls =

        typeof isGameLive === "function" && isGameLive(featured.status) ? " dash-widget--live" : "";

      liveHtml = `<a class="dash-widget dash-widget-live${liveCls}" href="${href}">

        <span class="dash-widget-label">Live Score</span>

        <div class="dash-widget-teams">${matchupLogoPair(featured, 26)}</div>

        <span class="dash-widget-score">${score}</span>

        <span class="dash-widget-meta">${clock.period}${clock.clock ? ` · ${clock.clock}` : ""}</span>

      </a>`;

    }



    const prop = (propsData?.very_strong_props || propsData?.top_props || [])[0];

    let propHtml = `<div class="dash-widget dash-widget-gold">

      <div class="dash-widget-gold-head">${GOLD_STAR_SVG}<span class="dash-widget-label">Very Strong</span></div>

      <p class="dash-widget-title">Loading props…</p>

    </div>`;

    if (prop) {

      const side = prop.recommended_side || "over";

      const odds = typeof fmtAmericanOdds === "function" ? fmtAmericanOdds(prop.recommended_odds) : "—";

      const href = prop.game_id ? `/mlb/game/${encodeURIComponent(prop.game_id)}` : "/mlb/props";

      propHtml = `<a class="dash-widget dash-widget-gold" href="${href}">

        <div class="dash-widget-gold-head">${GOLD_STAR_SVG}<span class="dash-widget-label">Very Strong</span></div>

        <div class="dash-widget-gold-body">

          ${playerPhotoHtml(prop, 40)}

          ${propTeamLogo(prop, gameList, 28)}

          <div class="dash-widget-gold-copy">

            <span class="dash-widget-form-badge">100% FORM</span>

            <p class="dash-widget-title">${prop.player}</p>

            <span class="dash-widget-meta">${prop.market_label || prop.market_type} ${side} ${prop.line} · ${odds}</span>

          </div>

        </div>

      </a>`;

    }



    const pick = (summary?.top_singles || [])[0];

    let evHtml = `<div class="dash-widget">

      <span class="dash-widget-label">Top Form Pick</span>

      <p class="dash-widget-title">Loading slate…</p>

    </div>`;

    if (pick) {

      const formPct = fmtPct(pickFormComposite(pick));

      const odds =

        pick.american_odds > 0 ? `+${pick.american_odds}` : pick.american_odds ?? "—";

      const href = pick.game_id ? `/mlb/game/${encodeURIComponent(pick.game_id)}` : "/mlb";

      evHtml = `<a class="dash-widget dash-widget-ev" href="${href}">

        <span class="dash-widget-label">Top Form Pick</span>

        <div class="dash-widget-ev-body">

          ${pickSideLogo(pick, gameList, 32)}

          <div class="dash-widget-ev-copy">

            <span class="dash-form-badge">${formPct} form</span>

            <span class="dash-widget-meta">${odds} · ${pickFormRow(pick)}</span>

          </div>

        </div>

      </a>`;

    }



    const mvPoints = charts?.model_vs_market?.points || [];

    let modelPct = 58;

    let marketPct = 52;

    let modelLine = "";

    let marketLine = "";

    if (mvPoints.length) {

      const modelSeries = chartSeriesPolyline(

        mvPoints.map((p) => p.model_pct),

        120,

        48

      );

      const marketSeries = chartSeriesPolyline(

        mvPoints.map((p) => p.market_pct),

        120,

        48

      );

      if (modelSeries) modelLine = modelSeries.points;

      if (marketSeries) marketLine = marketSeries.points;

      const last = mvPoints[mvPoints.length - 1];

      modelPct = Math.round(Number(last.model_pct));

      marketPct = Math.round(Number(last.market_pct));

    } else {

      const boardRow = summary?.slate_by_game_id

        ? Object.values(summary.slate_by_game_id)[0]

        : null;

      if (boardRow?.model_prob_home != null) {

        modelPct = Math.round(Number(boardRow.model_prob_home) * 100);

      }

      if (boardRow?.market_prob_home != null) {

        marketPct = Math.round(Number(boardRow.market_prob_home) * 100);

      }

    }

    const chartHtml = `<div class="dash-widget dash-widget-chart">

      <span class="dash-widget-label">Model vs Market</span>

      <svg class="dash-mini-chart" viewBox="0 0 120 48" aria-hidden="true">

        ${marketLine ? `<polyline class="dash-chart-market" points="${marketLine}" fill="none" stroke-width="2"/>` : ""}

        ${modelLine ? `<polyline class="dash-chart-model" points="${modelLine}" fill="none" stroke-width="2"/>` : ""}

      </svg>

      <div class="dash-chart-legend">

        <span><i class="dash-legend-model"></i> Model ${modelPct}%</span>

        <span><i class="dash-legend-market"></i> Market ${marketPct}%</span>

      </div>

    </div>`;



    el.innerHTML = liveHtml + propHtml + evHtml + chartHtml;

  }



  function rowFormComposite(p) {
    if (p.bet_type === "prop" || p.player) return propFormComposite(p);
    return pickFormComposite(p);
  }

  function renderDashboardBestBets(el, picks, games) {

    if (!el) return;

    const rows = picks || [];

    if (!rows.length) {

      el.innerHTML = `<p class="dash-empty">Loading today's top player props…</p>`;

      return;

    }

    el.innerHTML = rows

      .slice(0, 5)

      .map((p) => {

        const isProp = p.bet_type === "prop" || Boolean(p.player);

        const formPct = fmtPct(rowFormComposite(p));

        const href = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb/props";

        if (isProp) {

          const side = p.recommended_side || "over";

          const odds =

            typeof fmtAmericanOdds === "function"

              ? fmtAmericanOdds(p.recommended_odds)

              : "—";

          const game = findGameById(games, p.game_id);

          const matchup = game

            ? `<span class="dash-bet-matchup-logos">${teamLogoChip(game, "away", 20)}<span class="dash-bet-at">@</span>${teamLogoChip(game, "home", 20)}</span>`

            : "";

          return `<a class="dash-bet-row dash-bet-row-prop" href="${href}">

          <div class="dash-bet-main">

            <div class="dash-bet-team">${playerPhotoHtml(p, 36)}${propTeamLogo(p, games, 22)}</div>

            <strong class="dash-bet-player">${p.player}</strong>

            <span class="dash-bet-line">${p.market_label || p.market_type} ${side} ${p.line}</span>

            ${propFormRow(p)}

            ${matchup}

          </div>

          <span class="dash-form-badge">${formPct}</span>

          <span class="dash-bet-odds">${odds}</span>

          <span class="dash-bet-prob">prop</span>

        </a>`;

        }

        const odds =

          p.american_odds > 0 ? `+${p.american_odds}` : p.american_odds ?? "—";

        const prob =

          p.model_prob != null

            ? fmtPct(p.model_prob)

            : p.win_prob != null

              ? fmtPct(p.win_prob)

              : "—";

        const game = findGameById(games, p.game_id);

        const matchup = game

          ? `<span class="dash-bet-matchup-logos">${teamLogoChip(game, "away", 20)}<span class="dash-bet-at">@</span>${teamLogoChip(game, "home", 20)}</span>`

          : "";

        return `<a class="dash-bet-row dash-bet-row-ml" href="${href}">

          <div class="dash-bet-main">

            <div class="dash-bet-team">${pickSideLogo(p, games, 30)}</div>

            ${pickFormRow(p)}

            ${matchup}

          </div>

          <span class="dash-form-badge">${formPct}</span>

          <span class="dash-bet-odds">${odds}</span>

          <span class="dash-bet-prob">${prob} model</span>

        </a>`;

      })

      .join("");

  }



  function renderDashboardLiveBoard(el, games) {

    if (!el) return;

    updateDashboardLiveBadge(games);

    const sorted = (games || []).slice().sort((a, b) => {

      const liveA = typeof isGameLive === "function" && isGameLive(a.status) ? 0 : 1;

      const liveB = typeof isGameLive === "function" && isGameLive(b.status) ? 0 : 1;

      if (liveA !== liveB) return liveA - liveB;

      return new Date(a.start_time_utc || 0) - new Date(b.start_time_utc || 0);

    });

    if (!sorted.length) {

      el.innerHTML = `<p class="dash-empty">No games on the board today.</p>`;

      return;

    }

    el.innerHTML = sorted

      .slice(0, 8)

      .map((g, idx) => {

        const href = typeof gameDetailHref === "function" ? gameDetailHref(g) : "#";

        const hasScore = g.away_score != null && g.home_score != null;

        const score = hasScore

          ? `${g.away_score} – ${g.home_score}`

          : typeof formatLocalTimeShort === "function"

            ? formatLocalTimeShort(g.start_time_utc)

            : "—";

        const clock = parseGameClock(g);

        const liveBadge = clock.showLive

          ? `<span class="dash-live-badge">● LIVE</span>`

          : `<span class="dash-live-badge dash-live-badge-off"> </span>`;

        return `<a class="dash-live-row${clock.showLive ? " dash-live-row--active" : ""}" href="${href}">

          <span class="dash-live-idx">${idx + 1}</span>

          <div class="dash-live-teams">${matchupLogoPair(g, 24)}</div>

          <span class="dash-live-score">${score}</span>

          <div class="dash-live-time">

            <span class="dash-live-period">${clock.period}</span>

            ${clock.clock ? `<span class="dash-live-clock">${clock.clock}</span>` : ""}

          </div>

          ${liveBadge}

        </a>`;

      })

      .join("");

  }



  function renderDashboardParlayPreview(el, propsData, games) {

    if (!el) return;

    const legs = topPropsByForm(propsData, 3);

    if (!legs.length) {

      el.innerHTML = `<p class="dash-parlay-empty">Loading today's top form props…</p>`;

      return;

    }

    const decimal =

      typeof clientParlayDecimal === "function"

        ? clientParlayDecimal(

            legs.map((p) => ({

              american_odds: p.recommended_odds,

            }))

          )

        : 1;

    let american = "—";

    if (decimal >= 2) american = `+${Math.round((decimal - 1) * 100)}`;

    else if (decimal > 1) american = `${Math.round(-100 / (decimal - 1))}`;



    const legHtml = legs

      .map((prop, i) => {

        const side = prop.recommended_side || "over";

        const sideLetter = side === "under" ? "U" : "O";

        const href = prop.game_id

          ? `/mlb/game/${encodeURIComponent(prop.game_id)}`

          : "/mlb/props";

        return `<a class="dash-parlay-leg-card" href="${href}">

          ${playerPhotoHtml(prop, 36)}

          ${propTeamLogo(prop, games, 22)}

          <span class="dash-parlay-leg-name">${prop.player}</span>

          <span class="dash-parlay-leg-line">${prop.market_label || prop.market_type} ${sideLetter}${prop.line}</span>

        </a>${i < legs.length - 1 ? '<span class="dash-parlay-plus">+</span>' : ""}`;

      })

      .join("");



    el.innerHTML = `

      <p class="dash-parlay-sublabel">Auto-built from best L5 · L10 · season form today</p>

      <div class="dash-parlay-legs">${legHtml}</div>

      <div class="dash-parlay-foot">

        <div class="dash-parlay-odds">

          <span class="dash-parlay-odds-lbl">Parlay Odds</span>

          <strong>${american}</strong>

        </div>

        <a class="dash-btn dash-btn-sm dash-btn-primary" href="/mlb/props">View props</a>

      </div>`;

  }



  function renderDashboardPerformance(el, trackerSummary, perfSummary, charts) {

    if (!el) return;

    const pt = trackerSummary || perfSummary?.prop_tracker || {};

    const trend = charts?.performance_trend || perfSummary?.charts?.performance_trend || {};

    const series = trend.series || [];

    const hitRate = pt.overall_hit_rate ?? (trend.overall_hit_rate_pct != null ? trend.overall_hit_rate_pct / 100 : null);

    const settled = pt.props_settled ?? trend.settled ?? 0;

    const hrPct =

      hitRate != null

        ? Math.round(hitRate * 100)

        : trend.overall_hit_rate_pct != null

          ? Math.round(trend.overall_hit_rate_pct)

          : null;

    const roiPct =

      trend.overall_roi_pct != null

        ? Number(trend.overall_roi_pct).toFixed(1)

        : null;

    const hitSeries = chartSeriesPolyline(

      series.map((row) => row.hit_rate_pct),

      200,

      80

    );

    const roiSeries = chartSeriesPolyline(

      series.map((row) => row.roi_pct),

      200,

      80

    );

    const hitLine = hitSeries?.points || "";

    const roiLine = roiSeries?.points || "";

    const hrDisplay = hrPct != null ? `${hrPct}%` : "—";

    const roiDisplay = roiPct != null ? `${Number(roiPct) >= 0 ? "+" : ""}${roiPct}%` : "—";



    el.innerHTML = `

      <div class="dash-perf-layout">

        <div class="dash-perf-chart-wrap">

          <svg class="dash-perf-chart" viewBox="0 0 200 80" aria-hidden="true">

            ${hitLine ? `<polyline class="dash-perf-line-hit" points="${hitLine}" fill="none" stroke-width="2.5"/>` : ""}

            ${roiLine ? `<polyline class="dash-perf-line-roi" points="${roiLine}" fill="none" stroke-width="2.5"/>` : ""}

          </svg>

          <div class="dash-perf-chart-labels">

            <span>Hit Rate (${trend.days || 30}D)</span>

            <span>ROI (${trend.days || 30}D)</span>

          </div>

        </div>

        <div class="dash-perf-stats">

          <div class="dash-perf-stat">

            <span class="dash-perf-stat-num">${hrDisplay}</span>

            <span class="dash-perf-stat-lbl">Hit Rate</span>

          </div>

          <div class="dash-perf-stat dash-perf-stat-gold">

            <span class="dash-perf-stat-num">${roiDisplay}</span>

            <span class="dash-perf-stat-lbl">ROI</span>

          </div>

        </div>

        ${settled ? `<p class="dash-perf-footnote">${settled} graded props</p>` : `<p class="dash-perf-footnote">Grading starts after games finish</p>`}

      </div>`;

  }



  function renderDashboardGoldBand(el, props, games) {

    if (!el) return;

    const rows = (props || []).slice(0, 3);

    if (!rows.length) {

      el.innerHTML = "";

      return;

    }

    el.innerHTML = rows

      .map((p) => {

        const side = p.recommended_side || "over";

        const odds = typeof fmtAmericanOdds === "function" ? fmtAmericanOdds(p.recommended_odds) : "—";

        const href = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb/props";

        return `<a class="dash-gold-card" href="${href}">

          <span class="dash-gold-form">${GOLD_STAR_SVG} 100% FORM</span>

          <div class="dash-gold-card-body">

            ${playerPhotoHtml(p, 44)}

            ${propTeamLogo(p, games, 28)}

            <div class="dash-gold-card-copy">

              <strong>${p.player}</strong>

              <span>${p.market_label || p.market_type} ${side} ${p.line}</span>

              <span class="dash-gold-odds">${odds}</span>

            </div>

          </div>

        </a>`;

      })

      .join("");

  }



  function renderDashboardEdgeScroll(el, singles, props, games) {

    if (!el) return;

    const items = [];

    dedupeProps(props || [])

      .slice(0, 10)

      .forEach((p) => items.push({ type: "prop", data: p }));

    (singles || []).slice(0, 6).forEach((p) => items.push({ type: "single", data: p }));



    if (!items.length) {

      el.innerHTML = `<p class="dash-edge-empty">No picks on today's board yet.</p>`;

      return;

    }



    el.innerHTML = items

      .map(({ type, data: p }) => {

        if (type === "prop") {

          const side = p.recommended_side || "over";

          const odds = typeof fmtAmericanOdds === "function" ? fmtAmericanOdds(p.recommended_odds) : "—";

          const href = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb/props";

          const { l10 } = propSideFormRates(p, side);

          const formLabel = l10 != null ? `${fmtPct(l10)} L10` : "—";

          const gold =

            typeof propEffectiveStrength === "function" &&

            propEffectiveStrength(p).line_strength === "very_strong";

          return `<a class="dash-edge-card${gold ? " dash-edge-card-gold" : ""}" href="${href}">

            <div class="dash-edge-card-top">

              ${playerPhotoHtml(p, 48)}

              ${propTeamLogo(p, games, 24)}

            </div>

            <span class="dash-edge-player">${p.player}</span>

            <span class="dash-edge-line">${p.market_label || p.market_type} ${side} ${p.line}</span>

            <span class="dash-form-badge dash-form-badge-sm">${formLabel} form</span>

            <span class="dash-edge-odds">${odds}</span>

          </a>`;

        }

        const game = findGameById(games, p.game_id);

        const formPct = fmtPct(pickFormComposite(p));

        const href = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb";

        return `<a class="dash-edge-card" href="${href}">

          ${pickSideLogo(p, games, 36)}

          <span class="dash-edge-player">${teamAbbr(game, p.side === "away" ? "away" : "home")}</span>

          <span class="dash-edge-line">${game ? `${teamAbbr(game, "away")} @ ${teamAbbr(game, "home")}` : "ML pick"}</span>

          <span class="dash-form-badge dash-form-badge-sm">${formPct} form</span>

        </a>`;

      })

      .join("");

  }



  window.renderDashboardMetrics = renderDashboardMetrics;

  window.renderDashboardHeroWidgets = renderDashboardHeroWidgets;

  window.renderDashboardBestBets = renderDashboardBestBets;

  window.renderDashboardLiveBoard = renderDashboardLiveBoard;

  window.renderDashboardParlayPreview = renderDashboardParlayPreview;

  window.renderDashboardPerformance = renderDashboardPerformance;

  window.renderDashboardGoldBand = renderDashboardGoldBand;

  window.renderDashboardEdgeScroll = renderDashboardEdgeScroll;

  window.updateDashboardLiveBadge = updateDashboardLiveBadge;

})();


