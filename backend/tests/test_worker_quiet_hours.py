import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from learningx import northflank_worker as w


def utc(hour):
    return datetime(2026, 11, 24, hour, 30, tzinfo=timezone.utc)


class QuietHoursTests(unittest.TestCase):
    def check(self, spec, utc_hour, expected, **extra):
        with mock.patch.dict(os.environ, {"NIT_WORKER_QUIET_HOURS": spec, **extra}):
            self.assertEqual(w.quiet_now(utc(utc_hour)), expected)

    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NIT_WORKER_QUIET_HOURS", None)
            self.assertFalse(w.quiet_now(utc(18)))

    def test_window_in_kst(self):
        self.check("0-10", 15, True)    # 00:30 KST
        self.check("0-10", 0, True)     # 09:30 KST
        self.check("0-10", 1, False)    # 10:30 KST

    def test_wrapping_window(self):
        self.check("22-6", 14, True)    # 23:30 KST
        self.check("22-6", 20, True)    # 05:30 KST
        self.check("22-6", 3, False)    # 12:30 KST

    def test_invalid_spec_never_quiet(self):
        for spec in ("abc", "5", "5-5", "0-30"):
            self.check(spec, 15, False)


if __name__ == "__main__":
    unittest.main()
