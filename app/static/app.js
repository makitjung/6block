// 6block 클라이언트 - 누른 슬롯의 종료시각까지 집중하는 포모도로, 카테고리 띠, PWA 등록
(function () {
    'use strict';

    const TICK_MS = 1000;
    const SLOT_MIN = 30;   // 슬롯 길이(분). 집중은 누른 슬롯의 종료시각까지 흐른다.
    const RING_C = 2 * Math.PI * 44;   // 진행 링 둘레(r=44), CSS stroke-dasharray와 일치

    // 서버 동작 설정(window.__settings). localStorage 값이 있으면 우선, 없으면 이 기본값을 따른다.
    function setget(key) {
        try { return (window.__settings || {})[key]; } catch (e) { return undefined; }
    }
    function settingOn(key, def) {
        const v = setget(key);
        return (v === undefined || v === null) ? def : v === '1';
    }

    const state = {
        phase: 'IDLE',      // 'IDLE' | 'FOCUS'
        startedAt: 0,       // epoch ms (집중 시작 시각)
        endsAt: 0,          // epoch ms (집중 종료 = 슬롯 종료시각)
        slotStart: '',      // 'HH:MM'
        auto: localStorage.getItem('pomoAuto') !== null
            ? localStorage.getItem('pomoAuto') === 'true'
            : settingOn('pomo_auto', false),
    };

    // ---- storage ---------------------------------------------------------
    function persist() {
        localStorage.setItem('pomoState', JSON.stringify({
            phase: state.phase, startedAt: state.startedAt,
            endsAt: state.endsAt, slotStart: state.slotStart,
        }));
        localStorage.setItem('pomoAuto', String(state.auto));
    }
    function restore() {
        try {
            const raw = JSON.parse(localStorage.getItem('pomoState') || '{}');
            if (raw.phase === 'FOCUS') {
                state.phase = 'FOCUS';
                state.startedAt = raw.startedAt || 0;
                state.endsAt = raw.endsAt || 0;
                state.slotStart = raw.slotStart || '';
                // 종료시각이 지난 세션은 즉시 정리
                if (!state.endsAt || Date.now() >= state.endsAt) state.phase = 'IDLE';
                else warn5Fired = (state.endsAt - Date.now()) <= 5 * 60 * 1000;
            }
        } catch (e) {}
    }

    // ---- time helpers ----------------------------------------------------
    function currentSlotHHMM(date) {
        const d = date || new Date();
        const m = d.getMinutes();
        const slot = m < 30 ? '00' : '30';
        return `${String(d.getHours()).padStart(2, '0')}:${slot}`;
    }
    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec));
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    }
    function hhmmToMin(s) {
        if (!s) return -1;
        return parseInt(s.slice(0, 2), 10) * 60 + parseInt(s.slice(3, 5), 10);
    }
    // 'HH:MM' 슬롯 시작 → 그 슬롯 종료시각(시작+30분)의 epoch ms (오늘 기준)
    function slotEndEpoch(slotStart) {
        const endMin = hhmmToMin(slotStart) + SLOT_MIN;
        const d = new Date();
        d.setHours(Math.floor(endMin / 60), endMin % 60, 0, 0);
        return d.getTime();
    }

    // ---- screen wake lock (화면 꺼짐 방지) -------------------------------
    let wakeLock = null;
    async function requestWakeLock() {
        if (!('wakeLock' in navigator) || document.hidden) return;
        try { wakeLock = await navigator.wakeLock.request('screen'); }
        catch (e) {}
    }

    // ---- sound + notify --------------------------------------------------
    let audioCtx = null;
    function getAudio() {
        if (audioCtx) return audioCtx;
        try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
        catch (e) { audioCtx = null; }
        return audioCtx;
    }
    function chime(times, freq) {
        const ctx = getAudio(); if (!ctx) return;
        if (ctx.state === 'suspended') ctx.resume();
        const f = freq || 880;
        for (let i = 0; i < times; i++) {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = f;
            const start = ctx.currentTime + i * 0.42;
            const end = start + 0.22;
            gain.gain.setValueAtTime(0.0001, start);
            gain.gain.exponentialRampToValueAtTime(0.32, start + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, end);
            osc.connect(gain).connect(ctx.destination);
            osc.start(start);
            osc.stop(end + 0.05);
        }
    }
    // 종소리 알람(비조화 배음 + 긴 여운, 가볍게 times회)
    function bell(times) {
        const ctx = getAudio(); if (!ctx) return;
        if (ctx.state === 'suspended') ctx.resume();
        const base = 740;
        const partials = [1, 2.0, 2.96, 4.21];   // 종 특유의 비조화 배음
        const weights = [1, 0.5, 0.3, 0.18];
        for (let n = 0; n < (times || 1); n++) {
            const t0 = ctx.currentTime + n * 0.9;
            partials.forEach((p, i) => {
                const osc = ctx.createOscillator();
                const g = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = base * p;
                const peak = 0.22 * weights[i];
                g.gain.setValueAtTime(0.0001, t0);
                g.gain.exponentialRampToValueAtTime(peak, t0 + 0.006);
                g.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.6);
                osc.connect(g).connect(ctx.destination);
                osc.start(t0);
                osc.stop(t0 + 1.7);
            });
        }
    }
    function ensureNotifPermission() {
        if (!('Notification' in window)) return;
        if (Notification.permission === 'default') Notification.requestPermission();
    }
    function notify(title, body) {
        if (!('Notification' in window)) return;
        if (Notification.permission !== 'granted') return;
        try { new Notification(title, { body, icon: '/static/icon.svg', tag: '6block-pomo' }); }
        catch (e) {}
    }
    function toast(msg) {
        const t = document.getElementById('toast');
        if (!t) return;
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 1800);
    }

    // ---- state transitions ----------------------------------------------
    // 누른 슬롯의 종료시각까지 집중. 휴식 단계는 없고, AUTO는 다음 경계에서 다시 시작한다.
    function startFocus(slotTime) {
        const slot = slotTime || currentSlotHHMM();
        const endsAt = slotEndEpoch(slot);
        // 이미 끝난 슬롯이면 시작하지 않는다
        if (endsAt - Date.now() < 1000) { toast('이미 지난 슬롯'); return; }
        state.phase = 'FOCUS';
        state.startedAt = Date.now();
        state.endsAt = endsAt;
        state.slotStart = slot;
        warn5Fired = (endsAt - Date.now()) <= 5 * 60 * 1000;  // 5분 이하 남았으면 사전알림 생략
        persist();
        chime(1, 880);
        const mins = Math.round((endsAt - state.startedAt) / 60000);
        toast(`집중 시작 · ${state.slotStart} 끝까지 ${mins}분`);
        render();
    }
    function transitionToIdle(auto) {
        state.phase = 'IDLE';
        state.endsAt = 0;
        persist();
        bell(2);
        notify('슬롯 완료', auto ? '자동 모드: 다음 슬롯 대기' : '잘했어!');
        toast('슬롯 완료');
        render();
    }
    function skip() {
        if (state.phase === 'FOCUS') transitionToIdle(false);
    }
    function stop() {
        state.phase = 'IDLE';
        state.startedAt = 0;
        state.endsAt = 0;
        persist();
        render();
        toast('포모도로 중지');
    }
    function toggleAuto() {
        state.auto = !state.auto;
        persist();
        if (state.auto) ensureNotifPermission();
        toast(state.auto ? '자동 모드 ON · 정각/30분에 자동 시작' : '자동 모드 OFF');
        render();
    }

    // ---- main tick -------------------------------------------------------
    let lastBoundaryFired = '';
    let lastUserInteract = 0;   // 마지막 사용자 스크롤·터치 시각(자동 추적 억제용)
    let lastNowSlot = '';       // 마지막으로 추적한 현재 30분 슬롯(HH:MM)
    let lastRenderSlot = '';    // 슬롯·블록 강조를 마지막으로 다시 칠한 슬롯(매초 재계산 방지)
    let warn5Fired = false;     // 종료 5분 전 사전 알림을 한 슬롯에 한 번만 울리기 위한 플래그
    function tick() {
        const now = new Date();
        const sec = now.getSeconds();
        const min = now.getMinutes();

        if (state.phase === 'IDLE') {
            // 정각·30분 경계에서 자동 시작
            if (state.auto && sec < 3 && (min === 0 || min === 30)) {
                const key = `${now.getHours()}:${min}`;
                if (lastBoundaryFired !== key) {
                    lastBoundaryFired = key;
                    startFocus(currentSlotHHMM(now));
                }
            }
        } else if (state.phase === 'FOCUS') {
            const remain = state.endsAt - Date.now();
            if (remain <= 0) {
                transitionToIdle(state.auto);
            } else if (!warn5Fired && remain <= 5 * 60 * 1000) {
                // 종료 5분 전 사전 알림(종소리 2회, 설정에서 끌 수 있음)
                warn5Fired = true;
                if (settingOn('pomo_warn5', true)) {
                    bell(2);
                    notify('5분 남음', `슬롯 ${state.slotStart} 곧 종료`);
                    toast('종료 5분 전');
                }
            }
        }
        render(false);
    }

    // ---- render ----------------------------------------------------------
    // force가 false면(매초 tick) 슬롯이 바뀔 때만 강조를 다시 칠한다. 그 외 호출은 항상 갱신.
    function render(force) {
        // top clock
        const tc = document.getElementById('now-clock');
        if (tc) {
            const d = new Date();
            tc.textContent = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
        }

        // pomo dial
        const pomo = document.getElementById('pomo');
        if (pomo) {
            pomo.classList.toggle('active', state.phase !== 'IDLE' || state.auto);
            pomo.classList.toggle('focus', state.phase === 'FOCUS');
            const autoBtn = pomo.querySelector('.pomo-auto');
            if (autoBtn) autoBtn.classList.toggle('on', state.auto);

            const phaseLabel = state.phase === 'FOCUS' ? '집중'
                              : (state.auto ? '자동' : '대기');
            const phaseEl = pomo.querySelector('.pomo-phase');
            if (phaseEl) phaseEl.textContent = phaseLabel;

            const timeEl = pomo.querySelector('.pomo-time');
            const ringEl = pomo.querySelector('.pomo-ring-prog');
            const slotEl = pomo.querySelector('.pomo-slot');
            if (state.phase === 'IDLE') {
                if (timeEl) timeEl.textContent = state.auto ? 'AUTO' : '—';
                if (ringEl) ringEl.style.strokeDashoffset = RING_C;
                if (slotEl) slotEl.textContent = state.auto
                    ? `다음 시작 · ${nextBoundary()}` : '';
            } else {
                const total = (state.endsAt - state.startedAt) / 1000;
                const remain = (state.endsAt - Date.now()) / 1000;
                const frac = total > 0 ? Math.min(1, Math.max(0, remain / total)) : 0;
                if (timeEl) timeEl.textContent = fmt(remain);
                if (ringEl) ringEl.style.strokeDashoffset = RING_C * (1 - frac);
                if (slotEl) slotEl.textContent = `슬롯 ${state.slotStart} 끝까지`;
            }
        }

        // 슬롯·블록 강조는 매초가 아니라 30분 슬롯이 바뀔 때(또는 상태 변화에 의한 명시적
        // render 호출)에만 다시 칠해, 폰에서의 상시 CPU·배터리 소모를 줄인다.
        const cur = currentSlotHHMM();
        if (force !== false || cur !== lastRenderSlot) {
            lastRenderSlot = cur;
            // highlight current-time slot row
            document.querySelectorAll('.slot').forEach((row) => {
                const t = row.dataset.start;
                const isNow = t === cur;
                row.classList.toggle('is-now', isNow);
                row.classList.toggle('is-pomo-focus', isNow && state.phase === 'FOCUS' && state.slotStart === t);
            });

            // 현재 시각 블록 강조 (실제 오늘을 보는 경우에만)
            const dayForm = document.querySelector('.day-form');
            if (dayForm && isDeviceToday()) {
                const d = new Date();
                const m = d.getHours() * 60 + d.getMinutes();
                document.querySelectorAll('.block').forEach((blk) => {
                    const s = hhmmToMin(blk.dataset.start);
                    const e = hhmmToMin(blk.dataset.end);
                    blk.classList.toggle('is-current', m >= s && m < e);
                });
            }

            applyBlockCollapse();
        }
        autoFollowSlot();
    }

    // 접힘 상태면 현재 시각 블록만 보이게(현재 블록이 없으면 전체 표시)
    function applyBlockCollapse() {
        const stack = document.querySelector('.block-stack');
        if (!stack) return;
        const blocks = stack.querySelectorAll('.block');
        if (!stack.classList.contains('collapsed')) {
            blocks.forEach((b) => b.classList.remove('blk-collapsed'));
            return;
        }
        const hasCurrent = !!stack.querySelector('.block.is-current');
        blocks.forEach((b) => {
            b.classList.toggle('blk-collapsed', hasCurrent && !b.classList.contains('is-current'));
        });
    }

    function nextBoundary() {
        const d = new Date();
        d.setSeconds(0, 0);
        if (d.getMinutes() < 30) d.setMinutes(30);
        else { d.setHours(d.getHours() + 1); d.setMinutes(0); }
        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }

    // ---- category color stripe ------------------------------------------
    // 카테고리 색은 테마별 톤 변수(--tone-blue/red/black)로 칠해 다크모드에서도 보이게 한다.
    // 슬롯은 왼쪽 띠, 블록·주간 미니블록은 왼쪽 테두리 색으로 구분을 표시한다.
    function paintCategory(sel) {
        const opt = sel.options[sel.selectedIndex];
        const tone = (opt && opt.dataset) ? opt.dataset.tone : '';
        const accent = tone ? `var(--tone-${tone})` : '';
        sel.style.color = accent;
        sel.classList.toggle('has-cat', !!accent);   // 색이 지정되면 색 칩 테두리
        const slot = sel.closest('.slot');
        if (slot) { slot.style.setProperty('--row-accent', accent || 'transparent'); return; }
        const block = sel.closest('.block, .mini-block');
        if (block) block.style.borderLeftColor = accent || '';
    }

    // ---- offline write queue (오프라인 쓰기 대기열) ----------------------
    // 인터넷이 없을 때 저장·슬롯 체크·수집함 입력을 localStorage에 순서대로 쌓고,
    // 연결되면 들어온 순서대로 자동 전송한다(개인용 1인 기준 마지막 저장 우선).
    const Q_KEY = '6block-queue';
    const FORM_HEADERS = { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' };
    function loadQueue() {
        try { return JSON.parse(localStorage.getItem(Q_KEY) || '[]'); }
        catch (e) { return []; }
    }
    function saveQueue(q) {
        try { localStorage.setItem(Q_KEY, JSON.stringify(q)); } catch (e) {}
    }
    function genId() {
        return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    }
    function enqueue(op) {
        let q = loadQueue();
        // 전체 폼 저장은 최신 1건만 남겨 큰 스냅샷이 쌓이지 않게 한다.
        if (op.kind === 'form') q = q.filter((o) => !(o.kind === 'form' && o.url === op.url));
        // 같은 필드 자동저장(dedupe 키 동일)은 최신 1건만 남긴다.
        if (op.dedupe) q = q.filter((o) => o.dedupe !== op.dedupe);
        q.push(op);
        saveQueue(q);
        updateNetStatus();
    }
    function cancelQueued(opId) {
        if (!opId) return;
        saveQueue(loadQueue().filter((o) => o.id !== opId));
        updateNetStatus();
    }
    // navigator.onLine은 폰 PWA(특히 Tailscale 접속)에서 false로 잘못 나오는 일이 잦아
    // 신뢰하지 않는다. 항상 전송을 시도하고 실제로 실패할 때만 대기열로 보낸다.
    function sendOrQueue(op, onOk, onQueued) {
        fetch(op.url, { method: 'POST', headers: op.headers || {}, body: op.body })
            .then((r) => { if (!r.ok) throw new Error('bad'); if (onOk) onOk(); })
            .catch(() => { enqueue(op); if (onQueued) onQueued(); });
    }
    let flushing = false;
    async function flushQueue() {
        if (flushing) { updateNetStatus(); return; }
        const q = loadQueue();
        if (!q.length) { updateNetStatus(); return; }
        flushing = true;
        let sent = 0;
        while (q.length) {
            const op = q[0];
            try {
                const r = await fetch(op.url, { method: 'POST', headers: op.headers || {}, body: op.body });
                if (!r.ok) throw new Error('bad');
                q.shift(); saveQueue(q); sent += 1;
            } catch (e) { break; }   // 끊기면 남은 건 다음 연결 때 다시
        }
        flushing = false;
        updateNetStatus();
        if (sent) toast('동기화 완료 ' + sent + '건');
    }
    function updateNetStatus() {
        const el = document.getElementById('net-status');
        if (!el) return;
        // navigator.onLine은 신뢰하지 않는다. 전송 못 한 항목이 쌓이면 그 수만 표시한다.
        const n = loadQueue().length;
        if (n) {
            el.hidden = false; el.className = 'net-status pending';
            el.textContent = '대기 ' + n + '건';
        } else {
            el.hidden = true; el.textContent = '';
        }
    }

    // ---- 오프라인·지난 날짜 감지 (테일스케일/와이파이 꺼짐 대응) ---------
    // 서버에 못 닿으면 서비스워커가 마지막에 받은 '오늘' 화면(지난 날짜)을 보여준다.
    // 기기(폰) 로컬 날짜와 화면 날짜가 어긋나면 안내 배너를 띄우고,
    // 연결이 돌아오면(서버 도달) /today로 자동 이동해 오늘·현재 블록으로 포커스한다.
    function localDateStr(d) {
        d = d || new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    function isStaleToday() {
        const f = document.querySelector('.day-form');
        return !!(f && f.dataset.today === '1' && f.dataset.date !== localDateStr());
    }
    // 현재 보고 있는 날짜 화면이 '기기 시계 기준 오늘'인가.
    // 서버가 구운 data-today 대신 기기 날짜와 화면 날짜(data-date)를 비교하므로,
    // 인터넷이 없어 캐시 페이지를 보더라도 그 페이지가 오늘이면 현재 블록·슬롯에 포커싱된다.
    function isDeviceToday() {
        const f = document.querySelector('.day-form');
        return !!(f && f.dataset.date === localDateStr());
    }
    function checkStale() {
        const banner = document.getElementById('stale-banner');
        if (!isStaleToday()) { if (banner) banner.hidden = true; return; }
        if (banner) banner.hidden = false;
        // 서버에 닿으면 오늘 날짜로 새로 렌더해 자동 이동(닿지 않으면 조용히 대기)
        fetch('/api/now', { cache: 'no-store' })
            .then((r) => { if (r.ok) location.replace('/today'); })
            .catch(() => {});
    }

    // ---- form save (저장 버튼 → 백그라운드 저장 + 오프라인 대기열) -------
    function saveDayForm(form) {
        const op = {
            id: genId(), kind: 'form', url: form.getAttribute('action'),
            headers: FORM_HEADERS,
            body: new URLSearchParams(new FormData(form)).toString(),
        };
        fetch(op.url, { method: 'POST', headers: op.headers, body: op.body })
            .then((r) => { if (!r.ok) throw new Error('bad'); location.reload(); })
            .catch(() => {
                enqueue(op);
                toast('저장 대기 ' + loadQueue().length + '건 · 연결되면 자동 전송');
            });
    }
    function bindForm() {
        const dayForm = document.querySelector('form.day-form');
        if (dayForm) {
            dayForm.addEventListener('submit', (e) => { e.preventDefault(); saveDayForm(dayForm); });
        }
        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                const df = document.querySelector('form.day-form');
                if (df) { saveDayForm(df); return; }
                const wf = document.querySelector('form.week-form');
                if (wf) wf.submit();
            }
        });
    }

    // ---- 오늘/주간 폼의 각 필드에 자동저장 연결 --------------------------
    // name 속성 규칙을 파싱: plan_{id}, see_{id}, do_{id}, did_{id}, cat_{id},
    // bcat_{id}, bname_{id}, bloc_{id}, goal{1-3}, dplan{1-3}, memo, vow,
    // theme_{lbl}(주간), weekly_goal/appointments/vow/memo(주간).
    function bindAutosaveAll() {
        const dayForm = document.querySelector('form.day-form');
        const dateStr = dayForm ? dayForm.dataset.date : null;
        const weekForm = document.querySelector('form.week-form');
        const weekStart = weekForm ? (weekForm.getAttribute('action') || '').split('/').pop() : null;

        const each = (sel, fn) => document.querySelectorAll(sel).forEach((el) => { if (el.name) fn(el, el.name); });

        if (dayForm) {
            each('textarea[name], input[name]', (el, name) => {
                let m;
                if ((m = name.match(/^plan_(\d+)$/)))      bindAutoSave(el, 'block', m[1], 'plan_text');
                else if ((m = name.match(/^see_(\d+)$/)))   bindAutoSave(el, 'block', m[1], 'see_text');
                else if ((m = name.match(/^bname_(\d+)$/))) bindAutoSave(el, 'block', m[1], 'bname');
                else if ((m = name.match(/^bloc_(\d+)$/)))  bindAutoSave(el, 'block', m[1], 'bloc');
                else if ((m = name.match(/^do_(\d+)$/)))    bindAutoSave(el, 'slot', m[1], 'do_text');
                else if ((m = name.match(/^did_(\d+)$/)))   bindAutoSave(el, 'slot', m[1], 'did_text');
                else if ((m = name.match(/^goal([123])$/))) {
                    el.dataset.asPrefix = 'goal';
                    el.dataset.asIdx = m[1];
                    bindAutoSave(el, 'meta', dateStr, 'goal' + m[1], { groupPrefix: 'goal' });
                } else if ((m = name.match(/^dplan([123])$/))) {
                    el.dataset.asPrefix = 'dplan';
                    el.dataset.asIdx = m[1];
                    bindAutoSave(el, 'meta', dateStr, 'dplan' + m[1], { groupPrefix: 'dplan' });
                } else if (name === 'memo') bindAutoSave(el, 'meta', dateStr, 'memo');
                else if (name === 'vow')    bindAutoSave(el, 'meta', dateStr, 'vow');
            });
            // 카테고리 셀렉트(change 로 즉시 저장)
            each('select[name]', (el, name) => {
                let m;
                if ((m = name.match(/^bcat_(\d+)$/))) el.addEventListener('change', () => saveField('block', m[1], 'bcat', el.value));
                else if ((m = name.match(/^cat_(\d+)$/))) el.addEventListener('change', () => saveField('slot', m[1], 'cat', el.value));
            });
        }

        if (weekForm && weekStart) {
            const ws = weekStart;
            each('textarea[name], input[name]', (el, name) => {
                let m;
                if (['weekly_goal', 'appointments', 'vow', 'memo'].indexOf(name) >= 0)
                    bindAutoSave(el, 'wmeta', ws, name);
                else if ((m = name.match(/^theme_(.+)$/)))
                    bindAutoSave(el, 'theme', ws, 'theme', { extra: { label: m[1] } });
                else if ((m = name.match(/^bname_(\d+)$/)))
                    bindAutoSave(el, 'block', m[1], 'bname');
            });
            each('select[name]', (el, name) => {
                let m;
                if ((m = name.match(/^bcat_(\d+)$/)))
                    el.addEventListener('change', () => saveField('block', m[1], 'bcat', el.value));
            });
        }
    }

    // ---- theme -----------------------------------------------------------
    function applyTheme(t) {
        document.documentElement.setAttribute('data-theme', t);
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', t === 'dark' ? '#15171c' : '#ffffff');
        try { localStorage.setItem('theme', t); } catch (e) {}
    }
    function toggleTheme() {
        const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        applyTheme(cur === 'dark' ? 'light' : 'dark');
    }

    // ---- inbox (GTD 빠른 수집) -------------------------------------------
    let inboxInflight = false;   // IME 가드를 못 잡은 환경에서도 2회 실행을 막는 중복 가드
    function inboxAdd() {
        const input = document.getElementById('inbox-input');
        if (!input || inboxInflight) return;
        const text = input.value.trim();
        if (!text) return;
        inboxInflight = true;
        const op = {
            id: genId(), kind: 'inbox-add', url: '/inbox/add',
            headers: FORM_HEADERS, body: new URLSearchParams({ text }).toString(),
        };
        // 오프라인이면 임시 항목으로 먼저 보여주고(temp id) 연결 시 자동 전송한다.
        const queueIt = () => {
            enqueue(op);
            addInboxItem('tmp-' + op.id, text, op.id);
            input.value = '';
            bumpInboxCount(1);
            toast('수집함 대기 · 연결되면 전송');
            inboxInflight = false;
        };
        fetch(op.url, { method: 'POST', headers: op.headers, body: op.body })
            .then((r) => r.json())
            .then((data) => {
                if (!data.ok) return;
                addInboxItem(data.id, data.text);
                input.value = '';
                bumpInboxCount(1);
                toast('수집함에 추가');
            })
            .catch(queueIt)
            .finally(() => { inboxInflight = false; });
    }
    function addInboxItem(id, text, opId) {
        const list = document.getElementById('inbox-list');
        if (!list) return;
        const item = document.createElement('div');
        item.className = 'inbox-item';
        item.dataset.id = id;
        if (opId) item.dataset.op = opId;
        const span = document.createElement('span');
        span.className = 'txt';
        span.textContent = text;
        const send = document.createElement('button');
        send.type = 'button';
        send.className = 'inbox-send';
        send.title = '블록 계획으로 보내기';
        send.textContent = '→';
        send.addEventListener('click', () => openInboxBlocks(item));
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'inbox-done';
        btn.title = '완료/정리';
        btn.textContent = '✓';
        btn.addEventListener('click', () => inboxDone(item));
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'inbox-del';
        del.title = '삭제';
        del.textContent = '✕';
        del.addEventListener('click', () => inboxDelete(item));
        const blocks = document.createElement('div');
        blocks.className = 'inbox-blocks';
        blocks.hidden = true;
        item.appendChild(span);
        item.appendChild(send);
        item.appendChild(btn);
        item.appendChild(del);
        item.appendChild(blocks);
        list.insertBefore(item, list.firstChild);
    }

    // 수집함 항목을 코어 블록 PLAN으로 보내기(GTD 정리 단계). 칩으로 블록을 고른다.
    function coreBlocks() {
        return Array.from(document.querySelectorAll('.block.is-core')).map((b) => ({
            id: b.dataset.blockId,
            label: b.querySelector('.block-label')?.textContent.trim() || '',
            name: b.querySelector('.block-name-input')?.value.trim() || '',
        }));
    }
    function openInboxBlocks(item) {
        const box = item.querySelector('.inbox-blocks');
        if (!box) return;
        if (!box.hidden) { box.hidden = true; return; }
        document.querySelectorAll('.inbox-blocks').forEach((b) => { if (b !== box) b.hidden = true; });
        const blocks = coreBlocks();
        if (!blocks.length) { toast('오늘 화면에서만 보낼 수 있습니다'); return; }
        box.textContent = '';
        blocks.forEach((b) => {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'inbox-block-chip';
            chip.textContent = b.name ? `${b.label} · ${b.name}` : b.label;
            chip.addEventListener('click', () => assignInbox(item, b.id));
            box.appendChild(chip);
        });
        box.hidden = false;
    }
    function assignInbox(item, blockId) {
        const id = item.dataset.id;
        if (String(id).indexOf('tmp-') === 0) { toast('먼저 동기화가 필요합니다'); return; }
        const body = new URLSearchParams({ item_id: id, block_id: blockId }).toString();
        fetch('/inbox/assign', { method: 'POST', headers: FORM_HEADERS, body })
            .then((r) => r.json())
            .then((data) => {
                if (!data.ok) { toast('보내기 실패'); return; }
                const ta = document.querySelector('textarea[name="plan_' + data.block_id + '"]');
                if (ta) ta.value = data.plan_text;
                item.remove();
                bumpInboxCount(-1);
                toast('블록 계획으로 보냈습니다');
            })
            .catch(() => toast('연결이 필요합니다'));
    }
    // 아직 서버에 안 올라간 임시 항목(tmp-)은 대기 중인 추가를 취소하고 그냥 지운다.
    function inboxRemove(item, url) {
        if (!item) return;
        const id = item.dataset.id;
        item.remove();
        bumpInboxCount(-1);
        if (String(id).indexOf('tmp-') === 0) { cancelQueued(item.dataset.op); return; }
        sendOrQueue(
            { id: genId(), kind: 'inbox-op', url: url + id, headers: {}, body: '' },
            null,
            () => toast('전송 대기 · 자동 재시도'),
        );
    }
    function inboxDone(item) { inboxRemove(item, '/inbox/done/'); }
    function inboxDelete(item) { inboxRemove(item, '/inbox/delete/'); }
    function bumpInboxCount(delta) {
        const el = document.getElementById('inbox-count');
        if (!el) return;
        el.textContent = Math.max(0, (parseInt(el.textContent, 10) || 0) + delta);
    }

    // ---- 현재/지정 블록으로 스크롤 ---------------------------------------
    function initialScroll() {
        let target = null;
        let isSlot = false;
        const hash = location.hash;
        if (hash && hash.indexOf('#blk-') === 0) {
            target = document.querySelector(hash);
        } else {
            const dayForm = document.querySelector('.day-form');
            if (dayForm && isDeviceToday()) {
                // 현재 30분 슬롯을 우선 포커스, 없으면 현재 코어 블록
                const slot = document.querySelector('.slot.is-now');
                if (slot) { target = slot; isSlot = true; }
                else target = document.querySelector('.block.is-current');
            }
        }
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            lastNowSlot = currentSlotHHMM();
            if (!isSlot) {
                target.classList.add('flash');
                setTimeout(() => target.classList.remove('flash'), 1500);
            }
        }
    }

    // 현재 30분 슬롯이 바뀌면 화면을 부드럽게 따라 이동(사용자 조작 중에는 억제)
    function autoFollowSlot() {
        const dayForm = document.querySelector('.day-form');
        if (!dayForm || !isDeviceToday()) return;
        const cur = currentSlotHHMM();
        if (cur === lastNowSlot) return;
        if (lastNowSlot === '') { lastNowSlot = cur; return; }   // 초기 1회는 initialScroll이 담당
        lastNowSlot = cur;
        if (Date.now() - lastUserInteract < 8000) return;        // 손으로 조작 중이면 방해 안 함
        const slot = document.querySelector('.slot.is-now');
        if (slot) slot.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // 화면 회전·리사이즈 후 현재 슬롯을 다시 중앙에 맞춤(가로 전환 등에서 어긋남 방지)
    function refocusCurrent() {
        const dayForm = document.querySelector('.day-form');
        if (!dayForm || !isDeviceToday()) return;
        const target = document.querySelector('.slot.is-now') || document.querySelector('.block.is-current');
        if (target) target.scrollIntoView({ behavior: 'auto', block: 'center' });
        lastNowSlot = currentSlotHHMM();
    }

    // ---- 실시간 폴링 (캘린더/Things Today 갱신) -------------------------
    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function renderAgenda(data) {
        // 구글 일정과 Things3 할 일을 각각의 칸에 따로 그린다(분리 표시).
        const evBox = document.getElementById('agenda-events');
        const taskBox = document.getElementById('agenda-tasks');
        const events = data.events || [];
        const tasks = data.tasks || [];
        if (evBox) {
            evBox.textContent = '';
            events.forEach((ev) => {
                const row = el('div', 'agenda-row event' + (ev.color ? ' cal-' + ev.color : ''));
                row.appendChild(el('span', 't', ev.all_day ? '종일' : (ev.start || '')));
                row.appendChild(el('span', 'x', ev.title));
                evBox.appendChild(row);
            });
            if (!events.length) evBox.appendChild(el('div', 'ctx-empty agenda-empty', '오늘 일정이 없습니다.'));
        }
        if (taskBox) {
            taskBox.textContent = '';
            tasks.forEach((t) => {
                const row = el('div', 'agenda-row task');
                if (t.time) row.appendChild(el('span', 't', t.time));
                row.appendChild(el('span', 'x', t.title));
                if (t.overdue) row.appendChild(el('span', 'dl', '지남'));
                else if (t.deadline) row.appendChild(el('span', 'dl', '~' + t.deadline));
                taskBox.appendChild(row);
            });
            if (!tasks.length) taskBox.appendChild(el('div', 'ctx-empty agenda-empty', 'Things3 Today가 비어 있습니다.'));
        }
    }
    function renderBlockAgendas(data) {
        // 각 블록 '일정' 호버 팝오버: 그 시간대 캘린더 일정만 갱신
        const blocks = data.blocks || {};
        document.querySelectorAll('.cal-pop[data-order]').forEach((box) => {
            const items = blocks[box.dataset.order] || [];
            box.textContent = '';
            items.forEach((it) => {
                const row = el('div', 'pop-row ' + it.kind + (it.color ? ' cal-' + it.color : ''));
                if (it.time) row.appendChild(el('span', 't', it.time));
                row.appendChild(el('span', 'x', it.title));
                if (it.end) row.appendChild(el('span', 'end', '~' + it.end));
                box.appendChild(row);
            });
            if (!items.length) box.appendChild(el('div', 'pop-empty', '이 시간대 일정 없음'));
            const cnt = box.closest('.hover-wrap')?.querySelector('.hb-count');
            if (cnt) cnt.textContent = items.length;
        });
        // 각 블록 '할 일' 호버 팝오버: Things3 Today 전체(모든 블록 동일)
        const tasks = data.tasks || [];
        document.querySelectorAll('.task-pop').forEach((box) => {
            box.textContent = '';
            tasks.forEach((t) => {
                const row = el('div', 'pop-row task');
                row.appendChild(el('span', 'x', t.title));
                if (t.overdue) row.appendChild(el('span', 'dl', '지남'));
                else if (t.deadline) row.appendChild(el('span', 'dl', '~' + t.deadline));
                box.appendChild(row);
            });
            if (!tasks.length) box.appendChild(el('div', 'pop-empty', 'Things3 Today 비어 있음'));
        });
        document.querySelectorAll('.task-count').forEach((c) => { c.textContent = tasks.length; });
    }
    let polling = false;
    function pollDay() {
        const form = document.querySelector('.day-form');
        if (!form || polling || !form.dataset.date) return;
        if (document.hidden) return;
        polling = true;
        fetch('/api/day/' + form.dataset.date, { cache: 'no-store' })
            .then((r) => r.json())
            .then((data) => {
                renderAgenda(data);
                renderBlockAgendas(data);
                const w = document.querySelector('.cal-warn');
                if (w) w.style.display = data.cal_enabled ? 'none' : '';
            })
            .catch(() => {})
            .finally(() => { polling = false; });
    }

    // ---- DO 실행 체크 (즉시 저장) ---------------------------------------
    function bindSlotChecks() {
        document.querySelectorAll('.slot-check').forEach((cb) => {
            cb.addEventListener('change', () => {
                const done = cb.checked ? '1' : '0';
                cb.closest('.slot')?.classList.toggle('is-done', cb.checked);
                sendOrQueue(
                    { id: genId(), kind: 'slot', url: '/slot/done/' + cb.dataset.slot,
                      headers: FORM_HEADERS, body: 'done=' + done },
                    () => toast(cb.checked ? '완료 체크' : '체크 해제'),
                    () => toast('전송 대기 · 자동 재시도'),
                );
            });
        });
    }

    // ---- 블록 호버 버튼 + 현재/전체 토글 ---------------------------------
    function bindBlockTools() {
        // 호버 버튼: 데스크톱은 CSS :hover, 모바일은 탭으로 팝오버 토글
        // (슬롯 '한 일' 버튼도 같은 방식으로 탭하면 옆에 패널이 열린다)
        document.querySelectorAll('.hover-btn, .slot-did-btn').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const wrap = btn.closest('.hover-wrap');
                const open = wrap.classList.contains('open');
                document.querySelectorAll('.hover-wrap.open').forEach((w) => w.classList.remove('open'));
                if (!open) wrap.classList.add('open');
            });
        });
        document.querySelectorAll('.hover-pop').forEach((p) => {
            p.addEventListener('click', (e) => e.stopPropagation());
        });
        document.addEventListener('click', () => {
            document.querySelectorAll('.hover-wrap.open').forEach((w) => w.classList.remove('open'));
        });

        // 현재 블록만 보기 ↔ 전체 보기 (오늘 화면에서만)
        const stack = document.querySelector('.block-stack');
        const toggle = document.getElementById('blocks-toggle');
        const dayForm = document.querySelector('.day-form');
        if (stack && dayForm && isDeviceToday() && settingOn('collapse_blocks', true)) {
            stack.classList.add('collapsed');  // 기본값(설정): 현재 블록만
        }
        if (stack && toggle) {
            toggle.addEventListener('click', () => {
                const collapsed = stack.classList.toggle('collapsed');
                toggle.textContent = collapsed ? '전체 블록 보기' : '현재 블록만 보기';
                applyBlockCollapse();
                const cur = stack.querySelector('.block.is-current');
                if (collapsed) {
                    if (cur) cur.scrollIntoView({ behavior: 'smooth', block: 'center' });
                } else {
                    setTimeout(initialScroll, 60);
                }
            });
        }
    }

    // ---- 설정 페이지 -----------------------------------------------------
    function postForm(url, data) {
        return fetch(url, {
            method: 'POST', headers: FORM_HEADERS,
            body: new URLSearchParams(data).toString(),
        }).then((r) => r.json()).catch(() => null);
    }
    function moveCat(id, dir) {
        postForm('/settings/category/move', { id: id, dir: dir })
            .then((d) => { if (d && d.ok) location.reload(); });
    }
    function bindSettings() {
        const addBtn = document.getElementById('set-cat-add-btn');
        // 설정·데이터 페이지가 아니면 종료(데이터 탭의 백업·CSV·삭제 버튼도 여기서 바인딩)
        if (!addBtn && !document.getElementById('set-behavior')
            && !document.getElementById('set-backup-btn')) return;

        addBtn?.addEventListener('click', () => {
            const name = (document.getElementById('set-cat-new-name').value || '').trim();
            const tone = document.getElementById('set-cat-new-tone').value;
            if (!name) { toast('이름을 입력하세요'); return; }
            postForm('/settings/category/add', { name: name, tone: tone })
                .then((d) => { if (d && d.ok) location.reload(); else toast('추가 실패'); });
        });
        document.querySelectorAll('.set-cat-name').forEach((inp) => {
            inp.addEventListener('change', () => {
                const v = (inp.value || '').trim();
                if (!v) return;
                postForm('/settings/category/update', { id: inp.dataset.id, name: v })
                    .then(() => toast('이름 저장'));
            });
        });
        document.querySelectorAll('.set-cat-tone').forEach((sel) => {
            sel.addEventListener('change', () => {
                postForm('/settings/category/update', { id: sel.dataset.id, tone: sel.value })
                    .then(() => {
                        const dot = sel.closest('.set-cat-row')?.querySelector('.set-cat-dot');
                        if (dot) dot.style.background = 'var(--tone-' + sel.value + ')';
                        toast('색 저장');
                    });
            });
        });
        document.querySelectorAll('.set-cat-up').forEach((b) =>
            b.addEventListener('click', () => moveCat(b.dataset.id, 'up')));
        document.querySelectorAll('.set-cat-down').forEach((b) =>
            b.addEventListener('click', () => moveCat(b.dataset.id, 'down')));
        document.querySelectorAll('.set-cat-del').forEach((b) =>
            b.addEventListener('click', () => {
                postForm('/settings/category/delete', { id: b.dataset.id })
                    .then((d) => { if (d && d.ok) location.reload(); });
            }));
        document.querySelectorAll('.set-cat-show').forEach((b) =>
            b.addEventListener('click', () => {
                postForm('/settings/category/add', { name: b.dataset.name, tone: b.dataset.tone })
                    .then((d) => { if (d && d.ok) location.reload(); });
            }));

        document.querySelectorAll('#set-behavior select').forEach((sel) => {
            sel.addEventListener('change', () => {
                const o = {}; o[sel.dataset.key] = sel.value;
                postForm('/settings/save', o).then(() => toast('설정 저장'));
            });
        });

        // 요일별 컨셉 자동 저장
        document.querySelectorAll('.set-wd-input').forEach((inp) => {
            inp.addEventListener('change', () => {
                postForm('/settings/weekday', { weekday: inp.dataset.weekday, text: inp.value })
                    .then((d) => { if (d && d.ok) toast('요일 컨셉 저장'); });
            });
        });

        // 구글 일정 쓰기: 캘린더 ID 자동 저장 + 연결 테스트
        const evCal = document.getElementById('set-events-cal');
        const evStatus = document.getElementById('set-events-status');
        const setEvStatus = (on) => {
            if (!evStatus) return;
            evStatus.textContent = on ? '켜짐 · 연결됨' : '꺼짐 · ID 입력 필요';
            evStatus.classList.toggle('ok', !!on);
            evStatus.classList.toggle('bad', !on);
        };
        evCal?.addEventListener('change', () => {
            postForm('/settings/events-calendar', { value: evCal.value.trim() }).then((d) => {
                if (!d) return;
                setEvStatus(d.enabled);
                toast('캘린더 ID 저장');
            });
        });
        document.getElementById('set-events-test')?.addEventListener('click', (e) => {
            const btn = e.currentTarget; btn.disabled = true;
            postForm('/settings/events-calendar/test', {}).then((d) => {
                btn.disabled = false;
                if (d && d.ok) toast(d.warn || '연결 OK · 테스트 일정 생성/삭제 성공');
                else toast((d && d.error) || '연결 실패');
            });
        });

        document.getElementById('set-backup-btn')?.addEventListener('click', (e) => {
            const btn = e.currentTarget; btn.disabled = true;
            postForm('/settings/backup', {}).then((d) => {
                toast(d && d.ok ? '백업 완료' : '백업 실패');
                btn.disabled = false;
            });
        });
        document.getElementById('set-csv-btn')?.addEventListener('click', () => {
            const s = document.getElementById('set-csv-start').value;
            const en = document.getElementById('set-csv-end').value;
            if (!s || !en) { toast('기간을 선택하세요'); return; }
            window.location.href = '/settings/export.csv?start=' + s + '&end=' + en;
        });
        const pc = document.getElementById('set-purge-confirm');
        const pb = document.getElementById('set-purge-btn');
        pc?.addEventListener('change', () => { if (pb) pb.disabled = !pc.checked; });
        pb?.addEventListener('click', () => {
            const s = document.getElementById('set-purge-start').value;
            const en = document.getElementById('set-purge-end').value;
            if (!s || !en) { toast('기간을 선택하세요'); return; }
            if (!window.confirm(s + ' ~ ' + en + ' 기록을 삭제합니다. 되돌릴 수 없습니다.')) return;
            postForm('/settings/purge', { start: s, end: en }).then((d) => {
                if (d && d.ok) { toast('삭제 완료'); location.reload(); }
                else toast('삭제 실패');
            });
        });
    }

    // ---- 설정: 세션(블록) 시간 편집 (8칸 묶음 검증 → 변경 즉시 자동저장) ----
    function bindBlockTimes() {
        const box = document.getElementById('set-blocktimes');
        if (!box) return;
        const msg = document.getElementById('set-bt-msg');
        const collect = () => {
            const data = {};
            box.querySelectorAll('.set-bt-row').forEach((row) => {
                const o = row.dataset.order;
                data['start_' + o] = row.querySelector('.set-bt-start').value;
                data['end_' + o] = row.querySelector('.set-bt-end').value;
            });
            return data;
        };
        const save = () => {
            if (msg) { msg.textContent = ''; msg.classList.remove('bad'); }
            fetch('/settings/blocktimes', {
                method: 'POST', headers: FORM_HEADERS,
                body: new URLSearchParams(collect()).toString(),
            })
                .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
                .then(({ ok, d }) => {
                    if (ok && d.ok) { autosaveToast(); }
                    else if (msg) { msg.textContent = (d && d.error) || '저장 실패'; msg.classList.add('bad'); }
                })
                .catch(() => { if (msg) { msg.textContent = '연결이 필요합니다'; msg.classList.add('bad'); } });
        };
        box.querySelectorAll('.set-bt-start, .set-bt-end').forEach((inp) =>
            inp.addEventListener('change', save));
        document.getElementById('set-bt-reset')?.addEventListener('click', () => {
            if (!window.confirm('블록 시간을 기본값으로 되돌립니다.')) return;
            postForm('/settings/blocktimes/reset', {}).then((d) => { if (d && d.ok) location.reload(); });
        });
    }

    // ---- 장기플랜 (/plan) ------------------------------------------------
    function bindPlan() {
        const grid = document.querySelector('.plan-grid');
        if (!grid) return;

        // 칸 자동저장: blur 시 변경분 전송, 오프라인이면 대기열에 쌓고 자동 재시도
        grid.querySelectorAll('.pg-input').forEach((ta) => {
            ta.addEventListener('change', () => {
                const body = new URLSearchParams({
                    level: ta.dataset.level,
                    period_key: ta.dataset.period,
                    area_id: ta.dataset.area,
                    content: ta.value,
                }).toString();
                sendOrQueue(
                    { id: genId(), kind: 'plan-cell', url: '/plan/cell/save',
                      headers: FORM_HEADERS, body },
                    () => toast('저장'),
                    () => toast('저장 대기 · 자동 재시도'),
                );
            });
        });

        // 화면 밀도(축소/확대): data-zoom 0..3을 localStorage에 보존
        const ZKEY = '6block-plan-zoom';
        let z = parseInt(localStorage.getItem(ZKEY), 10);
        if (isNaN(z)) z = 1;
        const applyZoom = () => grid.setAttribute('data-zoom', String(z));
        applyZoom();
        document.getElementById('pg-zoom-in')?.addEventListener('click', () => {
            z = Math.min(3, z + 1); localStorage.setItem(ZKEY, z); applyZoom();
        });
        document.getElementById('pg-zoom-out')?.addEventListener('click', () => {
            z = Math.max(0, z - 1); localStorage.setItem(ZKEY, z); applyZoom();
        });

        // 현재 기간 열을 고정된 영역 열 바로 오른쪽으로 가로 스크롤(가려지지 않게)
        const nowCol = grid.querySelector('.pg-head.is-now');
        const scroller = grid.closest('.plan-scroll');
        if (nowCol && scroller) {
            const areaW = grid.querySelector('.pg-area')?.getBoundingClientRect().width || 0;
            const delta = nowCol.getBoundingClientRect().left
                        - scroller.getBoundingClientRect().left - areaW - 12;
            scroller.scrollLeft += delta;
        }
    }

    function bindPlanAreas() {
        const addBtn = document.getElementById('pg-area-add');
        if (!addBtn && !document.querySelector('.pg-area-name')) return;
        const addArea = () => {
            const inp = document.getElementById('pg-area-new');
            const name = (inp.value || '').trim();
            if (!name) { toast('이름을 입력하세요'); return; }
            postForm('/plan/area/add', { name: name })
                .then((d) => { if (d && d.ok) location.reload(); else toast('추가 실패'); });
        };
        addBtn?.addEventListener('click', addArea);
        document.getElementById('pg-area-new')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); addArea(); }
        });
        document.querySelectorAll('.pg-area-name').forEach((inp) => {
            inp.addEventListener('change', () => {
                const v = (inp.value || '').trim();
                if (!v) return;
                postForm('/plan/area/update', { id: inp.dataset.id, name: v })
                    .then(() => toast('이름 저장'));
            });
        });
        const move = (id, dir) =>
            postForm('/plan/area/move', { id: id, dir: dir })
                .then((d) => { if (d && d.ok) location.reload(); });
        document.querySelectorAll('.pg-area-up').forEach((b) =>
            b.addEventListener('click', () => move(b.dataset.id, 'up')));
        document.querySelectorAll('.pg-area-down').forEach((b) =>
            b.addEventListener('click', () => move(b.dataset.id, 'down')));
        document.querySelectorAll('.pg-area-del').forEach((b) =>
            b.addEventListener('click', () => {
                postForm('/plan/area/delete', { id: b.dataset.id })
                    .then((d) => { if (d && d.ok) location.reload(); });
            }));
        document.querySelectorAll('.pg-area-show').forEach((b) =>
            b.addEventListener('click', () => {
                postForm('/plan/area/add', { name: b.dataset.name })
                    .then((d) => { if (d && d.ok) location.reload(); });
            }));
    }

    // 모든 텍스트 입력창의 가벼운 목록 편집(애플노트/마크다운 느낌). 외부 라이브러리 없이 동작한다.
    //  - Tab: 목록 줄이면 한 단계 들여써 하위레벨(순서목록은 1.부터) 시작, 아니면 공백 2칸 들여쓰기.
    //  - Shift+Tab: 목록 줄이면 한 단계 내어쓰기(번호 재계산), 아니면 공백 내어쓰기.
    //  - Enter: '1. ' / '- ' / '* ' 로 시작한 줄이면 다음 줄을 같은 들여쓰기로 자동 번호·불릿 잇고,
    //           내용이 빈 항목에서 Enter면 그 표시를 지우고 목록을 끝낸다(애플노트 동작).
    //  한글 IME 조합 Enter(isComposing / 229)는 무시한다.
    function bindListEditor(ta) {
        if (!ta || ta.dataset.listed) return;
        ta.dataset.listed = '1';
        const INDENT = '  ';
        const setCaret = (pos) => { ta.selectionStart = ta.selectionEnd = pos; };
        // 줄을 목록 항목으로 해석(순서 1. / 불릿 - *). 들여쓰기·종류·내용을 나눈다.
        const listMatch = (line) => {
            const mo = line.match(/^(\s*)(\d+)\.(\s+)(.*)$/);
            if (mo) return { indent: mo[1], kind: 'o', rest: mo[4] };
            const mu = line.match(/^(\s*)([-*])(\s+)(.*)$/);
            if (mu) return { indent: mu[1], kind: 'u', bullet: mu[2], rest: mu[4] };
            return null;
        };
        // 주어진 들여쓰기 수준의 순서목록 번호: 같은 들여쓰기의 바로 위 형제 +1, 없으면 1.
        const orderedNumberAt = (value, lineStartPos, indentLen) => {
            const lines = value.slice(0, lineStartPos).split('\n');
            for (let i = lines.length - 1; i >= 0; i--) {
                if (lines[i].trim() === '') continue;
                const m = lines[i].match(/^(\s*)(\d+)\.\s+/);
                const ind = (lines[i].match(/^\s*/) || [''])[0].length;
                if (m && ind === indentLen) return parseInt(m[2], 10) + 1;
                if (ind < indentLen) break;   // 상위(부모) 줄을 만나면 하위목록은 1부터
            }
            return 1;
        };
        ta.addEventListener('keydown', (e) => {
            const s = ta.selectionStart, en = ta.selectionEnd;
            const ls = ta.value.lastIndexOf('\n', s - 1) + 1;   // 현재 줄 시작 위치
            if (e.key === 'Tab') {
                e.preventDefault();
                const le = ta.value.indexOf('\n', s);
                const lineEnd = le === -1 ? ta.value.length : le;
                const line = ta.value.slice(ls, lineEnd);
                const lm = listMatch(line);
                // 목록 줄: Tab은 하위레벨 시작(순서목록 1.부터), Shift+Tab은 한 단계 위로(번호 재계산)
                if (lm && !(e.shiftKey && lm.indent.length === 0)) {
                    const newIndent = e.shiftKey
                        ? lm.indent.slice(0, Math.max(0, lm.indent.length - INDENT.length))
                        : lm.indent + INDENT;
                    const marker = lm.kind === 'o'
                        ? orderedNumberAt(ta.value, ls, newIndent.length) + '. '
                        : lm.bullet + ' ';
                    const newLine = newIndent + marker + lm.rest;
                    const caretInRest = Math.max(0, s - (ls + line.length - lm.rest.length));
                    ta.value = ta.value.slice(0, ls) + newLine + ta.value.slice(lineEnd);
                    setCaret(ls + newLine.length - lm.rest.length + caretInRest);
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                    return;
                }
                // 목록이 아니면 기존 동작: 공백 들여쓰기 / 내어쓰기
                if (e.shiftKey) {
                    const cut = ta.value.slice(ls).match(/^ {1,2}/);
                    if (cut) {
                        const n = cut[0].length;
                        ta.value = ta.value.slice(0, ls) + ta.value.slice(ls + n);
                        setCaret(Math.max(ls, s - n));
                    }
                } else {
                    ta.value = ta.value.slice(0, s) + INDENT + ta.value.slice(en);
                    setCaret(s + INDENT.length);
                }
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                return;
            }
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
                const line = ta.value.slice(ls, s);
                const mo = line.match(/^(\s*)(\d+)\.\s+(.*)$/);   // 순서 목록 1. 2. 3.
                const mu = line.match(/^(\s*)([-*])\s+(.*)$/);    // 불릿 목록 - *
                const m = mo || mu;
                if (!m) return;
                e.preventDefault();
                if (m[3].trim() === '') {                          // 빈 항목 → 목록 종료
                    ta.value = ta.value.slice(0, ls) + ta.value.slice(s);
                    setCaret(ls);
                } else {
                    const marker = mo ? (parseInt(mo[2], 10) + 1) + '. ' : mu[2] + ' ';
                    const ins = '\n' + m[1] + marker;
                    ta.value = ta.value.slice(0, s) + ins + ta.value.slice(en);
                    setCaret(s + ins.length);
                }
                ta.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
    }

    // ---- 자동저장: 한 필드가 바뀌면 (blur 즉시 / input 1.2초 후) 즉시 저장 ----
    // 엔티티(block/slot/meta) + id + field 를 서버 /save/field 로 보낸다.
    // 오프라인이면 대기열로, 돌아오면 자동 재전송(개인용 1인 기준 마지막 저장 우선).
    const AS_TOAST_MS = 900;
    let asToastTimer = null;
    function autosaveToast() {
        const t = document.getElementById('toast');
        if (!t) return;
        t.textContent = '✓ 저장됨';
        t.classList.add('show');
        if (asToastTimer) clearTimeout(asToastTimer);
        asToastTimer = setTimeout(() => t.classList.remove('show'), AS_TOAST_MS);
    }
    // 같은 엔티티+id+field 의 자동저장 요청은 마지막 것만 남긴다(전체 폼 저장과 동일 전략).
    function asOpKey(entity, id, field) { return 'as:' + entity + ':' + id + ':' + field; }
    const asInflight = {};   // key -> 이전 요청이 진행 중인가 (중복 전송 억제)
    function saveField(entity, id, field, value, extra) {
        const key = asOpKey(entity, id, field);
        const bodyObj = { entity: entity, id: String(id), field: field, value: value };
        if (extra) Object.keys(extra).forEach((k) => { bodyObj[k] = extra[k]; });
        const op = {
            id: genId(), kind: 'autosave', url: '/save/field', headers: FORM_HEADERS,
            body: new URLSearchParams(bodyObj).toString(),
            dedupe: key,
        };
        // 진행 중인 동일 필드 요청이 있으면 결과는 무시(마지막 값이 대기열/새 요청으로 이김)
        asInflight[key] = true;
        sendOrQueue(
            op,
            () => { asInflight[key] = false; autosaveToast(); },
            () => toast('저장 대기 · 연결되면 자동 전송'),
        );
    }
    function bindAutoSave(el, entity, id, field, opts) {
        if (!el || el.dataset.autosave) return;
        el.dataset.autosave = '1';
        opts = opts || {};
        let timer = null;
        const flush = () => {
            if (timer) { clearTimeout(timer); timer = null; }
            let value = el.value;
            // 3칸 묶음(goal/dplan)은 그룹의 나머지 값도 같이 보내 서버에서 합치게 한다.
            // 정적 extra(예: 주간 테마의 label)는 항상 같이 보낸다.
            let extra = opts.extra ? Object.assign({}, opts.extra) : null;
            if (opts.groupPrefix) {
                extra = extra || {};
                document.querySelectorAll('[data-as-prefix="' + opts.groupPrefix + '"]').forEach((g) => {
                    extra[g.dataset.asIdx] = g.value;
                });
            }
            saveField(entity, id, field, value, extra);
        };
        el.addEventListener('change', flush);
        el.addEventListener('blur', flush);
        el.addEventListener('input', () => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(flush, 1200);
        });
    }


    // ---- 고결감 공용 태그 헬퍼 -------------------------------------------
    function normalizeTags(val) {
        if (!val) return '';
        return val.split(/[\s,]+/).filter(Boolean)
            .map((t) => (t.startsWith('#') ? t : '#' + t))
            .join(' ');
    }

    function bindTagAutocomplete(input) {
        const tags = (window._rfTags || []);
        if (!tags.length || !input) return;
        let drop = input.parentNode.querySelector('.rf-tag-drop');
        if (!drop) {
            drop = document.createElement('div');
            drop.className = 'rf-tag-drop';
            drop.hidden = true;
            input.parentNode.style.position = 'relative';
            input.parentNode.appendChild(drop);
        }
        const show = (matches) => {
            drop.innerHTML = '';
            if (!matches.length) { drop.hidden = true; return; }
            matches.slice(0, 8).forEach((t) => {
                const btn = document.createElement('button');
                btn.type = 'button'; btn.className = 'rf-tag-opt'; btn.textContent = t;
                btn.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    const v = input.value;
                    const last = v.lastIndexOf('#');
                    input.value = (last >= 0 ? v.slice(0, last) : v) + t + ' ';
                    drop.hidden = true; input.focus();
                });
                drop.appendChild(btn);
            });
            drop.hidden = false;
        };
        input.addEventListener('input', () => {
            const v = input.value;
            const last = v.lastIndexOf('#');
            if (last < 0) { drop.hidden = true; return; }
            const prefix = v.slice(last);
            show(tags.filter((t) => t.toLowerCase().startsWith(prefix.toLowerCase())));
        });
        input.addEventListener('blur', () => setTimeout(() => { drop.hidden = true; }, 150));
        input.addEventListener('keydown', (e) => { if (e.key === 'Escape') drop.hidden = true; });
    }

    // ---- 고결감 (/reflect) -----------------------------------------------
    function bindReflect() {
        const compose = document.querySelector('.reflect-compose');
        const list = document.getElementById('reflect-list');
        const upcoming = document.getElementById('reflect-upcoming');
        if (!compose && !list) return;
        let lastSig = list ? (list.dataset.sig || null) : null;
        const curKind = () => new URLSearchParams(location.search).get('kind') || '';

        // 대상 카드로 스크롤·펼침·강조(상호 이동·미도래 칩 공용)
        function focusCard(id) {
            if (!id || !list) return;
            const card = list.querySelector('.rf-card[data-id="' + id + '"]');
            if (!card) return;
            card.classList.add('expanded');
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
        }

        function deleteItem(id, after) {
            if (!window.confirm('이 기록을 삭제합니다. 캘린더 이벤트도 함께 지웁니다.')) return;
            postForm('/reflect/delete/' + id, {}).then((d) => {
                if (d && d.ok) { if (after) after(); toast('삭제'); refreshReflect(true); }
            });
        }

        // ---- 부분 갱신: 목록·미도래를 서버 진실로 다시 그린다 ----
        function refreshReflect(force) {
            if (!list) return Promise.resolve();
            const url = '/reflect/list?kind=' + encodeURIComponent(curKind()) + (force ? '&force=1' : '');
            return fetch(url).then((r) => r.json()).then((d) => {
                if (!d || !d.ok) return;
                if (!force) {
                    if (d.sig === lastSig) return;                                    // 변화 없음
                    if (list.querySelector('.rf-edit-panel:not([hidden])')) return;   // 편집 중 보호
                    const ae = document.activeElement;
                    if (ae && list.contains(ae)) return;                              // 입력 중 보호
                }
                lastSig = d.sig;
                if (upcoming) upcoming.innerHTML = d.upcoming_html;
                list.innerHTML = d.list_html;
                bindList(); bindUpcoming(); applySearch();
            }).catch(() => {});
        }

        // ---- 유사검색(부분 갱신 뒤에도 다시 적용) ----
        function applySearch() {
            const searchInput = document.getElementById('rf-search-input');
            if (!searchInput || !list) return;
            const items = Array.from(list.querySelectorAll('.rf-card'));
            const noMatch = list.querySelector('.rf-no-match');
            const norm = (s) => (s || '').normalize('NFC').toLowerCase();
            const subseq = (n, h) => {
                let i = 0;
                for (let k = 0; k < h.length && i < n.length; k++) if (h[k] === n[i]) i++;
                return i >= n.length;
            };
            const score = (toks, hay) => {
                let sc = 0;
                for (const t of toks) {
                    if (hay.indexOf(t) >= 0) sc += 2;
                    else if (subseq(t, hay)) sc += 1;
                    else return 0;
                }
                return sc;
            };
            const q = norm(searchInput.value.trim());
            const toks = q ? q.split(/\s+/).filter(Boolean) : [];
            let shown = 0;
            if (!toks.length) {
                items.forEach((el) => { el.hidden = false; list.appendChild(el); });
                shown = items.length;
            } else {
                const scored = items.map((el, idx) => ({
                    el, idx, s: score(toks, el.dataset.search || norm(el.textContent)),
                }));
                scored.forEach((o) => { o.el.hidden = o.s === 0; });
                scored.filter((o) => o.s > 0).sort((a, b) => b.s - a.s || a.idx - b.idx)
                    .forEach((o) => { list.appendChild(o.el); shown += 1; });
            }
            if (noMatch) { noMatch.hidden = !(items.length && shown === 0); list.appendChild(noMatch); }
        }

        // ---- 카드 바인딩(상호이동·삭제·재동기화·인라인편집) ----
        function enterEdit(card) {
            const panel = card.querySelector('.rf-inline-edit');
            if (!panel) return;
            panel.hidden = false;
            const ti = panel.querySelector('.rf-edit-tags');
            if (ti && !ti.dataset.acBound) { bindTagAutocomplete(ti); ti.dataset.acBound = '1'; }
        }

        function bindList() {
            if (!list) return;
            list.querySelectorAll('.rf-jump').forEach((b) =>
                b.addEventListener('click', (e) => { e.stopPropagation(); focusCard(b.dataset.target); }));
            list.querySelectorAll('.rf-del').forEach((b) =>
                b.addEventListener('click', () => deleteItem(b.dataset.id, () => b.closest('.rf-card')?.remove())));
            list.querySelectorAll('.rf-sync.retry').forEach((b) =>
                b.addEventListener('click', () => {
                    postForm('/reflect/sync/' + b.dataset.id, {}).then((d) => {
                        if (d && d.synced) { toast('캘린더 반영'); refreshReflect(true); }
                        else toast('캘린더 연동이 아직 설정되지 않았습니다');
                    });
                }));
            list.querySelectorAll('.rf-title-view, .rf-body-view').forEach((el) =>
                el.addEventListener('click', () => enterEdit(el.closest('.rf-card'))));
            list.querySelectorAll('.rf-edit').forEach((b) =>
                b.addEventListener('click', () => enterEdit(b.closest('.rf-card'))));
            list.querySelectorAll('.rf-edit-cancel').forEach((b) =>
                b.addEventListener('click', () => { b.closest('.rf-inline-edit').hidden = true; }));
            list.querySelectorAll('.rf-edit-save').forEach((b) =>
                b.addEventListener('click', () => {
                    const card = b.closest('.rf-card');
                    const id = card.dataset.id;
                    const kind = (card.querySelector('input[name="rek' + id + '"]:checked') || {}).value || '';
                    const title = (card.querySelector('.rf-edit-title')?.value || '').trim();
                    const text = (card.querySelector('.rf-edit-text')?.value || '').trim();
                    const tags = normalizeTags((card.querySelector('.rf-edit-tags')?.value || '').trim());
                    const review_date = card.querySelector('.rf-edit-review-date')?.value || '';
                    const event_date = card.querySelector('.rf-edit-event-date')?.value || '';
                    if (!title && !text) { toast('제목이나 내용을 입력하세요'); return; }
                    fetch('/reflect/update/' + id, {
                        method: 'POST', headers: FORM_HEADERS,
                        body: new URLSearchParams({ kind, title, text, tags, review_date, event_date }).toString(),
                    })
                        .then((r) => r.json())
                        .then((d) => {
                            if (!d.ok) { toast('저장 실패'); return; }
                            toast('저장됨'); refreshReflect(true);
                        })
                        .catch(() => toast('저장 실패'));
                }));
        }

        // ---- 미도래 칩 바인딩(클릭 이동·삭제) ----
        function bindUpcoming() {
            if (!upcoming) return;
            upcoming.querySelectorAll('.rf-chip').forEach((chip) =>
                chip.addEventListener('click', () => focusCard(chip.dataset.target)));
            upcoming.querySelectorAll('.rf-chip-del').forEach((b) =>
                b.addEventListener('click', (e) => { e.stopPropagation(); deleteItem(b.dataset.id); }));
        }

        // ---- 작성 바(기록 추가) ----
        bindListEditor(document.getElementById('rf-text'));
        bindTagAutocomplete(document.getElementById('rf-tags'));
        document.getElementById('rf-add')?.addEventListener('click', () => {
            const ta = document.getElementById('rf-text');
            const titleEl = document.getElementById('rf-title');
            const title = (titleEl?.value || '').trim();
            const text = (ta.value || '').trim();
            if (!title && !text) { toast('제목이나 내용을 입력하세요'); return; }
            const kind = (document.querySelector('input[name="rk"]:checked') || {}).value || '고민';
            const tags = normalizeTags((document.getElementById('rf-tags').value || '').trim());
            const review_date = document.getElementById('rf-review')?.value || '';
            const op = {
                id: genId(), kind: 'reflect-add', url: '/reflect/add', headers: FORM_HEADERS,
                body: new URLSearchParams({ kind, title, text, tags, review_date }).toString(),
            };
            fetch(op.url, { method: 'POST', headers: op.headers, body: op.body })
                .then((r) => r.json())
                .then((d) => {
                    if (!d.ok) { toast('저장 실패'); return; }
                    toast(d.synced ? '기록 · 캘린더 반영' : '기록함 (캘린더 미반영)');
                    titleEl.value = ''; ta.value = '';
                    document.getElementById('rf-tags').value = '';
                    document.getElementById('rf-review').value = '';
                    refreshReflect(true);
                })
                .catch(() => { enqueue(op); toast('저장 대기 · 연결되면 전송'); });
        });

        // 초기 바인딩
        bindList();
        bindUpcoming();
        const searchInput = document.getElementById('rf-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', applySearch);
            if (searchInput.value.trim()) applySearch();   // 딥링크 q 반영
        }

        // 자동 폴링·수동 동기화(구글 연동이 켜진 경우에만)
        const syncBtn = document.getElementById('rf-sync-now');
        if (syncBtn) {
            syncBtn.addEventListener('click', () => {
                syncBtn.disabled = true; toast('동기화 중…');
                refreshReflect(true).finally(() => { syncBtn.disabled = false; toast('동기화 완료'); });
            });
            setInterval(() => { if (!document.hidden) refreshReflect(false); }, 60000);
            window.addEventListener('focus', () => refreshReflect(false));
            document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshReflect(false); });
        }
    }

    // 슬롯 DO 옆 '고민' 버튼으로 여는 공용 작성창(오늘 화면)
    function bindReflectModal() {
        const modal = document.getElementById('reflect-modal');
        if (!modal) return;
        bindListEditor(document.getElementById('rm-text'));
        const close = () => { modal.hidden = true; };
        const open = () => {
            modal.hidden = false;
            setTimeout(() => document.getElementById('rm-text')?.focus(), 30);
        };
        document.querySelectorAll('.slot-reflect').forEach((btn) => {
            btn.addEventListener('click', (e) => { e.preventDefault(); open(); });
        });
        modal.querySelector('.rm-close')?.addEventListener('click', close);
        modal.querySelector('.rm-backdrop')?.addEventListener('click', close);
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.hidden) close();
        });
        document.getElementById('rm-save')?.addEventListener('click', () => {
            const ta = document.getElementById('rm-text');
            const titleEl = document.getElementById('rm-title');
            const title = (titleEl?.value || '').trim();
            const text = (ta.value || '').trim();
            if (!title && !text) { toast('제목이나 내용을 입력하세요'); return; }
            const kind = (modal.querySelector('input[name="rmk"]:checked') || {}).value || '고민';
            const tags = normalizeTags((document.getElementById('rm-tags').value || '').trim());
            const review_date = document.getElementById('rm-review')?.value || '';
            const op = {
                id: genId(), kind: 'reflect-add', url: '/reflect/add', headers: FORM_HEADERS,
                body: new URLSearchParams({ kind: kind, title: title, text: text, tags: tags, review_date: review_date }).toString(),
            };
            fetch(op.url, { method: 'POST', headers: op.headers, body: op.body })
                .then((r) => r.json())
                .then((d) => {
                    if (!d.ok) { toast('저장 실패'); return; }
                    toast(d.synced ? '기록 · 캘린더 반영' : '기록함');
                    ta.value = '';
                    if (titleEl) titleEl.value = '';
                    document.getElementById('rm-tags').value = '';
                    document.getElementById('rm-review').value = '';
                    close();
                })
                .catch(() => { enqueue(op); toast('저장 대기 · 연결되면 전송'); close(); });
        });
    }

    // ---- 주간 미처리 수집함 (오늘 빠른수집함과 같은 inbox 테이블, 추가·수정·삭제) ----
    function bindWeekInbox() {
        const list = document.getElementById('wk-inbox-list');
        const input = document.getElementById('wk-inbox-input');
        const addBtn = document.getElementById('wk-inbox-add');
        if (!list && !input) return;
        const countEl = document.getElementById('wk-inbox-count');
        const empty = document.getElementById('wk-inbox-empty');
        const bump = (d) => { if (countEl) countEl.textContent = Math.max(0, (parseInt(countEl.textContent, 10) || 0) + d); };
        const refreshEmpty = () => { if (empty) empty.hidden = !!(list && list.querySelector('.wk-inbox-item')); };
        const bindEdit = (ti) => {
            let last = ti.value;
            const save = () => {
                const v = (ti.value || '').trim();
                const id = ti.dataset.id;
                if (String(id).indexOf('tmp-') === 0) return;   // 아직 미동기화 항목
                if (v === last || !v) return;
                last = v;
                sendOrQueue(
                    { id: genId(), kind: 'inbox-edit', url: '/inbox/update', headers: FORM_HEADERS,
                      body: new URLSearchParams({ item_id: id, text: v }).toString(),
                      dedupe: 'inbox-edit:' + id },
                    () => autosaveToast(),
                    () => toast('저장 대기 · 자동 재시도'),
                );
            };
            ti.addEventListener('change', save);
            ti.addEventListener('blur', save);
            ti.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); ti.blur(); }
            });
        };
        const remove = (row) => {
            const id = row.dataset.id;
            row.remove(); bump(-1); refreshEmpty();
            if (String(id).indexOf('tmp-') === 0) { cancelQueued(row.dataset.op); return; }
            sendOrQueue(
                { id: genId(), kind: 'inbox-op', url: '/inbox/delete/' + id, headers: {}, body: '' },
                null, () => toast('전송 대기 · 자동 재시도'),
            );
        };
        // GTD 상태(미분류/다음행동/대기/언젠가/참고) 자동저장
        const STATUS_OPTS = [['', '미분류'], ['next', '다음행동'], ['wait', '대기'], ['someday', '언젠가'], ['ref', '참고']];
        const bindStatus = (sel) => {
            sel.addEventListener('change', () => {
                const id = sel.dataset.id;
                if (String(id).indexOf('tmp-') === 0) return;   // 아직 미동기화
                sendOrQueue(
                    { id: genId(), kind: 'inbox-status', url: '/inbox/status', headers: FORM_HEADERS,
                      body: new URLSearchParams({ item_id: id, status: sel.value }).toString(),
                      dedupe: 'inbox-status:' + id },
                    () => autosaveToast(),
                    () => toast('저장 대기 · 자동 재시도'),
                );
            });
        };
        const makeStatusSelect = (id, cur) => {
            const sel = document.createElement('select');
            sel.className = 'wk-inbox-status'; sel.dataset.id = id;
            STATUS_OPTS.forEach(([v, t]) => {
                const o = document.createElement('option');
                o.value = v; o.textContent = t;
                if (v === (cur || '')) o.selected = true;
                sel.appendChild(o);
            });
            bindStatus(sel);
            return sel;
        };
        const addRow = (id, text, opId) => {
            const row = el('div', 'wk-inbox-item');
            row.dataset.id = id; if (opId) row.dataset.op = opId;
            const ti = document.createElement('input');
            ti.type = 'text'; ti.className = 'wk-inbox-text'; ti.value = text; ti.dataset.id = id;
            bindEdit(ti);
            const del = document.createElement('button');
            del.type = 'button'; del.className = 'inbox-del wk-inbox-del'; del.title = '삭제'; del.textContent = '✕';
            del.addEventListener('click', () => remove(row));
            row.appendChild(ti); row.appendChild(makeStatusSelect(id, '')); row.appendChild(del);
            list.insertBefore(row, list.firstChild);
            refreshEmpty();
        };
        let inflight = false;
        const add = () => {
            if (!input || inflight) return;
            const text = input.value.trim();
            if (!text) return;
            inflight = true;
            const op = { id: genId(), kind: 'inbox-add', url: '/inbox/add', headers: FORM_HEADERS,
                         body: new URLSearchParams({ text: text }).toString() };
            fetch(op.url, { method: 'POST', headers: op.headers, body: op.body })
                .then((r) => r.json())
                .then((d) => {
                    if (!d.ok) return;
                    addRow(d.id, d.text); input.value = ''; bump(1); toast('수집함에 추가');
                })
                .catch(() => {
                    enqueue(op); addRow('tmp-' + op.id, text, op.id);
                    input.value = ''; bump(1); toast('수집함 대기 · 연결되면 전송');
                })
                .finally(() => { inflight = false; });
        };
        list?.querySelectorAll('.wk-inbox-text').forEach(bindEdit);
        list?.querySelectorAll('.wk-inbox-status').forEach(bindStatus);
        list?.querySelectorAll('.wk-inbox-del').forEach((b) =>
            b.addEventListener('click', () => remove(b.closest('.wk-inbox-item'))));
        addBtn?.addEventListener('click', add);
        input?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); add(); }
        });
    }

    // ---- 오늘 외부 입력: 일정→구글 / 할일→Things3 (입력 즉시 낙관적 표시) ----
    function bindTodayExternal() {
        const form = document.querySelector('.day-form');
        const dateOf = () => (form ? form.dataset.date : '');
        const optimistic = (boxId, makeRow) => {
            const box = document.getElementById(boxId);
            if (!box) return;
            box.querySelector('.agenda-empty')?.remove();
            box.insertBefore(makeRow(), box.firstChild);
        };

        const evInput = document.getElementById('ev-input');
        const evDate = document.getElementById('ev-date');
        const addEvent = () => {
            const title = (evInput?.value || '').trim();
            if (!title) return;
            const date = (evDate?.value || '').trim() || dateOf();
            fetch('/gcal/event/add', {
                method: 'POST', headers: FORM_HEADERS,
                body: new URLSearchParams({ title: title, date: date }).toString(),
            })
                .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
                .then(({ ok, d }) => {
                    if (ok && d.ok) {
                        optimistic('agenda-events', () => {
                            const row = el('div', 'agenda-row event');
                            row.appendChild(el('span', 't', date !== dateOf() ? date : '종일'));
                            row.appendChild(el('span', 'x', title));
                            return row;
                        });
                        evInput.value = ''; if (evDate) evDate.value = '';
                        toast('일정 추가 → 구글 캘린더');
                    } else { toast((d && d.error) || '일정 추가 실패'); }
                })
                .catch(() => toast('연결이 필요합니다'));
        };
        document.getElementById('ev-add')?.addEventListener('click', addEvent);
        evInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addEvent(); }
        });

        const taskInput = document.getElementById('task-input');
        const addTask = () => {
            const title = (taskInput?.value || '').trim();
            if (!title) return;
            fetch('/things/add', {
                method: 'POST', headers: FORM_HEADERS,
                body: new URLSearchParams({ title: title }).toString(),
            })
                .then((r) => r.json().then((d) => ({ ok: r.ok, d })))
                .then(({ ok, d }) => {
                    if (ok && d.ok) {
                        optimistic('agenda-tasks', () => {
                            const row = el('div', 'agenda-row task');
                            row.appendChild(el('span', 'x', title));
                            return row;
                        });
                        taskInput.value = '';
                        toast('할일 추가 → Things3');
                    } else { toast((d && d.error) || '할일 추가 실패'); }
                })
                .catch(() => toast('연결이 필요합니다'));
        };
        document.getElementById('task-add')?.addEventListener('click', addTask);
        taskInput?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); addTask(); }
        });
    }

    // ---- 블록 PLAN 이월(내일로) ------------------------------------------
    function bindRollover() {
        document.querySelectorAll('.block-rollover').forEach((btn) => {
            btn.addEventListener('click', () => {
                postForm('/block/rollover', { block_id: btn.dataset.blockId }).then((d) => {
                    if (d && d.ok) toast('내일 ' + (d.label || '') + ' 계획으로 이월');
                    else if (d && d.error === 'empty') toast('이 블록 PLAN이 비어 있습니다');
                    else toast('이월 실패');
                });
            });
        });
    }

    // ---- 하루 마감(오늘 감사 한 줄 → 고결감 / 내일 가장 중요한 일 → 내일 목표) ----
    function bindShutdown() {
        const form = document.querySelector('.day-form');
        const date = form ? form.dataset.date : '';
        const thanks = document.getElementById('sd-thanks');
        const saveThanks = () => {
            const t = (thanks?.value || '').trim();
            if (!t) return;
            fetch('/reflect/add', {
                method: 'POST', headers: FORM_HEADERS,
                body: new URLSearchParams({ kind: '감사', title: t, text: '', tags: '' }).toString(),
            })
                .then((r) => r.json())
                .then((d) => {
                    if (d && d.ok) { thanks.value = ''; toast(d.synced ? '감사 기록 · 캘린더 반영' : '감사 기록'); }
                    else toast('기록 실패');
                })
                .catch(() => toast('연결이 필요합니다'));
        };
        document.getElementById('sd-thanks-btn')?.addEventListener('click', saveThanks);
        thanks?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); saveThanks(); }
        });

        const tom = document.getElementById('sd-tomorrow');
        const saveTom = () => {
            const t = (tom?.value || '').trim();
            if (!t) return;
            postForm('/meta/tomorrow-goal', { date: date, text: t }).then((d) => {
                if (d && d.ok) { toast('내일 목표로 저장'); }
                else toast('저장 실패');
            });
        };
        document.getElementById('sd-tomorrow-btn')?.addEventListener('click', saveTom);
        tom?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); saveTom(); }
        });
    }

    // ---- init ------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', () => {
        restore();

        document.querySelectorAll('select.cat-select').forEach((sel) => {
            paintCategory(sel);
            sel.addEventListener('change', () => paintCategory(sel));
        });
        document.querySelectorAll('.slot-play').forEach((btn) => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                ensureNotifPermission();
                startFocus(btn.dataset.start);
            });
        });

        const pomo = document.getElementById('pomo');
        if (pomo) {
            pomo.querySelector('.pomo-dial')?.addEventListener('click', () => {
                pomo.classList.toggle('expanded');
            });
            pomo.querySelector('.pomo-start')?.addEventListener('click', () => {
                ensureNotifPermission();
                startFocus(currentSlotHHMM());
            });
            pomo.querySelector('.pomo-skip')?.addEventListener('click', () => skip());
            pomo.querySelector('.pomo-stop')?.addEventListener('click', () => stop());
            pomo.querySelector('.pomo-auto')?.addEventListener('click', () => toggleAuto());
        }

        // 테마 토글
        document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);

        // 빠른 수집함. 한글 IME 조합 Enter(229/isComposing)는 무시해 2회 추가를 막는다.
        document.getElementById('inbox-add')?.addEventListener('click', inboxAdd);
        document.getElementById('inbox-input')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) {
                e.preventDefault();
                inboxAdd();
            }
        });
        document.querySelectorAll('.inbox-send').forEach((btn) => {
            btn.addEventListener('click', () => openInboxBlocks(btn.closest('.inbox-item')));
        });
        document.querySelectorAll('.inbox-done').forEach((btn) => {
            btn.addEventListener('click', () => inboxDone(btn.closest('.inbox-item')));
        });
        document.querySelectorAll('.inbox-del').forEach((btn) => {
            btn.addEventListener('click', () => inboxDelete(btn.closest('.inbox-item')));
        });

        // 오늘 일정·할 일 수동 새로고침(즉시 폴링)
        document.getElementById('agenda-refresh')?.addEventListener('click', (e) => {
            const btn = e.currentTarget;
            btn.classList.add('spinning');
            pollDay();
            setTimeout(() => btn.classList.remove('spinning'), 800);
            toast('동기화');
        });

        bindSlotChecks();
        bindBlockTools();
        bindSettings();
        bindBlockTimes();
        bindPlan();
        bindPlanAreas();
        bindReflect();
        bindReflectModal();
        bindWeekInbox();
        bindTodayExternal();
        bindRollover();
        bindShutdown();

        // 실시간 폴링 + 앱 재진입 시 현재 블록 재포커싱
        if (document.querySelector('.day-form')) {
            setInterval(pollDay, 60000);
            let hiddenAt = 0;
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) { hiddenAt = Date.now(); return; }
                pollDay();
                flushQueue();
                checkStale();
                // 한동안 닫았다 다시 열면(폰 PWA 복귀 포함) 현재 블록으로 재포커스
                if (Date.now() - hiddenAt > 90000) setTimeout(initialScroll, 220);
            });
            window.addEventListener('focus', () => { pollDay(); flushQueue(); checkStale(); });
        }

        bindForm();
        bindAutosaveAll();
        // 모든 텍스트 입력창에 애플노트 스타일 마크다운(자동번호/들여쓰기/하위레벨) 적용.
        document.querySelectorAll('textarea').forEach((ta) => bindListEditor(ta));

        // 대기열 자동 전송: 로드 직후 + 30초마다 재시도 + 연결 복구 이벤트 때
        updateNetStatus();
        flushQueue();
        setInterval(flushQueue, 30000);
        window.addEventListener('online', () => { updateNetStatus(); flushQueue(); checkStale(); });

        // 오프라인·지난 날짜 감지: 로드 직후 + 주기적 재시도(연결되면 오늘로 자동 이동)
        checkStale();
        setInterval(checkStale, 30000);

        // 사용자가 직접 스크롤·터치 중이면 자동 슬롯 추적을 잠시 멈춤
        ['wheel', 'touchstart', 'touchmove', 'pointerdown'].forEach((ev) => {
            window.addEventListener(ev, () => { lastUserInteract = Date.now(); }, { passive: true });
        });

        // 화면 회전·리사이즈 시 현재 슬롯으로 재포커스(가로 전환에서 어긋남 방지)
        let reflowTimer = null;
        function scheduleRefocus(force) {
            clearTimeout(reflowTimer);
            reflowTimer = setTimeout(() => {
                if (!force && Date.now() - lastUserInteract < 1500) return;  // 스크롤발 주소창 리사이즈는 무시
                refocusCurrent();
            }, 300);
        }
        window.addEventListener('orientationchange', () => scheduleRefocus(true));
        window.addEventListener('resize', () => scheduleRefocus(false));

        // 화면 꺼짐 방지: 로드 시 + 다시 보일 때 + 첫 입력 시 wake lock 획득
        requestWakeLock();
        document.addEventListener('visibilitychange', () => { if (!document.hidden) requestWakeLock(); });
        window.addEventListener('pointerdown', requestWakeLock, { passive: true, once: true });

        render();
        // 브라우저 스크롤 복원이 초기 포커스를 덮어쓰지 않도록 수동 처리 후
        // 레이아웃이 끝난 시점(load + 약간의 지연)에 현재 블록으로 이동.
        if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
        const runScroll = () => setTimeout(initialScroll, 180);
        if (document.readyState === 'complete') runScroll();
        else window.addEventListener('load', runScroll, { once: true });
        setInterval(tick, TICK_MS);

        // service worker: 등록 + 업데이트 자동 적용
        // 새 서비스워커가 제어를 넘겨받으면(업데이트 활성화) 페이지를 한 번만 자동 새로고침해
        // 안드로이드 크롬 등에서 옛 캐시가 남아 옛 화면이 보이는 문제를 방지한다.
        if ('serviceWorker' in navigator) {
            if (navigator.serviceWorker.controller) {
                let swRefreshing = false;
                navigator.serviceWorker.addEventListener('controllerchange', () => {
                    if (swRefreshing) return;
                    swRefreshing = true;
                    window.location.reload();
                });
            }
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .then((reg) => { reg.update().catch(() => {}); })
                .catch(() => {});
        }

        // first user interaction → unlock audio
        const unlock = () => {
            getAudio();
            document.removeEventListener('click', unlock);
            document.removeEventListener('touchstart', unlock);
        };
        document.addEventListener('click', unlock, { once: true });
        document.addEventListener('touchstart', unlock, { once: true });
    });
})();
