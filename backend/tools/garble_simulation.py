"""Simulate the ways a PDF's text can come out garbled at the analysis (ingest) stage.

Each case builds a one-page PDF whose page LOOKS like the same Korean sentence but whose
text layer is broken in one known way, runs the real ingest pipeline, and reports what
reaches teachers and students: recovered, held back (quarantine/OCR), or passed through.

    python tools/garble_simulation.py [--out DIR] [--json]
"""
import argparse
import io
import json
import re
import sys
import tempfile
import unicodedata
from contextlib import redirect_stdout
from pathlib import Path

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nit_engine' / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT))
from ingest import ingest  # noqa: E402
from test_glyph_repair import quartz_like_pdf  # noqa: E402

TRUTH = "인지발달이론은 아동이 환경과 상호작용하며 사고의 구조를 스스로 만들어 간다는 관점이다"
ENGLISH = "The efficient fluid flows in a final official office"


CJK_FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'


LATIN_FONT = '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'
SPACED = TRUTH[:24]


def _clean(path, text=TRUTH, fontname='noto', size=12, spaced=False):
    """An embedded Type0 (Identity-H) font with a correct ToUnicode map, like most exports."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if fontname == 'noto':
        page.insert_font(fontname='noto', fontfile=CJK_FONT)
    elif fontname == 'latin':
        page.insert_font(fontname='latin', fontfile=LATIN_FONT)
    if spaced:
        x = 40
        for ch in text:
            page.insert_text((x, 80), ch, fontname=fontname, fontsize=size)
            x += size * 1.7
    else:
        page.insert_text((40, 80), text, fontname=fontname, fontsize=size)
    doc.save(path)


def _type0_font(doc):
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if '/Subtype /Type0' in obj:
            return xref, obj
    raise RuntimeError('no Type0 font')


def _rewrite_tounicode(path, transform, text=TRUTH):
    """Replace a Type0 font's ToUnicode with one built from transform(char) (None: no map at all)."""
    doc = pymupdf.open(path)
    xref, _ = _type0_font(doc)
    if transform is None:
        doc.xref_set_key(xref, 'ToUnicode', 'null')
    else:
        font = pymupdf.Font(fontfile=CJK_FONT)
        pairs = {font.has_glyph(ord(c)): transform(c) for c in set(text)}
        body = ''.join(f'<{gid:04X}> <{"".join(f"{ord(u):04X}" for u in target)}>\n'
                       for gid, target in sorted(pairs.items()) if gid)
        cmap = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Sim def /CMapType 2 def '
                f'1 begincodespacerange <0000> <FFFF> endcodespacerange {len(pairs)} beginbfchar\n{body}'
                'endbfchar endcmap CMapName currentdict /CMap defineresource pop end end')
        tu = doc.get_new_xref()
        doc.update_object(tu, '<<>>')
        doc.update_stream(tu, cmap.encode('ascii'))
        doc.xref_set_key(xref, 'ToUnicode', f'{tu} 0 R')
    data = doc.tobytes()
    doc.close()
    Path(path).write_bytes(data)


