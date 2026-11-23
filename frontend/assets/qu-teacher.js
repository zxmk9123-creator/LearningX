/* Question-up · 교수자 흐름
   체험 시작 → 내 수업 → 교안 올리기 → 검토·승인 → 수업 중 보드 → 화면에 띄우기 → 종료·CSV
   토큰은 메모리에만 둡니다. 새로고침하면 현재 체험 수업 관리 세션이 종료됩니다. */
(() => {
  'use strict';
  const { el, client, LEVEL, levelText, relTime, liveMark, liveTimingEnabled } = window.QU;
  const $ = (id) => document.getElementById(id);

  const MAX_APPROVAL_ITEMS = 1000;
  let token = null;
  const state = {
    courses: [],          // [{...course, materials, openSession}]
    upload: null,         // {courseId, file, uploadId, timer}
    prepare: null,        // {course, material, candidates, approved}
    live: null,           // {session, level, items, seen, timer}
    tick: null,
  };

  const api = client(() => token, () => { token = null; go('login'); });
  // Sign-in exchange: no token and no 401 redirect (a failed exchange must not re-enter the login screen).
  const authApi = client(() => null);
  // Unit learning objective step, shown after evidence approval and right before the class opens.
  const objectiveRules = window.QU.objectiveRules;
  const objectiveForm = window.QU.createObjectiveForm({ api, root: $('objective-form'), status: $('objective-status'),
    onState: paintObjectiveBar });

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
  let liveView;
  let uploadedPdf;
  let openedSession;
  function stopTimers() {
    liveView?.stop();
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
    show($('objective-bar'), name === 'objective');
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
        case 'objective': return await renderObjective(a, b);
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
  async function renderLogin() {
    if (token) return go('courses');
    header({});
    screen('login');
    $('guest-start').focus();
    // Back from Google sign-in (or sent here by the student page): finish the exchange.
    if (await resumeGoogle()) go('courses');
  }

  $('guest-start').addEventListener('click', async () => {
    const button = $('guest-start');
    setError($('login-error'), null);
    busy(button, true, '준비 중…');
    try {
      const result = await api('/v1/faculty/guest', { method: 'POST', body: {} });
      token = result.access_token;
      go('upload');
    } catch (err) {
      setError($('login-error'), err.status === 429 ? '오늘 열 수 있는 체험 수업이 모두 사용됐습니다.' : err);
    } finally {
      busy(button, false);
    }
  });

  /* ---------- 학교 Google 로그인 (Neon Auth) ---------- */
  // First sign-in picks a role once; later sign-ins are classified from the database.
  const google = window.QU.createGoogleLogin({ api: authApi });

  async function finishGoogle(result) {
    if (!result) return false;
    if (result.needsRole) {
      $('google-role-email').textContent = google.roleIntro(result);
      show($('google-role'), true);
      $('google-role').querySelector('.role-card').focus();
      return false;
    }
    show($('google-role'), false);
    if (result.role === 'student') { google.goTo('student.html', 'student'); return false; }
    token = result.access_token;
    google.clearReturnParams();
    return true;
  }

  async function resumeGoogle() {
    if (!google.returning()) return false;
    const button = $('google-start');
    busy(button, true, '로그인 확인 중…');
    try {
      return await finishGoogle(await google.exchange());
    } catch (err) {
      setError($('login-error'), err);
      return false;
    } finally { busy(button, false); }
  }

  $('google-start').addEventListener('click', async () => {
    const button = $('google-start');
    setError($('login-error'), null);
    busy(button, true, 'Google로 이동 중…');
    try {
      // A live Neon session signs in without leaving the page (for example after the 60-minute token expired).
      if (await google.session()) {
        if (await finishGoogle(await google.exchange())) return go('courses');
        return busy(button, false);
      }
      await google.start();
    } catch (err) {
      setError($('login-error'), err);
      busy(button, false);
    }
  });

  $('google-role').addEventListener('click', async (event) => {
    const card = event.target.closest('[data-role]');
    if (!card) return;
    setError($('login-error'), null);
    $('google-role').querySelectorAll('[data-role]').forEach((b) => { b.disabled = true; });
    try {
      if (await finishGoogle(await google.exchange(card.dataset.role))) go('courses');
    } catch (err) {
      setError($('login-error'), err);
    } finally {
      $('google-role').querySelectorAll('[data-role]').forEach((b) => { b.disabled = false; });
    }
  });

  $('google-cancel').addEventListener('click', async () => {
    show($('google-role'), false);
    await google.signOut();
    $('google-start').focus();
  });

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
        meta: c.openSession.status === 'PAUSED' ? '잠시 멈춤' : c.openSession.chat_open ? '질문 받는 중' : '채팅 잠김', chip: 'ok' };
    }
    const signed = c.materials.find((m) => m.status === 'FACULTY_SIGNED');
    const pending = c.materials.find((m) => m.status === 'PENDING_FACULTY_REVIEW');
    if (signed) return { label: '수업 시작', cls: 'btn primary', href: `#objective/${c.id}/${signed.id}`, meta: '승인 완료', chip: 'ok' };
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
    $('upload-file').removeAttribute('aria-invalid');
    $('upload-picker').classList.remove('is-dragging');
    paintPicker();
    $('upload-rights').checked = false;
    show($('upload-progress'), false);
    setError($('upload-error'), null);
    $('upload-submit').disabled = false;
    (course ? $('upload-file') : $('course-title')).focus();
  }

  function paintPicker(file) {
    $('upload-picker').classList.toggle('has-file', Boolean(file));
    $('upload-picker-name').textContent = file?.name ?? '교안을 여기에 놓으세요';
    $('upload-picker-meta').textContent = file
      ? (file.size >= 1024 * 1024 ? (file.size / (1024 * 1024)).toFixed(1) + ' MB' : Math.ceil(file.size / 1024) + ' KB')
      : 'PDF 최대 60MB · PPTX 최대 40MB';
    $('upload-picker-action').textContent = file ? '파일 변경' : '파일 선택';
  }

  function fileError(file) {
    if (!file) return '올릴 교안 파일을 골라 주세요.';
    if (!/\.(pdf|pptx)$/i.test(file.name)) return 'PDF 또는 PPTX 파일만 올릴 수 있습니다.';
    const maxMb = /\.pdf$/i.test(file.name) ? 60 : 40;
    if (file.size > maxMb * 1024 * 1024) return maxMb + 'MB 이하 파일만 올릴 수 있습니다.';
    return null;
  }

  function chooseFile(files) {
    const upload = state.upload;
    if (!upload || $('upload-submit').disabled || !files.length) return;
    const file = files[0];
    const error = files.length > 1 ? '교안 파일을 한 번에 하나만 선택해 주세요.' : fileError(file);
    setError($('upload-error'), error);
    $('upload-file').setAttribute('aria-invalid', String(Boolean(error)));
    upload.selection = error ? null : file;
    upload.failed = false;
    upload.uploadId = null;
    $('upload-submit').textContent = '올리기';
    show($('upload-progress'), false);
    paintPicker(upload.selection);
  }

  const picker = $('upload-picker');
  const fileInput = $('upload-file');
  picker.addEventListener('click', event => { if ($('upload-submit').disabled) event.preventDefault(); });
  fileInput.addEventListener('change', () => {
    chooseFile(fileInput.files);
    fileInput.value = '';
  });
  for (const eventName of ['dragenter', 'dragover']) {
    picker.addEventListener(eventName, event => {
      if (!event.dataTransfer?.types.includes('Files')) return;
      event.preventDefault();
      const busy = $('upload-submit').disabled;
      event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
      picker.classList.toggle('is-dragging', !busy);
    });
  }
  picker.addEventListener('dragleave', event => {
    if (!picker.contains(event.relatedTarget)) picker.classList.remove('is-dragging');
  });
  picker.addEventListener('drop', event => {
    event.preventDefault();
    picker.classList.remove('is-dragging');
    if (event.dataTransfer) chooseFile(event.dataTransfer.files);
  });

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
        if (u.file && /\.pdf$/i.test(u.file.name)) uploadedPdf = {material: job.material_id, file: u.file};
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

    const file = u.selection;
    const title = $('course-title').value.trim();
    if (!u.courseId && title.length < 2) return setError($('upload-error'), '수업 이름을 2자 이상 입력해 주세요.');
    const problem = fileError(file);
    if (problem) return setError($('upload-error'), problem);
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
      u.file = file;
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

  /* ---------- 3 · 학습목표 ---------- */
  async function renderObjective(courseId, materialId) {
    if (!state.courses.length) await loadCourses();
    const course = state.courses.find((c) => c.id === courseId);
    if (!course) return go('courses');
    let material = (course.materials || []).find((m) => m.id === materialId);
    if (!material) material = ((await loadCourses()).find((c) => c.id === courseId)?.materials || []).find((m) => m.id === materialId);
    // The preview and the class both coach from approved evidence, so approval (step 2) comes first.
    if (!material || material.status !== 'FACULTY_SIGNED' || !coachingAllowed(course)) return go(`prepare/${courseId}/${materialId}`);
    header({ crumb: course.title, step: 3, back: true });
    screen('objective');
    state.objective = { course, material };
    setError($('objective-error'), null);
    $('approval-edit').setAttribute('href', `#prepare/${courseId}/${materialId}`);
    resetSkip();
    await objectiveForm.load(materialId);
  }

  function paintObjectiveBar({ saved, dirty, missing, busy: working }) {
    const next = $('objective-next');
    const skip = $('objective-skip');
    $('objective-bar-state').textContent = saved ? `v${saved.version} 확정됨${dirty ? ' · 변경 있음' : ''}` : dirty ? '작성 중' : '미설정';
    $('objective-bar-hint').textContent = dirty && missing.length ? `${missing.join(', ')}을(를) 적으면 확정할 수 있어요`
      : dirty ? '확정하면 이후 여는 수업부터 이 설정을 써요'
        : saved ? objectiveRules.summary(saved).replace(/^v\d+ · /, '')
          : '학습목표와 수행 증거를 적으면 확정할 수 있어요';
    next.textContent = dirty || !saved ? '확정하고 수업 시작' : '수업 시작';
    next.disabled = working || (dirty ? missing.length > 0 : !saved);
    show(skip, !saved || dirty);
    if (!skip.dataset.confirm) skip.textContent = saved ? '변경 취소하고 수업 시작' : '설정 없이 수업 시작';
  }

  function resetSkip() {
    const skip = $('objective-skip');
    skip.dataset.confirm = '';
    skip.classList.remove('dark');
  }

  async function startClass(btn) {
    const { course, material } = state.objective;
    setError($('objective-error'), null);
    busy(btn, true, '시작하는 중…');
    try {
      const session = await api(`/v1/courses/${course.id}/sessions`, { method: 'POST', body: { material_id: material.id } });
      openedSession = session;
      go(`live/${session.id}`);
    } catch (err) {
      setError($('objective-error'), err.status === 409 && err.detail === 'LIVE_PREVIEW_UNAVAILABLE'
        ? '이 교안의 송출 화면이 준비되지 않았습니다. 새로 올리거나 PDF로 저장해 올려 주세요.' : err);
      busy(btn, false);
    }
  }

  $('objective-next').addEventListener('click', async () => {
    const btn = $('objective-next');
    if (btn.disabled || !state.objective) return;
    const { dirty } = objectiveForm.state();
    if (dirty) {
      busy(btn, true, '확정하는 중…');
      try { await objectiveForm.save(); } catch (_) { busy(btn, false); return; }
      busy(btn, false);
    }
    await startClass(btn);
  });

  $('objective-skip').addEventListener('click', () => {
    const btn = $('objective-skip');
    const { saved, dirty } = objectiveForm.state();
    // Unconfirmed input would be lost: ask once more before leaving.
    if (dirty && btn.dataset.confirm !== 'yes') {
      btn.dataset.confirm = 'yes';
      btn.classList.add('dark');
      btn.textContent = saved ? '변경을 버리고 진행' : '입력한 내용을 버리고 진행';
      setTimeout(() => { resetSkip(); paintObjectiveBar(objectiveForm.state()); }, 4000);
      return;
    }
    resetSkip();
    objectiveForm.discard();
    startClass(btn);
  });

  /* ---------- 2 · 교안 승인 ---------- */
  function clearConsentError() {
    setError($('ai-consent-error'), null);
    $('prepare-consent').classList.remove('consent-required');
    $('ai-consent').removeAttribute('aria-invalid');
    $('ai-consent').removeAttribute('aria-describedby');
  }

  $('ai-consent').addEventListener('change', clearConsentError);

  function showConsentError() {
    const card = $('prepare-consent');
    const checkbox = $('ai-consent');
    setError($('ai-consent-error'), 'AI 질문 코칭 사용에 동의해야 다음 단계로 갈 수 있습니다.');
    checkbox.setAttribute('aria-invalid', 'true');
    checkbox.setAttribute('aria-describedby', 'ai-consent-error');
    card.classList.remove('consent-required');
    void card.offsetWidth;
    card.classList.add('consent-required');
    checkbox.focus({preventScroll: true});
    card.scrollIntoView({block: 'nearest'});
  }

  async function renderPrepare(courseId, materialId) {
    if (!state.courses.length) await loadCourses();
    const course = state.courses.find((c) => c.id === courseId);
    if (!course) return go('courses');
    const material = (course.materials || []).find((m) => m.id === materialId) || { id: materialId, filename: '' };
    header({ crumb: course.title, step: 2, back: true });
    screen('prepare');
    clearConsentError();
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
    $('approve-start').textContent = items.length ? '승인하고 다음' : '다음';
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
    if (!p) return;
    setError($('prepare-error'), null);
    const items = selectedItems();
    if (items.length > MAX_APPROVAL_ITEMS) return setError($('prepare-error'), '한 번에 최대 1,000개까지 승인할 수 있습니다.');
    if (!$('ai-consent').checked) return showConsentError();
    if (items.length && p.isPptx && !$('media-ack').checked) return setError($('prepare-error'), '분석되지 않은 오디오·도표 안내를 확인해 주세요.');
    busy(btn, true, '승인하는 중…');
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
      const known = (p.course.materials || []).find((m) => m.id === p.material.id);
      if (known) known.status = 'FACULTY_SIGNED';
      else (p.course.materials ||= []).push({ ...p.material, status: 'FACULTY_SIGNED' });
      busy(btn, false);
      go(`objective/${p.course.id}/${p.material.id}`);
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
    const session = openedSession?.id === sessionId ? openedSession : await api(`/v1/sessions/${sessionId}`);
    openedSession = null;
    if (session.status === 'CLOSED') return go(`end/${sessionId}`);
    const prev = state.live && state.live.session.id === sessionId ? state.live : null;
    state.live = { session, level: prev ? prev.level : 'all', seen: prev ? prev.seen : null, items: [], timer: null };
    screen('live');
    liveView = window.QU.createLiveView({
      api, session: sessionId, panel: $('teacher-slide'), faculty: true,
      initial: session.initial_live,
      localFile: uploadedPdf?.material === session.material_id ? uploadedPdf.file : null,
      onState: ({slide, status}) => {
        if (slide && state.live?.lastRequestedPage === slide.page) state.live.lastRequestedPage = undefined;
        if (status === 'CLOSED') go(`end/${sessionId}`);
      },
      onQuestion: async (id) => {
        if (document.fullscreenElement) await document.exitFullscreen();
        setFilter('all');
        await refreshBoard();
        document.getElementById('indexed-' + id)?.scrollIntoView({block: 'center'});
      },
    });
    paintLiveHeader();
    $('live-code').textContent = session.join_code || '––––––';
    $('live-url').textContent = location.host + location.pathname.replace(/teacher\.html$/, '');
    setFilter(state.live.level);
    await refreshBoard();
    state.live.timer = setInterval(refreshBoard, 5000);
  }

  async function broadcast(page) {
    const live = state.live;
    const view = liveView;
    if (!live || !view || !Number.isInteger(page) || page < 1 || page > (view.total() ?? Infinity)) return;
    live.requestedPage = page;
    live.requestedAt = performance.now();
    live.publishAfter = live.requestedAt + 80;
    live.lastRequestedPage = page;
    view.select(page);
    if (live.sendingPage) return;
    live.sendingPage = true;
    try {
      while (live.requestedPage !== undefined && state.live === live && liveView === view) {
        const wait = live.publishAfter - performance.now();
        if (wait > 0) {
          await new Promise(resolve => setTimeout(resolve, wait));
          continue;
        }
        const next = live.requestedPage;
        live.requestedPage = undefined;
        const batchId = crypto.randomUUID();
        const version = batchId + ':0';
        let serverTiming;
        const options = {
          method: 'PUT', body: {page: next, batch_id: batchId, expected_version: view.confirmedVersion()}, timeout: 8000,
          onResponse: liveTimingEnabled ? response => { serverTiming = response.headers.get('Server-Timing'); } : undefined,
        };
        liveMark?.('teacher-action', version, next, live.requestedAt);
        liveMark?.('publish-start', version, next);
        try {
          const url = `/v1/sessions/${live.session.id}/live`;
          const result = await api(url, options).catch(err => {
            if ((err.status === 0 || err.status >= 500) && state.live === live && liveView === view &&
                live.requestedPage === undefined) return api(url, options);
            throw err;
          });
          liveMark?.('publish-response', version, next, undefined, serverTiming);
          view.acknowledge(result);
          setError($('global-error'), null);
        } catch (err) {
          if (state.live !== live || liveView !== view) return;
          if (live.requestedPage === undefined) {
            live.lastRequestedPage = undefined;
            view.reconcile();
          }
          setError($('global-error'), err.status === 422 ? '송출할 수 없는 페이지입니다.' : err);
        }
      }
    } finally {
      live.sendingPage = false;
    }
  }

  function moveSlide(delta) {
    const next = liveView?.neighbor(delta);
    if (next) broadcast(next);
  }
  $('share-prev').addEventListener('click', () => moveSlide(-1));
  $('share-next').addEventListener('click', () => moveSlide(1));
  $('teacher-slide').querySelector('[data-live-content]').addEventListener('click', e => {
    if (e.button === 0 && !window.getSelection()?.toString() && !e.target.closest('button, a')) moveSlide(1);
  });
  document.addEventListener('keydown', e => {
    if ($('teacher-slide').hidden || location.hash.split('/')[0] !== '#live' ||
        document.fullscreenElement && document.fullscreenElement !== $('teacher-slide') ||
        e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey ||
        e.target.closest('input, textarea, select, button, a, [contenteditable]')) return;
    const delta = ['ArrowRight', 'PageDown', ' '].includes(e.key) ? 1 :
      ['ArrowLeft', 'PageUp'].includes(e.key) ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    moveSlide(delta);
  });
  $('share-fullscreen').addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else {
        const panel = $('teacher-slide');
        await panel.requestFullscreen();
        panel.focus({preventScroll: true});
      }
    } catch (err) {
      setError($('global-error'), err);
    }
  });

  function paintLiveHeader() {
    const s = state.live.session;
    const chatting = s.status === 'OPEN' && s.chat_open === true;
    header({ crumb: s.course_title, step: 4, live: chatting, paused: !chatting, back: true });
    $('paused-chip').textContent = s.status === 'PAUSED' ? '잠시 멈춤' : '채팅 잠김';
    const toggle = $('chat-toggle');
    if (!toggle.disabled) toggle.textContent = s.chat_open ? '질문 채팅 닫기' : '질문 채팅 열기';
    toggle.className = s.chat_open ? 'btn block' : 'btn primary block';
    toggle.setAttribute('aria-pressed', String(!!s.chat_open));
    $('chat-state').textContent = s.chat_open ? '열림' : '잠김';
    $('chat-hint').textContent = s.chat_open ? '학생이 질문을 쓰고 AI 코칭을 받을 수 있어요. 닫으면 새 질문과 전송이 멈춰요.'
      : '질문 시간이 되면 열어 주세요. 열기 전에는 학생이 교안만 볼 수 있어요.';
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
    paintBoard(state.live);
  });

  async function refreshBoard() {
    const live = state.live;
    if (!live) return;
    const request = live.boardRequest = (live.boardRequest || 0) + 1;
    let data;
    try {
      data = await api(`/v1/sessions/${live.session.id}/board?include_hidden=true`);
    } catch (err) {
      if (state.live === live && request === live.boardRequest && err.status !== 401) globalError(err);
      return;
    }
    if (state.live !== live || request !== live.boardRequest || $('screen-live').hidden) return;
    live.session = data.session;
    if (live.session.status === 'CLOSED') return go(`end/${live.session.id}`);
    live.items = data.items;
    live.boardLoaded = true;
    paintLiveHeader();
    if (!proj.hidden && !proj.classList.contains('code-mode')) paintProjected(live);
    for (const k of ['all', 'deep', 'mid', 'shallow', 'unassessed']) {
      const n = $('level-filter').querySelector(`[data-count="${k}"]`);
      if (n) n.textContent = String(data.counts[k] || 0);
    }
    $('live-total').textContent = `${data.counts.all || 0}개`;
    paintBoard(live);
  }

  function paintBoard(live) {
    const items = live.level === 'all' ? live.items : live.items.filter(q => q.level === live.level);
    const firstLoad = live.seen === null;
    if (live.boardLoaded) live.seen ??= new Set();
    const board = $('board');
    if (!items.length) {
      board.replaceChildren(el('div', { class: 'empty' },
        live.level === 'all' ? '아직 들어온 질문이 없어요. 학생들이 코드로 입장해 질문을 다듬고 보내면 여기에 나타납니다.'
          : live.level === 'unassessed' ? '수준 판정이 보류된 질문이 아직 없어요.'
            : `${LEVEL[live.level]} 수준 질문이 아직 없어요.`));
      return;
    }
    board.replaceChildren(...items.map((q) => {
      const isNew = !firstLoad && !live.seen.has(q.id);
      live.seen.add(q.id);
      return questionCard(q, isNew);
    }));
  }

  // Names of Google students are masked until the faculty clicks; the choice survives board refreshes.
  const revealedNames = new Set();
  function studentChip(q) {
    if (!q.student) return null;
    const { name, email } = q.student;
    const label = (shown) => (shown ? `${name} 학생, ${email}. 눌러서 다시 가리기` : '가려진 학생 이름. 눌러서 보기');
    const shown = revealedNames.has(q.id);
    const nameNode = el('span', { class: 'who-name', text: name, 'aria-hidden': shown ? null : 'true' });
    const chip = el('button', { class: 'who' + (shown ? ' is-shown' : ''), type: 'button', 'aria-pressed': String(shown),
      'aria-label': label(shown), title: shown ? email : '눌러서 학생 이름 보기' }, nameNode);
    chip.addEventListener('click', () => {
      const now = !revealedNames.has(q.id);
      if (now) revealedNames.add(q.id); else revealedNames.delete(q.id);
      chip.classList.toggle('is-shown', now);
      chip.setAttribute('aria-pressed', String(now));
      chip.setAttribute('aria-label', label(now));
      chip.title = now ? email : '눌러서 학생 이름 보기';
      if (now) nameNode.removeAttribute('aria-hidden'); else nameNode.setAttribute('aria-hidden', 'true');
    });
    return chip;
  }

  function questionCard(q, isNew) {
    const cls = ['qcard', q.projected ? 'projected' : '', isNew ? 'is-new' : '', q.hidden ? 'is-hidden' : ''].filter(Boolean).join(' ');
    return el('article', { class: cls, id: 'indexed-' + q.id },
      el('span', { class: `edge ${q.level}` }),
      el('div', { class: 'body' },
        el('p', { class: 'q', text: q.text }),
        q.original ? el('p', { class: 'orig', text: `처음 질문: ${q.original}` }) : null,
        el('div', { class: 'meta' },
          el('span', { class: `lv ${q.level}`, text: levelText(q.level) }),
          studentChip(q),
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

  $('chat-toggle').addEventListener('click', async () => {
    const s = state.live.session;
    const btn = $('chat-toggle');
    busy(btn, true, s.chat_open ? '닫는 중…' : '여는 중…');
    try {
      // A paused class from before the chat switch resumes with the chat.
      if (!s.chat_open && s.status === 'PAUSED') await api(`/v1/sessions/${s.id}/state`, { method: 'PUT', body: { status: 'OPEN' } });
      const updated = await api(`/v1/sessions/${s.id}/chat`, { method: 'PUT', body: { open: !s.chat_open } });
      Object.assign(state.live.session, updated);
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
      const live = state.live;
      await live.projectionSync;
      if (state.live !== live) return;
      const s = await api(`/v1/sessions/${live.session.id}/state`, { method: 'PUT', body: { status: 'CLOSED' } });
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

  function project(id) {
    const live = state.live;
    if (!live?.items.some(q => q.id === id && !q.hidden)) return;
    live.projectorItem = id;
    paintProjected(live);
    if (proj.hidden) openProjector();
    live.pendingProjection ??= new Set();
    live.pendingProjection.add(id);
    live.publishProjectionAt = performance.now() + 80;
    void syncProjection(live);
  }

  async function syncProjection(live) {
    if (live.projectionSync) return live.projectionSync;
    const publish = async () => {
      while (live.pendingProjection.size && state.live === live) {
        const delay = live.publishProjectionAt - performance.now();
        if (delay > 0) {
          await new Promise(resolve => setTimeout(resolve, delay));
          continue;
        }
        const id = live.projectorItem;
        const selectedAt = live.publishProjectionAt;
        const displayedIds = [...live.pendingProjection].filter(other => other !== id).slice(0, 100);
        for (const displayedId of [...displayedIds, id]) live.pendingProjection.delete(displayedId);
        const path = `/v1/session-questions/${id}/project`;
        const options = { method: 'POST', body: { displayed_ids: displayedIds }, timeout: 8000 };
        try {
          await api(path, options).catch(err => {
            if (state.live !== live || !(err.status === 0 || err.status >= 500)) throw err;
            if (live.publishProjectionAt !== selectedAt) {
              for (const displayedId of [...displayedIds, id]) live.pendingProjection.add(displayedId);
              return;
            }
            return api(path, options);
          });
        } catch (err) {
          if (state.live !== live) return;
          if (live.projectorItem === id) globalError('발표 상태를 저장하지 못했습니다. 질문을 다시 선택해 주세요.');
        }
      }
    };
    live.projectionSync = publish().finally(() => { live.projectionSync = null; });
    return live.projectionSync;
  }

  function paintProjected(live) {
    proj.classList.remove('code-mode');
    const items = live.items.filter(q => !q.hidden);
    const index = items.findIndex(q => q.id === live.projectorItem);
    const item = items[index];
    $('proj-label').textContent = item && item.level !== 'unassessed' ? `${item.level_label} 질문` : '학생 질문';
    $('proj-text').textContent = item ? item.text : '띄울 질문이 없습니다.';
    $('proj-meta').textContent = item && item.original ? `처음 질문: ${item.original}` : '이름 없이 들어온 질문입니다.';
    const canMove = items.length > 1;
    live.prevId = canMove ? items[index < 0 ? items.length - 1 : (index + items.length - 1) % items.length].id : null;
    live.nextId = canMove ? items[(index + 1) % items.length].id : null;
    $('proj-prev').disabled = !live.prevId;
    $('proj-next').disabled = !live.nextId;
  }

  $('proj-prev').addEventListener('click', () => { if (state.live?.prevId) project(state.live.prevId); });
  $('proj-next').addEventListener('click', () => { if (state.live?.nextId) project(state.live.nextId); });
  $('proj-close').addEventListener('click', closeProjector);
  $('code-full').addEventListener('click', () => {
    proj.classList.add('code-mode');
    $('proj-label').textContent = '입장 코드';
    $('proj-text').textContent = state.live.session.join_code || '';
    $('proj-meta').textContent = `${location.host}${location.pathname.replace(/teacher\.html$/, '')} 에서 입력하세요`;
    $('proj-prev').disabled = true;
    $('proj-next').disabled = true;
    openProjector();
  });
  document.addEventListener('keydown', (e) => {
    if (proj.hidden) return;
    if (e.key === 'Escape') return closeProjector();
    if (proj.classList.contains('code-mode') || !state.live) return;
    const id = e.key === 'ArrowLeft' ? state.live.prevId
      : (e.key === 'ArrowRight' || (e.key === ' ' && !e.target.closest('button'))) ? state.live.nextId : null;
    if (id) { e.preventDefault(); project(id); }
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
