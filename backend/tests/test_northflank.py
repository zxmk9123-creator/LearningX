"""Pure contract tests for the Northflank container and DB-poll worker.

No cloud credentials, real storage, running Docker daemon or network calls.
"""
import asyncio
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from learningx import northflank_worker
from learningx.ingest_queue import _dispatch_job


class NorthflankDeploymentTests(unittest.TestCase):
    def test_dispatch_queues_in_db_without_external_api_calls(self):
        with patch.dict(os.environ, {"NIT_WORKER_BACKEND": "northflank_poll"}, clear=True):
            # Dispatch does not import a Vercel queue client or call GitHub.
            self.assertIsNone(asyncio.run(_dispatch_job("abc" * 10, 0)))

    def test_missing_database_never_falls_back_to_sqlite(self):
        with patch.dict(os.environ, {"NIT_WORKER_BACKEND": "northflank_poll",
                                      "DATABASE_URL": "sqlite:////tmp/unsafe.sqlite"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                northflank_worker.validate_config()

    def test_missing_s3_credentials_fails_closed(self):
        with patch.dict(os.environ, {"NIT_WORKER_BACKEND": "northflank_poll",
                                      "DATABASE_URL": "postgresql://local.invalid/learningx"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Neon S3"):
                northflank_worker.validate_config()

    def test_missing_engine_fails_closed(self):
        env = {
            "NIT_WORKER_BACKEND": "northflank_poll",
            "DATABASE_URL": "postgresql://local.invalid/learningx",
            "AWS_ACCESS_KEY_ID": "test-only",
            "AWS_SECRET_ACCESS_KEY": "test-only",
            "AWS_ENDPOINT_URL_S3": "https://invalid.example",
            "AWS_REGION": "ap-southeast-1",
            "NIT_STORAGE_BUCKET": "test-bucket",
        }
        with tempfile.TemporaryDirectory() as d:
            env["NIT_ENGINE_ROOT"] = d
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "engine"):
                    northflank_worker.validate_config()

    def test_worker_terminates_cleanly_without_busy_loop(self):
        stop = threading.Event()
        with patch.object(northflank_worker, "validate_config", return_value=None), \
             patch.object(northflank_worker, "poll_once") as poll:
            poll.side_effect = lambda: (stop.set() or {"result": "EMPTY"})
            northflank_worker.run(stop_event=stop, poll_interval=2)
            self.assertEqual(poll.call_count, 1)

    def test_image_does_not_use_vercel_serverless_entrypoint(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text()
        dockerignore = (root / ".dockerignore").read_text()
        self.assertIn("NIT_HOSTED_RUNTIME=1", dockerfile)
        self.assertIn('NIT_ENGINE_ROOT=/app/nit_engine', dockerfile)
        self.assertIn('CMD ["uvicorn", "learningx.api:app"', dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertNotIn('NIT_SERVERLESS_RUNTIME=1', dockerfile)
        for secret_pattern in (".env", "*.pdf", "*.pptx", "private_lessons/"):
            self.assertIn(secret_pattern, dockerignore)


if __name__ == "__main__":
    unittest.main()
