// 6block 클라이언트 - 누른 슬롯의 종료시각까지 집중하는 포모도로, 카테고리 띠, PWA 등록
(function () {
    'use strict';

    const TICK_MS = 1000;
    const SLOT_MIN = 30;   // 슬롯 길이(분). 집중은 누른 슬롯의 종료시각까지 흐른다.
    const RING_C = 2 * Math.PI * 44;   // 진행 링 둘레(r=44), CSS stroke-dasharray와 일치

    const state = {
        phase: 'IDLE',      // 'IDLE' | 'FOCUS'
        startedAt: 0,       // epoch ms (집중 시작 시각)
        endsAt: 0,          // epoch ms (집중 종료 = 슬롯 종료시각)
        slotStart: '',      // 'HH:MM'
        auto: localStorage.getItem('pomoAuto') === 'true',
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
        } else if (state.phase === 'FOCUS' && Date.now() >= state.endsAt) {
            transitionToIdle(state.auto);
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
        const slot = sel.closest('.slot');
        if (slot) { slot.style.setProperty('--row-accent', accent || 'transparent'); return; }
        const block = sel.closest('.block, .mini-block');
        if (block) block.style.borderLeftColor = accent || '';
    }

    // ---- form save indication -------------------------------------------
    function bindForm() {
        document.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                const form = document.querySelector('form.day-form, form.week-form');
                if (form) { e.preventDefault(); form.submit(); }
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
        fetch('/inbox/add', { method: 'POST', body: new URLSearchParams({ text }) })
            .then((r) => r.json())
            .then((data) => {
                if (!data.ok) return;
                addInboxItem(data.id, data.text);
                input.value = '';
                bumpInboxCount(1);
                toast('수집함에 추가');
            })
            .catch(() => toast('추가 실패'));
    }
    function addInboxItem(id, text) {
        const list = document.getElementById('inbox-list');
        if (!list) return;
        const item = document.createElement('div');
        item.className = 'inbox-item';
        item.dataset.id = id;
        const span = document.createElement('span');
        span.className = 'txt';
        span.textContent = text;
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
        item.appendChild(span);
        item.appendChild(btn);
        item.appendChild(del);
        list.insertBefore(item, list.firstChild);
    }
    function inboxDone(item) {
        if (!item) return;
        fetch('/inbox/done/' + item.dataset.id, { method: 'POST' })
            .then(() => { item.remove(); bumpInboxCount(-1); })
            .catch(() => toast('처리 실패'));
    }
    function inboxDelete(item) {
        if (!item) return;
        fetch('/inbox/delete/' + item.dataset.id, { method: 'POST' })
            .then(() => { item.remove(); bumpInboxCount(-1); })
            .catch(() => toast('삭제 실패'));
    }
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
            const row = el('div', 'agenda-row event');
            row.appendChild(el('span', 't', ev.all_day ? '종일' : (ev.start || '')));
            row.appendChild(el('span', 'x', ev.title));
            box.appendChild(row);
        });
        tasks.forEach((t) => {
            const row = el('div', 'agenda-row task');
            row.appendChild(el('span', 't', t.time || '·'));
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
                const row = el('div', 'pop-row ' + it.kind);
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
                fetch('/slot/done/' + cb.dataset.slot, {
                    method: 'POST', body: new URLSearchParams({ done }),
                })
                    .then((r) => r.json())
                    .then(() => toast(cb.checked ? '완료 체크' : '체크 해제'))
                    .catch(() => toast('저장 실패'));
            });
        });
    }

    // ---- 블록 호버 버튼 + 현재/전체 토글 ---------------------------------
    function bindBlockTools() {
        // 호버 버튼: 데스크톱은 CSS :hover, 모바일은 탭으로 팝오버 토글
        document.querySelectorAll('.hover-btn').forEach((btn) => {
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
        if (stack && dayForm && dayForm.dataset.today === '1') {
            stack.classList.add('collapsed');  // 기본값: 현재 블록만
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
        document.querySelectorAll('.inbox-done').forEach((btn) => {
            btn.addEventListener('click', () => inboxDone(btn.closest('.inbox-item')));
        });
        document.querySelectorAll('.inbox-del').forEach((btn) => {
            btn.addEventListener('click', () => inboxDelete(btn.closest('.inbox-item')));
        });

        bindSlotChecks();
        bindBlockTools();

        // 실시간 폴링 + 앱 재진입 시 현재 블록 재포커싱
        if (document.querySelector('.day-form')) {
            setInterval(pollDay, 60000);
            let hiddenAt = 0;
            document.addEventListener('visibilitychange', () => {
                if (document.hidden) { hiddenAt = Date.now(); return; }
                pollDay();
                // 한동안 닫았다 다시 열면(폰 PWA 복귀 포함) 현재 블록으로 재포커스
                if (Date.now() - hiddenAt > 90000) setTimeout(initialScroll, 220);
            });
            window.addEventListener('focus', pollDay);
        }

        bindForm();

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

        // service worker
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
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
