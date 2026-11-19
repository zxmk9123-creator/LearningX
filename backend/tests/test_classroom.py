"""Live classroom session flow (frozen engine mocked; ACL and anonymity are real)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-classroom-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient
from learningx.api import app
from learningx.storage import SessionLocal, User, Course, Member, Material, Question
from learningx.security import issue_token
from learningx import engine_adapter as nit
from learningx import classroom


def coaching(level):
    return {"mode": "MODEL_VALIDATED", "question_level": level,
            "follow_up_question": "두 개념을 비교하면 어떤 차이가 있을까요?",
            "feedback": "질문의 근거와 전제를 구체화해 보세요.",
            "evidence_refs": [{"id": "p3:b1", "page": 3}],
            "model_observability": {"provider": "secret-provider", "model": "x"}}


class ClassroomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    def setUp(self):
        out = Path(TEMP.name) / "private" / "mat"
        out.mkdir(parents=True, exist_ok=True)
        (out / "document.json").write_text(json.dumps({"pages": [
            {"page": n, "blocks": []} for n in range(1, 4)
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            fac = User(email=f"cls-faculty-{self._testMethodName}@test.local", role="faculty")
            other = User(email=f"cls-other-{self._testMethodName}@test.local", role="faculty")
            db.add_all([fac, other])
            db.flush()
            course = Course(title="의제설정 이론", policy="RESTRICTED_HINT_ONLY", teacher_approved=False, created_by=fac.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=fac.id, role="faculty"))
            mat = Material(course_id=course.id, filename="lesson.pdf", sha256="a" * 64, out_dir=str(out), ingested_by=fac.id)
            db.add(mat)
            db.commit()
            self.fac_id, self.other_id, self.course_id, self.material_id = fac.id, other.id, course.id, mat.id
        self.tf = issue_token(self.fac_id)
        self.to = issue_token(self.other_id)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def test_live_slide_index_scopes(self):
        cid = self.course_id
        self.req("PUT", f"/v1/courses/{cid}/policy", self.tf,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf,
                              json={"material_id": self.material_id}).json()
        path = f"/v1/sessions/{opened['id']}"
        first, second = [
            self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
            for _ in range(2)
        ]
        self.assertEqual(self.req("GET", path + "/live", self.to).status_code, 403)
        self.assertEqual(self.req("PUT", path + "/live", first, json={"page": 1}).status_code, 403)
        with patch.object(classroom, "build_snapshot", return_value=(3, {"text": "Shared slide"})):
            self.assertEqual(self.req("PUT", path + "/live", self.tf, json={"page": 1}).status_code, 200)
            snap = self.req("GET", path + "/live", first).json()["slide"]
            self.assertEqual(snap["text"], "Shared slide")
            self.assertNotIn("text", self.req("GET", path + "/live?version=" + snap["version"], first).json()["slide"])
            self.req("PUT", path + "/live", self.tf, json={"page": 2})
        anchor = {"x": .31, "y": .52, "quote": "선택한 교안 문구",
                  "rects": [{"left": .2, "top": .02 * i, "right": .4, "bottom": .02 * i + .01}
                            for i in range(24)]}
        reaction = path + "/live/confusion"
        self.assertEqual(self.req("PUT", reaction, first, json={"page": 3, "anchor": anchor}).status_code, 409)
        for _ in range(2):
            self.assertEqual(self.req("PUT", reaction, first, json={"page": 2, "anchor": anchor}).status_code, 200)
        summary = self.req("GET", path + "/live", self.tf).json()["confusion"]
        self.assertEqual(summary, {"count": 1, "spots": [{"x": .3, "y": .5, "count": 1, "rects": anchor["rects"]}]})
        self.assertIsNone(self.req("GET", path + "/live", second).json()["confusion"])
        self.assertTrue(self.req("GET", path + "/live", first).json()["confusion"]["mine"])
        self.assertEqual(self.req("PUT", reaction, first, json={"page": 1, "anchor": anchor}).status_code, 200)
        with patch.object(classroom, "build_snapshot", return_value=(3, {"text": "Shared slide"})):
            for page in (1, 2):
                self.assertEqual(self.req("PUT", path + "/live", self.tf, json={"page": page}).status_code, 200)
                self.assertEqual(self.req("GET", path + "/live", self.tf).json()["confusion"], summary)
        with patch.object(nit, "coach_question", return_value=coaching("analyze")):
            question = self.req("POST", path + "/questions", first,
                                json={"question": "질문의 근거는 무엇인가요?", "slide_page": 1, "anchor": anchor}).json()
        self.assertEqual(self.req("GET", path + "/live", self.tf).json()["index"], [])
        self.assertEqual(self.req("GET", path + "/live", second).json()["index"], [])
        expected = [{"id": question["id"], "level": "mid", "page": 1,
                     "text": "질문의 근거는 무엇인가요?", "anchor": anchor}]
        self.assertEqual(self.req("GET", path + "/live", first).json()["index"], expected)
        self.req("POST", f"/v1/session-questions/{question['id']}/submit", first)
        live = self.req("GET", path + "/live", self.tf).json()
        self.assertEqual(live["index"], expected)
        self.assertNotIn("index", self.req("GET", path + "/live", self.tf,
                                           params={"index_version": live["index_version"]}).json())
        self.req("POST", f"/v1/session-questions/{question['id']}/hide", self.tf)
        self.assertEqual(self.req("GET", path + "/live", self.tf).json()["index"], [])
        self.req("PUT", path + "/state", self.tf, json={"status": "CLOSED"})
        self.assertIsNone(self.req("GET", path + "/live", first).json()["slide"])

    def test_live_pdf_starts_automatically_and_limits_prefetch(self):
        import pymupdf

        folder = Path(TEMP.name) / "selectable-pdf"
        parsed = folder / "parsed"
        parsed.mkdir(parents=True)
        with pymupdf.open() as pdf:
            for label in ["Visible slide text", "Hidden slide text", "Next slide text", "Distant slide text"]:
                pdf.new_page().insert_text((72, 72), label)
            pdf.save(folder / "source.pdf")
        (parsed / "document.json").write_text(json.dumps({"pages": [
            {"page": 1, "blocks": []},
            {"page": 2, "blocks": [], "hidden_slide": True},
            {"page": 3, "blocks": []},
            {"page": 4, "blocks": []},
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            db.get(Material, self.material_id).out_dir = str(parsed)
            db.commit()
        course = f"/v1/courses/{self.course_id}"
        self.req("PUT", course + "/policy", self.tf,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", course + "/sessions", self.tf,
                              json={"material_id": self.material_id}).json()
        self.assertEqual(opened["initial_live"]["slide"]["page"], 1)
        self.assertEqual(opened["initial_live"]["slide"]["visible_pages"], [1, 3, 4])
        joined = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()
        self.assertEqual(joined["initial_live"]["slide"]["page"], 1)
        self.assertNotIn("visible_pages", joined["initial_live"]["slide"])
        student = joined["access_token"]
        path = f"/v1/sessions/{opened['id']}/live"
        self.assertEqual(self.req("GET", path + "/document", student).status_code, 403)
        self.assertEqual(self.req("GET", path + "/document", self.tf).status_code, 200)
        self.assertEqual(self.req("PUT", path, self.tf, json={"page": 2}).status_code, 422)
        initial = self.req("GET", path, student).json()
        self.assertEqual(initial["slide"]["page"], 1)
        self.assertEqual(initial["slide"]["after"], 3)
        prefetch = path + "/page/" + initial["slide"]["version"]
        for page in (2, 4):
            self.assertEqual(self.req("GET", prefetch, student, params={"page": page}).status_code, 403)
        next_pdf = self.req("GET", prefetch, student, params={"page": 3})
        with pymupdf.open(stream=next_pdf.content, filetype="pdf") as pdf:
            self.assertEqual(pdf[0].get_text().strip(), "Next slide text")
        batch = {"pages": [3, 1, 3], "batch_id": "6e2f3169-9826-42cd-82ec-fb3748478933"}
        for _ in range(2):
            self.assertEqual(self.req("PUT", path, self.tf, json=batch).status_code, 200)
        ordered = self.req("GET", path, student, params={"cursor": initial["slide"]["version"]}).json()["transitions"]
        self.assertEqual([item["page"] for item in ordered], [3, 1, 3])
        old_page = path + "/page/" + ordered[0]["version"]
        self.assertEqual(self.req("GET", old_page, student).status_code, 200)
        self.assertEqual(self.req("GET", old_page, student, params={"page": 4}).status_code, 403)
        before = initial["revision"]
        self.assertEqual(self.req("PUT", path, self.tf, json={"page": 1}).status_code, 200)
        state = self.req("GET", path, student, params={"after": before}).json()
        self.assertNotEqual(state["revision"], before)
        self.assertTrue(state["slide"]["pdf"])
        page = path + "/page/" + state["slide"]["version"]
        self.assertEqual(self.req("GET", page, self.to).status_code, 403)
        response = self.req("GET", page, student)
        self.assertEqual(response.status_code, 200)
        with pymupdf.open(stream=response.content, filetype="pdf") as pdf:
            self.assertEqual(len(pdf), 1)
            self.assertEqual(pdf[0].get_text().strip(), "Visible slide text")
        self.assertEqual(self.req("GET", path + "/page/stale", student).status_code, 409)

    def test_pptx_session_requires_worker_preview_and_serves_it(self):
        import pymupdf

        with SessionLocal() as db:
            material = db.get(Material, self.material_id)
            material.filename = "legacy.pptx"
            parsed = Path(material.out_dir)
            db.commit()
        preview = parsed / "live-preview.pdf"
        preview.unlink(missing_ok=True)
        course = f"/v1/courses/{self.course_id}"
        self.req("PUT", course + "/policy", self.tf,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        with patch.object(nit, "verified_count", return_value=1):
            response = self.req("POST", course + "/sessions", self.tf,
                                json={"material_id": self.material_id})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"], "LIVE_PREVIEW_UNAVAILABLE")
            with pymupdf.open() as pdf:
                for n in range(3):
                    pdf.new_page().insert_text((72, 72), f"Worker preview slide {n + 1}")
                pdf.save(preview)
            response = self.req("POST", course + "/sessions", self.tf,
                                json={"material_id": self.material_id})
        self.assertEqual(response.status_code, 201, response.text)
        path = f"/v1/sessions/{response.json()['id']}/live"
        self.assertEqual(self.req("GET", path, self.tf).json()["slide"]["page"], 1)
        document = self.req("GET", path + "/document", self.tf)
        with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
            self.assertEqual(pdf[0].get_text().strip(), "Worker preview slide 1")

    def test_full_flow(self):
        cid, mid = self.course_id, self.material_id
        # Coaching consent is required before opening.
        with patch.object(nit, "verified_count", return_value=3):
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 409, r.text)
        self.req("PUT", f"/v1/courses/{cid}/policy", self.tf, json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        # Unsigned material cannot open a session.
        with patch.object(nit, "verified_count", return_value=0):
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 409)
        # Non-member faculty cannot open.
        with patch.object(nit, "verified_count", return_value=3):
            self.assertEqual(self.req("POST", f"/v1/courses/{cid}/sessions", self.to, json={"material_id": mid}).status_code, 403)
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 201, r.text)
        sess = r.json()
        code, sid = sess["join_code"], sess["id"]
        self.assertEqual(len(code), 6)
        self.assertTrue(all(ch in classroom.JOIN_ALPHABET for ch in code))

        # Bad code is rejected; lowercase input works.
        self.assertEqual(self.req("POST", "/v1/join", json={"code": "ZZZZZZ"}).status_code, 404)
        j = self.req("POST", "/v1/join", json={"code": code.lower()})
        self.assertEqual(j.status_code, 200, j.text)
        ts = j.json()["access_token"]
        self.assertNotIn("join_code", j.json()["session"])
        j2 = self.req("POST", "/v1/join", json={"code": code})
        ts2 = j2.json()["access_token"]

        # Student cannot see the faculty board or export.
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/board", ts).status_code, 403)
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/export", ts).status_code, 403)

        # Ask → shallow, revise → mid → deep, submit.
        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            a = self.req("POST", f"/v1/sessions/{sid}/questions", ts, json={"question": "의제설정 이론은 누가 만들었나요?"})
        self.assertEqual(a.status_code, 201, a.text)
        body = a.json()
        self.assertEqual(body["level"], "shallow")
        self.assertNotIn("model_observability", body["coaching"])
        self.assertNotIn("id", body["coaching"]["evidence_refs"][0])
        rid = body["id"]
        with patch.object(nit, "coach_question", return_value=coaching("analyze")):
            r = self.req("POST", f"/v1/session-questions/{rid}/revisions", ts, json={"text": "의제설정 이론과 프레이밍 이론은 무엇이 다른가요?"})
        self.assertEqual(r.json()["level"], "mid")
        with patch.object(nit, "coach_question", return_value=coaching("evaluate")):
            r = self.req("POST", f"/v1/session-questions/{rid}/revisions", ts,
                         json={"text": "의제설정 이론을 유튜브 추천 알고리즘에 적용하면 언론의 영향력은 어떻게 달라지나요?"})
        self.assertEqual(r.json()["level"], "deep")
        # Another student cannot revise or submit someone else's question.
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid}/submit", ts2).status_code, 404)
        # Unsubmitted questions are invisible on the board.
        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["items"], [])


        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            a2 = self.req("POST", f"/v1/sessions/{sid}/questions", ts2, json={"question": "채플힐 연구는 언제 했나요?"})
        rid2 = a2.json()["id"]
        self.req("POST", f"/v1/session-questions/{rid2}/submit", ts2)
        s = self.req("POST", f"/v1/session-questions/{rid}/submit", ts)
        self.assertEqual(s.status_code, 200)
        # Submitted questions cannot be revised.
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid}/revisions", ts, json={"text": "다른 질문으로 바꿔 볼게요"}).status_code, 409)

        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["counts"], {"all": 2, "shallow": 1, "mid": 0, "deep": 1})
        self.assertEqual([item["id"] for item in b["items"]], [rid2, rid])
        index = self.req("GET", f"/v1/sessions/{sid}/live", self.tf).json()["index"]
        self.assertEqual([item["id"] for item in index], [rid2, rid])
        self.assertIsNotNone(b["items"][1]["original"])
        flat = str(b)
        self.assertNotIn("anon+", flat)
        self.assertNotIn("student", flat.lower().replace("students", ""))
        self.assertEqual(b["session"]["participants"], 2)
        self.assertEqual(b["session"]["questions_deep"], 1)
        deep_only = self.req("GET", f"/v1/sessions/{sid}/board?level=deep", self.tf).json()
        self.assertEqual(len(deep_only["items"]), 1)

        # Project, then hide the other; projected endpoint returns the item.
        p = self.req("POST", f"/v1/session-questions/{rid}/project", self.tf,
                     json={"displayed_ids": [rid2]})
        self.assertEqual(p.status_code, 200)
        after_batch = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(after_batch["session"]["projected_id"], rid)
        self.assertEqual(after_batch["session"]["questions_projected"], 2)
        pj = self.req("GET", f"/v1/sessions/{sid}/projected", self.tf).json()
        self.assertEqual(pj["item"]["id"], rid)
        self.assertEqual(pj["next_id"], rid2)
        self.assertEqual(pj["previous_id"], rid2)
        self.req("POST", f"/v1/session-questions/{rid2}/project", self.tf)
        back = self.req("GET", f"/v1/sessions/{sid}/projected", self.tf).json()
        self.assertEqual((back["previous_id"], back["next_id"]), (rid, rid))
        self.req("POST", f"/v1/session-questions/{rid}/project", self.tf)
        self.req("POST", f"/v1/session-questions/{rid2}/hide", self.tf)
        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["counts"]["all"], 1)
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid2}/project", self.tf).status_code, 409)

        # Student sees only their own questions.
        mine = self.req("GET", f"/v1/sessions/{sid}/mine", ts).json()
        self.assertEqual(len(mine["items"]), 1)
        self.assertTrue(mine["items"][0]["submitted"])

        # Pause blocks new questions and joins.
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "PAUSED"})
        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            self.assertEqual(self.req("POST", f"/v1/sessions/{sid}/questions", ts2, json={"question": "매콤스와 쇼는 누구인가요?"}).status_code, 423)
        self.assertEqual(self.req("POST", "/v1/join", json={"code": code}).status_code, 423)
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "OPEN"})

        # Close: code expires, join refused, CSV excludes hidden and identities.
        c = self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "CLOSED"})
        self.assertIsNone(c.json()["join_code"])
        self.assertEqual(self.req("POST", "/v1/join", json={"code": code}).status_code, 404)
        e = self.req("GET", f"/v1/sessions/{sid}/export", self.tf)
        self.assertEqual(e.status_code, 200)
        text = e.content.decode("utf-8-sig")
        self.assertIn("처음 질문", text)
        self.assertIn("유튜브", text)
        self.assertNotIn("채플힐", text)
        self.assertNotIn("anon+", text)
        self.assertEqual(self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "OPEN"}).status_code, 409)

        # Underlying question rows exist for the existing audit trail.
        with SessionLocal() as db:
            self.assertEqual(db.query(Question).filter_by(course_id=cid).count(), 2)

    def _open_for_security_test(self):
        self.req("PUT", f"/v1/courses/{self.course_id}/policy", self.tf,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.tf,
                              json={"material_id": self.material_id})
        self.assertEqual(opened.status_code, 201, opened.text)
        session = opened.json()
        joined = self.req("POST", "/v1/join", json={"code": session["join_code"]})
        self.assertEqual(joined.status_code, 200, joined.text)
        return session["id"], joined.json()["access_token"]

    def test_scoped_tokens_block_legacy_routes_and_pause_submission(self):
        sid, token = self._open_for_security_test()
        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            created = self.req("POST", f"/v1/sessions/{sid}/questions", token,
                               json={"question": "의제설정의 의미는 무엇인가요?"})
        self.assertEqual(created.status_code, 201, created.text)
        rid = created.json()["id"]
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "PAUSED"})
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid}/submit", token).status_code, 423)
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "CLOSED"})
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/mine", token).status_code, 200)
        with patch.object(nit, "coach_question") as provider:
            legacy = self.req("POST", f"/v1/courses/{self.course_id}/questions", token,
                              json={"material_id": self.material_id, "question": "종료 후 새 질문을 작성할 수 있나요?"})
            self.assertEqual(legacy.status_code, 403)
            provider.assert_not_called()
        self.assertEqual(self.req("GET", f"/v1/courses/{self.course_id}/materials", token).status_code, 403)
        self.assertEqual(self.req("GET", "/v1/courses/mine", token).status_code, 403)
        # Anonymous accounts cannot receive unscoped tokens as a fallback.
        import jwt
        claims = jwt.decode(token, options={"verify_signature": False})
        unscoped = issue_token(claims["sub"])
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/mine", unscoped).status_code, 403)
        second, _ = self._open_for_security_test()
        self.assertEqual(self.req("GET", f"/v1/sessions/{second}", token).status_code, 403)
        # A token bound to a session is still insufficient without DB participation.
        wrong_participant = issue_token(claims["sub"], session_id=second)
        self.assertEqual(self.req("GET", f"/v1/sessions/{second}", wrong_participant).status_code, 403)

    def test_csv_question_cells_are_not_formulas(self):
        sid, token = self._open_for_security_test()
        import csv, io
        for text in ("=1+1+1", "+SUM(1,2)", "-1+2+3", "@SUM(1,2)", "  =1+1+1", "\t=1+1+1"):
            with patch.object(nit, "coach_question", return_value=coaching("clarify")):
                row = self.req("POST", f"/v1/sessions/{sid}/questions", token, json={"question": text})
            self.assertEqual(row.status_code, 201, row.text)
            self.req("POST", f"/v1/session-questions/{row.json()['id']}/submit", token)
        exported = self.req("GET", f"/v1/sessions/{sid}/export", self.tf)
        cells = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))[1:]
        self.assertEqual(len(cells), 6)
        self.assertTrue(all(row[2].startswith("'") for row in cells))

    def test_join_throttle(self):
        classroom._FAILED_JOINS.clear()
        codes = [self.req("POST", "/v1/join", json={"code": "QQQQQQ"}).status_code for _ in range(classroom.JOIN_FAIL_LIMIT + 1)]
        self.assertEqual(codes[-1], 429)
        classroom._FAILED_JOINS.clear()


if __name__ == "__main__":
    unittest.main()
