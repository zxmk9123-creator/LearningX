"""Browsers call the API cross-origin, so each route method must pass the CORS preflight.

Deleting a course from 내 수업 failed in production with "서버 요청에 실패했습니다"
because DELETE was missing from the allowed methods: the preflight was rejected
and the request never reached the route.
"""
import os
import tempfile
import unittest
from pathlib import Path

TEMP = tempfile.TemporaryDirectory(prefix="learningx-cors-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

from learningx import api


class CorsMethodTests(unittest.TestCase):
    def test_every_route_method_is_allowed_by_cors(self):
        # The OpenAPI table covers routes from every included router.
        used = {method.upper() for path, operations in api.app.openapi()["paths"].items() if path.startswith("/v1/")
                for method in operations} - {"HEAD", "OPTIONS"}
        self.assertIn("DELETE", used)
        self.assertEqual(sorted(used - set(api.CORS_METHODS)), [], "add these methods to CORS_METHODS")

    def test_delete_preflight_passes(self):
        app = api.FastAPI()
        app.add_middleware(CORSMiddleware, allow_origins=["https://learning-x-one.vercel.app"], allow_credentials=False,
                           allow_methods=api.CORS_METHODS, allow_headers=["Authorization", "Content-Type"])

        @app.delete("/v1/courses/{course_id}")
        def remove(course_id: str):
            return {}

        response = TestClient(app).options("/v1/courses/x", headers={
            "Origin": "https://learning-x-one.vercel.app", "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("DELETE", response.headers["access-control-allow-methods"])


if __name__ == "__main__":
    unittest.main()
