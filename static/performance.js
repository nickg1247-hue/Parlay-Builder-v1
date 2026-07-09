async function bootPerformancePage() {
  const mlClvEl = document.getElementById("perf-ml-clv");
  const summaryEl = document.getElementById("perf-summary");
  const bucketsEl = document.getElementById("perf-buckets");
  const clvEl = document.getElementById("perf-clv");
  const picksEl = document.getElementById("perf-picks");
  const daysSelect = document.getElementById("perf-days");
  const strengthSelect = document.getElementById("perf-strength");
  const mlSportSelect = document.getElementById("perf-ml-sport");

  async function load() {
    const days = Number(daysSelect?.value || 30);
    const strength = strengthSelect?.value || "";
    const mlSport = mlSportSelect?.value || "mlb";
    const qs = new URLSearchParams({ limit: "50", days: String(days) });
    if (strength) qs.set("line_strength", strength);

    try {
      const [mlClv, summary, picksData, modelCmp] = await Promise.all([
        fetchJSON(`/api/clv/summary?days=${days}&sport=${encodeURIComponent(mlSport)}`),
        fetchJSON(`/api/performance/summary?days=${days}`),
        fetchJSON(`/api/performance/picks?${qs}`),
        mlSport === "ufc"
          ? fetchJSON("/api/ufc/model-comparison").catch(() => null)
          : Promise.resolve(null),
      ]);
      renderMlClv(mlClvEl, mlClv, days, mlSport, modelCmp);
      renderSummary(summaryEl, summary, days);
      renderBuckets(bucketsEl, summary.prop_tracker);
      renderClv(clvEl, summary.clv);
      renderPicks(picksEl, picksData.picks || []);
    } catch {
      brandedErrorState(summaryEl, {
        title: "Performance data unavailable",
        message: "Could not load performance summaries.",
        onRetry: load,
      });
      if (mlClvEl) mlClvEl.innerHTML = "<p class=\"text-muted\">ML CLV unavailable.</p>";
      if (bucketsEl) bucketsEl.innerHTML = "";
      if (clvEl) clvEl.innerHTML = "";
      if (picksEl) picksEl.innerHTML = "";
    }
  }

  daysSelect?.addEventListener("change", load);
  strengthSelect?.addEventListener("change", load);
  mlSportSelect?.addEventListener("change", load);
  await load();
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

function renderMlClv(el, clv, days, sport = "mlb", modelCmp = null) {
  if (!el) return;
  const sportLabel = sport === "ufc" ? "UFC" : "MLB";
  if (!clv || !clv.picks_logged) {
    el.innerHTML = `<p class="text-muted">No actionable ${sportLabel} ML singles logged in the last ${days} days. Run the ${sportLabel === "UFC" ? "UFC board live refresh" : "daily board live refresh"}.</p>`;
    return;
  }
  const mean = clv.mean_clv_implied_prob != null ? `${(clv.mean_clv_implied_prob * 100).toFixed(2)} pts` : "—";
  const pos = clv.pct_positive_clv != null ? fmtPct(clv.pct_positive_clv) : "—";
  const hr = fmtPct(clv.hit_rate);
  const modelNote =
    sport === "ufc" && modelCmp?.active_model_label
      ? `<p class="text-muted" style="font-size:0.82rem;margin-top:0.65rem;">Active model: <strong>${modelCmp.active_model_label}</strong>${
          modelCmp.baseline?.log_loss != null && modelCmp.matchup?.log_loss != null
            ? ` · holdout log-loss baseline ${modelCmp.baseline.log_loss.toFixed(4)} vs matchup ${modelCmp.matchup.log_loss.toFixed(4)}`
            : ""
        }</p>`
      : "";
  el.innerHTML = `
    <div class="perf-stat-grid">
      <div class="perf-stat">
        <span class="perf-stat-value">${clv.picks_logged}</span>
        <span class="perf-stat-label">ML picks logged (${days}d)</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${clv.picks_with_close ?? 0}</span>
        <span class="perf-stat-label">With closing line</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${mean}</span>
        <span class="perf-stat-label">Mean CLV (implied)</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${pos}</span>
        <span class="perf-stat-label">Positive CLV rate</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${hr}</span>
        <span class="perf-stat-label">Win rate (settled)</span>
      </div>
    </div>${modelNote}`;
}

function renderSummary(el, summary, days) {
  if (!el) return;
  const pt = summary.prop_tracker || {};
  const hr = fmtPct(pt.overall_hit_rate);
  const pending = pt.result_status_counts?.pending ?? 0;
  el.innerHTML = `
    <div class="perf-stat-grid">
      <div class="perf-stat">
        <span class="perf-stat-value">${pt.props_logged ?? 0}</span>
        <span class="perf-stat-label">Props logged (${days}d)</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${pt.props_settled ?? 0}</span>
        <span class="perf-stat-label">Settled</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${hr}</span>
        <span class="perf-stat-label">Hit rate</span>
      </div>
      <div class="perf-stat">
        <span class="perf-stat-value">${pending}</span>
        <span class="perf-stat-label">Pending</span>
      </div>
    </div>`;
}

function renderBuckets(el, pt) {
  if (!el) return;
  const buckets = pt?.line_strength || {};
  const rows = ["strong", "moderate", "weak"]
    .map((key) => {
      const b = buckets[key] || {};
      const hr = fmtPct(b.hit_rate);
      return `<tr>
        <td>${key.charAt(0).toUpperCase() + key.slice(1)}</td>
        <td>${b.offered ?? 0}</td>
        <td>${b.settled ?? 0}</td>
        <td>${b.hits ?? 0} / ${b.misses ?? 0}</td>
        <td>${hr}</td>
      </tr>`;
    })
    .join("");
  el.innerHTML = `
    <table class="perf-table">
      <thead><tr><th>Line strength</th><th>Offered</th><th>Settled</th><th>W–L</th><th>Hit rate</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderClv(el, clv) {
  if (!el) return;
  if (!clv || !clv.picks_with_close) {
    el.innerHTML = `<p class="text-muted">Prop CLV builds as picks settle and closing odds are captured.</p>`;
    return;
  }
  const mean = clv.mean_clv_implied_prob != null ? `${(clv.mean_clv_implied_prob * 100).toFixed(2)} pts` : "—";
  const pos = clv.pct_positive_clv != null ? fmtPct(clv.pct_positive_clv) : "—";
  el.innerHTML = `
    <div class="perf-stat-grid perf-stat-grid--compact">
      <div class="perf-stat"><span class="perf-stat-value">${clv.picks_with_close}</span><span class="perf-stat-label">With close</span></div>
      <div class="perf-stat"><span class="perf-stat-value">${mean}</span><span class="perf-stat-label">Mean CLV (implied)</span></div>
      <div class="perf-stat"><span class="perf-stat-value">${pos}</span><span class="perf-stat-label">Positive CLV rate</span></div>
    </div>`;
}

function renderPicks(el, picks) {
  if (!el) return;
  if (!picks.length) {
    el.innerHTML = `
      <div class="empty-state-card">
        ${emptyStateIcon("no-bets")}
        <p>No logged picks match these filters. See <a href="/methodology">methodology</a>.</p>
      </div>`;
    return;
  }

  el.innerHTML = picks
    .map((p) => {
      const side = p.recommended_side === "under" ? "Under" : "Over";
      const result =
        p.result_status === "settled"
          ? p.hit === true
            ? "Hit"
            : p.hit === false
              ? "Miss"
              : "Push"
          : p.result_status || "Pending";
      const resultCls =
        result === "Hit" ? "perf-result-hit" : result === "Miss" ? "perf-result-miss" : "";
      const edge =
        p.recommended_hit_rate != null
          ? `${Math.round(p.recommended_hit_rate * 100)}% L10`
          : "";
      const strength = p.line_strength ? `<span class="hero-chip hero-chip-muted">${p.line_strength}</span>` : "";
      const gameLink = p.game_id
        ? `<a class="btn-ghost btn-ghost-sm" href="/mlb/game/${encodeURIComponent(p.game_id)}">Game</a>`
        : "";
      const why = p.line_insight
        ? `<p class="why-pick-card__insight">${p.line_insight}</p>`
        : "";
      return `
      <article class="perf-pick-card ntg-card">
        <header class="perf-pick-head">
          <div>
            <h3 class="perf-pick-player">${p.player}</h3>
            <p class="perf-pick-meta">${p.market_label || p.market_type} · ${side} ${p.line} · ${edge}</p>
          </div>
          <span class="perf-pick-result ${resultCls}">${result}</span>
        </header>
        ${why}
        <footer class="perf-pick-foot">${strength} ${gameLink}</footer>
      </article>`;
    })
    .join("");
}
