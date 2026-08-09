// 6block PWA 서비스 워커 - HTML은 항상 최신, 정적 자원은 네트워크 우선 + 오프라인 캐시 폴백
// 캐시 이름은 등록 주소의 ?v=(app.js·style.css 수정시각)에서 자동으로 정해진다.
// 파일이 바뀌면 새 캐시가 생기고 activate에서 옛 캐시를 지우므로 손으로 버전을 올릴 일이 없다.
const CACHE_NAME = '6block-' + (new URL(self.location).searchParams.get('v') || 'dev');
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

    // HTML 페이지(내비게이션)는 건드리지 않는다. 브라우저가 알아서 서버에 물어보고,
    // 못 가져오면 제 오류 화면을 낸다.
    //
    // 예전에는 여기서 가로채 오프라인일 때 캐시를 대신 내줬는데, 그 폴백이 '다른 페이지'를
    // 내주는 바람에 앱이 거짓말을 했다. 테일스케일로 붙은 폰은 화면이 잠깐 잠들었다 깨는
    // 사이 첫 요청이 실패하는 일이 흔해서, 그때마다
    //   - 캐시에 있던 /today 가 나와 고결감·설정을 눌러도 오늘 화면이 뜨고
    //   - /plan?level=year 가 지난번 /plan?level=week 로 맞아 단위 버튼이 안 먹는 것처럼 보였다.
    // 주소만 바뀌고 내용이 딴 것인 화면은 오프라인 안내보다 훨씬 나쁘다. 애초에 자료가 전부
    // 서버에 있어 오프라인으로 볼 수 있는 것도 없으므로, 화면 요청은 그냥 통과시킨다.
    // (정적 자원 캐시는 그대로라 앱 설치·빠른 로딩은 유지된다.)

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
