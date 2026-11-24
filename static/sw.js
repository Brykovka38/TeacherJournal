// Only a public offline screen is cached. Never cache journal or login responses.
const CACHE = 'teacherjournal-public-v1.3';
const OFFLINE = '/static/offline.html';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add(new Request(OFFLINE, {cache: 'reload'}))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('teacherjournal-public-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || event.request.mode !== 'navigate' || new URL(event.request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(event.request).catch(async () => {
    return (await caches.open(CACHE)).match(OFFLINE).then(response => response || new Response('Нет подключения к интернету', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}}));
  }));
});
