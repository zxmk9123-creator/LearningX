/* Question-up unit learning objective step (between upload and evidence approval).
 * Order follows backward design: objective -> evidence to observe -> coaching.
 * Each thinking process gets a 1-5 emphasis level (default: the middle, 3); their
 * weighted average picks the process coaching aims at (mirrors objectives.py).
 * Pure helpers live on window.QU.objectiveRules so they can be tested alone. */
(() => {
  'use strict';
  const { el } = window.QU;

  const PROCESSES = [
    ['remember', '기억', '핵심 사실이나 용어를 확인한다.', '모형의 구성 요소 두 가지를 식별한다.'],
    ['understand', '이해', '의미나 관계를 자신의 말로 설명한다.', '두 개념을 예시로 구분한다.'],
    ['apply', '적용', '개념이나 절차를 주어진 사례에 사용한다.', '사례에 적용할 공식을 고르고 계산한다.'],
    ['analyze', '분석', '부분·관계·가정을 구분한다.', '두 자료의 차이를 찾아 가능한 설명을 나눈다.'],
    ['evaluate', '평가', '명시한 기준으로 주장이나 방법을 판단한다.', '신뢰성 기준을 제시하고 자료로 판단을 정당화한다.'],
    ['create', '창안', '목적과 제약에 맞는 새 방안이나 산출물을 구성한다.', '대안을 제안하고 제약을 어떻게 충족하는지 설명한다.'],
  ];
  // Emphasis levels 1..5; level 1 weighs 0 in the average.
  const LEVELS = ['다루지 않음', '낮음', '보통', '높음', '매우 높음'];
  const LEVEL_DEFAULT = 3;
  const SUPPORT = [
    ['minimal', '힌트 최소', '필요한 자료나 관점만 짧게 묻습니다.'],
    ['directional', '방향 제시', '살펴볼 방향을 제시하며 묻습니다.'],
    ['stepwise', '단계별 안내', '학생이 바로 할 수 있는 첫 단계를 구체적으로 묻습니다.'],
  ];
  const KNOWLEDGE = [['', '선택 안 함'], ['factual', '사실'], ['conceptual', '개념'], ['procedural', '절차'], ['metacognitive', '메타인지']];
  const CONTEXT = [['', '선택 안 함'], ['within_lesson', '교안 내부'], ['similar_case', '유사 사례'], ['new_situation', '새로운 상황']];
  const KIND = { goal: '추가 목표', evidence: '관찰 증거', guideline: '코칭 지침' };
  const REASON = {
    answer_disclosure: '정답·해설 공개 요청은 적용하지 않아요.',
    grading: '성적·점수 관련 지시는 적용하지 않아요.',
    permission_change: '권한 변경은 적용하지 않아요.',
    unapproved_material: '승인되지 않은 자료 사용은 적용하지 않아요.',
    evidence_unclear: '기타 입력에서 학생이 무엇을 어떻게 보여줄지 적고 다시 해석해 주세요. 예: 두 자료의 차이를 근거 하나로 설명한다.',
  };
  const UNASSESSED_REASON = { model_not_configured: 'AI 모델이 설정되지 않음', guest_model_disabled: '체험 수업은 AI 모델을 쓰지 않음',
    model_timeout: 'AI 응답 시간 초과', model_validation_or_transport_failed: 'AI 응답 검증 실패',
    no_matching_approved_evidence: '일치하는 승인 근거 없음', no_matching_approved_evidence_on_page: '그 쪽에 일치하는 승인 근거 없음',
    explicit_page_not_approved: '승인되지 않은 쪽 지정' };
  const RELEVANCE = { direct: '목표에 직접 연결', prerequisite: '선행 이해에 도움', out_of_unit: '단원 밖',
    insufficient_context: '맥락 부족' };
  const WARNING = { objective_not_observable: '학습목표가 관찰하기 어려운 표현이에요. 학생이 말하거나 수행할 행동으로 바꿔 보세요.',
    evidence_not_observable: '수행 증거가 관찰하기 어려운 표현이에요.' };
  const label = (table, key) => (table.find(([k]) => k === key) || [])[1] || '';

  /* ---------- pure rules (tested in smoke.mjs) ---------- */
  const rules = {
    PROCESSES, LEVELS, SUPPORT, REASON, RELEVANCE, UNASSESSED_REASON, LEVEL_DEFAULT,
    defaultWeights: () => Object.fromEntries(PROCESSES.map(([k]) => [k, LEVEL_DEFAULT])),
    // Position 1.0 (기억) .. 6.0 (창안); all at level 1 is neutral.
    weightedMean(weights) {
      const total = PROCESSES.reduce((sum, [k]) => sum + weights[k] - 1, 0);
      if (!total) return 3.5;
      return Math.round(PROCESSES.reduce((sum, [k], i) => sum + (i + 1) * (weights[k] - 1), 0) / total * 100) / 100;
    },
    // Nearest process to the weighted mean, halves rounded up (all default -> 분석).
    focus(weights) {
      const position = Math.floor(rules.weightedMean(weights) + 0.5);
      return PROCESSES[Math.min(PROCESSES.length, Math.max(1, position)) - 1][0];
    },
    // Profiles approved before levels existed: their single process at the top, the rest unset.
    weightsOf(profile) {
      if (profile.process_weights) return { ...rules.defaultWeights(), ...profile.process_weights };
      return Object.fromEntries(PROCESSES.map(([k]) => [k, k === profile.process ? 5 : 1]));
    },
    emptyDraft: () => ({ objective: '', weights: rules.defaultWeights(), evidence: '', support: 'directional',
      knowledge_type: '', application_context: '', other_raw: '', other_items: [] }),
    fromProfile(profile) {
      if (!profile) return rules.emptyDraft();
      return { objective: profile.objective, weights: rules.weightsOf(profile), evidence: profile.evidence,
        support: profile.support, knowledge_type: profile.knowledge_type || '',
        application_context: profile.application_context || '', other_raw: profile.other_raw || '',
        other_items: (profile.other_items || []).map(({ kind, text }) => ({ kind, text })) };
    },
    // Request body for POST /objective-profiles; optional choices are omitted when unset.
    payload(draft) {
      const body = { objective: draft.objective.trim(),
        process_weights: Object.fromEntries(PROCESSES.map(([k]) => [k, draft.weights[k]])), evidence: draft.evidence.trim(),
        support: draft.support, other_items: draft.other_items.map(({ kind, text }) => ({ kind, text: text.trim() }))
          .filter((item) => item.text) };
      if (draft.knowledge_type) body.knowledge_type = draft.knowledge_type;
      if (draft.application_context) body.application_context = draft.application_context;
      if (draft.other_raw.trim()) body.other_raw = draft.other_raw.trim();
      return body;
    },
    missing(draft) {
      const missing = [];
      if (draft.objective.trim().length < 5) missing.push('학습목표');
      if (draft.evidence.trim().length < 5) missing.push('확인할 수행 증거');
      return missing;
    },
    same: (a, b) => JSON.stringify(rules.payload(a)) === JSON.stringify(rules.payload(b)),
    summary(profile) {
      if (!profile) return '미설정 · 기존 코칭 흐름을 사용합니다';
      return `v${profile.version} · ${label(PROCESSES, profile.process)} 중심 · ${label(SUPPORT, profile.support)} · ${profile.objective}`;
    },
  };

  /* ---------- form ---------- */
  function createObjectiveForm({ api, root, status, onState }) {
    let materialId = null;
    let saved = null;
    let draft = rules.emptyDraft();
    let interpretation = null;
    let preview = null;
    let previewQuestion = '';
    let busy = false;
    let token = 0;
    const open = { optional: false, other: false, preview: false };

    const uid = (name) => `obj-${name}`;
    const byId = (name) => document.getElementById(uid(name));
    const say = (text) => { status.textContent = text || ''; };
    const dirty = () => !rules.same(draft, rules.fromProfile(saved));
    const state = () => ({ saved, dirty: dirty(), missing: rules.missing(draft), busy });
    const notify = () => onState?.(state());
    const set = (patch) => { Object.assign(draft, patch); render(); };

    // Required cards carry a visible "*" (read as "필수" by screen readers).
    function card(title, hint, ...children) {
      const required = title.endsWith('*');
      const name = required ? title.slice(0, -1).trim() : title;
      return el('section', { class: 'card obj-card' },
        el('div', { class: 'obj-card-head' }, el('h2', { class: 'obj-title' }, name,
          required ? el('span', { class: 'obj-req', 'aria-hidden': 'true', text: '*' }) : null,
          required ? el('span', { class: 'sr-only', text: ' (필수)' }) : null),
          hint ? el('p', { class: 'muted small', text: hint }) : null),
        ...children);
    }

    function fold(key, title, hint, ...children) {
      const details = el('details', { class: 'card obj-card obj-fold', open: open[key] },
        el('summary', {}, el('span', { class: 'obj-title', text: title }), hint ? el('span', { class: 'muted small', text: hint }) : null),
        el('div', { class: 'stack obj-fold-body' }, ...children));
      details.addEventListener('toggle', () => { open[key] = details.open; });
      return details;
    }

    function textarea(id, name, value, key, rows = 2, max = 300, placeholder = '', required = false) {
      return el('textarea', { id: uid(id), class: 'obj-text', rows, maxlength: max, text: value, placeholder,
        'aria-label': name, 'aria-required': required ? 'true' : null, oninput: (e) => { draft[key] = e.target.value; refresh(); } });
    }

    // One 5-level slider per process: native range inputs (arrow keys, Home/End, touch) drawn as
    // pill tracks. They are updated in place, never rebuilt, so a drag in progress is not interrupted.
    function scale() {
      const rows = PROCESSES.map(([key, name, description]) => el('div', { class: 'obj-wrow' },
        el('div', { class: 'obj-wrow-name' }, el('b', { text: name }), el('span', { class: 'small muted', text: description })),
        el('div', { class: 'obj-scale-track sm', id: uid(`w-${key}-track`) },
          el('input', { type: 'range', id: uid(`w-${key}`), class: 'obj-range', min: 1, max: LEVELS.length, step: 1,
            value: draft.weights[key], 'aria-label': `${name} 강조 수준`,
            oninput: (e) => setLevel(key, Number(e.target.value)) }),
          el('div', { class: 'obj-scale-dots', 'aria-hidden': 'true' }, ...LEVELS.map(() => el('span', { class: 'obj-scale-dot' })))),
        el('span', { class: 'obj-wrow-level', id: uid(`w-${key}-level`), 'aria-hidden': 'true' })));
      return el('div', { class: 'obj-scale' },
        el('div', { class: 'obj-scale-head' },
          el('span', { class: 'obj-scale-kicker', text: '코칭이 향할 사고 과정' }),
          el('span', { class: 'obj-scale-value', id: uid('focus') }),
          el('span', { class: 'obj-scale-desc', id: uid('focus-desc') }),
          el('div', { class: 'obj-mean', 'aria-hidden': 'true' },
            el('div', { class: 'obj-mean-track' }, el('span', { class: 'obj-mean-pin', id: uid('mean-pin') })),
            el('div', { class: 'obj-mean-labels' }, ...PROCESSES.map(([, name]) => el('span', { text: name })))),
          el('span', { class: 'sr-only', id: uid('focus-live'), role: 'status', 'aria-live': 'polite' })),
        el('div', { class: 'obj-wrows' }, ...rows),
        el('div', { class: 'row obj-scale-foot' },
          el('span', { class: 'muted small grow', text: '1 다루지 않음 · 3 보통 · 5 매우 높음. 강조 수준을 가중치로 평균 낸 위치에 가장 가까운 과정으로 되물어요.' }),
          el('button', { class: 'btn sm', type: 'button', id: uid('w-reset'), text: '모두 보통으로', onclick: () => {
            draft.weights = rules.defaultWeights(); paintScale(); refresh(); } })));
    }

    function setLevel(key, level) {
      draft.weights[key] = level;
      paintScale();
      refresh();
    }

    function paintScale() {
      if (!byId('focus')) return;
      const focus = rules.focus(draft.weights);
      const mean = rules.weightedMean(draft.weights);
      const [, name, description, example] = PROCESSES.find(([k]) => k === focus);
      PROCESSES.forEach(([key, pname]) => {
        const level = draft.weights[key];
        const range = byId(`w-${key}`);
        if (Number(range.value) !== level) range.value = String(level);
        range.setAttribute('aria-valuetext', `${pname} ${level}단계, ${LEVELS[level - 1]}`);
        const track = byId(`w-${key}-track`);
        track.style.setProperty('--fill', String((level - 1) / (LEVELS.length - 1)));
        track.querySelectorAll('.obj-scale-dot').forEach((dot, i) => dot.classList.toggle('is-filled', i < level));
        byId(`w-${key}-level`).textContent = LEVELS[level - 1];
        byId(`w-${key}-level`).classList.toggle('is-off', level === 1);
        range.closest('.obj-wrow').classList.toggle('is-focus', key === focus);
      });
      byId('focus').textContent = name;
      byId('focus-desc').textContent = `가중 평균 ${mean.toFixed(1)} · ${description}`;
      byId('mean-pin').style.setProperty('--pos', String((mean - 1) / (PROCESSES.length - 1)));
      if (byId('focus-live').dataset.focus !== focus) {
        byId('focus-live').dataset.focus = focus;
        byId('focus-live').textContent = `코칭이 향할 사고 과정: ${name}`;
      }
      if (byId('evidence')) byId('evidence').placeholder = example;
      if (byId('evidence-example')) byId('evidence-example').textContent = `예시 · ${example}`;
    }

    function supportControl() {
      return el('div', { class: 'obj-seg', role: 'radiogroup', 'aria-label': '코칭 지원 정도' },
        ...SUPPORT.map(([value, text, hint]) => el('label', { class: 'obj-seg-item' + (draft.support === value ? ' is-on' : '') },
          el('input', { type: 'radio', name: uid('support'), id: uid(`support-${value}`), value, checked: draft.support === value,
            onchange: () => set({ support: value }) }),
          el('b', { text: text }), el('span', { class: 'small', text: hint }))));
    }

    function selectField(id, text, options, value, key) {
      return el('label', { class: 'field obj-select', for: uid(id) }, el('span', { class: 'k', text }),
        el('select', { id: uid(id), onchange: (e) => set({ [key]: e.target.value }) },
          ...options.map(([v, t]) => el('option', { value: v, text: t, selected: v === value }))));
    }

    function otherSection() {
      const proposals = interpretation ? [
        ...interpretation.applicable.filter((p) => !draft.other_items.some((i) => i.kind === p.kind && i.text === p.text))
          .map((p) => el('div', { class: 'obj-row' },
            el('span', { class: 'obj-kind', text: KIND[p.kind] }), el('span', { class: 'grow', text: p.text }),
            el('button', { class: 'btn sm', type: 'button', text: '적용', onclick: () => { draft.other_items.push({ kind: p.kind, text: p.text }); render(); } }))),
        ...interpretation.held.map((p) => el('div', { class: 'obj-row is-held' },
          el('span', { class: 'obj-kind', text: p.status === 'rejected' ? '적용 안 함' : '보류' }),
          el('span', { class: 'grow', text: p.text }), el('span', { class: 'muted small', text: REASON[p.reason] || '' }))),
        ...interpretation.conflicts.map((c) => el('div', { class: 'obj-row is-conflict' },
          el('span', { class: 'grow', text: c.field === 'process'
            ? `“${c.item}”은(는) ${label(PROCESSES, c.suggested)} 과정에 가까워요. 현재 중심은 ${label(PROCESSES, c.current)}입니다.`
            : `“${c.item}”은(는) ${label(SUPPORT, c.suggested)}에 가까워요. 현재는 ${label(SUPPORT, c.current)}입니다.` }),
          el('button', { class: 'btn sm', type: 'button', text: c.field === 'process' ? `${label(PROCESSES, c.suggested)} 강조 높이기` : '제안 값 적용', onclick: () => {
            interpretation.conflicts = interpretation.conflicts.filter((x) => x !== c);
            if (c.field === 'process') set({ weights: { ...draft.weights, [c.suggested]: LEVELS.length } });
            else set({ [c.field]: c.suggested }); } }),
          el('button', { class: 'btn sm', type: 'button', text: '현재 값 유지', onclick: () => {
            interpretation.conflicts = interpretation.conflicts.filter((x) => x !== c); render(); } }))),
        el('p', { class: 'muted small', text: interpretation.source === 'model'
          ? 'AI가 나눈 초안이에요. 적용할 항목만 고르고 문장을 다듬어 주세요.'
          : '문장 단위로 나눈 초안이에요. 적용할 항목만 고르고 문장을 다듬어 주세요.' }),
      ] : [];
      const manualKind = el('select', { class: 'obj-mini-select', 'aria-label': '직접 추가할 항목 종류' },
        ...Object.entries(KIND).map(([k, t]) => el('option', { value: k, text: t })));
      const items = draft.other_items.map((item, index) => el('div', { class: 'obj-row' },
        el('span', { class: 'obj-kind', text: KIND[item.kind] }),
        el('input', { class: 'input grow', type: 'text', maxlength: 200, value: item.text, 'aria-label': `${KIND[item.kind]} 문장`,
          oninput: (e) => { item.text = e.target.value; refresh(); } }),
        el('button', { class: 'btn sm', type: 'button', text: '빼기', onclick: () => { draft.other_items.splice(index, 1); render(); } })));
      return [
        textarea('other', '기타 학습목표', draft.other_raw, 'other_raw', 3, 1000,
          '예: 목표: 두 사료의 한계를 구분한다. 관찰 증거: 사료별 근거를 하나씩 제시한다. 코칭 지침: 한 번에 하나씩 되묻는다.'),
        el('div', { class: 'row' },
          el('button', { class: 'btn sm', type: 'button', id: uid('interpret'), text: '해석 미리보기', onclick: interpret }),
          manualKind,
          el('button', { class: 'btn sm', type: 'button', text: '항목 직접 추가', onclick: () => { draft.other_items.push({ kind: manualKind.value, text: '' }); render(); } })),
        ...proposals,
        draft.other_items.length ? el('b', { class: 'small', text: '적용할 항목' }) : null,
        ...items,
      ];
    }

    function previewSection() {
      const question = el('input', { class: 'input grow', id: uid('preview-q'), type: 'text', maxlength: 2000,
        placeholder: '샘플 학생 질문 (예: 평균이 커진 건 왜예요?)', value: previewQuestion, 'aria-label': '샘플 학생 질문',
        oninput: (e) => { previewQuestion = e.target.value; } });
      return [
        el('div', { class: 'row' }, question,
          el('button', { class: 'btn sm', type: 'button', id: uid('preview-run'), text: '미리보기', onclick: () => runPreview(previewQuestion) })),
        preview ? el('dl', { class: 'obj-preview' },
          el('dt', { text: '질문 진단' }), el('dd', { text: preview.diagnosis.level === 'unassessed'
            ? `판정 보류${preview.reason ? ` · ${UNASSESSED_REASON[preview.reason] || '판정하지 못함'}` : ''}` : `${preview.diagnosis.level_label} 질문` }),
          el('dt', { text: '목표 관련성' }), el('dd', { text: RELEVANCE[preview.goal_relevance] || '판정 보류' }),
          el('dt', { text: '다음 역질문' }), el('dd', { text: preview.follow_up_question || '—' }),
          preview.evidence_scope === 'all_candidates' ? el('dt', { text: '참고한 교안' }) : null,
          preview.evidence_scope === 'all_candidates'
            ? el('dd', { text: '아직 승인 전이라 교안의 모든 내용을 근거로 미리봤어요. 수업에서는 다음 단계에서 승인한 내용만 씁니다.' }) : null,
          preview.ignored_items.length ? el('dt', { text: '반영되지 않은 항목' }) : null,
          preview.ignored_items.length ? el('dd', { text: preview.ignored_items.map((i) => i.text).join(' / ') }) : null) : null,
      ];
    }

    function render() {
      // Re-rendering replaces the controls; keep keyboard focus (scale, radios) where it was.
      const focused = document.activeElement && root.contains(document.activeElement) ? document.activeElement.id : null;
      const focusExample = () => PROCESSES.find(([k]) => k === rules.focus(draft.weights))[3];
      const example = focusExample();
      root.replaceChildren(
        card('단원 학습목표 *', '“학생은 [조건·자료]를 사용해 [대상]을 [관찰 가능한 행동]할 수 있다.”',
          textarea('objective', '단원 학습목표', draft.objective, 'objective', 2, 300,
            '학생은 토론 전후 분포를 보고 평균 변화가 어느 집단의 변화에서 비롯됐는지 설명할 수 있다.', true)),
        card('목표 사고 과정', '이번 단원에서 각 사고 과정을 얼마나 강조할지 정해 주세요. 학생 질문의 수준 판정에는 쓰지 않아요.', scale()),
        card('확인할 수행 증거 *', '학생 설명에서 무엇이 보이면 목표에 다가갔다고 볼지 적어 주세요.',
          textarea('evidence', '확인할 수행 증거', draft.evidence, 'evidence', 2, 300, example, true),
          el('div', { class: 'row' }, el('span', { class: 'muted small grow', id: uid('evidence-example'), text: `예시 · ${example}` }),
            el('button', { class: 'btn sm', type: 'button', text: '예시 넣기',
              onclick: () => set({ evidence: focusExample() }) }))),
        card('코칭 지원 정도', '목표 수준과 별개로 역질문을 얼마나 구체적으로 할지 정해요.', supportControl()),
        fold('optional', '선택 항목', '지식 유형 · 적용 맥락',
          el('div', { class: 'obj-grid' },
            selectField('knowledge', '지식 유형', KNOWLEDGE, draft.knowledge_type, 'knowledge_type'),
            selectField('context', '적용 맥락', CONTEXT, draft.application_context, 'application_context'))),
        fold('other', '기타 학습목표', '자유 서술을 항목으로 나눠 승인해요', ...otherSection()),
        fold('preview', '챗봇 미리보기', '샘플 질문에 어떻게 되묻는지 확인해요', ...previewSection()));
      paintScale();
      if (focused) document.getElementById(focused)?.focus();
      refresh();
    }

    // Text inputs do not re-render (that would move the cursor); derived states refresh on every keystroke.
    function refresh() {
      if (byId('interpret')) byId('interpret').disabled = busy || draft.other_raw.trim().length < 2;
      if (byId('preview-run')) byId('preview-run').disabled = busy;
      notify();
    }

    async function interpret() {
      busy = true; refresh(); say('해석하는 중…');
      try {
        interpretation = await api(`/v1/materials/${materialId}/objective-profiles/interpret`, { method: 'POST',
          body: { text: draft.other_raw.trim(), process: rules.focus(draft.weights), support: draft.support } });
        say(interpretation.items.length ? '' : '나눌 항목을 찾지 못했어요. 직접 추가해 주세요.');
      } catch (err) {
        interpretation = null;
        say('해석하지 못했어요. 항목을 직접 추가해 주세요.');
      } finally { busy = false; render(); }
    }

    async function runPreview(question) {
      if (question.trim().length < 10) return say('샘플 질문을 10자 이상 입력해 주세요.');
      const missing = rules.missing(draft);
      if (missing.length) return say(`${missing.join(', ')}을(를) 먼저 적어 주세요.`);
      busy = true; refresh(); say('미리보는 중…');
      try {
        preview = await api(`/v1/materials/${materialId}/objective-profiles/preview`, { method: 'POST', timeout: 60000,
          body: { profile: rules.payload(draft), question: question.trim() } });
        say('');
      } catch (err) {
        preview = null;
        say(err.status === 409 ? '교안에서 코칭에 쓸 수 있는 내용을 찾지 못해 미리볼 수 없어요.' : err.message);
      } finally { busy = false; render(); }
    }

    async function save() {
      busy = true; refresh(); say('확정하는 중…');
      try {
        const result = await api(`/v1/materials/${materialId}/objective-profiles`, { method: 'POST', body: rules.payload(draft) });
        saved = result;
        draft = rules.fromProfile(saved);
        interpretation = null;
        say((result.warnings || []).map((w) => WARNING[w]).join(' '));
        return result;
      } catch (err) {
        const detail = err.detail && typeof err.detail === 'object' ? err.detail : null;
        say(detail ? `${KIND[detail.kind]} 중 적용할 수 없는 항목이 있어요. ${REASON[detail.reason] || ''}` : err.message);
        throw err;
      } finally { busy = false; render(); }
    }

    async function load(id) {
      const mine = ++token;
      materialId = id; saved = null; draft = rules.emptyDraft(); interpretation = null; preview = null; previewQuestion = '';
      say('불러오는 중…'); render();
      try {
        const res = await api(`/v1/materials/${id}/objective-profiles`);
        if (mine !== token) return;
        saved = res.latest; draft = rules.fromProfile(saved); say('');
      } catch (err) {
        if (mine !== token) return;
        say('학습목표를 불러오지 못했어요. 다음에 설정하고 진행할 수 있습니다.');
      }
      render();
    }

    return { load, save, state, discard: () => { draft = rules.fromProfile(saved); render(); } };
  }

  window.QU = Object.assign(window.QU || {}, { createObjectiveForm, objectiveRules: rules });
})();
