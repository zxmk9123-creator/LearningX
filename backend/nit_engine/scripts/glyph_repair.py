"""Recover Hangul text from Type3 fonts whose ToUnicode map is wrong.

Seen in PDFs written by macOS Quartz (Keynote/Pages print or export, often then
edited with pdf-lib): a Korean font that cannot be embedded as-is is turned into
many small Type3 fonts. Each glyph keeps its ORIGINAL glyph id in its name
(``/gD553`` = glyph 0xD553), but the ToUnicode map points every code at a
Latin-1 character, so extractors return text like ``Piagetß ÿÉ¼þ``.

In common Korean fonts the 11,172 Hangul syllables sit in Unicode order in one
contiguous glyph run, so ``char = U+AC00 + (gid - base)`` for one unknown
``base``. The base is chosen by trying every value that keeps all glyph ids in
the syllable range and scoring how many decoded syllables are among the 2,350
KS X 1001 syllables everyday Korean uses and how many are among the ~200 most
frequent ones. A wrong base shifts every syllable and drops those shares
sharply; only a clear winner is applied. Otherwise the
file is left untouched and the quality gate quarantines the text for OCR.

The fix rewrites only the ToUnicode maps of the affected fonts in a COPY of the
source; glyph drawing is unchanged, so the page looks the same.
"""
import re

VERSION = 'type3-hangul-v1'
HANGUL_FIRST, HANGUL_COUNT = 0xAC00, 11172
MIN_GLYPHS = 20          # fewer distinct glyphs cannot pin the base reliably
MIN_SHARE = 0.95         # a base counts only if this share of its syllables is KS X 1001
MIN_FREQUENT = 0.3       # best base: at least this share of frequent syllables
MIN_MARGIN = 0.2         # ...and this far ahead of the runner-up
_GLYPH_NAME = re.compile(r'/g([0-9A-Fa-f]{2,6})$')
_UNI_NAME = re.compile(r'/(?:uni|u)([0-9A-Fa-f]{4,6})$')


# About 200 of the most frequent syllables in Korean prose. A wrong base shifts every
# syllable, so almost none of these survive it; this separates bases even for a short text.
FREQUENT = set(
    '이의다는에을를가고하지기서한로리사자인어아나대수도적정시일해게들상성그전으보부소주과있것원'
    '구제니무요라만장생동위연학개국회화신문미비경관실내오우여조물발결현중통교세안분당계가면방체'
    '모진출식간금명산법선용영거우이해행공발표치등러심단차작호변른후음께마같저데말두각번바또설'
    '었했였니까며야요습입할될된되여또른든런떤')


def _common(ch):
    try:
        return len(ch.encode('euc-kr')) == 2
    except UnicodeEncodeError:
        return False


def _differences(doc, font_obj, pattern=_GLYPH_NAME):
    """code -> number from a Type3 font's /Encoding /Differences (glyph names /gHEX or /uniHEX)."""
    m = re.search(r'/Encoding\s+(\d+)\s+0\s+R', font_obj)
    enc = doc.xref_object(int(m.group(1))) if m else font_obj
    diff = re.search(r'/Differences\s*\[(.*?)\]', enc, re.S)
    codes, code = {}, None
    for tok in (diff.group(1).split() if diff else ()):
        if tok.startswith('/'):
            if code is None:
                return {}
            g = pattern.match(tok)
            if g:
                codes[code] = int(g.group(1), 16)
            code += 1
        else:
            code = int(tok)
    return codes


def _unicode_targets(doc, font_obj):
    """Unicode values the font's ToUnicode map points at (empty: no map)."""
    m = re.search(r'/ToUnicode\s+(\d+)\s+0\s+R', font_obj)
    if not m:
        return []
    try:
        cmap = doc.xref_stream(int(m.group(1))).decode('latin-1')
    except Exception:
        return []
    return [int(u, 16) for u in re.findall(r'<[0-9A-Fa-f]+>\s*<([0-9A-Fa-f]{4,8})>', cmap)]


def _named_fonts(doc):
    """Type3 fonts whose glyph names already say the character (/uniD55C) but carry no usable map."""
    fonts = {}
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if '/Subtype /Type3' not in obj:
            continue
        targets = _unicode_targets(doc, obj)
        if targets and not all(u <= 0xFF for u in targets):
            continue
        codes = _differences(doc, obj, _UNI_NAME)
        if codes and all(0x20 <= cp < 0x110000 and not 0xD800 <= cp <= 0xDFFF for cp in codes.values()):
            fonts[xref] = codes
    return fonts


