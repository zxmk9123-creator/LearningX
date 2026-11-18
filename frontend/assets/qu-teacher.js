/* Question-up · 교수자 흐름
   로그인 → 내 수업 → 교안 올리기 → 검토·승인 → 수업 중 보드 → 화면에 띄우기 → 종료·CSV
   토큰은 메모리에만 둡니다. 새로고침하면 Google 계정으로 다시 로그인합니다. */
(() => {
  'use strict';
  const { el, client, LEVEL, relTime } = window.QU;
  const $ = (id) => document.getElementById(id);

  const MAX_APPROVAL_ITEMS = 1000;
  let token = null;
  const state = {
    courses: [],          // [{...course, materials, openSession}]
    upload: null,         // {courseId, file, uploadId, timer}
    prepare: null,        // {course, material, candidates, approved}
    live: null,           // {session, level, timer, seen:Set, projectedId}
    tick: null,
  };

  const api = client(() => token, () => { token = null; go('login'); });

  /* ---------- helpers ---------- */
  function show(node, on = true) { node.hidden = !on; }
  function setError(node, err) {
    if (!err) { node.hidden = true; node.textContent = ''; return; }
    node.textContent = typeof err === 'string' ? err : err.message;
    node.hidden = false;
  }
  function globalError(err) { setError($('global-error'), err); }
  function busy(btn, on, label) {
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || '처리 중…'; btn.disabled = true; }
    else { if (btn.dataset.label) btn.textContent = btn.dataset.label; btn.disabled = false; }
  }
  function stopTimers() {
    if (state.live && state.live.timer) clearInterval(state.live.timer);
    if (state.upload && state.upload.timer) clearTimeout(state.upload.timer);
    if (state.tick) clearInterval(state.tick);
    state.tick = null;
  }
  function coachingAllowed(course) {
    return course.policy !== 'PROHIBITED' && (course.policy === 'PERMITTED' || course.teacher_approved);
  }

  /* ---------- routing ---------- */
  function go(route) {
    const target = '#' + route;
    if (location.hash === target) render(); else location.hash = target;
  }

  function header({ crumb, step, live, paused, back }) {
    show($('crumb'), !!crumb); show($('crumb-sep'), !!crumb);
    $('crumb').textContent = crumb || '';
    show($('stepper'), !!step);
    if (step) {
      for (const s of $('stepper').querySelectorAll('[data-step]')) {
        const n = Number(s.dataset.step);
        s.className = n < step ? 'done' : n === step ? 'on' : '';
      }
    }
    show($('live-chip'), !!live); show($('paused-chip'), !!paused);
    show($('to-courses'), !!back);
  }

  function screen(name) {
    for (const s of document.querySelectorAll('[data-screen]')) s.hidden = s.id !== 'screen-' + name;
    show($('prepare-bar'), name === 'prepare');
    show($('projector'), false);
    globalError(null);
    window.scrollTo(0, 0);
  }

  async function render() {
    stopTimers();
    const [name, a, b] = location.hash.replace(/^#/, '').split('/');
    if (!token && name !== 'login') { history.replaceState(null, '', '#login'); return renderLogin(); }
    try {
      switch (name) {
        case 'login': return renderLogin();
        case 'upload': return renderUpload(a);
        case 'prepare': return await renderPrepare(a, b);
        case 'live': return await renderLive(a);
        case 'end': return await renderEnd(a);
        default: return await renderCourses();
      }
    } catch (err) {
      globalError(err);
    }
  }
  window.addEventListener('hashchange', render);

  /* ---------- 1 · 초대 코드 ---------- */
  let googleButtonReady = false;
  let loadingGoogle = false;
  async function renderLogin() {
    if (token) return go('courses');
    header({});
    screen('login');
    if (googleButtonReady || loadingGoogle) return;
    loadingGoogle = true;
    const status = $('login-error');
    status.textContent = '로그인 준비 중…';
    try {
      const config = await api('/v1/faculty/auth/config');
      await new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://accounts.google.com/gsi/client';
        script.onload = resolve;
        script.onerror = () => { script.remove(); reject(new Error('Google 로그인 화면을 불러오지 못했습니다.')); };
        document.head.append(script);
      });
      window.google.accounts.id.initialize({
        client_id: config.client_id,
        callback: async ({ credential }) => {
          status.textContent = '교수자 계정 확인 중…';
          try {
            const result = await api('/v1/faculty/auth/google', { method: 'POST', body: { credential } });
            token = result.access_token;
            status.textContent = '';
            go('courses');
          } catch (err) {
            status.textContent = err.status === 403 ? '등록된 교수자 Google 계정으로 로그인해 주세요.' : err.message;
          }
        },
      });
      window.google.accounts.id.renderButton($('google-signin'), { theme: 'outline', size: 'large' });
      googleButtonReady = true;
      status.textContent = '';
    } catch (err) {
      status.textContent = err.status === 503 ? '교수자 Google 로그인 설정을 준비 중입니다.' : err.message;
    } finally {
      loadingGoogle = false;
    }
  }

  /* ---------- 3 · 내 수업 ---------- */
  async function loadCourses() {
    const { items } = await api('/v1/courses/mine');
    const mine = items.filter((c) => c.role === 'faculty');
    await Promise.all(mine.map(async (c) => {
      const [mats, sess] = await Promise.all([
        api(`/v1/courses/${c.id}/materials`).catch(() => ({ items: [] })),
        api(`/v1/courses/${c.id}/sessions`).catch(() => ({ items: [] })),
      ]);
      c.materials = mats.items;
      c.openSession = sess.items.find((s) => s.status !== 'CLOSED') || null;
      c.lastSession = sess.items[0] || null;
    }));
    state.courses = mine;
    return mine;
  }

  function courseAction(c) {
    if (c.openSession) {
      return { label: '수업 이어가기', cls: 'btn primary', href: `#live/${c.openSession.id}`,
        meta: c.openSession.status === 'PAUSED' ? '잠시 멈춤' : '질문 받는 중', chip: 'ok' };
    }
    const signed = c.materials.find((m) => m.status === 'FACULTY_SIGNED');
    const pending = c.materials.find((m) => m.status === 'PENDING_FACULTY_REVIEW');
    if (signed) return { label: '수업 시작', cls: 'btn primary', href: `#prepare/${c.id}/${signed.id}`, meta: '승인 완료', chip: 'ok' };
    if (pending) return { label: '검토하기', cls: 'btn', href: `#prepare/${c.id}/${pending.id}`, meta: '검토 대기', chip: 'warn' };
    return { label: '교안 올리기', cls: 'btn', href: `#upload/${c.id}`, meta: '교안 없음', chip: '' };
  }

  async function renderCourses() {
    header({ crumb: '내 수업' });
    screen('courses');
    const list = $('course-list');
    list.replaceChildren(el('div', { class: 'item' }, el('div', { class: 'progress indeterminate grow' }, el('span'))));
    const courses = await loadCourses();
    if (!courses.length) {
      list.replaceChildren(el('div', { class: 'item' },
        el('div', { class: 'grow' }, el('div', { class: 'title', text: '아직 준비한 수업이 없어요' }),
          el('div', { class: 'meta', text: '“+ 새 수업 준비”로 교안을 올리면 시작할 수 있습니다.' }))));
      return;
    }
    list.replaceChildren(...courses.map((c) => {
      const act = courseAction(c);
      const mat = c.materials[0];
      const last = c.lastSession && c.lastSession.status === 'CLOSED'
        ? el('a', { class: 'btn ghost sm', href: `#end/${c.lastSession.id}`, text: '지난 수업 결과' }) : null;
      return el('div', { class: 'item' },
        el('div', { class: 'grow' },
          el('div', { class: 'title', text: c.title }),
          el('div', { class: 'meta', text: mat ? `${mat.filename}${c.materials.length > 1 ? ` 외 ${c.materials.length - 1}개` : ''}` : '교안을 올려 주세요' })),
        el('span', { class: `chip ${act.chip}`, text: act.meta }),
        last,
        c.materials.length ? el('a', { class: 'btn ghost sm', href: `#upload/${c.id}`, text: '교안 추가' }) : null,
        el('a', { class: act.cls, href: act.href, text: act.label }));
    }));
  }

  $('new-course').addEventListener('click', () => go('upload'));

  /* ---------- 2 · 교안 올리기 ---------- */
  function renderUpload(courseId) {
    const course = courseId ? state.courses.find((c) => c.id === courseId) : null;
    header({ crumb: course ? course.title : '새 수업', step: 1, back: true });
    screen('upload');
    state.upload = { courseId: course ? course.id : null };
    show($('course-title-field'), !course);
    $('course-title').value = '';
    $('upload-file').value = '';
    $('upload-rights').checked = false;
    show($('upload-progress'), false);
    setError($('upload-error'), null);
    $('upload-submit').disabled = false;
    (course ? $('upload-file') : $('course-title')).focus();
  }

  function setUploadState(label, pct) {
    show($('upload-progress'), true);
    $('upload-state').textContent = label;
    const bar = $('upload-bar');
    bar.classList.toggle('indeterminate', pct === null);
    bar.firstElementChild.style.width = pct === null ? '' : `${pct}%`;
  }

  function putFile(url, headers, file) {
    // XHR gives upload progress; fetch does not. The presigned URL needs no token.
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', url);
      for (const [k, v] of Object.entries(headers || {})) xhr.setRequestHeader(k, v);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setUploadState('올리는 중', Math.round((e.loaded / e.total) * 100)); };
      xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`파일 전송에 실패했습니다. (${xhr.status})`)));
      xhr.onerror = () => reject(new Error('파일 전송 중 연결이 끊어졌습니다.'));
      xhr.send(file);
    });
  }

  const JOB_LABEL = { AWAITING_UPLOAD: '전송 대기', QUEUED: '분석 대기 중', RUNNING: '교안 분석 중', READY: '분석 완료', FAILED: '분석 실패' };

  async function pollUpload() {
    const u = state.upload;
    if (!u || !u.uploadId) return;
    try {
      const job = await api(`/v1/courses/${u.courseId}/uploads/${u.uploadId}`);
      setUploadState(JOB_LABEL[job.status] || job.status, job.status === 'READY' ? 100 : null);
      if (job.status === 'READY' && job.material_id) {
        u.timer = null;
        await loadCourses().catch(() => {});
        return go(`prepare/${u.courseId}/${job.material_id}`);
      }
      if (job.status === 'FAILED') {
        setError($('upload-error'), '교안을 분석하지 못했습니다. 다시 시도하거나 다른 파일을 올려 주세요.');
        const retry = $('upload-submit');
        retry.disabled = false; retry.textContent = '분석 다시 시도';
        u.failed = true;
        return;
      }
    } catch (err) {
      if (err.status === 401) return;
      setUploadState('상태 확인 재시도 중', null);
    }
    u.timer = setTimeout(pollUpload, 3000);
  }

  $('upload-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const u = state.upload;
    const btn = $('upload-submit');
    setError($('upload-error'), null);

    if (u.failed && u.uploadId) {          // retry analysis of the same file
      busy(btn, true, '다시 요청 중…');
      try {
        await api(`/v1/courses/${u.courseId}/uploads/${u.uploadId}/retry`, { method: 'POST' });
        u.failed = false; btn.textContent = '올리기';
        setUploadState('분석 대기 중', null);
        pollUpload();
      } catch (err) { setError($('upload-error'), err); busy(btn, false); }
      return;
    }

    const file = $('upload-file').files[0];
    const title = $('course-title').value.trim();
    if (!u.courseId && title.length < 2) return setError($('upload-error'), '수업 이름을 2자 이상 입력해 주세요.');
    if (!file) return setError($('upload-error'), '올릴 교안 파일을 골라 주세요.');
    if (!/\.(pdf|pptx)$/i.test(file.name)) return setError($('upload-error'), 'PDF 또는 PPTX 파일만 올릴 수 있습니다.');
    if (file.size > 100 * 1024 * 1024) return setError($('upload-error'), '100MB 이하 파일만 올릴 수 있습니다.');
    if (!$('upload-rights').checked) return setError($('upload-error'), '업로드 권한을 확인해 주세요.');

    busy(btn, true, '올리는 중…');
    $('upload-name').textContent = file.name;
    setUploadState('준비 중', 0);
    try {
      if (!u.courseId) {
        const course = await api('/v1/courses', { method: 'POST', body: { title, policy: 'RESTRICTED_HINT_ONLY', teacher_approved: false } });
        u.courseId = course.id;
        header({ crumb: title, step: 1, back: true });
        show($('course-title-field'), false);
      }
      const init = await api(`/v1/courses/${u.courseId}/uploads/initiate`, { method: 'POST', body: { filename: file.name, size_bytes: file.size } });
      u.uploadId = init.upload_id;
      await putFile(init.upload_url, init.headers, file);
      setUploadState('분석 요청 중', null);
      await api(`/v1/courses/${u.courseId}/uploads/${u.uploadId}/complete`, { method: 'POST' });
      btn.textContent = '분석 중…';
      pollUpload();
    } catch (err) {
      setError($('upload-error'), err);
      show($('upload-progress'), false);
      busy(btn, false);
    }
  });

  /* ---------- 4 · 검토·승인 ---------- */
  async function renderPrepare(courseId, materialId) {
    if (!state.courses.length) await loadCourses();
    const course = state.courses.find((c) => c.id === courseId);
    if (!course) return go('courses');
    const material = (course.materials || []).find((m) => m.id === materialId) || { id: materialId, filename: '' };
    header({ crumb: course.title, step: 2, back: true });
    screen('prepare');
    setError($('prepare-error'), null);
    const list = $('candidate-list');
    list.replaceChildren(el('div', { class: 'candidate' }, el('div', { class: 'progress indeterminate grow' }, el('span'))));

    const res = await api(`/v1/materials/${materialId}/candidates`, { timeout: 120000 });
    const isPptx = /\.pptx$/i.test(material.filename || '');
    state.prepare = { course, material, candidates: res.eligible_candidates, approved: res.approved_count, isPptx };
    $('cand-count').textContent = String(res.eligible_candidates.length);
    $('cand-approved').textContent = String(res.approved_count);
    show($('media-notice'), isPptx); show($('media-ack-row'), isPptx);
    $('media-ack').checked = false;
    $('ai-consent').checked = coachingAllowed(course);
    $('ai-consent').disabled = coachingAllowed(course);

    if (!res.eligible_candidates.length) {
      list.replaceChildren(el('div', { class: 'candidate' }, el('span', { class: 'tx muted',
        text: res.approved_count ? '새로 승인할 내용이 없습니다. 이미 승인한 내용으로 수업을 시작할 수 있어요.'
          : '코칭에 쓸 수 있는 텍스트를 찾지 못했습니다. 텍스트가 들어 있는 PDF·PPTX인지 확인해 주세요.' })));
    } else {
      list.replaceChildren(...res.eligible_candidates.map((c, i) => el('label', { class: 'candidate' },
        el('input', { type: 'checkbox', 'data-index': i, onchange: updateSelection }),
        el('span', { class: 'pg', text: `${c.page}쪽` }),
        el('span', { class: 'tx', text: c.preview || '(내용 없음)' }))));
    }
    updateSelection();
  }

  function selectedItems() {
    const p = state.prepare;
    return [...$('candidate-list').querySelectorAll('input[type=checkbox]:checked')]
      .map((box) => p.candidates[Number(box.dataset.index)])
      .map(({ page, block_id, block_sha256 }) => ({ page, block_id, block_sha256 }));
  }

  function updateSelection() {
    const p = state.prepare;
    if (!p) return;
    const items = selectedItems();
    const pages = new Set(items.map((x) => x.page)).size;
    $('cand-selected').textContent = String(items.length);
    $('bar-count').textContent = items.length ? `${pages}쪽 ${items.length}개 내용` : p.approved ? `승인된 ${p.approved}개 내용` : '0개 내용';
    const all = $('candidate-list').querySelectorAll('input[type=checkbox]');
    $('select-all').textContent = items.length === Math.min(all.length, MAX_APPROVAL_ITEMS) && items.length ? '선택 해제' : all.length > MAX_APPROVAL_ITEMS ? '최대 1,000개 선택' : '모두 선택';
    $('select-all').disabled = !all.length;
    $('approve-start').textContent = items.length ? '승인하고 수업 시작' : '수업 시작';
    $('approve-start').disabled = items.length > MAX_APPROVAL_ITEMS || (!items.length && !p.approved);
    if (items.length > MAX_APPROVAL_ITEMS) $('bar-count').textContent = '한 번에 최대 1,000개까지 승인할 수 있습니다.';
  }

  $('select-all').addEventListener('click', () => {
    const boxes = [...$('candidate-list').querySelectorAll('input[type=checkbox]')];
    const on = !boxes.slice(0, MAX_APPROVAL_ITEMS).every((b) => b.checked);
    boxes.forEach((b, index) => { b.checked = on && index < MAX_APPROVAL_ITEMS; });
    updateSelection();
  });

  $('approve-start').addEventListener('click', async () => {
    const p = state.prepare;
    const btn = $('approve-start');
    setError($('prepare-error'), null);
    const items = selectedItems();
    if (items.length > MAX_APPROVAL_ITEMS) return setError($('prepare-error'), '한 번에 최대 1,000개까지 승인할 수 있습니다.');
    if (!$('ai-consent').checked) return setError($('prepare-error'), 'AI 질문 코칭 사용에 동의해야 수업을 시작할 수 있습니다.');
    if (items.length && p.isPptx && !$('media-ack').checked) return setError($('prepare-error'), '분석되지 않은 오디오·도표 안내를 확인해 주세요.');
    busy(btn, true, '시작하는 중…');
    try {
      if (!coachingAllowed(p.course)) {
        const policy = p.course.policy === 'PROHIBITED' ? 'RESTRICTED_HINT_ONLY' : p.course.policy;
        const res = await api(`/v1/courses/${p.course.id}/policy`, { method: 'PUT', body: { policy, teacher_approved: true } });
        Object.assign(p.course, { policy: res.policy, teacher_approved: res.teacher_approved });
      }
      if (items.length) {
        await api(`/v1/materials/${p.material.id}/approve`, { method: 'POST', timeout: 120000,
          body: { items, acknowledge_unprocessed_media: p.isPptx && $('media-ack').checked } });
      }
      const session = await api(`/v1/courses/${p.course.id}/sessions`, { method: 'POST', body: { material_id: p.material.id } });
      go(`live/${session.id}`);
    } catch (err) {
      if (err.status === 409 && /narration/i.test(err.detail)) {
        show($('media-ack-row'), true); show($('media-notice'), true); p.isPptx = true;
        setError($('prepare-error'), '이 PPTX에는 분석되지 않은 내레이션이 있습니다. 확인란에 체크한 뒤 다시 시도해 주세요.');
      } else {
        setError($('prepare-error'), err);
      }
      busy(btn, false);
    }
  });

  /* ---------- 5–6 · 수업 중 보드 ---------- */
  async function renderLive(sessionId) {
    const session = await api(`/v1/sessions/${sessionId}`);
    if (session.status === 'CLOSED') return go(`end/${sessionId}`);
    const prev = state.live && state.live.session.id === sessionId ? state.live : null;
    state.live = { session, level: prev ? prev.level : 'all', seen: prev ? prev.seen : null, timer: null };
    screen('live');
    paintLiveHeader();
    $('live-code').textContent = session.join_code || '––––––';
    $('live-url').textContent = location.host + location.pathname.replace(/teacher\.html$/, '');
    setFilter(state.live.level);
    await refreshBoard();
    state.live.timer = setInterval(refreshBoard, 5000);
  }

  function paintLiveHeader() {
    const s = state.live.session;
    header({ crumb: s.course_title, step: 3, live: s.status === 'OPEN', paused: s.status === 'PAUSED', back: true });
    $('pause-toggle').textContent = s.status === 'PAUSED' ? '질문 다시 받기' : '질문 받기 멈춤';
    $('live-joined').textContent = `${s.participants || 0}명`;
  }

  function setFilter(level) {
    state.live.level = level;
    for (const b of $('level-filter').querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.level === level));
  }

  $('level-filter').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-level]');
    if (!b || !state.live) return;
    setFilter(b.dataset.level);
    refreshBoard();
  });

  async function refreshBoard() {
    const live = state.live;
    if (!live) return;
    let data;
    try {
      [data, live.session] = await Promise.all([
        api(`/v1/sessions/${live.session.id}/board?level=${live.level}&include_hidden=true`),
        api(`/v1/sessions/${live.session.id}`),
      ]);
    } catch (err) {
      if (err.status !== 401) globalError(err);
      return;
    }
    globalError(null);
    if (live.session.status === 'CLOSED') return go(`end/${live.session.id}`);
    paintLiveHeader();
    const visible = data.items.filter((x) => !x.hidden);
    for (const k of ['all', 'deep', 'mid', 'shallow']) {
      const n = $('level-filter').querySelector(`[data-count="${k}"]`);
      if (n) n.textContent = String(data.counts[k] || 0);
    }
    $('live-total').textContent = `${data.counts.all || 0}개`;

    const firstLoad = live.seen === null;
    live.seen = live.seen || new Set();
    const board = $('board');
    if (!data.items.length) {
      board.replaceChildren(el('div', { class: 'empty' },
        live.level === 'all' ? '아직 들어온 질문이 없어요. 학생들이 코드로 입장해 질문을 다듬고 보내면 여기에 나타납니다.'
          : `${LEVEL[live.level]} 수준 질문이 아직 없어요.`));
      return;
    }
    // Keep hidden ones at the end, faded, so they can be restored.
    const ordered = [...visible, ...data.items.filter((x) => x.hidden)];
    board.replaceChildren(...ordered.map((q) => {
      const isNew = !firstLoad && !live.seen.has(q.id);
      live.seen.add(q.id);
      return questionCard(q, isNew);
    }));
  }

  function questionCard(q, isNew) {
    const cls = ['qcard', q.projected ? 'projected' : '', isNew ? 'is-new' : '', q.hidden ? 'is-hidden' : ''].filter(Boolean).join(' ');
    return el('article', { class: cls },
      el('span', { class: `edge ${q.level}` }),
      el('div', { class: 'body' },
        el('p', { class: 'q', text: q.text }),
        q.original ? el('p', { class: 'orig', text: `처음 질문: ${q.original}` }) : null,
        el('div', { class: 'meta' },
          el('span', { class: `lv ${q.level}`, text: `${q.level_label} 질문` }),
          q.original ? el('span', { text: '다시 씀' }) : null,
          el('span', { text: relTime(q.submitted_at) }),
          q.hidden ? el('span', { text: '숨김' }) : null,
          el('div', { class: 'acts' },
            q.hidden
              ? el('button', { class: 'btn sm', type: 'button', text: '다시 보이기', onclick: () => act(`/v1/session-questions/${q.id}/unhide`) })
              : el('button', { class: 'btn ghost sm', type: 'button', text: '숨기기', onclick: () => act(`/v1/session-questions/${q.id}/hide`) }),
            q.hidden ? null : el('button', { class: 'btn dark sm', type: 'button', text: '화면에 띄우기', onclick: () => project(q.id) })))));
  }

  async function act(path) {
    try { await api(path, { method: 'POST' }); await refreshBoard(); } catch (err) { globalError(err); }
  }

  $('pause-toggle').addEventListener('click', async () => {
    const s = state.live.session;
    const btn = $('pause-toggle');
    busy(btn, true);
    try {
      state.live.session = await api(`/v1/sessions/${s.id}/state`, { method: 'PUT', body: { status: s.status === 'PAUSED' ? 'OPEN' : 'PAUSED' } });
      busy(btn, false);
      paintLiveHeader();
    } catch (err) { busy(btn, false); globalError(err); }
  });

  $('end-class').addEventListener('click', async () => {
    const btn = $('end-class');
    if (btn.dataset.confirm !== '1') {        // two-step confirm without a modal dialog
      btn.dataset.confirm = '1'; btn.textContent = '정말 종료할까요?'; btn.classList.add('dark');
      setTimeout(() => { btn.dataset.confirm = ''; btn.textContent = '수업 종료'; btn.classList.remove('dark'); }, 4000);
      return;
    }
    busy(btn, true, '종료 중…');
    try {
      const s = await api(`/v1/sessions/${state.live.session.id}/state`, { method: 'PUT', body: { status: 'CLOSED' } });
      go(`end/${s.id}`);
    } catch (err) { busy(btn, false); globalError(err); }
  });

  /* ---------- 7 · 화면에 띄우기 ---------- */
  const proj = $('projector');

  function openProjector() {
    proj.hidden = false;
    $('proj-code').textContent = state.live.session.join_code || '';
    if (proj.requestFullscreen && !document.fullscreenElement) proj.requestFullscreen().catch(() => {});
  }
  function closeProjector() {
    proj.hidden = true;
    proj.classList.remove('code-mode');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    refreshBoard();
  }

  async function project(id) {
    try {
      await api(`/v1/session-questions/${id}/project`, { method: 'POST' });
      const data = await api(`/v1/sessions/${state.live.session.id}/projected`);
      paintProjected(data);
      openProjector();
    } catch (err) { globalError(err); }
  }

  function paintProjected(data) {
    proj.classList.remove('code-mode');
    const item = data.item;
    $('proj-label').textContent = item ? `${item.level_label} 질문` : '학생 질문';
    $('proj-text').textContent = item ? item.text : '띄울 질문이 없습니다.';
    $('proj-meta').textContent = item && item.original ? `처음 질문: ${item.original}` : '이름 없이 들어온 질문입니다.';
    state.live.nextId = data.next_id || null;
    $('proj-next').disabled = !data.next_id;
  }

  $('proj-next').addEventListener('click', () => { if (state.live.nextId) project(state.live.nextId); });
  $('proj-close').addEventListener('click', closeProjector);
  $('code-full').addEventListener('click', () => {
    proj.classList.add('code-mode');
    $('proj-label').textContent = '입장 코드';
    $('proj-text').textContent = state.live.session.join_code || '';
    $('proj-meta').textContent = `${location.host}${location.pathname.replace(/teacher\.html$/, '')} 에서 입력하세요`;
    $('proj-next').disabled = true;
    openProjector();
  });
  document.addEventListener('keydown', (e) => {
    if (proj.hidden) return;
    if (e.key === 'Escape') closeProjector();
    if ((e.key === 'ArrowRight' || e.key === ' ') && state.live && state.live.nextId) { e.preventDefault(); project(state.live.nextId); }
  });
  document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && !proj.hidden) closeProjector(); });

  /* ---------- 8 · 종료 ---------- */
  async function renderEnd(sessionId) {
    const s = await api(`/v1/sessions/${sessionId}`);
    if (s.status !== 'CLOSED') return go(`live/${sessionId}`);
    header({ crumb: s.course_title, back: true });
    screen('end');
    state.endSession = s;
    const stat = (k, v, accent) => el('div', { class: accent ? 'stat accent' : 'stat' }, el('div', { class: 'k', text: k }), el('div', { class: 'v', text: v }));
    $('end-stats').replaceChildren(
      stat('입장한 학생', `${s.participants}명`),
      stat('받은 질문', `${s.questions_submitted}개`),
      stat('깊은 질문', `${s.questions_deep}개`, true),
      stat('다시 쓴 질문', `${s.questions_revised}개`),
      stat('화면에 띄움', `${s.questions_projected}개`));
  }

  $('export-csv').addEventListener('click', async () => {
    const s = state.endSession;
    const btn = $('export-csv');
    busy(btn, true, '준비 중…');
    try {
      const res = await api(`/v1/sessions/${s.id}/export`, { raw: true });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = el('a', { href: url, download: `question-up-${s.created_at.slice(0, 10)}.csv` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { globalError(err); }
    finally { busy(btn, false); }
  });

  render();
})();
