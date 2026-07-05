/** NTG Sports service worker — cache shell assets; always fetch live API data. */

const CACHE_VERSION = "ntg-pwa-20260725b";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const STATIC_CACHE = `${CACHE_VERSION}-static`;

const PRECACHE_URLS = [
  "/offline",
  "/static/style.css",
  "/static/app.css",
  "/static/brand.css",
  "/static/design.css",
  "/static/home-v2.css",
  "/static/assets/ntg-logo.png",
  "/static/assets/ntg-logo-mark.png",
];

function isApiRequest(url) {
  return url.pathname.startsWith("/api/");
}

function isStaticAsset(url) {
  return url.pathname.startsWith("/static/");
}

function isLiveAsset(url) {
  const path = url.pathname;
  return (
    path.endsWith(".js") ||
    path.endsWith(".json") ||
    path.endsWith(".html")
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("ntg-pwa-") && key !== SHELL_CACHE && key !== STATIC_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live data: never cache — odds, picks, scores, props all use /api/*
  if (isApiRequest(url)) {
    event.respondWith(fetch(request));
    return;
  }

  // HTML pages: network first (fresh shell); cache only for offline fallback
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() =>
          caches.match(request).then((cached) => cached || caches.match("/offline"))
        )
    );
    return;
  }

  if (isStaticAsset(url)) {
    // JS/JSON: network first so deploys + in-app polling logic stay current
    if (isLiveAsset(url)) {
      event.respondWith(
        fetch(request)
          .then((response) => {
            if (response.ok) {
              caches.open(STATIC_CACHE).then((cache) => cache.put(request, response.clone()));
            }
            return response;
          })
          .catch(() => caches.match(request))
      );
      return;
    }

    // CSS/images: cache first, refresh in background when online
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(request);
        const network = fetch(request)
          .then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          })
          .catch(() => null);
        if (cached) {
          network.catch(() => {});
          return cached;
        }
        return network;
      })
    );
  }
});
