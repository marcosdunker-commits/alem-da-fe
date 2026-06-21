const CACHE = 'alemdafe-v2';
const STATIC = ['/static/logo.png', '/static/logo.jpg'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    if (e.request.url.includes('/static/')) {
        e.respondWith(
            caches.match(e.request).then(r => r || fetch(e.request))
        );
    }
});

self.addEventListener('push', e => {
    const data = e.data ? e.data.json() : {};
    const title = data.title || 'Além da Fé';
    const options = {
        body: data.body || 'Você tem uma nova mensagem de fé.',
        icon: '/static/logo.png',
        badge: '/static/logo.png',
        vibrate: [200, 100, 200],
        data: { url: data.url || '/home' }
    };
    e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', e => {
    e.notification.close();
    e.waitUntil(clients.openWindow(e.notification.data.url || '/home'));
});
