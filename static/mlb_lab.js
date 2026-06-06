let activeController = null;
let selectedRunId = null;
let selectedTrack = "moneyline";
let labMeta = null;

const GOAL_DEFAULTS = {
  moneyline: { metric: "log_loss_model", value: 0.68 },
  totals: { metric: "totals_log_loss_model", value: 0.72 },
};

const GOAL_LABELS = {
  log_loss_model: "log_loss_model (lower is better)",
  winner_accuracy_pct: "winner_accuracy_pct (higher is better)",
  model_beats_market: "model_beats_market (confirm only)",
  totals_log_loss_model: "totals_log_loss_model (lower is better)",
  ou_pick_accuracy_pct: "ou_pick_accuracy_pct (higher is better)",
  total_runs_mae: "total_runs_mae (lower is better)",
  totals_beats_market: "totals_beats_market (confirm only)",
};

function formatApiDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
        const msg = item.msg || JSON.stringify(item);
        return loc ? `${loc}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    return detail.message || JSON.stringify(detail);
  }
  return String(detail);
}

const els = {
  splitBanner: document.getElementById("split-banner"),
  trackSelect: document.getElementById("track-select"),
  experimentId: document.getElementById("experiment-id"),
  featureSet: document.getElementById("feature-set"),
  goalMetric: document.getElementById("goal-metric"),
  goalValue: document.getElementById("goal-value"),
  runBtn: document.getElementById("run-btn"),
  stopBtn: document.getElementById("stop-btn"),
  confirmBtn: document.getElementById("confirm-btn"),
  promoteBtn: document.getElementById("promote-btn"),
  runLoading: document.getElementById("run-loading"),
  runMessage: document.getElementById("run-message"),
  error: document.getElementById("error"),
  preflight: document.getElementById("preflight-warnings"),
  resultSection: document.getElementById("result-section"),
  runMeta: document.getElementById("run-meta"),
  validationMetrics: document.getElementById("validation-metrics"),
  chartTitle: document.getElementById("chart-title"),
  learningChart: document.getElementById("learning-chart"),
  confirmSection: document.getElementById("confirm-section"),
  gateMeta: document.getElementById("gate-meta"),
  confirmMetrics: document.getElementById("confirm-metrics"),
  runsBody: document.querySelector("#runs-table tbody"),
};

function metricsBlock(title, block) {
  const marketLl = block.log_loss_market ?? "—";
  const beats =
    block.model_beats_market ?? block.totals_beats_market;
  const beatsLabel =
    beats == null ? "—" : beats ? "yes" : "no";
  return `
    <div class="accuracy-block">
      <h3>${title}</h3>
      <dl>
        <dt>Games</dt><dd>${block.games_with_odds ?? block.games_with_ou_line ?? 0}</dd>
        <dt>Winner / O-U accuracy</dt><dd>${block.winner_accuracy_pct ?? block.ou_pick_accuracy_pct ?? 0}%</dd>
        <dt>+EV picks</dt><dd>${block.plus_ev_picks ?? block.plus_ev_ou_picks ?? 0}</dd>
        <dt>+EV accuracy</dt><dd>${block.plus_ev_accuracy_pct ?? block.plus_ev_ou_accuracy_pct ?? 0}%</dd>
        <dt>Log loss (model)</dt><dd>${block.log_loss_model ?? "—"}</dd>
        <dt>Log loss (market)</dt><dd>${marketLl}</dd>
        <dt>Beats market</dt><dd>${beatsLabel}</dd>
        <dt>Runs MAE</dt><dd>${block.total_runs_mae ?? "—"}</dd>
        <dt>Runs bias</dt><dd>${block.total_runs_bias ?? "—"}</dd>
      </dl>
    </div>`;
}

function renderSplits(splits) {
  els.splitBanner.innerHTML = `
    <strong>Data boundaries:</strong>
    Train ${splits.train} · Validation ${splits.validation} · Locked test ${splits.locked_test}
  `;
}

function renderTrackOptions() {
  if (!labMeta) return;
  const track = els.trackSelect.value;
  selectedTrack = track;
  const cfg = labMeta[track];
  if (!cfg || !cfg.feature_sets?.length) {
    els.error.textContent =
      "Lab config missing for this track — restart the server and hard-refresh (Ctrl+F5).";
    return;
  }
  els.featureSet.innerHTML = cfg.feature_sets
    .map((f) => `<option value="${f}">${f}</option>`)
    .join("");
  if (track === "moneyline" && cfg.feature_sets.includes("wave1_pruned")) {
    els.featureSet.value = "wave1_pruned";
  }
  if (track === "totals" && cfg.feature_sets.includes("totals_full")) {
    els.featureSet.value = "totals_full";
  }
  els.goalMetric.innerHTML = cfg.goal_metrics
    .map(
      (m) =>
        `<option value="${m}">${GOAL_LABELS[m] || m}</option>`
    )
    .join("");
  const defaults = GOAL_DEFAULTS[track];
  els.goalMetric.value = defaults.metric;
  els.goalValue.value = defaults.value;
}

function renderLearningChart(curve, track) {
  if (!curve || !curve.length) {
    els.learningChart.innerHTML = '<p class="empty">No monthly points.</p>';
    return;
  }
  const metricKey =
    track === "totals" ? "log_loss_model" : "log_loss_model";
  const values = curve.map((p) => {
    const v = p[metricKey];
    return v != null ? v : p.total_runs_mae ?? 0;
  });
  const maxVal = Math.max(...values.filter((v) => v != null));
  const isTotals = track === "totals";
  els.chartTitle.textContent = isTotals
    ? "Learning curve (monthly O/U log loss or MAE)"
    : "Learning curve (monthly log loss)";

  els.learningChart.innerHTML = curve
    .map((p, i) => {
      const v = values[i];
      const h = maxVal > 0 ? Math.round((v / maxVal) * 100) : 4;
      const label = p.log_loss_model != null ? p.log_loss_model : `MAE ${p.total_runs_mae}`;
      return `
        <div class="lab-bar-wrap" title="${p.month}: ${label}">
          <div class="lab-bar" style="height:${Math.max(h, 4)}%"></div>
          <span class="lab-bar-label">${p.month.slice(5)}</span>
        </div>`;
    })
    .join("");
}

function renderRunResult(run) {
  selectedRunId = run.id;
  selectedTrack = run.track || "moneyline";
  els.trackSelect.value = selectedTrack;
  const canConfirm =
    (run.goal_met || run.goal_within_tolerance) && !run.test_confirm;
  els.confirmBtn.disabled = !canConfirm;
  const gate = run.test_confirm?.production_gate || {};
  const canPromote =
    run.test_confirm &&
    gate.active_gate_passed &&
    !run.test_confirm.promoted;
  els.promoteBtn.disabled = !canPromote;

  const goalStatus = run.goal_met
    ? "met"
    : run.goal_within_tolerance
      ? "within 5%"
      : "not met";
  let meta = `[${run.track}] ${run.experiment_id} · ${run.feature_set} (${run.n_features} features) · goal ${run.goal_metric} ${run.goal_value} → ${goalStatus}`;
  if (run.metric_actual_value != null) {
    meta += ` · actual ${run.metric_actual_value}`;
  }
  if (run.goal_gap_pct != null) {
    meta += ` · gap ${(run.goal_gap_pct * 100).toFixed(1)}%`;
  }
  if (run.goal_note) meta += ` · ${run.goal_note}`;
  if (run.campaign) {
    meta += ` · ${run.campaign.attempts_count} attempt(s), stopped: ${run.campaign.stopped_reason}`;
    if (run.campaign.feature_sets_tried?.length) {
      meta += ` · tried: ${run.campaign.feature_sets_tried.join(", ")}`;
    }
  }
  els.runMeta.textContent = meta;

  const val = run.validation_summary || {};
  els.validationMetrics.innerHTML =
    metricsBlock("Moneyline", val.moneyline || {}) +
    metricsBlock("Totals", val.totals || {});

  renderLearningChart(run.learning_curve, run.track);
  els.resultSection.classList.remove("hidden");

  if (run.preflight) {
    const pf = run.preflight;
    els.preflight.innerHTML = pf.passed
      ? `<div class="warning-item">Preflight passed (${pf.track}: leakage + shuffle-label sanity).</div>`
      : `<div class="warning-item">Preflight failed: ${JSON.stringify(pf)}</div>`;
  }

  if (run.test_confirm) {
    renderConfirm(run.test_confirm);
  } else {
    els.confirmSection.classList.add("hidden");
  }
}

function renderConfirm(confirm) {
  const gate = confirm.production_gate || {};
  const track = confirm.track || gate.track || "moneyline";
  const active = gate.active_gate_passed;
  let gateText = `[${track}] ML gate=${gate.production_gate_passed} · Totals gate=${gate.totals_gate_passed} · Active=${active}`;
  if (confirm.promoted) {
    const manifest = confirm.active_manifest || {};
    gateText += ` · Promoted ${manifest.run_id || ""} (${manifest.model_version || ""})`;
  } else if (confirm.promotion_note) {
    gateText += ` · ${confirm.promotion_note}`;
  }
  els.gateMeta.textContent = gateText;
  els.confirmMetrics.innerHTML =
    metricsBlock("Moneyline (2025)", confirm.moneyline || {}) +
    metricsBlock("Totals (2025)", confirm.totals || {});
  els.confirmSection.classList.remove("hidden");
}

function renderRunsTable(runs) {
  if (!runs.length) {
    els.runsBody.innerHTML =
      '<tr><td colspan="11" class="empty">No runs yet.</td></tr>';
    return;
  }
  els.runsBody.innerHTML = runs
    .map(
      (r) => `
    <tr data-id="${r.id}" class="run-row">
      <td>${(r.created_at || "").slice(0, 19)}</td>
      <td>${r.track ?? "moneyline"}</td>
      <td>${r.experiment_id}</td>
      <td>${r.feature_set}</td>
      <td>${r.goal_metric} ${r.goal_value}</td>
      <td>${r.validation_log_loss_model ?? "—"}</td>
      <td>${r.validation_totals_log_loss ?? "—"}</td>
      <td>${r.goal_within_tolerance ? "yes" : "no"}</td>
      <td>${r.goal_met ? "yes" : "no"}</td>
      <td>${r.confirmed ? "yes" : "no"}</td>
      <td>${r.gate_passed == null ? "—" : r.gate_passed ? "pass" : "fail"}</td>
    </tr>`
    )
    .join("");

  document.querySelectorAll(".run-row").forEach((row) => {
    row.addEventListener("click", async () => {
      const id = row.getAttribute("data-id");
      const res = await fetch(`/api/lab/runs/${id}`);
      if (res.ok) renderRunResult(await res.json());
    });
  });
}

async function loadMeta() {
  try {
    const res = await fetch("/api/lab/meta");
    const data = await res.json();
    if (!res.ok) {
      throw new Error(formatApiDetail(data.detail) || `HTTP ${res.status}`);
    }
    labMeta = data;
    renderSplits(labMeta.splits);
    renderTrackOptions();
  } catch (err) {
    els.error.textContent = `Could not load lab config: ${err.message}`;
  }
}

async function loadRuns() {
  const res = await fetch("/api/lab/runs");
  const data = await res.json();
  renderRunsTable(data.runs || []);
}

async function runExperiment() {
  els.error.textContent = "";
  els.runLoading.classList.remove("hidden");
  const track = els.trackSelect.value;
  els.runMessage.textContent =
    track === "totals"
      ? "Trying totals feature sets until within 5% of goal… May take several minutes."
      : "Trying moneyline feature sets until within 5% of goal… May take several minutes.";
  els.runBtn.disabled = true;
  els.stopBtn.disabled = false;
  activeController = new AbortController();

  const experimentId = els.experimentId.value.trim();
  if (!experimentId) {
    els.error.textContent = "Run failed: Experiment ID is required.";
    return;
  }

  try {
    const res = await fetch("/api/lab/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        experiment_id: experimentId,
        track,
        feature_set: els.featureSet.value,
        goal_metric: els.goalMetric.value,
        goal_value: Number(els.goalValue.value),
        until_within_pct: 0.05,
      }),
      signal: activeController.signal,
    });
    let data;
    try {
      data = await res.json();
    } catch (_parseErr) {
      throw new Error(`HTTP ${res.status} — server returned non-JSON (is the app running?)`);
    }
    if (!res.ok) {
      throw new Error(formatApiDetail(data.detail) || `HTTP ${res.status}`);
    }
    renderRunResult(data);
    await loadRuns();
  } catch (err) {
    if (err.name !== "AbortError") {
      els.error.textContent = `Run failed: ${err.message}`;
    }
  } finally {
    els.runLoading.classList.add("hidden");
    els.runBtn.disabled = false;
    els.stopBtn.disabled = true;
    activeController = null;
  }
}

async function confirmTest(promote = false) {
  if (!selectedRunId) return;
  els.error.textContent = "";
  els.runLoading.classList.remove("hidden");
  els.runMessage.textContent = promote
    ? "Promoting to live production manifest…"
    : "One-shot locked 2025 evaluation…";
  try {
    const res = await fetch("/api/lab/confirm-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: selectedRunId, promote }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(formatApiDetail(data.detail) || `HTTP ${res.status}`);
    }
    renderRunResult(data);
    await loadRuns();
    if (promote && data.test_confirm?.promoted) {
      els.error.textContent = "";
      els.runMessage.textContent = data.test_confirm.promotion_note || "Promoted to live.";
    }
  } catch (err) {
    els.error.textContent = `${promote ? "Promote" : "Confirm"} failed: ${err.message}`;
  } finally {
    els.runLoading.classList.add("hidden");
  }
}

els.trackSelect.addEventListener("change", renderTrackOptions);
els.runBtn.addEventListener("click", runExperiment);
els.stopBtn.addEventListener("click", () => activeController?.abort());
els.confirmBtn.addEventListener("click", () => confirmTest(false));
els.promoteBtn.addEventListener("click", () => confirmTest(true));

loadMeta();
loadRuns();
