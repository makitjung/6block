// 6block 클라이언트 - 30분 슬롯 경계와 동기화되는 25/5 포모도로, 카테고리 띠, PWA 등록
(function () {
    'use strict';

    const FOCUS_SEC = 25 * 60;
    const BREAK_SEC = 5 * 60;
    const TICK_MS = 1000;

    const state = {
        phase: 'IDLE',      // 'IDLE' | 'FOCUS' | 'BREAK'
        startedAt: 0,       // epoch ms
        slotStart: '',      // 'HH:MM'
        auto: localStorage.getItem('pomoAuto') === 'true',
    };

    // ---- storage ---------------------------------------------------------
    function persist() {
        localStorage.setItem('pomoState', JSON.stringify({
            phase: state.phase, startedAt: state.startedAt, slotStart: state.slotStart,
        }));
        localStorage.setItem('pomoAuto', String(state.auto));
    }
    function restore() {
        try {
            const raw = JSON.parse(localStorage.getItem('pomoState') || '{}');
            if (raw.phase) {
                state.phase = raw.phase;
                state.startedAt = raw.startedAt;
                state.slotStart = raw.slotStart;
                // 만료된 세션은 즉시 정리
                const total = raw.phase === 'FOCUS' ? FOCUS_SEC
                            : raw.phase === 'BREAK' ? BREAK_SEC : 0;
                if (total && (Date.now() - raw.startedAt) / 1000 >= total + 60) {
                    state.phase = 'IDLE';
                }
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
    function startFocus(slotTime) {
        state.phase = 'FOCUS';
        state.startedAt = Date.now();
        state.slotStart = slotTime || currentSlotHHMM();
        persist();
        chime(1, 880);
        toast(`집중 시작 · ${state.slotStart}`);
        render();
    }
    function transitionToBreak() {
        state.phase = 'BREAK';
        state.startedAt = Date.now();
        persist();
        chime(2, 660);
        notify('휴식 시간', '5분 휴식 시작');
        toast('휴식 시작');
        render();
    }
    function transitionToIdle(auto) {
        state.phase = 'IDLE';
        persist();
        chime(3, 980);
        notify('30분 슬롯 완료', auto ? '자동 모드: 다음 슬롯 대기' : '잘했어!');
        toast('슬롯 완료');
        render();
    }
    function skip() {
        if (state.phase === 'FOCUS') transitionToBreak();
        else if (state.phase === 'BREAK') transitionToIdle(false);
    }
    function stop() {
        state.phase = 'IDLE';
        state.startedAt = 0;
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
        } else {
            const elapsed = (Date.now() - state.startedAt) / 1000;
            if (state.phase === 'FOCUS' && elapsed >= FOCUS_SEC) transitionToBreak();
            else if (state.phase === 'BREAK' && elapsed >= BREAK_SEC) transitionToIdle(state.auto);
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

        // pomo card
        const pomo = document.getElementById('pomo');
        if (pomo) {
            pomo.classList.toggle('active', state.phase !== 'IDLE' || state.auto);
            pomo.classList.toggle('focus', state.phase === 'FOCUS');
            pomo.classList.toggle('break', state.phase === 'BREAK');
            const autoBtn = pomo.querySelector('.pomo-auto');
            if (autoBtn) autoBtn.classList.toggle('on', state.auto);

            const phaseLabel = state.phase === 'FOCUS' ? '집중'
                              : state.phase === 'BREAK' ? '휴식'
                              : (state.auto ? '대기 (자동)' : '대기');
            const phaseEl = pomo.querySelector('.pomo-phase');
            if (phaseEl) phaseEl.textContent = phaseLabel;

            const timeEl = pomo.querySelector('.pomo-time');
            const barEl = pomo.querySelector('.pomo-bar');
            const slotEl = pomo.querySelector('.pomo-slot');
            if (state.phase === 'IDLE') {
                if (timeEl) timeEl.textContent = state.auto ? 'AUTO' : '—';
                if (barEl) barEl.style.width = '0%';
                if (slotEl) slotEl.textContent = state.auto
                    ? `다음 시작 · ${nextBoundary()}` : '';
            } else {
                const total = state.phase === 'FOCUS' ? FOCUS_SEC : BREAK_SEC;
                const remain = total - (Date.now() - state.startedAt) / 1000;
                if (timeEl) timeEl.textContent = fmt(remain);
                if (barEl) barEl.style.width =
                    Math.min(100, Math.max(0, (1 - remain / total) * 100)) + '%';
                if (slotEl) slotEl.textContent = `슬롯 ${state.slotStart}`;
            }
        }

        // highlight current-time slot row
        const cur = currentSlotHHMM();
        document.querySelectorAll('.slot').forEach((row) => {
            const t = row.dataset.start;
            const isNow = t === cur;
            row.classList.toggle('is-now', isNow);
            row.classList.toggle('is-pomo-focus', isNow && state.phase === 'FOCUS' && state.slotStart === t);
            row.classList.toggle('is-pomo-break', isNow && state.phase === 'BREAK' && state.slotStart === t);
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
    function paintCategory(sel) {
        const opt = sel.options[sel.selectedIndex];
        const color = (opt && opt.dataset) ? opt.dataset.color : '';
        const row = sel.closest('.slot');
        if (row) row.style.setProperty('--row-accent', color || 'transparent');
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

    // ---- init ------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', () => {
        restore();

        document.querySelectorAll('.slot select.cat-select').forEach((sel) => {
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
            pomo.querySelector('.pomo-start')?.addEventListener('click', () => {
                ensureNotifPermission();
                startFocus(currentSlotHHMM());
            });
            pomo.querySelector('.pomo-skip')?.addEventListener('click', () => skip());
            pomo.querySelector('.pomo-stop')?.addEventListener('click', () => stop());
            pomo.querySelector('.pomo-auto')?.addEventListener('click', () => toggleAuto());
        }

        bindForm();
        render();
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
