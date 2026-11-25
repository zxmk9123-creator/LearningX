/* Question-up shared API client. Tokens live in memory only: no storage, no cookies. */
(() => {
  'use strict';
  const DEFAULT_API = 'https://p01--learningx-shared-api--rdbm26q7ps85.code.run';

  function apiBase() {
    // Production accepts only the reviewed shared API. Local overrides require a
    // local frontend as well, so a crafted public URL cannot collect invite codes.
    const fromQuery = new URLSearchParams(location.search).get('api');
    if (fromQuery) {
      try {
        const parsed = new URL(fromQuery);
        const local = ['localhost', '127.0.0.1'];
        if (parsed.origin === DEFAULT_API && parsed.pathname === '/' && !parsed.search && !parsed.hash && !parsed.username && !parsed.password) return DEFAULT_API;
        if (local.includes(location.hostname) && local.includes(parsed.hostname) &&
            ['http:', 'https:'].includes(parsed.protocol) && parsed.pathname === '/' &&
            !parsed.search && !parsed.hash && !parsed.username && !parsed.password) return parsed.origin;
      } catch (_) { /* Invalid override: use the reviewed shared API. */ }
    }
    return DEFAULT_API;
  }

  const MESSAGES = {
    401: '로그인이 만료됐습니다. 다시 입장해 주세요.',
    403: '이 작업을 할 권한이 없습니다.',
    404: '찾을 수 없습니다.',
    409: '지금 상태에서는 할 수 없는 작업입니다.',
    413: '파일이 너무 큽니다.',
    415: 'PDF 또는 PPTX 파일만 올릴 수 있습니다.',
    423: '지금은 질문을 받지 않는 중입니다.',
    429: '시도가 너무 많습니다. 1분 뒤 다시 시도해 주세요.',
    503: '서버가 이 작업을 처리할 준비가 되지 않았습니다. 잠시 뒤 다시 시도해 주세요.',
  };

  class ApiError extends Error {
    constructor(status, detail) {
      super(MESSAGES[status] || `서버 요청에 실패했습니다. (${status})`);
      this.status = status;
      this.detail = detail || '';
    }
  }

  function client(getToken, onUnauthorized) {
    const base = apiBase();
    return async function request(path, { method = 'GET', body, raw = false, readBody, onResponse, timeout = 60000, signal } = {}) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      // A caller may cancel a request it no longer needs (for example a parked live poll).
      const cancel = () => controller.abort();
      if (signal?.aborted) cancel();
      else signal?.addEventListener('abort', cancel, { once: true });
      const abortReason = () => (signal?.aborted ? 'cancelled' : 'timeout');
      try {
        const headers = {};
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        const token = getToken && getToken();
        if (token) headers.Authorization = 'Bearer ' + token;
        let response;
        try {
          response = await fetch(base + path, {
            method, headers, signal: controller.signal, credentials: 'omit', mode: 'cors',
            cache: 'no-store', redirect: 'error',
            body: body === undefined ? undefined : JSON.stringify(body),
          });
        } catch (err) {
          throw new ApiError(0, err && err.name === 'AbortError' ? abortReason() : 'network');
        }
        if (!response.ok) {
          let detail = '';
          try { detail = (await response.json()).detail || ''; } catch (_) { /* ignore */ }
          if (response.status === 401 && onUnauthorized) onUnauthorized();
          throw new ApiError(response.status, detail);
        }
        onResponse?.(response);
        if (readBody) {
          try {
            return await readBody(response);
          } catch (err) {
            if (controller.signal.aborted) throw new ApiError(0, abortReason());
            throw err;
          }
        }
        if (raw) return response;
        return await response.json();
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', cancel);
      }
    };
  }

  // Expiry (ms) read from a JWT's payload; the server still verifies every token.
  function tokenExpiry(token) {
    try {
      const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
      const exp = JSON.parse(atob(part + '='.repeat((4 - part.length % 4) % 4))).exp;
      return Number.isFinite(exp) ? exp * 1000 : null;
    } catch (_) { return null; }
  }

  // Renews the in-memory token while the page is open, so a class that runs past the
  // token lifetime keeps polling and publishing instead of dropping to sign-in.
  const RENEW_BEFORE = 15 * 60 * 1000;
  function keepTokenFresh({ getToken, setToken, onLost }) {
    const renewApi = client(getToken);  // no 401 redirect: a failed renewal reports through onLost
    let timer = null;
    let running = null;
    function schedule() {
      clearTimeout(timer);
      const token = getToken();
      const exp = token && tokenExpiry(token);
      if (!exp) return;
      timer = setTimeout(renew, Math.max(5000, exp - RENEW_BEFORE - Date.now()));
    }
    function renew() {
      if (!getToken()) return Promise.resolve(false);
      running ??= (async () => {
        const sent = getToken();
        try {
          const res = await renewApi('/v1/auth/refresh', { method: 'POST', timeout: 15000 });
          if (getToken() === sent) setToken(res.access_token);
          schedule();
          return true;
        } catch (err) {
          if (getToken() !== sent) return false;  // signed out or replaced meanwhile
          if (err.status === 401) { onLost?.(); return false; }
          clearTimeout(timer);
          timer = setTimeout(renew, 30000);  // network or server hiccup: try again soon
          return false;
        } finally { running = null; }
      })();
      return running;
    }
    // Timers stall while a laptop sleeps; check again when the page comes back.
    const wake = () => {
      const exp = getToken() && tokenExpiry(getToken());
      if (exp && document.visibilityState !== 'hidden' && exp - Date.now() < RENEW_BEFORE) renew();
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    window.addEventListener('focus', wake);
    return { schedule, renew, stop: () => clearTimeout(timer) };
  }

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const LEVEL = { shallow: '얕은', mid: '중간', deep: '깊은', unassessed: '판정 보류' };
  // A question whose judgment failed (fallback coaching) has no level to show.
  const levelText = (level) => (level === 'unassessed' ? '수준 판정 보류' : `${LEVEL[level]} 질문`);

  function relTime(iso) {
    if (!iso) return '';
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return '방금';
    if (s < 3600) return Math.floor(s / 60) + '분 전';
    return Math.floor(s / 3600) + '시간 전';
  }

  const traceEnabled = new URLSearchParams(location.search).get('liveTiming') === '1';
  function liveMark(stage, version, page, startTime = performance.now(), serverTiming) {
    if (!traceEnabled || !version) return;
    performance.mark('question-up:' + stage, {
      startTime,
      detail: {version, page, epochMs: performance.timeOrigin + startTime, ...(serverTiming ? {serverTiming} : {})},
    });
  }

  // AI is answering: bouncing dots, a sweeping bar and a short line that moves through what
  // the coach is doing. Screen readers hear the line once (polite status); motion stops for
  // reduced-motion users (qu.css). stop() clears the timer; the caller removes the node.
  const THINKING_STEPS = ['질문을 읽고 있어요', '교안에서 근거를 찾고 있어요', '질문의 수준을 살피고 있어요', '되물을 질문을 고르고 있어요'];
  const THINKING_SLOW = '조금만 더 기다려 주세요. 꼼꼼히 살피는 중이에요';
  function thinking({ steps = THINKING_STEPS, every = 1800, slowAfter = 12000, avatar = true } = {}) {
    const label = el('span', { class: 'thinking-label', text: steps[0] });
    const body = el('div', { class: 'bubble' },
      el('span', { class: 'thinking-dots', 'aria-hidden': 'true' }, el('i'), el('i'), el('i')),
      label,
      el('span', { class: 'thinking-bar', 'aria-hidden': 'true' }));
    const node = avatar
      ? el('div', { class: 'msg thinking', role: 'status', 'aria-live': 'polite' },
        el('span', { class: 'av', 'aria-hidden': 'true', text: 'Q' }), body)
      : el('div', { class: 'thinking solo', role: 'status', 'aria-live': 'polite' }, body);
    const started = Date.now();
    let step = 0;
    const show = (text) => {
      if (label.textContent === text) return;
      label.classList.add('swap');
      setTimeout(() => { label.textContent = text; label.classList.remove('swap'); }, 160);
    };
    const timer = setInterval(() => {
      if (Date.now() - started >= slowAfter) { show(THINKING_SLOW); return; }
      step = Math.min(step + 1, steps.length - 1);
      show(steps[step]);
    }, every);
    node.stop = () => clearInterval(timer);
    return node;
  }

  window.QU = { apiBase, client, ApiError, keepTokenFresh, tokenExpiry, el, LEVEL, levelText, relTime, liveMark, thinking, liveTimingEnabled: traceEnabled };
})();
