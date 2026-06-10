// 6block PWA 서비스 워커 - HTML은 항상 최신, 정적 자원은 네트워크 우선 + 오프라인 캐시 폴백
const CACHE_NAME = '6block-v11';
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
    const req = event.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);

    // HTML 페이지(내비게이션): 항상 네트워크 최신본을 받고(HTTP 캐시 우회),
    // 오프라인일 때만 마지막으로 받았던 페이지로 폴백한다.
    if (req.mode === 'navigate') {
        event.respondWith(
            fetch(req, { cache: 'no-store' }).then((res) => {
                const copy = res.clone();
                caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
                return res;
            }).catch(() =>
                caches.match(req, { ignoreSearch: true })
                    .then((r) => r || caches.match('/today'))
            )
        );
        return;
    }

    // 정적 자원: 네트워크 우선 + 캐시 폴백(쿼리 무시 매칭)
    if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
        event.respondWith(
            fetch(req).then((res) => {
                const copy = res.clone();
                caches.open(CACHE_NAME).then((c) => c.put(req, copy)).catch(() => {});
                return res;
            }).catch(() => caches.match(req, { ignoreSearch: true }))
        );
    }
});
