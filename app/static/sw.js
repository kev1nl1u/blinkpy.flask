// Static assets are served cache-first with no per-file hash, so the version
// suffix is the only way an updated app.js/css reaches an installed client.
const SHELL_CACHE  = 'blink-shell-v3';
const STATIC_CACHE = 'blink-static-v3';

const SHELL_URLS = ['/'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then(cache => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  const KEEP = [SHELL_CACHE, STATIC_CACHE];
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => !KEEP.includes(k)).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET
  if (request.method !== 'GET') return;

  // API calls — always network, never cache
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/videos/')) return;

  // Navigation (HTML pages) — network-first, fall back to cached shell
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok) {
            caches.open(SHELL_CACHE).then(c => c.put(request, response.clone()));
          }
          return response;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  // CDN & static assets — cache-first, populate on miss
  if (url.origin !== location.origin || url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            caches.open(STATIC_CACHE).then(c => c.put(request, response.clone()));
          }
          return response;
        });
      })
    );
  }
});
