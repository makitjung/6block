// 6block PWA 서비스 워커 - 설치 기준 충족 및 정적 자원 캐시 폴백
const CACHE_NAME = '6block-v1';
const CORE_ASSETS = [
    '/static/style.css',
    '/static/app.js',
    '/static/icon.svg',
    '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((c) => c.addAll(CORE_ASSETS)).catch(() => {})
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (event.request.method !== 'GET') return;
    // 정적 자원만 네트워크-우선 + 캐시 폴백
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
        event.respondWith(
            fetch(event.request).then((res) => {
                const copy = res.clone();
                caches.open(CACHE_NAME).then((c) => c.put(event.request, copy)).catch(() => {});
                return res;
            }).catch(() => caches.match(event.request))
        );
    }
});
