const CACHE = 'whatson-v2';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))
    .then(() => self.skipWaiting()).catch(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE)
      .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') { return; }
  const url = new URL(req.url);
  if (url.origin !== location.origin) { return; }   // tiles and fonts: as-is

  // The page itself must be revalidated, not taken from the browser's own
  // HTTP cache. GitHub Pages serves index.html with ten minutes of freshness,
  // so without this the installed app can open on yesterday's board while
  // today's is already published, and say nothing about it.
  const isPage = req.mode === 'navigate' ||
    url.pathname.endsWith('/') || url.pathname.endsWith('.html');
  const live = isPage ? fetch(url.href, {cache: 'no-cache'}) : fetch(req);

  e.respondWith(
    live.then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(req).then(cached => cached ||
      caches.match('./index.html')))
  );
});
