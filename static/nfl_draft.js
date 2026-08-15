/**
 * NFL snake draft room — board / players / queue + player insight modal.
 */
(function () {
  const STORAGE_KEY = "ntg_nfl_draft_v2";
  const WRT_ELIGIBLE = new Set(["RB", "WR", "TE"]);
  const SF_ELIGIBLE = new Set(["QB", "RB", "WR", "TE"]);
  const DEFAULT_SLOT_COUNTS = {
    QB: 1,
    RB: 2,
    WR: 2,
    TE: 1,
    WRT: 2,
    SUPERFLEX: 0,
    K: 1,
    DST: 1,
    BENCH: 6,
    IR: 0,
  };
  const DEFAULT_MAXES = { QB: 4, RB: 7, WR: 7, TE: 3, DST: 3, K: 3 };
  const TIMER_SECS = 90;

  const state = {
    leagueSize: 10,
    userSlot: 3,
    scoring: "half_ppr",
    slotCounts: { ...DEFAULT_SLOT_COUNTS },
    rosterSize: 16,
    positionMaxes: { ...DEFAULT_MAXES },
    mockMode: true,
    mockSeed: null,
    mockPersonalities: null,
    aiBusy: false,
    picks: [],
    players: [],
    playersById: {},
    rec: null,
    fitById: {},
    posFilter: "",
    search: "",
    inRoom: false,
    tab: "players",
    boardMode: "roster",
    queue: [],
    modalPlayerId: null,
    timerLeft: TIMER_SECS,
    timerId: null,
  };

  const els = {};

  function $(id) {
    return document.getElementById(id);
  }

  function teamSlotForOverall(overall, leagueSize) {
    const roundNum = Math.floor((overall - 1) / leagueSize) + 1;
    const pickInRound = ((overall - 1) % leagueSize) + 1;
    if (roundNum % 2 === 1) return pickInRound;
    return leagueSize - pickInRound + 1;
  }

  function overallToRound(overall, leagueSize) {
    return Math.floor((overall - 1) / leagueSize) + 1;
  }

  function pickLabel(overall, leagueSize) {
    const r = overallToRound(overall, leagueSize);
    const p = ((overall - 1) % leagueSize) + 1;
    return `${r}.${p}`;
  }

  function expandSlotCounts(counts) {
    const c = { ...DEFAULT_SLOT_COUNTS, ...(counts || {}) };
    const order = [
      "QB",
      "RB",
      "WR",
      "TE",
      "WRT",
      "SUPERFLEX",
      "K",
      "DST",
      "BENCH",
      "IR",
    ];
    const out = [];
    order.forEach((key) => {
      const n = Math.max(0, Number(c[key]) || 0);
      for (let i = 0; i < n; i++) out.push(key);
    });
    return out;
  }

  function starterTemplate() {
    return expandSlotCounts(state.slotCounts).filter(
      (s) => s !== "BENCH" && s !== "IR"
    );
  }

  function rosterTemplate() {
    return expandSlotCounts(state.slotCounts).filter((s) => s !== "IR");
  }

  function rosterCapacity() {
    return starterTemplate().length + (Number(state.slotCounts.BENCH) || 0);
  }

  function totalPicks() {
    return state.leagueSize * rosterCapacity();
  }

  function apiLeaguePayload() {
    return {
      league_size: state.leagueSize,
      scoring: state.scoring,
      slot_counts: state.slotCounts,
      roster_size: rosterCapacity(),
      position_maxes: state.positionMaxes,
      superflex: (Number(state.slotCounts.SUPERFLEX) || 0) > 0,
    };
  }

  function readSlotCountsFromForm() {
    const clamp = (id, fallback) => {
      const el = $(id);
      const n = Number(el && el.value);
      if (!Number.isFinite(n)) return fallback;
      return Math.max(0, Math.min(20, Math.round(n)));
    };
    return {
      QB: clamp("slot-qb", 1),
      RB: clamp("slot-rb", 2),
      WR: clamp("slot-wr", 2),
      TE: clamp("slot-te", 1),
      WRT: clamp("slot-wrt", 2),
      SUPERFLEX: clamp("slot-sf", 0),
      K: clamp("slot-k", 1),
      DST: clamp("slot-dst", 1),
      BENCH: clamp("slot-bench", 6),
      IR: clamp("slot-ir", 0),
    };
  }

  function writeSlotCountsToForm(counts) {
    const c = { ...DEFAULT_SLOT_COUNTS, ...(counts || {}) };
    const map = {
      QB: "slot-qb",
      RB: "slot-rb",
      WR: "slot-wr",
      TE: "slot-te",
      WRT: "slot-wrt",
      SUPERFLEX: "slot-sf",
      K: "slot-k",
      DST: "slot-dst",
      BENCH: "slot-bench",
      IR: "slot-ir",
    };
    Object.keys(map).forEach((key) => {
      const el = $(map[key]);
      if (el) el.value = String(c[key]);
    });
    updateSlotSummary();
  }

  function updateSlotSummary() {
    const el = $("slot-summary");
    if (!el) return;
    const c = readSlotCountsFromForm();
    const starters = expandSlotCounts(c).filter(
      (s) => s !== "BENCH" && s !== "IR"
    ).length;
    const bench = Number(c.BENCH) || 0;
    el.textContent = `Roster capacity: ${starters + bench} (${starters} starters + ${bench} bench)`;
  }

  function readMaxesFromForm() {
    const clamp = (id, fallback) => {
      const el = $(id);
      const n = Number(el && el.value);
      if (!Number.isFinite(n)) return fallback;
      return Math.max(0, Math.min(20, Math.round(n)));
    };
    return {
      QB: clamp("max-qb", 4),
      RB: clamp("max-rb", 7),
      WR: clamp("max-wr", 7),
      TE: clamp("max-te", 3),
      DST: clamp("max-dst", 3),
      K: clamp("max-k", 3),
    };
  }

  function writeMaxesToForm(maxes) {
    const m = { ...DEFAULT_MAXES, ...(maxes || {}) };
    const map = {
      QB: "max-qb",
      RB: "max-rb",
      WR: "max-wr",
      TE: "max-te",
      DST: "max-dst",
      K: "max-k",
    };
    Object.keys(map).forEach((pos) => {
      const el = $(map[pos]);
      if (el) el.value = String(m[pos]);
    });
  }

  function countPos(rosterPlayers, pos) {
    return rosterPlayers.filter((p) => p.position === pos).length;
  }

  function canAddToTeam(teamSlot, position) {
    const roster = rosterForTeam(teamSlot);
    if (roster.length >= rosterCapacity()) return false;
    const max = state.positionMaxes[position];
    if (max == null) return true;
    return countPos(roster, position) < max;
  }

  function nextOverall() {
    if (!state.picks.length) return 1;
    return Math.max(...state.picks.map((p) => p.overall)) + 1;
  }

  function draftedIds() {
    return new Set(state.picks.map((p) => p.player_id));
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function posClass(pos) {
    return `pos-${(pos || "").toLowerCase()}`;
  }

  function fitClass(pct) {
    if (pct == null) return "";
    if (pct >= 85) return "fit-hot";
    if (pct >= 70) return "fit-good";
    if (pct >= 55) return "fit-ok";
    return "fit-low";
  }

  function save() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          leagueSize: state.leagueSize,
          userSlot: state.userSlot,
          scoring: state.scoring,
          slotCounts: state.slotCounts,
          rosterSize: rosterCapacity(),
          positionMaxes: state.positionMaxes,
          mockMode: state.mockMode,
          mockSeed: state.mockSeed,
          mockPersonalities: state.mockPersonalities,
          picks: state.picks,
          queue: state.queue,
          inRoom: state.inRoom,
          tab: state.tab,
          boardMode: state.boardMode,
        })
      );
    } catch (_) {
      /* ignore */
    }
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function fillSlotOptions() {
    const size = Number(els.leagueSize.value) || 10;
    const cur = Number(els.userSlot.value) || 1;
    els.userSlot.innerHTML = "";
    for (let i = 1; i <= size; i++) {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = String(i);
      if (i === Math.min(cur, size)) opt.selected = true;
      els.userSlot.appendChild(opt);
    }
  }

  async function fetchPlayers() {
    const res = await fetch(
      `/api/fantasy/nfl/players?scoring=${encodeURIComponent(state.scoring)}`
    );
    if (!res.ok) throw new Error("Failed to load players");
    const data = await res.json();
    state.players = data.players || [];
    state.playersById = Object.fromEntries(
      state.players.map((p) => [p.player_id, p])
    );
  }

  async function fetchRecommend() {
    const body = {
      ...apiLeaguePayload(),
      user_slot: state.userSlot,
      picks: state.picks,
    };
    const res = await fetch("/api/fantasy/nfl/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Recommend failed");
    }
    state.rec = await res.json();
    state.fitById = {};
    for (const row of state.rec.top_pool || []) {
      state.fitById[row.player_id] = row;
    }
    return state.rec;
  }

  async function fetchInsight(playerId) {
    const res = await fetch("/api/fantasy/nfl/insight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...apiLeaguePayload(),
        player_id: playerId,
        user_slot: state.userSlot,
        picks: state.picks,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Insight failed");
    }
    return res.json();
  }

  function setAiBusy(busy) {
    state.aiBusy = Boolean(busy);
    if (els.roomPanel) {
      els.roomPanel.classList.toggle("is-ai-busy", state.aiBusy);
    }
    if (els.btnAiToMe) els.btnAiToMe.disabled = state.aiBusy;
    if (els.btnAiFinish) els.btnAiFinish.disabled = state.aiBusy;
    if (els.btnDraftRec) els.btnDraftRec.disabled = state.aiBusy;
  }

  function stampPicksMeta(picks) {
    return (picks || []).map((p) => {
      const pl = state.playersById[p.player_id];
      if (!pl) return p;
      return {
        ...p,
        name: p.name || pl.name,
        position: p.position || pl.position,
      };
    });
  }

  async function mockAdvance(mode) {
    if (state.aiBusy) return null;
    if (nextOverall() > totalPicks()) return null;
    setAiBusy(true);
    try {
      const body = {
        ...apiLeaguePayload(),
        user_slot: state.userSlot,
        picks: state.picks,
        mode: mode || "until_user",
        seed: state.mockSeed,
        personalities: state.mockPersonalities || undefined,
      };
      const res = await fetch("/api/fantasy/nfl/mock-advance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        const detail = data.detail || data.error || "AI advance failed";
        throw new Error(
          typeof detail === "string" ? detail : detail.detail || JSON.stringify(detail)
        );
      }
      state.picks = stampPicksMeta(data.picks || state.picks);
      if (data.personalities) state.mockPersonalities = data.personalities;
      if (state.mockSeed == null) {
        state.mockSeed = Math.floor(Math.random() * 1e9);
      }
      const n = (data.cpu_picks || []).length;
      if (n && els.clockAutopick) {
        const last = data.cpu_picks[n - 1];
        els.clockAutopick.textContent = n === 1
          ? `AI: ${last.name} (${last.position}) → Team ${last.team_slot}`
          : `AI made ${n} picks · last ${last.name} (${last.position})`;
      }
      return data;
    } finally {
      setAiBusy(false);
    }
  }

  async function maybeAutoAdvanceMock() {
    if (!state.mockMode || state.aiBusy) return;
    const overall = nextOverall();
    if (overall > totalPicks()) return;
    const onClock = teamSlotForOverall(overall, state.leagueSize);
    if (onClock === state.userSlot) return;
    await mockAdvance("until_user");
    await refreshBoard();
  }

  function projPts(player) {
    if (player && player.projected_points != null) return Number(player.projected_points);
    const fit = state.fitById[player?.player_id];
    if (fit && fit.projected_points != null) return Number(fit.projected_points);
    return 0;
  }

  function assignSlots(rosterPlayers) {
    const slots = rosterTemplate().map((s) => ({ slot: s, player: null }));
    const indexed = rosterPlayers
      .map((p, j) => ({ p, j }))
      .sort((a, b) => projPts(b.p) - projPts(a.p));
    const used = new Set();

    // Dedicated starters
    for (let i = 0; i < slots.length; i++) {
      const su = slots[i].slot;
      if (su === "WRT" || su === "FLEX" || su === "SUPERFLEX" || su === "BENCH" || su === "IR")
        continue;
      for (const { p, j } of indexed) {
        if (used.has(j)) continue;
        if (p.position === su) {
          slots[i].player = p;
          used.add(j);
          break;
        }
      }
    }
    // WRT / SUPERFLEX
    for (let i = 0; i < slots.length; i++) {
      const su = slots[i].slot;
      if (su !== "WRT" && su !== "FLEX" && su !== "SUPERFLEX") continue;
      if (slots[i].player) continue;
      const ok = su === "SUPERFLEX" ? SF_ELIGIBLE : WRT_ELIGIBLE;
      for (const { p, j } of indexed) {
        if (used.has(j)) continue;
        if (ok.has(p.position)) {
          slots[i].player = p;
          used.add(j);
          break;
        }
      }
    }
    // Bench
    for (let i = 0; i < slots.length; i++) {
      if (slots[i].slot !== "BENCH" || slots[i].player) continue;
      for (const { p, j } of indexed) {
        if (used.has(j)) continue;
        slots[i].player = p;
        used.add(j);
        break;
      }
    }
    return slots;
  }

  function rosterForTeam(slot) {
    return state.picks
      .filter((p) => p.team_slot === slot)
      .sort((a, b) => a.overall - b.overall)
      .map((p) => {
        const pl = state.playersById[p.player_id];
        if (pl) return { ...pl, overall: p.overall };
        // Do not drop unknown IDs — undercount was a max-bypass bug
        return {
          player_id: p.player_id,
          name: p.name || p.player_id,
          position: p.position || "RB",
          overall: p.overall,
          _unknown: true,
        };
      });
  }

  function chipClass(text) {
    const t = (text || "").toLowerCase();
    if (t.includes("bench pick") || t.includes("value override"))
      return "draft-chip draft-chip--bench";
    if (t.includes("fills") || t.includes("scarce") || t.includes("wrt"))
      return "draft-chip draft-chip--need";
    if (t.includes("bye")) return "draft-chip draft-chip--warn";
    return "draft-chip";
  }

  function roleBadgeClass(badge) {
    const t = (badge || "").toUpperCase();
    if (t.includes("BENCH")) return "draft-role-badge is-bench";
    if (t.includes("WRT")) return "draft-role-badge is-wrt";
    return "draft-role-badge is-starter";
  }

  function renderRoleUi(primary) {
    const badgeEl = els.recRoleBadge;
    const roleLine = els.recRoleLine;
    if (!badgeEl || !roleLine) return;
    const role = primary?.projected_role;
    if (!primary || !role) {
      badgeEl.hidden = true;
      badgeEl.classList.add("hidden");
      badgeEl.textContent = "";
      roleLine.textContent = "";
      return;
    }
    const badge = role.badge || (role.is_bench ? "BENCH VALUE" : "STARTER");
    badgeEl.hidden = false;
    badgeEl.classList.remove("hidden");
    badgeEl.className = roleBadgeClass(badge);
    badgeEl.textContent = badge;
    const prefix = role.is_bench
      ? "Projected role: BENCH"
      : role.is_wrt
        ? "Projected role: WRT"
        : "Projected role: STARTER";
    roleLine.textContent = `${prefix} — ${role.label || ""}`;
  }

  function resetTimer() {
    state.timerLeft = TIMER_SECS;
    renderTimer();
    if (state.timerId) clearInterval(state.timerId);
    if (!state.inRoom) return;
    const reduce =
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    state.timerId = setInterval(() => {
      state.timerLeft = Math.max(0, state.timerLeft - 1);
      renderTimer();
    }, 1000);
  }

  function renderTimer() {
    const m = String(Math.floor(state.timerLeft / 60)).padStart(2, "0");
    const s = String(state.timerLeft % 60).padStart(2, "0");
    els.clockTimer.textContent = `${m}:${s}`;
  }

  function renderClock() {
    const rec = state.rec;
    const meta = rec?.board_meta || {};
    const overall = meta.current_overall;
    const done = overall == null || overall > totalPicks();
    const onClock = meta.on_clock_slot;
    const you = meta.user_on_clock;

    els.clockBar.classList.toggle("is-you", Boolean(you) && !done);
    els.clockBar.classList.toggle("is-done", Boolean(done));

    if (done) {
      els.clockLabel.textContent = "Draft complete";
      els.clockDetail.textContent = `${state.picks.length} picks recorded`;
      els.clockAutopick.textContent = "";
    } else {
      els.clockLabel.textContent = you
        ? "You are on the clock"
        : `Team ${onClock} is on the clock`;
      const rnd = overallToRound(overall, state.leagueSize);
      els.clockDetail.textContent = `Round ${rnd} · Overall #${overall} · Pick ${pickLabel(overall, state.leagueSize)}`;
      const primary = rec?.primary;
      if (primary) {
        els.clockAutopick.textContent = you
          ? `Recommended: ${primary.name}, ${primary.position}${primary.team ? `, ${primary.team}` : ""} · ${primary.fit_pct ?? "—"}% fit`
          : `Your next pick (#${meta.user_next_overall}): plan ${primary.name} (${primary.fit_pct ?? "—"}% fit)`;
      } else {
        els.clockAutopick.textContent = "";
      }
    }

    els.recEyebrow.textContent = you
      ? "Draft now"
      : meta.user_next_overall
        ? `Your next pick · #${meta.user_next_overall}`
        : "Your next pick";

    const primary = rec?.primary;
    if (!primary) {
      els.recName.textContent = done ? "Board complete" : "No players left";
      els.recMeta.textContent = "";
      els.recChips.innerHTML = "";
      els.recAlts.innerHTML = "";
      els.btnDraftRec.disabled = true;
      renderRoleUi(null);
    } else {
      els.recName.textContent = primary.name;
      els.recMeta.textContent = `${primary.position} · ${primary.team || "—"}${
        primary.bye ? ` · Bye ${primary.bye}` : ""
      } · ${primary.projected_points != null ? `${primary.projected_points} proj` : `Proj pick ${primary.projected_pick ?? "—"}`}${
        primary.vorp != null ? ` · VORP ${primary.vorp > 0 ? "+" : ""}${primary.vorp}` : ""
      } · ${primary.fit_pct ?? primary.score ?? "—"} score`;
      renderRoleUi(primary);
      els.recChips.innerHTML = (primary.reasons || [])
        .map((r) => `<span class="${chipClass(r)}">${escapeHtml(r)}</span>`)
        .join("");
      els.btnDraftRec.disabled = done || !you;
      els.recAlts.innerHTML = (rec.alternates || [])
        .map((alt) => {
          const rlabel = alt.projected_role?.label
            ? ` · ${alt.projected_role.label}`
            : "";
          return `<li data-id="${escapeAttr(alt.player_id)}" role="button" tabindex="0">
            <span class="alt-name">${escapeHtml(alt.name)}</span>
            <span class="alt-meta">${escapeHtml(alt.position)}${escapeHtml(rlabel)} · ${alt.fit_pct ?? alt.score ?? "—"}</span>
            ${alt.why_not ? `<span class="alt-why">${escapeHtml(alt.why_not)}</span>` : ""}
          </li>`;
        })
        .join("");
    }

    const needs = meta.user_needs || [];
    const outlook = meta.position_outlook || {};
    const outlookBits = Object.keys(outlook)
      .slice(0, 6)
      .map((k) => `${k}: ${outlook[k]}`)
      .join(" · ");
    els.needsLine.textContent = [
      needs.length ? `Open: ${needs.join(", ")}` : "Starters filled",
      outlookBits ? `Pool: ${outlookBits}` : "",
    ]
      .filter(Boolean)
      .join(" · ");

    const counts = meta.lineup?.counts || {};
    if (els.fillLine) {
      const sf = counts.starters_filled ?? 0;
      const st = counts.starters_total ?? starterTemplate().length;
      const wf = counts.wrt_filled ?? 0;
      const wt = counts.wrt_total ?? state.slotCounts.WRT ?? 0;
      const bf = counts.bench_filled ?? 0;
      const bt = counts.bench_total ?? state.slotCounts.BENCH ?? 0;
      els.fillLine.textContent = `STARTERS ${sf}/${st} · WRT ${wf}/${wt} · BENCH ${bf}/${bt}`;
    }
  }

  function renderPickStrip() {
    const cur = nextOverall();
    const start = Math.max(1, cur - 4);
    const end = Math.min(totalPicks(), cur + 5);
    const byOverall = Object.fromEntries(state.picks.map((p) => [p.overall, p]));
    const bits = [];
    for (let o = start; o <= end; o++) {
      const slot = teamSlotForOverall(o, state.leagueSize);
      const pick = byOverall[o];
      const pl = pick ? state.playersById[pick.player_id] : null;
      const isCur = o === cur && o <= totalPicks();
      const isYou = slot === state.userSlot;
      if (pl) {
        bits.push(`<button type="button" class="draft-strip-card ${posClass(pl.position)}${isYou ? " is-you" : ""}" data-insight="${escapeAttr(pl.player_id)}">
          <span class="strip-pick">${pickLabel(o, state.leagueSize)}</span>
          <span class="strip-name">${escapeHtml(pl.name)}</span>
          <span class="strip-meta">${escapeHtml(pl.position)} · ${escapeHtml(pl.team || "")}</span>
        </button>`);
      } else if (isCur) {
        bits.push(`<div class="draft-strip-card is-current${isYou ? " is-you" : ""}">
          <span class="strip-pick">${pickLabel(o, state.leagueSize)}</span>
          <span class="strip-name">On the clock</span>
          <button type="button" class="strip-make" id="strip-make-pick">Make pick</button>
        </div>`);
      } else {
        bits.push(`<div class="draft-strip-card is-empty${isYou ? " is-you" : ""}">
          <span class="strip-pick">${pickLabel(o, state.leagueSize)}</span>
          <span class="strip-name">Team ${slot}</span>
          <span class="strip-meta">Upcoming</span>
        </div>`);
      }
    }
    els.pickStrip.innerHTML = bits.join("");
  }

  function availablePlayers() {
    const taken = draftedIds();
    let list = state.players.filter((p) => !taken.has(p.player_id));
    if (state.posFilter) list = list.filter((p) => p.position === state.posFilter);
    const q = state.search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.team || "").toLowerCase().includes(q) ||
          p.position.toLowerCase().includes(q)
      );
    }
    return list;
  }

  function renderPlayers() {
    const list = availablePlayers();
    const clockSlot = state.rec?.board_meta?.on_clock_slot;
    els.playerList.innerHTML = list
      .slice(0, 220)
      .map((p) => {
        const fit = state.fitById[p.player_id];
        const fitPct = fit?.fit_pct;
        const blocked =
          clockSlot != null && !canAddToTeam(clockSlot, p.position);
        return `<div class="draft-player-row${blocked ? " is-blocked" : ""}" data-insight="${escapeAttr(p.player_id)}" role="button" tabindex="0">
          <span class="rk">${p.rank ?? "—"}</span>
          <span class="nm">${escapeHtml(p.name)}<small>${escapeHtml(p.team || "")}${p.bye ? ` · Bye ${p.bye}` : ""}${blocked ? " · at team max" : ""}</small></span>
          <span class="pos ${posClass(p.position)}">${escapeHtml(p.position)}</span>
          <span class="proj">${p.projected_pick ?? p.adp ?? "—"}</span>
          <span class="pwr">${p.power_rank ?? "—"}</span>
          <span class="fit ${fitClass(fitPct)}">${fitPct != null ? `${fitPct}%` : "—"}</span>
        </div>`;
      })
      .join("");
  }

  function renderBoardRoster() {
    const cols = [];
    for (let slot = 1; slot <= state.leagueSize; slot++) {
      const isUser = slot === state.userSlot;
      const isClock = state.rec?.board_meta?.on_clock_slot === slot;
      const assigned = assignSlots(rosterForTeam(slot));
      const cells = assigned
        .map(({ slot: s, player }) => {
          if (!player) {
            return `<div class="draft-cell is-empty"><span class="cell-slot">${escapeHtml(s)}</span><span class="cell-empty">—</span></div>`;
          }
          return `<button type="button" class="draft-cell ${posClass(player.position)}" data-insight="${escapeAttr(player.player_id)}">
            <span class="cell-top"><span class="cell-slot">${escapeHtml(s)}</span><span class="cell-pick">${pickLabel(player.overall, state.leagueSize)}</span></span>
            <span class="cell-name">${escapeHtml(player.name)}</span>
            <span class="cell-meta">${escapeHtml(player.team || "")} ${escapeHtml(player.position)}${player.bye ? ` (${player.bye})` : ""}</span>
          </button>`;
        })
        .join("");
      cols.push(`<div class="draft-col${isUser ? " is-user" : ""}${isClock ? " is-clock" : ""}">
        <header>${isUser ? "You" : `Team ${slot}`}</header>
        ${cells}
      </div>`);
    }
    els.boardGrid.innerHTML = `<div class="draft-board-roster">${cols.join("")}</div>`;
  }

  function renderBoardRound() {
    const rounds = rosterCapacity();
    let html = `<div class="draft-board-rounds" style="--teams:${state.leagueSize}">`;
    html += `<div class="draft-round-head"><span></span>`;
    for (let s = 1; s <= state.leagueSize; s++) {
      html += `<span class="${s === state.userSlot ? "is-user" : ""}">${s === state.userSlot ? "You" : s}</span>`;
    }
    html += `</div>`;
    const byOverall = Object.fromEntries(state.picks.map((p) => [p.overall, p]));
    for (let r = 1; r <= rounds; r++) {
      html += `<div class="draft-round-row"><span class="round-label">R${r}</span>`;
      for (let i = 0; i < state.leagueSize; i++) {
        const overall = (r - 1) * state.leagueSize + i + 1;
        const pick = byOverall[overall];
        const pl = pick ? state.playersById[pick.player_id] : null;
        const slot = teamSlotForOverall(overall, state.leagueSize);
        if (pl) {
          html += `<button type="button" class="draft-cell compact ${posClass(pl.position)}${slot === state.userSlot ? " is-user-cell" : ""}" data-insight="${escapeAttr(pl.player_id)}">
            <span class="cell-name">${escapeHtml(pl.name)}</span>
            <span class="cell-meta">${escapeHtml(pl.position)}</span>
          </button>`;
        } else {
          const isCur = overall === nextOverall();
          html += `<div class="draft-cell compact is-empty${isCur ? " is-current" : ""}${slot === state.userSlot ? " is-user-cell" : ""}"><span class="cell-empty">${pickLabel(overall, state.leagueSize)}</span></div>`;
        }
      }
      html += `</div>`;
    }
    html += `</div>`;
    els.boardGrid.innerHTML = html;
  }

  function renderBoard() {
    if (state.boardMode === "round") renderBoardRound();
    else renderBoardRoster();
  }

  function renderUserRoster() {
    const assigned = assignSlots(rosterForTeam(state.userSlot));
    const starters = assigned.filter((r) => r.slot !== "BENCH");
    const bench = assigned.filter((r) => r.slot === "BENCH");
    const line = ({ slot, player }) => {
      if (!player) {
        return `<div class="draft-roster-line"><span class="r-slot">${escapeHtml(slot)}</span><span class="r-empty">Empty</span><span></span></div>`;
      }
      return `<button type="button" class="draft-roster-line" data-insight="${escapeAttr(player.player_id)}">
          <span class="r-slot">${escapeHtml(slot)}</span>
          <span class="r-name">${escapeHtml(player.name)}</span>
          <span class="r-bye">${player.bye ? `Bye ${player.bye}` : ""}</span>
        </button>`;
    };
    els.userRoster.innerHTML = [
      `<div class="draft-roster-section"><h4>Starters</h4>${starters.map(line).join("")}</div>`,
      `<div class="draft-roster-section"><h4>Bench</h4>${bench.map(line).join("")}</div>`,
    ].join("");

    const counts = { QB: 0, RB: 0, WR: 0, TE: 0, DST: 0, K: 0 };
    for (const p of rosterForTeam(state.userSlot)) {
      if (counts[p.position] != null) counts[p.position] += 1;
    }
    els.posLimits.innerHTML = Object.keys(state.positionMaxes)
      .map(
        (pos) =>
          `<span class="${posClass(pos)}${counts[pos] >= state.positionMaxes[pos] ? " is-maxed" : ""}">${pos} ${counts[pos]}/${state.positionMaxes[pos]}</span>`
      )
      .join("");
  }

  function renderQueue() {
    const taken = draftedIds();
    const items = state.queue
      .map((id) => state.playersById[id])
      .filter((p) => p && !taken.has(p.player_id));
    if (!items.length) {
      els.queueList.innerHTML =
        '<li class="draft-queue-empty">Queue is empty — open a player and tap “Add to queue”.</li>';
      return;
    }
    els.queueList.innerHTML = items
      .map((p) => {
        const fit = state.fitById[p.player_id]?.fit_pct;
        return `<li>
          <button type="button" class="draft-queue-item" data-insight="${escapeAttr(p.player_id)}">
            <span class="pos ${posClass(p.position)}">${escapeHtml(p.position)}</span>
            <span class="nm">${escapeHtml(p.name)}</span>
            <span class="fit ${fitClass(fit)}">${fit != null ? `${fit}%` : "—"}</span>
          </button>
          <button type="button" class="draft-queue-remove" data-dequeue="${escapeAttr(p.player_id)}" aria-label="Remove">×</button>
        </li>`;
      })
      .join("");
  }

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".draft-tab").forEach((btn) => {
      const on = btn.getAttribute("data-tab") === tab;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    ["players", "board", "queue"].forEach((name) => {
      const panel = $(`tab-${name}`);
      const show = name === tab;
      panel.classList.toggle("hidden", !show);
      panel.hidden = !show;
    });
    save();
  }

  async function openModal(playerId) {
    state.modalPlayerId = playerId;
    els.modal.classList.remove("hidden");
    els.modal.hidden = false;
    els.modalName.textContent = "Loading…";
    els.modalMeta.textContent = "";
    els.modalAdp.textContent = "—";
    els.modalPower.textContent = "—";
    els.modalFit.textContent = "—";
    els.modalChips.innerHTML = "";
    els.modalNote.textContent = "";
    try {
      const data = await fetchInsight(playerId);
      const p = data.player;
      els.modalPos.textContent = p.position;
      els.modalPos.className = `draft-pos-pill ${posClass(p.position)}`;
      els.modalName.textContent = p.name;
        els.modalMeta.textContent = `${p.team || "FA"}${p.bye ? ` · Bye ${p.bye}` : ""} · Consensus #${p.rank}${p.projected_points != null ? ` · ${p.projected_points} proj pts` : ""}${p.tier ? ` · Tier ${p.tier}` : ""}`;
        els.modalAdp.textContent = String(p.projected_pick ?? p.adp ?? "—");
        els.modalPower.textContent = String(p.power_rank ?? "—");
      if (data.drafted) {
        els.modalFit.textContent = "Drafted";
        els.modalFit.className = "draft-stat-value";
        els.modalNote.textContent = data.pick
          ? `Taken at overall #${data.pick.overall} (Team ${data.pick.team_slot}).`
          : "Already drafted.";
        els.modalDraft.disabled = true;
      } else {
        const fit = data.fit_pct;
        els.modalFit.textContent = fit != null ? `${fit}%` : "—";
        els.modalFit.className = `draft-stat-value ${fitClass(fit)}`;
        els.modalChips.innerHTML = (data.reasons || [])
          .map((r) => `<span class="${chipClass(r)}">${escapeHtml(r)}</span>`)
          .join("");
        const clockOk = data.legal_for_clock_team !== false;
        const userOk = data.legal_for_user !== false;
        if (!clockOk) {
          els.modalNote.textContent = `Team on the clock is at the ${p.position} max (${state.positionMaxes[p.position]}).`;
          els.modalDraft.disabled = true;
        } else if (!userOk) {
          els.modalNote.textContent = `You are at the ${p.position} max — poor fit for your next pick, but another team can still take them.`;
          els.modalDraft.disabled = false;
          els.modalDraft.textContent = "Draft to team on clock";
        } else {
          els.modalNote.textContent =
            fit != null
              ? fit >= 85
                ? "Excellent value for your roster right now."
                : fit >= 70
                  ? "Solid fit versus board and your needs."
                  : fit >= 55
                    ? "Playable, but better fits may still be open."
                    : "Below your best available options."
              : "";
          const you = state.rec?.board_meta?.user_on_clock;
          els.modalDraft.disabled = false;
          els.modalDraft.textContent = you ? "Draft now" : "Draft to team on clock";
        }
      }
    } catch (err) {
      els.modalName.textContent = "Couldn’t load player";
      els.modalNote.textContent = String(err.message || err);
    }
  }

  function closeModal() {
    state.modalPlayerId = null;
    els.modal.classList.add("hidden");
    els.modal.hidden = true;
  }

  async function refreshBoard() {
    await fetchRecommend();
    renderClock();
    renderPickStrip();
    renderPlayers();
    renderBoard();
    renderUserRoster();
    renderQueue();
    save();
  }

  async function draftPlayer(playerId) {
    const overall = nextOverall();
    if (overall > totalPicks()) return;
    if (draftedIds().has(playerId)) return;
    const pl = state.playersById[playerId];
    if (!pl) return;
    const teamSlot = teamSlotForOverall(overall, state.leagueSize);
    if (!canAddToTeam(teamSlot, pl.position)) {
      alert(
        `Team ${teamSlot} is already at the ${pl.position} max (${state.positionMaxes[pl.position]}). Pick another position.`
      );
      return;
    }
    // Server-side hard validation (source of truth)
    try {
      const res = await fetch("/api/fantasy/nfl/apply-pick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          player_id: playerId,
          picks: state.picks,
          ...apiLeaguePayload(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        const detail = data.detail || data.error || data;
        alert(
          typeof detail === "string"
            ? detail
            : detail.detail || detail.error || "Illegal pick blocked"
        );
        return;
      }
      state.picks = data.picks || [
        ...state.picks,
        {
          overall,
          team_slot: teamSlot,
          player_id: playerId,
          position: pl.position,
          name: pl.name,
        },
      ];
      // Ensure position stamped for client max checks
      const last = state.picks[state.picks.length - 1];
      if (last && !last.position) {
        last.position = pl.position;
        last.name = pl.name;
      }
    } catch (err) {
      // Offline fallback: still enforce client gate
      state.picks.push({
        overall,
        team_slot: teamSlot,
        player_id: playerId,
        position: pl.position,
        name: pl.name,
      });
    }
    state.queue = state.queue.filter((id) => id !== playerId);
    resetTimer();
    closeModal();
    await refreshBoard();
    if (state.mockMode) {
      await maybeAutoAdvanceMock();
    }
  }

  function undo() {
    if (!state.picks.length || state.aiBusy) return;
    // Undo back through consecutive CPU picks until (and including) the last user pick,
    // or a single pick if undoing a CPU-only stretch.
    const maxO = Math.max(...state.picks.map((p) => p.overall));
    let cut = maxO;
    const last = state.picks.find((p) => p.overall === maxO);
    if (last && last.team_slot !== state.userSlot) {
      // Peel CPU picks until we hit a user pick (remove that too) or nothing left
      for (let o = maxO; o >= 1; o--) {
        const p = state.picks.find((x) => x.overall === o);
        if (!p) continue;
        cut = o;
        if (p.team_slot === state.userSlot) break;
      }
    }
    state.picks = state.picks.filter((p) => p.overall < cut);
    resetTimer();
    refreshBoard()
      .then(() => (state.mockMode ? maybeAutoAdvanceMock() : null))
      .catch(showError);
  }

  function resetDraft() {
    if (!confirm("Reset the entire draft? This clears all picks.")) return;
    state.picks = [];
    state.mockPersonalities = null;
    state.mockSeed = Math.floor(Math.random() * 1e9);
    resetTimer();
    refreshBoard()
      .then(() => (state.mockMode ? maybeAutoAdvanceMock() : null))
      .catch(showError);
  }

  function showError(err) {
    console.error(err);
    els.recName.textContent = "Something went wrong";
    els.recMeta.textContent = String(err.message || err);
  }

  function showSetup(show) {
    state.inRoom = !show;
    els.setupPanel.classList.toggle("hidden", !show);
    els.setupPanel.hidden = !show;
    els.roomPanel.classList.toggle("hidden", show);
    els.roomPanel.hidden = show;
  }

  async function enterRoom(fromSaved) {
    state.leagueSize = Number(els.leagueSize.value);
    state.userSlot = Number(els.userSlot.value);
    state.scoring = els.scoring.value || "half_ppr";
    state.slotCounts = readSlotCountsFromForm();
    state.rosterSize = rosterCapacity();
    state.positionMaxes = readMaxesFromForm();
    const mockEl = $("mock-mode");
    state.mockMode = mockEl ? mockEl.checked : true;
    if (!fromSaved) {
      state.picks = [];
      state.mockPersonalities = null;
      state.mockSeed = Math.floor(Math.random() * 1e9);
    } else if (state.mockSeed == null) {
      state.mockSeed = Math.floor(Math.random() * 1e9);
    }
    if (state.userSlot > state.leagueSize) state.userSlot = state.leagueSize;
    showSetup(false);
    setTab(state.tab || "players");
    resetTimer();
    await fetchPlayers();
    await refreshBoard();
    if (state.mockMode) {
      await maybeAutoAdvanceMock();
    }
  }

  function onInsightClick(e) {
    const btn = e.target.closest("[data-insight]");
    if (!btn) return;
    openModal(btn.getAttribute("data-insight"));
  }

  function bind() {
    els.setupPanel = $("setup-panel");
    els.roomPanel = $("room-panel");
    els.leagueSize = $("league-size");
    els.userSlot = $("user-slot");
    els.scoring = $("scoring");
    els.setupForm = $("setup-form");
    els.clockBar = $("clock-bar");
    els.clockLabel = $("clock-label");
    els.clockDetail = $("clock-detail");
    els.clockTimer = $("clock-timer");
    els.clockAutopick = $("clock-autopick");
    els.pickStrip = $("pick-strip");
    els.recEyebrow = $("rec-eyebrow");
    els.recRoleBadge = $("rec-role-badge");
    els.recName = $("rec-name");
    els.recMeta = $("rec-meta");
    els.recRoleLine = $("rec-role-line");
    els.recChips = $("rec-chips");
    els.recAlts = $("rec-alts");
    els.btnDraftRec = $("btn-draft-rec");
    els.btnAiToMe = $("btn-ai-to-me");
    els.btnAiFinish = $("btn-ai-finish");
    els.btnUndo = $("btn-undo");
    els.btnReset = $("btn-reset");
    els.btnBack = $("btn-back-setup");
    els.playerSearch = $("player-search");
    els.posFilters = $("pos-filters");
    els.playerList = $("player-list");
    els.boardGrid = $("board-grid");
    els.needsLine = $("needs-line");
    els.fillLine = $("fill-line");
    els.userRoster = $("user-roster");
    els.posLimits = $("pos-limits");
    els.queueList = $("queue-list");
    els.modal = $("player-modal");
    els.modalPos = $("modal-pos");
    els.modalName = $("modal-name");
    els.modalMeta = $("modal-meta");
    els.modalAdp = $("modal-adp");
    els.modalPower = $("modal-power");
    els.modalFit = $("modal-fit");
    els.modalChips = $("modal-chips");
    els.modalNote = $("modal-note");
    els.modalDraft = $("modal-draft");
    els.modalQueue = $("modal-queue");

    fillSlotOptions();
    els.leagueSize.addEventListener("change", fillSlotOptions);
    [
      "slot-qb",
      "slot-rb",
      "slot-wr",
      "slot-te",
      "slot-wrt",
      "slot-sf",
      "slot-k",
      "slot-dst",
      "slot-bench",
      "slot-ir",
    ].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener("input", updateSlotSummary);
    });
    updateSlotSummary();

    els.setupForm.addEventListener("submit", (e) => {
      e.preventDefault();
      enterRoom(false).catch(showError);
    });

    els.btnDraftRec.addEventListener("click", () => {
      const id = state.rec?.primary?.player_id;
      if (id) draftPlayer(id).catch(showError);
    });
    if (els.btnAiToMe) {
      els.btnAiToMe.addEventListener("click", () => {
        mockAdvance("until_user")
          .then(() => refreshBoard())
          .catch(showError);
      });
    }
    if (els.btnAiFinish) {
      els.btnAiFinish.addEventListener("click", () => {
        if (
          !confirm(
            "Let AI finish the entire draft (including your remaining picks)?"
          )
        ) {
          return;
        }
        mockAdvance("finish")
          .then(() => refreshBoard())
          .catch(showError);
      });
    }
    els.btnUndo.addEventListener("click", undo);
    els.btnReset.addEventListener("click", resetDraft);
    els.btnBack.addEventListener("click", () => {
      showSetup(true);
      if (state.timerId) clearInterval(state.timerId);
      save();
    });

    els.playerSearch.addEventListener("input", () => {
      state.search = els.playerSearch.value;
      renderPlayers();
    });

    els.posFilters.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-pos]");
      if (!btn) return;
      state.posFilter = btn.getAttribute("data-pos") || "";
      els.posFilters.querySelectorAll(".draft-pos-btn").forEach((b) => {
        b.classList.toggle("is-active", b === btn);
      });
      renderPlayers();
    });

    document.querySelectorAll(".draft-tab").forEach((btn) => {
      btn.addEventListener("click", () => setTab(btn.getAttribute("data-tab")));
    });

    $("btn-view-roster").addEventListener("click", () => {
      state.boardMode = "roster";
      $("btn-view-roster").classList.add("is-active");
      $("btn-view-round").classList.remove("is-active");
      renderBoard();
      save();
    });
    $("btn-view-round").addEventListener("click", () => {
      state.boardMode = "round";
      $("btn-view-round").classList.add("is-active");
      $("btn-view-roster").classList.remove("is-active");
      renderBoard();
      save();
    });

    els.playerList.addEventListener("click", onInsightClick);
    els.boardGrid.addEventListener("click", onInsightClick);
    els.pickStrip.addEventListener("click", (e) => {
      if (e.target.closest("#strip-make-pick")) {
        setTab("players");
        return;
      }
      onInsightClick(e);
    });
    els.userRoster.addEventListener("click", onInsightClick);
    els.recAlts.addEventListener("click", (e) => {
      const row = e.target.closest("[data-id]");
      if (!row) return;
      openModal(row.getAttribute("data-id"));
    });
    els.queueList.addEventListener("click", (e) => {
      const rm = e.target.closest("[data-dequeue]");
      if (rm) {
        const id = rm.getAttribute("data-dequeue");
        state.queue = state.queue.filter((x) => x !== id);
        renderQueue();
        save();
        return;
      }
      onInsightClick(e);
    });

    els.modal.addEventListener("click", (e) => {
      if (e.target.closest("[data-close-modal]")) closeModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });
    els.modalDraft.addEventListener("click", () => {
      if (state.modalPlayerId) draftPlayer(state.modalPlayerId).catch(showError);
    });
    els.modalQueue.addEventListener("click", () => {
      const id = state.modalPlayerId;
      if (!id) return;
      if (!state.queue.includes(id)) state.queue.push(id);
      renderQueue();
      save();
      closeModal();
      setTab("queue");
    });
  }

  function init() {
    bind();
    const saved = loadSaved();
    if (saved) {
      els.leagueSize.value = String(saved.leagueSize || 10);
      fillSlotOptions();
      els.userSlot.value = String(
        Math.min(saved.userSlot || 1, Number(els.leagueSize.value))
      );
      els.scoring.value = saved.scoring || "half_ppr";
      writeSlotCountsToForm(saved.slotCounts || DEFAULT_SLOT_COUNTS);
      writeMaxesToForm(saved.positionMaxes || DEFAULT_MAXES);
      const mockEl = $("mock-mode");
      if (mockEl) mockEl.checked = saved.mockMode !== false;
      state.mockMode = saved.mockMode !== false;
      state.mockSeed = saved.mockSeed ?? null;
      state.mockPersonalities = saved.mockPersonalities || null;
      state.slotCounts = {
        ...DEFAULT_SLOT_COUNTS,
        ...(saved.slotCounts || {}),
      };
      state.rosterSize = rosterCapacity();
      state.positionMaxes = {
        ...DEFAULT_MAXES,
        ...(saved.positionMaxes || {}),
      };
      state.queue = Array.isArray(saved.queue) ? saved.queue : [];
      state.tab = saved.tab || "players";
      state.boardMode = saved.boardMode || "roster";
      if (saved.inRoom && Array.isArray(saved.picks)) {
        state.picks = saved.picks;
        enterRoom(true).catch(showError);
        return;
      }
    }
    showSetup(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