def _suspect_fonts(doc):
    """Type3 fonts with glyph-id names whose ToUnicode is missing or points only at U+0000-U+00FF."""
    fonts = {}
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if '/Subtype /Type3' not in obj:
            continue
        codes = _differences(doc, obj)
        if not codes:
            continue
        targets = _unicode_targets(doc, obj)
        if targets and not all(u <= 0xFF for u in targets):
            continue  # a real map (already Hangul or other text): leave it
        fonts[xref] = codes
    return fonts


def calibrate(glyph_ids):
    """(base, share, runner_up_share) for the best Hangul base, or None when none fits.

    share = part of the distinct glyphs that decode to the frequent syllables; a base is
    considered only when nearly all of its syllables are everyday (KS X 1001) ones.
    """
    gids = sorted(set(glyph_ids))
    if len(gids) < MIN_GLYPHS or gids[-1] - gids[0] >= HANGUL_COUNT:
        return None
    scored = []
    for base in range(gids[-1] - HANGUL_COUNT + 1, gids[0] + 1):
        chars = [chr(HANGUL_FIRST + g - base) for g in gids]
        common = sum(_common(c) for c in chars) / len(chars)
        frequent = sum(c in FREQUENT for c in chars) / len(chars)
        scored.append((frequent if common >= MIN_SHARE else 0.0, base))
    scored.sort(reverse=True)
    best, base = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    return base, round(best, 3), round(runner, 3)


def _cmap(mapping):
    lines = ''.join(f'<{c:02X}> <{u:04X}>\n' for c, u in sorted(mapping.items()))
    return ('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n'
            '/CMapType 2 def\n/CMapName /QURepair def\n1 begincodespacerange\n<00> <FF>\n'
            f'endcodespacerange\n{len(mapping)} beginbfchar\n{lines}endbfchar\n'
            'endcmap\nCMapName currentdict /CMap defineresource pop\nend end\n').encode('ascii')


# ---------- CID (Type0, Identity-H) fonts: rebuild the text map from the embedded font ----------

def _parse_cmap(data):
    """ToUnicode stream -> {code: text} (bfchar and both bfrange forms)."""
    out = {}
    text = data.decode('latin-1')
    hexs = lambda h: ''.join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h), 4)) if len(h) >= 4 else chr(int(h, 16))
    for block in re.findall(r'beginbfchar(.*?)endbfchar', text, re.S):
        for code, dst in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>', block):
            out[int(code, 16)] = hexs(dst) if dst else ''
    for block in re.findall(r'beginbfrange(.*?)endbfrange', text, re.S):
        for lo, hi, rest in re.findall(r'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)', block):
            lo, hi = int(lo, 16), int(hi, 16)
            if rest.startswith('['):
                for i, dst in enumerate(re.findall(r'<([0-9A-Fa-f]+)>', rest)):
                    out[lo + i] = hexs(dst)
            else:
                start = int(rest[1:-1], 16)
                for i in range(min(hi - lo + 1, 65536)):
                    out[lo + i] = chr(start + i) if start + i < 0x110000 else ''
    return out


