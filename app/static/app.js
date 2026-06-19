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
        render();
    }

    // ---- render ----------------------------------------------------------
    function render() {
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

        // highlight current-time slot row
        const cur = currentSlotHHMM();
        document.querySelectorAll('.slot').forEach((row) => {
            const t = row.dataset.start;
            const isNow = t === cur;
            row.classList.toggle('is-now', isNow);
            row.classList.toggle('is-pomo-focus', isNow && state.phase === 'FOCUS' && state.slotStart === t);
        });

        // 현재 시각 블록 강조 (실제 오늘을 보는 경우에만)
        const dayForm = document.querySelector('.day-form');
        if (dayForm && dayForm.dataset.today === '1') {
            const d = new Date();
            const m = d.getHours() * 60 + d.getMinutes();
            document.querySelectorAll('.block').forEach((blk) => {
                const s = hhmmToMin(blk.dataset.start);
                const e = hhmmToMin(blk.dataset.end);
                blk.classList.toggle('is-current', m >= s && m < e);
            });
        }

        applyBlockCollapse();
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
    function inboxAdd() {
        const input = document.getElementById('inbox-input');
        if (!input) return;
        const text = input.value.trim();
        if (!text) return;
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
            .catch(queueIt);
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
            if (dayForm && dayForm.dataset.today === '1') {
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
        if (!dayForm || dayForm.dataset.today !== '1') return;
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
        if (!dayForm || dayForm.dataset.today !== '1') return;
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
        const box = document.getElementById('agenda');
        if (!box) return;
        const events = data.events || [];
        const tasks = data.tasks || [];
        box.textContent = '';
        events.forEach((ev) => {
            const row = el('div', 'agenda-row event' + (ev.color ? ' cal-' + ev.color : ''));
            row.appendChild(el('span', 't', ev.all_day ? '종일' : (ev.start || '')));
            row.appendChild(el('span', 'x', ev.title));
            box.appendChild(row);
        });
        tasks.forEach((t) => {
            const row = el('div', 'agenda-row task');
            if (t.time) row.appendChild(el('span', 't', t.time));
            row.appendChild(el('span', 'x', t.title));
            if (t.overdue) row.appendChild(el('span', 'dl', '지남'));
            else if (t.deadline) row.appendChild(el('span', 'dl', '~' + t.deadline));
            box.appendChild(row);
        });
        if (!events.length && !tasks.length) {
            box.appendChild(el('div', 'ctx-empty agenda-empty',
                'Things3 Today와 구글 캘린더 일정이 여기에 한 번에 모입니다.'));
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
        if (stack && dayForm && dayForm.dataset.today === '1' && settingOn('collapse_blocks', true)) {
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
        if (!addBtn && !document.getElementById('set-behavior')) return;   // 설정 페이지 아님

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

        // 빠른 수집함
        document.getElementById('inbox-add')?.addEventListener('click', inboxAdd);
        document.getElementById('inbox-input')?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); inboxAdd(); }
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
        bindPlan();
        bindPlanAreas();

        // 실시간 폴링 + 앱 재진입 시 현재 블록 재포커싱
        if (document.querySelector('.day-form')) {
            setInterval(pollDay, 60000);
            let hiddenAt = 0;
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) { hiddenAt = Date.now(); return; }
                pollDay();
                flushQueue();
                // 한동안 닫았다 다시 열면(폰 PWA 복귀 포함) 현재 블록으로 재포커스
                if (Date.now() - hiddenAt > 90000) setTimeout(initialScroll, 220);
            });
            window.addEventListener('focus', () => { pollDay(); flushQueue(); });
        }

        bindForm();

        // 대기열 자동 전송: 로드 직후 + 30초마다 재시도 + 연결 복구 이벤트 때
        updateNetStatus();
        flushQueue();
        setInterval(flushQueue, 30000);
        window.addEventListener('online', () => { updateNetStatus(); flushQueue(); });

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
