"""LearningX shared-beta fail-closed home checks (no operator credentials)."""
import os
from pathlib import Path
import re
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from threading import Thread
import unittest
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*_): pass

class BetaGateE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(ROOT)))
        cls.thread=Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.http.server_port)
        cls.playwright=sync_playwright().start()
        cls.browser=cls.playwright.chromium.launch(
            headless=True,executable_path=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,
            args=['--no-sandbox'])
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()
        cls.http.shutdown();cls.http.server_close()
    def setUp(self):
        self.ctx=self.browser.new_context(viewport={'width':1440,'height':900},locale='ko-KR')
        self.page=self.ctx.new_page()
        self.js_errors=[]
        self.suspect_requests=[]
        self.page.on('pageerror',lambda e:self.js_errors.append(str(e)))
        self.page.on('request',lambda r:self.suspect_requests.append(r.url)
            if re.search(r'/v1/|/health|/api/|/invite',r.url) else None)
    def tearDown(self):self.ctx.close()

    def test_01_unconfigured_beta_does_not_accept_invite_or_lesson(self):
        self.page.goto(self.url+'/index.html',wait_until='domcontentloaded')
        expect(self.page.locator('#beta-server-message')).to_contain_text('서버 연결 준비 중')
        for key in ['beta-invite-code','beta-invite-submit','beta-rights',
                    'beta-retention-consent','beta-file','beta-join-submit']:
            expect(self.page.locator('#'+key)).to_be_disabled()
        self.assertEqual(self.suspect_requests,[])
        self.assertEqual(self.js_errors,[])
        self.assertEqual(self.page.evaluate('localStorage.length'),0)

    def test_02_student_code_normalization_and_no_silent_demo_redirect(self):
        self.page.goto(self.url+'/index.html')
        self.page.locator('#beta-class-code').fill('ab-c 234')
        expect(self.page.locator('#beta-class-code')).to_have_value('ABC234')
        self.page.locator('#beta-join-form').evaluate('(form)=>form.requestSubmit()')
        expect(self.page.locator('#beta-code-error')).to_contain_text('베타 서버 연결 전')
        self.assertTrue(self.page.url.endswith('/index.html'))
        self.assertEqual(self.suspect_requests,[])
        self.assertEqual(self.js_errors,[])

    def test_03_demo_is_explicit_link_and_no_auto_fallback(self):
        self.page.goto(self.url+'/index.html')
        link=self.page.get_by_role('link',name='데모 모드 체험')
        expect(link).to_have_attribute('href','app.html?demo=student')
        self.assertTrue(self.page.locator('#beta-server-message').is_visible())
        self.assertTrue(self.page.locator('.beta-mode-demo').is_visible())
        self.assertTrue(self.page.url.endswith('/index.html'))
        self.assertEqual(self.js_errors,[])

    def test_04_legacy_manual_api_only_developer_query(self):
        self.page.goto(self.url+'/app.html?demo=faculty',wait_until='domcontentloaded')
        expect(self.page.locator('#api-mode')).to_be_hidden()
        expect(self.page.locator('#pilot-runtime-badge')).to_have_text('데모')
        self.page.goto(self.url+'/app.html?dev=1',wait_until='domcontentloaded')
        expect(self.page.locator('#api-mode')).to_be_visible()
        self.page.locator('#api-mode').click()
        expect(self.page.locator('#pilot-runtime-badge')).to_contain_text('개발자')
        expect(self.page.locator('#api-url')).to_be_visible()
        self.assertEqual(self.js_errors,[])

    def test_05_mobile_and_desktop_have_no_horizontal_overflow(self):
        self.page.goto(self.url+'/index.html')
        for width in [1440,390]:
            self.page.set_viewport_size({'width':width,'height':844})
            metrics=self.page.evaluate('({viewport:innerWidth,html:document.documentElement.scrollWidth,body:document.body.scrollWidth})')
            self.assertLessEqual(metrics['html'],metrics['viewport'],str(metrics))
            self.assertLessEqual(metrics['body'],metrics['viewport'],str(metrics))
            if width==390:
                self.assertLess(
                    self.page.locator('#beta-class-code').bounding_box()['y']+
                    self.page.locator('#beta-class-code').bounding_box()['height'],844,
                    'student code entry must remain above the mobile fold')
        self.assertEqual(self.js_errors,[])

if __name__=='__main__':unittest.main(verbosity=2)
