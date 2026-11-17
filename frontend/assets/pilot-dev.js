/* Developer-only manual backend form. No credentials or URL in public builds. */
(() => {
  'use strict';
  const developerMode = new URLSearchParams(location.search).get('dev') === '1';
  const apiButton = document.getElementById('api-mode');
  const demoButton = document.getElementById('demo-mode');
  const badge = document.getElementById('pilot-runtime-badge');
  if (!apiButton || !demoButton || !badge) return;
  apiButton.hidden = !developerMode;
  // A demo link or server outage must never silently switch to an actual session.
  const sync = () => {
    const demo = demoButton.getAttribute('aria-pressed') === 'true';
    badge.textContent = demo ? '데모' : '실제 베타 · 개발자 연결';
    badge.setAttribute('aria-label', demo ? '데모 모드' : '백엔드 개발자 연결 모드');
  };
  const observer = new MutationObserver(sync);
  observer.observe(demoButton, {attributes:true, attributeFilter:['aria-pressed']});
  sync();
})();
