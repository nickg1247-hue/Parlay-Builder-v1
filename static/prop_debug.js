(function () {
  const dateEl = document.getElementById("debug-date");
  const gameEl = document.getElementById("debug-game-id");
  const bookEl = document.getElementById("debug-book");
  const metaEl = document.getElementById("debug-meta");
  const bodyEl = document.getElementById("debug-body");
  const loadBtn = document.getElementById("debug-load");

  if (dateEl) dateEl.value = new Date().toISOString().slice(0, 10);

  function pct(v) {
    if (v == null) return "—";
    return `${Math.round(Number(v) * 100)}%`;
  }

  function fmt(v) {
    return v == null || v === "" ? "—" : String(v);
  }

  async function loadDebug() {
    const params = new URLSearchParams();
    if (dateEl?.value) params.set("date", dateEl.value);
    if (gameEl?.value) params.set("game_id", gameEl.value.trim());
    if (bookEl?.value) params.set("bookmaker", bookEl.value);
    metaEl.textContent = "Loading…";
    bodyEl.innerHTML = "";
    const res = await fetch(`/api/props/debug?${params}`);
    const data = await res.json();
    metaEl.textContent = `${data.total} props · ${data.total_actionable} actionable · ${data.total_elite} elite${data.elite_message ? ` · ${data.elite_message}` : ""}`;
    bodyEl.innerHTML = (data.props || [])
      .map((p) => {
        const tier = p.confidence_tier || "rejected";
        const reason = p.actionable
          ? p.best_reason || "Recommended"
          : (p.rejection_reasons || []).join("; ") || "Rejected";
        return `<tr>
          <td>${fmt(p.player)}</td>
          <td>${fmt(p.market_type)}</td>
          <td>${fmt(p.line)}</td>
          <td>${fmt(p.recommended_side)}</td>
          <td>${fmt(p.prop_score)}</td>
          <td class="tier-${tier}">${tier}</td>
          <td>${fmt(p.model_projection)}</td>
          <td>${pct(p.recommended_side === "over" ? p.model_probability_over : p.model_probability_under)}</td>
          <td>${pct(p.recommended_side === "over" ? p.market_probability_over : p.market_probability_under)}</td>
          <td>${p.edge_pct != null ? `${p.edge_pct}%` : "—"}</td>
          <td>${fmt(p.recent_form_grade)}</td>
          <td>${fmt(p.matchup_grade)}</td>
          <td>${fmt(p.line_value_grade)}</td>
          <td>${fmt(p.risk_flag)}</td>
          <td>${reason}</td>
        </tr>`;
      })
      .join("");
  }

  loadBtn?.addEventListener("click", loadDebug);
})();
