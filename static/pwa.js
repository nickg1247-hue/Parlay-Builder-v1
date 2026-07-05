/** PWA: manifest tags, service worker registration, install prompt. */
(function initNTGPwa() {
  const PWA_V = "20260725b";
  const DISMISS_KEY = "ntg_pwa_install_dismissed";
  const DISMISS_DAYS = 7;

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function isIosSafari() {
    const ua = window.navigator.userAgent || "";
    const ios = /iPad|iPhone|iPod/.test(ua);
    const webkit = /WebKit/.test(ua);
    const notChrome = !/CriOS|FxiOS|EdgiOS/.test(ua);
    return ios && webkit && notChrome;
  }

  function ensureHeadTags() {
    if (!document.querySelector('link[rel="manifest"]')) {
      const manifest = document.createElement("link");
      manifest.rel = "manifest";
      manifest.href = `/manifest.webmanifest?v=${PWA_V}`;
      document.head.appendChild(manifest);
    }

    if (!document.querySelector('meta[name="theme-color"]')) {
      const theme = document.createElement("meta");
      theme.name = "theme-color";
      theme.content = "#030712";
      document.head.appendChild(theme);
    }

    if (!document.querySelector('meta[name="mobile-web-app-capable"]')) {
      const capable = document.createElement("meta");
      capable.name = "mobile-web-app-capable";
      capable.content = "yes";
      document.head.appendChild(capable);
    }

    if (!document.querySelector('meta[name="apple-mobile-web-app-capable"]')) {
      const apple = document.createElement("meta");
      apple.name = "apple-mobile-web-app-capable";
      apple.content = "yes";
      document.head.appendChild(apple);
    }

    if (!document.querySelector('meta[name="apple-mobile-web-app-title"]')) {
      const title = document.createElement("meta");
      title.name = "apple-mobile-web-app-title";
      title.content = "NTG Sports";
      document.head.appendChild(title);
    }

    if (!document.querySelector('link[rel="apple-touch-icon"]')) {
      const touch = document.createElement("link");
      touch.rel = "apple-touch-icon";
      touch.href = `/static/assets/ntg-logo.png?v=${PWA_V}`;
      document.head.appendChild(touch);
    }
  }

  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register(`/sw.js?v=${PWA_V}`, { scope: "/" })
        .catch(() => {});
    });
  }

  function dismissRecently() {
    try {
      const raw = localStorage.getItem(DISMISS_KEY);
      if (!raw) return false;
      const ts = Number(raw);
      if (!Number.isFinite(ts)) return false;
      return Date.now() - ts < DISMISS_DAYS * 24 * 60 * 60 * 1000;
    } catch {
      return false;
    }
  }

  function dismissBanner(banner) {
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
    document.body.classList.remove("ntg-pwa-install-visible");
    banner.remove();
  }

  let deferredInstallPrompt = null;

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
    maybeShowInstallBanner();
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    document.getElementById("ntg-pwa-install")?.remove();
  });

  function createInstallBanner({ ios }) {
    if (document.getElementById("ntg-pwa-install")) return;
    if (isStandalone() || dismissRecently()) return;

    const banner = document.createElement("aside");
    banner.id = "ntg-pwa-install";
    banner.className = "ntg-pwa-install";
    banner.setAttribute("role", "dialog");
    banner.setAttribute("aria-label", "Install NTG Sports app");

    const title = ios ? "Add NTG Sports to your home screen" : "Install NTG Sports";
    const body = ios
      ? "Tap Share, then <strong>Add to Home Screen</strong> for quick access to picks and props."
      : "Install the app for a full-screen home screen icon and faster load.";

    banner.innerHTML = `
      <div class="ntg-pwa-install__inner">
        <img class="ntg-pwa-install__icon" src="/static/assets/ntg-logo-mark.png?v=${PWA_V}" alt="" width="40" height="40" />
        <div class="ntg-pwa-install__copy">
          <p class="ntg-pwa-install__title">${title}</p>
          <p class="ntg-pwa-install__body">${body}</p>
        </div>
        <div class="ntg-pwa-install__actions">
          ${
            ios
              ? `<a class="dash-btn dash-btn-primary ntg-pwa-install__cta" href="/install">How to install</a>`
              : `<button type="button" class="dash-btn dash-btn-primary ntg-pwa-install__cta" id="ntg-pwa-install-btn">Install</button>`
          }
          <button type="button" class="ntg-pwa-install__dismiss" id="ntg-pwa-install-dismiss" aria-label="Dismiss">Not now</button>
        </div>
      </div>`;

    document.body.appendChild(banner);
    document.body.classList.add("ntg-pwa-install-visible");
    banner.querySelector("#ntg-pwa-install-dismiss")?.addEventListener("click", () => dismissBanner(banner));

    if (!ios) {
      banner.querySelector("#ntg-pwa-install-btn")?.addEventListener("click", async () => {
        if (!deferredInstallPrompt) return;
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        dismissBanner(banner);
      });
    }
  }

  function maybeShowInstallBanner() {
    if (isStandalone() || dismissRecently()) return;
    const mobile = window.matchMedia("(max-width: 900px)").matches;
    if (!mobile && !deferredInstallPrompt) return;
    createInstallBanner({ ios: isIosSafari() && !deferredInstallPrompt });
  }

  ensureHeadTags();
  registerServiceWorker();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      window.setTimeout(maybeShowInstallBanner, 1200);
    });
  } else {
    window.setTimeout(maybeShowInstallBanner, 1200);
  }

  window.initNTGPwa = { maybeShowInstallBanner, isStandalone };
})();
