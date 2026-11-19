(() => {
  'use strict';
  const {el, LEVEL} = window.QU;
  const pdfBase = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
  let pdfLibrary;
  function warmPdf() {
    pdfLibrary ??= import(pdfBase + 'build/pdf.min.mjs').then(lib => {
      lib.GlobalWorkerOptions.workerSrc = pdfBase + 'build/pdf.worker.min.mjs';
      return lib;
    }).catch(err => {
      pdfLibrary = null;
      throw err;
    });
    return pdfLibrary;
  }

  function loadPdf(bytes) {
    return warmPdf().then(async lib => ({
      lib,
      doc: await lib.getDocument({
        data: bytes, isEvalSupported: false, useWasm: false, enableXfa: false,
        cMapUrl: pdfBase + 'cmaps/', cMapPacked: true, standardFontDataUrl: pdfBase + 'standard_fonts/'
      }).promise,
    }));
  }

  async function renderPage({lib, doc}, number, width, height) {
    const page = await doc.getPage(number);
    const natural = page.getViewport({scale: 1});
    const scale = Math.min(width / natural.width, height / natural.height);
    const viewport = page.getViewport({scale});
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const canvas = el('canvas', {width: Math.ceil(viewport.width * ratio), height: Math.ceil(viewport.height * ratio),
      role: 'img', 'aria-label': number + '쪽 교안'});
    const frame = el('div', {class: 'live-pdf'}, canvas);
    frame.style.width = viewport.width + 'px';
    frame.style.setProperty('--total-scale-factor', String(viewport.scale * (viewport.userUnit || 1)));
    const text = el('div', {class: 'live-pdf-text textLayer'});
    frame.append(text);
    await Promise.all([
      page.render({canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0]}).promise,
      new lib.TextLayer({textContentSource: page.streamTextContent(), container: text, viewport}).render(),
    ]);
    return frame;
  }

  // Start loading the renderer during entry/review so opening a slide can overlap
  // authentication and network I/O with module loading.
  warmPdf().catch(() => {});

  window.QU.createLiveView = ({api, session, panel, faculty = false, initial, localFile, onState, onQuestion, onAnchor}) => {
    const page = panel.querySelector('[data-live-page]');
    const content = panel.querySelector('[data-live-content]');
    const index = panel.querySelector('[data-live-index]');
    const status = panel.querySelector('[data-live-status]');
    const path = '/v1/sessions/' + encodeURIComponent(session) + '/live';
    const frames = new Map();
    const pageFiles = new Map();
    const downloads = new Map();
    const pageCacheLimit = 24 * 1024 * 1024;
    let retainedBytes = 0;
    let deck = null;
    let slide = initial?.slide ?? null;
    let selected = null;
    let navigation = null;
    let visiblePages = slide?.visible_pages ?? [];
    let displayed = null;
    let stopped = false;
    let timer;
    let indexKey = '';
    let stamp;
    let indexVersion;
    let items = initial?.index ?? [];
    let renderId = 0;
    let activeRender;
    let retryTimer;
    let retryCount = 0;

    function cached(key, make) {
      if (frames.has(key)) {
        const existing = frames.get(key);
        frames.delete(key);
        frames.set(key, existing);
        return existing;
      }
      const frame = make().catch(err => {
        if (frames.get(key) === frame) frames.delete(key);
        throw err;
      });
      frames.set(key, frame);
      if (frames.size > 8) frames.delete(frames.keys().next().value);
      return frame;
    }

    const width = () => Math.max(1, Math.floor(content.clientWidth || panel.clientWidth || 320));
    const height = () => Math.floor(document.fullscreenElement === panel || panel.classList?.contains('live-expanded') ? innerHeight : innerHeight * .75);

    const feedback = panel.querySelector('[data-live-feedback]');
    const confusedButton = panel.querySelector('[data-live-confused]');
    const askButton = panel.querySelector('[data-live-ask]');
    const confusionSummary = panel.querySelector('[data-live-confusion]');
    const strip = panel.querySelector('[data-live-strip]');
    let confusion;
    let picked;
    let marksKey = '';
    let open = initial?.status === 'OPEN';
    let sendingConfusion = false;
    let drag;

    function beginDrag(event) {
      if (drag || event.pointerType !== 'mouse' || event.button !== 0 ||
          !event.target.closest('.live-pdf-text')) return;
      const x = window.scrollX;
      const y = window.scrollY;
      let release;
      const done = new Promise(resolve => { release = resolve; });
      const restore = () => {
        if (window.scrollX !== x || window.scrollY !== y) window.scrollTo(x, y);
      };
      drag = {pointer: event.pointerId, done, release, restore};
      document.documentElement.classList.add('live-selecting');
      window.addEventListener('scroll', restore);
    }

    function finishDrag(event) {
      if (!drag || (event?.pointerId != null && drag.pointer !== event.pointerId)) return;
      const current = drag;
      drag = null;
      window.removeEventListener('scroll', current.restore);
      document.documentElement.classList.remove('live-selecting');
      current.restore();
      if (!stopped) pickText();
      current.release();
    }

    function pickText() {
      const selection = window.getSelection();
      const frame = content.querySelector('.live-pdf');
      if (!selection || selection.isCollapsed || !selection.rangeCount || !frame) return;
      const textLayer = frame.querySelector('.live-pdf-text');
      if (!textLayer || !textLayer.contains(selection.anchorNode)) return;
      const range = selection.getRangeAt(0).cloneRange();
      if (!textLayer.contains(range.startContainer)) range.setStart(textLayer, 0);
      if (!textLayer.contains(range.endContainer)) range.setEnd(textLayer, textLayer.childNodes.length);
      const area = frame.getBoundingClientRect();
      const hit = range.getBoundingClientRect();
      if (!area.width || !area.height) return;
      const clamp = value => Math.max(0, Math.min(1, value));
      const rects = [...range.getClientRects()].map(rect => ({
        left: clamp((rect.left - area.left) / area.width), top: clamp((rect.top - area.top) / area.height),
        right: clamp((rect.right - area.left) / area.width), bottom: clamp((rect.bottom - area.top) / area.height),
      })).filter(rect => rect.right > rect.left && rect.bottom > rect.top);
      // Keep the entire range even on slides with densely fragmented text.
      if (rects.length > 2048) {
        const step = Math.ceil(rects.length / 2048);
        const compact = [];
        for (let i = 0; i < rects.length; i += step) {
          const group = rects.slice(i, i + step);
          compact.push({left: Math.min(...group.map(rect => rect.left)),
            top: Math.min(...group.map(rect => rect.top)),
            right: Math.max(...group.map(rect => rect.right)),
            bottom: Math.max(...group.map(rect => rect.bottom))});
        }
        rects.splice(0, rects.length, ...compact);
      }
      picked = {rects, x: clamp((hit.left + hit.width / 2 - area.left) / area.width),
        y: clamp((hit.top + hit.height / 2 - area.top) / area.height),
        quote: range.toString().trim().slice(0, 240)};
      if (feedback) feedback.textContent = picked.quote ? '선택: “' + picked.quote + '”' : '';
      paintIndex();
    }

    function pickPoint(event) {
      if (window.getSelection()?.toString().trim()) return;
      const frame = event.target.closest('.live-pdf');
      if (!frame) return;
      const area = frame.getBoundingClientRect();
      picked = {x: (event.clientX - area.left) / area.width, y: (event.clientY - area.top) / area.height};
      if (picked.x < 0 || picked.x > 1 || picked.y < 0 || picked.y > 1) {
        picked = null;
        return;
      }
      feedback.textContent = '교안에서 위치를 선택했어요.';
      paintIndex();
    }

    function sendConfusion() {
      pickText();
      const target = displayed;
      if (!target || !open || sendingConfusion) return;
      sendingConfusion = true;
      confusedButton.disabled = true;
      api(path + '/confusion', {method: 'PUT', body: {page: target, anchor: picked}}).then(() => {
        if (displayed === target) feedback.textContent = '헷갈리는 부분을 전달했어요.';
      }).catch(err => {
        if (displayed === target) feedback.textContent = err.status === 409 ? '페이지가 바뀌었어요. 다시 선택해 주세요.' : err.message;
      }).finally(() => { sendingConfusion = false; confusedButton.disabled = !open || !displayed; });
    }

    function askHere() {
      pickText();
      if (!displayed || !open) return;
      if (!onAnchor?.({page: displayed, anchor: picked})) {
        feedback.textContent = '지금 작성 중인 질문을 먼저 마무리해 주세요.';
        return;
      }
      feedback.textContent = displayed + '쪽에 질문을 붙여요.';
    }

    const updateSelection = () => { if (!drag) pickText(); };
    if (!faculty) {
      document.addEventListener('selectionchange', updateSelection);
      content.addEventListener('click', pickPoint);
      content.addEventListener('pointerdown', beginDrag);
      window.addEventListener('pointerup', finishDrag);
      window.addEventListener('pointercancel', finishDrag);
      window.addEventListener('blur', finishDrag);
      confusedButton?.addEventListener('click', sendConfusion);
      askButton?.addEventListener('click', askHere);
    }

    function paintIndex() {
      const visible = items.filter(item => item.page === displayed);
      const key = JSON.stringify([displayed, visible, strip ? items.map(({id, level, page}) => [id, level, page]) : null]);
      if (key !== indexKey) {
        indexKey = key;
        index.replaceChildren(...visible.map((item, number) => el('button', {
          type: 'button', class: 'live-tab ' + item.level,
          'aria-label': LEVEL[item.level] + ' 질문: ' + (item.text || ''),
          onclick: () => onQuestion(item.id)
        }, el('span', {class: 'live-tab-label', text: (number + 1) + ' · ' + LEVEL[item.level]}),
        el('span', {class: 'live-tab-number', text: number + 1, 'aria-hidden': 'true'}),
        el('span', {class: 'live-tab-preview'},
          item.anchor?.quote ? el('small', {text: '“' + item.anchor.quote + '”'}) : null,
          el('span', {text: item.text || LEVEL[item.level] + ' 질문'})))));
        if (strip) {
          const groups = new Map();
          for (const item of items) {
            if (!groups.has(item.page)) groups.set(item.page, []);
            groups.get(item.page).push(item);
          }
          strip.replaceChildren(...Array.from(groups).sort(([a], [b]) => a - b).map(([number, questions]) =>
            el('div', {class: 'live-strip-page'}, el('span', {text: number + '쪽'}),
              ...questions.map(item => el('button', {type: 'button', class: 'live-tab ' + item.level,
                'aria-label': number + '쪽 ' + LEVEL[item.level] + ' 질문', onclick: () => onQuestion(item.id)})))));
        }
      }
      const current = displayed === slide?.page ? confusion : null;
      const markState = JSON.stringify([displayed, current, picked, visible.map(item => [item.id, item.level, item.anchor])]);
      const frame = content.querySelector('.live-pdf');
      if (confusionSummary) confusionSummary.textContent = displayed ? '헷갈려요 · ' + (current?.count ?? 0) + '명' : '';
      if (confusedButton) {
        confusedButton.textContent = current?.mine ? '여기가 헷갈려요 ✓' : '여기가 헷갈려요';
        confusedButton.disabled = !displayed || !open || sendingConfusion;
        askButton.disabled = !displayed || !open;
      }
      if (!frame || markState === marksKey) return;
      marksKey = markState;
      frame.querySelector('.live-marks')?.remove();
      const layer = el('div', {class: 'live-marks', 'aria-hidden': 'true'});
      const pins = [...(current?.spots ?? []).map(spot => ({...spot, level: 'confused'})),
        ...visible.map((item, number) => item.anchor ? {...item.anchor, level: item.level, count: number + 1} : null).filter(Boolean)];
      if (picked) pins.push({...picked, level: 'picked'});
      for (const pin of pins) {
        for (const rect of pin.rects ?? []) {
          const highlight = el('span', {class: 'live-highlight ' + pin.level});
          highlight.style.left = (rect.left * 100) + '%';
          highlight.style.top = (rect.top * 100) + '%';
          highlight.style.width = ((rect.right - rect.left) * 100) + '%';
          highlight.style.height = ((rect.bottom - rect.top) * 100) + '%';
          layer.append(highlight);
        }
        const corner = pin.level === 'confused' && pin.rects?.length;
        const marker = el('span', {class: 'live-mark ' + pin.level + (corner ? ' at-selection-corner' : ''), text: pin.count ?? ''});
        if (corner) {
          marker.style.left = (Math.max(...pin.rects.map(rect => rect.right)) * 100) + '%';
          marker.style.top = (Math.min(...pin.rects.map(rect => rect.top)) * 100) + '%';
        } else {
          marker.style.left = (pin.x * 100) + '%';
          marker.style.top = (pin.y * 100) + '%';
        }
        layer.append(marker);
      }
      frame.append(layer);
    }

    function pageBytes(target, number) {
      const retained = pageFiles.get(number);
      if (retained) {
        pageFiles.delete(number);
        pageFiles.set(number, retained);
        return Promise.resolve(retained);
      }
      const key = target.version + ":" + number;
      if (downloads.has(key)) return downloads.get(key);
      const url = path + '/page/' + encodeURIComponent(target.version) + '?page=' + number;
      const pending = api(url, {raw: true, timeout: 12000}).then(res => res.arrayBuffer()).then(bytes => {
        if (!stopped && bytes.byteLength <= pageCacheLimit) {
          while (retainedBytes + bytes.byteLength > pageCacheLimit || pageFiles.size >= 8) {
            const oldest = pageFiles.keys().next().value;
            retainedBytes -= pageFiles.get(oldest).byteLength;
            pageFiles.delete(oldest);
          }
          pageFiles.set(number, bytes);
          retainedBytes += bytes.byteLength;
        }
        return bytes;
      }).finally(() => downloads.delete(key));
      downloads.set(key, pending);
      return pending;
    }

    function pageFrame(target, number, w, h, onBytes) {
      const rendered = 'rendered:' + number + ':' + w + ':' + h;
      const ready = frames.get(rendered);
      if (ready) return cached(rendered, () => ready);
      const inFlight = 'page:' + target.version + ':' + number + ':' + w + ':' + h;
      return cached(inFlight, async () => {
        const bytes = await pageBytes(target, number);
        onBytes?.();
        // PDF.js transfers the input to its worker. Keep the cached bytes intact.
        const pdf = await loadPdf(bytes.slice(0));
        try {
          const frame = await renderPage(pdf, 1, w, h);
          if (!stopped) {
            frames.delete(inFlight);
            cached(rendered, () => Promise.resolve(frame));
          }
          return frame;
        } finally {
          pdf.doc.destroy().catch(() => {});
        }
      });
    }

    function follow(target) {
      clearTimeout(retryTimer);
      const render = display(target);
      activeRender = render;
      render.then(shown => {
        if (activeRender !== render || stopped) return;
        activeRender = null;
        if (shown || faculty || !target || target.version !== slide?.version) {
          retryCount = 0;
          return;
        }
        retryTimer = setTimeout(() => {
          if (!stopped && target.version === slide?.version) follow(target);
        }, Math.min(350 * 2 ** retryCount++, 2000));
      });
    }

    async function display(target) {
      const id = ++renderId;
      if (!target) {
        displayed = null;
        page.textContent = '송출 대기';
        content.replaceChildren();
        paintIndex();
        return;
      }
      const w = width();
      const h = height();
      let frame;
      const preloadNext = () => {
        if (!faculty && !stopped && id === renderId && target.version === slide?.version && target.after) {
          pageBytes(target, target.after).catch(() => {});
        }
      };
      try {
        if (deck) {
          frame = await cached('deck:' + target.page + ':' + w + ':' + h, () => renderPage(deck, target.page, w, h));
        } else if (target.pdf && (!faculty || !localFile)) {
          frame = await pageFrame(target, target.page, w, h, preloadNext);
        }
      } catch (err) {
        if (stopped || id !== renderId || err.status === 409) return;
        status.textContent = '교안 화면을 불러오지 못했습니다.';
        return;
      }
      while (drag && !stopped && id === renderId) await drag.done;
      if (stopped || id !== renderId) return;
      if (faculty && !frame) return;
      if (target.image) frame ??= el('img', {src: 'data:image/jpeg;base64,' + target.image, alt: target.page + '쪽 교안'});
      frame ??= el('p', {class: 'live-slide-text', text: target.text || '교안 화면을 준비하는 중입니다.'});
      if (displayed !== target.page) {
        picked = null;
        if (feedback) feedback.textContent = '';
      }
      displayed = target.page;
      page.textContent = target.page + ' / ' + target.total;
      status.textContent = '동기화 중';
      content.replaceChildren(frame);
      marksKey = '';
      paintIndex();
      if (deck) {
        for (const number of [target.after ?? displayed + 1, target.before ?? displayed - 1]) {
          if (number > 0 && number <= deck.doc.numPages) {
            cached('deck:' + number + ':' + w + ':' + h, () => renderPage(deck, number, w, h)).catch(() => {});
          }
        }
      } else if (!faculty && target.pdf && target.version === slide?.version) {
        for (const number of [target.after, target.before]) {
          if (number) pageFrame(target, number, w, h).catch(() => {});
        }
      }
      return true;
    }

    async function tick() {
      let delay = 0;
      try {
        const params = new URLSearchParams({version: slide?.version || ''});
        if (stamp !== undefined) params.set('after', stamp);
        if (indexVersion) params.set('index_version', indexVersion);
        const data = await api(path + '?' + params, {timeout: 10000});
        if (stopped) return;
        stamp = data.revision;
        if (stamp === undefined) delay = 2000;
        const changed = slide?.version !== data.slide?.version;
        if (changed) {
          slide = data.slide;
          if (slide?.visible_pages) visiblePages = slide.visible_pages;
          if (selected === null || slide?.page === selected) navigation = slide;
        }
        indexVersion = data.index_version;
        if (data.index) items = data.index;
        open = data.status === 'OPEN';
        confusion = data.confusion;
        if (changed && (selected === null || data.status === 'CLOSED')) {
          retryCount = 0;
          follow(slide);
        }
        if (selected === slide?.page) selected = null;
        paintIndex();
        if (!displayed && status.textContent === '') status.textContent = '교안 화면을 준비하는 중입니다.';
        onState(data);
        if (data.status === 'CLOSED') stop();
      } catch (err) {
        if (stopped) return;
        status.textContent = '연결을 다시 확인하는 중입니다.';
        delay = 1200;
      } finally {
        if (!stopped) timer = setTimeout(tick, delay);
      }
    }

    function resize() {
      if (stopped || !slide) return;
      if (deck && selected) display({page: selected, total: deck.doc.numPages});
      else if (faculty) display(slide);
      else follow(slide);
    }

    if (faculty) {
      window.addEventListener('resize', resize);
      document.addEventListener('fullscreenchange', resize);
    }

    function stop() {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(retryTimer);
      document.removeEventListener('selectionchange', updateSelection);
      content.removeEventListener('click', pickPoint);
      content.removeEventListener('pointerdown', beginDrag);
      window.removeEventListener('pointerup', finishDrag);
      window.removeEventListener('pointercancel', finishDrag);
      window.removeEventListener('blur', finishDrag);
      finishDrag();
      confusedButton?.removeEventListener('click', sendConfusion);
      askButton?.removeEventListener('click', askHere);
      window.removeEventListener('resize', resize);
      document.removeEventListener('fullscreenchange', resize);
      ++renderId;
      deck?.doc.destroy();
      frames.clear();
      pageFiles.clear();
      downloads.clear();
      retainedBytes = 0;
    }

    function select(number) {
      if (!deck) return;
      selected = number;
      const position = visiblePages.indexOf(number);
      display({page: number, total: deck.doc.numPages,
        before: visiblePages[position - 1], after: visiblePages[position + 1]});
    }

    panel.hidden = false;
    page.textContent = '송출 대기';
    content.replaceChildren();
    index.replaceChildren();
    status.textContent = '교안 화면을 준비하는 중입니다.';
    if (slide && (!faculty || !localFile)) follow(slide);
    tick();
    if (faculty) {
      const remote = () => api(path + '/document', {raw: true}).then(res => res.arrayBuffer()).then(loadPdf);
      const loading = localFile ? localFile.arrayBuffer().then(loadPdf).catch(remote) : remote();
      loading.then(pdf => {
        if (stopped) return pdf.doc.destroy();
        deck = pdf;
        if (slide && displayed !== slide.page) display(slide);
        onState({slide, total: deck.doc.numPages});
      }).catch(() => { if (!stopped) status.textContent = '원본 교안 화면을 불러오지 못했습니다. 수업에 다시 접속해 주세요.'; });
    }
    return {
      stop, select, resize, page: () => displayed,
      acknowledge: result => { if (!stopped && (selected === null || selected === result.page)) navigation = result; },
      neighbor: delta => {
        if (!deck) return null;
        const current = selected ?? slide?.page ?? displayed;
        if (current === null) return null;
        if (visiblePages.length) return visiblePages[visiblePages.indexOf(current) + delta];
        if (navigation?.page === current && navigation.pdf) return delta < 0 ? navigation.before : navigation.after;
        const next = current + delta;
        return next >= 1 && next <= deck.doc.numPages ? next : null;
      },
      target: () => selected ?? slide?.page ?? displayed,
      total: () => deck?.doc.numPages ?? slide?.total,
      reconcile: () => {
        if (!stopped) {
          selected = null;
          display(slide || (deck && {page: 1, total: deck.doc.numPages}));
        }
      },
    };
  };
})();
