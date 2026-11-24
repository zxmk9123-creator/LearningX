/* Question-up Google sign-in (Neon Auth, shared credentials) for the teacher and student pages.
 * The Neon client loads only when used. The server admits Google-verified school emails, asks for
 * student/faculty once on the first sign-in, and classifies later sign-ins from the database.
 * Tokens stay in memory (no browser storage). */
(() => {
  'use strict';
  const NEON_AUTH_URL = 'https://ep-late-darkness-biqqwodu.neonauth.c-5.ap-southeast-1.aws.neon.tech/neondb/auth';
  const MESSAGES = {
    domain_not_allowed: (d) => `학교 Google 계정(${(d.domains || []).map((x) => '@' + x).join(', ')})으로 로그인해 주세요.`,
    email_unverified: () => 'Google에서 이메일 확인이 끝난 계정만 사용할 수 있어요.',
    account_already_linked: () => '이 이메일의 계정은 이미 다른 Google 로그인과 연결돼 있어요. 운영진에게 문의해 주세요.',
    account_inactive: () => '사용할 수 없는 계정이에요. 운영진에게 문의해 주세요.',
  };

  function createGoogleLogin({ api }) {
    let client = null;
    let neonToken = null;
    let email = '';

    function load() {
      if (client) return Promise.resolve(client);
      return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'assets/vendor/neon-auth.js';
        script.onload = () => { client = window.QUNeonAuth.create(NEON_AUTH_URL); resolve(client); };
        script.onerror = () => reject(new Error('Google 로그인을 불러오지 못했어요.'));
        document.head.append(script);
      });
    }

    // Back from Google (Neon adds a verifier), or sent here by the other page after classification.
    const returning = () => {
      const q = new URLSearchParams(location.search);
      return q.has('neon_auth_session_verifier') || q.get('google') === '1';
    };

    async function session() {
      const { data } = await (await load()).getSession();
      neonToken = data && data.session && data.session.token;
      email = (data && data.user && data.user.email) || '';
      return neonToken ? { email } : null;
    }

    // The role chosen on the previous page, carried in the redirect (masters choose on every sign-in).
    function roleHint() {
      const as = returning() ? new URLSearchParams(location.search).get('as') : null;
      return as === 'student' || as === 'faculty' ? as : null;
    }

    // Returns {role, access_token | join_ticket} or {needsRole: true, email, master}; throws Error with a message.
    async function exchange(role) {
      if (!neonToken && !(await session())) return null;
      const chosen = role || roleHint();
      try {
        return await api('/v1/auth/neon/session', { method: 'POST', body: { token: neonToken, ...(chosen ? { role: chosen } : {}) } });
      } catch (err) {
        const detail = err.detail && typeof err.detail === 'object' ? err.detail : {};
        if (detail.code === 'role_required') return { needsRole: true, email: detail.email || email, master: !!detail.master };
        if (MESSAGES[detail.code]) {
          // Let the person pick another Google account next time.
          if (detail.code === 'domain_not_allowed' || detail.code === 'email_unverified') await signOut();
          throw new Error(MESSAGES[detail.code](detail));
        }
        if (err.status === 401) { neonToken = null; throw new Error('Google 로그인이 만료됐어요. 다시 로그인해 주세요.'); }
        throw err;
      }
    }

    async function start() {
      await (await load()).signIn.social({ provider: 'google', callbackURL: location.origin + location.pathname + location.search });
    }

    async function signOut() {
      neonToken = null;
      try { await (await load()).signOut(); } catch (_) { /* ignore */ }
    }

    // Move to the page for a role, keeping a local ?api= override; the Neon session carries over.
    function goTo(page, role) {
      const q = new URLSearchParams(location.search);
      const next = new URLSearchParams({ google: '1', ...(role ? { as: role } : {}) });
      if (q.get('api')) next.set('api', q.get('api'));
      location.href = `${page}?${next}`;
    }

    function clearReturnParams() {
      const url = new URL(location.href);
      ['google', 'as', 'neon_auth_session_verifier'].forEach((k) => url.searchParams.delete(k));
      history.replaceState(history.state, '', url.href);
    }

    // What the role step says about the account.
    const roleIntro = (result) => (result.master
      ? `${result.email} · 마스터 계정이라 로그인할 때마다 역할을 골라요.`
      : `${result.email} 계정으로 처음 로그인했어요.`);

    return { returning, session, exchange, start, signOut, goTo, clearReturnParams, roleIntro, email: () => email };
  }

  window.QU = Object.assign(window.QU || {}, { createGoogleLogin });
})();