def _raw_bytes(path, payload):
    """Text shown with a WinAnsi Type1 font from raw bytes (legacy Korean code page in a Latin font)."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = doc.get_new_xref()
    doc.update_object(font, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    stream = doc.get_new_xref()
    doc.update_object(stream, '<<>>')
    doc.update_stream(stream, b'BT /F1 12 Tf 40 780 Td <' + payload.hex().encode() + b'> Tj ET')
    doc.xref_set_key(page.xref, 'Resources', f'<< /Font << /F1 {font} 0 R >> >>')
    doc.xref_set_key(page.xref, 'Contents', f'{stream} 0 R')
    doc.save(path)


def _image_only(path):
    src = pymupdf.open()
    _clean_page = src.new_page(width=595, height=842)
    _clean_page.insert_font(fontname='noto', fontfile=CJK_FONT)
    _clean_page.insert_text((40, 80), TRUTH, fontname='noto', fontsize=12)
    pix = src[0].get_pixmap(dpi=150)
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
    doc.save(path)


def _shuffled_type3(path):
    """Quartz-like Type3 fonts whose glyph ids do not follow Unicode order."""
    quartz_like_pdf(path)
    doc = pymupdf.open(path)
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref)
        if '/Type /Encoding' in obj:
            names = re.findall(r'/g([0-9A-F]+)', obj)
            scrambled = {n: f'{(int(n, 16) * 7919) % 60000 + 1000:X}' for n in names}
            doc.update_object(xref, re.sub(r'/g([0-9A-F]+)', lambda m: '/g' + scrambled[m.group(1)], obj))
    data = doc.tobytes()
    doc.close()
    Path(path).write_bytes(data)


def _uni_names(path):
    """Type3 Hangul named /uniXXXX (no ToUnicode): readers fall back to the glyph names."""
    quartz_like_pdf(path, tounicode=False)
    doc = pymupdf.open(path)
    for xref in range(1, doc.xref_length()):
        obj = doc.xref_object(xref)
        if '/Type /Encoding' in obj or '/CharProcs' in obj:
            fixed = re.sub(r'/g([0-9A-F]+)', lambda m: f'/uni{int(m.group(1), 16) - 47611 + 0xAC00:04X}', obj)
            doc.update_object(xref, fixed)
    data = doc.tobytes()
    doc.close()
    Path(path).write_bytes(data)


def _encrypted(path):
    _clean(path)
    doc = pymupdf.open(path)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='',
                       permissions=pymupdf.PDF_PERM_PRINT)
    doc.close()
    Path(path).write_bytes(data)


def _ocr_layer(path, text):
    """A scanned page with an invisible OCR text layer (render mode 3), as scanners write it."""
    _image_only(path)
    doc = pymupdf.open(path)
    page = doc[0]
    page.insert_font(fontname='noto', fontfile=CJK_FONT)
    page.insert_text((40, 80), text, fontname='noto', fontsize=12, render_mode=3)
    data = doc.tobytes()
    doc.close()
    Path(path).write_bytes(data)


CASES = [
    ('clean', '정상 PDF (기준선)', lambda p: _clean(p)),
    ('quartz_type3', 'Type3 글꼴 + ToUnicode가 Latin-1을 가리킴 (보고된 사례)', lambda p: quartz_like_pdf(p)),
    ('quartz_type3_no_map', 'Type3 글꼴 + ToUnicode 없음', lambda p: quartz_like_pdf(p, tounicode=False)),
    ('type3_unordered', 'Type3 글꼴 + 글리프 번호가 유니코드 순서가 아님', _shuffled_type3),
    ('cid_no_map', 'CID 글꼴(Identity-H) + ToUnicode 없음', lambda p: (_clean(p), _rewrite_tounicode(p, None))),
    ('cid_pua', 'CID 글꼴 + ToUnicode가 사용자 정의 영역(PUA)을 가리킴',
     lambda p: (_clean(p), _rewrite_tounicode(p, lambda c: chr(0xE000 + ord(c) % 0x1000) if c != ' ' else c))),
    ('cid_replacement', 'CID 글꼴 + ToUnicode가 U+FFFD를 가리킴',
     lambda p: (_clean(p), _rewrite_tounicode(p, lambda c: '�' if c != ' ' else c))),
    ('cid_shifted_hangul', 'CID 글꼴 + ToUnicode가 엉뚱한 한글을 가리킴 (그럴듯한 오답)',
     lambda p: (_clean(p), _rewrite_tounicode(p, lambda c: chr(ord(c) + 3) if '가' <= c <= '힣' else c))),
    ('cid_ascii', 'CID 글꼴 + ToUnicode가 영문 알파벳을 가리킴',
     lambda p: (_clean(p), _rewrite_tounicode(p, lambda c: chr(0x61 + ord(c) % 26) if '가' <= c <= '힣' else c))),
    ('legacy_cp949', '한글 코드 페이지(CP949) 바이트를 Latin 글꼴로 표시', lambda p: _raw_bytes(p, TRUTH.encode('cp949'))),
    ('nfd_jamo', '한글이 자모로 분해되어 추출됨 (NFD)', lambda p: _clean(p, unicodedata.normalize('NFD', TRUTH))),
    ('letter_spaced', '자간이 넓어 음절마다 띄어 추출됨', lambda p: _clean(p, SPACED, spaced=True)),
    ('ligature', '영문 합자(ﬁ, ﬂ)가 그대로 추출됨',
     lambda p: _clean(p, ENGLISH.replace('fi', '\ufb01').replace('fl', '\ufb02'), fontname='latin')),
    ('missing_glyph', '글꼴에 없는 글자(ﬃ)를 써서 빈 문자(U+0000)로 추출됨',
     lambda p: _clean(p, ENGLISH.replace('ffi', '\ufb03'), fontname='latin')),
    ('image_only', '스캔 이미지만 있는 페이지', _image_only),
    ('uni_glyph_names', 'Type3 글꼴 + /uniXXXX 글리프 이름, ToUnicode 없음', _uni_names),
    ('encrypted', '복사 제한(암호화)된 PDF', _encrypted),
    ('scan_ocr_layer_good', '스캔 + 보이지 않는 OCR 글자층 (인식 양호)', lambda p: _ocr_layer(p, TRUTH)),
    ('scan_ocr_layer_bad', '스캔 + 보이지 않는 OCR 글자층 (오인식)',
     lambda p: _ocr_layer(p, 'Ol지발달Ol론은 Of동Ol 환경과 상호작용하며 AF고의 구조를 스스로 만들Of 간다는 관점Ol다')),
]


def _sim(a, b):
    from difflib import SequenceMatcher
    return round(SequenceMatcher(None, a.replace(' ', ''), b.replace(' ', '')).ratio(), 3)


def run(out_dir):
    rows = []
    for key, title, build in CASES:
        work = Path(out_dir) / key
        work.mkdir(parents=True, exist_ok=True)
        pdf = work / 'lesson.pdf'
        build(pdf)
        with redirect_stdout(io.StringIO()):
            ingest(pdf, work / 'out')
        doc = json.loads((work / 'out' / 'document.json').read_text(encoding='utf-8'))
        page = doc['pages'][0]
        text = ' '.join(b['text'] for b in page['blocks'])
        truth = {'ligature': ENGLISH, 'missing_glyph': ENGLISH, 'letter_spaced': SPACED}.get(key, TRUTH)
        eligible = any(b.get('quality', {}).get('eligible_for_coaching') for b in page['blocks'])
        accurate = _sim(text, truth)
        reasons = sorted({r for b in page['blocks'] for r in b.get('quality', {}).get('reasons', [])})
        if eligible and accurate >= 0.98:
            outcome = 'ok'
        elif eligible:
            outcome = 'LEAK'          # wrong text can become coaching evidence
        else:
            outcome = 'held'          # kept away from students, queued for OCR/review
        rows.append({'case': key, 'title': title, 'outcome': outcome, 'accuracy': accurate,
                     'quality_state': page.get('quality_state'), 'reasons': reasons,
                     'ocr_queued': page.get('ocr_state') == 'queued',
                     'recovery': page.get('text_recovery') or [b.get('text_recovery') for b in page['blocks'] if b.get('text_recovery')],
                     'sample': text[:40]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        rows = run(args.out or tmp)
    if args.json:
        sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
        return
    for r in rows:
        print(f"{r['outcome']:5} {r['accuracy']:.3f} {r['quality_state']:22} ocr={str(r['ocr_queued']):5} "
              f"{r['case']:20} {','.join(r['reasons'])[:40]:40} {r['sample']}")


if __name__ == '__main__':
    main()
