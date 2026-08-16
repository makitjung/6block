// 6block PWA 서비스 워커 - 아무것도 캐시하지 않는다. 모든 요청은 네트워크로 그대로 통과한다.
//
// 예전에는 두 가지를 캐시했다가 둘 다 걷어냈다.
//
// 1) 화면(내비게이션). 오프라인일 때 캐시를 대신 내줬는데 그 폴백이 '다른 페이지'를 내주는
//    바람에 앱이 거짓말을 했다. 테일스케일로 붙은 폰은 화면이 잠깐 잠들었다 깨는 사이 첫
//    요청이 실패하는 일이 흔해서, 그때마다 캐시에 있던 /today 가 나와 고결감·설정을 눌러도
//    오늘 화면이 뜨고, /plan?level=year 가 지난번 /plan?level=week 로 맞아 단위 버튼이
//    안 먹는 것처럼 보였다.
//
// 2) 정적 자원(app.js·style.css). 네트워크 우선이라 평소에는 최신이 나갔지만, 실패했을 때의
//    폴백이 caches.match(req, { ignoreSearch: true }) 라 ?v= 를 무시하고 맞췄다. 그래서
//    app.js?v=새것 요청이 실패하면 app.js?v=옛것 캐시가 나왔다. HTML 은 no-cache 라 늘 새것인데
//    JS 만 옛것인 상태는, 화면이 통째로 안 열리는 것보다 알아채기 어렵고 더 나쁘다.
//    (같은 부류의 함정이 1)에서 이미 한 번 사람을 잡았다.)
//
// 캐시를 지워도 잃는 것이 없다.
//  - 오프라인 실행: 화면 요청을 캐시하지 않으므로 어차피 앱이 안 열린다. CSS·JS 만 남아 있어야
//    쓸 데가 없다.
//  - 빠른 로딩: /static 은 ?v= 가 붙으면 서버가 1년 immutable 로 주므로(app/main.py) 브라우저
//    HTTP 캐시가 이미 처리한다. 서비스워커를 깨우는 것보다 그쪽이 빠르다.
//  - 앱으로 설치: 설치 조건은 fetch 핸들러가 '있는' 것이다. 가로채서 응답할 필요는 없다.
self.addEventListener('install', () => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    // 예전 워커가 만들어 둔 캐시를 전부 지운다. 이걸 안 하면 이미 설치된 기기에는
    // 옛 app.js 가 계속 남아 있다가 네트워크가 흔들릴 때 튀어나온다.
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
            .catch(() => {})
            .then(() => self.clients.claim())
    );
});

// 가로채지 않는다. 핸들러 자체는 남겨 둬야 크롬이 '앱으로 설치'를 제안한다.
self.addEventListener('fetch', () => {});
