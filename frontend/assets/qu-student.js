/* Question-up · 학생 흐름
   코드 입장 → 대화형 질문 코치(콩나무) → 교수자에게 보내기 → 내 질문
   익명 토큰은 메모리에만 둡니다. 탭을 닫으면 사라집니다. */
(() => {
  'use strict';
  const { el, client, LEVEL, levelText, plant } = window.QU;
  const $ = (id) => document.getElementById(id);

  document.addEventListener('copy', event => {
    if (event.target?.closest?.('input, textarea') || event.target?.isContentEditable) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return;
    const protectedNodes = [...document.querySelectorAll('#student-slide [data-copy-protected], #student-browse [data-copy-protected]')];
    for (let i = 0; i < selection.rangeCount; i++) {
      const range = selection.getRangeAt(i);
      if (protectedNodes.some(node => range.intersectsNode(node))) {
        event.preventDefault();
        return;
      }
    }
  }, true);

  let token = null;
  const st = {
    session: null,      // {id, course_title, status}
    current: null,      // {id, level, original}
    stage: 'seed',
    sentCount: 0,
    sending: false,
  };
  const api = client(() => token, () => {
    token = null;
    fresh.stop();
    stopPoll();
    clearTimeout(draftPoll);
    showScreen('join');
    setError($('join-error'), '입장 시간이 만료됐습니다. 코드를 다시 입력해 주세요.');
  });

  // Class tokens last four hours; an open tab renews before then.
  const fresh = window.QU.keepTokenFresh({ getToken: () => token, setToken: (value) => { token = value; } });

  const STAGE_TEXT = { seed: '아직 씨앗', shallow: '얕은 질문', mid: '중간 질문', deep: '깊은 질문' };
  const GROWTH = {
    shallow: '싹이 텄어요 · 얕은 질문',
    mid: '한 마디 자랐어요 · 중간 질문',
    deep: '구름 위까지 자랐어요 · 깊은 질문',
  };
  const RANK = { seed: 0, shallow: 1, mid: 2, deep: 3 };
  const TEMPLATES = {
    shallow: { next: '중간 질문 연습 · 적용과 분석', items: [
      ['비교', '교안에서 다룬 ___와 ___를 ___ 기준으로 비교하면 어떤 차이가 있나요?'],
      ['사례 설명', '교안의 ___ 개념으로 ___ 사례를 설명하면 어떤 점이 드러나나요?'],
      ['원인 분석', '교안의 ___ 사례에서 ___는 어떤 조건에서 나타나나요?'],
    ] },
    mid: { next: '깊은 질문 연습 · 평가와 설계', items: [
      ['기준으로 평가', '교안에서 다룬 ___를 ___ 기준으로 평가할 때 어떤 한계를 고려해야 하나요?'],
      ['근거 비교', '교안의 ___와 ___ 중 어느 근거가 ___ 주장을 더 잘 뒷받침하며, 그 판단 기준은 무엇인가요?'],
      ['조건 설계', '교안의 ___ 개념을 검증할 실험을 설계한다면, ___ 조건을 어떻게 바꾸고 무엇을 일정하게 유지해야 하나요?'],
    ] },
  };
  const BLANK = /_{2,}/;

  /* ---------- helpers ---------- */
  function setError(node, msg) {
    node.hidden = !msg;
    node.textContent = msg ? (typeof msg === 'string' ? msg : msg.message) : '';
  }
  function showScreen(name) {
    for (const s of document.querySelectorAll('[data-screen]')) s.hidden = s.id !== 'screen-' + name;
    window.scrollTo(0, 0);
  }
  let liveView;
  let questionAnchor;
  const slidePanel = $('student-slide');
  const expandButton = $('student-slide-expand');

  const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement;
  let fullscreenResizeFrame;

  function expanded() {
    return fullscreenElement() === slidePanel || slidePanel.classList.contains('live-expanded');
  }

  function resizeExpandedSlide() {
    cancelAnimationFrame(fullscreenResizeFrame);
    fullscreenResizeFrame = requestAnimationFrame(() => liveView?.resize());
  }

  function updateFullscreen() {
    const active = expanded();
    slidePanel.classList.toggle('live-native-fullscreen', fullscreenElement() === slidePanel);
    document.documentElement.classList.toggle('live-view-expanded', active);
    const label = active ? '교안 전체화면 종료' : '교안 전체화면';
    expandButton.setAttribute('aria-label', label);
    expandButton.title = label;
    if (!active) slidePanel.querySelector('[data-live-actions]').classList.remove('selection-actions');
    expandButton.setAttribute('aria-pressed', String(active));
    resizeExpandedSlide();
  }

  async function collapseSlide() {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (fullscreenElement() === slidePanel && exit) {
      try { await exit.call(document); } catch (_) { /* The browser may already have exited. */ }
    }
    if (slidePanel.classList.contains('live-expanded')) {
      slidePanel.classList.remove('live-expanded');
      updateFullscreen();
    }
  }

  expandButton.addEventListener('click', async () => {
    if (expanded()) return collapseSlide();
    try {
      const request = slidePanel.requestFullscreen || slidePanel.webkitRequestFullscreen;
      if (request) {
        await request.call(slidePanel);
        updateFullscreen();
        return;
      }
    } catch (_) { /* Use the viewport on browsers that deny native fullscreen. */ }
    slidePanel.classList.add('live-expanded');
    updateFullscreen();
  });
  document.addEventListener('fullscreenchange', updateFullscreen);
  document.addEventListener('webkitfullscreenchange', updateFullscreen);
  window.addEventListener('resize', () => {
    if (expanded()) resizeExpandedSlide();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && slidePanel.classList.contains('live-expanded')) collapseSlide();
  });
  function stopPoll() {
    liveView?.stop();
    liveView = null;
    if (expanded()) collapseSlide();
    slidePanel.hidden = true;
    applyBrowse([]);
  }

  function setStage(stage) {
    st.stage = stage;
    $('badge-plant').replaceChildren(plant(stage));
    $('badge-level').textContent = STAGE_TEXT[stage];
    $('panel-plant').replaceChildren(plant(stage, { panel: true, label: `내 질문의 키: ${STAGE_TEXT[stage]}` }));
    $('panel-cap').textContent = stage === 'deep' ? '구름 위까지 자랐어요! 교수자에게 보내 보세요.'
      : stage === 'seed' ? '질문을 쓰면 콩나무가 자라요. 구름 위까지 키워 보세요.'
        : `지금은 ${STAGE_TEXT[stage]}이에요. 한 마디 더 키워 볼까요?`;
  }

  function scrollThread() {
    requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
  }

  // One "AI is answering" indicator at a time; it stays at the end of the thread.
  let thinkingNode = null;
  let thinkingGuard = null;
  function showThinking() {
    hideThinking();
    thinkingNode = QU.thinking();
    $('thread').append(thinkingNode);
    scrollThread();
    // Never leave it spinning: a check that never resolves on screen ends the wait after 90 s.
    thinkingGuard = setTimeout(() => hideThinking(), 90000);
  }
  function hideThinking(draftId) {
    if (!thinkingNode) return;
    if (draftId && thinkingNode.draftId && thinkingNode.draftId !== draftId) return;
    clearTimeout(thinkingGuard);
    thinkingNode.stop();
    thinkingNode.remove();
    thinkingNode = null;
  }

  function coachMsg(...children) {
    const node = el('div', { class: 'msg' }, el('span', { class: 'av', 'aria-hidden': 'true', text: 'Q' }), el('div', { class: 'bubble' }, ...children));
    if (thinkingNode && thinkingNode.parentNode === $('thread')) $('thread').insertBefore(node, thinkingNode);
    else $('thread').append(node);
    scrollThread();
    return node;
  }
  function meMsg(text) {
    $('thread').append(el('div', { class: 'msg me' }, el('div', { class: 'bubble', text })));
    scrollThread();
  }

  /* ---------- 교수자에게 보낼 질문 ---------- */
  const DRAFT_LIMIT = 20;
  const EDIT_MULTIPLE_WARNING = '다듬기는 한 번에 하나만 가능해요. 질문을 하나만 선택해 주세요.';
  const REJECTION_MESSAGE = '반응만 적기보다 수업에서 궁금하거나 더 알아보고 싶은 내용을 적어 주세요.';
  const drafts = new Map();
  const selected = new Map();
  const checking = new Set();
  const requestedLegacyChecks = new Set();
  let batchSupported = false;
  let batchSending = false;
  let pendingBatch = null;
  let pendingAsk = null;
  let draftPoll;
  let draftRead = 0;

  // Faculty hold the chat: it stays locked until the teacher opens it at question time.
  const chatOpen = () => st.session?.chat_open === true;
  const openForQuestions = () => st.session?.status === 'OPEN' && chatOpen();
  function selectable(draft) {
    return openForQuestions() && draft.revision_id && draft.suitability?.status === 'eligible';
  }

  function updateBatchButton() {
    const button = $('question-batch-send');
    button.disabled = !openForQuestions() || st.sending || batchSending || !selected.size;
    button.textContent = batchSending ? '보내는 중…' : '선택한 질문 보내기';
    $('question-new').disabled = $('question-edit').disabled = !openForQuestions() || st.sending || batchSending;
  }

  function paintDrafts() {
    const visible = [...drafts.values()].filter(draft => draft.suitability?.status === 'eligible');
    for (const [id, revision] of selected) {
      const draft = drafts.get(id);
      if (!draft || !selectable(draft) || draft.revision_id !== revision) selected.delete(id);
    }
    $('question-selection').hidden = !batchSupported || !visible.length;
    $('question-count').textContent = `${visible.length}개`;
    if (!visible.length) $('question-disclosure').open = false;
    const list = $('question-options');
    const focused = list.contains(document.activeElement) ? document.activeElement.id : '';
    const scrollTop = list.scrollTop;
    const cards = visible.map(draft => {
      const {id, revision_id} = draft;
      const checkbox = el('input', {
        id: 'pick-' + id, type: 'checkbox', checked: selected.has(id),
        disabled: !selectable(draft) || batchSending,
        'aria-label': '보낼 질문 선택: ' + draft.text,
        onchange: event => {
          if (event.target.checked && selected.size >= DRAFT_LIMIT) {
            event.target.checked = false;
            $('question-batch-status').textContent = '한 번에 20개까지 보낼 수 있어요.';
          } else {
            if (event.target.checked) selected.set(id, revision_id);
            else selected.delete(id);
            $('question-batch-status').textContent = '';
          }
          updateBatchButton();
        },
      });
      return el('div', {class: 'question-option'},
        el('label', {class: 'row'}, checkbox, el('span', {text: draft.text})));
    });
    list.replaceChildren(...cards);
    list.scrollTop = scrollTop;
    if (focused) $(focused)?.focus({preventScroll: true});
    updateBatchButton();
  }

  function editDraft(draft = null) {
    if (st.sending || batchSending || !openForQuestions()) return;
    if (draft && selected.size > 1) {
      $('question-batch-status').textContent = EDIT_MULTIPLE_WARNING;
      return false;
    }
    $('question-batch-status').textContent = '';
    st.current = draft ? {...draft} : null;
    questionAnchor = null;
    const input = $('q-input');
    input.value = draft?.text || '';
    input.placeholder = '궁금한 점을 질문으로 써 보세요';
    input.style.height = '';
    updateComposer();
    paintDrafts();
    input.focus();
    return true;
  }

  function scheduleDraftPoll() {
    clearTimeout(draftPoll);
    if (token && openForQuestions() && [...drafts.values()].some(({id, suitability, revision_id}) =>
      suitability?.status === 'pending' && suitability.primary_reason !== 'check_unavailable' &&
      (!revision_id?.startsWith('legacy-') || requestedLegacyChecks.has(id)))) {
      draftPoll = setTimeout(refreshDrafts, 3000);
    }
  }

  async function refreshDrafts() {
    const access = token;
    const read = ++draftRead;
    try {
      const data = await api(`/v1/sessions/${st.session.id}/mine`);
      if (token !== access || read !== draftRead || !data.suitability?.policy_version) return;
      batchSupported = true;
      const unsent = data.items.filter(draft => !draft.submitted).reverse();
      for (const draft of unsent) {
        const previous = drafts.get(draft.id);
        if (previous?.revision_id === draft.revision_id && previous.suitability?.status === 'pending' &&
            (draft.suitability?.status !== 'pending' ||
             previous.suitability.primary_reason !== draft.suitability.primary_reason)) {
          showDraftOutcome(draft);
        } else if (!previous && draft.suitability?.status === 'pending' &&
                   (draft.revision_id?.startsWith('legacy-') ||
                    draft.suitability.primary_reason === 'check_unavailable')) {
          showDraftRetry(draft);
        }
      }
      drafts.clear();
      for (const draft of unsent) if (draft.suitability?.status !== 'needs_revision') drafts.set(draft.id, draft);
      st.sentCount = data.items.length - unsent.length;
      updateComposer();
      paintDrafts();
    } catch (_) { /* Keep local drafts on a transient read failure. */ }
    finally { if (token === access && read === draftRead) scheduleDraftPoll(); }
  }

  function showDraftRetry(draft) {
    hideThinking(draft.id);
    coachMsg(el('span', {text: '질문 적합성을 다시 확인해 주세요.'}),
      el('button', {class: 'btn sm', type: 'button', text: '다시 확인', onclick: event => {
        const latest = drafts.get(draft.id);
        if (latest?.revision_id !== draft.revision_id || latest.suitability?.status !== 'pending') return;
        const button = event.currentTarget;
        button.disabled = true;
        retryDraft(latest).finally(() => { button.disabled = false; });
      }}));
  }

  function showDraftOutcome(draft) {
    if (draft.suitability?.status !== 'pending') hideThinking(draft.id);
    if (draft.suitability?.status === 'needs_revision') {
      coachMsg(el('span', {class: 'question-rejection'},
        el('span', {class: 'question-rejection-icon', 'aria-hidden': 'true', text: '!'}),
        el('span', {text: REJECTION_MESSAGE})));
    } else if (draft.suitability?.status === 'eligible') {
      renderCoaching(draft.level, draft.coaching, draft);
    } else if (draft.suitability?.primary_reason === 'check_unavailable') {
      showDraftRetry(draft);
    }
  }

  async function retryDraft(draft) {
    const access = token;
    const {id, revision_id} = draft;
    if (checking.has(id) || !openForQuestions()) return;
    checking.add(id);
    if (revision_id.startsWith('legacy-')) requestedLegacyChecks.add(id);
    paintDrafts();
    try {
      await api(`/v1/session-questions/${id}/suitability/check`, {method: 'POST', body: {revision_id}});
    } catch (err) {
      if (token === access) {
        requestedLegacyChecks.delete(id);
        coachMsg(el('span', {text: err.message}));
      }
    } finally {
      if (token === access) {
        checking.delete(id);
        paintDrafts();
        await refreshDrafts();
      }
    }
  }

  // Opening the send box by hand glides the page to the box's bottom (the send button),
  // kept clear of the sticky composer. Programmatic opens do not move the page.
  let disclosureByUser = false;
  $('question-summary').addEventListener('click', () => { disclosureByUser = true; });
  $('question-disclosure').addEventListener('toggle', event => {
    const opened = event.currentTarget.open && disclosureByUser;
    disclosureByUser = false;
    if (!opened) return;
    const box = $('question-selection');
    const composer = $('composer') && $('composer').parentElement;
    box.style.scrollMarginBottom = ((composer ? composer.offsetHeight : 0) + 12) + 'px';
    const still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    requestAnimationFrame(() => box.scrollIntoView({block: 'end', behavior: still ? 'auto' : 'smooth'}));
  });
  $('question-new').addEventListener('click', () => editDraft());
  $('question-edit').addEventListener('click', () => {
    const draft = drafts.get(selected.keys().next().value);
    if (draft) editDraft(draft);
    else $('question-batch-status').textContent = '다듬을 질문 하나를 선택해 주세요.';
  });
  $('question-batch-send').addEventListener('click', async () => {
    if ($('question-batch-send').disabled) return;
    const access = token;
    const items = [...drafts.keys()].filter(id => selected.has(id))
      .map(question_id => ({question_id, revision_id: selected.get(question_id)}));
    if (items.some(({question_id}) => question_id === st.current?.id &&
      $('q-input').value.trim() && $('q-input').value.trim() !== st.current.text)) {
      $('question-batch-status').textContent = '수정 중인 질문을 먼저 채팅창에 보내 다시 확인해 주세요.';
      return;
    }
    if (!pendingBatch || JSON.stringify(pendingBatch.items) !== JSON.stringify(items)) {
      pendingBatch = {request_id: crypto.randomUUID(), items};
    }
    batchSending = true;
    draftRead++;
    clearTimeout(draftPoll);
    updateComposer();
    paintDrafts();
    try {
      const data = await api(`/v1/sessions/${st.session.id}/questions/submit-batch`, {method: 'POST', body: pendingBatch});
      if (token !== access) return;
      draftRead++;
      $('question-batch-status').textContent = '';
      pendingBatch = null;
      for (const item of data.submitted) {
        selected.delete(item.question_id);
        drafts.delete(item.question_id);
        if (st.current?.id === item.question_id) st.current = null;
      }
      st.sentCount += data.submitted.length;
      coachMsg(el('span', {text: `선택한 질문 ${data.submitted.length}개를 교수자에게 보냈어요.`}));
    } catch (err) {
      if (token !== access) return;
      if (err.status === 409) {
        pendingBatch = null;
        selected.clear();
        await refreshDrafts();
      }
      if (err.status === 423) await pollStatus();
      $('question-batch-status').textContent = err.status === 409
        ? '질문 상태가 바뀌었어요. 다시 확인하고 선택해 주세요.' : err.message;
    } finally {
      if (token === access) {
        batchSending = false;
        updateComposer();
        paintDrafts();
        scheduleDraftPoll();
      }
    }
  });

  /* ---------- 입장 ---------- */
  $('code').addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6);
    setError($('join-error'), null);
  });

  /* ---------- 학교 Google 로그인 (선택) ---------- */
  // Optional: a Google student joins as the same account; questions stay anonymous on every screen.
  const authApi = client(() => null);
  const google = window.QU.createGoogleLogin({ api: authApi });
  let joinTicket = null;
  const show = (node, on) => { node.hidden = !on; };

  function paintGoogle() {
    const signedIn = !!joinTicket;
    show($('google-start'), !signedIn);
    show($('google-divider'), !signedIn);
    show($('google-privacy'), !signedIn);
    show($('google-cancel'), signedIn || !$('google-role').hidden);
    $('join-mode').textContent = signedIn
      ? `학교 계정(${google.email()})으로 참여해요. 질문을 보내면 이름과 학교 이메일이 교수자에게 함께 전달돼요(다른 학생에게는 보이지 않아요).`
      : '로그인 없이 익명으로 참여합니다. 다른 학생에게는 내 이름이 보이지 않아요.';
  }

  function finishGoogle(result) {
    if (!result) return;
    if (result.needsRole) {
      $('google-role-email').textContent = google.roleIntro(result);
      show($('google-role'), true);
      paintGoogle();
      return;
    }
    show($('google-role'), false);
    if (result.role === 'faculty') return google.goTo('teacher.html', 'faculty');
    joinTicket = result.join_ticket;
    google.clearReturnParams();
    paintGoogle();
    $('code').focus();
  }

  async function runGoogle(task) {
    setError($('join-error'), null);
    const button = $('google-start');
    button.disabled = true;
    try { finishGoogle(await task()); } catch (err) { setError($('join-error'), err); } finally { button.disabled = false; }
  }

  $('google-start').addEventListener('click', () => runGoogle(async () => {
    if (await google.session()) return google.exchange();
    await google.start();
    return null;
  }));
  $('google-role').addEventListener('click', (event) => {
    const card = event.target.closest('[data-role]');
    if (card) runGoogle(() => google.exchange(card.dataset.role));
  });
  $('google-cancel').addEventListener('click', async () => {
    joinTicket = null;
    show($('google-role'), false);
    await google.signOut();
    paintGoogle();
  });
  if (google.returning()) runGoogle(() => google.exchange());

  async function join(code) {
    const btn = $('join-btn');
    btn.disabled = true; btn.textContent = '입장 중…';
    try {
      // With a Google join ticket the student joins as that account; without one, anonymously.
      const joinApi = joinTicket ? client(() => joinTicket) : api;
      const res = await joinApi('/v1/join', { method: 'POST', body: { code } });
      token = res.access_token;
      fresh.schedule();
      st.session = res.session;
      history.replaceState(null, '', location.pathname + (new URLSearchParams(location.search).get('api') ? `?api=${encodeURIComponent(new URLSearchParams(location.search).get('api'))}` : ''));
      startCoach(res.initial_live);
    } catch (err) {
      const msg = err.status === 404 ? '열려 있는 수업을 찾지 못했어요. 코드를 다시 확인해 주세요.'
        : err.status === 423 ? '교수자가 잠시 질문 받기를 멈췄어요. 조금 뒤 다시 입장해 주세요.'
          : err.status === 401 && joinTicket ? '학교 계정 로그인이 만료됐어요. 다시 로그인하거나 익명으로 참여해 주세요.' : err;
      if (err.status === 401 && joinTicket) { joinTicket = null; paintGoogle(); }
      setError($('join-error'), msg);
    } finally {
      btn.disabled = false; btn.textContent = '입장하기';
    }
  }

  $('join-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const code = $('code').value.trim();
    if (!/^[A-Z0-9]{6}$/.test(code)) return setError($('join-error'), '6자리 코드를 입력해 주세요.');
    join(code);
  });

  /* ---------- 질문 코치 ---------- */
  function startCoach(initial) {
    showScreen('coach');
    $('course-title').textContent = st.session.course_title || '오늘 수업';
    batchSupported = batchSending = st.sending = false;
    pendingBatch = pendingAsk = null;
    drafts.clear();
    selected.clear();
    checking.clear();
    requestedLegacyChecks.clear();
    $('question-batch-status').textContent = '';
    $('question-disclosure').open = false;
    paintDrafts();
    resetThread();
    refreshDrafts();
    applyChat(chatOpen());
    applyStatus(st.session.status);
    stopPoll();
    browse.live = initial?.slide?.page || null;
    applyBrowse(st.session.browse_pages || []);
    liveView = window.QU.createLiveView({
      api, session: st.session.id, panel: $('student-slide'), initial,
      onAnchor: ({page, anchor}) => {
        if (!chatOpen()) return '교수자가 질문 채팅을 열면 이 부분으로 질문할 수 있어요.';
        if (st.current || st.sending) return false;
        questionAnchor = {page, anchor};
        const input = $('q-input');
        input.placeholder = anchor?.quote ? '“' + anchor.quote + '”에 관해 질문하세요' : page + '쪽에 관해 질문하세요';
        if (expanded()) {
          const session = st.session;
          collapseSlide().then(() => {
            // Wait for the fullscreen layout to clear before measuring the chat's position.
            requestAnimationFrame(() => requestAnimationFrame(() => {
              const coach = $('screen-coach');
              if (st.session !== session || coach.hidden) return;
              if (matchMedia('(pointer: fine)').matches) input.focus({preventScroll: true});
              coach.scrollIntoView({
                behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
                block: 'start',
              });
            }));
          });
        } else {
          input.focus();
        }
        return true;
      },
      onState: ({status, chat_open: open, browse_pages: pages, slide}) => {
        if (slide?.page) browse.live = slide.page;
        if (Array.isArray(pages)) applyBrowse(pages);
        if (typeof open === 'boolean' && open !== chatOpen()) applyChat(open);
        if (status && status !== st.session.status) applyStatus(status);
      },
      onQuestion: async (id) => {
        await showMine();
        document.getElementById('my-indexed-' + id)?.scrollIntoView({block: 'center'});
      },
    });
  }

  function resetThread() {
    st.current = null;
    questionAnchor = null;
    $('q-input').placeholder = '궁금한 점을 적어 보세요';
    $('thread').replaceChildren();
    setStage('seed');
    coachMsg(el('span', { text: '오늘 수업에서 궁금한 점을 질문으로 써 보세요. 먼저 내 생각대로 쓰면, 질문이 한 마디씩 자라도록 도와 드릴게요.' }));
    $('q-input').value = '';
    $('paste-status').textContent = '';
    $('q-input').placeholder = '궁금한 점을 질문으로 써 보세요';
    updateComposer();
    $('q-input').focus();
  }

  function updateComposer() {
    const v = $('q-input').value.trim();
    const open = openForQuestions();
    $('q-input').disabled = !open;
    const hasBlank = BLANK.test(v);
    $('send').disabled = !open || st.sending || batchSending || !v || hasBlank;
    $('hint').textContent = hasBlank ? '빈칸(___)에 교안의 개념이나 수업에서 다룬 사례·근거를 넣어 주세요'
      : st.current ? '고친 질문을 보내면 다시 코칭해 드려요' : '다른 학생에게는 내 이름이 보이지 않아요';
    $('send-now').hidden = !st.current || !open || batchSupported;
    updateBatchButton();
    $('to-mine').hidden = st.sentCount === 0;
  }
  function blockQuestionPaste(event) {
    event.preventDefault();
    $('paste-status').textContent = '붙여넣기는 사용할 수 없어요. 질문을 직접 입력해 주세요.';
  }

  $('q-input').addEventListener('paste', blockQuestionPaste);
  $('q-input').addEventListener('beforeinput', event => {
    if (event.cancelable && event.inputType === 'insertFromPaste') blockQuestionPaste(event);
  });
  $('q-input').addEventListener('input', () => {
    const t = $('q-input');
    $('paste-status').textContent = '';
    t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 140) + 'px';
    updateComposer();
  });
  $('q-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('composer').requestSubmit(); }
  });

  $('composer').addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = $('q-input').value.trim();
    if ($('send').disabled) return;
    if (st.current && batchSupported && selected.size > 1) {
      $('question-disclosure').open = true;
      $('question-batch-status').textContent = EDIT_MULTIPLE_WARNING;
      return;
    }
    const access = token;
    st.sending = true; updateComposer(); paintDrafts();
    meMsg(text);
    $('q-input').value = ''; $('q-input').style.height = '';
    showThinking();
    try {
      const body = {question: text, slide_page: questionAnchor?.page ?? liveView?.page(), anchor: questionAnchor?.anchor};
      if (!st.current && (!pendingAsk || pendingAsk.content !== JSON.stringify(body))) {
        pendingAsk = {content: JSON.stringify(body), requestId: crypto.randomUUID()};
      }
      const res = st.current
        ? await api(`/v1/session-questions/${st.current.id}/revisions`, { method: 'POST', body: { text }, timeout: 90000 })
        : await api(`/v1/sessions/${st.session.id}/questions`, { method: 'POST',
          body: {...body, request_id: pendingAsk.requestId}, timeout: 90000 });
      if (token !== access) { hideThinking(); return; }
      pendingAsk = null;
      if (!st.current) st.current = { id: res.id, original: text };
      st.current.level = res.level;
      st.current.text = text;
      if (res.revision_id && res.starter && res.coaching) {
        // A starter (a definition, an unclear target, "이해가 안 돼요"): coach it and keep the
        // thread on this question, so the next send rewrites it instead of starting over.
        // It reaches the question list only after it becomes a question the board accepts.
        draftRead++;
        batchSupported = true;
        drafts.delete(st.current.id);
        hideThinking();
        coachMsg(el('span', {class: 'muted', text: '좋은 출발이에요. 아래 질문을 참고해 내 질문으로 다듬으면 교수자에게 보낼 수 있어요.'}));
        renderCoaching(res.level, res.coaching, null);
        scheduleDraftPoll();
      } else if (res.revision_id) {
        draftRead++;
        batchSupported = true;
        const draft = {...st.current, revision_id: res.revision_id, suitability: res.suitability, coaching: res.coaching};
        st.current = null;
        questionAnchor = null;
        $('q-input').placeholder = '궁금한 점을 질문으로 써 보세요';
        if (draft.suitability?.status === 'needs_revision') drafts.delete(draft.id);
        else drafts.set(draft.id, draft);
        $('question-batch-status').textContent = '';
        // The coaching appears once the suitability check finishes; keep the indicator until then.
        if (draft.suitability?.status === 'pending' && draft.suitability.primary_reason !== 'check_unavailable') {
          if (thinkingNode) thinkingNode.draftId = draft.id;
        } else {
          hideThinking();
        }
        showDraftOutcome(draft);
        scheduleDraftPoll();
      } else {
        hideThinking();
        renderCoaching(res.level, res.coaching, null);
      }
    } catch (err) {
      hideThinking();
      if (token !== access) return;
      if (err.status === 423) { await pollStatus(); }
      coachMsg(el('span', { text: err.status === 423 ? '지금은 질문을 받지 않는 중이에요. 교수자가 다시 열면 이어서 보낼 수 있어요.'
        : err.status === 409 ? '이미 교수자에게 보낸 질문이에요. 새 질문을 써 주세요.' : `${err.message} 다시 보내 볼까요?` }));
      if (err.status === 423 && err.detail === 'CHAT_LOCKED') applyChat(false);
      if (err.status === 409) st.current = null;
      if (err.status !== 409) $('q-input').value = text;
    } finally {
      if (token === access) { st.sending = false; updateComposer(); paintDrafts(); }
    }
  });

  function renderCoaching(level, c, draft) {
    if (level === 'unassessed') {
      // Offer guidance without assigning a level or advancing the plant.
      const parts = [el('span', { class: 'lv unassessed', text: levelText(level) }),
        el('span', { text: '이번에는 질문 수준을 판정하지 못했어요. 아래 질문을 참고해 다듬어 보세요.' })];
      if (c && c.follow_up_question) parts.push(el('span', {}, el('b', { text: '생각해 볼 질문 · ' }), c.follow_up_question));
      coachMsg(...parts);
      renderQuestionGuide({...TEMPLATES.shallow, next: '질문을 한 단계 발전시키기'}, draft);
      return;
    }
    const grew = RANK[level] > RANK[st.stage];
    if (grew) {
      const chip = el('div', { class: 'growth' });
      chip.append(plant(level), document.createTextNode(GROWTH[level]));
      $('thread').append(chip);
    }
    setStage(level);

    const parts = [el('span', { class: `lv ${level}`, text: levelText(level) })];
    // The model's reason for this level replaces the generic feedback line when present.
    const why = c && (c.level_reason || c.feedback);
    if (why) parts.push(el('span', { text: why }));
    if (c && c.follow_up_question) parts.push(el('span', {}, el('b', { text: '생각해 볼 질문 · ' }), c.follow_up_question));
    if (!c || (!why && !c.follow_up_question)) {
      parts.push(el('span', { text: level === 'deep' ? '수업 내용을 새로운 상황으로 확장하는 깊은 질문이에요.'
        : '좋은 출발이에요. 한 마디 더 키워 볼까요?' }));
    }
    const pages = c && c.evidence_refs ? [...new Set(c.evidence_refs.map((r) => r.page).filter(Boolean))] : [];
    if (pages.length) parts.push(el('div', { class: 'row' }, ...pages.slice(0, 4).map((p) => el('span', { class: 'evidence', text: `근거 · 교안 p.${p}` }))));

    if (level === 'deep' && !batchSupported) {
      parts.push(el('b', { text: '이 질문을 교수자에게 보낼까요?' }));
      parts.push(el('div', { class: 'row' },
        el('button', { class: 'btn primary', type: 'button', text: '교수자에게 보내기', onclick: submitCurrent }),
        el('button', { class: 'btn', type: 'button', text: '더 다듬기', onclick: () => { $('q-input').value = st.current.text; updateComposer(); $('q-input').focus(); } })));
      coachMsg(...parts);
      return;
    }
    coachMsg(...parts);
    renderQuestionGuide(guideFor(level, c), draft);
    if (!draft) $('q-input').placeholder = '빈칸(___)을 내 생각으로 채워 다시 보내세요';
  }

  // Frames written from the student's own question come first; the fixed templates are the fallback.
  function guideFor(level, c) {
    const base = TEMPLATES[level];
    if (!base) return base;
    const frames = (c && Array.isArray(c.question_frames) ? c.question_frames : [])
      .filter((f) => typeof f === 'string' && BLANK.test(f)).slice(0, 2);
    if (!frames.length) return base;
    return { next: base.next, items: frames.map((f, i) => [`내 질문에서 ${i + 1}`, f]) };
  }

  function renderQuestionGuide(guide, draft) {
    if (guide) {
      $('thread').append(el('div', { class: 'opts' },
        el('span', { class: 'k', text: `↑ ${guide.next}` }),
        el('span', {class: 'muted', text: '빈칸에 교안의 개념, 수업 사례, 판단 기준이나 근거를 넣어 보세요.'}),
        ...guide.items.map(([k, tpl]) => el('button', { class: 'opt', type: 'button', onclick: () => {
          if (draft) {
            const latest = drafts.get(draft.id);
            if (!latest || latest.revision_id !== draft.revision_id || !selectable(latest)) return;
            if (selected.size > 1) $('question-disclosure').open = true;
            if (!editDraft(latest)) return;
          }
          useTemplate(tpl);
        } },
          el('b', { text: k }), ` · ${tpl}`))));
      scrollThread();
    }
  }

  function useTemplate(tpl) {
    const t = $('q-input');
    t.value = tpl;
    updateComposer();
    t.focus();
    const i = tpl.indexOf('___');
    if (i >= 0) t.setSelectionRange(i, i + 3);
  }

  $('send-now').addEventListener('click', submitCurrent);

  async function submitCurrent() {
    if (!st.current || batchSupported) return;
    try {
      const res = await api(`/v1/session-questions/${st.current.id}/submit`, { method: 'POST' });
      st.sentCount += 1;
      showSent(res);
    } catch (err) {
      coachMsg(el('span', { text: err.status === 423 ? '수업이 끝나 보낼 수 없어요.' : err.message }));
    }
  }

  /* ---------- 제출 완료 ---------- */
  function showSent(res) {
    showScreen('sent');
    const card = $('sent-card');
    card.replaceChildren(
      el('span', { class: `lv ${res.level}`, text: levelText(res.level) }),
      el('b', { text: res.text }),
      res.original && res.original !== res.text ? el('span', { class: 'muted', text: `처음 질문: ${res.original}` }) : null);
    $('sent-mine').textContent = `내가 보낸 질문 ${st.sentCount}개`;
    $('ask-another').disabled = !openForQuestions();
  }
  $('ask-another').addEventListener('click', () => { showScreen('coach'); resetThread(); });
  $('sent-mine').addEventListener('click', showMine);
  $('to-mine').addEventListener('click', showMine);

  /* ---------- 내 질문 ---------- */
  async function showMine() {
    showScreen('mine');
    const list = $('mine-list');
    list.replaceChildren(el('div', { class: 'progress indeterminate' }, el('span')));
    try {
      const data = await api(`/v1/sessions/${st.session.id}/mine`);
      st.session = Object.assign(st.session, data.session);
      paintMine(data.items);
    } catch (err) {
      list.replaceChildren(el('p', { class: 'error', text: err.message }));
    }
  }

  function paintMine(items) {
    const closed = st.session.status === 'CLOSED';
    $('mine-course').textContent = st.session.course_title || '';
    $('mine-status').textContent = closed ? '수업 종료' : st.session.status === 'PAUSED' ? '잠시 멈춤' : '수업 중';
    $('mine-status').className = closed ? 'chip' : 'chip ok';
    $('mine-closed').hidden = !closed;
    $('mine-back').hidden = closed;
    $('mine-rejoin').hidden = !closed;
    const sent = items.filter(q => q.submitted || q.suitability?.status === 'eligible');
    if (!sent.length) {
      $('mine-list').replaceChildren(el('div', { class: 'empty', text: '아직 작성한 질문이 없어요.' }));
      return;
    }
    $('mine-list').replaceChildren(...sent.map((q) => el('div', { class: 'sent-card', id: 'my-indexed-' + q.id },
      el('div', { class: 'row between' }, el('span', { class: `lv ${q.level}`, text: levelText(q.level) }),
        el('span', { class: 'muted', text: q.submitted_at ? new Date(q.submitted_at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : '' })),
      el('span', {class: 'muted', text: [q.slide_page ? q.slide_page + '쪽' : '', q.submitted ? '전송됨' : '작성 중'].filter(Boolean).join(' · ')}),
      q.original !== q.text ? el('span' , { class: 'muted', text: `처음 질문 · ${q.original}` }) : null,
      el('b', { text: q.original !== q.text ? `다시 쓴 질문 · ${q.text}` : q.text }))));
  }
  $('mine-back').addEventListener('click', () => { showScreen('coach'); updateComposer(); });

  /* ---------- 교안 넘겨보기 (교수자가 연 쪽만) ---------- */
  const browse = { pages: [], page: null, live: null, open: false, request: 0 };

  function applyBrowse(pages) {
    const same = pages.length === browse.pages.length && pages.every((p, i) => p === browse.pages[i]);
    if (same) return;
    browse.pages = pages.slice();
    const button = $('browse-open');
    button.hidden = !pages.length;
    button.textContent = `교안 넘겨보기 (${pages.length}쪽)`;
    const select = $('browse-select');
    select.replaceChildren(...pages.map(p => el('option', { value: String(p), text: `${p}쪽` })));
    if (!pages.length) { closeBrowse(); return; }
    if (browse.open) showBrowsePage(pages.includes(browse.page) ? browse.page : nearestBrowsePage(browse.page));
  }

  function nearestBrowsePage(target) {
    if (!browse.pages.length) return null;
    if (target == null) return browse.pages[0];
    return browse.pages.reduce((best, p) => Math.abs(p - target) < Math.abs(best - target) ? p : best, browse.pages[0]);
  }

  function openBrowse() {
    if (!browse.pages.length) return;
    browse.open = true;
    $('student-browse').hidden = false;
    $('browse-open').setAttribute('aria-expanded', 'true');
    showBrowsePage(browse.pages.includes(browse.live) ? browse.live : nearestBrowsePage(browse.page ?? browse.live));
    $('student-browse').scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }

  function closeBrowse() {
    browse.open = false;
    browse.request++;
    $('student-browse').hidden = true;
    $('browse-open').setAttribute('aria-expanded', 'false');
    $('browse-view').replaceChildren();
  }

  async function showBrowsePage(page) {
    if (page == null) return;
    browse.page = page;
    const index = browse.pages.indexOf(page);
    $('browse-select').value = String(page);
    $('browse-prev').disabled = index <= 0;
    $('browse-next').disabled = index < 0 || index >= browse.pages.length - 1;
    $('browse-live').hidden = !browse.live || !browse.pages.includes(browse.live) || browse.live === page;
    const request = ++browse.request;
    const view = $('browse-view');
    $('browse-status').textContent = `${page}쪽을 불러오는 중…`;
    try {
      const bytes = await api(`/v1/sessions/${st.session.id}/browse/${page}`, { raw: true }).then(r => r.arrayBuffer());
      if (request !== browse.request) return;
      const width = Math.max(240, view.clientWidth - 24);
      const canvas = await window.QU.renderPdfImage(bytes, width, Math.max(320, innerHeight * 0.8), `${page}쪽 교안`);
      if (request !== browse.request) return;
      canvas.addEventListener('contextmenu', e => e.preventDefault());
      view.replaceChildren(canvas);
      $('browse-status').textContent = `${page}쪽 · ${index + 1} / ${browse.pages.length}`;
    } catch (err) {
      if (request !== browse.request) return;
      view.replaceChildren();
      $('browse-status').textContent = err.status === 403 ? '교수자가 이 쪽의 열람을 닫았어요.'
        : err.status === 409 ? '수업이 끝나 교안을 볼 수 없어요.' : '교안을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.';
    }
  }

  function stepBrowse(delta) {
    const next = browse.pages[browse.pages.indexOf(browse.page) + delta];
    if (next != null) showBrowsePage(next);
  }

  $('browse-open').addEventListener('click', () => (browse.open ? closeBrowse() : openBrowse()));
  $('browse-close').addEventListener('click', closeBrowse);
  $('browse-prev').addEventListener('click', () => stepBrowse(-1));
  $('browse-next').addEventListener('click', () => stepBrowse(1));
  $('browse-live').addEventListener('click', () => showBrowsePage(browse.live));
  $('browse-select').addEventListener('change', e => showBrowsePage(Number(e.target.value)));
  $('student-browse').addEventListener('keydown', e => {
    if (e.target.closest('select')) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); stepBrowse(-1); }
    if (e.key === 'ArrowRight') { e.preventDefault(); stepBrowse(1); }
  });

  /* ---------- 수업 상태 ---------- */
  function applyChat(open) {
    const wasOpen = chatOpen();
    st.session.chat_open = open;
    const coach = $('screen-coach');
    coach.classList.toggle('is-chat-locked', !open);
    $('chat-lock').hidden = open || st.session.status === 'CLOSED';
    updateComposer();
    paintDrafts();
    if (open && !wasOpen && !coach.hidden && matchMedia('(pointer: fine)').matches) $('q-input').focus({preventScroll: true});
  }

  function applyStatus(status) {
    st.session.status = status;
    const banner = $('status-banner');
    banner.hidden = status === 'OPEN';
    banner.textContent = status === 'PAUSED' ? '교수자가 잠시 질문 받기를 멈췄어요. 다시 열리면 보낼 수 있어요.'
      : status === 'CLOSED' ? '수업이 끝났어요. 보낸 질문은 “내 질문”에서 볼 수 있어요.' : '';
    updateComposer();
    paintDrafts();
    scheduleDraftPoll();
    if (status === 'CLOSED') {
      stopPoll();
      if (!$('screen-coach').hidden || !$('screen-sent').hidden) showMine();
    }
  }

  async function pollStatus() {
    if (!st.session || !token) return;
    try {
      const s = await api(`/v1/sessions/${st.session.id}`);
      if (Array.isArray(s.browse_pages)) applyBrowse(s.browse_pages);
      if (s.chat_open !== chatOpen()) applyChat(s.chat_open === true);
      if (s.status !== st.session.status) applyStatus(s.status);
    } catch (_) { /* transient; next tick retries */ }
  }

  /* ---------- 시작 ---------- */
  showScreen('join');
  const preset = (new URLSearchParams(location.search).get('code') || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (preset.length === 6) { $('code').value = preset; join(preset); } else { $('code').focus(); }
})();
