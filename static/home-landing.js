/**
 * NTG Sports homepage landing — presentation only.
 * Uses existing rankings and APIs. Does not score or re-rank picks.
 */
(function () {
  "use strict";

  const SPORT_ORDER = ["mlb", "nfl", "nba", "cfb", "ufc"];
  const SPORT_LABEL = { mlb: "MLB", nfl: "NFL", nba: "NBA", cfb: "CFB", ufc: "UFC" };
  const SPORT_MARK = {
    mlb: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8.5"/><path d="M12 3.5c1.4 2.4 1.4 14.6 0 17"/><path d="M12 3.5c-1.4 2.4-1.4 14.6 0 17"/></svg>',
    nfl: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><ellipse cx="12" cy="12" rx="8.5" ry="5.5" transform="rotate(-32 12 12)"/><path d="M9.5 9.5l5 5"/></svg>',
    nba: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5v17"/></svg>',
    cfb: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><ellipse cx="12" cy="12" rx="8.5" ry="5.5" transform="rotate(-32 12 12)"/><path d="M8.5 14.5l1.5-.75"/><path d="M14 10.25l1.5-.75"/></svg>',
    ufc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="8.5"/><path d="M8 8l8 8"/><path d="M16 8l-8 8"/></svg>',
  };
  const GAME_HREF = {
    mlb: (id) => (id ? `/mlb/game/${encodeURIComponent(id)}` : "/mlb"),
    nfl: (id) => (id ? `/nfl/game/${encodeURIComponent(id)}` : "/nfl"),
    nba: (id) => (id ? `/nba/game/${encodeURIComponent(id)}` : "/nba"),
    cfb: (id) => (id ? `/cfb/game/${encodeURIComponent(id)}` : "/cfb"),
    ufc: (id) => (id ? `/ufc/game/${encodeURIComponent(id)}` : "/ufc"),
  };

  function $(id) {
    return document.getElementById(id);
  }

  function pageData() {
    return typeof window.getPageData === "function" ? window.getPageData() : null;
  }

  function fetchJSON(url) {
    if (typeof window.fetchJSON === "function") return window.fetchJSON(url);
    return fetch(url, { credentials: "same-origin" }).then((res) => {
      if (!res.ok) throw new Error("unavailable");
      return res.json();
    });
  }

  function setProgress(progress, value, status) {
    if (typeof window.setHomeLoadProgress === "function") {
      window.setHomeLoadProgress(progress, value, status);
      return;
    }
    if (!progress) return;
    progress.value = value;
    if (status) progress.status = status;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function num(value) {
    if (value == null || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmtPct(value, digits) {
    const n = num(value);
    if (n == null) return null;
    const pct = Math.abs(n) <= 1.5 ? n * 100 : n;
    return `${pct.toFixed(digits == null ? 1 : digits)}%`;
  }

  function fmtSignedPct(value) {
    const n = num(value);
    if (n == null) return null;
    const pct = Math.abs(n) <= 1.5 ? n * 100 : n;
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(1)}%`;
  }

  function sportOf(row) {
    return String(row?.sport || row?.league || "mlb").toLowerCase();
  }

  function gameHref(sport, gameId) {
    const fn = GAME_HREF[sport] || GAME_HREF.mlb;
    return fn(gameId);
  }

  function chartPoints(values, width, height, pad) {
    const nums = (values || []).map(num).filter((v) => v != null);
    if (nums.length < 2) return "";
    const inset = pad == null ? 4 : pad;
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    const span = max - min || 1;
    const innerW = width - inset * 2;
    const innerH = height - inset * 2;
    const step = innerW / (nums.length - 1);
    return nums
      .map((v, i) => {
        const x = inset + i * step;
        const y = inset + innerH * (1 - (v - min) / span);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  function errorState(el, title, message, retry) {
    if (!el) return;
    if (typeof window.brandedErrorState === "function") {
      window.brandedErrorState(el, { title, message, kind: "no-board", onRetry: retry });
      return;
    }
    el.innerHTML = `<div class="hl-error"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></div>`;
  }

  function confidenceLabel(raw) {
    const text = String(raw || "").trim();
    if (!text) return "";
    const lower = text.toLowerCase();
    if (["rejected", "weak", "na", "n/a", "none"].includes(lower)) return "";
    return text;
  }

  function marketLabel(raw) {
    if (!raw) return "Market";
    return String(raw).replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function asEdgesFromSingles(rows) {
    return (rows || []).map((row) => {
      if (row.bet_type === "prop" || row.player || row.market_type) {
        return {
          kind: "prop",
          sport: sportOf(row),
          game_id: row.game_id,
          name: row.player || row.player_name,
          matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
          market: row.market_label || marketLabel(row.market_type),
          recommendation: row.recommended_side || row.side,
          line: row.line,
          win_prob: num(row.model_probability ?? row.recommended_probability ?? row.model_prob),
          edge: num(row.model_edge ?? row.edge ?? row.edge_pct),
          photo_url: row.photo_url,
          player_id: row.player_id,
        };
      }
      return {
        kind: "game",
        sport: sportOf(row),
        game_id: row.game_id,
        name: row.team || row.model_pick || row.best_pick?.team,
        matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
        home_team: row.home_team,
        away_team: row.away_team,
        recommendation: row.team || row.best_pick?.team || row.model_pick,
        win_prob: num(row.model_prob ?? row.model_probability ?? row.best_pick?.model_prob),
        edge: num(row.edge ?? row.model_edge ?? row.best_pick?.edge),
      };
    });
  }

  function asEdgesFromSlate(summary) {
    return Object.values(summary?.slate_by_game_id || {})
      .filter((row) => row && row.plus_ev_single && (row.best_pick || row.model_pick_team))
      .map((row) => ({
        kind: "game",
        sport: sportOf(row),
        game_id: row.game_id,
        name: row.best_pick?.team || row.model_pick_team,
        matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
        home_team: row.home_team,
        away_team: row.away_team,
        home_logo_url: row.home_logo_url,
        away_logo_url: row.away_logo_url,
        recommendation: row.best_pick?.team || row.model_pick_team,
        win_prob: num(row.best_pick?.model_prob),
        edge: num(row.best_pick?.edge ?? row.ev_pick_edge),
        confidence: confidenceLabel(row.model_category_label),
      }));
  }

  function asEdgesFromCfbPreds(payload) {
    const rows = Array.isArray(payload)
      ? payload
      : Object.values(payload && !Array.isArray(payload) ? payload.predictions || payload : {});
    return rows
      .filter((row) => row && row.game_id && (row.model_pick_team || row.model_pick))
      .map((row) => ({
        kind: "game",
        sport: "cfb",
        game_id: row.game_id,
        name: row.model_pick_team || row.model_pick,
        matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
        home_team: row.home_team,
        away_team: row.away_team,
        home_logo_url: row.home_logo_url,
        away_logo_url: row.away_logo_url,
        recommendation: row.model_pick_team || row.model_pick,
        win_prob: num(row.model_prob_home != null && String(row.model_pick_side) === "home" ? row.model_prob_home : row.model_prob_away),
        edge: num(row.ev_pick_edge ?? row.edge),
        confidence: row.public_tier_label || row.model_category_label || row.ml_confidence,
      }));
  }

  function asEdgesFromProps(payload, sport) {
    const rows = [].concat(payload?.very_strong_props || [], payload?.top_props || [], payload?.props || []);
    const seen = new Set();
    const out = [];
    rows.forEach((row) => {
      const key = [row.player, row.market_type, row.line, row.recommended_side || row.side].join("|");
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        kind: "prop",
        sport: sport || sportOf(row),
        game_id: row.game_id,
        name: row.player || row.player_name,
        matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
        market: row.market_label || marketLabel(row.market_type),
        recommendation: row.recommended_side || row.side,
        line: row.line,
        win_prob: num(row.model_probability ?? row.recommended_probability ?? row.model_prob),
        edge: num(row.model_edge ?? row.edge ?? row.edge_pct),
        photo_url: row.photo_url,
        player_id: row.player_id,
      });
    });
    return out;
  }

  function mostConfidentGame(summary) {
    const rows = Object.values(summary?.slate_by_game_id || {}).filter((row) => row && (row.best_pick || row.model_pick_team));
    if (!rows.length) return null;
    return rows.reduce((best, row) => {
      const score = num(row.best_pick?.model_prob ?? row.model_prob_home) || 0;
      const bestScore = num(best.best_pick?.model_prob ?? best.model_prob_home) || 0;
      return score > bestScore ? row : best;
    });
  }

  function logo(game, side) {
    if (typeof window.logoForGame === "function" && game) return window.logoForGame(game, side) || "";
    return side === "away" ? game?.away_logo_url || "" : game?.home_logo_url || "";
  }

  function teamShort(name) {
    if (!name) return "—";
    const parts = String(name).trim().split(/\s+/);
    return parts[parts.length - 1];
  }

  function initials(name) {
    return String(name || "?")
      .split(/\s+/)
      .slice(0, 2)
      .map((p) => p[0] || "")
      .join("")
      .toUpperCase();
  }

  function imgOrFallback(src, alt, cls) {
    if (!src) return "";
    return `<img class="${cls}" src="${escapeHtml(src)}" alt="${escapeHtml(alt || "")}" onerror="this.remove()">`;
  }

  function gameStartLabel(game) {
    const raw = game.start_time_et || game.start_time_local || game.game_time_et || game.start_time_utc || game.commence_time || game.start_time;
    const status = String(game.status || "").toLowerCase();
    if (status.includes("live") || status === "in") return "Live";
    if (status.includes("final") || status === "ft") return "Final";
    if (!raw) return "TBD";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return String(raw);
    return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
  }

  function findGame(games, gameId) {
    return (games || []).find((g) => String(g.game_id) === String(gameId)) || null;
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function countUp(el, end, digits, suffix) {
    if (!el) return;
    const target = num(end);
    const extra = suffix || "";
    if (target == null) {
      el.textContent = end == null ? "—" : String(end);
      return;
    }
    const places = digits == null ? 0 : digits;
    const format = (value) => (places ? value.toFixed(places) : String(Math.round(value))) + extra;
    if (prefersReducedMotion()) {
      el.textContent = format(target);
      return;
    }
    const start = performance.now();
    const dur = 700;
    function frame(now) {
      const t = Math.min(1, (now - start) / dur);
      el.textContent = format(target * t);
      if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function renderPerformance(el, tracker, perf) {
    if (!el) return;
    const pt = tracker || perf?.prop_tracker || {};
    const trend = perf?.charts?.performance_trend || {};
    const series = trend.series || [];
    const hitRate =
      pt.overall_hit_rate != null
        ? num(pt.overall_hit_rate)
        : num(trend.overall_hit_rate_pct != null ? trend.overall_hit_rate_pct / 100 : null);
    const settledHits = Object.values(pt.line_strength || {}).reduce((s, b) => s + (b.hits || 0), 0);
    const settledMisses = Object.values(pt.line_strength || {}).reduce((s, b) => s + (b.misses || 0), 0);
    const record = settledHits + settledMisses > 0 ? `${settledHits}–${settledMisses}` : null;
    const hitPts = series.map((row) => row.hit_rate_pct).filter((v) => v != null);
    const chart = chartPoints(hitPts, 280, 58, 4);
    const metrics = [];
    if (record) metrics.push({ label: "Record", value: record });
    if (trend.overall_roi_pct != null) metrics.push({ label: "Tracker ROI", value: fmtSignedPct(trend.overall_roi_pct / 100) });
    if (pt.props_settled != null) metrics.push({ label: "Settled", value: String(pt.props_settled) });
    const acc = hitRate != null ? `${(hitRate * 100).toFixed(1)}%` : "—";
    el.innerHTML = `
      <div class="hl-perf-head"><h2>Model Performance</h2><a href="/performance">Full report</a></div>
      <div class="hl-perf-value">${escapeHtml(acc)}</div>
      <p class="hl-perf-label">Hit rate${pt.days ? ` · ${pt.days}d` : ""}</p>
      ${chart ? `<svg class="hl-perf-chart" viewBox="0 0 280 58" role="img" aria-label="Hit rate trend"><polyline points="${chart}"></polyline></svg>` : ""}
      ${
        metrics.length
          ? `<dl class="hl-perf-metrics">${metrics
              .slice(0, 3)
              .map((m) => `<div class="hl-perf-metric"><dt>${escapeHtml(m.label)}</dt><dd>${escapeHtml(m.value)}</dd></div>`)
              .join("")}</dl>`
          : `<p class="hl-picks-copy">Settled results appear here after tracked picks grade.</p>`
      }`;
    if (hitRate != null) countUp(el.querySelector(".hl-perf-value"), hitRate * 100, 1, "%");
  }

  function renderLiveSummary(el, edgeCount, gameCount, status) {
    if (!el) return;
    const fresh = Boolean(status?.display_updated_at || status?.odds_fetched_at || status?.ran_at);
    el.innerHTML = `
      <div><dd data-hl-count="${edgeCount}">${edgeCount}</dd><dt>Edges today</dt></div>
      <div><dd data-hl-count="${gameCount}">${gameCount}</dd><dt>Games today</dt></div>
      <div><dd class="hl-live-flag">${fresh ? '<span class="hl-live-dot" aria-hidden="true"></span>Latest data' : "—"}</dd><dt>Data updates</dt></div>`;
    el.querySelectorAll("[data-hl-count]").forEach((node) => countUp(node, node.getAttribute("data-hl-count")));
  }

  function renderSports(el, sports) {
    if (!el) return;
    el.innerHTML = SPORT_ORDER.map((key) => {
      const n = Number(sports[key] || 0);
      return `
        <a class="hl-sport-tile" data-sport="${key}" href="/${key}">
          <span class="hl-sport-mark" aria-hidden="true">${SPORT_MARK[key]}</span>
          <h3 class="hl-sport-name">${SPORT_LABEL[key]}</h3>
          <p class="hl-sport-meta">${n} ${n === 1 ? "game" : "games"} today</p>
          <span class="hl-sport-go">Explore →</span>
        </a>`;
    }).join("");
  }

  function renderIntel(el, { gameEdge, playerEdge, confident }, games) {
    if (!el) return;
    const game = gameEdge ? findGame(games, gameEdge.game_id) || gameEdge : null;
    const gameProb = num(gameEdge?.win_prob);
    const gamePct = gameProb == null ? 0 : Math.abs(gameProb) <= 1.5 ? gameProb * 100 : gameProb;
    const gameVisual = gameEdge
      ? `<a class="hl-story hl-story-main" data-sport="${escapeHtml(gameEdge.sport)}" href="${escapeHtml(gameHref(gameEdge.sport, gameEdge.game_id))}">
          <div class="hl-story-photo" aria-hidden="true"></div>
          <div class="hl-story-shade"></div>
          <div class="hl-story-body">
            <p class="hl-intel-kicker">Featured Model Opportunity · ${escapeHtml(SPORT_LABEL[gameEdge.sport] || "MLB")}</p>
            <div class="hl-story-matchup">
              <div class="hl-story-team">${imgOrFallback(logo(game, "away") || gameEdge.away_logo_url, gameEdge.away_team, "hl-story-logo")}<span>${escapeHtml(teamShort(gameEdge.away_team || game?.away_team))}</span></div>
              <span class="hl-story-at">AT</span>
              <div class="hl-story-team">${imgOrFallback(logo(game, "home") || gameEdge.home_logo_url, gameEdge.home_team, "hl-story-logo")}<span>${escapeHtml(teamShort(gameEdge.home_team || game?.home_team))}</span></div>
            </div>
            <h3>${escapeHtml(gameEdge.matchup || gameEdge.name)}</h3>
            <p class="hl-story-lean">NTG lean <strong>${escapeHtml(gameEdge.recommendation || "")}</strong></p>
            <div class="hl-story-evidence">
              <div class="hl-prob-ring" style="--prob:${Math.max(0, Math.min(100, gamePct)).toFixed(1)}"><span>${escapeHtml(fmtPct(gameProb) || "—")}</span><small>Model</small></div>
              <div><b>${escapeHtml(fmtSignedPct(gameEdge.edge) || "Qualified")}</b><small>${gameEdge.edge != null ? "Model edge" : "Opportunity"}</small></div>
            </div>
            <span class="hl-story-cta">Open full matchup <b>→</b></span>
          </div>
        </a>`
      : `<div class="hl-story hl-story-main hl-story-empty hl-model-hold"><div class="hl-story-body"><p class="hl-intel-kicker">Model Status · Slate Monitor</p><div class="hl-hold-signal" aria-hidden="true"><span></span></div><h3>The model is holding.</h3><p>No game has cleared today’s edge threshold. NTG will surface an opportunity only when the data supports it.</p><a class="hl-story-cta" href="/mlb">Explore all matchups <b>→</b></a></div></div>`;

    const playerVisual = playerEdge
      ? `<a class="hl-story-side hl-story-player" href="${escapeHtml(playerEdge.player_id || playerEdge.name ? `/players/${encodeURIComponent(playerEdge.sport)}/${encodeURIComponent(playerEdge.player_id || playerEdge.name)}` : "/props")}">
          <div class="hl-story-side-top"><p class="hl-intel-kicker">Player Edge · ${escapeHtml(SPORT_LABEL[playerEdge.sport] || "")}</p>${playerEdge.photo_url ? imgOrFallback(playerEdge.photo_url, playerEdge.name, "hl-player-photo") : `<div class="hl-player-fallback">${escapeHtml(initials(playerEdge.name))}</div>`}</div>
          <h3>${escapeHtml(playerEdge.name)}</h3>
          <p>${escapeHtml(playerEdge.market || "")} · ${escapeHtml(String(playerEdge.recommendation || "").toUpperCase())}${playerEdge.line != null ? ` ${escapeHtml(playerEdge.line)}` : ""}</p>
          <strong>${escapeHtml(fmtSignedPct(playerEdge.edge) || fmtPct(playerEdge.win_prob) || "—")}</strong>
          <span>View player analysis →</span>
        </a>`
      : `<div class="hl-story-side hl-model-hold-side"><p class="hl-intel-kicker">Prop Market Monitor</p><div class="hl-hold-status"><i></i><span>Qualification gates active</span></div><h3>No prop clears the model threshold.</h3><p>The board is live. NTG is choosing discipline over a forced recommendation.</p><a href="/props">Browse the full prop board →</a></div>`;

    const watchProb = num(confident?.best_pick?.model_prob || confident?.model_prob_home);
    const watchPct = watchProb == null ? 0 : Math.abs(watchProb) <= 1.5 ? watchProb * 100 : watchProb;
    const watchVisual = confident
      ? `<a class="hl-story-side hl-story-watch" href="${escapeHtml(gameHref(sportOf(confident), confident.game_id))}">
          <p class="hl-intel-kicker">Matchup to Watch</p>
          <h3>${escapeHtml(confident.matchup || [confident.away_team, confident.home_team].filter(Boolean).join(" @ "))}</h3>
          <p>Model side · ${escapeHtml(confident.best_pick?.team || confident.model_pick_team || "NTG lean")}</p>
          <div class="hl-prob-track"><span class="hl-prob-fill" style="--p:${watchPct.toFixed(1)}%"></span></div>
          <strong>${escapeHtml(fmtPct(watchProb) || "—")}</strong>
          <span>View matchup →</span>
        </a>`
      : `<div class="hl-story-side hl-model-hold-side"><p class="hl-intel-kicker">Matchup Radar</p><div class="hl-hold-status"><i></i><span>Monitoring the slate</span></div><h3>Waiting for a qualified signal.</h3><p>Line movement and model confidence remain under review.</p><a href="#hl-games">Open the live board →</a></div>`;

    el.innerHTML = gameVisual + `<div class="hl-story-stack">${playerVisual}${watchVisual}</div>`;
  }
  function renderGameFilters(el, sports, active, onChange) {
    if (!el) return;
    const available = ["all"].concat(SPORT_ORDER.filter((key) => (sports[key] || 0) > 0));
    el.innerHTML = available
      .map((key) => `<button type="button" class="hl-filter${key === active ? " is-active" : ""}" data-sport="${key}">${key === "all" ? "All" : SPORT_LABEL[key]}</button>`)
      .join("");
    el.querySelectorAll("[data-sport]").forEach((btn) => {
      btn.addEventListener("click", () => onChange(btn.getAttribute("data-sport")));
    });
  }

  function renderGames(el, games, edgeCounts, sport) {
    if (!el) return;
    const rows = (games || []).filter((g) => sport === "all" || sportOf(g) === sport);
    if (!rows.length) {
      el.innerHTML = `<div class="hl-board-empty"><h3>No games on today's slate</h3><p>Check back after the next schedule refresh.</p></div>`;
      return;
    }
    el.innerHTML = rows.map((game, index) => {
      const key = sportOf(game);
      const label = gameStartLabel(game);
      const isLive = String(label).toLowerCase() === "live";
      const awayScore = num(game.away_score ?? game.score_away);
      const homeScore = num(game.home_score ?? game.score_home);
      const count = edgeCounts[String(game.game_id || "")] || 0;
      return `
        <a class="hl-score-card${isLive ? " is-live" : ""}" style="--delay:${Math.min(index, 8) * 55}ms" href="${escapeHtml(gameHref(key, game.game_id))}">
          <div class="hl-score-card-top"><span>${escapeHtml(SPORT_LABEL[key] || key)}</span><b>${escapeHtml(label)}</b></div>
          <div class="hl-score-match">
            <div class="hl-score-team">${imgOrFallback(logo(game, "away"), game.away_team, "hl-score-logo")}<strong>${escapeHtml(game.away_team_abbr || teamShort(game.away_team))}</strong>${awayScore != null ? `<em>${awayScore}</em>` : ""}</div>
            <span class="hl-score-vs">VS</span>
            <div class="hl-score-team">${imgOrFallback(logo(game, "home"), game.home_team, "hl-score-logo")}<strong>${escapeHtml(game.home_team_abbr || teamShort(game.home_team))}</strong>${homeScore != null ? `<em>${homeScore}</em>` : ""}</div>
          </div>
          <div class="hl-score-card-foot">${count ? `<span>${count} model ${count === 1 ? "edge" : "edges"}</span>` : "<span>Matchup analysis</span>"}<b>Open →</b></div>
        </a>`;
    }).join("");
  }
  function renderYours(el, payload, signedIn) {
    if (!el) return;
    if (!signedIn) {
      el.innerHTML = `
        <h2>Your NTG</h2>
        <p class="hl-picks-copy">Save predictions and track them from pick to result.</p>
        <a class="hl-cta" href="/signin?next=${encodeURIComponent("/")}">Sign in</a>`;
      return;
    }
    const counts = payload?.counts || {};
    const total = (payload?.props || []).length + (payload?.games || []).length;
    el.innerHTML = `
      <h2>Your NTG</h2>
      <div class="hl-yours-grid">
        <div><b>${total}</b><span>Saved picks</span></div>
        <div><b>${counts.live || 0}</b><span>Live picks</span></div>
        <div><b>${counts.final || 0}</b><span>Finalized</span></div>
      </div>
      <a class="hl-link-more" href="/watchlist">View My Picks →</a>`;
  }

  function edgeCountsByGame(edges) {
    const counts = {};
    edges.forEach((item) => {
      if (!item.game_id) return;
      const key = String(item.game_id);
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }

  async function loadLanding(progress) {
    const root = $("home-landing");
    if (!root) return null;
    setProgress(progress, 0.12, "Loading today's board…");
    const embedded = pageData();
    let summary = embedded?.kind === "home" ? embedded.summary : null;
    let mlbProps = embedded?.kind === "home" ? embedded.propsData : null;
    let tracker = embedded?.kind === "home" ? embedded.trackerSummary : null;
    let perf = embedded?.kind === "home" ? embedded.perfSummary : null;
    let status = embedded?.kind === "home" ? embedded.status : null;
    let scores = null;
    let nflProps = null;
    let watch = null;

    try {
      const first = [];
      if (!summary) first.push(fetchJSON("/api/home/today").then((d) => (summary = d)));
      if (!mlbProps) {
        first.push(fetchJSON("/api/daily/props?sport=mlb&limit=20&cache_only=true&scan=false").then((d) => (mlbProps = d)).catch(() => null));
      }
      if (!perf) first.push(fetchJSON("/api/performance/summary?days=30").then((d) => (perf = d)).catch(() => null));
      if (!status) first.push(fetchJSON("/api/status/refresh").then((d) => (status = d)).catch(() => null));
      if (first.length) await Promise.all(first);
    } catch (_) {
      errorState($("hl-intel"), "Unable to load today's board", "Try refreshing in a moment.", () => loadLanding(progress));
      errorState($("hl-performance"), "Unable to load performance", "Try again in a moment.", () => loadLanding(progress));
      setProgress(progress, 1, "Ready");
      return null;
    }

    tracker = tracker || perf?.prop_tracker || null;
    renderPerformance($("hl-performance"), tracker, perf);

    let gameEdges = asEdgesFromSlate(summary);
    let props = asEdgesFromProps(mlbProps, "mlb").concat(asEdgesFromSingles(summary?.top_singles || []).filter((e) => e.kind === "prop"));
    renderIntel($("hl-intel"), { gameEdge: gameEdges[0] || null, playerEdge: props[0] || null, confident: mostConfidentGame(summary) }, []);
    setProgress(progress, 0.62, "Loading today's games…");

    const signedIn = Boolean((window.pbUserAuth || {}).signed_in);
    try {
      const extra = await Promise.all([
        fetchJSON("/api/scores/today?sport=all").catch(() => embedded?.scores || { games: [], sports: {} }),
        fetchJSON("/api/daily/props?sport=nfl&limit=20&cache_only=true&scan=false").catch(() => null),
        signedIn ? fetch("/api/watchlist", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null) : Promise.resolve(null),
        fetchJSON("/api/cfb/predictions").catch(() => null),
      ]);
      scores = extra[0];
      nflProps = extra[1];
      watch = extra[2];
      gameEdges = gameEdges.concat(asEdgesFromCfbPreds(extra[3]));
    } catch (_) {
      scores = embedded?.scores || { games: [], sports: {} };
    }

    const mlbPropEdges = asEdgesFromProps(mlbProps, "mlb");
    const nflPropEdges = asEdgesFromProps(nflProps, "nfl");
    props = nflPropEdges.concat(mlbPropEdges).concat(asEdgesFromSingles(summary?.top_singles || []).filter((e) => e.kind === "prop"));
    const edges = gameEdges.concat(props);
    const games = scores?.games || [];
    const sports = scores?.sports || {};
    SPORT_ORDER.forEach((key) => {
      if (sports[key] == null) sports[key] = games.filter((g) => sportOf(g) === key).length;
    });
    renderSports($("hl-sports"), sports);

    const paintGames = (sport) => {
      renderGameFilters($("hl-game-filters"), sports, sport, paintGames);
      const sportEdges =
        sport === "nfl"
          ? nflPropEdges.concat(gameEdges.filter((e) => e.sport === "nfl"))
          : sport === "mlb"
            ? mlbPropEdges.concat(gameEdges.filter((e) => e.sport === "mlb"))
            : sport === "cfb"
              ? gameEdges.filter((e) => e.sport === "cfb")
              : edges;
      renderLiveSummary($("hl-live-summary"), sportEdges.length, games.filter((g) => sport === "all" || sportOf(g) === sport).length, status);
      renderGames($("hl-games"), games, edgeCountsByGame(sportEdges), sport);
      const playerPool = sport === "nfl" ? nflPropEdges : sport === "mlb" ? mlbPropEdges : props;
      const gamePool = sport === "all" ? gameEdges : gameEdges.filter((e) => e.sport === sport);
      renderIntel(
        $("hl-intel"),
        { gameEdge: gamePool[0] || null, playerEdge: playerPool[0] || null, confident: mostConfidentGame(summary) },
        scores?.games || []
      );
    };
    paintGames("all");
    renderYours($("hl-picks"), watch, signedIn);

    const live = $("hl-live-status");
    if (live) {
      if (typeof window.formatRefreshStatus === "function" && status) live.textContent = window.formatRefreshStatus(status);
      else live.textContent = "Model slate updates through the day.";
    }
    setProgress(progress, 1, "Ready");
    return { summary, scores, edges };
  }

  window.hydrateHomeLanding = loadLanding;
})();
