/** Global NBA factor weight editor — linked from /nba/board only. */

const els = {
  list: document.getElementById("factor-list"),
  total: document.getElementById("factor-total"),
  status: document.getElementById("status"),
  error: document.getElementById("error"),
  save: document.getElementById("save-btn"),
  reset: document.getElementById("reset-btn"),
};

let factors = [];

function setStatus(msg, isError = false) {
  if (els.status) {
    els.status.textContent = msg || "";
    els.status.classList.toggle("error-text", isError);
  }
}

function sumPct() {
  return factors.reduce((s, f) => s + f.weight_pct, 0);
}

function renderList() {
  if (!els.list) return;
  els.list.innerHTML = factors
    .map(
      (f, idx) => `
    <li class="factor-row">
      <span class="factor-rank">${idx + 1}</span>
      <span class="factor-label">${f.label}</span>
      <div class="factor-controls">
        <button type="button" class="factor-btn" data-key="${f.key}" data-delta="-1" aria-label="Decrease ${f.label}">▼</button>
        <span class="factor-pct" id="pct-${f.key}">${f.weight_pct}%</span>
        <button type="button" class="factor-btn" data-key="${f.key}" data-delta="1" aria-label="Increase ${f.label}">▲</button>
      </div>
    </li>`
    )
    .join("");

  const total = sumPct();
  if (els.total) {
    els.total.textContent = `${total}%`;
    els.total.parentElement.classList.toggle("factor-total-bad", total !== 100);
  }
}

function bump(key, delta) {
  const row = factors.find((f) => f.key === key);
  if (!row) return;

  const min = 1;
  const max = 40;
  row.weight_pct = Math.min(max, Math.max(min, row.weight_pct + delta));

  let excess = sumPct() - 100;
  if (excess === 0) {
    renderList();
    return;
  }

  const step = excess > 0 ? -1 : 1;
  let remaining = Math.abs(excess);
  const others = factors
    .filter((f) => f.key !== key)
    .sort((a, b) => (excess > 0 ? b.weight_pct - a.weight_pct : a.weight_pct - b.weight_pct));

  let guard = 0;
  let i = 0;
  while (remaining > 0 && others.length && guard < 500) {
    const other = others[i % others.length];
    const next = other.weight_pct + step;
    if (next >= min && next <= max) {
      other.weight_pct = next;
      remaining -= 1;
    }
    i += 1;
    guard += 1;
  }

  renderList();
}

function factorsToPayload() {
  const out = {};
  for (const f of factors) {
    out[f.key] = Math.round(f.weight_pct) / 100;
  }
  return out;
}

async function loadWeights() {
  setStatus("Loading…");
  els.error.textContent = "";
  try {
    const res = await fetch("/api/nba/custom-weights");
    if (!res.ok) throw new Error(`Could not load weights (${res.status})`);
    const data = await res.json();
    factors = (data.factors || []).map((f) => ({ ...f }));
    renderList();
    setStatus("");
  } catch (err) {
    els.error.textContent = err.message;
    setStatus("");
  }
}

async function saveWeights() {
  if (sumPct() !== 100) {
    setStatus("Total must be 100% before saving.", true);
    return;
  }
  setStatus("Saving…");
  try {
    const res = await fetch("/api/nba/custom-weights", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ factors: factorsToPayload() }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Save failed (${res.status})`);
    }
    const data = await res.json();
    factors = (data.factors || []).map((f) => ({ ...f }));
    renderList();
    setStatus("Saved — return to the board and refresh to see updated predictions.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function resetWeights() {
  if (!window.confirm("Reset all factor weights to the original defaults?")) return;
  setStatus("Resetting…");
  try {
    const res = await fetch("/api/nba/custom-weights/reset", { method: "POST" });
    if (!res.ok) throw new Error(`Reset failed (${res.status})`);
    const data = await res.json();
    factors = (data.factors || []).map((f) => ({ ...f }));
    renderList();
    setStatus("Defaults restored. Click Save weights or return to the board.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

els.list?.addEventListener("click", (e) => {
  const btn = e.target.closest(".factor-btn");
  if (!btn) return;
  bump(btn.dataset.key, Number(btn.dataset.delta));
});

els.save?.addEventListener("click", saveWeights);
els.reset?.addEventListener("click", resetWeights);

loadWeights();
