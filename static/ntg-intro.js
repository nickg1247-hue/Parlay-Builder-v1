/**
 * NTG Sports — Concept 3 animated intro (OSU stadium, field-centered).
 */
(function (global) {
  "use strict";

  const STADIUM_SRC = "/static/assets/osu-stadium-field-centered.png?v=20260630e";
  const DURATION_MS = 3800;
  const HOLD_MS = 280;
  const EXIT_MS = 450;
  const MAX_MS = 12000;

  const STATUS = [
    "Running win-probability models…",
    "Syncing live odds…",
    "Loading today's slate…",
  ];

  function buildElement(reducedMotion) {
    const root = document.createElement("div");
    root.id = "ntg-splash";
    root.className =
      "ntg-splash ntg-splash--analytics" +
      (reducedMotion ? " ntg-splash--reduced" : " ntg-splash--animate");
    root.setAttribute("role", "presentation");
    root.setAttribute("aria-hidden", "true");
    root.innerHTML = `
      <div class="ntg-splash__stadium-bg" style="background-image:url('${STADIUM_SRC}')" aria-hidden="true"></div>
      <div class="ntg-splash__stadium-overlay" aria-hidden="true"></div>
      <div class="ntg-splash__analytics-stage">
        <div class="ntg-splash__analytics-wordmark">
          <p class="ntg-splash__analytics-lockup">
            <span class="ntg-splash__analytics-ntg">NTG</span>
            <span class="ntg-splash__analytics-sports">SPORTS</span>
          </p>
          <span class="ntg-splash__analytics-accent" aria-hidden="true"></span>
        </div>
        <p class="ntg-splash__analytics-status">${STATUS[0]}</p>
        <div class="ntg-splash__analytics-bar" aria-hidden="true">
          <div class="ntg-splash__analytics-bar-fill"></div>
        </div>
      </div>
    `;
    return root;
  }

  function runSplash(root, options) {
    const reduced = options?.reducedMotion ?? false;
    const minMs = reduced ? 750 : (options?.minDurationMs ?? DURATION_MS);
    const getProgress = typeof options?.getProgress === "function" ? options.getProgress : null;
    const getStatus = typeof options?.getStatus === "function" ? options.getStatus : null;
    const waitFor = options?.waitFor;
    const fill = root.querySelector(".ntg-splash__analytics-bar-fill");
    const status = root.querySelector(".ntg-splash__analytics-status");
    let loadSettled = !waitFor;

    if (waitFor) {
      Promise.resolve(waitFor).finally(() => {
        loadSettled = true;
      });
    }

    return new Promise((resolve) => {
      const start = performance.now();
      let raf = 0;

      const tick = (now) => {
        const timeU = Math.min(1, (now - start) / minMs);
        const loadU = getProgress ? Math.min(1, Math.max(0, getProgress())) : 1;
        const u = getProgress ? Math.min(timeU, loadU) : timeU;
        const pct = Math.floor(u * 100);

        if (fill) fill.style.width = pct + "%";

        if (status && !reduced) {
          if (getStatus) {
            status.textContent = getStatus();
          } else {
            status.textContent = STATUS[Math.min(STATUS.length - 1, Math.floor(u * STATUS.length))];
          }
        }

        if (timeU >= 1 && loadU >= 1 && loadSettled) {
          if (fill) fill.style.width = "100%";
          setTimeout(resolve, reduced ? 120 : HOLD_MS);
          return;
        }
        raf = requestAnimationFrame(tick);
      };

      raf = requestAnimationFrame(tick);
    });
  }

  global.NTGIntro = {
    STADIUM_SRC,
    DURATION_MS,
    EXIT_MS,
    MAX_MS,
    buildElement,
    runSplash,
  };
})(window);
