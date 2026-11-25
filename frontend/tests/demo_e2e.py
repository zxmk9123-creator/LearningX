"""Playwright: faculty and student as separate tabs in one browser context."""
import hashlib
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
PDF=b"%PDF-1.4\n% Fictional LearningX file, not parsed\n%%EOF"
RESTRICTED=[
  '잠시 휴식을 취하고 10분 뒤 수업을 재개합니다.',
  '다음 시간에는 프레이밍 이론의 사례를 살펴봅니다.',
  '복습문제: 오늘 배운 핵심 내용을 세 가지로 요약해 보세요.',
  '제공 답안: 반복 보도 빈도는 수용자의 중요도 판단에 항상 비례한다.',
  '교수자 노트: 특정 사례에 관한 비공개 강의 진행 메모를 소개합니다.',
  '미검증 도표: 가상의 응답률 91%가 모든 집단에서 동일하게 나타났습니다.',
  '내레이션 오디오: 녹음 설명과 발언 내용은 분석되지 않았습니다.',
]
class Quiet(SimpleHTTPRequestHandler):
  def log_message(self,*_):pass

class DemoE2E(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    handler=partial(Quiet,directory=str(ROOT))
    cls.http=ThreadingHTTPServer(('127.0.0.1',0),handler)
    cls.thread=Thread(target=cls.http.serve_forever,daemon=True)
    cls.thread.start()
    cls.url='http://127.0.0.1:'+str(cls.http.server_port)+'/app.html'
    cls.playwright=sync_playwright().start()
    executable=os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE')
    cls.browser=cls.playwright.chromium.launch(headless=True,
      executable_path=executable or None,args=['--no-sandbox'])
  @classmethod
  def tearDownClass(cls):
    cls.browser.close();cls.playwright.stop()
    cls.http.shutdown();cls.http.server_close()
  def setUp(self):
    self.context=self.browser.new_context(viewport={'width':1440,'height':900},locale='ko-KR')
    self.faculty=self.context.new_page()
    self.student=self.context.new_page()
    self.errors=[]
    for name,page in [('faculty',self.faculty),('student',self.student)]:
      page.on('pageerror',lambda err,n=name:self.errors.append((n,str(err))))
      page.on('console',lambda msg,n=name:self.errors.append((n,msg.text)) if msg.type=='error' else None)
    self.faculty.on('dialog',lambda dialog:dialog.accept())
    self.faculty.goto(self.url+'?demo=faculty',wait_until='domcontentloaded')
    self.student.goto(self.url+'?demo=student',wait_until='domcontentloaded')
    expect(self.faculty.locator('#demo-workspace')).to_be_visible()
    expect(self.student.locator('#demo-workspace')).to_be_visible()
    expect(self.faculty.locator('#demo-faculty-panel')).to_be_visible()
    expect(self.student.locator('#demo-student-panel')).to_be_visible()
  def tearDown(self):self.context.close()
  def upload(self):
    self.faculty.locator('#demo-file').set_input_files({'name':'demo.pdf','mimeType':'application/pdf','buffer':PDF})
    expect(self.faculty.locator('#demo-disclosure')).to_be_visible()
    expect(self.faculty.locator('#demo-file-hash')).to_have_text(hashlib.sha256(PDF).hexdigest())
  def start(self):
    self.upload()
    self.faculty.locator('.demo-block[data-block-id="p1:concept"] input').check()
    self.faculty.locator('#demo-approve').click()
    expect(self.faculty.locator('#demo-start')).to_be_enabled()
    self.faculty.locator('#demo-start').click()
    expect(self.faculty.locator('#demo-class-code')).to_have_text(re.compile(r'^[A-HJ-NP-Z2-9]{6}$'))
    return self.faculty.locator('#demo-class-code').text_content()
  def join(self,code):
    self.student.locator('#demo-code-entry').fill(code[:3].lower()+'-'+code[3:].lower())
    self.student.locator('#demo-join-form button').click()
    expect(self.student.locator('#demo-student-session')).to_be_visible()
  def ask(self):
    self.student.locator('#demo-question').fill('왜 의제설정 이론은 뉴스 중요도에 영향을 주나요?')
    self.student.locator('#demo-question-submit').click()

  def test_01_real_sha256_and_demo_disclosure(self):
    self.upload()
    self.assertIn('올린 파일은 분석하지 않았습니다',self.faculty.locator('#demo-disclosure').inner_text())
    self.assertEqual(self.faculty.locator('#demo-file-name').inner_text(),'demo.pdf')
    expect(self.faculty.locator('#demo-count-candidates')).to_have_text('10')

  def test_02_all_seven_exclusions_have_disabled_checkboxes(self):
    self.upload()
    expect(self.faculty.locator('.demo-block input:disabled')).to_have_count(7)
    expect(self.faculty.locator('.demo-block input:enabled')).to_have_count(3)
    for ident in ['p4:break','p5:preview','p6:question','p7:answer','p8:note','p9:chart','p10:audio']:
      self.assertIn('승인 불가 사유:',
        self.faculty.locator('.demo-block[data-block-id="'+ident+'"]').inner_text())

  def test_03_zero_approval_gate_and_six_character_code(self):
    self.upload()
    expect(self.faculty.locator('#demo-start')).to_be_disabled()
    self.faculty.locator('.demo-block[data-block-id="p1:concept"] input').check()
    self.faculty.locator('#demo-approve').click()
    expect(self.faculty.locator('#demo-count-approved')).to_have_text('1')
    self.faculty.locator('#demo-start').click()
    code=self.faculty.locator('#demo-class-code').inner_text()
    self.assertRegex(code,r'^[A-HJ-NP-Z2-9]{6}$')
    self.assertFalse(set(code)&set('O0I1'))

  def test_04_invalid_code_then_lowercase_hyphen_code(self):
    code=self.start()
    self.student.locator('#demo-code-entry').fill('FAKE-XX')
    self.student.locator('#demo-join-form button').click()
    expect(self.student.locator('#demo-code-error')).to_be_visible()
    self.join(code)
    self.assertIn('같은 브라우저',self.student.locator('#demo-joined-title').inner_text())

  def test_05_question_depth_stems_and_approved_citations_only(self):
    code=self.start();self.join(code)
    expect(self.student.locator('#demo-coach-result')).to_be_hidden()
    self.ask()
    expect(self.student.locator('#demo-question-level')).to_have_text('깊은 질문')
    expect(self.student.locator('.demo-stem')).to_have_count(3)
    self.assertEqual(self.student.locator('#demo-evidence summary').all_text_contents(),
      ['교안 p.1 · 교수자 승인됨'])
    self.student.locator('#demo-evidence summary').click()
    self.assertIn('의제설정 이론은 뉴스가',self.student.locator('#demo-evidence').inner_text())
    self.student.locator('#demo-question').fill('축구 경기장 좌석이 왜 이렇게 달라질까요?')
    self.student.locator('#demo-question-submit').click()
    expect(self.student.locator('#demo-evidence summary')).to_have_count(0)

  def test_06_student_dom_never_contains_unapproved_blocks(self):
    code=self.start();self.join(code);self.ask()
    entire=self.student.locator('body').inner_text()
    for text in RESTRICTED:self.assertNotIn(text,entire)

  def test_07_blank_gate_submission_realtime_anonymous_and_projector(self):
    code=self.start();self.join(code);self.ask()
    self.student.locator('.demo-stem').first.click()
    area=self.student.locator('#demo-rewrite')
    self.assertIn('___',area.input_value())
    self.student.locator('#demo-rewrite-form button').click()
    expect(self.student.locator('#demo-rewrite-error')).to_have_text('빈칸을 내 생각으로 채워 주세요')
    area.fill('의제설정 이론을 다른 뉴스 매체에 적용하면 어떤 차이가 나타날까요?')
    self.student.locator('#demo-rewrite-form button').click()
    expect(self.student.locator('#demo-submit-toast')).to_have_text('익명으로 제출했어요')
    expect(self.faculty.locator('#demo-unread')).to_have_text('1',timeout=1000)
    expect(self.faculty.locator('.demo-question-card')).to_have_count(1,timeout=1000)
    self.assertNotIn('학생 이름',self.faculty.locator('.demo-question-card').inner_text())
    self.faculty.locator('.demo-question-card button').click()
    expect(self.faculty.locator('#demo-projector')).to_be_visible()
    self.assertIn('다른 뉴스 매체',self.faculty.locator('#demo-projector-question').inner_text())

  def test_08_reset_removes_class_and_questions_from_both_tabs(self):
    code=self.start();self.join(code);self.ask()
    self.student.locator('.demo-stem').first.click()
    self.student.locator('#demo-rewrite').fill('의제설정 이론의 조건을 바꾸면 어떤 차이가 나타날까요?')
    self.student.locator('#demo-rewrite-form button').click()
    expect(self.faculty.locator('.demo-question-card')).to_have_count(1)
    self.faculty.locator('#demo-reset').click()
    expect(self.faculty.locator('#demo-class-code-panel')).to_be_hidden()
    expect(self.faculty.locator('.demo-question-card')).to_have_count(0)
    expect(self.student.locator('#demo-join-card')).to_be_visible(timeout=1000)
    self.assertIsNone(self.faculty.evaluate("localStorage.getItem('learningx-demo-session-v1')"))

  def test_09_no_horizontal_overflow_or_console_errors(self):
    self.upload()
    for page in [self.faculty,self.student]:
      for width in [1440,390]:
        page.set_viewport_size({'width':width,'height':844})
        measurements=page.evaluate("({viewport:innerWidth,body:document.body.scrollWidth,root:document.documentElement.scrollWidth})")
        self.assertLessEqual(measurements['body'],measurements['viewport'],str(measurements))
        offenders=page.evaluate("Array.from(document.querySelectorAll('*')).map(e=>({tag:e.tagName,id:e.id,cls:typeof e.className==='string'?e.className.slice(0,70):'',right:Math.round(e.getBoundingClientRect().right),width:Math.round(e.getBoundingClientRect().width)})).filter(x=>x.right>innerWidth+1).sort((a,b)=>b.right-a.right).slice(0,12)")
        self.assertLessEqual(measurements['root'],measurements['viewport'],str(measurements)+' offending elements: '+str(offenders))
    self.assertEqual(self.errors,[])

if __name__=='__main__':unittest.main(verbosity=2)
