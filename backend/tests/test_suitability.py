"""Question suitability checks and selective (batch) send, spec v0.1 with the agreed decisions."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-suitability-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx import suitability as fit
from learningx.api import app
from learningx.classroom import ClassSession, SessionQuestion
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, Question, SessionLocal, User, uid

COACHING = {"mode": "MODEL_VALIDATED", "question_level": "analyze", "feedback": "근거를 구체화해 보세요.",
            "follow_up_question": "두 개념은 어떤 기준으로 구별할 수 있을까요?", "evidence_refs": [{"id": "p1:b1", "page": 1}]}
CLEAN = "토론 뒤 평균이 극단으로 이동한 이유를 분포로 설명할 수 있나요?"


class Gateway:
    """Model stand-in: answers by keyword, or fails."""

    def __init__(self, answers=None, fail=False):
        self.answers, self.fail, self.calls = answers or {}, fail, []

    def generate(self, system, user):
        self.calls.append(json.loads(user))
        if self.fail:
            raise TimeoutError("model timeout")
        question = json.loads(user)["question"]
        for key, (status, codes) in self.answers.items():
            if key in question:
                return SimpleNamespace(text=json.dumps({"status": status, "reason_codes": codes}))
        return SimpleNamespace(text='{"status":"eligible","reason_codes":[]}')


class SuitabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        self.patches = [patch.object(fit, "RETRY_BACKOFF_SECONDS", (0, 0, 0)),
                        patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "", "NIT_WORKER_BACKEND": ""})]
        for p in self.patches:
            p.start()
        self.gateway = None  # no model configured unless a test sets one
        self.patches.append(patch.object(nit, "model_gateway", lambda: self.gateway))
        self.patches[-1].start()
        self.open_class()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def open_class(self, guest=False):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            email = f"fit-{uid()}@" + ("guest.question-up.local" if guest else "test.local")
            faculty = User(email=email, role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="집단극화", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="e" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            self.course_id, self.material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                              json={"material_id": self.material_id}).json()
        self.session_id, self.code = opened["id"], opened["join_code"]

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def student(self):
        return self.req("POST", "/v1/join", json={"code": self.code}).json()["access_token"]

    def ask(self, token, text=CLEAN, **extra):
        with patch.object(nit, "coach_question", return_value=COACHING):
            res = self.req("POST", f"/v1/sessions/{self.session_id}/questions", token, json={"question": text, **extra})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def revise(self, token, row_id, text):
        with patch.object(nit, "coach_question", return_value=COACHING):
            res = self.req("POST", f"/v1/session-questions/{row_id}/revisions", token, json={"text": text})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def mine(self, token):
        return {item["id"]: item for item in self.req("GET", f"/v1/sessions/{self.session_id}/mine", token).json()["items"]}

    def batch(self, token, items, request_id=None):
        return self.req("POST", f"/v1/sessions/{self.session_id}/questions/submit-batch", token, json={
            "request_id": request_id or ("req-" + uid()),
            "items": [{"question_id": i["id"], "revision_id": i["revision_id"]} for i in items]})

    def board_texts(self):
        board = self.req("GET", f"/v1/sessions/{self.session_id}/board?include_hidden=true", self.faculty).json()
        return sorted(item["text"] for item in board["items"])

    # ---------- rules ----------

    def test_rules_block_only_verifiable_patterns(self):
        cases = {
            "제 번호 010-1234-5678로 연락 주세요. 평균 이동은 왜 생기나요?": ["personal_or_private"],
            "메일 kim.student@example.com 으로 자료 주세요": ["personal_or_private"],
            "오픈채팅 https://open.kakao.com/o/abc 들어오세요": ["disruption"],
            "할인 이벤트 https://shop.example.com 확인하세요": ["disruption"],
        }
        for text, codes in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fit.rule_reasons(text), codes)
        allowed = [
            "교수님 설명과 달리 저는 평균 이동이 소수 의견 때문이라고 생각하는데, 이 주장은 틀렸나요?",
            "교안의 '바보 같은 결정'이라는 표현은 집단극화의 예시로 인용된 건가요?",
            "제가 동아리 회의에서 비슷한 경험을 했는데 이것도 집단극화인가요?",
            "자료 출처 https://doi.org/10.1000/xyz 에 나온 분포 그림과 같은 의미인가요?",
            "죽음이나 차별처럼 민감한 주제도 집단극화로 분석할 수 있나요?",
        ]
        for text in allowed:
            with self.subTest(text=text):
                self.assertEqual(fit.rule_reasons(text), [], "polite objection, quotation, anonymised experience")

    def test_rules_catch_obfuscated_identifiers_direct_abuse_and_spam(self):
        cases = {
            "제 번호는 ０１０-１２３４-５６７８입니다": ["personal_or_private"],
            "메일 me@exam\u200bple.com 으로 보내 주세요": ["personal_or_private"],
            "선생님 집 주소 알려주세요": ["personal_or_private"],
            "교수님은 진짜 멍청하네": ["targeted_attack"],
            "저희 채널을 구독해 주세요": ["disruption"],
            "ㅋㅋㅋㅋㅋㅋ": ["disruption"],
            "교안에서 '교수님은 바보야'라는 발화는 어떤 오류인가요?": [],
            "교수님 설명이 틀렸다고 생각하는데 다시 설명해 주실 수 있나요?": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(fit.rule_reasons(text), expected)

    def test_batch_enforces_suitability_even_with_legacy_guard_off(self):
        token = self.student()
        bad = self.ask(token, "교수님은 진짜 멍청하네")
        good = self.ask(token)
        self.assertEqual(bad["suitability"]["primary_reason"], "targeted_attack")
        self.assertEqual(self.batch(token, [good, bad]).status_code, 409)
        self.assertEqual(self.board_texts(), [])
        self.assertEqual(self.batch(token, [good]).status_code, 200)
        self.assertFalse(self.mine(token)[bad["id"]]["submitted"])

    def test_repeated_identical_text_is_disruption_but_revisions_are_not(self):
        token = self.student()
        first = self.ask(token, "평균은 왜 움직이나요?")
        self.revise(token, first["id"], "평균은 왜 움직이나요??")  # a revision is not a new question
        self.assertEqual(self.ask(token, "평균은 왜 움직이나요")["suitability"]["status"], "eligible")
        third = self.ask(token, "평균은 왜 움직이나요 !")
        self.assertEqual(third["suitability"]["reason_codes"], ["disruption"])

    # ---------- no model / guest ----------

    def test_without_a_model_clean_questions_are_eligible_immediately(self):
        token = self.student()
        body = self.ask(token)
        self.assertEqual(body["suitability"]["status"], "eligible")
        self.assertTrue(body["revision_id"])
        self.assertEqual(body["coaching"]["follow_up_question"], COACHING["follow_up_question"])
        blocked = self.ask(token, "제 번호는 010-2222-3333 입니다. 분포가 궁금해요")
        self.assertEqual((blocked["suitability"]["status"], blocked["suitability"]["primary_reason"]),
                         ("needs_revision", "personal_or_private"))
        self.assertNotIn("010-2222-3333", json.dumps(blocked["suitability"]), "no echo of the blocked detail")

    def test_guest_classes_use_rules_even_when_a_model_is_configured(self):
        self.gateway = Gateway(answers={"분포": ("needs_revision", ["off_topic"])})
        self.open_class(guest=True)
        body = self.ask(self.student())
        self.assertEqual(body["suitability"]["status"], "eligible")
        self.assertEqual(self.gateway.calls, [])

    # ---------- model path ----------

    def test_model_check_runs_after_the_response_and_updates_the_draft(self):
        self.gateway = Gateway(answers={"연예인": ("needs_revision", ["off_topic"])})
        token = self.student()
        with patch.object(fit, "process") as later:
            body = self.ask(token)
        self.assertEqual(body["suitability"]["status"], "pending", "ask answers with coaching and pending")
        later.assert_called_once_with(body["id"], body["revision_id"])
        fit.process(body["id"], body["revision_id"])
        self.assertEqual(self.mine(token)[body["id"]]["suitability"]["status"], "eligible")
        off = self.ask(token, "좋아하는 연예인 누구예요?")  # TestClient runs the background task
        self.assertEqual(self.mine(token)[off["id"]]["suitability"]["reason_codes"], ["off_topic"])
        sent = self.gateway.calls[-1]
        self.assertEqual(set(sent), {"lesson", "question"}, "only lesson context and the question are sent")

    def test_model_failure_leaves_pending_and_a_retry_succeeds(self):
        self.gateway = Gateway(fail=True)
        token = self.student()
        body = self.ask(token)
        item = self.mine(token)[body["id"]]
        self.assertEqual((item["suitability"]["status"], item["suitability"]["reason_codes"]),
                         ("pending", ["check_unavailable"]))
        self.assertEqual(len(self.gateway.calls), fit.MAX_ATTEMPTS)
        self.gateway.fail = False
        again = self.req("POST", f"/v1/session-questions/{body['id']}/suitability/check", token,
                         json={"revision_id": body["revision_id"]})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self.mine(token)[body["id"]]["suitability"]["status"], "eligible")
        stale = self.req("POST", f"/v1/session-questions/{body['id']}/suitability/check", token,
                         json={"revision_id": "old"})
        self.assertEqual((stale.status_code, stale.json()["detail"]["code"]), (409, "stale_revision"))

    def test_a_late_result_for_an_older_draft_is_discarded(self):
        self.gateway = Gateway(answers={"원래": ("needs_revision", ["context_needed"])})
        token = self.student()
        with patch.object(fit, "process"):
            first = self.ask(token, "원래 질문: 이건 왜 그런가요?")
        with SessionLocal() as db:
            old = fit.claim(db, first["id"], first["revision_id"])  # the worker picked up the first draft
        with patch.object(fit, "process"):
            second = self.revise(token, first["id"], CLEAN)
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual(fit.run(old), "stale", "the older draft's result is dropped")
        self.assertEqual(self.mine(token)[first["id"]]["suitability"]["status"], "pending")
        fit.process(first["id"], second["revision_id"])
        item = self.mine(token)[first["id"]]
        self.assertEqual((item["revision_id"], item["suitability"]["status"]), (second["revision_id"], "eligible"))

    def test_worker_polls_queued_checks(self):
        self.gateway = Gateway()
        with patch.dict(os.environ, {"NIT_WORKER_BACKEND": "northflank_poll"}):
            token = self.student()
            body = self.ask(token)  # no in-process run when the worker owns checks
        self.assertEqual(self.mine(token)[body["id"]]["suitability"]["status"], "pending")
        outcomes = []
        for _ in range(50):  # drain: earlier tests may have left queued checks behind
            outcomes.append(fit.poll_once())
            if outcomes[-1] == "EMPTY":
                break
        self.assertIn("eligible", outcomes)
        self.assertEqual(outcomes[-1], "EMPTY")
        self.assertEqual(self.mine(token)[body["id"]]["suitability"]["status"], "eligible")

    # ---------- policy version ----------

    def test_open_classes_keep_their_policy_version(self):
        token = self.student()
        with patch.object(fit, "POLICY_VERSION", "suitability-v2"):
            body = self.ask(token)
            self.assertEqual(body["suitability"]["policy_version"], "suitability-v1",
                             "a deploy does not change an open class's policy")
            with patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "1"}):
                self.assertEqual(self.batch(token, [body]).status_code, 200)
            self.open_class()
            self.assertEqual(self.ask(self.student())["suitability"]["policy_version"], "suitability-v2")

    # ---------- send gate ----------

    def test_batch_sends_only_when_every_selected_draft_is_eligible(self):
        token = self.student()
        a, b = self.ask(token, CLEAN), self.ask(token, "개인 의견과 집단 의견은 어떻게 구분하나요?")
        bad = self.ask(token, "제 이메일 me@example.com 입니다. 분포 그림을 다시 설명해 주세요")
        with patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "1"}):
            mixed = self.batch(token, [a, b, bad])
            self.assertEqual(mixed.status_code, 409)
            self.assertEqual(mixed.json()["detail"], {"code": "batch_not_submitted",
                                                      "items": [{"question_id": bad["id"], "code": "not_eligible"}]})
            self.assertEqual(self.board_texts(), [], "a failed batch submits nothing")
            key = "req-" + uid()
            ok = self.batch(token, [a, b], key)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual([i["question_id"] for i in ok.json()["submitted"]], [a["id"], b["id"]])
            self.assertEqual(self.batch(token, [a, b], key).json(), ok.json(), "same key and list -> same response")
            reused = self.batch(token, [a], key)
            self.assertEqual((reused.status_code, reused.json()["detail"]["code"]), (409, "request_id_reused"))
            again = self.batch(token, [a])
            self.assertEqual(again.json()["detail"]["items"], [{"question_id": a["id"], "code": "already_submitted"}])
        self.assertEqual(self.board_texts(), sorted([CLEAN, "개인 의견과 집단 의견은 어떻게 구분하나요?"]))
        csv_text = self.req("GET", f"/v1/sessions/{self.session_id}/export", self.faculty).text
        self.assertNotIn("me@example.com", csv_text)

    def test_stale_pending_and_foreign_ids_are_rejected_whole(self):
        token, other = self.student(), self.student()
        mine = self.ask(token)
        theirs = self.ask(other)
        old_revision = mine["revision_id"]
        self.revise(token, mine["id"], CLEAN + " 예를 들어 주세요.")
        with patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "1"}):
            stale = self.batch(token, [{**mine, "revision_id": old_revision}])
            self.assertEqual(stale.json()["detail"]["items"][0]["code"], "stale_revision")
            current = self.mine(token)[mine["id"]]
            foreign = self.batch(token, [current, theirs])
            self.assertEqual(foreign.status_code, 404, "no detail about whose question it is")
            self.assertNotIn(theirs["id"], foreign.text)
            self.gateway = Gateway()
            with patch.object(fit, "process"):
                pending = self.ask(token, "아직 확인 중인 질문입니다. 분포는요?")
            self.assertEqual(self.batch(token, [pending]).json()["detail"]["items"][0]["code"], "check_pending")
        self.assertEqual(self.board_texts(), [])

    def test_batch_validation_and_closed_class(self):
        token = self.student()
        body = self.ask(token)
        ref = {"question_id": body["id"], "revision_id": body["revision_id"]}
        url = f"/v1/sessions/{self.session_id}/questions/submit-batch"
        for payload in ({"request_id": "req-12345678", "items": []},
                        {"request_id": "req-12345678", "items": [ref, ref]},
                        {"request_id": "req-12345678", "items": [ref] * 21},
                        {"request_id": "bad key!", "items": [ref]}):
            with self.subTest(payload=len(payload["items"])):
                self.assertEqual(self.req("POST", url, token, json=payload).status_code, 422)
        self.req("PUT", f"/v1/sessions/{self.session_id}/state", self.faculty, json={"status": "CLOSED"})
        self.assertEqual(self.batch(token, [body]).status_code, 423)

    def test_single_submit_keeps_old_behaviour_when_the_gate_is_off(self):
        token = self.student()
        bad = self.ask(token, "제 번호 010-9999-8888, 평균 이동이 궁금합니다")
        with patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "1"}):
            blocked = self.req("POST", f"/v1/session-questions/{bad['id']}/submit", token)
            self.assertEqual((blocked.status_code, blocked.json()["detail"]["code"]), (409, "not_eligible"))
            good = self.ask(token)
            self.assertEqual(self.req("POST", f"/v1/session-questions/{good['id']}/submit", token).status_code, 200,
                             "an old client without a revision id sends the current eligible draft")
        off = self.req("POST", f"/v1/session-questions/{bad['id']}/submit", token)
        self.assertEqual(off.status_code, 200, "gate off: the previous single submit")

    def test_questions_written_before_checks_exist(self):
        token = self.student()
        body = self.ask(token)
        with SessionLocal() as db:
            row = db.get(SessionQuestion, body["id"])
            q = Question(course_id=self.course_id, material_id=self.material_id, student_id=row.student_id,
                         text="예전 질문입니다. 분포가 왜 바뀌나요?", feedback_json="{}")
            db.add(q)
            db.flush()
            legacy = SessionQuestion(session_id=self.session_id, question_id=q.id, student_id=row.student_id,
                                     latest_text=q.text, latest_coaching_json=json.dumps(COACHING))
            db.add(legacy)
            db.commit()
            legacy_id = legacy.id
        item = self.mine(token)[legacy_id]
        self.assertEqual((item["revision_id"], item["suitability"]["status"]), (fit.legacy_revision(legacy_id), "pending"))
        with patch.dict(os.environ, {"NIT_QUESTION_SUITABILITY_ENFORCE": "1"}):
            self.assertEqual(self.batch(token, [item]).json()["detail"]["items"][0]["code"], "check_pending",
                             "no old level is converted into suitability")
            checked = self.req("POST", f"/v1/session-questions/{legacy_id}/suitability/check", token,
                               json={"revision_id": item["revision_id"]}).json()
            self.assertEqual(checked["suitability"]["status"], "eligible")
            self.assertEqual(self.batch(token, [self.mine(token)[legacy_id]]).status_code, 200)

    def test_two_tabs_sending_the_same_questions_submit_once(self):
        from learningx.storage import engine
        if engine.dialect.name != "postgresql":
            self.skipTest("row locks need PostgreSQL")
        import threading
        token = self.student()
        a, b = self.ask(token), self.ask(token, "개인 의견과 집단 의견은 어떻게 구분하나요?")
        results, barrier = [], threading.Barrier(2)

        def send():
            barrier.wait()
            results.append(self.batch(token, [a, b]))

        threads = [threading.Thread(target=send) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        codes = sorted(r.status_code for r in results)
        self.assertEqual(codes, [200, 409], [r.text for r in results])
        loser = next(r for r in results if r.status_code == 409).json()["detail"]
        self.assertEqual({i["code"] for i in loser["items"]}, {"already_submitted"})
        self.assertEqual(len(self.board_texts()), 2, "no duplicate on the board")

    def test_retransmitted_ask_returns_the_first_question(self):
        token = self.student()
        first = self.ask(token, request_id="ask-" + "a" * 12)
        again = self.ask(token, request_id="ask-" + "a" * 12)
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(self.mine(token)), 1)


if __name__ == "__main__":
    unittest.main()
