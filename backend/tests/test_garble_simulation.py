"""Every known way a PDF's text layer can come out garbled, through the real ingest.

tools/garble_simulation.py builds one page per case. No case may LEAK (wrong text that
could become coaching evidence); the listed cases must be recovered exactly.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import garble_simulation  # noqa: E402

RECOVERED = {'clean', 'quartz_type3', 'quartz_type3_no_map', 'cid_no_map', 'cid_pua', 'cid_replacement',
             'cid_shifted_hangul', 'cid_ascii', 'legacy_cp949', 'nfd_jamo', 'letter_spaced', 'ligature',
             'uni_glyph_names', 'encrypted'}
HELD = {'type3_unordered', 'missing_glyph', 'image_only', 'scan_ocr_layer_good', 'scan_ocr_layer_bad'}


FONTS = (Path(garble_simulation.CJK_FONT), Path(garble_simulation.LATIN_FONT))


@unittest.skipUnless(all(f.is_file() for f in FONTS), 'simulation needs Noto Sans CJK and Liberation Sans')
class GarbleSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rows = {r['case']: r for r in garble_simulation.run(cls.tmp.name)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_case_is_covered(self):
        self.assertEqual(set(self.rows), RECOVERED | HELD)

    def test_no_garbled_text_can_become_coaching_evidence(self):
        leaks = {k: r['sample'] for k, r in self.rows.items() if r['outcome'] == 'LEAK'}
        self.assertEqual(leaks, {})

    def test_recoverable_cases_come_back_exactly(self):
        for case in sorted(RECOVERED):
            with self.subTest(case=case):
                self.assertEqual((self.rows[case]['outcome'], self.rows[case]['accuracy']), ('ok', 1.0))

    def test_unrecoverable_cases_are_held_for_review(self):
        for case in sorted(HELD):
            with self.subTest(case=case):
                self.assertEqual(self.rows[case]['outcome'], 'held')


if __name__ == '__main__':
    unittest.main()
