self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (new URL(event.request.url).pathname.startsWith('/api/')) return;
  event.respondWith(caches.open('notes-digitizer-legacy-regression').then(async cache =>
    await cache.match(event.request) || fetch(event.request)));
});