def _type0_fonts(doc):
    """(font xref, ToUnicode map, embedded font bytes, cid->gid or None) for Identity-H/V fonts."""
    found = []
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref)
        except Exception:
            continue
        if '/Subtype /Type0' not in obj or not re.search(r'/Encoding\s*/Identity-[HV]', obj):
            continue
        m = re.search(r'/DescendantFonts\s*\[\s*(\d+)\s+0\s+R', obj)
        if not m:
            continue
        desc = doc.xref_object(int(m.group(1)))
        fd = re.search(r'/FontDescriptor\s+(\d+)\s+0\s+R', desc)
        if not fd:
            continue
        ff = re.search(r'/FontFile[23]?\s+(\d+)\s+0\s+R', doc.xref_object(int(fd.group(1))))
        if not ff:
            continue
        cid2gid = None
        stream = re.search(r'/CIDToGIDMap\s+(\d+)\s+0\s+R', desc)
        if stream:
            raw = doc.xref_stream(int(stream.group(1)))
            cid2gid = {i // 2: (raw[i] << 8) | raw[i + 1] for i in range(0, len(raw) - 1, 2)}
        tu = re.search(r'/ToUnicode\s+(\d+)\s+0\s+R', obj)
        tomap = _parse_cmap(doc.xref_stream(int(tu.group(1)))) if tu else {}
        found.append((xref, tomap, doc.xref_stream(int(ff.group(1))), cid2gid))
    return found


def _font_reverse(data):
    """gid -> code point from the embedded font's own cmap, or {} when it cannot be read."""
    import pymupdf
    try:
        font = pymupdf.Font(fontbuffer=data)
        reverse = {}
        for cp in font.valid_codepoints():
            gid = font.has_glyph(cp)
            if gid and (gid not in reverse or (0xE000 <= reverse[gid] <= 0xF8FF and not 0xE000 <= cp <= 0xF8FF)):
                reverse[gid] = cp
        return reverse
    except Exception:
        return {}


def _cid_plan(entry):
    """New {cid: text} for a CID font whose ToUnicode disagrees with its own glyph table, else None."""
    _, tomap, data, cid2gid = entry
    reverse = _font_reverse(data)
    if len(reverse) < MIN_GLYPHS:
        return None
    if sum(0xE000 <= cp <= 0xF8FF for cp in reverse.values()) > 0.2 * len(reverse):
        return None  # the font's own table is not trustworthy either
    gid_of = (lambda cid: cid2gid.get(cid)) if cid2gid is not None else (lambda cid: cid)
    cids = list(cid2gid) if cid2gid is not None else list(reverse)
    known = [(cid, chr(reverse[gid_of(cid)])) for cid in cids if gid_of(cid) in reverse]
    if not known:
        return None
    agree = sum(tomap.get(cid) == ch for cid, ch in known) / len(known)
    if agree >= 0.5:
        return None
    plan = dict(tomap)            # keep entries the font cannot speak for (ligatures, spaces)
    plan.update(known)
    return plan, round(agree, 3)


def _cmap_cid(mapping):
    def hex16(text):
        return ''.join(f'{ord(c):04X}' if ord(c) < 0x10000 else
                       f'{0xD800 + ((ord(c) - 0x10000) >> 10):04X}{0xDC00 + ((ord(c) - 0x10000) & 0x3FF):04X}'
                       for c in text)
    items = [(c, t) for c, t in sorted(mapping.items()) if t]
    chunks = []
    for i in range(0, len(items), 100):   # PDF limits a bfchar block to 100 entries
        part = items[i:i + 100]
        chunks.append(f'{len(part)} beginbfchar\n' + ''.join(f'<{c:04X}> <{hex16(t)}>\n' for c, t in part) + 'endbfchar\n')
    return ('/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CMapType 2 def\n'
            '/CMapName /QURepairCID def\n1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n'
            + ''.join(chunks) + 'endcmap\nCMapName currentdict /CMap defineresource pop\nend end\n').encode('ascii')


def _set_tounicode(doc, font_xref, data):
    stream = doc.get_new_xref()
    doc.update_object(stream, '<<>>')
    doc.update_stream(stream, data)
    doc.xref_set_key(font_xref, 'ToUnicode', f'{stream} 0 R')


def repair_pdf(source, target):
    """Write a repaired copy to ``target`` when needed. Returns a report dict.

    Two independent repairs, both only on the text map (the drawing never changes):
    - type3: Quartz-style Type3 Hangul named by glyph id (base calibrated as above)
    - cid: Identity-H fonts whose ToUnicode is missing or disagrees with the embedded
      font's own character table (shifted, PUA, U+FFFD or Latin targets)
    """
    import pymupdf as fitz
    report = {'version': VERSION, 'status': 'not_needed', 'fonts': 0, 'glyphs': 0, 'cid_fonts': 0}
    changed = False
    with fitz.open(source) as doc:
        fonts = _suspect_fonts(doc)
        if fonts:
            gids = [g for codes in fonts.values() for g in codes.values()]
            report.update(fonts=len(fonts), glyphs=len(set(gids)))
            fit = calibrate(gids)
            if not fit:
                report.update(status='unrepairable', reason='glyphs_outside_one_hangul_run_or_too_few')
            else:
                base, share, runner = fit
                report.update(base_glyph=base, frequent_share=share, runner_up_share=runner)
                if share < MIN_FREQUENT or share - runner < MIN_MARGIN:
                    report.update(status='unrepairable', reason='no_clear_hangul_base')
                else:
                    for xref, codes in fonts.items():
                        mapping = {c: HANGUL_FIRST + g - base for c, g in codes.items() if 0 <= c <= 0xFF}
                        _set_tounicode(doc, xref, _cmap(mapping))
                    changed = True
        named = _named_fonts(doc)
        for xref, codes in named.items():
            _set_tounicode(doc, xref, _cmap({c: cp for c, cp in codes.items() if 0 <= c <= 0xFF}))
        if named:
            report['named_fonts'] = len(named)
            changed = True
        agreements = []
        for entry in _type0_fonts(doc):
            planned = _cid_plan(entry)
            if planned:
                plan, agree = planned
                _set_tounicode(doc, entry[0], _cmap_cid(plan))
                agreements.append(agree)
        if agreements:
            report.update(cid_fonts=len(agreements), cid_prior_agreement=min(agreements))
            changed = True
        if changed:
            doc.save(target, garbage=0, deflate=True)
            report['status'] = 'repaired'
        return report
