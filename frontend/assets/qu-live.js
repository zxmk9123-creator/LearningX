(() => {
  'use strict';
  const {el, LEVEL, liveMark, liveTimingEnabled, liveProtocol} = window.QU;
  const pdfBase = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
  let pdfLibrary;
  let pdfWorker;
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
    return warmPdf().then(async lib => {
      pdfWorker ??= new lib.PDFWorker();
      const worker = pdfWorker;
      try {
        await worker.promise;
      } catch (error) {
        if (pdfWorker === worker) pdfWorker = null;
        worker.destroy();
        throw error;
      }
      const task = lib.getDocument({
        worker, data: bytes, isEvalSupported: false, useWasm: false, enableXfa: false,
        cMapUrl: pdfBase + 'cmaps/', cMapPacked: true, standardFontDataUrl: pdfBase + 'standard_fonts/'
      });
      try {
        return {lib, doc: await task.promise};
      } catch (error) {
        await task.destroy().catch(() => {});
        throw error;
      }
    });
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
    // Show the slide as soon as it is painted; the selectable text layer follows.
    const drawn = page.render({canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0]}).promise;
    frame.textReady = new lib.TextLayer({textContentSource: page.streamTextContent(), container: text, viewport})
      .render().catch(() => {});
    await drawn;
    return frame;
  }

  // One standalone page (for example a page a student opens on their own): an image only,
  // without a selectable text layer, drawn to fit the given box.
  window.QU.renderPdfImage = async (bytes, width, height, label) => {
    const pdf = await loadPdf(bytes);
    try {
      const page = await pdf.doc.getPage(1);
      const natural = page.getViewport({scale: 1});
      const viewport = page.getViewport({scale: Math.min(width / natural.width, height / natural.height)});
      const ratio = Math.min(devicePixelRatio || 1, 2);
      const canvas = el('canvas', {width: Math.ceil(viewport.width * ratio), height: Math.ceil(viewport.height * ratio),
        role: 'img', 'aria-label': label});
      canvas.style.width = viewport.width + 'px';
      canvas.style.height = viewport.height + 'px';
      await page.render({canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0]}).promise;
      return canvas;
    } finally {
      pdf.doc.destroy();
    }
  };

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
    let deckFailed = false;
    let slide = initial?.slide ?? null;
    let selected = null;
    let confirmed = slide;
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
    let refreshNow = false;
    let pollController = null;
    let pollParked = false;

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
    const fullscreen = () => (document.fullscreenElement || document.webkitFullscreenElement) === panel
      || panel.classList?.contains('live-expanded');
    const height = () => Math.max(1, Math.floor(fullscreen() ? (panel.clientHeight || innerHeight) : innerHeight * .75));

    const actions = panel.querySelector('[data-live-actions]');
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

    function hideSelectionActions(event) {
      if (!event || !actions?.contains(event.target)) actions?.classList.remove('selection-actions');
    }

    function showSelectionActions(hit) {
      if (!actions || !open || !picked.quote || !picked.rects.length ||
          !fullscreen()) return;
      const bounds = panel.getBoundingClientRect();
      const anchorX = Math.max(8, Math.min(hit.left - bounds.left, bounds.width - 228));
      const below = hit.bottom - bounds.top + 8;
      const anchorY = below + 136 < bounds.height ? below : Math.max(8, hit.top - bounds.top - 136);
      actions.style.left = anchorX + 'px';
      actions.style.top = anchorY + 'px';
      actions.classList.add('selection-actions');
    }

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
      if (feedback) {
        if (picked.quote) {
          feedback.replaceChildren(el('span', {'data-copy-protected': true, text: '선택: “' + picked.quote + '”'}));
        } else {
          feedback.replaceChildren();
        }
      }
      if (!drag) showSelectionActions(hit);
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
      const accepted = onAnchor?.({page: displayed, anchor: picked});
      if (accepted !== true) {
        feedback.textContent = typeof accepted === 'string' ? accepted : '지금 작성 중인 질문을 먼저 마무리해 주세요.';
        return;
      }
      feedback.textContent = displayed + '쪽에 질문을 붙여요.';
    }

    const updateSelection = () => pickText();
    if (!faculty) {
      document.addEventListener('selectionchange', updateSelection);
      panel.addEventListener('pointerdown', hideSelectionActions);
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
          item.anchor?.quote ? el('small', {'data-copy-protected': true, text: '“' + item.anchor.quote + '”'}) : null,
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
      const markState = JSON.stringify([displayed, slide?.version, current, picked, visible.map(item => [item.id, item.level, item.anchor])]);
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
      const layer = el('div', {class: 'live-marks'});
      const pins = [...(current?.spots ?? []).map(spot => ({...spot, level: 'confused'})),
        ...visible.map((item, number) => item.anchor ? {...item.anchor, level: item.level, count: number + 1} : null).filter(Boolean)];
      if (picked) pins.push({...picked, level: 'picked'});
      const highlights = new Map();
      for (const pin of pins) {
        for (const rect of pin.rects ?? []) {
          if (!highlights.has(pin.level)) {
            const group = el('div', {class: 'live-highlight-group ' + pin.level});
            highlights.set(pin.level, group);
            layer.append(group);
          }
          const highlight = el('span', {class: 'live-highlight ' + pin.level});
          highlight.style.left = (rect.left * 100) + '%';
          highlight.style.top = (rect.top * 100) + '%';
          highlight.style.width = ((rect.right - rect.left) * 100) + '%';
          highlight.style.height = ((rect.bottom - rect.top) * 100) + '%';
          highlights.get(pin.level).append(highlight);
        }
        const corner = pin.level === 'confused' && pin.rects?.length;
        const canAcknowledge = pin.tag_ids?.length && slide?.version;
        const marker = el('span', {class: 'live-mark ' + pin.level + (corner ? ' at-selection-corner' : ''),
          text: pin.count ?? '', 'aria-hidden': canAcknowledge ? undefined : 'true'});
        if (corner) {
          const first = pin.rects.reduce((top, rect) => rect.top < top.top ? rect : top);
          const lineMiddle = (first.top + first.bottom) / 2;
          const right = pin.rects.reduce((edge, rect) =>
            rect.top <= lineMiddle && rect.bottom >= lineMiddle ? Math.max(edge, rect.right) : edge, first.right);
          marker.style.left = (right * 100) + '%';
          marker.style.top = (first.top * 100) + '%';
        } else {
          marker.style.left = (pin.x * 100) + '%';
          marker.style.top = (pin.y * 100) + '%';
        }
        if (canAcknowledge) {
          const target = {page: displayed, version: slide.version, tag_ids: pin.tag_ids};
          marker.classList.add('acknowledgeable');
          marker.textContent = '';
          const toggle = el('button', {type: 'button', class: 'live-mark-toggle', text: pin.count,
            'aria-label': '헷갈려요 ' + pin.count + '건', 'aria-expanded': 'false'});
          const confirm = el('button', {type: 'button', class: 'live-mark-confirm', text: faculty ? '확인' : '×',
            'aria-label': faculty ? '이 위치의 헷갈려요 ' + pin.count + '건 확인' : '내 헷갈려요 태그 지우기'});
          if (faculty) confirm.append(el('span', {class: 'live-mark-spinner', 'aria-hidden': 'true'}));
          toggle.addEventListener('click', () => {
            marker.classList.remove('dismissed');
            const expanded = marker.classList.toggle('expanded');
            toggle.setAttribute('aria-expanded', String(expanded));
          });
          marker.addEventListener('pointerdown', event => event.stopPropagation());
          marker.addEventListener('click', event => event.stopPropagation());
          marker.addEventListener('keydown', event => {
            if (event.key !== 'Escape') return;
            marker.classList.remove('expanded');
            marker.classList.add('dismissed');
            toggle.setAttribute('aria-expanded', 'false');
            toggle.focus();
          });
          marker.addEventListener('pointerleave', () => marker.classList.remove('dismissed'));
          marker.addEventListener('focusout', event => {
            if (!marker.contains(event.relatedTarget)) marker.classList.remove('dismissed');
          });
          confirm.addEventListener('click', async () => {
            confirm.disabled = true;
            confirm.setAttribute('aria-busy', 'true');
            try {
              await api(path + '/confusion/acknowledge', {method: 'POST', body: target});
            } catch (error) {
              if (!stopped && slide?.version === target.version) {
                status.textContent = error.status === 409 ? '송출 쪽이 바뀌었어요.' : '확인 처리에 실패했어요. 다시 시도해 주세요.';
              }
            } finally {
              confirm.disabled = false;
              confirm.removeAttribute('aria-busy');
            }
          });
          marker.append(toggle, confirm);
        }
        layer.append(marker);
      }
      frame.append(layer);
    }

    function pageBytes(target, number) {
      const key = target.version + ":" + number;
      const cacheKey = faculty ? number : key;
      const retained = pageFiles.get(cacheKey);
      if (retained) {
        pageFiles.delete(cacheKey);
        pageFiles.set(cacheKey, retained);
        return Promise.resolve(retained);
      }
      if (downloads.has(key)) return downloads.get(key);
      const url = path + '/page/' + encodeURIComponent(target.version) + '?page=' + number;
      if (!faculty) liveMark?.('pdf-request', target.version, number);
      const pending = api(url, {
        timeout: 12000,
        readBody: response => {
          if (!faculty) liveMark?.('pdf-headers', target.version, number, undefined,
            liveTimingEnabled ? response.headers.get('Server-Timing') : undefined);
          return response.arrayBuffer();
        },
      }).then(bytes => {
        if (!faculty) liveMark?.('pdf-received', target.version, number);
        if (!stopped && (faculty || target.version === slide?.version) && bytes.byteLength <= pageCacheLimit) {
          while (retainedBytes + bytes.byteLength > pageCacheLimit || pageFiles.size >= 8) {
            const oldest = pageFiles.keys().next().value;
            retainedBytes -= pageFiles.get(oldest).byteLength;
            pageFiles.delete(oldest);
          }
          pageFiles.set(cacheKey, bytes);
          retainedBytes += bytes.byteLength;
        }
        return bytes;
      }).catch(err => {
        if (!faculty) liveMark?.(err.detail === 'timeout' ? 'pdf-timeout' : 'pdf-error', target.version, number);
        throw err;
      }).finally(() => downloads.delete(key));
      downloads.set(key, pending);
      return pending;
    }

    function pageFrame(target, number, w, h) {
      const rendered = 'rendered:' + (faculty ? '' : target.version + ':') + number + ':' + w + ':' + h;
      const ready = frames.get(rendered);
      if (ready) return cached(rendered, () => ready);
      const inFlight = 'page:' + target.version + ':' + number + ':' + w + ':' + h;
      return cached(inFlight, async () => {
        const bytes = await pageBytes(target, number);
        // PDF.js transfers the input to its worker. Keep the cached bytes intact.
        const pdf = await loadPdf(bytes.slice(0));
        let frame;
        try {
          frame = await renderPage(pdf, 1, w, h);
          if (!stopped && (faculty || target.version === slide?.version)) {
            frames.delete(inFlight);
            cached(rendered, () => Promise.resolve(frame));
          }
          return frame;
        } finally {
          Promise.resolve(frame?.textReady).finally(() => pdf.doc.destroy().catch(() => {}));
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
        // 'stale': the server moved on; the next poll brings the latest page, so do not retry this one.
        if (shown === true || shown === 'stale' || faculty || !target || target.version !== slide?.version) {
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
      try {
        if (deck) {
          frame = await cached('deck:' + target.page + ':' + w + ':' + h, () => renderPage(deck, target.page, w, h));
        } else if (target.pdf && (!faculty || !localFile || deckFailed)) {
          frame = await pageFrame(target, target.page, w, h);
        }
      } catch (err) {
        if (stopped || id !== renderId) return;
        if (!faculty && (err.status === 401 || err.status === 403)) {
          // Access was revoked: stop showing the lesson rather than retrying.
          stop();
          status.textContent = '교안 접근 권한이 없습니다.';
          return;
        }
        if (err.status === 409) {
          // The shared page changed or the class ended: poll the latest state without waiting.
          // A poll already parked on the old revision may not be woken (another API
          // instance, or a missed notification), so cancel it and ask again now.
          refreshNow = true;
          if (pollParked) pollController?.abort();
          return 'stale';
        }
        status.textContent = '교안 화면을 불러오지 못했습니다.';
        return;
      }
      if (!faculty && frame) liveMark?.('student-render-ready', target.version, target.page);
      while (drag && !stopped && id === renderId) await drag.done;
      if (stopped || id !== renderId) return;
      if (faculty && !frame) return;
      if (target.image) frame ??= el('img', {src: 'data:image/jpeg;base64,' + target.image, alt: target.page + '쪽 교안'});
      frame ??= el('p', {class: 'live-slide-text', text: target.text || '교안 화면을 준비하는 중입니다.'});
      if (displayed !== target.page) {
        picked = null;
        hideSelectionActions();
        if (feedback) feedback.textContent = '';
      }
      displayed = target.page;
      page.textContent = target.page + ' / ' + target.total;
      status.textContent = '동기화 중';
      content.replaceChildren(frame);
      if (!faculty) liveMark?.('student-display', target.version, target.page);
      marksKey = '';
      paintIndex();
      if (deck) {
        for (const number of [target.after ?? displayed + 1, target.before ?? displayed - 1]) {
          if (number > 0 && number <= deck.doc.numPages) {
            cached('deck:' + number + ':' + w + ':' + h, () => renderPage(deck, number, w, h)).catch(() => {});
          }
        }
      }
      return true;
    }

    async function readLiveUpdate(response) {
      if (!liveProtocol.isLivePage(response.headers.get('Content-Type'))) return response.json();
      return liveProtocol.decodeLiveFrame(await response.arrayBuffer());
    }

    let tryInlinePage = !faculty;

    async function tick() {
      let delay = 0;
      try {
        const fresh = refreshNow;
        refreshNow = false;
        const query = liveProtocol.pollQuery({version: slide?.version, stamp, indexVersion, inline: tryInlinePage, fresh});
        let serverTiming;
        const controller = new AbortController();
        pollController = controller;
        pollParked = new URLSearchParams(query).has('after');
        const data = await api(path + '?' + query, {
          timeout: 6000,
          signal: controller.signal,
          readBody: faculty ? undefined : readLiveUpdate,
          onResponse: liveTimingEnabled ? response => { serverTiming = response.headers.get('Server-Timing'); } : undefined,
        });
        if (stopped) return;
        tryInlinePage = !faculty;
        const update = liveProtocol.applyUpdate({slide, confirmed}, data);
        stamp = update.stamp;
        delay = update.delay;
        if (update.stale) return;
        confirmed = update.confirmed;
        const changed = update.changed;
        if (changed) {
          if (!faculty) {
            frames.clear();
            pageFiles.clear();
            retainedBytes = 0;
          }
          slide = data.slide;
          if (!faculty && slide) {
            liveMark?.('student-detected', slide.version, slide.page, undefined, serverTiming);
            if (data.inlinePdf) {
              pageFiles.set(slide.version + ':' + slide.page, data.inlinePdf);
              retainedBytes = data.inlinePdf.byteLength;
              liveMark?.('pdf-inline-received', slide.version, slide.page);
            }
          }
          if (slide?.visible_pages) visiblePages = slide.visible_pages;
          if (selected === null || slide?.page === selected) navigation = slide;
        }
        if (!data.partial) indexVersion = data.index_version;
        if (data.index) items = data.index;
        open = data.status === 'OPEN';
        if (!open) hideSelectionActions();
        if (!data.partial || changed) confusion = data.confusion;
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
        if (err.detail === 'cancelled') return;  // Replaced by an immediate fresh poll (finally).
        if (!faculty && (err.status === 401 || err.status === 403)) {
          stop();
          status.textContent = '교안 접근 권한이 없습니다.';
          return;
        }
        tryInlinePage = false;  // Retry with metadata only if the combined response stalled.
        status.textContent = '연결을 다시 확인하는 중입니다.';
        delay = err.detail === 'timeout' ? 0 : 1200;  // A stalled poll retries at once.
      } finally {
        pollController = null;
        pollParked = false;
        if (!stopped) timer = setTimeout(tick, refreshNow ? 0 : delay);
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
      if (!faculty) {
        displayed = null;
        content.replaceChildren();
        index?.replaceChildren();
        page.textContent = '송출 종료';
      }
      clearTimeout(timer);
      clearTimeout(retryTimer);
      pollController?.abort();
      hideSelectionActions();
      panel.removeEventListener('pointerdown', hideSelectionActions);
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
      if (!deck && !slide?.pdf) return;
      selected = number;
      const position = visiblePages.indexOf(number);
      // Without the whole PDF yet, the page comes from the per-page route (current page and its neighbours).
      display({page: number, total: deck ? deck.doc.numPages : slide.total, pdf: !deck, version: slide?.version,
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
      // The whole lesson PDF lets the presenter flip pages locally. A large file on a slow
      // link can take a while, and a failed download must not stop the broadcast: retry, and
      // meanwhile show and move between pages through the per-page route.
      const remote = () => api(path + '/document', {raw: true, timeout: 180000}).then(res => res.arrayBuffer()).then(loadPdf);
      const loadDeck = attempt => (attempt === 0 && localFile ? localFile.arrayBuffer().then(loadPdf).catch(remote) : remote())
        .then(pdf => {
          if (stopped) return pdf.doc.destroy();
          deck = pdf;
          deckFailed = false;
          if (slide && displayed !== slide.page) display(slide);
          onState({slide, total: deck.doc.numPages});
        })
        .catch(() => {
          if (stopped) return;
          if (!deckFailed) {
            deckFailed = true;
            if (slide && displayed !== slide.page) display(slide);
          }
          if (attempt < 3) setTimeout(() => { if (!stopped && !deck) loadDeck(attempt + 1); }, [3000, 10000, 30000][attempt]);
          else if (!displayed) status.textContent = '원본 교안 화면을 불러오지 못했습니다. 수업에 다시 접속해 주세요.';
        });
      loadDeck(0);
    }
    return {
      stop, select, resize, page: () => displayed,
      confirmedVersion: () => confirmed?.version,
      acknowledge: result => {
        if (stopped || !liveProtocol.acceptsAcknowledgement(confirmed, result)) return;
        if (result.version) confirmed = result;
        if (selected === null || selected === result.page) navigation = result;
      },
      neighbor: delta => {
        if (!deck && !slide) return null;
        const current = selected ?? slide?.page ?? displayed;
        if (current === null) return null;
        return liveProtocol.neighborPage({visiblePages, navigation, current, delta, pageCount: deck ? deck.doc.numPages : slide.total});
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
