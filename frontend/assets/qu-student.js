/* Question-up · 학생 흐름
   코드 입장 → 대화형 질문 코치(콩나무) → 교수자에게 보내기 → 내 질문
   익명 토큰은 메모리에만 둡니다. 탭을 닫으면 사라집니다. */
(() => {
  'use strict';
  const { el, client, LEVEL, plant } = window.QU;
  const $ = (id) => document.getElementById(id);

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
    stopPoll();
    showScreen('join');
    setError($('join-error'), '입장 시간이 만료됐습니다. 코드를 다시 입력해 주세요.');
  });

  const STAGE_TEXT = { seed: '아직 씨앗', shallow: '얕은 질문', mid: '중간 질문', deep: '깊은 질문' };
  const GROWTH = {
    shallow: '싹이 텄어요 · 얕은 질문',
    mid: '한 마디 자랐어요 · 중간 질문',
    deep: '구름 위까지 자랐어요 · 깊은 질문',
  };
  const RANK = { seed: 0, shallow: 1, mid: 2, deep: 3 };
  const TEMPLATES = {
    shallow: { next: '중간 질문으로 자라기', items: [
      ['비교', '___와 ___는 무엇이 다른가요?'],
      ['정의', '___를 한 문장으로 정의하면 무엇인가요?'],
      ['이유', '___는 왜 중요한가요?'],
    ] },
    mid: { next: '깊은 질문으로 자라기', items: [
      ['적용', '___를 ___ 상황에 적용하면 어떻게 되나요?'],
      ['원인', '왜 ___에서 ___가 생길까요?'],
      ['평가', '___라고 볼 수 있는 근거는 무엇인가요?'],
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

  function expanded() {
    return document.fullscreenElement === slidePanel || slidePanel.classList.contains('live-expanded');
  }

  function updateFullscreen() {
    const active = expanded();
    const label = active ? '교안 전체화면 종료' : '교안 전체화면';
    expandButton.setAttribute('aria-label', label);
    expandButton.title = label;
    if (!active) slidePanel.querySelector('[data-live-actions]').classList.remove('selection-actions');
    expandButton.setAttribute('aria-pressed', String(active));
    liveView?.resize();
  }

  async function collapseSlide() {
    if (document.fullscreenElement === slidePanel && document.exitFullscreen) {
      await document.exitFullscreen().catch(() => {});
    }
    if (slidePanel.classList.contains('live-expanded')) {
      slidePanel.classList.remove('live-expanded');
      updateFullscreen();
    }
  }

  expandButton.addEventListener('click', async () => {
    if (expanded()) return collapseSlide();
    try {
      if (slidePanel.requestFullscreen) {
        await slidePanel.requestFullscreen();
        return;
      }
    } catch (_) { /* Use the viewport on browsers that deny native fullscreen. */ }
    slidePanel.classList.add('live-expanded');
    updateFullscreen();
  });
  document.addEventListener('fullscreenchange', updateFullscreen);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && slidePanel.classList.contains('live-expanded')) collapseSlide();
  });
  function stopPoll() {
    liveView?.stop();
    liveView = null;
    if (expanded()) collapseSlide();
    slidePanel.hidden = true;
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

  function coachMsg(...children) {
    const node = el('div', { class: 'msg' }, el('span', { class: 'av', 'aria-hidden': 'true', text: 'Q' }), el('div', { class: 'bubble' }, ...children));
    $('thread').append(node);
    scrollThread();
    return node;
  }
  function meMsg(text) {
    $('thread').append(el('div', { class: 'msg me' }, el('div', { class: 'bubble', text })));
    scrollThread();
  }

  /* ---------- 입장 ---------- */
  $('code').addEventListener('input', (e) => {
    e.target.value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6);
    setError($('join-error'), null);
  });

  async function join(code) {
    const btn = $('join-btn');
    btn.disabled = true; btn.textContent = '입장 중…';
    try {
      const res = await api('/v1/join', { method: 'POST', body: { code } });
      token = res.access_token;
      st.session = res.session;
      history.replaceState(null, '', location.pathname + (new URLSearchParams(location.search).get('api') ? `?api=${encodeURIComponent(new URLSearchParams(location.search).get('api'))}` : ''));
      startCoach(res.initial_live);
    } catch (err) {
      const msg = err.status === 404 ? '열려 있는 수업을 찾지 못했어요. 코드를 다시 확인해 주세요.'
        : err.status === 423 ? '교수자가 잠시 질문 받기를 멈췄어요. 조금 뒤 다시 입장해 주세요.' : err;
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
    resetThread();
    applyStatus(st.session.status);
    stopPoll();
    liveView = window.QU.createLiveView({
      api, session: st.session.id, panel: $('student-slide'), initial,
      onAnchor: ({page, anchor}) => {
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
      onState: ({status}) => {
        if (status !== st.session.status) applyStatus(status);
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
    $('q-input').placeholder = '궁금한 점을 질문으로 써 보세요';
    updateComposer();
    $('q-input').focus();
  }

  function updateComposer() {
    const v = $('q-input').value.trim();
    const open = st.session && st.session.status === 'OPEN';
    const hasBlank = BLANK.test(v);
    $('send').disabled = !open || st.sending || v.length < 5 || hasBlank;
    $('hint').textContent = hasBlank ? '빈칸(___)이 아직 남아 있어요. 내 생각으로 채워 주세요'
      : st.current ? '고친 질문을 보내면 다시 코칭해 드려요' : '다른 학생에게는 내 이름이 보이지 않아요';
    $('send-now').hidden = !st.current || !open;
    $('to-mine').hidden = st.sentCount === 0;
  }
  $('q-input').addEventListener('input', () => {
    const t = $('q-input');
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
    st.sending = true; updateComposer();
    meMsg(text);
    $('q-input').value = ''; $('q-input').style.height = '';
    const typing = el('p', { class: 'typing', text: '질문을 살펴보는 중…' });
    $('thread').append(typing); scrollThread();
    try {
      const res = st.current
        ? await api(`/v1/session-questions/${st.current.id}/revisions`, { method: 'POST', body: { text }, timeout: 90000 })
        : await api(`/v1/sessions/${st.session.id}/questions`, { method: 'POST', body: { question: text, slide_page: questionAnchor?.page ?? liveView?.page(), anchor: questionAnchor?.anchor }, timeout: 90000 });
      typing.remove();
      if (!st.current) st.current = { id: res.id, original: text };
      st.current.level = res.level;
      st.current.text = text;
      renderCoaching(res.level, res.coaching);
    } catch (err) {
      typing.remove();
      if (err.status === 423) { await pollStatus(); }
      coachMsg(el('span', { text: err.status === 423 ? '지금은 질문을 받지 않는 중이에요. 교수자가 다시 열면 이어서 보낼 수 있어요.'
        : err.status === 409 ? '이미 교수자에게 보낸 질문이에요. 새 질문을 써 주세요.' : `${err.message} 다시 보내 볼까요?` }));
      if (err.status === 409) st.current = null;
      if (err.status !== 409) $('q-input').value = text;
    } finally {
      st.sending = false; updateComposer();
    }
  });

  function renderCoaching(level, c) {
    const grew = RANK[level] > RANK[st.stage];
    if (grew) {
      const chip = el('div', { class: 'growth' });
      chip.append(plant(level), document.createTextNode(GROWTH[level]));
      $('thread').append(chip);
    }
    setStage(level);

    const parts = [el('span', { class: `lv ${level}`, text: `${LEVEL[level]} 질문` })];
    if (c && c.feedback) parts.push(el('span', { text: c.feedback }));
    if (c && c.follow_up_question) parts.push(el('span', {}, el('b', { text: '생각해 볼 질문 · ' }), c.follow_up_question));
    if (!c || (!c.feedback && !c.follow_up_question)) {
      parts.push(el('span', { text: level === 'deep' ? '수업 내용을 새로운 상황으로 확장하는 깊은 질문이에요.'
        : '좋은 출발이에요. 한 마디 더 키워 볼까요?' }));
    }
    const pages = c && c.evidence_refs ? [...new Set(c.evidence_refs.map((r) => r.page).filter(Boolean))] : [];
    if (pages.length) parts.push(el('div', { class: 'row' }, ...pages.slice(0, 4).map((p) => el('span', { class: 'evidence', text: `근거 · 교안 p.${p}` }))));

    if (level === 'deep') {
      parts.push(el('b', { text: '이 질문을 교수자에게 보낼까요?' }));
      parts.push(el('div', { class: 'row' },
        el('button', { class: 'btn primary', type: 'button', text: '교수자에게 보내기', onclick: submitCurrent }),
        el('button', { class: 'btn', type: 'button', text: '더 다듬기', onclick: () => { $('q-input').value = st.current.text; updateComposer(); $('q-input').focus(); } })));
      coachMsg(...parts);
      return;
    }
    coachMsg(...parts);
    const t = TEMPLATES[level];
    if (t) {
      $('thread').append(el('div', { class: 'opts' },
        el('span', { class: 'k', text: `↑ ${t.next}` }),
        ...t.items.map(([k, tpl]) => el('button', { class: 'opt', type: 'button', onclick: () => useTemplate(tpl) },
          el('b', { text: k }), ` · ${tpl}`))));
      scrollThread();
    }
    $('q-input').placeholder = '빈칸(___)을 내 생각으로 채워 다시 보내세요';
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
    if (!st.current) return;
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
      el('span', { class: `lv ${res.level}`, text: `${LEVEL[res.level]} 질문` }),
      el('b', { text: res.text }),
      res.original && res.original !== res.text ? el('span', { class: 'muted', text: `처음 질문: ${res.original}` }) : null);
    $('sent-mine').textContent = `내가 보낸 질문 ${st.sentCount}개`;
    $('ask-another').disabled = st.session.status !== 'OPEN';
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
    const sent = items;
    if (!sent.length) {
      $('mine-list').replaceChildren(el('div', { class: 'empty', text: '아직 작성한 질문이 없어요.' }));
      return;
    }
    $('mine-list').replaceChildren(...sent.map((q) => el('div', { class: 'sent-card', id: 'my-indexed-' + q.id },
      el('div', { class: 'row between' }, el('span', { class: `lv ${q.level}`, text: `${q.level_label} 질문` }),
        el('span', { class: 'muted', text: q.submitted_at ? new Date(q.submitted_at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : '' })),
      el('span', {class: 'muted', text: [q.slide_page ? q.slide_page + '쪽' : '', q.submitted ? '전송됨' : '작성 중'].filter(Boolean).join(' · ')}),
      q.original !== q.text ? el('span' , { class: 'muted', text: `처음 질문 · ${q.original}` }) : null,
      el('b', { text: q.original !== q.text ? `다시 쓴 질문 · ${q.text}` : q.text }))));
  }
  $('mine-back').addEventListener('click', () => { showScreen('coach'); if (!st.current) resetThread(); updateComposer(); });

  /* ---------- 수업 상태 ---------- */
  function applyStatus(status) {
    st.session.status = status;
    const banner = $('status-banner');
    banner.hidden = status === 'OPEN';
    banner.textContent = status === 'PAUSED' ? '교수자가 잠시 질문 받기를 멈췄어요. 다시 열리면 보낼 수 있어요.'
      : status === 'CLOSED' ? '수업이 끝났어요. 보낸 질문은 “내 질문”에서 볼 수 있어요.' : '';
    updateComposer();
    if (status === 'CLOSED') {
      stopPoll();
      if (!$('screen-coach').hidden || !$('screen-sent').hidden) showMine();
    }
  }

  async function pollStatus() {
    if (!st.session || !token) return;
    try {
      const s = await api(`/v1/sessions/${st.session.id}`);
      if (s.status !== st.session.status) applyStatus(s.status);
    } catch (_) { /* transient; next tick retries */ }
  }

  /* ---------- 시작 ---------- */
  showScreen('join');
  const preset = (new URLSearchParams(location.search).get('code') || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (preset.length === 6) { $('code').value = preset; join(preset); } else { $('code').focus(); }
})();
