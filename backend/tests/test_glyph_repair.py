"""Korean text drawn with Type3 fonts whose ToUnicode is wrong (macOS Quartz output).

Regression for a lecture PDF whose text came out as 'Piagetß ÿÉ¼þ, Kohlbergß ÷÷ñ ¼þ÷ø':
every Hangul glyph kept its original glyph id in its name, but the ToUnicode map
pointed at Latin-1 characters. Ingest must recover the Hangul, and when it
cannot, the quality gate must keep the noise away from students.
"""
import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

from learningx import engine_adapter as nit

nit._modules()
import glyph_repair  # noqa: E402
from ingest import ingest  # noqa: E402
from quality_gate import evaluate  # noqa: E402

BASE = 47611  # Hangul '가' glyph id in the font of the reported file
SENTENCE = "인지발달이론은 아동이 환경과 상호작용하며 사고의 구조를 스스로 만들어 간다는 관점이다"


def quartz_like_pdf(path, text=SENTENCE, base=BASE, tounicode=True):
    """A page whose Hangul is drawn by a Type3 font named by glyph id, mapped to Latin-1."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    syllables = sorted({c for c in text if '가' <= c <= '힣'})
    codes = {c: 0x21 + i for i, c in enumerate(syllables)}
    procs = []
    for c in syllables:
        x = doc.get_new_xref()
        doc.update_object(x, '<<>>')
        doc.update_stream(x, b'920 0 0 0 900 900 d1 0 0 m 900 0 l 900 900 l 0 900 l h f')
        procs.append(f'/g{base + ord(c) - 0xAC00:X} {x} 0 R')
    diff = ' '.join(f'/g{base + ord(c) - 0xAC00:X}' for c in syllables)
    enc = doc.get_new_xref()
    doc.update_object(enc, f'<< /Type /Encoding /BaseEncoding /WinAnsiEncoding /Differences [ 33 {diff} ] >>')
    extra = ''
    if tounicode:
        cmap = ''.join(f'<{codes[c]:02X}> <{0xC0 + i:04X}>\n' for i, c in enumerate(syllables))
        tu = doc.get_new_xref()
        doc.update_object(tu, '<<>>')
        doc.update_stream(tu, ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapType 2 def '
                               '/CMapName /Quartz def 1 begincodespacerange <00> <FF> endcodespacerange '
                               f'{len(syllables)} beginbfchar\n{cmap}endbfchar endcmap '
                               'CMapName currentdict /CMap defineresource pop end end').encode())
        extra = f'/ToUnicode {tu} 0 R'
    font = doc.get_new_xref()
    widths = ' '.join('920' for _ in syllables)
    doc.update_object(font, f'<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] '
                            f'/FontMatrix [0.001 0 0 0.001 0 0] /CharProcs << {" ".join(procs)} >> '
                            f'/Encoding {enc} 0 R /FirstChar 33 /LastChar {32 + len(syllables)} '
                            f'/Widths [ {widths} ] /Resources << >> {extra} >>')
    words = []
    for word in text.split(' '):
        words.append('<' + ''.join(f'{codes[c]:02X}' for c in word) + '> Tj')
    body = ' ( ) Tj '.join(words)
    content = f'BT /T3 12 Tf 40 780 Td {body} ET'
    helv = doc.get_new_xref()
    doc.update_object(helv, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
    # Spaces come from a standard font so the words stay apart, as in the reported file.
    content = content.replace('( ) Tj', '/H 12 Tf ( ) Tj /T3 12 Tf')
    stream = doc.get_new_xref()
    doc.update_object(stream, '<<>>')
    doc.update_stream(stream, content.encode())
    doc.xref_set_key(page.xref, 'Resources', f'<< /Font << /T3 {font} 0 R /H {helv} 0 R >> >>')
    doc.xref_set_key(page.xref, 'Contents', f'{stream} 0 R')
    doc.save(path)


class GlyphRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def text(self, path):
        with pymupdf.open(path) as doc:
            return ' '.join(doc[0].get_text().split())

    def test_the_fixture_reproduces_the_reported_noise(self):
        quartz_like_pdf(self.dir / 'bad.pdf')
        text = self.text(self.dir / 'bad.pdf')
        self.assertNotIn('인지', text)
        self.assertTrue(any('À' <= c <= 'ÿ' for c in text), text)
        self.assertEqual(evaluate(text)['status'], 'QUARANTINED')

    def test_hangul_is_recovered_exactly(self):
        quartz_like_pdf(self.dir / 'bad.pdf')
        report = glyph_repair.repair_pdf(self.dir / 'bad.pdf', self.dir / 'fixed.pdf')
        self.assertEqual((report['status'], report['base_glyph']), ('repaired', BASE))
        self.assertGreater(report['frequent_share'] - report['runner_up_share'], glyph_repair.MIN_MARGIN)
        self.assertEqual(self.text(self.dir / 'fixed.pdf'), SENTENCE)

    def test_a_font_without_any_tounicode_is_recovered_too(self):
        quartz_like_pdf(self.dir / 'bad.pdf', tounicode=False)
        self.assertEqual(glyph_repair.repair_pdf(self.dir / 'bad.pdf', self.dir / 'fixed.pdf')['status'], 'repaired')
        self.assertEqual(self.text(self.dir / 'fixed.pdf'), SENTENCE)

    def test_too_few_glyphs_are_left_for_review(self):
        quartz_like_pdf(self.dir / 'bad.pdf', text='인지 발달')
        report = glyph_repair.repair_pdf(self.dir / 'bad.pdf', self.dir / 'fixed.pdf')
        self.assertEqual(report['status'], 'unrepairable')
        self.assertFalse((self.dir / 'fixed.pdf').exists())

    def test_an_ordinary_pdf_is_not_touched(self):
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), '정상적인 한국어 교안 문장입니다', fontname='korea')
        doc.save(self.dir / 'ok.pdf')
        self.assertEqual(glyph_repair.repair_pdf(self.dir / 'ok.pdf', self.dir / 'fixed.pdf')['status'], 'not_needed')

    def test_ingest_parses_the_repaired_text_and_keeps_the_original_identity(self):
        source = self.dir / 'lesson.pdf'
        quartz_like_pdf(source)
        ingest(source, self.dir / 'out')
        doc = json.loads((self.dir / 'out' / 'document.json').read_text(encoding='utf-8'))
        page = doc['pages'][0]
        self.assertEqual(' '.join(b['text'] for b in page['blocks']), SENTENCE)
        self.assertEqual((page['text_recovery'], page['quality_state']), (glyph_repair.VERSION, 'NATIVE_UNREVIEWED'))
        self.assertEqual(doc['glyph_repair']['status'], 'repaired')
        self.assertFalse(any(p.suffix == '.pdf' for p in (self.dir / 'out').rglob('*')),
                         'the repaired copy is never stored with the material')

    def test_unrepairable_noise_is_quarantined_and_queued_for_ocr(self):
        source = self.dir / 'lesson.pdf'
        quartz_like_pdf(source, text='인지 발달 이론 아동 환경 상호')  # too few glyphs to pin the base
        ingest(source, self.dir / 'out')
        doc = json.loads((self.dir / 'out' / 'document.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['glyph_repair']['status'], 'unrepairable')
        page = doc['pages'][0]
        self.assertIn(page['quality_state'], ('QUARANTINED', 'PARSER_REVIEW_REQUIRED'))
        self.assertFalse(any(b['quality']['eligible_for_coaching'] for b in page['blocks']),
                         'noise never becomes coaching evidence')
        self.assertTrue((self.dir / 'out' / 'ocr_jobs.jsonl').read_text().strip())


class Latin1NoiseGateTests(unittest.TestCase):
    def test_noise_is_quarantined_but_real_accents_and_signs_pass(self):
        self.assertIn('legacy_encoding_mojibake', evaluate('Piagetß ÿÉ¼þ, Kohlbergß ÷÷ñ ¼þ÷ø')['reasons'])
        for text in ('Le café de Montréal est très connu à Paris pour son goût.',
                     '실험실 온도는 25°C ± 2°로 유지하고 농도는 3×10² 입니다.'):
            with self.subTest(text=text):
                self.assertNotIn('legacy_encoding_mojibake', evaluate(text)['reasons'])


if __name__ == '__main__':
    unittest.main()
