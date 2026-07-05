/** Read server-injected page payload — no dependencies, load before app.js. */
(function () {
  "use strict";

  function readPageData() {
    const el = document.getElementById("ntg-page-data");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || "");
    } catch (err) {
      console.error("ntg-page-data JSON parse failed", err);
      return null;
    }
  }

  window.getPageData = readPageData;
  window.readNTGPageData = readPageData;
})();
