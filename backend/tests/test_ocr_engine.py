"""Batched, single-threaded Tesseract OCR reads Korean pages and skips blank ones."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import pymupdf

from learningx import engine_adapter as nit

nit._modules()
from recover_ocr import recover, run_tesseract_batch  # noqa: E402

CJK = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')


def scanned(path, lines, blank_pages=0):
    """Pages that carry only a picture of the text, as a scanner produces them."""
    draft = pymupdf.open()
    for line in lines:
        page = draft.new_page(width=595, height=842)
        page.insert_font(fontname='noto', fontfile=str(CJK))
        page.insert_text((60, 120), line, fontname='noto', fontsize=20)
    for _ in range(blank_pages):
        draft.new_page(width=595, height=842)
    doc = pymupdf.open()
    for page in draft:
        target = doc.new_page(width=595, height=842)
        target.insert_image(target.rect, pixmap=page.get_pixmap(dpi=150))
    doc.save(path)


@unittest.skipUnless(shutil.which('tesseract') and CJK.is_file(), 'needs Tesseract and Noto Sans CJK')
class OcrEngineTests(unittest.TestCase):
    def test_one_process_reads_several_korean_pages_and_skips_blank_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'scan.pdf'
            scanned(source, ['인지발달 이론', '도덕성 발달 단계'], blank_pages=1)
            with pymupdf.open(source) as pdf:
                texts = run_tesseract_batch(pdf, [1, 2, 3], 'kor+eng', 200)
        self.assertIn('인지', texts[1])
        self.assertIn('도덕', texts[2])
        self.assertEqual(texts[3], '')

    def test_recover_marks_every_scanned_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scanned(root / 'source.pdf', ['인지발달 이론', '도덕성 발달 단계', '구체적 조작기'])
            nit.ingest(root / 'source.pdf', root / 'parsed')
            report = recover(root / 'source.pdf', root / 'parsed', auto_reindex=False)
            doc = json.loads((root / 'parsed' / 'document.json').read_text(encoding='utf-8'))
        self.assertEqual((report['engine'], report['processed'], report['failed']), ('tesseract-cli', 3, []))
        self.assertEqual([p['ocr_state'] for p in doc['pages']], ['complete'] * 3)
        for page in doc['pages']:
            ocr = [b for b in page['blocks'] if b['origin'] == 'ocr']
            self.assertEqual(ocr[0]['verification'], 'unverified_ocr', 'OCR never becomes evidence by itself')


if __name__ == '__main__':
    unittest.main()
