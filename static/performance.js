async function bootPerformancePage() {
  const summaryEl = document.getElementById("perf-summary");
  const picksEl = document.getElementById("perf-picks");

  try {
    const [summary, picksData] = await Promise.all([
      fetchJSON("/api/performance/summary"),
      fetchJSON("/api/performance/picks?limit=50"),
    ]);
    const pt = summary.prop_tracker || {};
    const hr = pt.overall_hit_rate != null ? `${Math.round(pt.overall_hit_rate * 100)}%` : "—";
    summaryEl.innerHTML = `
      <p><strong>${pt.props_logged ?? 0}</strong> props logged (30d) ·
      <strong>${pt.props_settled ?? 0}</strong> settled ·
      Hit rate <strong>${hr}</strong></p>`;

    const picks = picksData.picks || [];
    if (!picks.length) {
      picksEl.innerHTML = `
        <div class="empty-state-card">
          ${emptyStateIcon("no-bets")}
          <p>No logged picks yet. See <a href="/methodology">methodology</a>.</p>
        </div>`;
      return;
    }

    picksEl.innerHTML = picks
      .map((p) => {
        const side = p.recommended_side === "under" ? "U" : "O";
        const result =
          p.result_status === "settled"
            ? p.hit === true
              ? "Hit"
              : p.hit === false
                ? "Miss"
                : "Push"
            : p.result_status || "Pending";
        const edge =
          p.recommended_hit_rate != null
            ? `${Math.round(p.recommended_hit_rate * 100)}% L10`
            : "";
        return `
        <div class="best-bet-card ntg-card" style="padding:0.65rem">
          <span class="best-bet-team">${p.player}</span>
          <span class="best-bet-meta">${p.board_date} · ${p.market_label || p.market_type} ${side}${p.line}</span>
          <span class="best-bet-edge pick-edge-chip">${edge}</span>
          <span class="best-bet-meta">${result}</span>
        </div>`;
      })
      .join("");
  } catch {
    brandedErrorState(summaryEl, {
      title: "Performance data unavailable",
      message: "Could not load the prop tracker summary.",
      onRetry: bootPerformancePage,
    });
    picksEl.innerHTML = "";
  }
}
