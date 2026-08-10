/**
 * NFL snake draft room — vanilla JS board + recommend API.
 * State persisted in localStorage; no accounts.
 */
(function () {
  const STORAGE_KEY = "ntg_nfl_draft_v1";
  const ROSTER_TEMPLATE = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "DST", "K"];

  const state = {
    leagueSize: 10,
    userSlot: 3,
    scoring: "half_ppr",
    picks: [],
    players: [],
    playersById: {},
    rec: null,
    posFilter: "",
    search: "",
    inRoom: false,
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

  function totalPicks() {
    return state.leagueSize * ROSTER_TEMPLATE.length;
  }

  function nextOverall() {
    if (!state.picks.length) return 1;
    return Math.max(...state.picks.map((p) => p.overall)) + 1;
  }

  function draftedIds() {
    return new Set(state.picks.map((p) => p.player_id));
  }

  function save() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          leagueSize: state.leagueSize,
          userSlot: state.userSlot,
          scoring: state.scoring,
          picks: state.picks,
          inRoom: state.inRoom,
        })
      );
    } catch (_) {
      /* ignore quota */
    }
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
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
      league_size: state.leagueSize,
      scoring: state.scoring,
      user_slot: state.userSlot,
      picks: state.picks,
      roster_template: ROSTER_TEMPLATE,
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
    return state.rec;
  }

  function chipClass(text) {
    const t = (text || "").toLowerCase();
    if (t.includes("fills") || t.includes("scarce")) return "draft-chip draft-chip--need";
    if (t.includes("bye")) return "draft-chip draft-chip--warn";
    return "draft-chip";
  }

  function renderHero() {
    const rec = state.rec;
    const meta = rec?.board_meta || {};
    const overall = meta.current_overall;
    const done = overall == null || overall > totalPicks();
    const onClock = meta.on_clock_slot;
    const you = meta.user_on_clock;

    if (done) {
      els.clockLabel.textContent = "Draft complete";
      els.clockDetail.textContent = `${state.picks.length} picks recorded`;
    } else {
      const rnd = overallToRound(overall, state.leagueSize);
      els.clockLabel.textContent = you ? "You're on the clock" : "On the clock";
      els.clockDetail.textContent = `Team ${onClock} · Round ${rnd} · Overall #${overall}`;
    }

    els.recEyebrow.textContent = you
      ? "Draft now"
      : meta.user_next_overall
        ? `Your next pick · overall #${meta.user_next_overall}`
        : "Your next pick";

    const primary = rec?.primary;
    if (!primary) {
      els.recName.textContent = done ? "Board complete" : "No players left";
      els.recMeta.textContent = "";
      els.recChips.innerHTML = "";
      els.recAlts.innerHTML = "";
      els.btnDraftRec.disabled = true;
      return;
    }

    els.recName.textContent = primary.name;
    els.recMeta.textContent = `${primary.position} · ${primary.team || "—"}${
      primary.bye ? ` · Bye ${primary.bye}` : ""
    } · Rank #${primary.rank}`;
    els.recChips.innerHTML = (primary.reasons || [])
      .map((r) => `<span class="${chipClass(r)}">${escapeHtml(r)}</span>`)
      .join("");

    els.btnDraftRec.disabled = done || !you;
    els.btnDraftRec.textContent = "Draft recommended";
    els.btnDraftRec.title = you
      ? "Add this player to your roster"
      : "Available when you are on the clock — pick for other teams from the list below";

    els.recAlts.innerHTML = (rec.alternates || [])
      .map((alt) => {
        const chips = (alt.reasons || [])
          .slice(0, 2)
          .map((r) => `<span class="${chipClass(r)}">${escapeHtml(r)}</span>`)
          .join("");
        return `<li data-id="${escapeAttr(alt.player_id)}" role="button" tabindex="0">
          <span class="alt-name">${escapeHtml(alt.name)}</span>
          <span class="alt-meta">${escapeHtml(alt.position)} · #${alt.rank}</span>
          ${chips}
        </li>`;
      })
      .join("");

    const needs = meta.user_needs || [];
    els.needsLine.textContent = needs.length
      ? `Your open starters: ${needs.join(", ")}`
      : "Your starters filled";
  }

  function availablePlayers() {
    const taken = draftedIds();
    let list = state.players.filter((p) => !taken.has(p.player_id));
    if (state.posFilter) {
      list = list.filter((p) => p.position === state.posFilter);
    }
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
    els.playerList.innerHTML = list
      .slice(0, 200)
      .map(
        (p) => `<div class="draft-player-row" data-id="${escapeAttr(p.player_id)}" role="button" tabindex="0">
          <span class="rk">${p.rank ?? "—"}</span>
          <span class="nm">${escapeHtml(p.name)}</span>
          <span class="pos">${escapeHtml(p.position)}</span>
          <span class="tm">${escapeHtml(p.team || "")}</span>
        </div>`
      )
      .join("");
  }

  function rosterForTeam(slot) {
    return state.picks
      .filter((p) => p.team_slot === slot)
      .sort((a, b) => a.overall - b.overall)
      .map((p) => state.playersById[p.player_id])
      .filter(Boolean);
  }

  function renderRosters() {
    const onClock = state.rec?.board_meta?.on_clock_slot;
    const cards = [];
    for (let slot = 1; slot <= state.leagueSize; slot++) {
      const roster = rosterForTeam(slot);
      const isUser = slot === state.userSlot;
      const isClock = slot === onClock;
      const rows = roster
        .map(
          (p) =>
            `<li><span class="ppos">${escapeHtml(p.position)}</span>${escapeHtml(p.name)}</li>`
        )
        .join("");
      cards.push(`<article class="draft-roster-card${isUser ? " is-user" : ""}${
        isClock ? " is-clock" : ""
      }">
        <h4><span>${isUser ? "You" : `Team ${slot}`}</span><span>${roster.length}</span></h4>
        <ul>${rows || "<li>—</li>"}</ul>
      </article>`);
    }
    els.rosterGrid.innerHTML = cards.join("");
  }

  async function refreshBoard() {
    await fetchRecommend();
    renderHero();
    renderPlayers();
    renderRosters();
    save();
  }

  function draftPlayer(playerId) {
    const overall = nextOverall();
    if (overall > totalPicks()) return;
    if (draftedIds().has(playerId)) return;
    const teamSlot = teamSlotForOverall(overall, state.leagueSize);
    state.picks.push({ overall, team_slot: teamSlot, player_id: playerId });
    refreshBoard().catch(showError);
  }

  function undo() {
    if (!state.picks.length) return;
    const maxO = Math.max(...state.picks.map((p) => p.overall));
    for (let i = state.picks.length - 1; i >= 0; i--) {
      if (state.picks[i].overall === maxO) {
        state.picks.splice(i, 1);
        break;
      }
    }
    refreshBoard().catch(showError);
  }

  function resetDraft() {
    if (!confirm("Reset the entire draft? This clears all picks.")) return;
    state.picks = [];
    refreshBoard().catch(showError);
  }

  function showError(err) {
    console.error(err);
    els.recName.textContent = "Something went wrong";
    els.recMeta.textContent = String(err.message || err);
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
    if (state.userSlot > state.leagueSize) state.userSlot = state.leagueSize;
    if (!fromSaved) state.picks = [];
    showSetup(false);
    await fetchPlayers();
    await refreshBoard();
  }

  function bind() {
    els.setupPanel = $("setup-panel");
    els.roomPanel = $("room-panel");
    els.leagueSize = $("league-size");
    els.userSlot = $("user-slot");
    els.scoring = $("scoring");
    els.setupForm = $("setup-form");
    els.clockLabel = $("clock-label");
    els.clockDetail = $("clock-detail");
    els.recEyebrow = $("rec-eyebrow");
    els.recName = $("rec-name");
    els.recMeta = $("rec-meta");
    els.recChips = $("rec-chips");
    els.recAlts = $("rec-alts");
    els.btnDraftRec = $("btn-draft-rec");
    els.btnUndo = $("btn-undo");
    els.btnReset = $("btn-reset");
    els.btnBack = $("btn-back-setup");
    els.playerSearch = $("player-search");
    els.posFilters = $("pos-filters");
    els.playerList = $("player-list");
    els.rosterGrid = $("roster-grid");
    els.needsLine = $("needs-line");

    fillSlotOptions();
    els.leagueSize.addEventListener("change", fillSlotOptions);

    els.setupForm.addEventListener("submit", (e) => {
      e.preventDefault();
      enterRoom(false).catch(showError);
    });

    els.btnDraftRec.addEventListener("click", () => {
      const id = state.rec?.primary?.player_id;
      if (id) draftPlayer(id);
    });
    els.btnUndo.addEventListener("click", undo);
    els.btnReset.addEventListener("click", resetDraft);
    els.btnBack.addEventListener("click", () => {
      showSetup(true);
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

    els.playerList.addEventListener("click", (e) => {
      const row = e.target.closest("[data-id]");
      if (!row) return;
      draftPlayer(row.getAttribute("data-id"));
    });

    els.recAlts.addEventListener("click", (e) => {
      const row = e.target.closest("[data-id]");
      if (!row) return;
      draftPlayer(row.getAttribute("data-id"));
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
