// 6block 클라이언트 - 누른 슬롯의 종료시각까지 집중하는 포모도로, 카테고리 띠, PWA 등록
(function () {
    'use strict';

    const TICK_MS = 1000;
    // 집중 25분 뒤에는 다음 슬롯이 시작할 때까지 휴식한다. 슬롯이 30분이라 블록 안에서는 5분,
    // B1·B3·B5의 마지막 슬롯은 블록 사이 10분 공백이 더해져 15분 휴식이 된다.
    const FOCUS_MIN = 25;
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
        phase: 'IDLE',      // 'IDLE' | 'FOCUS' | 'BREAK'
        startedAt: 0,       // epoch ms (집중 시작 시각, 휴식이면 집중이 끝난 시각)
        endsAt: 0,          // epoch ms (집중이면 시작+25분, 휴식이면 다음 슬롯 시작시각)
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
            if (raw.phase === 'FOCUS' || raw.phase === 'BREAK') {
                state.phase = raw.phase;
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
    // 화면에 그려진 슬롯 행의 시간 범위. 블록이 :00/:30이 아닌 시각(예: 09:10)에 시작해도
    // '지금' 슬롯을 찾을 수 있게 실제 행을 기준으로 판단한다. 목록은 페이지당 한 번만 만든다.
    let slotRangeCache = null;
    function slotRanges() {
        if (!slotRangeCache) {
            slotRangeCache = [...document.querySelectorAll('.block-stack .slot')].map((el) => ({
                el, s: hhmmToMin(el.dataset.start), e: hhmmToMin(el.dataset.end),
            }));
        }
        return slotRangeCache;
    }
    function currentSlotEl(date) {
        const d = date || new Date();
        const m = d.getHours() * 60 + d.getMinutes();
        const hit = slotRanges().find((r) => m >= r.s && m < r.e);
        return hit ? hit.el : null;
    }
    // 강조·자동추적이 쓰는 '지금 슬롯' 키. 슬롯 행이 없으면 30분 격자로 되돌아간다.
    function currentSlotKey(date) {
        const el = currentSlotEl(date);
        return el ? el.dataset.start : currentSlotHHMM(date);
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
    // 오늘의 '자정부터 몇 분' → epoch ms
    function epochAtMin(min) {
        const d = new Date();
        d.setHours(Math.floor(min / 60), min % 60, 0, 0);
        return d.getTime();
    }
    function hhmmOfEpoch(ms) {
        const d = new Date(ms);
        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    // 'HH:MM' 슬롯 시작 → 그 슬롯의 집중 종료시각(시작+25분)의 epoch ms (오늘 기준)
    function focusEndEpoch(slotStart) {
        return epochAtMin(hhmmToMin(slotStart) + FOCUS_MIN);
    }
    // 이 슬롯 다음에 오는 슬롯 행. 하루의 마지막 슬롯이면 null.
    // 휴식은 이 슬롯의 시작시각까지 흐르므로 블록 사이 공백이 그대로 휴식에 더해진다.
    function nextSlotAfter(slotStart) {
        const cur = hhmmToMin(slotStart);
        return slotRanges().find((r) => r.s > cur) || null;
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
    // 잠긴(suspended) 오디오는 시계가 멈춰 있다. resume()은 비동기라서 먼저 소리를 예약하면
    // 멈춘 시계 기준으로 예약돼 깨어난 순간엔 이미 지난 시각이 되어 그냥 사라진다.
    // 그래서 반드시 깨어난 뒤에 예약한다. 시작음이 안 울리던 원인이 이것이다.
    function withAudio(play) {
        const ctx = getAudio(); if (!ctx) return;
        if (ctx.state === 'suspended') ctx.resume().then(() => play(ctx)).catch(() => {});
        else play(ctx);
    }
    // ---- 알람 음원 -------------------------------------------------------
    // 음원과 길이는 설정 탭에서 고른다. 파일 없이 코드로 만들어 어떤 길이로도 늘어난다.
    // 한 음을 예약하는 공통 도구. mix는 다 합쳐 1이 넘지 않게 두어 찌그러짐을 막는다.
    function tone(ctx, at, dur, freq, mix, type, attack) {
        const g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, at);
        g.gain.exponentialRampToValueAtTime(Math.max(0.0002, mix), at + (attack || 0.01));
        g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
        g.connect(ctx.destination);
        const osc = ctx.createOscillator();
        osc.type = type || 'triangle';
        osc.frequency.value = freq;
        osc.connect(g);
        osc.start(at);
        osc.stop(at + dur + 0.05);
    }

    // 각 음원은 (ctx, t0, sec)를 받아 sec 길이로 울린다.
    const SOUNDS = {
        // 맑은 완전5도 화음 한 번 + 또렷한 어택. 집중 시작에 어울리는 기본값.
        chord(ctx, t0, sec) {
            [[659, 0.4], [988, 0.22], [1319, 0.12]].forEach(([f, mix]) =>
                tone(ctx, t0, sec, f, mix, 'triangle', 0.05));
            tone(ctx, t0, 0.12, 1976, 0.2, 'triangle', 0.008);
        },
        // 비조화 배음의 종소리. 길이에 맞춰 여러 번 친다.
        bell(ctx, t0, sec) {
            const strikes = Math.max(1, Math.round(sec / 1.3));
            const gap = sec / strikes;
            for (let n = 0; n < strikes; n++) {
                [[1, 0.22], [2.0, 0.11], [2.96, 0.07], [4.21, 0.04]].forEach(([p, mix]) =>
                    tone(ctx, t0 + n * gap, gap * 0.95, 440 * p, mix, 'sine', 0.006));
            }
        },
        // 낮고 깊게 퍼지는 울림. 종소리보다 묵직하다.
        gong(ctx, t0, sec) {
            [[1, 0.3], [1.48, 0.12], [2.35, 0.07], [3.42, 0.04]].forEach(([p, mix]) =>
                tone(ctx, t0, sec, 220 * p, mix, 'sine', 0.02));
        },
        // 또렷한 비프 반복. 가장 알람답고 놓치기 어렵다.
        beep(ctx, t0, sec) {
            for (let at = 0; at < sec - 0.05; at += 0.45) {
                tone(ctx, t0 + at, Math.min(0.2, sec - at), 1046, 0.45, 'triangle', 0.01);
            }
        },
        // 아래에서 위로 올라가는 한 줄기 소리.
        rise(ctx, t0, sec) {
            const g = ctx.createGain();
            g.gain.setValueAtTime(0.0001, t0);
            g.gain.exponentialRampToValueAtTime(0.5, t0 + sec * 0.3);
            g.gain.exponentialRampToValueAtTime(0.0001, t0 + sec);
            g.connect(ctx.destination);
            const osc = ctx.createOscillator();
            osc.type = 'triangle';
            osc.frequency.setValueAtTime(440, t0);
            osc.frequency.exponentialRampToValueAtTime(1320, t0 + sec * 0.9);
            osc.connect(g);
            osc.start(t0);
            osc.stop(t0 + sec + 0.05);
        },
    };

    function soundSec(key, def) {
        const v = parseFloat(setget(key));
        return (v > 0 && v <= 10) ? v : def;
    }
    function playSound(name, sec) {
        const make = SOUNDS[name] || SOUNDS.chord;
        withAudio((ctx) => make(ctx, ctx.currentTime, sec));
    }
    // 집중 시작 알람
    function chime() {
        playSound(setget('pomo_start_sound') || 'chord', soundSec('pomo_start_sec', 2.5));
    }
    // 집중 종료(= 휴식 시작) 알람
    function bell() {
        playSound(setget('pomo_end_sound') || 'bell', soundSec('pomo_end_sec', 2.5));
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
    const TOAST_MS = 1800;
    let lastToastAt = 0;        // 사용자가 누른 동작의 안내가 마지막으로 뜬 시각
    function toast(msg) {
        const t = document.getElementById('toast');
        if (!t) return;
        lastToastAt = Date.now();
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), TOAST_MS);
    }

    // ---- state transitions ----------------------------------------------
    // 누른 슬롯의 앞 25분을 집중하고, 남은 시간은 다음 슬롯이 시작할 때까지 휴식한다.
    // 집중 25분이 이미 지난 슬롯을 누르면 곧바로 휴식으로 들어간다.
    function startFocus(slotTime) {
        const slot = slotTime || currentSlotKey();
        const endsAt = focusEndEpoch(slot);
        if (endsAt - Date.now() < 1000) {
            if (startBreak(slot)) toast(`휴식 ${breakMin()}분 · ${hhmmOfEpoch(state.endsAt)} 시작`);
            else toast('이미 지난 슬롯');
            return;
        }
        state.phase = 'FOCUS';
        state.startedAt = Date.now();
        state.endsAt = endsAt;
        state.slotStart = slot;
        persist();
        chime();
        toast(`집중 시작 · ${Math.round((endsAt - Date.now()) / 60000)}분`);
        render();
    }
    // 휴식 단계로 넘긴다. 다음 슬롯이 없거나 이미 지났으면 IDLE로 두고 false를 준다.
    function startBreak(slot) {
        const next = nextSlotAfter(slot);
        const endsAt = next ? epochAtMin(next.s) : 0;
        if (!endsAt || endsAt - Date.now() < 1000) {
            state.phase = 'IDLE';
            state.startedAt = 0;
            state.endsAt = 0;
            persist();
            render();
            return false;
        }
        state.phase = 'BREAK';
        state.startedAt = focusEndEpoch(slot);   // 링 진행률을 휴식 전체 길이로 재게 한다
        state.endsAt = endsAt;
        state.slotStart = slot;
        persist();
        render();
        return true;
    }
    function breakMin() {
        return Math.round((state.endsAt - state.startedAt) / 60000);
    }
    // 집중 25분이 끝났을 때. 종소리로 알리고 휴식으로 넘어간다.
    // '한 일' 칸은 저절로 열지 않는다(슬롯의 '한' 버튼으로 직접 열어 적는다).
    function endFocus(auto) {
        const finished = state.slotStart;
        if (settingOn('pomo_end_alarm', true)) bell();
        const resting = startBreak(finished);
        notify('집중 완료', resting ? `휴식 ${breakMin()}분`
                                    : (auto ? '자동 모드: 다음 슬롯 대기' : '잘했어!'));
        toast(resting ? `집중 완료 · 휴식 ${breakMin()}분` : '집중 완료 · 한 일을 적어두세요');
    }
    function skip() {
        if (state.phase === 'FOCUS') endFocus(false);
        else if (state.phase === 'BREAK') stop();
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
        toast(state.auto ? '자동 모드 ON · 슬롯 시작에 자동 시작' : '자동 모드 OFF');
        render();
    }

    // ---- main tick -------------------------------------------------------
    // 자동 시작은 이 탭을 실제로 쓰고 있을 때만 건다. 다른 기기에 며칠씩 열어둔 탭이
    // 저 혼자 슬롯을 시작해 종을 울리던 것을 막는다. 다시 손대면 곧바로 되살아난다.
    const AUTO_IDLE_MS = 3 * 60 * 60 * 1000;
    let lastBoundaryFired = '';
    let lastActiveAt = Date.now();   // 이 탭에서 마지막으로 사용자가 조작한 시각
    let lastUserInteract = 0;   // 마지막 사용자 스크롤·터치 시각(자동 추적 억제용)
    let lastNowSlot = '';       // 마지막으로 추적한 현재 30분 슬롯(HH:MM)
    let lastRenderSlot = '';    // 슬롯·블록 강조를 마지막으로 다시 칠한 슬롯(매초 재계산 방지)
    function tick() {
        const now = new Date();
        const sec = now.getSeconds();

        if (state.phase === 'IDLE') {
            // 슬롯이 시작될 때 자동 시작. 슬롯 행이 있는 오늘 화면에서만 걸린다.
            // 장기플랜·고민·설정처럼 슬롯 행이 없는 화면에 탭이 떠 있어도 저절로 울리지 않는다.
            if (state.auto && sec < 3 && Date.now() - lastActiveAt < AUTO_IDLE_MS) {
                const el = currentSlotEl(now);
                const nowMin = now.getHours() * 60 + now.getMinutes();
                if (el && nowMin === hhmmToMin(el.dataset.start)
                    && lastBoundaryFired !== el.dataset.start) {
                    lastBoundaryFired = el.dataset.start;
                    startFocus(el.dataset.start);
                }
            }
        } else if (state.phase === 'FOCUS') {
            if (state.endsAt - Date.now() <= 0) endFocus(state.auto);
        } else if (state.phase === 'BREAK') {
            // 휴식이 끝나는 시점은 다음 슬롯이 막 시작한 시점이다. 알람은 울리지 않고,
            // 자동모드면 여기서 곧바로 다음 집중을 시작한다(시작음은 startFocus가 낸다).
            if (state.endsAt - Date.now() <= 0) {
                const next = nextSlotAfter(state.slotStart);
                state.phase = 'IDLE';
                state.startedAt = 0;
                state.endsAt = 0;
                persist();
                if (next && state.auto && Date.now() - lastActiveAt < AUTO_IDLE_MS) {
                    lastBoundaryFired = next.el.dataset.start;
                    startFocus(next.el.dataset.start);
                }
            }
        }
        checkVersion();
        applyPendingReload();
        render(false);
    }

    // ---- 새 버전 자동 반영 -------------------------------------------------
    // 오래 열어둔 탭이 옛 코드를 들고 있으면 기기마다 동작이 달라진다(폰은 새 동작, 맥은 옛
    // 동작). 서버의 현재 버전과 이 페이지가 불러온 버전을 견줘 다르면 스스로 새로고침한다.
    // 탭을 다시 볼 때, 그리고 10분마다 확인한다. 집중·휴식이 도는 중이거나 무언가 입력하는
    // 중에는 미룬다(타이머를 끊거나 타이핑을 날리지 않게).
    const VERSION_CHECK_MS = 10 * 60 * 1000;
    let myVer = '';
    let lastVerCheck = 0;
    let verChecking = false;
    // 새 버전을 봤지만 지금은 끊기 곤란해 미뤄 둔 상태. 한 번 서면 안 내린다.
    let pendingReload = false;
    let pendingToldAt = 0;
    const PENDING_NAG_MS = 5 * 60 * 1000;

    function busyNow() {
        // 타이핑 중이거나 세션(집중·휴식)이 도는 중에는 새로고침으로 끊지 않는다.
        const tag = (document.activeElement || {}).tagName || '';
        return state.phase !== 'IDLE' || /^(INPUT|TEXTAREA|SELECT)$/.test(tag);
    }

    // 매초 불린다. 미뤄 둔 새로고침을 손이 비는 순간 바로 처리한다.
    // 예전에는 버전이 다를 때 busy 면 그냥 넘어가고 끝이라, 세션을 켜 두거나 입력칸에
    // 커서가 있으면 그 탭은 영영 옛 코드로 남았다(맥에서 며칠 묵은 코드가 돌던 원인).
    function applyPendingReload() {
        if (!pendingReload) return;
        if (!busyNow()) { location.reload(); return; }
        // 세션이 몇 시간씩 이어지면 계속 못 바꾼다. 가끔 알려서 사람이 정하게 한다.
        const now = Date.now();
        if (now - pendingToldAt > PENDING_NAG_MS) {
            pendingToldAt = now;
            toast('새 버전이 있습니다. 세션을 마치거나 새로고침하면 바뀝니다.');
        }
    }

    function checkVersion(force) {
        // 안 보이는 탭도 확인한다. 오히려 그때 새로고침하는 것이 가장 방해가 적다.
        if (!myVer || verChecking) return;
        if (!force && Date.now() - lastVerCheck < VERSION_CHECK_MS) return;
        lastVerCheck = Date.now();
        verChecking = true;
        fetch('/version', { cache: 'no-store' })
            .then((r) => r.json())
            .then((d) => {
                if (!d.v || d.v === myVer) return;
                pendingReload = true;
                applyPendingReload();
            })
            .catch(() => {})
            .then(() => { verChecking = false; });
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
            pomo.classList.toggle('break', state.phase === 'BREAK');
            const autoBtn = pomo.querySelector('.pomo-auto');
            if (autoBtn) autoBtn.classList.toggle('on', state.auto);

            const phaseLabel = state.phase === 'FOCUS' ? '집중'
                              : state.phase === 'BREAK' ? '휴식'
                              : (state.auto ? '자동' : '대기');
            const phaseEl = pomo.querySelector('.pomo-phase');
            if (phaseEl) phaseEl.textContent = phaseLabel;

            const timeEl = pomo.querySelector('.pomo-time');
            const ringEl = pomo.querySelector('.pomo-ring-prog');
            const slotEl = pomo.querySelector('.pomo-slot');
            if (state.phase === 'IDLE') {
                if (timeEl) timeEl.textContent = state.auto ? 'AUTO' : '—';
                if (ringEl) ringEl.style.strokeDashoffset = RING_C;
                if (slotEl) {
                    const nb = state.auto ? nextBoundary() : '';
                    // 슬롯 행이 없는 화면(설정·장기 등)에서는 자동 시작이 걸리지 않으므로 비워 둔다.
                    slotEl.textContent = nb ? `다음 시작 · ${nb}`
                        : (state.auto && slotRanges().length ? '오늘 남은 슬롯 없음' : '');
                }
            } else {
                const total = (state.endsAt - state.startedAt) / 1000;
                const remain = (state.endsAt - Date.now()) / 1000;
                const frac = total > 0 ? Math.min(1, Math.max(0, remain / total)) : 0;
                if (timeEl) timeEl.textContent = fmt(remain);
                if (ringEl) ringEl.style.strokeDashoffset = RING_C * (1 - frac);
                if (slotEl) slotEl.textContent = state.phase === 'FOCUS'
                    ? `슬롯 ${state.slotStart} · 집중 ${FOCUS_MIN}분`
                    : `휴식 ${breakMin()}분 · ${hhmmOfEpoch(state.endsAt)} 시작`;
            }
        }

        // 슬롯·블록 강조는 매초가 아니라 30분 슬롯이 바뀔 때(또는 상태 변화에 의한 명시적
        // render 호출)에만 다시 칠해, 폰에서의 상시 CPU·배터리 소모를 줄인다.
        const cur = currentSlotKey();
        if (force !== false || cur !== lastRenderSlot) {
            lastRenderSlot = cur;
            // highlight current-time slot row
            const nowEl = currentSlotEl();
            document.querySelectorAll('.slot').forEach((row) => {
                const t = row.dataset.start;
                const isNow = row === nowEl;
                row.classList.toggle('is-now', isNow);
                row.classList.toggle('is-pomo-focus', isNow && state.phase === 'FOCUS' && state.slotStart === t);
            });

            // 현재 시각 블록 강조 + 포커스 블록 지정 (실제 오늘을 보는 경우에만)
            const dayForm = document.querySelector('.day-form');
            if (dayForm && isDeviceToday()) {
                const d = new Date();
                const m = d.getHours() * 60 + d.getMinutes();
                const focus = focusBlock();
                document.querySelectorAll('.block').forEach((blk) => {
                    const s = hhmmToMin(blk.dataset.start);
                    const e = hhmmToMin(blk.dataset.end);
                    blk.classList.toggle('is-current', m >= s && m < e);
                    blk.classList.toggle('is-focus', blk === focus);
                });
            }

            applyBlockCollapse();
        }
        autoFollowSlot();
    }

    // 지금 봐야 할 블록. 시각이 블록 안이면 그 블록, 블록 사이 틈이나 하루 시작 전이면
    // 다음 블록, 마지막 블록까지 끝났으면 마지막 블록. 블록 시간표에 틈이 있어도
    // 포커싱이 비지 않게 한다.
    function focusBlock() {
        const blocks = [...document.querySelectorAll('.block-stack .block')];
        if (!blocks.length) return null;
        const d = new Date();
        const m = d.getHours() * 60 + d.getMinutes();
        return blocks.find((b) => m >= hhmmToMin(b.dataset.start) && m < hhmmToMin(b.dataset.end))
            || blocks.find((b) => hhmmToMin(b.dataset.start) > m)
            || blocks[blocks.length - 1];
    }

    // 접힘 상태면 포커스 블록만 보이게(정할 블록이 없으면 전체 표시)
    function applyBlockCollapse() {
        const stack = document.querySelector('.block-stack');
        if (!stack) return;
        const blocks = stack.querySelectorAll('.block');
        if (!stack.classList.contains('collapsed')) {
            blocks.forEach((b) => b.classList.remove('blk-collapsed'));
            return;
        }
        const hasFocus = !!stack.querySelector('.block.is-focus');
        blocks.forEach((b) => {
            b.classList.toggle('blk-collapsed', hasFocus && !b.classList.contains('is-focus'));
        });
    }

    // 자동 시작이 다음으로 걸릴 슬롯 시작시각. 30분 격자로 계산하면 블록이 09:40·11:10처럼
    // 시작하는 실제 시간표와 어긋나므로(11:00에 11:30이라고 잘못 표시됐다) 슬롯 행을 따른다.
    // 남은 슬롯이 없으면 빈 문자열.
    function nextBoundary() {
        const d = new Date();
        const m = d.getHours() * 60 + d.getMinutes();
        const next = slotRanges().find((r) => r.s > m);
        return next ? next.el.dataset.start : '';
    }

    // ---- category color stripe ------------------------------------------
    // 카테고리 색은 테마별 톤 변수(--tone-blue/red/black)로 칠해 다크모드에서도 보이게 한다.
    // 슬롯은 왼쪽 띠, 블록·주간 미니블록은 왼쪽 테두리 색으로 구분을 표시한다.
    // 블록 구분 select(.block-cat)의 현재 선택 색 톤을 읽는다(슬롯 상속 색용).
    // 블록 구분 select(.block-cat)의 현재 선택 톤·이름을 읽는다(슬롯 상속 표시용).
    function blockCat(blockEl) {
        const bc = blockEl && blockEl.querySelector('.block-cat');
        if (!bc || !bc.value) return { tone: '', name: '' };
        const opt = bc.options[bc.selectedIndex];
        if (!opt) return { tone: '', name: '' };
        return { tone: (opt.dataset ? opt.dataset.tone || '' : ''), name: (opt.textContent || '').trim() };
    }
    function paintCategory(sel) {
        const opt = sel.options[sel.selectedIndex];
        let tone = (opt && opt.dataset) ? opt.dataset.tone : '';
        const slot = sel.closest('.slot');
        // 슬롯 구분이 비면(상속) 그 블록의 구분 이름·색을 드롭다운에 그대로 표시한다(값은 빈칸=상속 유지).
        let inherited = false;
        if (slot && !sel.value) {
            const bc = blockCat(slot.closest('.block'));
            tone = bc.tone;
            inherited = !!bc.tone;
            const blank = sel.options[0];
            if (blank && blank.value === '') blank.textContent = inherited ? bc.name : '';
        }
        const accent = tone ? `var(--tone-${tone})` : '';
        sel.style.color = accent;
        sel.classList.toggle('has-cat', !!accent && !inherited);   // 개별 지정한 슬롯만 굵게
        sel.classList.toggle('cat-inherited-sel', inherited);       // 상속(블록 따라감)은 옅게 표시
        if (slot) {
            slot.style.setProperty('--row-accent', accent || 'transparent');
            slot.classList.toggle('cat-inherited', inherited);
            return;
        }
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
        // 저장 버튼을 없앤 뒤로 폼이 스스로 전송되는 길은 '칸에서 Enter' 하나뿐이다.
        // 값은 칸마다 이미 자동저장되므로 화면을 새로 띄우지 않고, 그 칸만 확정(blur)해
        // 곧바로 저장되게 한다. ⌘S 는 아래에서 그대로 전체 저장을 맡는다.
        document.querySelectorAll('form.day-form, form.week-form').forEach((f) => {
            f.addEventListener('submit', (e) => {
                e.preventDefault();
                document.activeElement?.blur();
            });
        });
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
                } else if ((m = name.match(/^grat([123])$/))) {
                    el.dataset.asPrefix = 'grat';
                    el.dataset.asIdx = m[1];
                    bindAutoSave(el, 'meta', dateStr, 'grat' + m[1], { groupPrefix: 'grat' });
                } else if ((m = name.match(/^concept([123])$/))) {
                    el.dataset.asPrefix = 'concept';
                    el.dataset.asIdx = m[1];
                    bindAutoSave(el, 'meta', dateStr, 'concept' + m[1], { groupPrefix: 'concept' });
                } else if (name === 'memo') bindAutoSave(el, 'meta', dateStr, 'memo');
                else if (name === 'vow')    bindAutoSave(el, 'meta', dateStr, 'vow');
                else if (name === 'day_review') bindAutoSave(el, 'meta', dateStr, 'day_review');
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
                if (['appointments', 'vow', 'memo'].indexOf(name) >= 0)
                    bindAutoSave(el, 'wmeta', ws, name);
                else if ((m = name.match(/^wgoal([123])$/))) {
                    el.dataset.asPrefix = 'wgoal';
                    el.dataset.asIdx = m[1];
                    bindAutoSave(el, 'wmeta', ws, 'wgoal' + m[1], { groupPrefix: 'wgoal' });
                } else if ((m = name.match(/^ltgoal_(\d+)$/)))
                    bindAutoSave(el, 'ltgoal', m[1], 'ltgoal', { extra: { week_start: ws } });
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
    // 부드럽게 옮기되, 실제로 움직이지 않으면 곧바로 튀어 옮긴다. 기기에서 '동작 줄이기'를
    // 켜 두면 behavior:'smooth' 가 통째로 무시되는 경우가 있어, 그대로 두면 포커싱이
    // 조용히 실패한다(로고를 눌러도 지금 블록으로 가지 않는다).
    function scrollToEl(el) {
        if (!el) return;
        const before = window.scrollY;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => {
            const r = el.getBoundingClientRect();
            const off = Math.abs((r.top + r.height / 2) - window.innerHeight / 2);
            if (window.scrollY === before && off > 80) {
                el.scrollIntoView({ behavior: 'auto', block: 'center' });
            }
        }, 250);
    }

    function initialScroll() {
        let target = null;
        let isSlot = false;
        const hash = location.hash;
        if (hash && hash.indexOf('#blk-') === 0) {
            target = document.querySelector(hash);
        } else {
            const dayForm = document.querySelector('.day-form');
            if (dayForm && isDeviceToday()) {
                // 현재 30분 슬롯을 우선 포커스, 없으면(블록 사이 틈 등) 포커스 블록
                const slot = document.querySelector('.slot.is-now');
                if (slot) { target = slot; isSlot = true; }
                else target = document.querySelector('.block.is-focus');
            }
        }
        if (target) {
            scrollToEl(target);
            lastNowSlot = currentSlotKey();
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
        const cur = currentSlotKey();
        if (cur === lastNowSlot) return;
        if (lastNowSlot === '') { lastNowSlot = cur; return; }   // 초기 1회는 initialScroll이 담당
        lastNowSlot = cur;
        if (Date.now() - lastUserInteract < 8000) return;        // 손으로 조작 중이면 방해 안 함
        scrollToEl(document.querySelector('.slot.is-now'));
    }

    // 화면 회전·리사이즈 후 현재 슬롯을 다시 중앙에 맞춤(가로 전환 등에서 어긋남 방지)
    function refocusCurrent() {
        const dayForm = document.querySelector('.day-form');
        if (!dayForm || !isDeviceToday()) return;
        const target = document.querySelector('.slot.is-now') || document.querySelector('.block.is-focus');
        if (target) target.scrollIntoView({ behavior: 'auto', block: 'center' });
        lastNowSlot = currentSlotKey();
    }

    // ---- 실시간 폴링 (캘린더/Things Today 갱신) -------------------------
    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    // 일정·할 일 목록을 3개까지만 보여주고, 3개를 넘으면 '더보기'로 접는다.
    const agendaExpanded = { 'agenda-events': false, 'agenda-tasks': false };
    function setupAgendaMore(boxId) {
        const box = document.getElementById(boxId);
        if (!box) return;
        const btn = document.querySelector('.agenda-more[data-target="' + boxId + '"]');
        if (!btn) return;
        const n = box.querySelectorAll('.agenda-row').length;
        if (n <= 3) { btn.hidden = true; box.classList.remove('collapsed'); return; }
        btn.hidden = false;
        const open = !!agendaExpanded[boxId];
        box.classList.toggle('collapsed', !open);
        btn.textContent = open ? '접기' : ('+' + (n - 3) + '개 더보기');
    }
    function setupAgendaMoreAll() { setupAgendaMore('agenda-events'); setupAgendaMore('agenda-tasks'); }

    // 목표·달성·감사반성 칸: 포커스하면 전체가 보이게 높이를 늘리고, 벗어나면 한 줄로 접는다.
    // Enter 는 줄바꿈 대신 저장(블러)로 처리해 한 칸이 여러 줄로 저장되지 않게 한다.
    function bindGpInputs() {
        document.querySelectorAll('textarea.gp-input').forEach((ta) => {
            if (ta.dataset.gpBound) return;
            ta.dataset.gpBound = '1';
            // 포커스 이벤트에서 직접 줄바꿈을 켜(:focus 의사클래스에 의존하지 않음) 전체 높이로 늘린다.
            const grow = () => { ta.style.whiteSpace = 'normal'; ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; };
            ta.addEventListener('focus', grow);
            ta.addEventListener('input', () => { if (document.activeElement === ta) grow(); });
            ta.addEventListener('blur', () => { ta.style.whiteSpace = ''; ta.style.height = ''; });
            ta.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); ta.blur(); }
            });
        });
    }

    // ---- 태그 입력 모달: 목표·달성·감사 각 줄의 '＃' 버튼을 누르면 별도 창에서 태그를 입력한다.
    // 값은 숨김 필드(goaltag1 등)에 담겨 저장 버튼(전체 폼)과 함께 가고, 모달 저장 시 즉시 자동저장도 한다.
    function setupTagModal() {
        const modal = document.getElementById('tag-modal');
        if (!modal) return;
        const dayForm = document.querySelector('form.day-form');
        const dateStr = dayForm ? dayForm.dataset.date : null;
        const input = document.getElementById('tag-modal-input');
        const titleEl = document.getElementById('tag-modal-title');
        let curBtn = null;
        const hiddenOf = (btn) => document.querySelector('input[name="' + btn.dataset.group + btn.dataset.idx + '"]');
        const close = () => { modal.hidden = true; curBtn = null; };
        const open = (btn) => {
            curBtn = btn;
            const hid = hiddenOf(btn);
            input.value = hid ? hid.value : '';
            if (titleEl) titleEl.textContent = (btn.getAttribute('aria-label') || '태그');
            modal.hidden = false;
            setTimeout(() => { input.focus(); input.select(); }, 30);
        };
        const save = () => {
            if (!curBtn) return;
            const group = curBtn.dataset.group, idx = curBtn.dataset.idx;
            const val = input.value.replace(/\s+/g, ' ').trim();
            const hid = hiddenOf(curBtn);
            if (hid) hid.value = val;
            curBtn.textContent = val || '＃';
            curBtn.classList.toggle('has-tag', !!val);
            if (dateStr) saveField('meta', dateStr, group, val, { [group + idx]: val });
            close();
        };
        document.querySelectorAll('.gp-tag-btn').forEach((btn) => {
            btn.addEventListener('click', () => open(btn));
        });
        document.getElementById('tag-modal-save')?.addEventListener('click', save);
        document.getElementById('tag-modal-close')?.addEventListener('click', close);
        modal.querySelector('.rm-backdrop')?.addEventListener('click', close);
        input?.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); save(); }
            else if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
    }

    // ---- 화면 맨 위 구글 일정 배너 ---------------------------------------
    // 일정 칸의 목록은 감춰 두고, 그날 일정을 여기서 한 건씩 번갈아 보여 준다.
    // 제목이 길면 CSS(text-overflow)가 앞부분만 남기고 …로 자른다.
    const HERO_CAL_MS = 4000;
    let heroCalItems = [];
    let heroCalKey = '';
    let heroCalIdx = 0;
    let heroCalTimer = null;

    function paintHeroCal() {
        const box = document.getElementById('hero-cal');
        const it = heroCalItems[heroCalIdx];
        if (!box || !it) return;
        box.querySelector('.hero-cal-t').textContent = it.t;
        box.querySelector('.hero-cal-x').textContent = it.x;
        box.title = (it.t ? it.t + ' · ' : '') + it.x;
    }

    function startHeroCal() {
        const box = document.getElementById('hero-cal');
        if (!box) return;
        clearInterval(heroCalTimer);
        heroCalTimer = null;
        box.hidden = !heroCalItems.length;
        if (!heroCalItems.length) return;
        if (heroCalIdx >= heroCalItems.length) heroCalIdx = 0;
        paintHeroCal();
        if (heroCalItems.length < 2) return;
        heroCalTimer = setInterval(() => {
            if (document.hidden) return;
            heroCalIdx = (heroCalIdx + 1) % heroCalItems.length;
            // 글자를 먼저 갈아 끼우고 애니메이션은 덧입히기만 한다. 예전에는 먼저 흐리게 만든 뒤
            // setTimeout 으로 되살렸는데, 그 사이 탭이 뒤로 가면 타이머가 눌려 배너가 빈 채로 남았다.
            paintHeroCal();
            box.classList.remove('is-swap');
            void box.offsetWidth;          // 같은 애니메이션을 처음부터 다시 돌리기 위한 리플로우
            box.classList.add('is-swap');
        }, HERO_CAL_MS);
    }

    // 목록이 그대로면 돌아가던 순번을 건드리지 않는다(60초 폴링마다 처음으로 돌아가지 않게).
    function setHeroCalItems(items) {
        const key = JSON.stringify(items);
        if (key === heroCalKey) return;
        heroCalKey = key;
        heroCalItems = items;
        heroCalIdx = 0;
        startHeroCal();
    }

    function heroCalFromPage() {
        setHeroCalItems([...document.querySelectorAll('#agenda-events .agenda-row.event')]
            .map((r) => ({
                t: r.querySelector('.t')?.textContent || '',
                x: r.querySelector('.x')?.textContent || '',
            })));
    }

    // ---- 주간 띠의 Things3 할 일 칩 --------------------------------------
    function renderWkTasks(tasks) {
        const box = document.getElementById('wk-tasks');
        if (!box) return;
        box.textContent = '';
        tasks.forEach((t) => {
            // id 가 있으면 누를 때 Things3 가 그 할일을 연다(폰·맥 모두 같은 주소).
            const node = t.id ? el('a', 'wk-task', t.title) : el('span', 'wk-task', t.title);
            if (t.id) node.href = 'things:///show?id=' + encodeURIComponent(t.id);
            node.title = t.title;
            box.appendChild(node);
        });
        box.hidden = !tasks.length;
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
                (t.tags || []).forEach((tg) => row.appendChild(el('span', 'task-tag', tg)));
                if (t.overdue) row.appendChild(el('span', 'dl', '지남'));
                else if (t.deadline) row.appendChild(el('span', 'dl', '~' + t.deadline));
                taskBox.appendChild(row);
            });
            if (!tasks.length) taskBox.appendChild(el('div', 'ctx-empty agenda-empty', 'Things3 Today가 비어 있습니다.'));
        }
        // 감춘 목록에서 실제로 보이는 두 자리(맨 위 일정 배너·주간 띠 할 일)를 함께 갱신한다.
        setHeroCalItems(events.map((ev) => ({
            t: ev.all_day ? '종일' : (ev.start || ''), x: ev.title,
        })));
        renderWkTasks(tasks);
        setupAgendaMoreAll();
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
        renderTaskPops((data.tasks || []).map((t) => ({
            title: t.title,
            dl: t.overdue ? '지남' : (t.deadline ? '~' + t.deadline : ''),
        })));
    }

    // 블록마다 있는 '할 일' 팝오버는 맨 위 Things3 목록과 늘 같은 내용이다. 서버가 블록
    // 수만큼 같은 목록을 더 그려 보내지 않도록, 여기서 한 번에 채운다.
    // rows = [{title, dl}] · dl 은 '지남' 또는 '~마감'(없으면 빈 문자열).
    function renderTaskPops(rows) {
        document.querySelectorAll('.task-pop').forEach((box) => {
            box.textContent = '';
            rows.forEach((t) => {
                const row = el('div', 'pop-row task');
                row.appendChild(el('span', 'x', t.title));
                if (t.dl) row.appendChild(el('span', 'dl', t.dl));
                box.appendChild(row);
            });
            if (!rows.length) box.appendChild(el('div', 'pop-empty', 'Things3 Today 비어 있음'));
        });
        document.querySelectorAll('.task-count').forEach((c) => { c.textContent = rows.length; });
    }

    // 첫 화면용. 폴링이 처음 돌기 전까지는 서버가 그려 준 맨 위 목록이 유일한 자료다.
    function fillTaskPopsFromPage() {
        const rows = [...document.querySelectorAll('#agenda-tasks .agenda-row.task')].map((r) => ({
            title: r.querySelector('.x')?.textContent || '',
            dl: r.querySelector('.dl')?.textContent || '',
        }));
        renderTaskPops(rows);
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
                const cur = stack.querySelector('.block.is-focus');
                if (collapsed) {
                    scrollToEl(cur);
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
    // 설정 그룹 탭. 한 번에 한 그룹만 보여 스크롤을 줄인다(고른 탭은 주소 #에 남긴다).
    function bindSettingsTabs() {
        const nav = document.getElementById('set-tabs');
        if (!nav) return;
        const tabs = [...nav.querySelectorAll('button')];
        const show = (id) => {
            tabs.forEach((b) => {
                const on = b.dataset.tab === id;
                b.classList.toggle('is-active', on);
                document.getElementById(b.dataset.tab).hidden = !on;
            });
        };
        tabs.forEach((b) => b.addEventListener('click', () => {
            show(b.dataset.tab);
            history.replaceState(null, '', '#' + b.dataset.tab);
        }));
        const want = location.hash.slice(1);
        if (want && tabs.some((b) => b.dataset.tab === want)) show(want);
    }

    // 설정 탭 상태판. 무엇이 고장 났는지 로그를 뒤지지 않고 알 수 있게 한 자리에 모은다.
    // /api/health 는 구글 조회와 AppleScript 가 섞여 몇 초 걸려서, 화면이 뜬 뒤 따로 부른다.
    function bindStatusPanel() {
        const grid = document.getElementById('set-status-grid');
        if (!grid) return;

        // ok=true 초록, false 빨강, null 회색(설정 안 함 — 고장이 아니다)
        const row = (label, ok, text) => {
            const r = el('div', 'set-st-row' + (ok === true ? ' ok' : ok === false ? ' bad' : ' off'));
            r.appendChild(el('span', 'set-st-label', label));
            r.appendChild(el('span', 'set-st-val', text));
            return r;
        };
        const load = () => {
            grid.textContent = '';
            grid.appendChild(el('div', 'ctx-empty', '확인 중…'));
            fetch('/api/health', { cache: 'no-store' })
                .then((r) => r.json())
                .then((d) => {
                    grid.textContent = '';
                    const frag = document.createDocumentFragment();

                    // 구글 캘린더 읽기: 캘린더마다 한 줄
                    if (Array.isArray(d.gcal)) {
                        d.gcal.forEach((c) => frag.appendChild(row(
                            '캘린더 읽기 · ' + c.name,
                            !!c.reachable,
                            c.reachable ? c.vevents + '개 일정' : (c.reason || '연결 실패'),
                        )));
                    } else {
                        frag.appendChild(row('캘린더 읽기', null, d.gcal?.reason || '미설정'));
                    }
                    const link = (label, o, offText) => frag.appendChild(row(
                        label,
                        o.enabled ? true : (o.calendar || o.reason ? false : null),
                        o.enabled ? '연결됨' : (o.reason || offText),
                    ));
                    link('고결감 캘린더 쓰기', d.gcal_write, '미설정');
                    link('일정 캘린더 쓰기', d.events, '미설정');
                    link('성과 캘린더 쓰기', d.achieve, '미설정');
                    frag.appendChild(row('Things3', d.things?.ok === true,
                        d.things?.ok ? d.things.today + '개 Today' : (d.things?.reason || '연결 안 됨')));
                    frag.appendChild(row('AI', d.ai?.enabled ? true : null,
                        d.ai?.enabled ? d.ai.model : (d.ai?.has_key ? '주소·모델 필요' : '쓰지 않음')));

                    // 백업: 하루라도 밀리면 빨강(매일 23시에 돌아야 한다)
                    (d.backup || []).forEach((b) => frag.appendChild(row(
                        '백업 · ' + b.label,
                        b.ok ? (b.age !== null && b.age <= 1) : false,
                        b.ok ? `${b.name} · ${b.kb}KB · ${b.age === null ? '?' : b.age}일 전` : '없음',
                    )));

                    // 기록 신선도: 이틀 넘게 비면 기록이 끊긴 것이다.
                    // 앞날에 미리 적어 둔 기록이 있으면 경과일이 음수가 되므로 그때는 안 붙인다.
                    const rec = d.records || {};
                    const ago = (rec.age === null || rec.age < 0) ? ''
                        : (rec.age === 0 ? ' · 오늘' : ` · ${rec.age}일 전`);
                    frag.appendChild(row('마지막 기록', rec.age !== null && rec.age <= 1,
                        rec.last + ago));

                    // 오류: 로그 끝부분의 500 응답 수
                    const er = d.errors || {};
                    frag.appendChild(row('최근 오류', !er.count,
                        er.count ? `500 응답 ${er.count}건 · ${er.last || ''}` : '없음'));
                    frag.appendChild(row('서버 코드 버전', null, d.version || ''));
                    grid.appendChild(frag);
                })
                .catch(() => {
                    grid.textContent = '';
                    grid.appendChild(el('div', 'ctx-empty', '상태를 불러오지 못했습니다 · 서버 연결을 확인하세요'));
                });
        };
        document.getElementById('set-status-refresh')?.addEventListener('click', load);
        load();
    }

    // 구분 템플릿 격자(요일 7 × 코어블록 6 = 42칸)를 카드를 펼칠 때 그린다.
    // 서버에서 미리 그려 보내면 템플릿 하나당 40KB 넘게 붙어, 템플릿을 늘릴수록
    // 설정 화면이 계속 무거워졌다. 값은 window.__tpl* 에 JSON 으로만 실려 온다.
    function buildTemplateGrids() {
        const card = document.getElementById('set-tpl-card');
        if (!card || !window.__tplCats) return;
        const cats = window.__tplCats, blocks = window.__tplBlocks;
        const weekdays = window.__tplWeekdays, cells = window.__tplCells;
        let built = false;
        const build = () => {
            if (built) return;
            built = true;
            card.querySelectorAll('.set-tpl-grid').forEach((grid) => {
                const mine = cells[Number(grid.dataset.idx)] || {};
                const frag = document.createDocumentFragment();
                frag.appendChild(el('div', 'set-tpl-corner'));
                weekdays.forEach(([, label]) => frag.appendChild(el('div', 'set-tpl-blabel', label)));
                blocks.forEach((lbl) => {
                    frag.appendChild(el('div', 'set-tpl-daytype', lbl));
                    weekdays.forEach(([wd, wdLabel]) => {
                        const sel = el('select', 'set-tpl-cell cat-select');
                        sel.dataset.tpl = grid.dataset.tpl;
                        sel.dataset.weekday = wd;
                        sel.dataset.label = lbl;
                        sel.setAttribute('aria-label', wdLabel + ' ' + lbl + ' 구분');
                        // JSON 키는 문자열이라 요일도 문자열로 찾는다.
                        const cur = (mine[String(wd)] || {})[lbl];
                        sel.appendChild(new Option('—', ''));
                        cats.forEach((c) => {
                            const o = new Option(c.name, c.id, false, c.id === cur);
                            o.dataset.tone = c.tone;
                            sel.appendChild(o);
                        });
                        frag.appendChild(sel);
                    });
                });
                grid.appendChild(frag);
                // 화면이 뜰 때 도는 초기 색칠(select.cat-select)은 이미 지나간 뒤라 여기서 칠한다.
                grid.querySelectorAll('.set-tpl-cell').forEach(paintCategory);
            });
            card.querySelectorAll('.set-rt').forEach((box) => {
                const rules = (window.__tplRules || [])[Number(box.dataset.idx)] || [];
                const list = box.querySelector('.set-rt-list');
                rules.forEach((r) => list.appendChild(routineRow(r)));
            });
            card.querySelectorAll('.set-tpl').forEach(paintTplParts);
        };
        // 열 때 한 번만 그린다. 이미 열린 채로 들어왔으면(브라우저가 상태를 되살릴 때) 바로.
        card.addEventListener('toggle', () => { if (card.open) build(); });
        if (card.open) build();
        // 부분 탭(구분·세션시간·블록 이름·고정 할일). 누른 부분만 세운다.
        card.addEventListener('click', (e) => {
            const tab = e.target.closest('.set-tpl-tab');
            if (tab) {
                const tpl = tab.closest('.set-tpl');
                tpl.querySelectorAll('.set-tpl-tab').forEach(
                    (b) => b.classList.toggle('is-active', b === tab));
                let shown = null;
                tpl.querySelectorAll('.set-tpl-pane').forEach((pane) => {
                    pane.hidden = pane.dataset.part !== tab.dataset.part;
                    if (!pane.hidden) shown = pane;
                });
                showTplPane(shown);
                return;
            }
            // 칸 단위(B1p4) 격자 켜고 끄기. 처음 켤 때 그린다.
            const pm = e.target.closest('.set-tpl-pmode');
            if (!pm) return;
            const tpl = pm.closest('.set-tpl');
            const grid = tpl.querySelector('.set-tpl-grid');
            const pgrid = tpl.querySelector('.set-tpl-pgrid');
            const on = pgrid.hidden;
            if (on && !pgrid.dataset.built) {
                pgrid.dataset.built = '1';
                buildTplPGrid(pgrid);
            }
            pgrid.hidden = !on;
            grid.hidden = on;
            pm.classList.toggle('is-on', on);
            pm.setAttribute('aria-pressed', on ? 'true' : 'false');
        });

        // 칸이 나중에 생기므로 위임으로 받는다.
        card.addEventListener('change', (e) => {
            const sel = e.target.closest('.set-tpl-cell');
            if (sel) {
                paintCategory(sel);
                postForm('/settings/template/cell', {
                    template_id: sel.dataset.tpl, weekday: sel.dataset.weekday,
                    block_label: sel.dataset.label, category_id: sel.value,
                }).then(() => {
                    const idx = Number(sel.closest('.set-tpl').dataset.idx);
                    const cells = window.__tplCells[idx] = window.__tplCells[idx] || {};
                    cells[sel.dataset.weekday] = cells[sel.dataset.weekday] || {};
                    cells[sel.dataset.weekday][sel.dataset.label] = Number(sel.value) || null;
                    paintTplParts(sel.closest('.set-tpl'));
                    toast('저장');
                });
                return;
            }
            // 칸 단위 구분(B1p4). 적어 둔 칸만 블록 구분 위에 덮어쓴다.
            const pc = e.target.closest('.set-tpl-pcell');
            if (!pc) return;
            paintCategory(pc);
            postForm('/settings/template/slot-cell', {
                template_id: pc.dataset.tpl, weekday: pc.dataset.weekday,
                block_label: pc.dataset.label, p: pc.dataset.p, category_id: pc.value,
            }).then((d) => {
                if (!d || !d.ok) { toast('저장 실패'); return; }
                const idx = Number(pc.closest('.set-tpl').dataset.idx);
                const slots = window.__tplSlots[idx] = window.__tplSlots[idx] || {};
                const wd = slots[pc.dataset.weekday] = slots[pc.dataset.weekday] || {};
                const blk = wd[pc.dataset.label] = wd[pc.dataset.label] || {};
                if (pc.value) blk[pc.dataset.p] = Number(pc.value);
                else delete blk[pc.dataset.p];
                paintTplParts(pc.closest('.set-tpl'));
                toast('저장');
            });
        });

        // 고정 할일 규칙 줄: 값이 바뀌면 바로 저장, 요일 칩은 눌러서 켜고 끈다.
        card.addEventListener('change', (e) => {
            const row = e.target.closest('.set-rt-row');
            if (row && e.target.closest('.set-rt-time, .set-rt-span, .set-rt-cat')) {
                if (e.target.classList.contains('set-rt-cat')) paintCategory(e.target);
                saveRoutineRow(row);
            }
        });
        // 할 일 문구는 다른 칸과 같이 손을 떼면 저장하되, 타자가 멈춰도 한 번 저장한다
        // (칸을 벗어나지 않고 카드를 닫거나 탭을 옮겨도 글이 남게).
        let rtTimer = null;
        card.addEventListener('input', (e) => {
            const row = e.target.closest('.set-rt-row');
            if (!row || !e.target.classList.contains('set-rt-do')) return;
            clearTimeout(rtTimer);
            rtTimer = setTimeout(() => saveRoutineRow(row), 1200);
        });
        card.addEventListener('blur', (e) => {
            const row = e.target.closest('.set-rt-row');
            if (!row || !e.target.classList.contains('set-rt-do')) return;
            clearTimeout(rtTimer);
            saveRoutineRow(row);
        }, true);
        card.addEventListener('click', (e) => {
            const wd = e.target.closest('.set-rt-wd-btn');
            if (wd) {
                wd.classList.toggle('is-on');
                wd.setAttribute('aria-pressed', wd.classList.contains('is-on'));
                saveRoutineRow(wd.closest('.set-rt-row'));
                return;
            }
            const del = e.target.closest('.set-rt-del');
            if (del) {
                const row = del.closest('.set-rt-row');
                const box = del.closest('.set-rt');
                postForm('/settings/routine/delete', { id: row.dataset.id })
                    .then((d) => {
                        if (!d || !d.ok) return;
                        row.remove();
                        paintTplParts(box.closest('.set-tpl'));
                        toast('삭제');
                    });
                return;
            }
            const add = e.target.closest('.set-rt-add');
            if (!add) return;
            postForm('/settings/routine/add', { template_id: add.dataset.tpl })
                .then((d) => {
                    if (!d || !d.ok) { toast('추가 실패'); return; }
                    const box = add.closest('.set-rt');
                    const row = routineRow({
                        id: d.id, weekdays: '', span: 1, do_text: '',
                        start_time: (window.__tplTimes || [])[0] || '', category_id: null,
                    });
                    box.querySelector('.set-rt-list').appendChild(row);
                    paintTplParts(box.closest('.set-tpl'));
                    row.querySelector('.set-rt-do').focus();
                });
        });
    }

    // 고정 할일 규칙 한 줄(요일 칩 7개 · 시작시각 · 칸 수 · 할 일 · 구분 · 삭제)을 만든다.
    function routineRow(r) {
        const cats = window.__tplCats || [], times = window.__tplTimes || [];
        const weekdays = window.__tplWeekdays || [];
        const on = new Set(String(r.weekdays || '').split(',').filter((s) => s !== ''));
        const row = el('div', 'set-rt-row');
        row.dataset.id = r.id;
        const wdBox = el('div', 'set-rt-wd');
        weekdays.forEach(([wd, label]) => {
            const b = el('button', 'set-rt-wd-btn' + (on.has(String(wd)) ? ' is-on' : ''), label);
            b.type = 'button';
            b.dataset.wd = wd;
            b.setAttribute('aria-pressed', on.has(String(wd)));
            b.setAttribute('aria-label', label + '요일');
            wdBox.appendChild(b);
        });
        row.appendChild(wdBox);
        const time = el('select', 'set-rt-time');
        time.setAttribute('aria-label', '시작 시각');
        times.forEach((t) => time.appendChild(new Option(t, t, false, t === r.start_time)));
        row.appendChild(time);
        const span = el('select', 'set-rt-span');
        span.setAttribute('aria-label', '칸 수');
        [1, 2, 3, 4].forEach((n) => {
            span.appendChild(new Option(n + '칸', n, false, n === Number(r.span)));
        });
        row.appendChild(span);
        const doIn = el('input', 'set-rt-do');
        doIn.type = 'text';
        doIn.value = r.do_text || '';
        doIn.placeholder = '할 일 (예: 논문 읽기)';
        doIn.autocomplete = 'off';
        doIn.setAttribute('aria-label', '고정 할 일');
        row.appendChild(doIn);
        const cat = el('select', 'set-rt-cat cat-select');
        cat.setAttribute('aria-label', '구분');
        cat.appendChild(new Option('구분', ''));
        cats.forEach((c) => {
            const o = new Option(c.name, c.id, false, c.id === r.category_id);
            o.dataset.tone = c.tone;
            cat.appendChild(o);
        });
        row.appendChild(cat);
        paintCategory(cat);
        const del = el('button', 'set-mini-btn set-rt-del', '✕');
        del.type = 'button';
        del.title = '삭제';
        row.appendChild(del);
        return row;
    }

    function saveRoutineRow(row) {
        if (!row) return;
        const wds = [...row.querySelectorAll('.set-rt-wd-btn.is-on')]
            .map((b) => b.dataset.wd).join(',');
        postForm('/settings/routine/save', {
            id: row.dataset.id,
            weekdays: wds,
            start_time: row.querySelector('.set-rt-time').value,
            span: row.querySelector('.set-rt-span').value,
            do_text: row.querySelector('.set-rt-do').value,
            category_id: row.querySelector('.set-rt-cat').value,
        }).then((d) => toast((d && d.ok) ? '저장' : '저장 실패'));
    }

    // ---- 주간 템플릿의 부분들 ---------------------------------------------
    // 템플릿 하나가 네 부분(구분·세션시간·블록 이름·고정 할일)을 담고 각각 비울 수 있다.
    // 지금 보고 있는 부분만 화면에 세우고, 부분마다 처음 열 때 그린다.

    function tplHasTimes(idx) {
        const c = (window.__tplTimesCommon || [])[idx];
        const w = (window.__tplTimesWd || [])[idx] || {};
        return !!(c || Object.keys(w).length);
    }

    // 템플릿 idx 가 그 범위(''=공통, '0'~'6'=요일)에 쓸 8칸 시간.
    // 서버의 template_day_blocks 와 같은 규칙이다 — 세션시간을 안 담은 템플릿이면
    // 지금 설정값을 그대로 보여 주고, 담았으면 그 템플릿만으로 정한다.
    function tplScopeTimes(idx, key) {
        const base = window.__btScopes || {};
        if (!tplHasTimes(idx)) return base[key] || base[''] || [];
        const common = (window.__tplTimesCommon || [])[idx] || base[''] || [];
        if (!key) return common;
        return ((window.__tplTimesWd || [])[idx] || {})[key] || common;
    }

    // 그 요일 그 블록이 몇 칸(30분)인지. B1p4 의 p 가 어디까지 있는지를 정한다.
    function tplPCount(idx, wd, label) {
        const rows = tplScopeTimes(idx, String(wd));
        const i = (window.__btBlocks || []).findIndex((b) => b.label === label);
        if (i < 0 || !rows[i]) return 0;
        return Math.max(0, Math.round(
            (hhmmToMin(rows[i].end) - hhmmToMin(rows[i].start)) / 30));
    }

    // 머리줄에 이 템플릿이 담고 있는 부분을 적는다(담은 것만 진하게).
    function paintTplParts(tpl) {
        const box = tpl.querySelector('.set-tpl-parts');
        if (!box) return;
        const idx = Number(tpl.dataset.idx);
        const cells = (window.__tplCells || [])[idx] || {};
        const slots = (window.__tplSlots || [])[idx] || {};
        const names = (window.__tplNames || [])[idx] || {};
        // 고정 할일은 줄을 더하거나 지운 뒤에도 맞아야 하므로 화면의 줄을 센다.
        const rules = tpl.querySelectorAll('.set-rt-row').length;
        const hasCat = Object.keys(cells).some(
            (wd) => Object.keys(cells[wd]).some((k) => cells[wd][k])
        ) || Object.keys(slots).length > 0;
        box.textContent = '';
        [['구분', hasCat], ['세션시간', tplHasTimes(idx)],
         ['이름', Object.keys(names).length > 0], ['고정', rules > 0]
        ].forEach(([label, yes]) => {
            const s = el('span', 'set-tpl-part' + (yes ? ' is-on' : ''), label);
            s.title = yes ? label + ' 을(를) 담고 있습니다' : label + ' 은(는) 안 담았습니다';
            box.appendChild(s);
        });
    }

    // 부분을 처음 열 때 그린다. 이미 그렸으면 아무것도 하지 않는다.
    function showTplPane(pane) {
        if (!pane || pane.dataset.built) return;
        pane.dataset.built = '1';
        if (pane.dataset.part === 'times') buildTplTimes(pane);
        else if (pane.dataset.part === 'names') buildTplNames(pane);
    }

    // 세션시간 편집기. 설정 위쪽의 것과 같은 모양(공통 1벌 + 요일별 덮어쓰기)이지만
    // 저장은 이 템플릿 안으로만 간다. 클래스 이름을 set-tt-* 로 따로 두는 이유는,
    // bindBlockTimes 가 .set-bt-panel 을 문서 전체에서 찾아 물기 때문이다.
    function buildTplTimes(pane) {
        const idx = Number(pane.dataset.idx);
        const tid = pane.dataset.tpl;
        const blocks = window.__btBlocks || [];
        const scopes = [['', '공통']].concat(
            (window.__tplWeekdays || []).map(([wd, label]) => [String(wd), label]));

        const state = el('div', 'set-tt-state');
        const badge = el('span', 'set-bt-badge');
        const drop = el('button', 'set-mini-btn set-tt-drop', '세션시간 빼기');
        drop.type = 'button';
        drop.title = '이 템플릿에서 세션시간을 뺍니다(주간 탭에서 골라도 시간표를 안 건드립니다)';
        state.append(badge, drop);
        const paintBadge = () => {
            const on = tplHasTimes(idx);
            badge.classList.toggle('on', on);
            badge.textContent = on
                ? '이 템플릿이 세션시간을 담고 있습니다'
                : '아직 안 담았습니다 · 지금 설정값을 보여 줍니다. 값을 고치면 그때부터 담습니다';
            drop.hidden = !on;
        };
        paintBadge();

        const tabs = el('div', 'set-tt-tabs');
        pane.append(state, tabs);
        scopes.forEach(([key, label], i) => {
            const btn = el('button', 'set-tt-tab' + (i === 0 ? ' is-active' : ''), label);
            btn.type = 'button';
            btn.dataset.scope = key;
            if (key && ((window.__tplTimesWd || [])[idx] || {})[key]) btn.classList.add('is-over');
            tabs.appendChild(btn);

            const panel = el('div', 'set-tt-panel');
            panel.dataset.scope = key;
            panel.hidden = i !== 0;
            const box = el('div', 'set-blocktimes');
            const rows = tplScopeTimes(idx, key);
            blocks.forEach((bl, o) => {
                const row = el('div', 'set-bt-row');
                row.dataset.order = o;
                row.appendChild(el('span', 'set-bt-label' + (bl.is_core ? ' core' : ''), bl.label));
                const st = el('input', 'set-bt-start');
                st.type = 'time';
                st.step = '300';
                st.value = (rows[o] || {}).start || '';
                st.setAttribute('aria-label', bl.label + ' 시작');
                const en = el('input', 'set-bt-end');
                en.type = 'time';
                en.step = '300';
                en.value = (rows[o] || {}).end || '';
                en.setAttribute('aria-label', bl.label + ' 끝');
                row.append(st, el('span', 'set-bt-dash', '–'), en);
                box.appendChild(row);
            });
            const acts = el('div', 'set-bt-actions');
            const msg = el('span', 'set-bt-msg');
            acts.appendChild(msg);
            if (key) {
                const reset = el('button', 'ghost-btn set-tt-reset', '이 템플릿 공통으로');
                reset.type = 'button';
                acts.appendChild(reset);
                reset.addEventListener('click', () => {
                    postForm('/settings/template/times/clear', { template_id: tid, scope: key })
                        .then((d) => {
                            if (!d || !d.ok) { toast('되돌리지 못했습니다'); return; }
                            delete ((window.__tplTimesWd || [])[idx] || {})[key];
                            redrawTplTimes(pane);
                        });
                });
            }
            panel.append(box, acts);
            pane.appendChild(panel);

            const save = () => {
                msg.textContent = '';
                msg.classList.remove('bad');
                const data = { template_id: tid, scope: key };
                box.querySelectorAll('.set-bt-row').forEach((row) => {
                    const o = row.dataset.order;
                    data['start_' + o] = row.querySelector('.set-bt-start').value;
                    data['end_' + o] = row.querySelector('.set-bt-end').value;
                });
                postForm('/settings/template/times', data).then((d) => {
                    if (!d || !d.ok) {
                        msg.textContent = (d && d.error) || '저장 실패';
                        msg.classList.add('bad');
                        return;
                    }
                    const times = blocks.map((_b, o) => ({
                        start: data['start_' + o], end: data['end_' + o],
                    }));
                    if (key) {
                        window.__tplTimesWd[idx] = window.__tplTimesWd[idx] || {};
                        window.__tplTimesWd[idx][key] = times;
                        btn.classList.add('is-over');
                    } else {
                        window.__tplTimesCommon[idx] = times;
                    }
                    paintBadge();
                    paintTplParts(pane.closest('.set-tpl'));
                    resetTplPGrid(pane.closest('.set-tpl'));   // 칸 수가 달라졌다
                    autosaveToast();
                });
            };
            box.querySelectorAll('.set-bt-start, .set-bt-end').forEach(
                (inp) => inp.addEventListener('change', save));
        });

        drop.addEventListener('click', () => {
            if (!window.confirm('이 템플릿에서 세션시간을 뺍니다. 주간 탭에서 골라도 시간표는 그대로 둡니다.')) return;
            postForm('/settings/template/times/clear', { template_id: tid, scope: '' })
                .then((d) => {
                    if (!d || !d.ok) { toast('빼지 못했습니다'); return; }
                    window.__tplTimesCommon[idx] = null;
                    window.__tplTimesWd[idx] = {};
                    redrawTplTimes(pane);
                });
        });

        tabs.addEventListener('click', (e) => {
            const btn = e.target.closest('.set-tt-tab');
            if (!btn) return;
            tabs.querySelectorAll('.set-tt-tab').forEach(
                (b) => b.classList.toggle('is-active', b === btn));
            pane.querySelectorAll('.set-tt-panel').forEach((p) => {
                p.hidden = p.dataset.scope !== btn.dataset.scope;
            });
        });
    }

    function redrawTplTimes(pane) {
        const tpl = pane.closest('.set-tpl');
        pane.textContent = '';
        buildTplTimes(pane);
        paintTplParts(tpl);
        resetTplPGrid(tpl);
    }

    // 세션시간이 바뀌면 칸 단위 격자의 p 개수가 달라진다. 다음에 열 때 다시 그린다.
    function resetTplPGrid(tpl) {
        const grid = tpl && tpl.querySelector('.set-tpl-pgrid');
        if (!grid) return;
        const wasOpen = !grid.hidden;
        grid.textContent = '';
        delete grid.dataset.built;
        if (wasOpen) { grid.dataset.built = '1'; buildTplPGrid(grid); }
    }

    // 블록 이름 6칸(B1~B6). 주간 탭에서 고르면 그 주 블록 이름으로 그대로 들어간다.
    function buildTplNames(pane) {
        const idx = Number(pane.dataset.idx);
        const tid = pane.dataset.tpl;
        const names = (window.__tplNames || [])[idx] || {};
        const wrap = el('div', 'set-tpl-names');
        (window.__tplBlocks || []).forEach((lbl) => {
            const row = el('label', 'set-tpl-nrow');
            row.appendChild(el('span', 'set-bt-label core', lbl));
            const inp = el('input', 'set-tpl-bname');
            inp.type = 'text';
            inp.autocomplete = 'off';
            inp.value = names[lbl] || '';
            inp.placeholder = lbl + ' 이번 주 이름';
            inp.setAttribute('aria-label', lbl + ' 이름');
            const save = () => {
                const v = (inp.value || '').trim();
                if (v === (names[lbl] || '')) return;
                postForm('/settings/template/blockname',
                         { template_id: tid, block_label: lbl, name: v })
                    .then((d) => {
                        if (!d || !d.ok) { toast('저장 실패'); return; }
                        if (v) names[lbl] = v; else delete names[lbl];
                        window.__tplNames[idx] = names;
                        paintTplParts(pane.closest('.set-tpl'));
                        autosaveToast();
                    });
            };
            inp.addEventListener('change', save);
            inp.addEventListener('blur', save);
            row.appendChild(inp);
            wrap.appendChild(row);
        });
        pane.appendChild(wrap);
        pane.appendChild(el('p', 'set-events-hint',
            '주간 탭에서 이 템플릿을 고르면 그 주 블록 이름(B1~B6)에 그대로 들어갑니다. '
            + '비워 둔 칸은 건드리지 않습니다.'));
    }

    // 칸 단위 구분 격자. 줄이 B1p1~B6pN 이고 열이 요일이다. p 개수는 이 템플릿의
    // 세션시간을 따르므로, 요일마다 블록 길이가 다르면 없는 칸은 점으로 비워 둔다.
    function buildTplPGrid(grid) {
        const idx = Number(grid.dataset.idx);
        const tid = grid.dataset.tpl;
        const cats = window.__tplCats || [];
        const weekdays = window.__tplWeekdays || [];
        const mine = (window.__tplSlots || [])[idx] || {};
        const frag = document.createDocumentFragment();
        frag.appendChild(el('div', 'set-tpl-corner'));
        weekdays.forEach(([, label]) => frag.appendChild(el('div', 'set-tpl-blabel', label)));
        (window.__tplBlocks || []).forEach((lbl) => {
            const counts = weekdays.map(([wd]) => tplPCount(idx, wd, lbl));
            const maxP = counts.length ? Math.max.apply(null, counts) : 0;
            for (let p = 1; p <= maxP; p += 1) {
                frag.appendChild(el('div', 'set-tpl-daytype', lbl + 'p' + p));
                weekdays.forEach(([wd, wdLabel], i) => {
                    if (p > counts[i]) {
                        const gap = el('div', 'set-tpl-gap', '·');
                        gap.title = wdLabel + '요일 ' + lbl + ' 에는 이 칸이 없습니다';
                        frag.appendChild(gap);
                        return;
                    }
                    const sel = el('select', 'set-tpl-pcell cat-select');
                    sel.dataset.tpl = tid;
                    sel.dataset.weekday = wd;
                    sel.dataset.label = lbl;
                    sel.dataset.p = p;
                    sel.setAttribute('aria-label', wdLabel + ' ' + lbl + 'p' + p + ' 구분');
                    const cur = ((mine[String(wd)] || {})[lbl] || {})[String(p)];
                    sel.appendChild(new Option('—', ''));
                    cats.forEach((c) => {
                        const o = new Option(c.name, c.id, false, c.id === cur);
                        o.dataset.tone = c.tone;
                        sel.appendChild(o);
                    });
                    frag.appendChild(sel);
                });
            }
        });
        grid.appendChild(frag);
        grid.querySelectorAll('.set-tpl-pcell').forEach(paintCategory);
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

        // 동작 설정(그룹이 나뉘어 있어도 data-key를 가진 select·text면 모두 같은 방식으로 저장)
        document.querySelectorAll('select[data-key], input[data-key]').forEach((f) => {
            f.addEventListener('change', () => {
                const o = {}; o[f.dataset.key] = f.value;
                postForm('/settings/save', o).then(() => toast('설정 저장'));
            });
        });

        // 요일 컨셉 7칸: 한 칸을 고치면 7칸을 함께 보내 저장한다(오늘 탭 날짜 옆에 표시)
        const wdcBox = document.getElementById('set-wdc');
        if (wdcBox) {
            const msg = document.getElementById('set-wdc-msg');
            const saveWdc = () => {
                const data = {};
                wdcBox.querySelectorAll('.set-wdc-input').forEach((inp) => {
                    data['wd' + inp.dataset.wd] = inp.value;
                });
                postForm('/settings/weekday-concepts', data).then((d) => {
                    if (msg) {
                        msg.textContent = (d && d.ok) ? '저장됨' : '저장 실패';
                        setTimeout(() => { msg.textContent = ''; }, 1500);
                    }
                });
            };
            wdcBox.querySelectorAll('.set-wdc-input').forEach((inp) => {
                inp.addEventListener('change', saveWdc);
                inp.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) {
                        e.preventDefault();
                        inp.blur();
                    }
                });
            });
        }

        // 구분 템플릿: 추가·이름변경·삭제·셀(평일/주말×블록) 저장
        document.getElementById('set-tpl-add-btn')?.addEventListener('click', () => {
            const inp = document.getElementById('set-tpl-new-name');
            const name = (inp.value || '').trim();
            if (!name) { toast('이름을 입력하세요'); return; }
            postForm('/settings/template/add', { name: name })
                .then((d) => { if (d && d.ok) location.reload(); else toast('추가 실패'); });
        });
        document.querySelectorAll('.set-tpl-name').forEach((inp) => {
            inp.addEventListener('change', () => {
                const v = (inp.value || '').trim();
                if (!v) return;
                postForm('/settings/template/rename', { id: inp.dataset.id, name: v })
                    .then(() => toast('이름 저장'));
            });
        });
        document.querySelectorAll('.set-tpl-del').forEach((b) => {
            b.addEventListener('click', () => {
                if (!confirm('이 템플릿을 삭제할까요?')) return;
                postForm('/settings/template/delete', { id: b.dataset.id })
                    .then((d) => { if (d && d.ok) location.reload(); });
            });
        });
        buildTemplateGrids();
        bindStatusPanel();

        // 서버(launchd) 재시작: 요청 후 서버가 다시 올라오면 자동 새로고침
        const restartBtn = document.getElementById('set-restart-btn');
        const restartMsg = document.getElementById('set-restart-msg');
        const waitServerUp = () => {
            let tries = 0;
            const ping = () => {
                tries += 1;
                fetch('/static/manifest.json', { cache: 'no-store' })
                    .then((r) => { if (r.ok) location.reload(); else throw 0; })
                    .catch(() => { if (tries < 30) setTimeout(ping, 1000); else location.reload(); });
            };
            setTimeout(ping, 2500);
        };
        restartBtn?.addEventListener('click', () => {
            if (!confirm('서버를 재시작할까요? 몇 초간 화면이 잠시 끊긴 뒤 자동으로 새로고침됩니다.')) return;
            restartBtn.disabled = true;
            if (restartMsg) restartMsg.textContent = '재시작 요청 중…';
            postForm('/settings/restart', {})
                .then((d) => {
                    if (d && d.ok) {
                        if (restartMsg) restartMsg.textContent = '재시작 중… 서버가 올라오면 자동 새로고침';
                        waitServerUp();
                    } else {
                        restartBtn.disabled = false;
                        if (restartMsg) restartMsg.textContent = '재시작 실패' + (d && d.error ? ' · ' + d.error : '');
                    }
                })
                .catch(() => {
                    // 요청 도중 서버가 이미 내려갔을 수 있다. 올라오면 새로고침.
                    if (restartMsg) restartMsg.textContent = '재시작 중… 서버가 올라오면 자동 새로고침';
                    waitServerUp();
                });
        });

        // .env 편집: 저장 후 서버 재시작(값은 재시작해야 반영됨)
        const envSaveBtn = document.getElementById('set-env-save');
        const envText = document.getElementById('set-env-text');
        const envMsg = document.getElementById('set-env-msg');
        envSaveBtn?.addEventListener('click', () => {
            if (!confirm('.env를 저장하고 서버를 재시작할까요? 몇 초간 화면이 끊긴 뒤 자동 새로고침됩니다.')) return;
            envSaveBtn.disabled = true;
            if (envMsg) envMsg.textContent = '저장 중…';
            postForm('/settings/env/save', { content: envText ? envText.value : '' })
                .then((d) => {
                    if (!d || !d.ok) {
                        envSaveBtn.disabled = false;
                        if (envMsg) envMsg.textContent = '저장 실패' + (d && d.error ? ' · ' + d.error : '');
                        return null;
                    }
                    if (envMsg) envMsg.textContent = '저장됨 · 재시작 중… 서버가 올라오면 자동 새로고침';
                    return postForm('/settings/restart', {}).then(() => waitServerUp());
                })
                .catch(() => {
                    if (envMsg) envMsg.textContent = '재시작 중… 서버가 올라오면 자동 새로고침';
                    waitServerUp();
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

        // 성과 캘린더 쓰기: 캘린더 ID 자동 저장 + 연결 테스트
        const achCal = document.getElementById('set-achieve-cal');
        const achStatus = document.getElementById('set-achieve-status');
        const setAchStatus = (on) => {
            if (!achStatus) return;
            achStatus.textContent = on ? '켜짐 · 연결됨' : '꺼짐 · ID 입력 필요';
            achStatus.classList.toggle('ok', !!on);
            achStatus.classList.toggle('bad', !on);
        };
        achCal?.addEventListener('change', () => {
            postForm('/settings/achieve-calendar', { value: achCal.value.trim() }).then((d) => {
                if (!d) return;
                setAchStatus(d.enabled);
                toast('성과 캘린더 ID 저장');
            });
        });
        document.getElementById('set-achieve-test')?.addEventListener('click', (e) => {
            const btn = e.currentTarget; btn.disabled = true;
            postForm('/settings/achieve-calendar/test', {}).then((d) => {
                btn.disabled = false;
                if (d && d.ok) toast(d.warn || '연결 OK · 테스트 이벤트 생성/삭제 성공');
                else toast((d && d.error) || '연결 실패');
            });
        });

        // AI 연결: base URL·모델 저장 + 연결 테스트 (키는 .env)
        const aiBase = document.getElementById('set-ai-base');
        const aiModel = document.getElementById('set-ai-model');
        const aiStatus = document.getElementById('set-ai-status');
        const setAiStatus = (st) => {
            if (!aiStatus || !st) return;
            aiStatus.textContent = st.enabled ? '켜짐 · 연결됨'
                : (st.has_key ? '주소·모델 입력 필요' : '꺼짐 · .env AI_API_KEY 필요');
            aiStatus.classList.toggle('ok', !!st.enabled);
            aiStatus.classList.toggle('bad', !st.enabled);
        };
        const saveAi = () => postForm('/settings/ai/save', {
            base_url: (aiBase?.value || '').trim(), model: (aiModel?.value || '').trim(),
        }).then((d) => { if (d) { setAiStatus(d.status); toast('AI 설정 저장'); } });
        aiBase?.addEventListener('change', saveAi);
        aiModel?.addEventListener('change', saveAi);
        document.getElementById('set-ai-test')?.addEventListener('click', (e) => {
            const btn = e.currentTarget; btn.disabled = true;
            postForm('/settings/ai/test', {}).then((d) => {
                btn.disabled = false;
                toast(d && d.ok ? ('연결 OK · ' + (d.reply || '')) : ((d && d.error) || '연결 실패'));
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

    // ---- 설정: 세션(블록) 시간 편집 (공통 + 요일 탭, 8칸 묶음 검증 → 변경 즉시 자동저장) ----
    function bindBlockTimes() {
        const tabs = document.getElementById('set-bt-tabs');
        if (!tabs) return;
        const panels = document.querySelectorAll('.set-bt-panel');

        // 탭 전환: 공통 / 월~일 중 한 범위만 보여준다(값은 서버가 이미 전부 그려 두었다).
        tabs.querySelectorAll('.set-bt-tab').forEach((btn) => {
            btn.addEventListener('click', () => {
                tabs.querySelectorAll('.set-bt-tab').forEach((b) =>
                    b.classList.toggle('is-active', b === btn));
                panels.forEach((p) => { p.hidden = p.dataset.scope !== btn.dataset.scope; });
            });
        });

        panels.forEach((panel) => {
            const scope = panel.dataset.scope;
            const box = panel.querySelector('.set-blocktimes');
            const msg = panel.querySelector('.set-bt-msg');
            const collect = () => {
                const data = { scope: scope };
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
                        if (ok && d.ok) {
                            autosaveToast();
                            // 요일을 고치면 그 요일은 이제 공통과 무관하게 따로 관리된다.
                            if (scope) {
                                const badge = panel.querySelector('.set-bt-badge');
                                if (badge) {
                                    badge.classList.add('on');
                                    badge.textContent = badge.textContent.split(' ')[0] + ' 따로 지정됨';
                                }
                                tabs.querySelector('.set-bt-tab[data-scope="' + scope + '"]')
                                    ?.classList.add('is-over');
                            }
                        } else if (msg) {
                            msg.textContent = (d && d.error) || '저장 실패';
                            msg.classList.add('bad');
                        }
                    })
                    .catch(() => {
                        if (msg) { msg.textContent = '연결이 필요합니다'; msg.classList.add('bad'); }
                    });
            };
            box.querySelectorAll('.set-bt-start, .set-bt-end').forEach((inp) =>
                inp.addEventListener('change', save));
            panel.querySelector('.set-bt-reset')?.addEventListener('click', () => {
                const q = scope ? '이 요일의 시간을 지우고 공통을 따르게 합니다.'
                                : '공통 블록 시간을 기본값으로 되돌립니다.';
                if (!window.confirm(q)) return;
                postForm('/settings/blocktimes/reset', { scope: scope })
                    .then((d) => { if (d && d.ok) location.reload(); });
            });
        });
    }

    // ---- 장기플랜 (/plan) ------------------------------------------------
    // 상위 기간 막대 안에 하위 기간 막대가 겹쳐 그려진다. 어느 막대든 누르면 그 줄 아래
    // 편집칸이 열린다. 추가·수정·삭제 후에는 상위 기간이 서버에서 다시 계산되므로
    // 화면을 새로 그린다(reload).
    // ---- 장기: 다시 그려도 보던 자리를 지킨다 ------------------------------
    // 항목을 더하거나 고치면 화면을 통째로 다시 그리는데(location.reload), 그러면 맨 위로
    // 올라가 방금 적던 블록 줄을 다시 찾아 내려가야 했다. 떠나기 직전 자리를 적어 두었다가
    // 돌아왔을 때 되돌린다. 가로 자리는 같은 기간으로 돌아왔을 때만 뜻이 있다.
    const PLAN_SCROLL_KEY = 'plan-scroll';

    function savePlanScroll() {
        const box = document.querySelector('.plan-scroll');
        try {
            sessionStorage.setItem(PLAN_SCROLL_KEY, JSON.stringify({
                y: window.scrollY, x: box ? box.scrollLeft : 0, q: location.search,
            }));
            // 브라우저가 제 나름대로 기억해 둔 자리를 나중에 덮어쓰지 못하게 잠근다.
            // 안 그러면 우리가 되돌린 뒤에 브라우저 것이 한 번 더 들어와 자리가 어긋난다.
            if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
        } catch (e) { /* 저장 못 해도 나머지는 그대로 돈다 */ }
    }

    function restorePlanScroll() {
        let saved = null;
        try {
            saved = JSON.parse(sessionStorage.getItem(PLAN_SCROLL_KEY) || 'null');
            sessionStorage.removeItem(PLAN_SCROLL_KEY);   // 한 번 쓰고 버린다
        } catch (e) { return; }
        if (!saved) return;
        const box = document.querySelector('.plan-scroll');
        const put = () => {
            if (box && saved.q === location.search) box.scrollLeft = saved.x || 0;
            window.scrollTo(0, saved.y || 0);
        };
        // 격자를 다 그린 뒤라야 세로 길이가 정해져 원하는 만큼 내려간다. 몇 번 더 짚는다.
        put();
        requestAnimationFrame(put);
        setTimeout(put, 200);
        // 다 끝나면 브라우저에게 자리 기억을 도로 맡긴다(뒤로가기는 원래대로).
        setTimeout(() => {
            put();
            if ('scrollRestoration' in history) history.scrollRestoration = 'auto';
        }, 600);
    }

    // 부분 새로고침 뒤에 다시 열어 둘 것. 새 격자에는 새 bindGantt 가 붙으므로,
    // 여기 적어 두었다가 그 안에서 꺼내 연다(연달아 넣기·순서 바꾸기가 끊기지 않게).
    let pendingForm = null;     // 추가 폼 {block, area, parent, title, start, end}
    let pendingEdit = null;     // 편집칸 {id, block}
    let ganttBusy = false;

    // 화면 전체(location.reload) 대신 간트 부분만 새로 받아 갈아끼운다. 스크롤 자리와
    // 열어 둔 칸이 그대로 남아, 항목을 연달아 넣거나 순서를 잇달아 바꿀 수 있다.
    function refreshGantt(focusId, after) {
        const box = document.querySelector('.plan-scroll');
        const u = new URL(location.href);
        if (focusId) u.searchParams.set('focus', focusId);
        else u.searchParams.delete('focus');
        if (!box || ganttBusy) { savePlanScroll(); location.href = u.toString(); return; }
        ganttBusy = true;
        fetch(u.toString(), { credentials: 'same-origin', cache: 'no-store' })
            .then((r) => r.text())
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html')
                    .querySelector('.plan-scroll');
                if (!fresh) throw new Error('no-gantt');
                const x = box.scrollLeft;
                box.replaceWith(fresh);
                fresh.scrollLeft = x;
                // 서버가 방금 옮긴 항목을 따라 보는 기간을 바꿨으면 주소도 그 기간으로 맞춘다
                const anchor = fresh.querySelector('.gantt')?.dataset.anchor;
                if (anchor) u.searchParams.set('anchor', anchor);
                u.searchParams.delete('focus');
                history.replaceState(null, '', u.pathname + u.search);
                ganttBusy = false;
                bindGantt(true);
                bindDateParts();        // 새로 온 날짜 칸도 한 칸 입력으로 감싼다
                if (after) after();
            })
            .catch(() => {
                ganttBusy = false;
                savePlanScroll();       // 못 받아 왔으면 통째로 다시 그리되 자리는 지킨다
                location.href = u.toString();
            });
    }

    // 그 막대가 속한 계획 묶음 전체(뿌리와 그 아래 전부)의 항목 id 들. 형제까지 함께
    // 밝혀야 '이것들이 한 계획'이라는 것이 보인다.
    function ganttFamily(gantt, bar) {
        const kids = new Map();
        gantt.querySelectorAll('.gt-bar').forEach((b) => {
            const p = b.dataset.parent || '';
            if (!kids.has(p)) kids.set(p, []);
            kids.get(p).push(b);
        });
        const out = new Set([bar.dataset.root]);
        const stack = [bar.dataset.root];
        while (stack.length) {
            (kids.get(stack.pop()) || []).forEach((b) => {
                if (out.has(b.dataset.id)) return;
                out.add(b.dataset.id);
                stack.push(b.dataset.id);
            });
        }
        return out;
    }

    // 한 막대에 손을 얹으면 그 묶음만 남기고 나머지를 흐린다. 상하위가 색 진하기로만
    // 구분돼 어디에 붙은 것인지 읽기 어려웠던 것을, 짚어 볼 수 있게 한 것이다.
    function litGanttFamily(gantt, bar) {
        gantt.querySelectorAll('.is-lit').forEach((el) => el.classList.remove('is-lit'));
        const ids = bar ? ganttFamily(gantt, bar) : null;
        if (!ids || ids.size < 2) { gantt.classList.remove('is-lit'); return; }
        gantt.classList.add('is-lit');
        gantt.querySelectorAll('.gt-bar').forEach((b) => {
            if (ids.has(b.dataset.id)) b.classList.add('is-lit');
        });
        gantt.querySelectorAll('.gt-links [data-from]').forEach((p) => {
            if (ids.has(p.dataset.from) && ids.has(p.dataset.to)) p.classList.add('is-lit');
        });
    }

    function bindGantt(again) {
        const gantt = document.querySelector('.gantt');
        if (!gantt) return;
        // 위쪽 체크박스로 켜 둔 상태를 새 격자에도 옮긴다(부분 새로고침에서 잃지 않게)
        gantt.classList.toggle('show-today',
                               !!document.getElementById('pg-today-line')?.checked);
        gantt.classList.toggle('hide-past',
                               !!document.getElementById('pg-past-hide')?.checked);

        // 자동저장이 막대 자리를 바꿔 놓았으면 그 항목 id 를 여기 적어 둔다. 칸마다
        // 다시 그리면 치는 중에 초점이 튀므로, 편집칸을 닫을 때 한 번만 그린다.
        let dirtyRedraw = null;
        const flushRedraw = () => {
            if (!dirtyRedraw) return false;
            const id = dirtyRedraw;
            dirtyRedraw = null;
            refreshGantt(id);
            return true;
        };
        const closeAll = () => {
            gantt.querySelectorAll('.gt-edit, .gt-form').forEach((el) => { el.hidden = true; });
            gantt.querySelectorAll('.gt-bar.is-editing')
                 .forEach((b) => b.classList.remove('is-editing'));
            litGanttFamily(gantt, null);
            flushRedraw();
        };
        // 격자의 빈 곳을 누르면 닫는다. 입력칸 안이라도 칸·버튼이 아닌 빈 자리를 누르면
        // 마찬가지로 닫는다(입력칸 자신이나 줄 묶음이 눌린 경우).
        gantt.addEventListener('click', (e) => {
            const box = e.target.closest('.gt-edit, .gt-form');
            if (box) {
                if (e.target === box || e.target.classList.contains('gt-e-row')) closeAll();
                return;
            }
            if (e.target.closest('.gt-bar, .gt-add')) return;
            closeAll();
        });
        // 막대에 손을 얹으면 그 묶음을 밝힌다. 편집칸을 열어 둔 동안에는 그쪽을 그대로 둔다.
        gantt.addEventListener('mouseover', (e) => {
            if (gantt.querySelector('.gt-bar.is-dragging, .gt-bar.is-resizing')) return;
            const b = e.target.closest('.gt-bar');
            if (b) litGanttFamily(gantt, b);
            else if (!gantt.querySelector('.gt-bar.is-editing')) litGanttFamily(gantt, null);
        });

        // 항목 추가 폼 열기(블록 줄마다 하나). 하위 추가면 상위 항목·영역·기간을 물려받는다.
        const openForm = (opt) => {
            const o = opt || {};
            if (dirtyRedraw) { pendingForm = o; flushRedraw(); return; }
            closeAll();
            const form = gantt.querySelector('.gt-form[data-block="' + (o.block || '') + '"]');
            if (!form) return;
            form.hidden = false;
            const area = form.querySelector('.gt-f-area');
            if (area && o.area) area.value = o.area;
            form.querySelector('.gt-f-parent').value = o.parent || '';
            const label = form.querySelector('.gt-f-parent-label');
            if (label) label.textContent = o.parent ? ('하위 · ' + o.title) : '';
            [['start', '.gt-f-start'], ['end', '.gt-f-end']].forEach(([k, sel]) => {
                if (!o[k]) return;
                const el = form.querySelector(sel);
                el.value = o[k];
                syncDateParts(el);      // 코드가 넣은 값을 한 칸 입력에도 반영
            });
            const t = form.querySelector('.gt-f-title');
            t.value = '';
            t.focus();
            form.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        };

        gantt.querySelectorAll('.gt-add').forEach((btn) =>
            btn.addEventListener('click', () => openForm({ block: btn.dataset.block })));

        // 빈 자리를 두 번 누르면 그 날짜로 시작하는 1주짜리 항목을 그 블록 줄에 만든다
        const spanStart = gantt.dataset.start;
        const spanDays = parseInt(gantt.dataset.days, 10) || 1;
        gantt.querySelectorAll('.gt-blockrow .gt-track').forEach((track) => {
            track.addEventListener('dblclick', (e) => {
                if (e.target.closest('.gt-bar') || !spanStart) return;
                const r = track.getBoundingClientRect();
                if (!r.width) return;
                const off = Math.max(0, Math.min(spanDays - 1,
                    Math.floor((e.clientX - r.left) / r.width * spanDays)));
                const s = new Date(spanStart + 'T00:00:00');
                s.setDate(s.getDate() + off);
                openForm({ block: track.closest('.gt-blockrow').dataset.block,
                           start: ymd(s), end: ymd(addSpan(s, 'week')) });
            });
        });

        gantt.querySelectorAll('.gt-form').forEach((form) => {
            let sending = false;    // 보내는 중 다시 누르면 같은 항목이 2개 저장된다
            const submit = () => {
                if (sending) return;
                const title = (form.querySelector('.gt-f-title').value || '').trim();
                const start = form.querySelector('.gt-f-start').value;
                const end = form.querySelector('.gt-f-end').value;
                if (!title) { toast('항목 이름을 입력하세요'); return; }
                if (!start || !end) { toast('시작일과 종료일을 고르세요'); return; }
                sending = true;
                const parent = form.querySelector('.gt-f-parent').value;
                const area = form.querySelector('.gt-f-area').value;
                const ptext = form.querySelector('.gt-f-parent-label')?.textContent || '';
                postForm('/plan/item/add', {
                    area_id: area,
                    block: form.dataset.block,
                    title: title, start: start, end: end,
                    parent_id: parent,
                }).then((d) => {
                    if (d && d.ok) {
                        // 같은 자리에 폼을 그대로 다시 열어 연달아 적을 수 있게 한다
                        pendingForm = {
                            block: form.dataset.block, area: area, parent: parent,
                            title: ptext.replace(/^하위 · /, ''), start: start, end: end,
                        };
                        refreshGantt(d.id);
                        return;
                    }
                    sending = false;
                    toast((d && d.error) || '추가 실패');
                });
            };
            form.querySelector('.gt-f-save')?.addEventListener('click', submit);
            // 한글 IME 조합 Enter(229/isComposing)는 무시해 2회 추가를 막는다.
            form.querySelector('.gt-f-title')?.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) {
                    e.preventDefault();
                    submit();
                }
            });
            form.querySelector('.gt-f-cancel')?.addEventListener('click', () => { form.hidden = true; });
            // 길이 버튼: 어느 화면 단위에서든 1주·1개월·1분기·1년짜리 막대를 바로 만든다.
            form.querySelectorAll('.gt-f-len-btn').forEach((b) => {
                b.addEventListener('click', () => {
                    const si = form.querySelector('.gt-f-start');
                    const ei = form.querySelector('.gt-f-end');
                    const start = si.value ? new Date(si.value + 'T00:00:00') : new Date();
                    if (!si.value) si.value = ymd(start);
                    ei.value = ymd(addSpan(start, b.dataset.len));
                    syncDateParts(si);      // 코드가 넣은 값을 연·월·일 3칸에도 반영
                    syncDateParts(ei);
                });
            });
        });

        // 막대를 누르면 그 줄의 편집칸이 열린다. 어느 막대의 것인지 알 수 있게 막대에
        // 테두리를 두르고 그 묶음을 밝힌 채로 둔다(편집칸이 줄 아래라 멀어 보이던 것).
        // 편집칸은 <template> 안에 들어 있다(plan.html 참고). 처음 누를 때 그 자리에
        // 세우고 그때 손잡이를 붙인다. 막대 하나마다 입력칸 여섯 줄이 딸려 있어서,
        // 미리 다 세워 두면 열지도 않은 칸 수십 개가 배치·스타일 계산을 받는다.
        const editBoxFor = (bar) => {
            const group = bar.closest('.gt-group');
            if (!group) return null;
            const sel = '[data-id="' + bar.dataset.id + '"]';
            const live = group.querySelector('.gt-edit' + sel);
            if (live) return live;
            const tpl = group.querySelector('template.gt-edit-tpl' + sel);
            if (!tpl) return null;
            const box = tpl.content.firstElementChild.cloneNode(true);
            tpl.replaceWith(box);          // 템플릿이 있던 자리에 그대로 세운다(줄 순서 유지)
            bindEditBox(box);
            bindDateParts();               // 새로 선 날짜 칸도 한 칸 입력으로 감싼다
            return box;
        };

        const openEdit = (bar) => {
            const box = editBoxFor(bar);
            if (!box) return;
            const wasOpen = !box.hidden;
            if (dirtyRedraw) {
                pendingEdit = wasOpen ? null
                    : { id: bar.dataset.id, block: bar.closest('.gt-group')?.dataset.block };
                flushRedraw();
                return;
            }
            closeAll();
            box.hidden = wasOpen;
            if (wasOpen) return;
            bar.classList.add('is-editing');
            litGanttFamily(gantt, bar);
            box.querySelector('.gt-e-title')?.focus();
            box.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        };
        gantt.querySelectorAll('.gt-bar').forEach((bar) => {
            bar.addEventListener('click', () => {
                if (bar.dataset.dragged === '1') { delete bar.dataset.dragged; return; }
                openEdit(bar);      // 한 항목이 여러 줄에 나오므로 누른 줄의 것만 연다
            });
        });

        const removeItem = (id, hasKids) => {
            const msg = hasKids
                ? '이 항목을 삭제합니다. 하위 항목도 함께 지워집니다.'
                : '이 항목을 삭제합니다.';
            if (!window.confirm(msg)) return;
            postForm('/plan/item/delete', { id: id }).then((d) => {
                if (d && d.ok) refreshGantt();
                else toast('삭제 실패');
            });
        };
        // 막대 위 ✕: 편집창을 열지 않고 바로 지운다(하위가 있으면 함께 지워진다고 알린다)
        gantt.querySelectorAll('.gt-del').forEach((x) => {
            x.addEventListener('pointerdown', (e) => e.stopPropagation());
            x.addEventListener('click', (e) => {
                e.stopPropagation();
                removeItem(x.dataset.id,
                           !!x.closest('.gt-bar')?.classList.contains('is-parent'));
            });
        });

        // 같은 줄에서 위(아래)로 이웃한 '다른 묶음'의 막대. 세로 순서를 바꿀 상대다.
        const neighbourBar = (bar, dir) => {
            const row = bar.closest('.gt-blockrow');
            if (!row) return null;
            const me = bar.getBoundingClientRect();
            let best = null;
            let bestD = Infinity;
            row.querySelectorAll('.gt-bar').forEach((o) => {
                // 순서는 같은 영역 안에서만 매긴다(영역끼리의 위아래는 영역 관리가 정한다)
                if (o === bar || o.dataset.root === bar.dataset.root
                    || o.dataset.area !== bar.dataset.area || !o.offsetParent) return;
                const r = o.getBoundingClientRect();
                const dy = dir === 'up' ? me.top - r.top : r.top - me.top;
                if (dy <= 1) return;                    // 같은 칸이거나 반대쪽
                // 가장 가까운 칸을 먼저, 그 칸 안에서는 가로로 가장 가까운 것을 고른다
                const d = dy * 10000 + Math.abs((r.left + r.width / 2) - (me.left + me.width / 2));
                if (d < bestD) { bestD = d; best = o; }
            });
            return best;
        };
        const moveOrder = (box, dir) => {
            const bar = box.closest('.gt-group')
                ?.querySelector('.gt-bar[data-id="' + box.dataset.id + '"]');
            const peer = bar && neighbourBar(bar, dir);
            if (!peer) {
                toast(dir === 'up' ? '위에 바꿀 항목이 없습니다' : '아래에 바꿀 항목이 없습니다');
                return;
            }
            postForm('/plan/item/order', {
                id: box.dataset.id, peer: peer.dataset.id,
                place: dir === 'up' ? 'before' : 'after',
            }).then((d) => {
                if (!d || !d.ok) { toast((d && d.error) || '순서를 바꾸지 못했습니다'); return; }
                // 잇달아 누를 수 있게 편집칸을 그대로 다시 연다
                pendingEdit = { id: box.dataset.id, block: box.dataset.block };
                refreshGantt(box.dataset.id);
            });
        };

        bindGanttDrag(gantt);
        drawGanttLinks(gantt);
        if (!again) {
            window.addEventListener('resize', () => {
                const g = document.querySelector('.gantt');
                if (g) drawGanttLinks(g);
            });
            restorePlanScroll();   // 다른 화면에서 돌아온 것이면 보던 자리로
        }

        // 그 항목의 막대들(한 항목이 여러 블록 줄에 나올 수 있다)
        const barsOf = (id) => gantt.querySelectorAll('.gt-bar[data-id="' + id + '"]');

        // 편집칸의 한 칸만 보내 저장한다. redraw 를 주면 막대 자리가 달라지는 것이므로
        // 다시 그릴 것으로 적어 둔다(그리기는 편집칸을 닫을 때 한 번).
        function saveItemField(id, data, redraw) {
            const body = { id: id };
            Object.keys(data).forEach((k) => { body[k] = data[k]; });
            postForm('/plan/item/update', body).then((d) => {
                if (!d || !d.ok) { toast((d && d.error) || '저장 실패'); return; }
                autosaveToast();
                if (redraw) dirtyRedraw = id;
            });
        }

        function bindEditBox(box) {
            const id = box.dataset.id;
            // ---- 자동저장: 칸에서 손을 떼면 그 칸 하나가 바로 저장된다 -------
            // 저장 버튼은 그대로 두어 '저장하고 닫기'로 남는다.
            const title = box.querySelector('.gt-e-title');
            if (title) {
                let timer = null;
                let last = title.value;
                const flushTitle = () => {
                    if (timer) { clearTimeout(timer); timer = null; }
                    const v = (title.value || '').trim();
                    // 빈 이름은 서버가 안 받는다(이름 없는 막대가 생기면 누를 수가 없다).
                    if (!v || v === last) return;
                    last = v;
                    barsOf(id).forEach((b) => {
                        b.dataset.title = v;
                        const t = b.querySelector('.gt-bartext');
                        if (t) t.textContent = v;
                    });
                    saveItemField(id, { title: v });
                };
                title.addEventListener('change', flushTitle);
                title.addEventListener('blur', flushTitle);
                title.addEventListener('input', () => {
                    if (timer) clearTimeout(timer);
                    timer = setTimeout(flushTitle, 1200);
                });
            }
            [['.gt-e-start', 'start'], ['.gt-e-end', 'end']].forEach(([sel, key]) => {
                const el = box.querySelector(sel);
                el?.addEventListener('change', () => {
                    // 덜 친 날짜는 한 칸 입력이 빈 값으로 내려보낸다. 그때는 그냥 둔다.
                    if (!el.value) return;
                    const one = {};
                    one[key] = el.value;
                    saveItemField(id, one, true);
                });
            });
            const prog = box.querySelector('.gt-e-progress');
            prog?.addEventListener('change', () => {
                if (prog.disabled || prog.value === '') return;
                const v = Math.max(0, Math.min(100, Number(prog.value) || 0));
                barsOf(id).forEach((b) => {
                    const f = b.querySelector('.gt-fill');
                    if (f) f.style.width = v + '%';
                    const pc = b.querySelector('.gt-barpct');
                    if (pc) pc.textContent = v;
                });
                // 상위 항목의 진척률이 하위 평균으로 따라 바뀌므로 다시 그린다
                saveItemField(id, { progress: String(v) }, true);
            });
            box.querySelectorAll('.gt-e-blocks input').forEach((c) => {
                c.addEventListener('change', () => {
                    const v = [...box.querySelectorAll('.gt-e-blocks input:checked')]
                        .map((x) => x.value).join(',');
                    saveItemField(id, { block: v }, true);
                });
            });
            box.querySelector('.gt-e-hidden')?.addEventListener('change', (e) => {
                saveItemField(id, { hidden: e.target.checked ? '1' : '0' }, true);
            });
            box.querySelector('.gt-e-masked')?.addEventListener('change', (e) => {
                saveItemField(id, { masked: e.target.checked ? '1' : '0' }, true);
            });

            box.querySelector('.gt-e-save')?.addEventListener('click', () => {
                const data = { id: id, title: (box.querySelector('.gt-e-title').value || '').trim() };
                const s = box.querySelector('.gt-e-start');
                const e = box.querySelector('.gt-e-end');
                const p = box.querySelector('.gt-e-progress');
                data.start = s.value;
                data.end = e.value;
                // 블록은 여러 개 고를 수 있다(여러 블록에서 동시 진행). 하나도 없으면 미지정.
                data.block = [...box.querySelectorAll('.gt-e-blocks input:checked')]
                    .map((c) => c.value).join(',');
                data.hidden = box.querySelector('.gt-e-hidden').checked ? '1' : '0';
                // 가리기는 주간·오늘에서만 뺀다(간트에는 그대로 남는다).
                data.masked = box.querySelector('.gt-e-masked').checked ? '1' : '0';
                if (!p.disabled) data.progress = p.value;   // 하위가 있으면 진척률은 하위 평균
                box.hidden = true;          // 누르는 즉시 닫아 준다(새로 그리기 전에)
                dirtyRedraw = null;         // 이 저장이 어차피 다시 그린다
                postForm('/plan/item/update', data).then((d) => {
                    if (!d || !d.ok) { box.hidden = false; toast((d && d.error) || '저장 실패'); return; }
                    refreshGantt(id);
                });
            });
            // 영역(막대 색)을 바꾼다. 하위 항목이면 상위에서 빠져 그 영역의 최상위가 된다.
            box.querySelector('.gt-e-area')?.addEventListener('change', (ev) => {
                postForm('/plan/item/reparent', { id: id, area_id: ev.target.value })
                    .then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '영역을 바꾸지 못했습니다');
                    });
            });
            box.querySelector('.gt-e-up')?.addEventListener('click', () => moveOrder(box, 'up'));
            box.querySelector('.gt-e-down')?.addEventListener('click', () => moveOrder(box, 'down'));
            // 상위에서 떼기. 예전에는 막대를 아래로 끄는 몸짓이었는데, 그 자리를 순서
            // 바꾸기가 쓰게 되어 버튼으로 옮겼다(실수로 떨어지던 일도 함께 사라진다).
            box.querySelector('.gt-e-detach')?.addEventListener('click', () => {
                postForm('/plan/item/reparent', { id: id, area_id: box.dataset.area })
                    .then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '떼어내지 못했습니다');
                    });
            });
            box.querySelector('.gt-e-del')?.addEventListener('click', () => {
                const bar = box.closest('.gt-group')
                    ?.querySelector('.gt-bar[data-id="' + id + '"]');
                removeItem(id, !!bar?.classList.contains('is-parent'));
            });
            box.querySelector('.gt-e-close')?.addEventListener('click', closeAll);
            // 하위 추가. 상위의 기간을 그대로 물려받아 날짜를 다시 고르지 않아도 되게 한다.
            box.querySelector('.gt-e-child')?.addEventListener('click', (ev) => {
                const b = ev.currentTarget;
                openForm({ block: b.dataset.block, area: b.dataset.area,
                           parent: b.dataset.parent, title: b.dataset.title,
                           start: b.dataset.start, end: b.dataset.end });
            });
        }
        // 이미 서 있는 편집칸(부분 새로고침 전에 열어 두었던 것)에도 붙인다.
        gantt.querySelectorAll('.gt-edit').forEach(bindEditBox);

        // 부분 새로고침 전에 열려 있던 칸을 그대로 다시 연다(연달아 넣기·순서 바꾸기)
        if (pendingForm) {
            const p = pendingForm;
            pendingForm = null;
            openForm(p);
        } else if (pendingEdit) {
            const p = pendingEdit;
            pendingEdit = null;
            const bar = gantt.querySelector('.gt-group[data-block="' + p.block + '"] '
                                            + '.gt-bar[data-id="' + p.id + '"]');
            if (bar) openEdit(bar);
        }

        if (!again) {
            // 지난 항목(종료일이 오늘 이전) 접기·펴기. 새로고침하면 다시 펴진 상태로 돌아간다.
            // 오늘 세로선은 기본으로 안 보이고, 체크할 때만 긋는다(늘 켜 두면 너무 튄다).
            // 격자는 부분 새로고침으로 갈릴 수 있어, 누를 때마다 지금 것을 다시 찾는다.
            document.getElementById('pg-today-line')?.addEventListener('change', (e) => {
                document.querySelector('.gantt')?.classList.toggle('show-today', e.target.checked);
            });
            // 숨긴 항목은 서버에서 아예 빼고 그리므로 화면을 다시 불러 온다(칸 배치가 달라진다).
            document.getElementById('pg-show-hidden')?.addEventListener('change', (e) => {
                const u = e.target.dataset.url;
                savePlanScroll();       // 통째로 다시 그리므로 보던 자리를 적어 둔다
                location.href = u + (e.target.checked ? '&show_hidden=1' : '');
            });
            const pastBox = document.getElementById('pg-past-hide');
            pastBox?.addEventListener('change', () => {
                const g = document.querySelector('.gantt');
                if (!g) return;
                g.classList.toggle('hide-past', pastBox.checked);
                drawGanttLinks(g);         // 막대가 사라지면 연결선도 다시 긋는다
            });
        }

        // 방금 옮긴 막대가 있으면 그 자리로 스크롤해 놓친 것처럼 보이지 않게 한다
        const found = gantt.querySelector('.gt-bar.is-focus');
        if (found) {
            found.scrollIntoView({ block: 'nearest', inline: 'center' });
            return;
        }
        if (again) return;      // 아래 가로 스크롤은 화면을 처음 열 때만

        // 현재 기간 열이 화면에 들어오도록 가로 스크롤(항목 이름 열에 가리지 않게)
        const nowCol = gantt.querySelector('.gt-col.is-now');
        const scroller = gantt.closest('.plan-scroll');
        if (nowCol && scroller) {
            const delta = nowCol.getBoundingClientRect().left
                        - scroller.getBoundingClientRect().left - 12;
            if (delta > 0) scroller.scrollLeft += delta;
        }
    }

    // ---- 주간 블록 이름 ----------------------------------------------------
    // 주간 이름을 고치면 아래 7일 격자에서 아직 안 채운 칸의 안내글도 따라 바뀐다
    // (그 칸이 비면 주간 이름을 따르므로, 다르게 갈 날만 채우면 된다).
    function bindThemeWeekdays() {
        document.querySelectorAll('.theme-item').forEach((item) => {
            const weekly = item.querySelector('input[name^="theme_"]');
            const label = item.dataset.label;
            weekly?.addEventListener('input', () => {
                const ph = weekly.value.trim() || '이름';
                document.querySelectorAll('.mini-block').forEach((mb) => {
                    if (mb.querySelector('.mini-head strong')?.textContent.trim() !== label) return;
                    const el = mb.querySelector('.mini-name');
                    if (el) el.placeholder = ph;
                });
            });
        });
    }

    // ---- 물음표 도움말 -----------------------------------------------------
    // 제목 옆 작은 ? 를 누르면 그 자리에서 설명이 뜬다. 설명 글은 data-hint 에만 두어
    // 평소 화면에는 글자가 늘지 않는다.
    function bindHints() {
        let pop = null;
        const close = () => { if (pop) { pop.remove(); pop = null; } };
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.hint');
            if (!btn) { close(); return; }
            const was = pop && pop.dataset.for === btn.dataset.hint;
            close();
            if (was) return;
            pop = document.createElement('span');
            pop.className = 'hint-pop';
            pop.dataset.for = btn.dataset.hint;
            pop.textContent = btn.dataset.hint;
            btn.parentNode.insertBefore(pop, btn.nextSibling);
        });
    }

    // ---- 이번 주 계획 연결(🔗) ---------------------------------------------
    // 블록 이름·DO·목표 앞의 작은 버튼. 누르면 그 주 할 일 목록이 열리고 고른 키만
    // 숨은 칸에 담긴다(글은 각자 직접 쓴다). 블록은 여러 개를 쉼표로 잇는다.
    function bindWkLinks() {
        const todos = window.__wkTodos || [];
        const boxes = document.querySelectorAll('.wl');
        if (!boxes.length || !todos.length) return;
        const labelOf = (key) => {
            const hit = todos.find((t) => t.key === key);
            return hit ? hit.label : key;
        };
        let openBox = null;
        const closePop = () => {
            if (!openBox) return;
            openBox.querySelector('.wl-pop')?.remove();
            openBox = null;
        };
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.wl')) closePop();
        });

        boxes.forEach((box) => {
            const hidden = box.querySelector('input[type="hidden"]');
            const btn = box.querySelector('.wl-btn');
            const multi = box.dataset.multi === '1';
            const keys = () => (hidden.value || '').split(',').filter(Boolean);

            const paint = () => {
                const ks = keys();
                box.classList.toggle('is-linked', ks.length > 0);
                btn.textContent = ks.length > 1 ? '🔗' + ks.length : '🔗';
                btn.title = ks.length ? ks.map(labelOf).join(' · ') : '이번 주 계획 연결';
            };
            paint();

            const save = () => {
                const m = /^wt([bs])_(\d+)$/.exec(hidden.name);
                if (m) {
                    saveField(m[1] === 'b' ? 'block' : 'slot', m[2], 'wk_todo', hidden.value);
                    return;
                }
                const g = /^goallink([123])$/.exec(hidden.name);
                if (!g) return;
                const form = document.querySelector('form.day-form');
                const extra = {};
                document.querySelectorAll('input[name^="goallink"]').forEach((el) => {
                    extra[el.name] = el.value;
                });
                saveField('meta', form.dataset.date, 'goallink' + g[1], hidden.value, extra);
            };

            btn.addEventListener('click', () => {
                if (openBox === box) { closePop(); return; }
                closePop();
                const pop = document.createElement('div');
                pop.className = 'wl-pop';
                const cur = keys();
                if (!multi) {
                    pop.append(mkOpt('', '연결 안 함', !cur.length, false));
                }
                // 장기 탭에서 이 블록에 배정해 둔 계획을 맨 위로 올린다. 나머지도 구분선
                // 아래에 그대로 남겨, 급히 다른 블록 일을 잡는 날도 연결할 수 있게 한다.
                const mine = box.dataset.block || '';
                const here = [];
                const rest = [];
                todos.forEach((t) => {
                    const on = mine && (t.blocks || '').split(',').indexOf(mine) >= 0;
                    (on ? here : rest).push(t);
                });
                here.forEach((t) => {
                    pop.append(mkOpt(t.key, t.label, cur.indexOf(t.key) >= 0, multi, t.blocks));
                });
                if (here.length && rest.length) {
                    const sep = document.createElement('div');
                    sep.className = 'wl-sep';
                    sep.textContent = '다른 블록';
                    pop.append(sep);
                }
                rest.forEach((t) => {
                    pop.append(mkOpt(t.key, t.label, cur.indexOf(t.key) >= 0, multi, t.blocks));
                });
                pop.addEventListener('change', (e) => {
                    const inp = e.target;
                    if (multi) {
                        const set = keys().filter((k) => k !== inp.value);
                        if (inp.checked) set.push(inp.value);
                        hidden.value = set.join(',');
                    } else {
                        hidden.value = inp.value;
                        closePop();
                    }
                    paint();
                    save();
                });
                box.append(pop);
                openBox = box;
            });
        });

        function mkOpt(value, label, checked, multi, blocks) {
            const l = document.createElement('label');
            l.className = 'wl-opt';
            const i = document.createElement('input');
            i.type = multi ? 'checkbox' : 'radio';
            i.value = value;
            i.checked = checked;
            if (!multi) i.name = 'wl-pick';
            l.append(i, document.createTextNode(label));
            if (blocks) {       // 장기 탭에서 정한 블록(여러 개면 다 적는다)
                const chip = document.createElement('span');
                chip.className = 'wl-blk';
                chip.textContent = blocks;
                l.append(chip);
            }
            return l;
        }
    }

    // ---- 날짜 입력: 한 칸에 연월일을 이어서 --------------------------------
    // 브라우저 기본 date 입력은 연도가 4자리를 넘겨도 월로 넘어가지 않고 칸도 넓다.
    // 그래서 화면에는 칸 하나만 두고 숫자를 치는 대로 20260727 → 2026-07-27 로 끊어 준다.
    // 원래 date 입력은 값 보관·달력 버튼 용도로 옆에 남긴다(id·name·class 가 그대로라
    // 기존 코드가 안 깨진다).
    function bindDateParts() {
        document.querySelectorAll('input[type="date"]').forEach(wrapDateInput);
    }

    // 숫자만 남겨 8자리까지 받고 YYYY-MM-DD 모양으로 끊는다.
    function dateMask(s) {
        const n = (s || '').replace(/\D/g, '').slice(0, 8);
        if (n.length <= 4) return n;
        if (n.length <= 6) return n.slice(0, 4) + '-' + n.slice(4);
        return n.slice(0, 4) + '-' + n.slice(4, 6) + '-' + n.slice(6);
    }

    function wrapDateInput(native) {
        if (native.dataset.dp) return;
        native.dataset.dp = '1';
        const box = document.createElement('span');
        box.className = 'dp';
        const text = document.createElement('input');
        text.type = 'text';
        text.className = 'dp-text';
        text.inputMode = 'numeric';
        text.autocomplete = 'off';
        text.maxLength = 10;
        text.placeholder = 'YYYY-MM-DD';
        text.setAttribute('aria-label', native.getAttribute('aria-label') || '날짜');
        if (native.disabled) text.disabled = true;
        native.parentNode.insertBefore(box, native);
        box.append(text, native);

        // 한 칸 -> date 입력. 8자리를 다 쳐야 값이 선다(덜 쳤으면 빈 값).
        // syncing 은 아래 pull 이 제 손으로 낸 change 를 되받지 않게 막는 빗장이다.
        // 이게 없으면 첫 글자를 칠 때 값이 서 있던 칸이 빈 값으로 바뀌며 change 가 나고,
        // pull 이 그 빈 값을 한 칸에 도로 써서 방금 친 글자가 사라진다.
        let syncing = false;
        const push = () => {
            const v = text.value.length === 10 ? text.value : '';
            if (native.value === v) return;
            syncing = true;
            native.value = v;
            native.dispatchEvent(new Event('input', { bubbles: true }));
            native.dispatchEvent(new Event('change', { bubbles: true }));
            syncing = false;
        };
        // date 입력 -> 한 칸. 달력으로 고르거나 코드가 값을 넣었을 때 되돌려 받는다.
        const pull = () => { if (syncing) return; text.value = native.value || ''; };
        pull();
        native.addEventListener('change', pull);
        native.dpSync = pull;

        text.addEventListener('input', () => {
            const masked = dateMask(text.value);
            if (text.value !== masked) text.value = masked;
            push();
        });
        text.addEventListener('blur', () => {
            // 다 못 채웠으면 지운다(반쪽 날짜를 남겨 두지 않는다).
            if (text.value.length !== 10) { text.value = native.value || ''; }
        });
    }
    // 코드가 date 입력 값을 직접 바꾼 뒤 화면 칸을 맞춘다(장기 탭 기간 길이 버튼 등).
    function syncDateParts(el) {
        if (el && el.dpSync) el.dpSync();
    }

    // 'YYYY-MM-DD' 문자열(로컬 기준)
    function ymd(d) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    // 시작일에서 그 길이만큼 뒤의 '종료일'(마지막 날). 1개월=한 달 뒤 하루 전.
    function addSpan(start, len) {
        const d = new Date(start.getTime());
        if (len === 'week') { d.setDate(d.getDate() + 6); return d; }
        const months = len === 'year' ? 12 : (len === 'quarter' ? 3 : 1);
        const day = d.getDate();
        d.setDate(1);
        d.setMonth(d.getMonth() + months);
        // 그 달에 없는 날(1/31 + 1개월)은 말일로 맞춘다
        const last = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
        d.setDate(Math.min(day, last));
        d.setDate(d.getDate() - 1);
        return d;
    }

    // ---- 장기: 막대 끌어 옮기기 ------------------------------------------
    // 옮긴 뒤에는 그 항목을 짚어 다시 그린다. 끌어서 과거로 보내면 '지난 항목'이 되어 접히고,
    // 보이는 기간 밖으로 보내면 아예 안 그려져 막대가 사라진 것처럼 보이기 때문이다.
    // 서버가 focus 를 보고 필요하면 그 항목이 보이는 기간으로 화면을 옮기고 접힘도 풀어 준다.
    // 막대끼리의 관계를 격자 위에 겹친 SVG 한 장에 그린다. 두 가지다.
    //  1) 상위–하위: 옅은 실선 갈고리(상위 왼쪽에서 내려와 하위 왼쪽으로 붙는다)
    //  2) 한 항목이 여러 블록에 걸린 것: 최상위 항목만 옅은 점선 곡선으로 잇는다
    // 자리 계산이 필요해 그린 뒤에 재며, 폭이 바뀌거나 막대가 나타나고 사라지면 다시 그린다.
    const SVG_NS = 'http://www.w3.org/2000/svg';

    function drawGanttLinks(gantt) {
        gantt.querySelector('.gt-links')?.remove();
        const vis = [...gantt.querySelectorAll('.gt-bar')].filter((b) => b.offsetParent);
        if (!vis.length) return;
        const base = gantt.getBoundingClientRect();
        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('class', 'gt-links');
        svg.setAttribute('width', gantt.scrollWidth);
        svg.setAttribute('height', gantt.scrollHeight);
        const box = (el) => {
            const r = el.getBoundingClientRect();
            return { l: r.left - base.left, w: r.width,
                     t: r.top - base.top, b: r.bottom - base.top,
                     cy: r.top - base.top + r.height / 2 };
        };
        // 선·점에 양 끝 항목 id 를 적어 둔다. 한 묶음을 밝힐 때 그 선만 골라 켜려는 것이다.
        const tint = (el, from, ids) => {
            const st = getComputedStyle(from);
            el.style.setProperty('--gt-tone', st.getPropertyValue('--gt-tone'));
            el.style.setProperty('--gt-hue', st.getPropertyValue('--gt-hue'));
            el.dataset.from = ids[0];
            el.dataset.to = ids[1];
            svg.appendChild(el);
        };
        const draw = (d, cls, from, ids) => {
            const p = document.createElementNS(SVG_NS, 'path');
            p.setAttribute('d', d);
            p.setAttribute('class', cls);
            tint(p, from, ids);
        };
        const dot = (x, y, from, ids) => {
            const c = document.createElementNS(SVG_NS, 'circle');
            c.setAttribute('cx', x);
            c.setAttribute('cy', y);
            c.setAttribute('r', 3);
            c.setAttribute('class', 'gt-linkdot');
            tint(c, from, ids);
        };

        const byId = new Map();
        vis.forEach((b) => {
            if (!byId.has(b.dataset.id)) byId.set(b.dataset.id, []);
            byId.get(b.dataset.id).push(b);
        });

        // 1) 상위 → 하위. 하위 막대 한가운데에서 위로 올라가 상위에 붙는다.
        // 상위가 그 가로 위치를 안 덮으면 상위 쪽 끝까지만 꺾어 붙인다.
        vis.forEach((b) => {
            const kin = byId.get(b.dataset.parent);
            if (!kin) return;
            const row = b.closest('.gt-blockrow');
            const up = kin.find((p) => p.closest('.gt-blockrow') === row) || kin[0];
            const pb = box(up);
            const cb = box(b);
            const cx = cb.l + cb.w / 2;
            const px = Math.max(pb.l, Math.min(cx, pb.l + pb.w));
            const ids = [b.dataset.parent, b.dataset.id];
            draw(`M ${cx} ${cb.cy} V ${pb.cy} H ${px}`, 'gt-link', b, ids);
            dot(px, pb.cy, b, ids);      // 상위 쪽 끝에 점을 찍어 어디에 붙는지 보이게
        });

        // 2) 같은 항목이 여러 블록에 걸린 것. 최상위만 이어 화면이 복잡해지지 않게 한다.
        // 막대 앞이 아니라 뒤(오른쪽 끝)에서 바깥으로 부풀려 글자를 가리지 않는다.
        byId.forEach((bars) => {
            if (bars.length < 2 || bars[0].dataset.level !== '0') return;
            const sorted = bars.slice().sort((a, c) => box(a).t - box(c).t);
            for (let i = 0; i + 1 < sorted.length; i++) {
                const a = box(sorted[i]);
                const c = box(sorted[i + 1]);
                const x = a.l + a.w - Math.min(14, a.w / 2);
                // 멀수록 더 부풀려 언제나 곡선으로 보이게 하되, 오른쪽 끝을 넘지 않게 줄인다
                const bow = Math.min(Math.max(18, Math.min(46, (c.t - a.b) * 0.32)),
                                     Math.max(8, gantt.scrollWidth - x - 4));
                draw(`M ${x} ${a.b} C ${x + bow} ${a.b + 8}, ${x + bow} ${c.t - 8}, ${x} ${c.t}`,
                     'gt-span-link', sorted[i],
                     [sorted[i].dataset.id, sorted[i].dataset.id]);
            }
        });
        gantt.appendChild(svg);
    }

    // 좌우로 끌면 끈 픽셀을 그대로 날짜로 바꿔 그만큼 기간이 옮겨지고(열 단위로 튀지 않는다),
    // 다른 막대 위에 놓으면 그 막대의 하위계획이 된다. 다른 블록(B1~B6·미지정) 줄에 놓으면
    // 잡은 줄에서 빠져 놓은 줄로 옮겨간다. 마우스·터치 모두 같은 포인터 이벤트로 처리한다.
    function bindGanttDrag(gantt) {
        // 보이는 기간 전체 날수. 트랙 폭을 이걸로 나누면 1px이 며칠인지 나온다.
        const spanDays = parseInt(gantt.dataset.days, 10) || 1;
        const daysPerPx = (track) => {
            const w = track ? track.getBoundingClientRect().width : 0;
            return w ? spanDays / w : 0;
        };
        let drag = null;

        const clearMarks = () => {
            gantt.querySelectorAll('.is-drop-target').forEach(
                (el) => el.classList.remove('is-drop-target'));
        };
        const EDGE = 16;        // 양 끝에서 이 폭 안을 잡으면 기간 조절(좁으면 자꾸 이동이 된다)
        const MIN_RESIZABLE = 14;   // 이보다 좁은 막대는 통째 이동만(끝을 잡을 자리가 없다)
        const reset = () => {
            if (drag) {
                clearTimeout(drag.holdT);
                tip.hidden = true;
                line.hidden = true;
                drag.bar.classList.remove('is-dragging', 'is-resizing',
                                          'is-nesting', 'is-reordering');
                drag.bar.style.transform = '';
                drag.bar.style.left = drag.css.left;
                drag.bar.style.width = drag.css.width;
            }
            clearMarks();
            drag = null;
        };
        const HOLD = 350;   // 다른 막대 위에서 이만큼 멈춰 있으면 '하위로 넣기'로 잡는다
        const REARM = 25;   // 그만큼 움직이면 멈춤 판정을 처음부터 다시 센다
        const LANE = 15;    // 이만큼 세로로(가로보다 많이) 움직이면 순서 바꾸기로 본다
        const DAY = 86400000;
        const iso = (s) => new Date(s + 'T00:00:00');
        // 분기·연 화면에서는 하루가 1px도 안 돼 손끝으로 겨눌 수 없다. 그 두 화면에서만
        // 주 경계에 붙이고, 주·월 화면에서는 끈 그대로 하루 단위로 옮긴다.
        const snapWeek = gantt.dataset.level === 'year' || gantt.dataset.level === 'quarter';
        // 그 날짜에서 가장 가까운 '그 요일'로 옮긴다(0=월 … 6=일)
        const toWd = (d, want) => {
            const wd = (d.getDay() + 6) % 7;
            const fwd = (want - wd + 7) % 7;
            const back = (wd - want + 7) % 7;
            const out = new Date(d);
            out.setDate(out.getDate() + (fwd <= back ? fwd : -back));
            return out;
        };
        // 끈 픽셀을 날수로. 주 단위로 붙이는 화면에서는 옮긴 자리에서 시작은 월요일,
        // 종료는 일요일에 맞춘다(7일씩 끊는 게 아니라 주의 경계에 붙는다).
        const dragDays = (dx) => {
            const edge = drag.edge === 'end' ? 'end' : 'start';
            const src = drag.bar.dataset[edge];
            if (!src) return 0;
            const raw = Math.round(dx * daysPerPx(drag.track));
            if (!snapWeek) return raw;
            const from = iso(src);
            const moved = new Date(from);      // 자정끼리 재야 시각 잔여분에 하루가 안 밀린다
            moved.setDate(moved.getDate() + raw);
            return Math.round((toWd(moved, edge === 'end' ? 6 : 0) - from) / DAY);
        };
        // 그 날수를 다시 픽셀로. 끄는 동안 막대를 이 값만큼만 움직여야 "보이는 자리 = 놓일 자리"다.
        const snapPx = (dx) => {
            const dpp = daysPerPx(drag.track);
            return dpp ? dragDays(dx) / dpp : 0;
        };
        // 끄는 동안 바뀔 기간을 막대 위에 띄운다(몇 주 움직였고 며칠~며칠이 되는지)
        const tip = document.createElement('span');
        tip.className = 'gt-tip';
        tip.hidden = true;
        gantt.appendChild(tip);
        const WD = ['월', '화', '수', '목', '금', '토', '일'];
        const at = (src, add) => {
            const d = iso(src);
            d.setDate(d.getDate() + add);
            return d;
        };
        const md = (d, yr) => (yr ? String(d.getFullYear()).slice(2) + '.' : '')
            + (d.getMonth() + 1) + '/' + d.getDate() + '(' + WD[(d.getDay() + 6) % 7] + ')';
        const showTip = (days, px) => {
            const s = drag.bar.dataset.start;
            const e = drag.bar.dataset.end;
            if (!s || !e) return;
            const a = at(s, drag.edge === 'end' ? 0 : days);
            const b = at(e, drag.edge === 'start' ? 0 : days);
            const yr = a.getFullYear() !== b.getFullYear();   // 해를 넘기면 연도도 적는다
            tip.textContent = md(a, yr) + '~' + md(b, yr);
            const base = gantt.getBoundingClientRect();
            const top = drag.px.top - base.top;
            tip.style.left = Math.max(0, drag.px.left - base.left + px) + 'px';
            // 위로 띄우면 머리글에 가리는 자리에서는 막대 아래로 내린다
            tip.style.top = (top < 30 ? top + drag.bar.offsetHeight + 4 : top - 22) + 'px';
            tip.hidden = false;
        };
        // 놓일 자리를 미리 보여 주는 가로선(순서 바꾸기)
        const line = document.createElement('span');
        line.className = 'gt-dropline';
        line.hidden = true;
        gantt.appendChild(line);
        const showLine = (peer) => {
            if (!peer) { line.hidden = true; return; }
            const base = gantt.getBoundingClientRect();
            const r = peer.el.getBoundingClientRect();
            const track = drag.track.getBoundingClientRect();
            line.style.left = (track.left - base.left) + 'px';
            line.style.width = track.width + 'px';
            line.style.top = ((peer.place === 'before' ? r.top - 3 : r.bottom + 1)
                              - base.top) + 'px';
            line.hidden = false;
        };
        // 세로로 끌어 순서를 바꾸려는 것인가. 같은 줄 안에서 세로로 뚜렷하게 움직였을 때만
        // 본다(가로로 미는 김에 조금 흔들린 것까지 순서 바꾸기로 잡으면 날짜가 안 옮겨진다).
        const reorderPeerAt = (x, y) => {
            if (!drag.row) return null;
            const dx = x - drag.x0;
            const dy = y - drag.y0;
            if (Math.abs(dy) < LANE || Math.abs(dy) <= Math.abs(dx)) return null;
            const u = under(x, y);
            if (u.row && u.row !== drag.row) return null;   // 다른 줄이면 블록 이동이다
            let best = null;
            let bestD = Infinity;
            drag.row.querySelectorAll('.gt-bar').forEach((o) => {
                if (o === drag.bar || o.dataset.root === drag.bar.dataset.root
                    || o.dataset.area !== drag.bar.dataset.area || !o.offsetParent) return;
                const r = o.getBoundingClientRect();
                const cy = r.top + r.height / 2;
                // 가장 가까운 칸을 먼저, 그 칸 안에서는 가로로 가까운 막대를 고른다
                const d = Math.abs(cy - y) * 10000 + Math.abs((r.left + r.width / 2) - x);
                if (d < bestD) { bestD = d; best = { el: o, cy: cy }; }
            });
            if (!best) return null;
            return { el: best.el, id: best.el.dataset.id,
                     place: y < best.cy ? 'before' : 'after' };
        };
        // 포인터 아래에 무엇이 있는지(끌고 있는 막대는 잠시 통과시켜 밑을 본다)
        const under = (x, y) => {
            drag.bar.style.pointerEvents = 'none';
            const el = document.elementFromPoint(x, y);
            drag.bar.style.pointerEvents = '';
            if (!el) return {};
            const b = el.closest('.gt-bar');
            return { bar: b && b !== drag.bar ? b : null, row: el.closest('.gt-blockrow') };
        };
        // 놓을 곳. 멈춰서 겨눈 막대가 있으면 그 하위로, 아니면 다른 줄로 넘어갔을 때만
        // 붙이기·블록 이동이다. 가로로 미는 동안 밑에 깔린 막대에 빨려 들어가지 않게 한다.
        const dropTargetAt = (x, y) => {
            if (drag.nest && drag.nest.isConnected) return { kind: 'bar', el: drag.nest };
            const u = under(x, y);
            if (u.row && u.row !== drag.row) {
                if (u.bar) return { kind: 'bar', el: u.bar };
                return { kind: 'block', el: u.row, block: u.row.dataset.block };
            }
            return null;
        };
        // 같은 막대 위에 머무는 동안만 시계를 돌린다. 다른 막대로 옮겨가거나 많이 움직이면
        // 다시 센다. 잡히면 양쪽에 표시를 켜 사용자가 모드가 바뀐 걸 보고 물릴 수 있게 한다.
        const armNest = (target, x, y) => {
            clearTimeout(drag.holdT);
            drag.nest = null;
            drag.hover = target;
            drag.armX = x;
            drag.armY = y;
            if (!target) return;
            drag.holdT = setTimeout(() => {
                if (!drag) return;
                drag.nest = target;
                target.classList.add('is-drop-target');
                drag.bar.classList.add('is-nesting');
            }, HOLD);
        };

        gantt.querySelectorAll('.gt-bar').forEach((bar) => {
            bar.addEventListener('pointerdown', (e) => {
                if (e.pointerType === 'mouse' && e.button !== 0) return;
                const r = bar.getBoundingClientRect();
                const off = e.clientX - r.left;
                // 좁은 막대에서도 양 끝을 잡을 수 있게 손잡이 폭을 막대 폭의 1/3까지 줄인다
                const edgeW = Math.max(4, Math.min(EDGE, r.width / 3));
                let edge = '';
                if (r.width >= MIN_RESIZABLE) {
                    if (off <= edgeW) edge = 'start';
                    else if (off >= r.width - edgeW) edge = 'end';
                }
                drag = {
                    bar, id: bar.dataset.id, x0: e.clientX, y0: e.clientY, moved: false,
                    edge, nest: null, hover: null, holdT: 0,
                    armX: e.clientX, armY: e.clientY,
                    track: bar.closest('.gt-track'), row: bar.closest('.gt-blockrow'),
                    css: { left: bar.style.left, width: bar.style.width },
                    px: { left: r.left, width: r.width, top: r.top },
                };
                try { bar.setPointerCapture(e.pointerId); } catch (_) { /* 캡처 못해도 진행 */ }
            });
            bar.addEventListener('pointermove', (e) => {
                if (!drag || drag.bar !== bar) return;
                const dx = e.clientX - drag.x0;
                const dy = e.clientY - drag.y0;
                if (!drag.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
                drag.moved = true;
                // 놓일 자리(주 단위로 끊은 값)만큼만 움직여 '보이는 자리 = 놓일 자리'가 되게 한다.
                // 1:1로 따라 움직이면 놓는 순간 딴 데로 붙어 안 먹은 것처럼 보인다.
                const px = snapPx(dx);
                showTip(dragDays(dx), drag.edge === 'start' ? px : 0);
                if (drag.edge) {
                    // 기간 조절: 잡은 쪽 끝만 따라 움직인다
                    bar.classList.add('is-resizing');
                    const trackLeft = drag.track.getBoundingClientRect().left;
                    if (drag.edge === 'start') {
                        const w = Math.max(6, drag.px.width - px);
                        bar.style.left = (drag.px.left - trackLeft + (drag.px.width - w)) + 'px';
                        bar.style.width = w + 'px';
                    } else {
                        bar.style.width = Math.max(6, drag.px.width + px) + 'px';
                    }
                    return;
                }
                bar.classList.add('is-dragging');
                bar.style.transform = `translate(${px}px, ${dy}px)`;
                const u = under(e.clientX, e.clientY);
                const moved = Math.abs(e.clientX - drag.armX) > REARM
                           || Math.abs(e.clientY - drag.armY) > REARM;
                if (u.bar !== drag.hover || (moved && !drag.nest)) {
                    clearMarks();
                    bar.classList.remove('is-nesting');
                    armNest(u.bar, e.clientX, e.clientY);
                }
                if (!drag.nest) {           // 멈춤이 안 잡혔으면 줄 이동만 미리 보여 준다
                    clearMarks();
                    const t = dropTargetAt(e.clientX, e.clientY);
                    if (t) t.el.classList.add('is-drop-target');
                    bar.classList.toggle('is-nesting', !!(t && t.kind === 'bar'));
                    const peer = t ? null : reorderPeerAt(e.clientX, e.clientY);
                    bar.classList.toggle('is-reordering', !!peer);
                    showLine(peer);
                    // 순서를 바꾸는 중에는 기간이 안 바뀌므로 날짜 안내도 그렇게 적는다
                    if (peer) tip.textContent = '순서 바꾸기';
                } else {
                    showLine(null);
                }
            });
            bar.addEventListener('pointerup', (e) => {
                if (!drag || drag.bar !== bar) return;
                if (!drag.moved) { reset(); return; }
                bar.dataset.dragged = '1';           // 이어서 오는 click(편집창 열기)은 무시
                const dx = e.clientX - drag.x0;
                const days = dragDays(dx);           // 끈 만큼을 날수로
                const id = drag.id;
                const from = drag.row ? (drag.row.dataset.block || '') : '';
                const blocks = (bar.dataset.blocks || '').split(',').filter(Boolean);
                if (drag.edge) {
                    const edge = drag.edge;
                    reset();
                    if (days === 0) return;
                    postForm('/plan/item/resize',
                             { id: id, edge: edge, days: days }).then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '기간을 바꾸지 못했습니다');
                    });
                    return;
                }
                const target = dropTargetAt(e.clientX, e.clientY);
                const peer = target ? null : reorderPeerAt(e.clientX, e.clientY);
                reset();
                if (target && target.kind === 'bar') {
                    // 하위로 넣기는 되돌리기 번거로워 한 번 묻는다(스쳐 지나 붙는 일을 막는다)
                    const to = target.el.dataset.title || '그 항목';
                    if (!window.confirm('「' + (bar.dataset.title || '이 항목')
                                        + '」를 「' + to + '」의 하위로 넣습니까?')) return;
                    postForm('/plan/item/reparent',
                             { id: id, parent_id: target.el.dataset.id }).then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '하위로 넣지 못했습니다');
                    });
                } else if (target && target.kind === 'block') {
                    // 잡은 줄만 놓은 줄로 바꾼다(다른 블록에 걸린 것은 그대로 남는다)
                    const next = blocks.filter((b) => b !== from);
                    if (target.block && !next.includes(target.block)) next.push(target.block);
                    postForm('/plan/item/update',
                             { id: id, block: next.join(',') }).then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '옮기지 못했습니다');
                    });
                } else if (peer) {
                    // 같은 줄에서 위아래로 끌었다. 그 자리로 계획 묶음째 옮긴다.
                    postForm('/plan/item/order',
                             { id: id, peer: peer.id, place: peer.place }).then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '순서를 바꾸지 못했습니다');
                    });
                } else if (days !== 0) {
                    // 하위가 있으면 서버가 하위 사슬까지 같은 날수만큼 함께 민다
                    postForm('/plan/item/shift', { id: id, days: days }).then((d) => {
                        if (d && d.ok) refreshGantt(id);
                        else toast((d && d.error) || '옮기지 못했습니다');
                    });
                }
            });
            bar.addEventListener('pointercancel', reset);
        });
    }

    // ---- 주간 탭: 이번 주 장기 항목 (장기 ↔ 주간 연동) ---------------------
    // 진척률은 장기 탭과 같은 엔드포인트로 저장해 막대·상위 항목이 함께 갱신되고,
    // '주간 목표로'·'블록으로'는 서버가 합친 결과를 그대로 화면 입력칸에 반영한다.
    function bindWeekLtItems() {
        const card = document.querySelector('.meta-goal');
        if (!card) return;
        const week = card.dataset.week;
        // ✎ 를 누르면 그 줄에 직접 입력칸이 열린다. 비워 두면 장기 이름을 그대로 쓴다.
        card.querySelectorAll('.wg-edit').forEach((btn) => {
            btn.addEventListener('click', () => {
                const inp = btn.closest('.wg-item').querySelector('.wg-goal');
                const open = inp.hidden;
                inp.hidden = !open;
                btn.setAttribute('aria-expanded', open ? 'true' : 'false');
                if (open) inp.focus();
            });
        });
        card.querySelectorAll('.wg-item').forEach((row) => {
            const id = row.dataset.id;
            const prog = row.querySelector('.wk-lt-prog-input');
            prog?.addEventListener('change', () => {
                const v = Math.max(0, Math.min(100, parseInt(prog.value, 10) || 0));
                prog.value = v;
                postForm('/plan/item/update', { id: id, progress: v })
                    .then((d) => toast((d && d.ok) ? '진척률 저장' : '저장 실패'));
            });
            row.querySelector('.wk-lt-theme')?.addEventListener('click', () => {
                const label = row.querySelector('.wk-lt-label')?.value || '';
                postForm('/week/item-to-theme',
                         { week_start: week, item_id: id, label: label }).then((d) => {
                    if (!d || !d.ok) { toast((d && d.error) || '옮기기 실패'); return; }
                    const inp = document.querySelector('input[name="theme_' + d.label + '"]');
                    if (inp) inp.value = d.text;
                    toast(d.label + ' 이름에 반영');
                });
            });
        });
    }

    // 영역 관리(추가·이름·색·순서·숨김). 영역은 간트 행이 아니라 막대 색으로만 쓰인다.
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
            if (e.isComposing || e.keyCode === 229) return;   // 한글 조합 중 엔터는 조합 확정이다
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
        // 영역 색을 바꾸면 그 영역 막대가 전부 바뀌므로 화면을 다시 그린다
        document.querySelectorAll('.pg-area-tone').forEach((sel) => {
            sel.addEventListener('change', () => {
                postForm('/plan/area/update', { id: sel.dataset.id, tone: sel.value })
                    .then((d) => { if (d && d.ok) location.reload(); else toast('색 저장 실패'); });
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
        // 방금 누른 동작의 안내가 아직 떠 있으면 덮지 않는다. '저장됨'은 배경 알림이라
        // 사용자가 시킨 일의 결과보다 뒤에 선다. 안 그러면 '내일로'를 눌러 이월해 놓고도
        // 곧바로 '저장됨'으로 바뀌어(자동저장이 늦게 끝난다) 된 건지 알 수 없다.
        if (Date.now() - lastToastAt < TOAST_MS) return;
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
                    extra[opts.groupPrefix + g.dataset.asIdx] = g.value;
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
    // 태그를 견줄 때 쓰는 맨몸 형태. 6block 은 '#AI', Record 는 'AI' 로 적어 두므로
    // '#' 를 떼고 견주지 않으면 같은 태그가 둘로 갈린다.
    function bareTag(t) {
        return String(t || '').trim().replace(/^#+/, '');
    }

    function normalizeTags(val) {
        if (!val) return '';
        return val.split(/[\s,]+/).filter(Boolean)
            .map((t) => (t.startsWith('#') ? t : '#' + t))
            .join(' ');
    }


    // ---- 고결감 (/reflect) -----------------------------------------------
    // 화면 구성과 동작은 Record 고결감 탭과 같다(2026-08-03 사용자 결정). 카드는 크기를
    // 맞춰 훑고, 고치는 것은 카드를 눌러 여는 창에서 한다. 다시보기 사본도 같은 창으로
    // 열리며 내용 칸에 다시 볼 내용을 적는다 — 저장은 원본 행으로 간다(서버가 사본
    // 수정을 거부한다). 원문은 그 아래 링크를 눌러 한 겹 더 열어 본다.
    function bindReflect() {
        const compose = document.querySelector('.reflect-compose');
        const list = document.getElementById('reflect-list');
        const upcoming = document.getElementById('reflect-upcoming');
        if (!compose && !list) return;
        let lastSig = list ? (list.dataset.sig || null) : null;
        const curKind = () => new URLSearchParams(location.search).get('kind') || '';

        // ---- 문제 알림: 잘 도는 동안은 아무 말도 하지 않는다 ----
        // 실패했을 때만 제목 행에 빨간 느낌표가 서고, 누르면 무엇이 문제이고 무엇을
        // 하면 되는지와 다시 시도 버튼이 나온다.
        let problemMsg = '';
        const errBtn = document.getElementById('rf-err');
        const errBox = document.getElementById('rf-errbox');
        function problem(msg) {
            problemMsg = msg || '';
            if (errBtn) errBtn.hidden = !problemMsg;
            if (errBox && !problemMsg) { errBox.hidden = true; errBox.innerHTML = ''; }
        }
        if (errBtn && errBox) {
            errBtn.addEventListener('click', () => {
                if (!errBox.hidden) { errBox.hidden = true; return; }
                errBox.textContent = problemMsg + ' ';
                const again = document.createElement('button');
                again.type = 'button'; again.className = 'rf-mini'; again.textContent = '다시 시도';
                again.addEventListener('click', () => { problem(''); refreshReflect(true); });
                errBox.appendChild(again);
                errBox.hidden = false;
            });
        }

        // 대상 카드로 스크롤·강조(미도래 칩이 쓴다)
        function focusCard(id) {
            if (!id || !list) return;
            const card = list.querySelector('.rf-card[data-id="' + id + '"]');
            if (!card) return;
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
        }

        function deleteItem(id, after) {
            if (!window.confirm('이 기록을 삭제합니다. 캘린더 이벤트도 함께 지웁니다.')) return;
            postForm('/reflect/delete/' + id, {}).then((d) => {
                if (d && d.ok) { if (after) after(); toast('삭제'); problem(''); refreshReflect(true); }
                else problem('지우지 못했습니다. 잠시 뒤 다시 시도해주세요.');
            }).catch(() => problem('지우지 못했습니다. 연결을 확인해주세요.'));
        }

        // ---- 부분 갱신: 목록·미도래를 서버 진실로 다시 그린다 ----
        function refreshReflect(force) {
            if (!list) return Promise.resolve();
            const url = '/reflect/list?kind=' + encodeURIComponent(curKind()) + (force ? '&force=1' : '');
            return fetch(url).then((r) => r.json()).then((d) => {
                if (!d || !d.ok) return;
                if (!force) {
                    if (d.sig === lastSig) return;                                    // 변화 없음
                    const ae = document.activeElement;
                    if (ae && list.contains(ae)) return;                              // 입력·인라인수정 중 보호
                }
                lastSig = d.sig;
                if (upcoming) upcoming.innerHTML = d.upcoming_html;
                list.innerHTML = d.list_html;
                bindList(); bindUpcoming(); applySearch();
            }).catch(() => {});
        }

        // ---- 유사검색(오타·부분도 찾음) + 태그 좁히기 ----
        let tagFilter = '';
        function applySearch() {
            const searchInput = document.getElementById('rf-search-input');
            if (!list) return;
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
            // 태그는 '#' 를 떼고 견준다. 6block 은 '#AI' 로, Record 는 'AI' 로 적어 두므로
            // 그대로 견주면 같은 태그가 둘로 갈린다.
            const inTag = (el) => !tagFilter ||
                (el.dataset.tags || '').split(/\s+/).map(bareTag).indexOf(tagFilter) >= 0;
            const q = norm(searchInput ? searchInput.value.trim() : '');
            const toks = q ? q.split(/\s+/).filter(Boolean) : [];
            let shown = 0;
            if (!toks.length) {
                items.forEach((el) => {
                    el.hidden = !inTag(el);
                    if (!el.hidden) { list.appendChild(el); shown += 1; }
                });
            } else {
                const scored = items.map((el, idx) => ({
                    el, idx, s: inTag(el) ? score(toks, el.dataset.search || norm(el.textContent)) : 0,
                }));
                scored.forEach((o) => { o.el.hidden = o.s === 0; });
                scored.filter((o) => o.s > 0).sort((a, b) => b.s - a.s || a.idx - b.idx)
                    .forEach((o) => { list.appendChild(o.el); shown += 1; });
            }
            if (noMatch) { noMatch.hidden = !(items.length && shown === 0); list.appendChild(noMatch); }
        }

        // ---- 태그 칩: 평소 접혀 있다. 칩이 목록보다 길어지면 정작 목록이 밀린다 ----
        const tagTog = document.getElementById('rf-tagtog');
        const tagChips = document.getElementById('rf-tagchips');
        function drawTagChips() {
            if (!tagChips) return;
            const seen = [];
            (window._rfTags || []).forEach((t) => {
                const b = bareTag(t);
                if (b && seen.indexOf(b) < 0) seen.push(b);
            });
            const tags = seen.slice(0, 24);
            tagChips.innerHTML = '';
            if (!tags.length) return;
            [''].concat(tags).forEach((t) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'rf-tag rf-tag-btn' + (tagFilter === t ? ' is-active' : '');
                b.textContent = t || '태그 전체';
                b.addEventListener('click', () => {
                    tagFilter = (tagFilter === t) ? '' : t;
                    drawTagChips();
                    applySearch();
                });
                tagChips.appendChild(b);
            });
        }
        if (tagTog && tagChips) {
            tagTog.addEventListener('click', () => {
                tagChips.hidden = !tagChips.hidden;
                tagTog.classList.toggle('is-active', !tagChips.hidden);
                if (!tagChips.hidden) drawTagChips();
            });
        }

        // ---- 카드를 눌러 여는 창 ----
        const modal = document.getElementById('rf-edit-modal');
        const srcModal = document.getElementById('rf-src-modal');
        // 창이 다루는 카드와, 저장이 향하는 카드(다시보기는 원본)를 따로 들고 있는다.
        let openCard = null;
        let saveCard = null;

        function closeSrc() { if (srcModal) srcModal.hidden = true; }
        function closeModal() {
            closeSrc();
            if (modal) { modal.hidden = true; modal.dataset.id = ''; }
            const wasRevisit = !!(openCard && openCard.dataset.source);
            openCard = null; saveCard = null;
            // 다시 볼 내용은 창에서 고쳤어도 목록 카드에는 옛 글이 남는다. 닫을 때 맞춘다.
            if (wasRevisit) refreshReflect(true);
        }

        // 원문 창에 세우는 것은 원본의 값이다. 원본 카드가 목록에 있으면 그 값을 쓰고,
        // 없으면 사본이 들고 있는 원본 제목·본문으로 대신한다.
        function srcOf(card) {
            if (!card || !card.dataset.source) return null;
            const origin = list
                ? list.querySelector('.rf-card[data-id="' + card.dataset.source + '"]') : null;
            if (origin) {
                return {
                    title: origin.dataset.title || '',
                    kind: origin.dataset.kind || '',
                    date: origin.dataset.event || '',
                    text: origin.dataset.text || '',
                };
            }
            return {
                title: card.dataset.ptitle || '', kind: card.dataset.kind || '',
                date: '', text: card.dataset.ptext || '',
            };
        }

        function openSrc(card) {
            const s = srcOf(card);
            if (!srcModal || !s) return;
            document.getElementById('rf-src-title').textContent = s.title;
            document.getElementById('rf-src-date').textContent = [s.kind, s.date].join(' ').trim();
            document.getElementById('rf-src-text').textContent = s.text;
            srcModal.hidden = false;
        }

        function openEditModal(card) {
            if (!modal || !card) return;
            const revisit = !!card.dataset.source;
            // 사본은 원본을 비추기만 한다. 화면에 세우는 값도, 저장이 가는 곳도 원본이다.
            const origin = revisit && list
                ? list.querySelector('.rf-card[data-id="' + card.dataset.source + '"]') : null;
            openCard = card;
            saveCard = origin || card;
            modal.dataset.id = saveCard.dataset.id;
            document.getElementById('rf-modal-headline').textContent = revisit ? '다시보기' : '기록 고치기';
            // 날짜칸은 자체 위젯이 한 칸을 덧씌운다. 코드가 값을 넣었으면 그 칸도 맞춰야
            // 화면에 보인다(syncDateParts).
            const set = (id, v) => {
                const el = document.getElementById(id);
                if (!el) return;
                el.value = v || '';
                if (el.type === 'date') syncDateParts(el);
            };
            set('rf-modal-title', saveCard.dataset.title);
            set('rf-modal-tags', saveCard.dataset.tags);
            set('rf-modal-event', saveCard.dataset.event);
            set('rf-modal-review', saveCard.dataset.review);
            const kind = saveCard.dataset.kind || '';
            modal.querySelectorAll('input[name="rfmk"]').forEach((r) => { r.checked = (r.value === kind); });
            const box = document.getElementById('rf-modal-text');
            box.value = revisit ? (card.dataset.note || '') : (saveCard.dataset.text || '');
            box.placeholder = revisit ? '다시 볼 내용' : '내용';
            const srcBtn = document.getElementById('rf-modal-src');
            srcBtn.hidden = !revisit;
            if (revisit) {
                const s = srcOf(card);
                srcBtn.textContent = ('원문 · ' + s.date + ' ' + s.title).replace(/\s+/g, ' ');
            }
            modal.hidden = false;
            setTimeout(() => document.getElementById('rf-modal-title')?.focus(), 30);
        }

        function saveModal() {
            if (!modal || !saveCard) return;
            const revisit = !!(openCard && openCard.dataset.source);
            const box = (document.getElementById('rf-modal-text').value || '').trim();
            const kind = (modal.querySelector('input[name="rfmk"]:checked') || {}).value || '';
            const title = (document.getElementById('rf-modal-title').value || '').trim();
            const tags = normalizeTags((document.getElementById('rf-modal-tags').value || '').trim());
            const event_date = document.getElementById('rf-modal-event').value || '';
            const review_date = document.getElementById('rf-modal-review').value || '';
            // 다시보기의 내용 칸은 다시 볼 내용이다. 본문 자리로 보내면 원본 본문을 덮어써
            // 버리므로, 메모는 메모 길로 보내고 본문은 원본 것을 그대로 되쓴다.
            const text = revisit ? (saveCard.dataset.text || '') : box;
            if (!title && !text) { toast('제목이나 내용을 입력하세요'); return; }
            const note = revisit
                ? fetch('/reflect/review-note/' + saveCard.dataset.id, {
                    method: 'POST', headers: FORM_HEADERS,
                    body: new URLSearchParams({ note: box }).toString(),
                }).then((r) => r.json())
                : Promise.resolve({ ok: true });
            note.then(() => fetch('/reflect/update/' + saveCard.dataset.id, {
                method: 'POST', headers: FORM_HEADERS,
                body: new URLSearchParams({ kind, title, text, tags, review_date, event_date }).toString(),
            })).then((r) => r.json()).then((d) => {
                if (!d || !d.ok) { problem('고치지 못했습니다. 잠시 뒤 다시 시도해주세요.'); toast('저장 실패'); return; }
                problem(''); toast('저장됨'); closeModal(); refreshReflect(true);
            }).catch(() => { problem('고치지 못했습니다. 연결을 확인해주세요.'); toast('저장 실패'); });
        }

        if (modal) {
            modal.querySelectorAll('.rf-modal-x, .rf-modal-backdrop')
                .forEach((el) => el.addEventListener('click', closeModal));
            document.addEventListener('keydown', (e) => {
                if (e.key !== 'Escape') return;
                if (srcModal && !srcModal.hidden) { closeSrc(); return; }
                if (!modal.hidden) closeModal();
            });
            bindListEditor(document.getElementById('rf-modal-text'));
            document.getElementById('rf-modal-save')?.addEventListener('click', saveModal);
            document.getElementById('rf-modal-del')?.addEventListener('click', () => {
                // 지우는 것은 연 카드다. 원본을 지우면 사본과 두 캘린더 일정까지 함께 사라지고,
                // 사본을 지우면 원본의 '다시 볼 날짜'가 풀린다(서버가 그렇게 정리한다).
                if (openCard) deleteItem(openCard.dataset.id, closeModal);
            });
            document.getElementById('rf-modal-src')?.addEventListener('click', () => openSrc(openCard));
        }
        if (srcModal) {
            srcModal.querySelector('#rf-src-close')?.addEventListener('click', closeSrc);
            srcModal.querySelector('.rf-modal-backdrop')?.addEventListener('click', closeSrc);
        }

        function bindList() {
            if (!list) return;
            list.querySelectorAll('.rf-del').forEach((b) =>
                b.addEventListener('click', (e) => {
                    e.stopPropagation();
                    deleteItem(b.dataset.id, () => b.closest('.rf-card')?.remove());
                }));

            // '캘린더 안 됨' 을 누르면 그 자리에서 다시 올린다. 붉은 표시에 뻔한 고침이
            // 있으면 그 표시가 곧 버튼이다 — 알리기만 하면 손쓸 데가 없다.
            list.querySelectorAll('.rf-sync.off').forEach((b) =>
                b.addEventListener('click', (e) => {
                    e.stopPropagation();
                    if (b.disabled) return;
                    b.disabled = true;
                    b.textContent = '올리는 중';
                    fetch('/reflect/sync/' + b.dataset.id, { method: 'POST' })
                        .then((r) => r.json())
                        .then((d) => {
                            if (d && d.synced) { b.remove(); return; }
                            b.disabled = false;
                            b.textContent = '캘린더 안 됨';
                            toast('캘린더에 못 올렸어요. 구글 연결을 확인해주세요.');
                        })
                        .catch(() => {
                            b.disabled = false;
                            b.textContent = '캘린더 안 됨';
                        });
                }));

            // 카드 아무 데나 눌러도 열린다. ✕·캘린더 버튼과 다시 볼 내용 칸은 빼고.
            list.querySelectorAll('.rf-card').forEach((card) =>
                card.addEventListener('click', (e) => {
                    if (e.target.closest('.rf-del') || e.target.closest('.rf-sync')
                        || e.target.tagName === 'TEXTAREA') return;
                    openEditModal(card);
                }));

            // 다시 볼 내용: 버튼 없이 자동 저장(원본 review_note + 사본 캘린더 반영)
            list.querySelectorAll('.rf-note').forEach((ta) => {
                if (ta.dataset.rfnote) return;
                ta.dataset.rfnote = '1';
                let timer = null, sent = ta.value;
                const flush = () => {
                    if (timer) { clearTimeout(timer); timer = null; }
                    if (ta.value === sent) return;
                    sent = ta.value;
                    fetch('/reflect/review-note/' + ta.dataset.target, {
                        method: 'POST', headers: FORM_HEADERS,
                        body: new URLSearchParams({ note: ta.value }).toString(),
                    }).then((r) => r.json()).then((d) => {
                        if (d && d.ok) {
                            const card = ta.closest('.rf-card');
                            if (card) card.dataset.note = ta.value;
                            problem('');
                            if (typeof autosaveToast === 'function') autosaveToast();
                        } else problem('다시 볼 내용을 저장하지 못했습니다.');
                    }).catch(() => problem('다시 볼 내용을 저장하지 못했습니다. 연결을 확인해주세요.'));
                };
                ta.addEventListener('change', flush);
                ta.addEventListener('blur', flush);
                ta.addEventListener('input', () => { if (timer) clearTimeout(timer); timer = setTimeout(flush, 1200); });
            });
        }

        // ---- 미도래 칩 바인딩(클릭 이동) ----
        function bindUpcoming() {
            if (!upcoming) return;
            upcoming.querySelectorAll('.rf-chip').forEach((chip) =>
                chip.addEventListener('click', () => focusCard(chip.dataset.target)));
        }

        // ---- 태그 · 다시 볼 날 창(제목 행의 ＋) ----
        // Record 고결감 탭과 같은 짜임이다(2026-08-19 사용자 요청). 어쩌다 쓰는 칸이
        // 늘 한 행을 먹으면 정작 내용칸이 그만큼 짧다. 붙여 둔 것이 있으면 단추에
        // 개수와 날짜를 적는다 — 접힌 칸은 무엇을 달아 뒀는지 화면에 안 남는다.
        const rfExtraWin = document.getElementById('rf-extra');
        const rfExtraBtn = document.getElementById('rf-extra-btn');
        function paintRfExtra() {
            if (!rfExtraBtn) return;
            const bits = [];
            const tags = ((document.getElementById('rf-tags').value || '').trim()
                .split(/[,\s]+/).filter(Boolean));
            if (tags.length) bits.push(String(tags.length));
            // 날짜는 월/일만. 연도까지 적으면 이 좁은 행에서 옆 단추를 밀어낸다.
            const d = (document.getElementById('rf-review').value || '').trim();
            if (d) bits.push(d.slice(5).replace('-', '/'));
            const n = document.getElementById('rf-extra-n');
            n.hidden = bits.length === 0;
            n.textContent = bits.join(' · ');
            rfExtraBtn.classList.toggle('on', bits.length > 0);
        }
        if (rfExtraBtn && rfExtraWin) {
            const closeRfExtra = () => { rfExtraWin.hidden = true; paintRfExtra(); };
            rfExtraBtn.addEventListener('click', () => {
                rfExtraWin.hidden = false;
                document.getElementById('rf-tags').focus();
            });
            document.getElementById('rf-extra-close').addEventListener('click', closeRfExtra);
            rfExtraWin.querySelector('.rf-modal-backdrop')
                .addEventListener('click', closeRfExtra);
            ['rf-tags', 'rf-review'].forEach((id) => document.getElementById(id)
                .addEventListener('input', paintRfExtra));
            // 조합 중 엔터는 조합을 끝내는 키다. 가로채면 치던 한글이 자모로 쪼개진다.
            document.getElementById('rf-tags').addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
                e.preventDefault();
                closeRfExtra();
            });
        }

        // ---- 작성칸(저장) ----
        bindListEditor(document.getElementById('rf-text'));
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
                    if (!d.ok) { problem('저장하지 못했습니다. 잠시 뒤 다시 시도해주세요.'); toast('저장 실패'); return; }
                    problem('');
                    toast(d.synced ? '기록 · 캘린더 반영' : '기록함 (캘린더 미반영)');
                    titleEl.value = ''; ta.value = '';
                    document.getElementById('rf-tags').value = '';
                    document.getElementById('rf-review').value = '';
                    syncDateParts(document.getElementById('rf-review'));
                    paintRfExtra();      // 비웠으니 ＋ 의 개수·날짜도 함께 내린다
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
        // 슬롯의 '고민' 버튼과 하루 마감의 '고결감에 기록하기'가 같은 작성창을 연다.
        document.querySelectorAll('.slot-reflect, .open-reflect').forEach((btn) => {
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
                        setupAgendaMore('agenda-events');
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
                        setupAgendaMore('agenda-tasks');
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
                // 화면에 적힌 것을 그대로 함께 보낸다(블록 PLAN 과 그 블록 슬롯의 DO 칸).
                // 이 버튼을 누르면 방금 고친 칸에서 blur 가 나며 자동저장도 같이 출발하는데,
                // 둘 중 무엇이 서버에 먼저 닿을지는 정해져 있지 않다. 예전에는 이월이 먼저
                // 닿아 방금 적은 것을 못 보고 '비어 있다'고 되돌려 보냈다.
                const body = { block_id: btn.dataset.blockId };
                const plan = document.querySelector(
                    'textarea[name="plan_' + btn.dataset.blockId + '"]');
                if (plan) body.plan = plan.value;
                // .block-stack 은 하루 전체를 감싸는 통이고, 블록 하나는 article.block 이다.
                const block = btn.closest('article.block');
                if (block) {
                    block.querySelectorAll('[name^="do_"]').forEach((el) => {
                        body[el.name] = el.value;
                    });
                }
                postForm('/block/rollover', body).then((d) => {
                    if (!d || !d.ok) {
                        toast(d && d.error === 'empty'
                            ? '이 블록에 넘길 내용이 없습니다'
                            : '이월 실패');
                        return;
                    }
                    const parts = [];
                    if (d.moved) parts.push(d.moved + '칸');
                    if (d.plan) parts.push('PLAN');
                    let msg = '내일 ' + (d.label || '') + '로 '
                        + (parts.join(' · ') || '내용') + ' 이월';
                    if (d.skipped) msg += ' · ' + d.skipped + '칸은 자리가 없어 못 넘김';
                    toast(msg);
                    // 넘긴 자리로 곧장 데려간다. 넘겨 놓고 다음 날로 손수 넘어가
                    // 그 블록을 찾아 내려가던 걸음을 없앤다.
                    // 잠깐 기다렸다 옮기는 것은 방금 고친 칸에서 함께 출발한 자동저장이
                    // 닿을 틈을 주기 위해서다(화면을 떠나면 가는 중인 요청이 끊긴다).
                    if (d.date) {
                        setTimeout(() => {
                            location.href = '/day/' + d.date
                                + (d.block_order === undefined ? '' : '#blk-' + d.block_order);
                        }, 800);
                    }
                });
            });
        });
    }

    // ---- 하루 마감(하루 평가 · 내일 가장 중요한 일 → 내일 목표) ----
    // 하루 마감 '기록이 빈 슬롯'. 여기서 적으면 곧바로 저장하고 위 블록의 같은 슬롯에도 옮긴다.
    // 위 슬롯 줄과 이름(name)이 겹치면 폼 저장 때 서로 덮어써서, 이 칸들은 이름 없이 두고
    // data-slot 으로 직접 저장한다.
    function bindCatchup() {
        const list = document.getElementById('cu-list');
        if (!list) return;
        const countEl = document.getElementById('cu-count');
        const emptyEl = document.getElementById('cu-empty');

        // 위 블록에 있는 같은 슬롯의 칸들. 두 화면이 어긋나 보이지 않게 함께 맞춘다.
        const mirror = (id, did, done) => {
            const ta = document.querySelector(`textarea[name="did_${id}"]`);
            if (ta && did !== null) {
                ta.value = did;
                ta.closest('.slot-did')?.querySelector('.slot-did-btn')
                    ?.classList.toggle('has-did', !!did);
            }
            const cb = document.querySelector(`.slot-check[data-slot="${id}"]`);
            if (cb && done !== null) {
                cb.checked = done;
                cb.closest('.slot')?.classList.toggle('is-done', done);
            }
        };
        const settle = (row) => {
            row.classList.add('is-filled');
            const left = list.querySelectorAll('.cu-row:not(.is-filled)').length;
            if (countEl) countEl.textContent = left;
            if (emptyEl) emptyEl.hidden = left > 0;
        };

        list.querySelectorAll('.cu-row').forEach((row) => {
            const id = row.dataset.slot;
            const input = row.querySelector('.cu-did');
            const check = row.querySelector('.cu-check');
            const same = row.querySelector('.cu-same');

            const saveDid = () => {
                const v = input.value.trim();
                if (!v) return;
                saveField('slot', id, 'did_text', v);
                mirror(id, v, null);
                settle(row);
            };
            input.addEventListener('change', saveDid);
            input.addEventListener('blur', saveDid);
            input.addEventListener('keydown', (e) => {
                // 한글 조합 중의 Enter 는 조합을 확정하는 키라 가로채면 글자가 잘린다.
                if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
                e.preventDefault();
                saveDid();
                const rows = [...list.querySelectorAll('.cu-row:not(.is-filled) .cu-did')];
                (rows.find((x) => x !== input) || input).focus();
            });

            check.addEventListener('change', () => {
                const done = check.checked ? '1' : '0';
                sendOrQueue(
                    { id: genId(), kind: 'slot', url: '/slot/done/' + id,
                      headers: FORM_HEADERS, body: 'done=' + done },
                    () => toast(check.checked ? '완료 체크' : '체크 해제'),
                    () => toast('전송 대기 · 자동 재시도'),
                );
                mirror(id, null, check.checked);
                if (check.checked) settle(row);
            });

            same?.addEventListener('click', () => {
                // 계획 글은 위 슬롯 줄의 DO 칸에서 지금 값을 읽는다(방금 고친 것도 반영된다).
                const plan = document.querySelector(`input[name="do_${id}"]`);
                input.value = (plan ? plan.value : input.placeholder).trim();
                saveDid();
                if (!check.checked) { check.checked = true; check.dispatchEvent(new Event('change')); }
            });
        });
    }

    function bindShutdown() {
        const form = document.querySelector('.day-form');
        const date = form ? form.dataset.date : '';
        // 하루 평가는 name="day_review" 로 자동저장에 걸려 있다(bindAutosaveAll).
        // 고결감 기록은 슬롯의 '고민' 버튼이 여는 작성창(bindReflectModal) 하나로 모았다.
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
            sel.addEventListener('change', () => {
                paintCategory(sel);
                // 블록 구분을 바꾸면 그 블록에서 구분이 빈(상속) 슬롯들의 색을 즉시 다시 칠한다.
                if (sel.classList.contains('block-cat')) {
                    const blk = sel.closest('.block');
                    if (blk) blk.querySelectorAll('.slot .cat-select').forEach((s) => {
                        if (!s.value) paintCategory(s);
                    });
                }
            });
        });

        // 주간 탭: 이번 주 장기 항목(장기 탭 계획 막대) 연동
        bindWeekLtItems();

        // 주간 탭: 구분 템플릿은 고르는 즉시 그 주 전체에 적용된다(따로 누를 버튼이 없다)
        const wkTplSel = document.getElementById('wk-apply-tpl-sel');
        wkTplSel?.addEventListener('change', () => {
            const tid = wkTplSel.value;
            if (!tid) return;
            postForm('/week/apply-template',
                     { week_start: wkTplSel.dataset.week, template_id: tid })
                .then((d) => {
                    if (!d || !d.ok) {
                        wkTplSel.value = '';
                        toast((d && d.error === 'empty-template')
                            ? '템플릿이 비어 있습니다' : '적용 실패');
                        return;
                    }
                    // 담은 부분만 적용되므로, 실제로 들어간 것만 세어 알린다.
                    // 세션시간·고정 할일은 주간 화면에 안 보이니(칸은 오늘 탭에 있다) 더 그렇다.
                    const parts = [];
                    if (d.days) parts.push('세션시간 ' + d.days + '일');
                    if (d.names) parts.push('블록 이름 ' + d.names + '칸');
                    if (d.applied) parts.push('구분 ' + d.applied + '블록');
                    if (d.slots) parts.push('칸 구분 ' + d.slots + '칸');
                    if (d.filled) parts.push('고정 할일 ' + d.filled + '칸');
                    let msg = parts.length ? parts.join(' · ') + ' 적용' : '바뀐 것이 없습니다';
                    if (d.skipped_days) {
                        msg += ' · 적어 둔 것이 있는 ' + d.skipped_days
                            + '일은 세션시간을 안 바꿨습니다';
                    }
                    toast(msg);
                    setTimeout(() => location.reload(), 1400);
                });
        });

        // 주간 탭: '블록이름'(B1~B6 이번 주 이름). 기본은 접힘.
        const wkNamesBtn = document.getElementById('wk-names-toggle');
        wkNamesBtn?.addEventListener('click', () => {
            const box = document.getElementById('wk-names');
            if (!box) return;
            const show = box.hidden;
            box.hidden = !show;
            wkNamesBtn.setAttribute('aria-expanded', show ? 'true' : 'false');
            if (show) box.querySelector('input')?.focus();
        });

        // 주간 탭: 이번 주 계획으로 블록 테마(B1~B6) 자동 채우기(빈 칸만)
        const wkFillThemes = document.getElementById('wk-fill-themes');
        wkFillThemes?.addEventListener('click', () => {
            wkFillThemes.disabled = true;
            postForm('/week/decompose-themes', { week_start: wkFillThemes.dataset.week })
                .then((d) => {
                    wkFillThemes.disabled = false;
                    if (d && d.ok) {
                        toast((d.used_ai ? 'AI ' : '') + '블록 테마 ' + d.filled + '칸 채움');
                        if (d.filled) location.reload();
                    } else {
                        toast((d && d.error) || '자동 채우기 실패');
                    }
                });
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
            const dialBtn = pomo.querySelector('.pomo-dial');
            dialBtn?.addEventListener('click', () => {
                const open = pomo.classList.toggle('expanded');
                dialBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
            });
            pomo.querySelector('.pomo-start')?.addEventListener('click', () => {
                ensureNotifPermission();
                startFocus(currentSlotKey());
            });
            pomo.querySelector('.pomo-skip')?.addEventListener('click', () => skip());
            pomo.querySelector('.pomo-stop')?.addEventListener('click', () => stop());
            pomo.querySelector('.pomo-auto')?.addEventListener('click', () => toggleAuto());
        }

        // 로고: 오늘 탭을 새로 받고, 뜬 뒤 지금 블록으로 맞춘다(load → initialScroll).
        // 이미 오늘 탭이면 같은 주소라 브라우저가 이동을 건너뛰기도 해서 직접 다시 부른다.
        document.querySelector('a.logo')?.addEventListener('click', (e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;   // 새 탭·새 창은 그대로
            e.preventDefault();
            if (location.pathname === '/today') location.reload();
            else location.assign('/today');
        });

        // 테마 토글
        document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);

        // 오늘 탭 '평가'(하루 지표 + 하루 평가·내일 한 가지 + 기록이 빈 슬롯).
        // 날짜 줄 오른쪽 버튼으로 그 자리에서 편다. 빈 슬롯 개수 뱃지는 그대로 둔다.
        const kpiBtn = document.getElementById('kpi-toggle');
        kpiBtn?.addEventListener('click', () => {
            const box = document.getElementById('eval-panel');
            if (!box) return;
            const show = box.hidden;
            box.hidden = !show;
            kpiBtn.setAttribute('aria-expanded', show ? 'true' : 'false');
            kpiBtn.firstChild.nodeValue = (show ? '평가 접기' : '평가')
                + (kpiBtn.querySelector('.count') ? ' ' : '');
        });

        // 맨 위 막대: 새로고침 아이콘(지금 화면을 서버에서 다시 받는다)
        document.getElementById('page-refresh')?.addEventListener('click', () => {
            location.reload();
        });

        // 주간 탭 '통계'(코어·PLAN→DO 달성률·기록된 시간 + 카테고리별 시간 분포). 기본은 접힘.
        const wkStatsBtn = document.getElementById('wk-stats-toggle');
        wkStatsBtn?.addEventListener('click', () => {
            const box = document.getElementById('wk-stats');
            if (!box) return;
            const show = box.hidden;
            box.hidden = !show;
            wkStatsBtn.setAttribute('aria-expanded', show ? 'true' : 'false');
            wkStatsBtn.textContent = show ? '통계 접기' : '통계';
        });

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

        // 일정·할 일 '더보기' 토글 + 초기 접힘 계산
        document.querySelectorAll('.agenda-more').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.target;
                agendaExpanded[id] = !agendaExpanded[id];
                setupAgendaMore(id);
            });
        });
        setupAgendaMoreAll();

        bindSlotChecks();
        bindBlockTools();
        bindSettingsTabs();
        bindSettings();
        bindBlockTimes();
        // 분석 탭: AI 제안은 버튼을 누를 때만 호출한다(화면 로드마다 부르지 않는다).
        const aiBtn = document.getElementById('ai-insight-btn');
        aiBtn?.addEventListener('click', () => {
            const body = document.getElementById('ai-insight-body');
            aiBtn.disabled = true;
            if (body) body.textContent = 'AI에게 묻는 중… 몇 초 걸립니다.';
            postForm('/analytics/ai', { rng: aiBtn.dataset.rng || '7' }).then((d) => {
                aiBtn.disabled = false;
                if (body) body.textContent = (d && d.ok) ? d.text
                    : ((d && d.error) || '호출 실패');
            });
        });

        bindHints();
        bindThemeWeekdays();
        bindDateParts();
        bindWkLinks();
        bindGantt();
        bindPlanAreas();
        bindReflect();
        bindReflectModal();
        bindTodayExternal();
        bindRollover();
        bindShutdown();
        bindCatchup();

        // 실시간 폴링 + 앱 재진입 시 현재 블록 재포커싱
        if (document.querySelector('.day-form')) {
            fillTaskPopsFromPage();   // 첫 폴링 전까지 블록 팝오버를 채워 둔다
            heroCalFromPage();        // 맨 위 일정 배너도 서버가 그려 준 목록에서 시작한다
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
        document.querySelectorAll('textarea:not(.gp-input)').forEach((ta) => bindListEditor(ta));
        bindGpInputs();
        setupTagModal();

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
            window.addEventListener(ev, () => {
                lastUserInteract = Date.now();
                lastActiveAt = lastUserInteract;
            }, { passive: true });
        });
        // 타이핑도 '이 탭을 쓰는 중'으로 친다(한 일·메모 입력). 자동 시작이 이 값을 본다.
        window.addEventListener('keydown', () => { lastActiveAt = Date.now(); }, { passive: true });

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

        // 이 페이지가 불러온 app.js 버전. 서버 버전과 달라지면 스스로 새로고침한다.
        const appSrcForVer = document.querySelector('script[src*="/static/app.js"]');
        if (appSrcForVer) {
            myVer = new URL(appSrcForVer.src, location.href).searchParams.get('v') || '';
        }
        const verMineEl = document.getElementById('set-ver-mine');   // 설정 탭에만 있다
        if (verMineEl) verMineEl.textContent = myVer || '알 수 없음';

        // 설정 탭 '들어보기'. 저장 전에도 지금 고른 음원·길이 그대로 울려 바로 비교할 수 있다.
        document.querySelectorAll('[data-preview]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const which = btn.dataset.preview;
                const sound = document.querySelector('[data-key="pomo_' + which + '_sound"]');
                const sec = document.querySelector('[data-key="pomo_' + which + '_sec"]');
                playSound(sound ? sound.value : 'chord',
                          parseFloat(sec ? sec.value : '2.5') || 2.5);
            });
        });

        // 화면 꺼짐 방지: 로드 시 + 다시 보일 때 + 첫 입력 시 wake lock 획득
        requestWakeLock();
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) return;
            requestWakeLock();
            checkVersion(true);   // 탭을 다시 볼 때 옛 코드면 바로 새로고침
        });
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
            // app.js 를 불러온 주소의 ?v= (파일 수정시각)을 그대로 넘겨 캐시 이름에 쓴다.
            const appSrc = document.querySelector('script[src*="/static/app.js"]')?.src || '';
            const ver = new URL(appSrc, location.href).searchParams.get('v') || '0';
            navigator.serviceWorker.register('/sw.js?v=' + ver, { scope: '/' })
                .then((reg) => { reg.update().catch(() => {}); })
                .catch(() => {});
        }

        // 화면을 만질 때마다 오디오 잠금을 푼다. 만들어만 두고 resume 하지 않으면 잠긴 채로
        // 남아 알람이 안 울리고, 탭이 백그라운드에 갔다 오면 다시 잠기므로 한 번만 걸지 않는다.
        const unlock = () => {
            const ctx = getAudio();
            if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
        };
        document.addEventListener('click', unlock);
        document.addEventListener('touchstart', unlock, { passive: true });
    });
})();
