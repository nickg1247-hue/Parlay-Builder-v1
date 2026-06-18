/** Shared helpers for ESPN-style shell (Phase A). */

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

function teamLogoUrl(teamId, sport = "mlb", abbr) {
  if (sport === "nba") {
    if (abbr) {
      return `https://a.espncdn.com/i/teamlogos/nba/500/scoreboard/${String(abbr).toLowerCase()}.png`;
    }
    return "";
  }
  if (sport === "cfb") {
    if (teamId) {
      return `https://a.espncdn.com/i/teamlogos/ncaa/500/${teamId}.png`;
    }
    return "";
  }
  return `https://www.mlbstatic.com/team-logos/team-cap-on-dark/${teamId}.svg`;
}

function gameSport(game) {
  return game?.sport || "mlb";
}

function gameDetailHref(game, options = {}) {
  const sport = gameSport(game);
  const base = `/${sport}/game/${game.game_id}`;
  const slateDate = options.gameDate || game.slate_date;
  const params = new URLSearchParams();
  if ((sport === "nba" || sport === "cfb") && slateDate) {
    params.set("date", slateDate);
  }
  if (options.useCache) {
    params.set("use_cache", "true");
  }
  const q = params.toString();
  return q ? `${base}?${q}` : base;
}

function logoForGame(game, side) {
  const isAway = side === "away";
  const direct = isAway ? game.away_logo_url : game.home_logo_url;
  if (direct) return direct;
  const teamId = isAway ? game.away_team_id : game.home_team_id;
  const abbr = isAway ? game.away_team_abbr : game.home_team_abbr;
  return teamLogoUrl(teamId, gameSport(game), abbr);
}

function teamRecordHtml(record) {
  if (!record) return "";
  return `<span class="team-record">${record}</span>`;
}

function renderSlateAdvanceBanner(el, slate) {
  if (!el) return;
  if (!slate?.auto_advanced || !(slate.days_ahead > 0)) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  const resolved = slate.resolved_date || slate.date || "";
  const ahead =
    slate.days_ahead === 1
      ? "tomorrow"
      : `${slate.days_ahead} days ahead`;
  el.innerHTML = `<p class="slate-advance-banner">No games on the requested day — showing the next slate (${resolved}, ${ahead}).</p>`;
}

