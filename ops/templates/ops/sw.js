// SMAGOK GALLERY service worker. Keeps only public files (fonts, icons, the offline page);
// gallery pages, photos, orders and the API always come from the network and are never stored.
const CACHE = "smagok-{{ version }}";
const OFFLINE_URL = "{{ offline }}";
const PRECACHE = {{ precache|safe }};

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // Leave everything else to the browser: forms, R2 photos, the payment provider.
  if (request.method !== "GET" || url.origin !== location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    // Fonts, icons and the dashboard's files: versioned, public, safe to keep.
    event.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(request).then((hit) => {
          const fresh = fetch(request).then((response) => {
            if (response.ok) cache.put(request, response.clone());
            return response;
          });
          return hit || fresh;
        }),
      ),
    );
    return;
  }

  if (request.mode === "navigate") {
    // Pages are private: always the network, and the offline page only when there's none.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
  }
});
