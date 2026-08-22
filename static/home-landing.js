/**
 * NTG Sports homepage landing — presentation only.
 * Uses existing rankings and APIs. Does not score or re-rank picks.
 */
(function () {
  "use strict";

  const SPORT_ORDER = ["mlb", "nfl", "nba", "cfb", "ufc"];
  const SPORT_LABEL = { mlb: "MLB", nfl: "NFL", nba: "NBA", cfb: "CFB", ufc: "UFC" };
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

  function fmtAmerican(odds) {
    const n = num(odds);
    if (n == null) return null;
    return n > 0 ? `+${Math.round(n)}` : `${Math.round(n)}`;
  }

  function marketLabel(raw) {
    if (!raw) return "Market";
    return String(raw)
      .replace(/_/g, " ")
      .replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function bookLabel(raw) {
    const key = String(raw || "").toLowerCase();
    const names = {
      draftkings: "DraftKings",
      fanduel: "FanDuel",
      betmgm: "BetMGM",
      caesars: "Caesars",
      consensus: "Consensus",
    };
    if (names[key]) return names[key];
    if (!raw) return "";
    return String(raw).replace(/_/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
  }

  function confidenceLabel(raw) {
    const text = String(raw || "").trim();
    if (!text) return "";
    const lower = text.toLowerCase();
    if (["rejected", "weak", "na", "n/a", "none"].includes(lower)) return "";
    return text;
  }

  function sportOf(row) {
    return String(row?.sport || row?.league || "mlb").toLowerCase();
  }

  function gameHref(sport, gameId) {
    const fn = GAME_HREF[sport] || GAME_HREF.mlb;
    return fn(gameId);
  }

  function edgeHref(item) {
    const sport = sportOf(item);
    if (item.kind === "prop") {
      if (item.player_id || item.player) {
        const key = item.player_id || item.player;
        return `/players/${encodeURIComponent(sport)}/${encodeURIComponent(key)}`;
      }
      return sport === "nfl" ? "/props?sport=nfl" : "/props";
    }
    return gameHref(sport, item.game_id);
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
          market_type: row.market_type,
          recommendation: row.recommended_side || row.side,
          line: row.line,
          win_prob: num(row.model_probability ?? row.recommended_probability ?? row.model_prob),
          edge: num(row.model_edge ?? row.edge ?? row.edge_pct),
          confidence: confidenceLabel(row.confidence || row.line_strength_label || row.line_strength),
          odds: row.recommended_odds ?? row.american_odds ?? row.odds,
          sportsbook: bookLabel(row.sportsbook || row.bookmaker),
          player_id: row.player_id,
          player: row.player || row.player_name,
        };
      }
      return {
        kind: "game",
        sport: sportOf(row),
        game_id: row.game_id,
        name: row.team || row.model_pick || row.best_pick?.team,
        matchup: row.matchup || [row.away_team, row.home_team].filter(Boolean).join(" @ "),
        market: "Moneyline",
        market_type: "moneyline",
        recommendation: row.team || row.best_pick?.team || row.model_pick,
        line: null,
        win_prob: num(row.model_prob ?? row.model_probability ?? row.best_pick?.model_prob),
        edge: num(row.edge ?? row.model_edge ?? row.best_pick?.edge),
        confidence: confidenceLabel(row.confidence || row.line_strength_label || row.model_confidence),
        odds: row.american_odds ?? row.best_pick?.american_odds,
        sportsbook: bookLabel(row.sportsbook),
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
        market: "Moneyline",
        market_type: "moneyline",
        recommendation: row.best_pick?.team || row.model_pick_team,
        line: null,
        win_prob: num(row.best_pick?.model_prob ?? row.model_confidence),
        edge: num(row.best_pick?.edge ?? row.ev_pick_edge),
        confidence: confidenceLabel(row.model_category_label || row.model_confidence),
        odds: row.best_pick?.american_odds,
        sportsbook: "",
      }));
  }

  function asEdgesFromProps(payload, sport) {
    const rows = []
      .concat(payload?.very_strong_props || [])
      .concat(payload?.top_props || [])
      .concat(payload?.props || []);
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
        market_type: row.market_type,
        recommendation: row.recommended_side || row.side,
        line: row.line,
        win_prob: num(row.model_probability ?? row.recommended_probability ?? row.model_prob),
        edge: num(row.model_edge ?? row.edge ?? row.edge_pct),
        confidence: confidenceLabel(row.confidence || row.line_strength_label || row.line_strength),
        odds: row.recommended_odds ?? row.american_odds ?? row.odds,
        sportsbook: bookLabel(row.sportsbook || row.bookmaker),
        player_id: row.player_id,
        player: row.player || row.player_name,
      });
    });
    return out;
  }

  function mergeEdges(primary, extra, limit) {
    const seen = new Set();
    const out = [];
    primary.concat(extra).forEach((item) => {
      if (!item?.name) return;
      const key = [item.kind, item.sport, item.name, item.market_type, item.recommendation, item.line].join("|");
      if (seen.has(key)) return;
      seen.add(key);
      out.push(item);
    });
    return out.slice(0, limit);
  }

  function renderPerformance(el, tracker, perf) {
    if (!el) return;
    const pt = tracker || perf?.prop_tracker || {};
    const trend = perf?.charts?.performance_trend || {};
    const series = trend.series || [];
    const hitRate = pt.overall_hit_rate != null ? num(pt.overall_hit_rate) : num(trend.overall_hit_rate_pct != null ? trend.overall_hit_rate_pct / 100 : null);
    const settledHits = Object.values(pt.line_strength || {}).reduce((sum, bucket) => sum + (bucket.hits || 0), 0);
    const settledMisses = Object.values(pt.line_strength || {}).reduce((sum, bucket) => sum + (bucket.misses || 0), 0);
    const record = settledHits + settledMisses > 0 ? `${settledHits}–${settledMisses}` : null;
    const hitPts = series.map((row) => row.hit_rate_pct).filter((v) => v != null);
    const delta =
      hitPts.length >= 2 && hitPts[hitPts.length - 1] != null && hitPts[0] != null
        ? hitPts[hitPts.length - 1] - hitPts[0]
        : null;
    const chart = chartPoints(hitPts, 280, 64, 4);
    const metrics = [];
    if (record) metrics.push({ label: "Recent record", value: record });
    if (trend.overall_roi_pct != null) metrics.push({ label: "Tracker ROI", value: fmtSignedPct(trend.overall_roi_pct / 100) });
    if (pt.props_settled != null) metrics.push({ label: "Settled", value: String(pt.props_settled) });

    if (hitRate == null && !metrics.length && !chart) {
      el.innerHTML = `
        <div class="hl-perf-head"><h2>Model Performance</h2><a href="/performance">Full report</a></div>
        <p class="hl-picks-copy">Settled results will appear here after tracked picks grade.</p>`;
      return;
    }

    const acc = hitRate != null ? `${(hitRate * 100).toFixed(1)}%` : "—";
    const deltaClass = delta == null ? "is-flat" : delta > 0.05 ? "is-up" : delta < -0.05 ? "is-down" : "is-flat";
    const deltaText = delta == null ? "" : `${delta > 0 ? "+" : ""}${delta.toFixed(1)} pts`;
    el.innerHTML = `
      <div class="hl-perf-head">
        <h2>Model Performance</h2>
        <a href="/performance">Full report</a>
      </div>
      <div class="hl-perf-accuracy">
        <span class="hl-perf-value">${escapeHtml(acc)}</span>
        ${deltaText ? `<span class="hl-perf-delta ${deltaClass}">${escapeHtml(deltaText)}</span>` : ""}
      </div>
      <p class="hl-perf-label">Overall accuracy${pt.days ? ` · ${pt.days}d` : ""}</p>
      ${chart ? `<svg class="hl-perf-chart" viewBox="0 0 280 64" role="img" aria-label="Accuracy trend"><polyline points="${chart}"></polyline></svg>` : ""}
      ${
        metrics.length
          ? `<dl class="hl-perf-metrics">${metrics
              .slice(0, 3)
              .map((m) => `<div class="hl-perf-metric"><dt>${escapeHtml(m.label)}</dt><dd>${escapeHtml(m.value)}</dd></div>`)
              .join("")}</dl>`
          : ""
      }`;
  }

  function renderEdges(el, items) {
    if (!el) return;
    if (!items.length) {
      el.innerHTML = `
        <div class="hl-empty">
          <h3>No qualifying edges yet</h3>
          <p>NTG hasn't identified a qualifying edge for the current slate.</p>
        </div>`;
      return;
    }
    el.innerHTML = items
      .map((item) => {
        const recBits = [item.recommendation ? String(item.recommendation).toUpperCase() : "", item.line != null ? item.line : ""]
          .filter(Boolean)
          .join(" ");
        const line = fmtAmerican(item.odds);
        const book = item.sportsbook ? String(item.sportsbook) : "";
        const win = fmtPct(item.win_prob);
        const edge = fmtSignedPct(item.edge);
        return `
          <a class="hl-edge-card" href="${escapeHtml(edgeHref(item))}">
            <div class="hl-edge-top">
              <span class="hl-sport">${escapeHtml(SPORT_LABEL[item.sport] || item.sport)}</span>
              <span class="hl-edge-kind">${escapeHtml(item.kind === "prop" ? "Player prop" : "Game")}</span>
            </div>
            <h3 class="hl-edge-name">${escapeHtml(item.name)}</h3>
            <p class="hl-edge-sub">${escapeHtml(item.matchup || "")}</p>
            <p class="hl-edge-market">${escapeHtml(item.market || "")}</p>
            <p class="hl-edge-rec">${escapeHtml(recBits)}${line ? ` · ${escapeHtml(line)}` : ""}${book ? ` · ${escapeHtml(book)}` : ""}</p>
            <div class="hl-edge-stats">
              ${win ? `<span><em>Win prob</em><b>${escapeHtml(win)}</b></span>` : ""}
              ${edge ? `<span><em>Edge</em><b class="hl-stat-pos">${escapeHtml(edge)}</b></span>` : ""}
              ${item.confidence ? `<span><em>Confidence</em><b>${escapeHtml(item.confidence)}</b></span>` : ""}
            </div>
          </a>`;
      })
      .join("");
  }

  function gameStartLabel(game) {
    const raw =
      game.start_time_et ||
      game.start_time_local ||
      game.game_time_et ||
      game.start_time_utc ||
      game.commence_time ||
      game.start_time;
    if (!raw) {
      const status = String(game.status || "").toLowerCase();
      if (status.includes("live") || status.includes("in")) return "Live";
      if (status.includes("final") || status.includes("ft")) return "Final";
      return "TBD";
    }
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return String(raw);
    return d.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/New_York",
    });
  }

  function teamShort(name) {
    if (!name) return "—";
    const parts = String(name).trim().split(/\s+/);
    return parts[parts.length - 1];
  }

  function renderGameFilters(el, sports, active, onChange) {
    if (!el) return;
    const available = ["all"].concat(SPORT_ORDER.filter((key) => (sports[key] || 0) > 0));
    el.innerHTML = available
      .map((key) => {
        const label = key === "all" ? "All sports" : SPORT_LABEL[key];
        return `<button type="button" class="hl-filter${key === active ? " is-active" : ""}" data-sport="${key}">${label}</button>`;
      })
      .join("");
    el.querySelectorAll("[data-sport]").forEach((btn) => {
      btn.addEventListener("click", () => onChange(btn.getAttribute("data-sport")));
    });
  }

  function renderGames(el, games, edgeCounts, sport) {
    if (!el) return;
    const rows = (games || []).filter((g) => sport === "all" || sportOf(g) === sport);
    if (!rows.length) {
      el.innerHTML = `<div class="hl-empty"><h3>No games on today's slate</h3><p>Check back after the next schedule refresh.</p></div>`;
      return;
    }
    el.innerHTML = rows
      .map((game) => {
        const sportKey = sportOf(game);
        const away = game.away_team_abbr || teamShort(game.away_team);
        const home = game.home_team_abbr || teamShort(game.home_team);
        const count = edgeCounts[String(game.game_id || "")] || 0;
        return `
          <a class="hl-game-row" href="${escapeHtml(gameHref(sportKey, game.game_id))}">
            <span class="hl-game-time">${escapeHtml(gameStartLabel(game))}</span>
            <span class="hl-game-match">${escapeHtml(away)} <span class="hl-game-at">@</span> ${escapeHtml(home)}</span>
            ${count ? `<span class="hl-game-edges">Top Edges ${count}</span>` : `<span></span>`}
          </a>`;
      })
      .join("");
  }

  function insightMetrics(edges) {
    const withProb = edges.filter((e) => e.win_prob != null);
    const withEdge = edges.filter((e) => e.edge != null);
    const markets = {};
    edges.forEach((e) => {
      if (e.kind !== "prop" || !e.market) return;
      markets[e.market] = (markets[e.market] || 0) + 1;
    });
    const topMarket = Object.entries(markets).sort((a, b) => b[1] - a[1])[0] || null;
    const avgProb = withProb.length
      ? withProb.reduce((s, e) => s + e.win_prob, 0) / withProb.length
      : null;
    const avgEdge = withEdge.length ? withEdge.reduce((s, e) => s + e.edge, 0) / withEdge.length : null;
    return {
      total: edges.length,
      avgProb,
      avgEdge,
      topMarket,
      markets: Object.entries(markets)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5),
    };
  }

  function renderInsights(el, marketsEl, stats) {
    if (!el) return;
    const cards = [];
    if (stats.total) {
      cards.push({ label: "Total edges found", value: String(stats.total) });
    }
    const avgP = fmtPct(stats.avgProb);
    if (avgP) cards.push({ label: "Avg win probability", value: avgP });
    const avgE = fmtSignedPct(stats.avgEdge);
    if (avgE) cards.push({ label: "Avg edge", value: avgE, pos: true });
    if (stats.topMarket) {
      cards.push({
        label: "Top market",
        value: stats.topMarket[0],
        note: `${stats.topMarket[1]} ${stats.topMarket[1] === 1 ? "opportunity" : "opportunities"}`,
      });
    }
    if (!cards.length) {
      el.innerHTML = `<div class="hl-empty"><h3>No insight totals yet</h3><p>Totals appear when today's model slate has qualifying edges.</p></div>`;
      if (marketsEl) marketsEl.hidden = true;
      return;
    }
    el.innerHTML = cards
      .map(
        (c) => `
        <div class="hl-insight">
          <dt>${escapeHtml(c.label)}</dt>
          <dd${c.pos ? ' class="hl-stat-pos"' : ""}>${escapeHtml(c.value)}</dd>
          ${c.note ? `<small>${escapeHtml(c.note)}</small>` : ""}
        </div>`
      )
      .join("");
    if (marketsEl && stats.markets.length) {
      const max = stats.markets[0][1] || 1;
      marketsEl.hidden = false;
      marketsEl.innerHTML = `
        <h3>Top Markets</h3>
        ${stats.markets
          .map(
            ([name, count]) => `
            <div class="hl-market-row">
              <span>${escapeHtml(name)}</span>
              <span class="hl-market-bar" aria-hidden="true"><i style="width:${Math.max(8, (count / max) * 100)}%"></i></span>
              <span class="hl-market-count">${count}</span>
            </div>`
          )
          .join("")}`;
    } else if (marketsEl) {
      marketsEl.hidden = true;
    }
  }

  function renderPicks(el, payload, signedIn) {
    if (!el) return;
    if (!signedIn) {
      el.innerHTML = `
        <header class="hl-section-head"><h2>My Picks</h2></header>
        <p class="hl-picks-copy">Save games and player props to track them in one place.</p>
        <a class="ntg-btn ntg-btn-primary" href="/signin?next=${encodeURIComponent("/")}">Sign in</a>`;
      return;
    }
    const props = payload?.props || [];
    const games = payload?.games || [];
    const total = props.length + games.length;
    const today = typeof window.ntgSlateTodayIso === "function" ? window.ntgSlateTodayIso() : "";
    const addedToday = [...props, ...games].filter((item) => String(item.saved_at || "").slice(0, 10) === today).length;
    const upcoming = (payload?.counts && payload.counts.upcoming) || 0;
    el.innerHTML = `
      <header class="hl-section-head"><h2>My Picks</h2></header>
      <div class="hl-picks-count">${total}</div>
      <p class="hl-picks-copy">${
        total
          ? `${upcoming} active${addedToday ? ` · ${addedToday} added today` : ""}`
          : "Save games and player props to track them here."
      }</p>
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
        first.push(
          fetchJSON("/api/daily/props?sport=mlb&limit=20&cache_only=true&scan=false").then((d) => (mlbProps = d)).catch(() => null)
        );
      }
      if (!perf) first.push(fetchJSON("/api/performance/summary?days=30").then((d) => (perf = d)).catch(() => null));
      if (!status) first.push(fetchJSON("/api/status/refresh").then((d) => (status = d)).catch(() => null));
      if (first.length) await Promise.all(first);
    } catch (_) {
      errorState($("hl-top-edges"), "Unable to load today's board", "Try refreshing in a moment.", () => loadLanding(progress));
      errorState($("hl-performance"), "Unable to load performance", "Try again in a moment.", () => loadLanding(progress));
      setProgress(progress, 1, "Ready");
      return null;
    }

    tracker = tracker || perf?.prop_tracker || null;
    renderPerformance($("hl-performance"), tracker, perf);

    const ranked = asEdgesFromSingles(summary?.top_singles || []);
    const gameEdges = asEdgesFromSlate(summary);
    let edges = mergeEdges(ranked, gameEdges.concat(asEdgesFromProps(mlbProps, "mlb")), 16);
    const visible = [];
    if (ranked[0]) visible.push(ranked[0]);
    const firstGame = gameEdges.find((g) => g.name && g.name !== ranked[0]?.name);
    if (firstGame) visible.push(firstGame);
    edges.forEach((item) => {
      if (visible.length >= 4) return;
      if (!visible.some((v) => v.name === item.name && v.market_type === item.market_type)) visible.push(item);
    });
    renderEdges($("hl-top-edges"), visible);
    renderInsights($("hl-insights"), $("hl-markets"), insightMetrics(edges));
    setProgress(progress, 0.62, "Loading today's games…");

    const signedIn = Boolean((window.pbUserAuth || {}).signed_in);
    try {
      const extra = await Promise.all([
        fetchJSON("/api/scores/today?sport=all").catch(() => embedded?.scores || { games: [], sports: {} }),
        fetchJSON("/api/daily/props?sport=nfl&limit=20&cache_only=true&scan=false").catch(() => null),
        signedIn ? fetch("/api/watchlist", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null) : Promise.resolve(null),
      ]);
      scores = extra[0];
      nflProps = extra[1];
      watch = extra[2];
    } catch (_) {
      scores = embedded?.scores || { games: [], sports: {} };
    }

    edges = mergeEdges(edges, asEdgesFromProps(nflProps, "nfl"), 16);
    edges.forEach((item) => {
      if (visible.length >= 4) return;
      if (!visible.some((v) => v.name === item.name && v.market_type === item.market_type)) visible.push(item);
    });
    renderEdges($("hl-top-edges"), visible);
    renderInsights($("hl-insights"), $("hl-markets"), insightMetrics(edges));

    const games = scores?.games || [];
    const sports = scores?.sports || {};
    SPORT_ORDER.forEach((key) => {
      if (sports[key] == null) sports[key] = games.filter((g) => sportOf(g) === key).length;
    });
    const counts = edgeCountsByGame(edges);
    const paintGames = (sport) => {
      renderGameFilters($("hl-game-filters"), sports, sport, paintGames);
      renderGames($("hl-games"), games, counts, sport);
    };
    paintGames("all");

    renderPicks($("hl-picks"), watch, signedIn);

    const live = $("hl-live-status");
    if (live) {
      if (typeof window.formatRefreshStatus === "function" && status) {
        live.textContent = window.formatRefreshStatus(status);
      } else if (status?.display_updated_at || status?.ran_at) {
        live.textContent = `Updated ${new Date(status.display_updated_at || status.ran_at).toLocaleTimeString([], {
          hour: "numeric",
          minute: "2-digit",
        })}`;
      } else {
        live.textContent = "Model slate updates through the day.";
      }
    }

    setProgress(progress, 1, "Ready");
    return { summary, scores, edges };
  }

  window.hydrateHomeLanding = loadLanding;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (!window.__ntgHomeLandingBooted) {
        window.__ntgHomeLandingBooted = true;
        if (!document.body?.dataset?.ntgSplash) loadLanding({ value: 0 });
      }
    });
  }
})();