function formatLocalTime(isoUtc) {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatLocalTimeShort(isoUtc) {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatRefreshStatus(status) {
  if (!status) return "Not refreshed yet";

  const displayWhen = status.display_updated_at
    ? formatLocalTime(status.display_updated_at)
    : null;
  const oddsWhen = status.odds_fetched_at
    ? formatLocalTime(status.odds_fetched_at)
    : null;
  const oddsFresh =
    status.odds_seconds_since_fetch != null &&
    status.odds_seconds_since_fetch < 7200;

  if (displayWhen) {
    const games =
      status.games_on_slate != null ? ` · ${status.games_on_slate} games` : "";
    const source =
      status.display_source === "hourly_odds"
        ? " · odds"
        : status.display_source === "props"
          ? " · props"
          : "";
    return `Updated ${displayWhen}${games}${source}`;
  }

  if (status.ok && status.ran_at) {
    const when = formatLocalTime(status.ran_at);
    const games =
      status.games_on_slate != null ? ` · ${status.games_on_slate} games` : "";
    return `Updated ${when}${games}`;
  }

  if (oddsFresh && oddsWhen) {
    return `Odds updated ${oddsWhen}`;
  }

  if (status.ran_at && !status.ok) {
    return `Board refresh failed ${formatLocalTime(status.ran_at)}`;
  }

  if (oddsWhen) {
    return `Odds updated ${oddsWhen}`;
  }

  return status.error || "Not refreshed yet";
}

async function pollRefreshStatusLine(el, intervalMs = 60000) {
  if (!el) return;
  async function tick() {
    try {
      const status = await fetchJSON("/api/status/refresh");
      el.textContent = formatRefreshStatus(status);
      el.classList.toggle("ok", Boolean(status?.ok || status?.display_updated_at));
    } catch (_) {
      /* keep last line */
    }
  }
  await tick();
  window.setInterval(tick, intervalMs);
}

function formatRelativeShort(isoUtc) {
  if (!isoUtc) return "—";
  const d = new Date(isoUtc);
  if (Number.isNaN(d.getTime())) return "—";
  const diffMs = Date.now() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return formatLocalTimeShort(isoUtc);
}

function isGameLive(status) {
  const s = (status || "").toLowerCase();
  return s === "live" || s === "in progress";
}

function isGameFinal(status) {
  const s = (status || "").toLowerCase();
  return s === "final" || s === "game over";
}

function shouldShowScores(game) {
  if (!game) return false;
  if (game.home_score != null || game.away_score != null) {
    return isGameLive(game.status) || isGameFinal(game.status);
  }
  return false;
}

function statusBadgeClass(status) {
  const s = (status || "").toLowerCase();
  if (isGameLive(status)) return "badge-live";
  if (isGameFinal(status)) return "badge-final";
  if (s === "preview" || s === "pre-game" || s === "scheduled") return "badge-scheduled";
  return "badge-default";
}

function statusLabel(status) {
  if (!status) return "Scheduled";
  const s = status.toLowerCase();
  if (s === "in progress" || s === "live") return "Live";
  if (s === "preview" || s === "pre-game") return "Scheduled";
  if (s === "final" || s === "game over") return "Final";
  return status;
}

function gameStatusText(game) {
  if (!game) return "Scheduled";
  if (isGameLive(game.status) && game.period_label) return game.period_label;
  return statusLabel(game.status);
}

function sortGamesByStart(games) {
  return (games || []).slice().sort((a, b) => {
    const ta = new Date(a.start_time_utc || 0).getTime();
    const tb = new Date(b.start_time_utc || 0).getTime();
    return ta - tb;
  });
}

// ~0.32 px/frame ≈ 19 px/s at 60fps — ~10s for ~3 game cards to cross the viewport.
const TICKER_SCROLL_PX_PER_FRAME = 0.32;
const TICKER_RESUME_DEBOUNCE_MS = 500;

function tickerItemHtml(game) {
  const away = game.away_score != null ? game.away_score : "";
  const home = game.home_score != null ? game.home_score : "";
  const score =
    away !== "" && home !== ""
      ? `<span class="ticker-score">${away}–${home}</span>`
      : "";
  const meta = gameStatusText(game);
  const shortAway = game.away_team.split(" ").pop();
  const shortHome = game.home_team.split(" ").pop();
  const sport = gameSport(game).toUpperCase();
  const colorStyle = gameCardColorStyle(game, _teamColors);
  const liveClass = isGameLive(game.status) ? " ticker-item-live" : "";
  return `
    <a class="ticker-item${liveClass}" href="${gameDetailHref(game)}" style="${colorStyle}">
      <span class="ticker-sport">${sport}</span>
      <span class="ticker-teams">${shortAway} @ ${shortHome}</span>
      ${score}
      <span class="ticker-meta">${meta}</span>
    </a>
  `;
}

function stopTickerMarquee(el) {
  if (!el) return;
  if (el._tickerRafId) {
    cancelAnimationFrame(el._tickerRafId);
    el._tickerRafId = null;
  }
  if (el._tickerResumeTimer) {
    clearTimeout(el._tickerResumeTimer);
    el._tickerResumeTimer = null;
  }
  if (el._tickerCleanup) {
    el._tickerCleanup();
    el._tickerCleanup = null;
  }
}

function buildTickerLoopTrack(viewport, track, sorted) {
  const singleHtml = sorted.map(tickerItemHtml).join("");
  track.innerHTML = singleHtml;
  const loopWidth = track.scrollWidth;
  if (!loopWidth) return 0;

  let copies = sorted.length === 1 ? 3 : 2;
  track.innerHTML = singleHtml.repeat(copies);

  const viewportWidth = Math.max(viewport.clientWidth, 1);
  // Ensure total track is wider than viewport so marquee has room to move.
  while (track.scrollWidth < viewportWidth + loopWidth && copies < 24) {
    copies += 1;
    track.innerHTML = singleHtml.repeat(copies);
  }

  track.dataset.loopWidth = String(loopWidth);
  track.style.transform = "translate3d(0, 0, 0)";
  return loopWidth;
}

function initTickerMarquee(el) {
  stopTickerMarquee(el);
  el.classList.remove("ticker-paused");

  const viewport = el.querySelector(".ticker-viewport");
  const track = el.querySelector(".ticker-track");
  if (!viewport || !track) return;

  const loopWidth = parseFloat(track.dataset.loopWidth) || 0;
  if (!loopWidth) return;

  let paused = false;
  let hovering = false;
  let pointerDown = false;
  let wheelTimer = null;
  let offset = 0;

  function setPausedState(next) {
    paused = next;
    el.classList.toggle("ticker-paused", paused);
  }

  function pauseNow() {
    setPausedState(true);
    if (el._tickerResumeTimer) {
      clearTimeout(el._tickerResumeTimer);
      el._tickerResumeTimer = null;
    }
  }

  function shouldStayPaused() {
    return hovering || pointerDown;
  }

  function armResume() {
    if (el._tickerResumeTimer) clearTimeout(el._tickerResumeTimer);
    el._tickerResumeTimer = setTimeout(() => {
      if (!shouldStayPaused()) setPausedState(false);
      el._tickerResumeTimer = null;
    }, TICKER_RESUME_DEBOUNCE_MS);
  }

  const onEnter = () => {
    hovering = true;
    pauseNow();
  };
  const onLeave = () => {
    hovering = false;
    armResume();
  };
  const onDown = () => {
    pointerDown = true;
    pauseNow();
  };
  const onUp = () => {
    pointerDown = false;
    if (!shouldStayPaused()) armResume();
  };
  const onWheel = () => {
    pauseNow();
    if (wheelTimer) clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => {
      wheelTimer = null;
      if (!shouldStayPaused()) armResume();
    }, TICKER_RESUME_DEBOUNCE_MS);
  };

  viewport.addEventListener("pointerenter", onEnter);
  viewport.addEventListener("pointerleave", onLeave);
  viewport.addEventListener("touchstart", onDown, { passive: true });
  viewport.addEventListener("touchend", onUp);
  viewport.addEventListener("touchcancel", onUp);
  viewport.addEventListener("pointerdown", onDown);
  viewport.addEventListener("pointerup", onUp);
  viewport.addEventListener("pointercancel", onUp);
  viewport.addEventListener("wheel", onWheel, { passive: true });

  el._tickerCleanup = () => {
    viewport.removeEventListener("pointerenter", onEnter);
    viewport.removeEventListener("pointerleave", onLeave);
    viewport.removeEventListener("touchstart", onDown);
    viewport.removeEventListener("touchend", onUp);
    viewport.removeEventListener("touchcancel", onUp);
    viewport.removeEventListener("pointerdown", onDown);
    viewport.removeEventListener("pointerup", onUp);
    viewport.removeEventListener("pointercancel", onUp);
    viewport.removeEventListener("wheel", onWheel);
    if (wheelTimer) clearTimeout(wheelTimer);
    track.style.transform = "";
  };

  function tick() {
    el._tickerRafId = requestAnimationFrame(tick);
    if (document.visibilityState === "hidden") return;
    if (paused) return;

    offset += TICKER_SCROLL_PX_PER_FRAME;
    if (offset >= loopWidth) offset -= loopWidth;
    track.style.transform = `translate3d(${-offset}px, 0, 0)`;
  }

  el._tickerRafId = requestAnimationFrame(tick);
}

function renderLiveTicker(el, games) {
  if (!el) return;
  stopTickerMarquee(el);

  const sorted = sortGamesByStart(games);
  if (!sorted.length) {
    el.innerHTML = '<span class="ticker-empty">No games today</span>';
    return;
  }

  el.innerHTML = `
    <div class="ticker-viewport">
      <div class="ticker-track"></div>
    </div>`;

  const viewport = el.querySelector(".ticker-viewport");
  const track = el.querySelector(".ticker-track");
  const loopWidth = buildTickerLoopTrack(viewport, track, sorted);

  let layoutTries = 0;
  function startMarquee() {
    const lw = parseFloat(track.dataset.loopWidth) || loopWidth;
    if (!lw && layoutTries < 30) {
      layoutTries += 1;
      buildTickerLoopTrack(viewport, track, sorted);
      requestAnimationFrame(startMarquee);
      return;
    }
    if (lw) initTickerMarquee(el);
  }

  requestAnimationFrame(() => requestAnimationFrame(startMarquee));
}

let _teamColors = null;

async function loadTeamColors() {
  if (_teamColors) return _teamColors;
  try {
    _teamColors = await fetchJSON("/static/mlb_team_colors.json");
  } catch {
    _teamColors = {};
  }
  return _teamColors;
}

function teamPrimaryColor(teamName, colors) {
  if (!teamName) return "#2f3336";
  const map = colors || _teamColors || {};
  return map[teamName] || "#2f3336";
}

function gameCardColorStyle(game, colors) {
  const away =
    game.away_color ||
    game.away_team_color ||
    teamPrimaryColor(game.away_team, colors);
  const home =
    game.home_color ||
    game.home_team_color ||
    teamPrimaryColor(game.home_team, colors);
  return `--away-color: ${away}; --home-color: ${home};`;
}

const WATCH_STORAGE_KEY = "pb_watched_games";

function getWatchedGameIds() {
  try {
    const raw = localStorage.getItem(WATCH_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function isWatched(gameId) {
  return getWatchedGameIds().includes(String(gameId));
}

function toggleWatch(gameId) {
  const id = String(gameId);
  const set = new Set(getWatchedGameIds());
  if (set.has(id)) set.delete(id);
  else set.add(id);
  localStorage.setItem(WATCH_STORAGE_KEY, JSON.stringify([...set]));
  return set.has(id);
}

function oddsSnapshotKey(date) {
  return `pb_odds_snap_${date || "today"}`;
}

function enrichOddsGamesWithIds(games, slateGames) {
  if (!slateGames?.length) return games;
  return (games || []).map((g) => {
    const match = slateGames.find(
      (s) => s.home_team === g.home_team && s.away_team === g.away_team
    );
    return match ? { ...g, game_id: match.game_id } : g;
  });
}

function recordOddsSnapshot(snap, slateGames) {
  if (!snap?.games?.length) return;
  const games = enrichOddsGamesWithIds(snap.games, slateGames);
  const prev = JSON.parse(sessionStorage.getItem(oddsSnapshotKey(snap.date)) || "null");
  if (prev?.fetched_at === snap.fetched_at) return;
  if (prev) {
    sessionStorage.setItem(`${oddsSnapshotKey(snap.date)}_prev`, JSON.stringify(prev));
  }
  sessionStorage.setItem(
    oddsSnapshotKey(snap.date),
    JSON.stringify({ fetched_at: snap.fetched_at, games })
  );
}

function findOddsGameInSnapshot(games, gameId, teams) {
  if (!games?.length) return null;
  const byId = games.find((g) => g.game_id != null && String(g.game_id) === String(gameId));
  if (byId) return byId;
  if (!teams?.home_team || !teams?.away_team) return null;
  return (
    games.find((g) => g.home_team === teams.home_team && g.away_team === teams.away_team) || null
  );
}

function lineMovementForGame(gameId, gameDate, teams) {
  const cur = JSON.parse(sessionStorage.getItem(oddsSnapshotKey(gameDate)) || "null");
  const prev = JSON.parse(sessionStorage.getItem(`${oddsSnapshotKey(gameDate)}_prev`) || "null");
  if (!cur?.games || !prev?.games) return null;
  const current = findOddsGameInSnapshot(cur.games, gameId, teams);
  const previous = findOddsGameInSnapshot(prev.games, gameId, teams);
  if (!current || !previous) return null;
  const move = (c, p) => {
    if (c == null || p == null || c === p) return null;
    return c > p ? "up" : "down";
  };
  return {
    away_ml: move(current.away_ml, previous.away_ml),
    home_ml: move(current.home_ml, previous.home_ml),
    ou_line: move(current.ou_line, previous.ou_line),
  };
}

function modelLeanLabel(boardRow, options = {}) {
  const chips = modelLeanChips(boardRow, options);
  return chips.length ? chips[0] : null;
}

function modelLeanChips(boardRow, options = {}) {
  if (!boardRow) return [];
  const chips = [];
  let modelTeam = boardRow.model_pick_team;
  const modelConf = boardRow.model_confidence;
  if (!modelTeam && boardRow.model_prob_home != null) {
    const home = Number(boardRow.model_prob_home) >= 0.5;
    modelTeam = home ? boardRow.home_team : boardRow.away_team;
  }
  if (!modelTeam && boardRow.model_pick) {
    modelTeam = boardRow.model_pick;
  }
  if (modelTeam && modelConf) {
    chips.push({ text: `Model: ${modelTeam}`, tier: modelConf });
  }
  const evTeam = boardRow.ev_pick_team ?? boardRow.best_pick?.team;
  if (evTeam && options.sport !== "cfb") {
    chips.push({ text: `+EV: ${evTeam}`, tier: boardRow.ml_confidence, chipKind: "ev" });
  }
  if (boardRow.spread_pick) {
    chips.push({ text: `Spread: ${boardRow.spread_pick}`, tier: boardRow.spread_confidence });
  }
  if (boardRow.totals_pick) {
    chips.push({ text: `O/U: ${boardRow.totals_pick}`, tier: boardRow.totals_confidence });
  }
  return chips;
}

function confidenceChipClass(tier) {
  const t = (tier || "").toLowerCase();
  if (t === "lean only" || t.startsWith("blocked")) return "chip-lean";
  if (t === "low") return "chip-low";
  if (t === "moderate" || t === "medium") return "chip-medium";
  if (t === "high" || t === "very high" || t === "extremely high") return "chip-high";
  return "";
}

function propSlipCorrelationWarnings(legs) {
  const warnings = [];
  const byGame = {};
  for (const leg of legs) {
    const gid = leg.game_id || "unknown";
    byGame[gid] = byGame[gid] || [];
    byGame[gid].push(leg);
  }
  for (const [gid, gameLegs] of Object.entries(byGame)) {
    if (gameLegs.length < 2) continue;
    const players = new Set(gameLegs.map((l) => l.player));
    for (const player of players) {
      const same = gameLegs.filter((l) => l.player === player);
      if (same.length < 2) continue;
      const markets = same.map((l) => l.market_type).join(", ");
      warnings.push(`Same-game: ${player} has multiple legs (${markets}) — books may block or price differently.`);
    }
  }
  return warnings;
}

function matchupPreviewText(boardRow, lineMove) {
  if (!boardRow) return "";
  const parts = [];
  if (boardRow.expected_total_pts != null) {
    parts.push(`Est. ${Number(boardRow.expected_total_pts).toFixed(1)} pts`);
  } else if (boardRow.expected_total_runs != null) {
    parts.push(`Est. ${Number(boardRow.expected_total_runs).toFixed(1)} runs`);
  }
  if (boardRow.model_margin != null) {
    const mm = Number(boardRow.model_margin);
    const side = mm >= 0 ? "H" : "A";
    parts.push(`Margin ${side} ${Math.abs(mm).toFixed(1)}`);
  }
  if (boardRow.ou_line != null) {
    let line = `Line ${boardRow.ou_line}`;
    if (lineMove?.ou_line === "up") line += " ▲";
    if (lineMove?.ou_line === "down") line += " ▼";
    parts.push(line);
  }
  if (boardRow.ml_confidence && boardRow.ml_confidence !== "—") {
    parts.push(`${boardRow.ml_confidence} ML`);
  }
  return parts.join(" · ");
}

function lineMoveBadge(side, lineMove) {
  if (!lineMove) return "";
  const key = side === "away" ? "away_ml" : "home_ml";
  if (lineMove[key] === "up") return '<span class="line-move up" title="Line moved up">▲</span>';
  if (lineMove[key] === "down") return '<span class="line-move down" title="Line moved down">▼</span>';
  return "";
}

function renderSkeletonGameList(listEl, count = 6) {
  if (!listEl) return;
  listEl.classList.remove("hidden");
  listEl.innerHTML = Array.from({ length: count })
    .map(
      () => `
    <div class="game-card skeleton-card" aria-hidden="true">
      <div class="skeleton-band"></div>
      <div class="skeleton-row"></div>
      <div class="skeleton-row short"></div>
    </div>`
    )
    .join("");
}

const EMPTY_STATE_ICONS = {
  "no-games":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>',
  "no-nba-games":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>',
  "no-cfb-games":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="4" width="18" height="17" rx="2"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>',
  "no-odds":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3zM7 5V3M17 5V3"/></svg>',
  "no-board":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 19V5M4 19h16M8 15l3-4 3 3 4-6"/></svg>',
  "no-scores":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M7 10h3v4H7zM14 10h3v4h-3z"/></svg>',
  "no-bets":
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 19V5M4 19h16M8 15l3-4 3 3 4-6"/></svg>',
  news:
    '<svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="M7 9h10M7 13h6"/></svg>',
};

function emptyStateIcon(kind) {
  return EMPTY_STATE_ICONS[kind] || EMPTY_STATE_ICONS["no-games"];
}

function renderEmptyState(el, kind, extraHtml = "") {
  if (!el) return;
  const copy = {
    "no-games": {
      title: "No games on the slate",
      body: "Check back later or pick another date.",
      cta: '<a href="/mlb">MLB slate</a>',
    },
    "no-nba-games": {
      title: "No games on the slate",
      body: "No NBA games in the next few days. Check back during the season or Finals.",
      cta: '<a href="/nba">Refresh slate</a>',
    },
    "no-cfb-games": {
      title: "No games on the slate",
      body: "No FBS games in the next week. Try a Saturday during the season.",
      cta: '<a href="/cfb">Refresh slate</a>',
    },
    "no-odds": {
      title: "Lines not loaded yet",
      body: "Lines may appear after the morning refresh or when live odds are enabled.",
      cta: '<a href="/mlb">Back to slate</a>',
    },
    "no-board": {
      title: "Model picks warming up",
      body: "Run morning refresh to pre-build today's slate and picks.",
      cta: '<a href="/">Home</a>',
    },
  }[kind] || {
    title: "Nothing here yet",
    body: extraHtml || "Try again in a moment.",
    cta: '<a href="/">Home</a>',
  };
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="empty-state-card">
      ${emptyStateIcon(kind)}
      <h3>${copy.title}</h3>
      <p>${copy.body}</p>
      <p class="empty-cta">${copy.cta}</p>
    </div>`;
}

function renderHomeHeroChips(el, { summary, scoreCounts, status }) {
  if (!el) return;
  const mlb = scoreCounts?.mlb ?? "—";
  const nba = scoreCounts?.nba ?? "—";
  const gamesChip = `<span class="hero-chip"><span class="hero-chip-dot" aria-hidden="true"></span>${mlb} MLB · ${nba} NBA</span>`;

  let modelChip;
  if (summary?.board_available) {
    const ev = summary.plus_ev_singles ?? 0;
    const slate = summary.games_on_slate ?? 0;
    modelChip = `<span class="hero-chip"><span class="hero-chip-dot hero-chip-dot-ev" aria-hidden="true"></span>${ev} +EV · ${slate} on board</span>`;
  } else {
    modelChip = `<span class="hero-chip hero-chip-muted"><span class="hero-chip-dot" aria-hidden="true"></span>Picks warming up</span>`;
  }

  let refreshChip;
  if (status?.ran_at) {
    const rel = formatRelativeShort(status.ran_at);
    refreshChip = `<span class="hero-chip"><span class="hero-chip-dot hero-chip-dot-ok" aria-hidden="true"></span>Refresh ${rel}</span>`;
  } else {
    refreshChip = `<span class="hero-chip hero-chip-muted"><span class="hero-chip-dot" aria-hidden="true"></span>No refresh yet</span>`;
  }

  el.innerHTML = gamesChip + modelChip + refreshChip;
}

function applyGamePageWash(game) {
  if (!game) return;
  document.body.classList.add("game-page-bg");
  const away =
    game.away_color ||
    game.away_team_color ||
    teamPrimaryColor(game.away_team, _teamColors);
  const home =
    game.home_color ||
    game.home_team_color ||
    teamPrimaryColor(game.home_team, _teamColors);
  document.documentElement.style.setProperty("--game-away-color", away);
  document.documentElement.style.setProperty("--game-home-color", home);
  const wash = document.querySelector(".game-page-wash");
  if (wash) {
    wash.style.setProperty("--game-away-color", away);
    wash.style.setProperty("--game-home-color", home);
  }
}

function initHeadlineTicker(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  let items = [];
  let idx = 0;

  async function load() {
    try {
      const data = await fetchJSON("/api/news");
      items = data.items || [];
      if (!items.length) {
        el.classList.add("hidden");
        return;
      }
      el.classList.remove("hidden");
      show();
    } catch {
      el.classList.add("hidden");
    }
  }

  function show() {
    if (!items.length) return;
    const item = items[idx % items.length];
    idx += 1;
    const link = document.createElement("a");
    link.className = "headline-ticker-link";
    link.href = item.link;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const label = document.createElement("span");
    label.className = "headline-ticker-label";
    label.textContent = "Around the league";
    const text = document.createElement("span");
    text.className = "headline-ticker-text";
    text.textContent = item.title;
    link.append(label, text);
    el.replaceChildren(link);
  }

  load();
  setInterval(show, 12000);
}

function initLiveTicker(elementId, options = {}) {
  const el = document.getElementById(elementId);
  if (!el) return null;
  el.classList.remove("ticker-placeholder");
  el.classList.add("live-ticker");

  const intervalMs = options.intervalMs || 60000;
  const dateParam = options.date || qs("date");
  const sport = options.sport || "all";
  const base = `/api/scores/today?sport=${encodeURIComponent(sport)}`;
  const url = dateParam ? `${base}&date=${encodeURIComponent(dateParam)}` : base;

  let timer = null;

  async function refresh() {
    try {
      const data = await fetchJSON(url);
      renderLiveTicker(el, data.games);
    } catch {
      stopTickerMarquee(el);
      if (!el.querySelector(".ticker-viewport")) {
        el.innerHTML = '<span class="ticker-empty">Scores unavailable</span>';
      }
    }
  }

  refresh();
  timer = setInterval(refresh, intervalMs);
  return () => {
    clearInterval(timer);
    stopTickerMarquee(el);
  };
}

function gameCardHtml(game, options = {}) {
  const showScores = shouldShowScores(game);
  const boardRow = options.boardRow || null;
  const lineMove = options.lineMove || null;
  const colors = options.colors || _teamColors;
  const lean = modelLeanLabel(boardRow, { sport: gameSport(game) });
  const leanChips = modelLeanChips(boardRow, { sport: gameSport(game) });
  const preview = matchupPreviewText(boardRow, lineMove);
  const watched = options.showWatch !== false && isWatched(game.game_id);
  const leanHtml = leanChips.length
    ? leanChips
        .map(
          (chip) =>
            `<span class="model-lean-chip ${confidenceChipClass(chip.tier)}">${chip.text}</span>`
        )
        .join("")
    : lean
      ? `<span class="model-lean-chip ${confidenceChipClass(lean.tier)}">${lean.text}</span>`
      : "";
  const seriesHtml = game.series_summary
    ? `<p class="game-card-series">${game.series_summary}</p>`
    : "";
  return `
    <div class="game-card-color-band" aria-hidden="true"></div>
    <button type="button" class="watch-btn ${watched ? "watched" : ""}" data-watch-id="${game.game_id}" aria-label="Watch game">★</button>
    <div class="game-card-top">
      <span>${isGameLive(game.status) && game.period_label ? game.period_label : formatLocalTimeShort(game.start_time_utc)}</span>
      <span class="status-badge ${statusBadgeClass(game.status)}${isGameLive(game.status) ? " badge-pulse" : ""}">${gameStatusText(game)}</span>
    </div>
    ${seriesHtml}
    ${leanHtml}
    <div class="game-card-matchup">
      <div class="team-side away">
        <img class="team-logo" src="${logoForGame(game, "away")}" alt="" width="40" height="40" loading="lazy">
        <div class="team-name-block">
          <span class="team-name">${game.away_team}${lineMoveBadge("away", lineMove)}</span>
          ${teamRecordHtml(game.away_record)}
        </div>
        ${showScores ? `<span class="team-score">${game.away_score ?? 0}</span>` : ""}
      </div>
      <span class="game-card-at">@</span>
      <div class="team-side home">
        <img class="team-logo" src="${logoForGame(game, "home")}" alt="" width="40" height="40" loading="lazy">
        <div class="team-name-block">
          <span class="team-name">${game.home_team}${lineMoveBadge("home", lineMove)}</span>
          ${teamRecordHtml(game.home_record)}
        </div>
        ${showScores ? `<span class="team-score">${game.home_score ?? 0}</span>` : ""}
      </div>
    </div>
    ${preview ? `<p class="game-card-preview">${preview}</p>` : ""}
  `;
}

function attachWatchHandlers(listEl) {
  if (!listEl) return;
  listEl.querySelectorAll(".watch-btn").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const id = btn.dataset.watchId;
      const on = toggleWatch(id);
      btn.classList.toggle("watched", on);
    };
  });
}

function renderGameList(listEl, games, options = {}) {
  if (!listEl) return;
  listEl._renderOptions = options;
  listEl.innerHTML = "";
  const boardMap = options.boardMap || {};
  const gameDate = options.gameDate || null;
  sortGamesByStart(games).forEach((game) => {
    const card = document.createElement("a");
    card.className = "game-card" + (isGameLive(game.status) ? " game-card-live" : "");
    card.href = gameDetailHref(game, options);
    card.dataset.gameId = game.game_id;
    card.style.cssText = gameCardColorStyle(game, options.colors);
    const boardRow = boardMap[String(game.game_id)];
    const lineMove = gameDate
      ? lineMovementForGame(game.game_id, gameDate, {
          home_team: game.home_team,
          away_team: game.away_team,
        })
      : null;
    card.innerHTML = gameCardHtml(game, { ...options, boardRow, lineMove });
    listEl.appendChild(card);
  });
  attachWatchHandlers(listEl);
}

function updateGameCards(listEl, games) {
  if (!listEl) return;
  const opts = listEl._renderOptions || {};
  const byId = Object.fromEntries((games || []).map((g) => [String(g.game_id), g]));
  listEl.querySelectorAll(".game-card").forEach((card) => {
    const game = byId[card.dataset.gameId];
    if (!game) return;
    const boardRow = (opts.boardMap || {})[String(game.game_id)];
    const lineMove = opts.gameDate
      ? lineMovementForGame(game.game_id, opts.gameDate, {
          home_team: game.home_team,
          away_team: game.away_team,
        })
      : null;
    card.classList.toggle("game-card-live", isGameLive(game.status));
    card.style.cssText = gameCardColorStyle(game, opts.colors);
    card.innerHTML = gameCardHtml(game, { ...opts, boardRow, lineMove });
  });
  attachWatchHandlers(listEl);
}

function renderTodayGlance(el, summary, scoreCounts) {
  if (!el) return;
  const mlb = scoreCounts?.mlb ?? "—";
  const nba = scoreCounts?.nba ?? "—";
  if (!summary?.board_available) {
    el.innerHTML = `
      <div class="glance-card glance-muted">
        <span><strong>${mlb}</strong> MLB · <strong>${nba}</strong> NBA today</span>
        <span>${summary?.message || "Summary not loaded yet"}</span>
      </div>`;
    return;
  }
  el.innerHTML = `
    <div class="glance-card">
      <div class="glance-stat"><span class="glance-num">${summary.games_on_slate}</span><span class="glance-lbl">MLB games</span></div>
      <div class="glance-stat"><span class="glance-num">${mlb}</span><span class="glance-lbl">Live slate</span></div>
      <div class="glance-stat"><span class="glance-num">${summary.plus_ev_singles}</span><span class="glance-lbl">+EV singles</span></div>
      <div class="glance-stat"><span class="glance-num">${summary.games_with_odds}</span><span class="glance-lbl">w/ lines</span></div>
    </div>`;
}

function propHitRateTier(rate) {
  if (rate == null || Number.isNaN(rate)) return null;
  if (rate >= 0.9) return "high";
  if (rate >= 0.75) return "medium";
  if (rate <= 0.6) return "red";
  return null;
}

function hitRateTier(rate) {
  if (rate == null || Number.isNaN(rate)) return null;
  if (rate >= 0.62) return "high";
  if (rate >= 0.55) return "medium";
  if (rate >= 0.45) return "low";
  return null;
}

function hitRateChip(label, rate) {
  const tier = hitRateTier(rate);
  const pct = rate != null ? `${Math.round(rate * 100)}%` : "—";
  const cls = tier ? `hit-rate-chip hit-rate-${tier}` : "hit-rate-chip";
  return `<span class="${cls}"><span class="hit-rate-lbl">${label}</span> ${pct}</span>`;
}

function propHitRateChip(label, rate) {
  const tier = propHitRateTier(rate);
  const pct = rate != null ? `${Math.round(rate * 100)}%` : "—";
  const cls = tier ? `hit-rate-chip hit-rate-${tier}` : "hit-rate-chip";
  return `<span class="${cls}"><span class="hit-rate-lbl">${label}</span> ${pct}</span>`;
}

function propHitRatesHtml(prop, side) {
  const overKey = side === "over";
  const l5 = overKey ? prop.hit_rate_over_l5 : prop.hit_rate_under_l5;
  const l10 = overKey ? prop.hit_rate_over_l10 : prop.hit_rate_under_l10;
  const season = overKey ? prop.hit_rate_over_season : prop.hit_rate_under_season;
  return `<span class="hit-rate-row">${propHitRateChip("L5", l5)}${propHitRateChip("L10", l10)}${propHitRateChip("Season", season)}</span>`;
}

function teamWinRatesHtml(pick) {
  return `<span class="hit-rate-row">${hitRateChip("L5", pick.win_rate_l5)}${hitRateChip("L10", pick.win_rate_l10)}${hitRateChip("Season", pick.win_rate_season)}</span>`;
}

function lineStrengthHtml(item) {
  const level = item?.line_strength;
  if (!level) return "";
  const label = item.line_strength_label || level;
  const insight = item.line_insight || "";
  const safeInsight = String(insight).replace(/"/g, "&quot;");
  return `<span class="line-strength line-strength-${level}" title="${safeInsight}">${label}</span>`;
}

window.hitRateChip = hitRateChip;
window.propHitRateChip = propHitRateChip;
window.propHitRatesHtml = propHitRatesHtml;
window.teamWinRatesHtml = teamWinRatesHtml;
window.lineStrengthHtml = lineStrengthHtml;

function renderBestBets(el, topSingles) {
  if (!el) return;
  const picks = topSingles || [];
  if (!picks.length) {
    el.innerHTML = `
      <div class="best-bets-empty-card">
        ${emptyStateIcon("no-bets")}
        <p>No +EV singles at today's edge threshold.</p>
      </div>`;
    return;
  }
  el.innerHTML = picks
    .map((p) => {
      const edge = p.edge != null ? `${(p.edge * 100).toFixed(1)}%` : "—";
      const odds = p.american_odds > 0 ? `+${p.american_odds}` : p.american_odds;
      const gameHref = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb";
      const form = teamWinRatesHtml(p);
      const strength = lineStrengthHtml(p);
      const insight = p.line_insight
        ? `<span class="best-bet-insight">${p.line_insight}</span>`
        : "";
      return `
      <a class="best-bet-card" href="${gameHref}">
        <span class="best-bet-team">${p.team}</span>
        <span class="best-bet-meta">${p.matchup || ""}</span>
        <span class="best-bet-edge">EV ${edge} · ${odds}</span>
        <span class="best-bet-form">${form}</span>
        ${strength ? `<span class="best-bet-strength">${strength}</span>` : ""}
        ${insight}
      </a>`;
    })
    .join("");
}

function propSlipLegFromProp(p) {
  if (!p || !p.actionable || p.recommended_odds == null) return null;
  if (p.slip_leg) return p.slip_leg;
  const side = p.recommended_side || "over";
  return {
    id: [p.game_id, p.player, p.market_type, p.line, side].join("|"),
    game_id: p.game_id,
    matchup: p.matchup,
    player: p.player,
    market_type: p.market_type,
    market_label: p.market_label,
    side,
    line: p.line,
    american_odds: p.recommended_odds,
    hit_rate: p.recommended_hit_rate,
    score: p.rank_score ?? p.score,
  };
}

function renderBestProps(el, topProps, options = {}) {
  if (!el) return;
  const props = topProps || [];
  if (!props.length) {
    el.innerHTML = `
      <div class="best-bets-empty-card">
        ${emptyStateIcon("no-bets")}
        <p>${options.emptyMessage || "No actionable player props yet. Open MLB games to load lines."}</p>
      </div>`;
    return;
  }
  el.innerHTML = props
    .map((p, i) => {
      const side = p.recommended_side || "over";
      const odds = fmtAmericanOdds(p.recommended_odds);
      const gameHref = p.game_id ? `/mlb/game/${encodeURIComponent(p.game_id)}` : "/mlb";
      const line = `${p.market_label || p.market_type}: ${side} ${p.line}`;
      const form = propHitRatesHtml(p, side);
      const strength = lineStrengthHtml(p);
      const insight = p.line_insight
        ? `<span class="best-bet-insight">${p.line_insight}</span>`
        : "";
      return `
      <div class="best-bet-card best-prop-card">
        <a class="best-prop-card-link" href="${gameHref}">
          <span class="best-bet-team">${p.player}</span>
          <span class="best-bet-meta">${p.matchup || ""}</span>
          <span class="best-bet-meta">${line}</span>
          <span class="best-bet-edge">${odds}</span>
          <span class="best-bet-form">${form}</span>
          ${strength ? `<span class="best-bet-strength">${strength}</span>` : ""}
          ${insight}
        </a>
        <button type="button" class="home-prop-add-btn" data-add-home-prop="${i}" aria-label="Add ${p.player} to prop slip">+ Add</button>
      </div>`;
    })
    .join("");

  el.querySelectorAll("[data-add-home-prop]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const idx = Number(btn.dataset.addHomeProp);
      const prop = props[idx];
      const leg = propSlipLegFromProp(prop);
      if (!leg || !window.addPropToSlip) return;
      window.addPropToSlip(leg);
      const prev = btn.textContent;
      btn.textContent = "Added";
      window.setTimeout(() => {
        btn.textContent = prev;
      }, 1500);
    });
  });
}

function propSlipPlayerKey(player) {
  return String(player || "").trim().toLowerCase();
}

function selectUniquePlayerPropLegs(props, count) {
  const legs = [];
  const usedPlayers = new Set();
  for (const p of props || []) {
    if (legs.length >= count) break;
    const playerKey = propSlipPlayerKey(p.player);
    if (!playerKey || usedPlayers.has(playerKey)) continue;
    const leg = propSlipLegFromProp(p);
    if (leg.american_odds == null) continue;
    legs.push(leg);
    usedPlayers.add(playerKey);
  }
  return legs;
}

function populatePropSlipFromProps(props, count, { replace = true } = {}) {
  const legs = selectUniquePlayerPropLegs(props, count);

  if (replace) {
    savePropSlipLegs(legs);
  } else {
    const existing = getPropSlipLegs();
    const seenIds = new Set(existing.map((l) => l.id));
    const seenPlayers = new Set(existing.map((l) => propSlipPlayerKey(l.player)));
    for (const leg of legs) {
      const playerKey = propSlipPlayerKey(leg.player);
      if (seenIds.has(leg.id) || (playerKey && seenPlayers.has(playerKey))) continue;
      existing.push(leg);
      seenIds.add(leg.id);
      if (playerKey) seenPlayers.add(playerKey);
    }
    savePropSlipLegs(existing.slice(0, 30));
  }
  renderPropSlipPanel();
  document.getElementById("prop-slip-panel")?.classList.add("prop-slip-panel--open");
  return legs.length;
}

window.populatePropSlipFromProps = populatePropSlipFromProps;

function renderWatchedGamesSection(el, games, options = {}) {
  if (!el) return;
  const ids = new Set(getWatchedGameIds());
  const watched = (games || []).filter((g) => ids.has(String(g.game_id)));
  if (!watched.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = '<p class="app-section-title">Your games</p><div class="game-list game-list-compact" id="watched-games-list"></div>';
  const inner = el.querySelector("#watched-games-list");
  renderGameList(inner, watched, options);
}

function renderMatchupHeader(el, game) {
  if (!el || !game) return;
  applyGamePageWash(game);
  el.innerHTML = matchupHeaderHtml(game);
  const btn = el.querySelector(".watch-btn");
  if (btn) {
    btn.onclick = (e) => {
      e.preventDefault();
      const on = toggleWatch(btn.dataset.watchId);
      btn.classList.toggle("watched", on);
    };
  }
}

function matchupHeaderHtml(game) {
  const showScores = shouldShowScores(game);
  const centerText = isGameLive(game.status) && game.period_label
    ? game.period_label
    : formatLocalTime(game.start_time_utc);
  const watched = isWatched(game.game_id);
  const seriesHtml = game.series_summary
    ? `<p class="matchup-series">${game.series_summary}</p>`
    : "";
  return `
    <div class="matchup-header-wrap" style="${gameCardColorStyle(game)}">
    <div class="game-card-color-band matchup-band" aria-hidden="true"></div>
    <button type="button" class="watch-btn ${watched ? "watched" : ""}" data-watch-id="${game.game_id}" aria-label="Watch game">★</button>
    ${seriesHtml}
    <div class="matchup-grid">
      <div class="matchup-team">
        <img class="team-logo" src="${logoForGame(game, "away")}" alt="${game.away_team}" width="56" height="56">
        <h2>${game.away_team}</h2>
        ${teamRecordHtml(game.away_record)}
        ${showScores ? `<span class="matchup-score">${game.away_score ?? 0}</span>` : ""}
      </div>
      <div class="matchup-center">
        <span class="status-badge ${statusBadgeClass(game.status)}">${gameStatusText(game)}</span>
        <p class="matchup-time">${centerText}</p>
      </div>
      <div class="matchup-team">
        <img class="team-logo" src="${logoForGame(game, "home")}" alt="${game.home_team}" width="56" height="56">
        <h2>${game.home_team}</h2>
        ${teamRecordHtml(game.home_record)}
        ${showScores ? `<span class="matchup-score">${game.home_score ?? 0}</span>` : ""}
      </div>
    </div>
    </div>
  `;
}

function gameIdFromPath() {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const idx = parts.indexOf("game");
  if (idx >= 0 && parts[idx + 1]) return parts[idx + 1];
  return null;
}

function qs(name) {
  return new URLSearchParams(window.location.search).get(name);
}

function imgLogo(teamId, alt) {
  const img = document.createElement("img");
  img.className = "team-logo";
  img.src = teamLogoUrl(teamId);
  img.alt = alt || "Team logo";
  img.loading = "lazy";
  img.width = 40;
  img.height = 40;
  return img;
}

const NTG_SPLASH_LETTERS = {
  n: "/static/assets/ntg-letter-n.png",
  t: "/static/assets/ntg-letter-t.png",
  g: "/static/assets/ntg-letter-g.png",
};
const NTG_SPLASH_MAX_MS = 5000;

function buildNTGSplashElement(reducedMotion) {
  const root = document.createElement("div");
  root.id = "ntg-splash";
  root.className = "ntg-splash" + (reducedMotion ? " ntg-splash--reduced" : "");
  root.setAttribute("role", "presentation");
  root.setAttribute("aria-hidden", "true");

  root.innerHTML = `
    <div class="ntg-splash__stage">
      <svg class="ntg-splash__arcs" viewBox="0 0 420 300" aria-hidden="true">
        <defs>
          <linearGradient id="ntg-arc-grad-top" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#1d9bf0" stop-opacity="0.2"/>
            <stop offset="40%" stop-color="#4aa8ff"/>
            <stop offset="100%" stop-color="#ffffff"/>
          </linearGradient>
          <linearGradient id="ntg-arc-grad-bottom" x1="100%" y1="0%" x2="0%" y2="0%">
            <stop offset="0%" stop-color="#1d9bf0" stop-opacity="0.2"/>
            <stop offset="45%" stop-color="#2b7fd4"/>
            <stop offset="100%" stop-color="#ffffff"/>
          </linearGradient>
        </defs>
        <path class="ntg-splash__arc ntg-splash__arc--top" pathLength="1"
          d="M 28 210 C 60 40, 200 8, 392 72"/>
        <path class="ntg-splash__arc ntg-splash__arc--bottom" pathLength="1"
          d="M 392 228 C 340 290, 120 292, 28 248"/>
      </svg>
      <div class="ntg-splash__logo-wrap">
        <div class="ntg-splash__wordmark" role="img" aria-label="NTG Sports">
          <img class="ntg-splash__letter-img ntg-splash__letter-img--n" src="${NTG_SPLASH_LETTERS.n}" alt="" />
          <img class="ntg-splash__letter-img ntg-splash__letter-img--t" src="${NTG_SPLASH_LETTERS.t}" alt="" />
          <img class="ntg-splash__letter-img ntg-splash__letter-img--g" src="${NTG_SPLASH_LETTERS.g}" alt="" />
        </div>
        <p class="ntg-splash__sports" aria-hidden="true"><span class="ntg-splash__sports-line"></span>SPORTS<span class="ntg-splash__sports-line"></span></p>
      </div>
      <div class="ntg-splash__glow"></div>
    </div>
  `;
  return root;
}

function clearNTGSplashState() {
  document.documentElement.classList.remove("ntg-splash-pending", "ntg-splash-active");
}

/**
 * Cinematic NTG Sports intro — plays on every full page load.
 * Returns a promise that resolves when the splash finishes.
 */
function initNTGSplash() {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.classList.add("ntg-splash-pending");

  const splash = buildNTGSplashElement(reduced);
  document.body.appendChild(splash);
  document.documentElement.classList.add("ntg-splash-active");

  const holdMs = reduced ? 900 : 3000;
  const exitMs = reduced ? 350 : 650;
  let finished = false;

  return new Promise((resolve) => {
    const finish = () => {
      if (finished) return;
      finished = true;
      splash.classList.add("ntg-splash--out");
      clearNTGSplashState();
      window.setTimeout(() => {
        splash.remove();
        resolve();
      }, exitMs);
    };
    window.setTimeout(finish, holdMs);
    window.setTimeout(finish, NTG_SPLASH_MAX_MS);
  });
}

function shouldPlayNTGSplash() {
  return document.body?.dataset?.ntgSplash === "1";
}

function initSandboxNav() {
  const path = window.location.pathname || "";
  document.querySelectorAll(".app-nav-links").forEach((nav) => {
    if (nav.querySelector("[data-nav-sandbox]")) return;
    const link = document.createElement("a");
    link.href = "/sandbox";
    link.dataset.navSandbox = "1";
    link.textContent = "Sandbox";
    link.classList.add("nav-sandbox");
    if (path === "/sandbox") {
      link.classList.add("active");
    }
    nav.appendChild(link);
  });
}

const PROP_SLIP_KEY = "pb_prop_slip";

function getPropSlipLegs() {
  try {
    const raw = localStorage.getItem(PROP_SLIP_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function savePropSlipLegs(legs) {
  localStorage.setItem(PROP_SLIP_KEY, JSON.stringify(legs));
}

function fmtAmericanOdds(odds) {
  if (odds == null) return "—";
  return odds > 0 ? `+${odds}` : `${odds}`;
}

function propSlipLegLabel(leg) {
  const side = leg.side === "under" ? "U" : "O";
  const market = leg.market_label || leg.market_type;
  return `${leg.player} ${market} ${side}${leg.line} (${fmtAmericanOdds(leg.american_odds)})`;
}

function clientParlayDecimal(legs) {
  let payout = 1;
  for (const leg of legs) {
    const odds = Number(leg.american_odds);
    if (!Number.isFinite(odds)) continue;
    payout *= odds > 0 ? 1 + odds / 100 : 1 + 100 / Math.abs(odds);
  }
  return payout;
}

function renderPropSlipPanel() {
  const panel = document.getElementById("prop-slip-panel");
  const countEl = document.getElementById("prop-slip-count");
  const legsEl = document.getElementById("prop-slip-legs");
  const totalsEl = document.getElementById("prop-slip-totals");
  if (!panel || !legsEl || !totalsEl) return;

  const legs = getPropSlipLegs();
  if (countEl) countEl.textContent = String(legs.length);

  if (!legs.length) {
    legsEl.innerHTML = "<p class=\"prop-slip-empty\">Add props from any game page to build a cross-game parlay slip.</p>";
    totalsEl.innerHTML = "";
    return;
  }

  legsEl.innerHTML = legs
    .map(
      (leg) => `
        <div class="prop-slip-leg">
          <div class="prop-slip-leg-text">
            <strong>${leg.matchup || "MLB"}</strong>
            <span>${propSlipLegLabel(leg)}</span>
          </div>
          <button type="button" class="prop-slip-remove" data-remove-prop="${leg.id}" aria-label="Remove">×</button>
        </div>`
    )
    .join("");

  legsEl.querySelectorAll("[data-remove-prop]").forEach((btn) => {
    btn.addEventListener("click", () => {
      removePropFromSlip(btn.getAttribute("data-remove-prop"));
    });
  });

  const decimal = clientParlayDecimal(legs);
  const profit10 = (decimal * 10 - 10).toFixed(2);
  let american = "—";
  if (decimal >= 2) american = fmtAmericanOdds(Math.round((decimal - 1) * 100));
  else if (decimal > 1) american = fmtAmericanOdds(Math.round(-100 / (decimal - 1)));

  const corrWarnings = propSlipCorrelationWarnings(legs);
  const warnHtml = corrWarnings.length
    ? `<div class="prop-slip-warn">${corrWarnings.map((w) => `<p>${w}</p>`).join("")}</div>`
    : "";

  totalsEl.innerHTML = `
    ${warnHtml}
    <p><strong>${legs.length}-leg parlay</strong> · Combined ${american}</p>
    <p class="prop-slip-meta">$10 stake → $${profit10} profit if all legs hit (book may differ)</p>
    <button type="button" class="btn-ghost" id="prop-slip-clear">Clear slip</button>
  `;

  document.getElementById("prop-slip-clear")?.addEventListener("click", () => {
    savePropSlipLegs([]);
    renderPropSlipPanel();
  });

  fetch("/api/parlay/props/eval", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ legs }),
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data?.american_payout) return;
      totalsEl.querySelector("p")?.insertAdjacentHTML(
        "beforeend",
        ` · Decimal ${Number(data.decimal_payout).toFixed(2)}`
      );
    })
    .catch(() => {});
}

function addPropToSlip(leg) {
  if (!leg?.id || leg.american_odds == null) return;
  const legs = getPropSlipLegs();
  if (legs.some((l) => l.id === leg.id)) return;
  const playerKey = propSlipPlayerKey(leg.player);
  if (playerKey && legs.some((l) => propSlipPlayerKey(l.player) === playerKey)) return;
  legs.push(leg);
  savePropSlipLegs(legs);
  renderPropSlipPanel();
  const panel = document.getElementById("prop-slip-panel");
  panel?.classList.add("prop-slip-panel--open");
}

function removePropFromSlip(legId) {
  const legs = getPropSlipLegs().filter((l) => l.id !== legId);
  savePropSlipLegs(legs);
  renderPropSlipPanel();
}

function initPropSlipUi() {
  if (document.getElementById("prop-slip-root")) return;

  const root = document.createElement("div");
  root.id = "prop-slip-root";
  root.innerHTML = `
    <button type="button" id="prop-slip-toggle" class="prop-slip-toggle" aria-expanded="false">
      Prop slip <span id="prop-slip-count">0</span>
    </button>
    <div id="prop-slip-panel" class="prop-slip-panel" aria-label="Prop parlay slip">
      <div class="prop-slip-head">
        <strong>Your prop parlay</strong>
        <button type="button" id="prop-slip-close" class="prop-slip-close" aria-label="Close">×</button>
      </div>
      <div id="prop-slip-legs" class="prop-slip-legs"></div>
      <div id="prop-slip-totals" class="prop-slip-totals"></div>
    </div>
  `;
  document.body.appendChild(root);

  document.getElementById("prop-slip-toggle")?.addEventListener("click", () => {
    document.getElementById("prop-slip-panel")?.classList.toggle("prop-slip-panel--open");
  });
  document.getElementById("prop-slip-close")?.addEventListener("click", () => {
    document.getElementById("prop-slip-panel")?.classList.remove("prop-slip-panel--open");
  });

  window.addPropToSlip = addPropToSlip;
  renderPropSlipPanel();
}

function showBuildBadge() {
  const el = document.getElementById("pb-build-badge");
  if (!el) return;
  fetch("/api/build")
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data?.build_id) return;
      const props = data.props_api || {};
      const n = props.total_actionable ?? 0;
      el.textContent = `Build ${data.build_id} · ${n} props cached`;
      el.title = [
        props.source ? `source: ${props.source}` : "",
        props.hint || "",
        JSON.stringify(data.features || {}),
      ]
        .filter(Boolean)
        .join(" · ");
    })
    .catch(() => {});
}

function bootNTGSplash() {
  initSandboxNav();
  initPropSlipUi();
  showBuildBadge();
  if (document.querySelector(".app-shell") && shouldPlayNTGSplash()) {
    initNTGSplash().catch(() => clearNTGSplashState());
  } else {
    clearNTGSplashState();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootNTGSplash);
} else {
  bootNTGSplash();
}
