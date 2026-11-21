/* Question-up live protocol: response decoding and version rules shared by the
 * teacher and student live views (qu-live.js). Pure functions only: no DOM,
 * no network, no timers, so the rules can be tested on their own. */
(() => {
  'use strict';
  const LIVE_PAGE_TYPE = 'application/vnd.question-up.live-page';

  const sequenceOf = slide => slide?.sequence ?? 0;

  function isLivePage(contentType) {
    return Boolean(contentType?.startsWith(LIVE_PAGE_TYPE));
  }

  // Combined live response: 4-byte big-endian metadata length, UTF-8 JSON
  // metadata, then one raw PDF page (see classroom.live_snapshot).
  function decodeLiveFrame(buffer) {
    if (buffer.byteLength < 4) throw new Error('Incomplete live page response');
    const length = new DataView(buffer).getUint32(0);
    const end = 4 + length;
    if (end >= buffer.byteLength) throw new Error('Incomplete live page response');
    const data = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));
    return {...data, inlinePdf: buffer.slice(end)};
  }

  function pollQuery({version, stamp, indexVersion, inline}) {
    const params = new URLSearchParams({version: version || ''});
    if (stamp !== undefined) params.set('after', stamp);
    if (indexVersion) params.set('index_version', indexVersion);
    if (inline) {
      params.set('inline_page', 'true');
      params.set('partial_page', 'true');
    }
    return params.toString();
  }

  /* Decide what one poll response means for the view.
   *   stamp     revision to wait after next time (undefined after a partial response)
   *   delay     ms before the next poll (2 s when the server sent no revision)
   *   stale     the response is older than the slide on screen; ignore it
   *   confirmed newest slide acknowledged by the server
   *   changed   the shared slide version differs from the one on screen */
  function applyUpdate({slide, confirmed}, data) {
    const stamp = data.partial ? undefined : data.revision;
    const delay = !data.partial && stamp === undefined ? 2000 : 0;
    if (data.slide && sequenceOf(data.slide) < sequenceOf(slide)) {
      return {stamp, delay, stale: true, confirmed, changed: false};
    }
    const newest = data.slide && sequenceOf(data.slide) >= sequenceOf(confirmed) ? data.slide : confirmed;
    return {stamp, delay, stale: false, confirmed: newest, changed: slide?.version !== data.slide?.version};
  }

  // A publish result older than the newest confirmed slide must not move the view back.
  function acceptsAcknowledgement(confirmed, result) {
    return sequenceOf(result) >= sequenceOf(confirmed);
  }

  function neighborPage({visiblePages, navigation, current, delta, pageCount}) {
    if (visiblePages.length) return visiblePages[visiblePages.indexOf(current) + delta];
    if (navigation?.page === current && navigation.pdf) return delta < 0 ? navigation.before : navigation.after;
    const next = current + delta;
    return next >= 1 && next <= pageCount ? next : null;
  }

  window.QU = Object.assign(window.QU || {}, {
    liveProtocol: {LIVE_PAGE_TYPE, isLivePage, decodeLiveFrame, pollQuery, applyUpdate, acceptsAcknowledgement, neighborPage},
  });
})();
