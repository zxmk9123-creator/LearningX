#!/usr/bin/env python3
"""Page-wise PDF parser quality arbitration (no invented text / no OCR).

Select *one original parser output* per page, retain source and decisions.
Scores are heuristic extraction-quality indicators, not source-truth accuracy.
"""
from __future__ import annotations
import re
import unicodedata
from difflib import SequenceMatcher
from text_normalization import BAD_SCRIPTS, dangerous_characters, clean_compare, char_bag, token_bag, counter_f1, numeric_bag, repair_text


def flatten(page):
    return '\n'.join(b.get('text', '') or ' '.join(' '.join(map(str, r)) for r in b.get('rows', []))
                     for b in page.get('blocks', [])).strip()

def assess(text):
    """Conservative Unicode sanity score. Never infer correctness from length alone."""
    text = str(text or '')
    letters = [c for c in text if c.isalpha()]
    n = len(letters)
    if not n:
        return {'score': 0.0, 'status': 'NO_TEXT', 'alphabetic_chars': 0,
                'hangul_syllables': 0, 'suspicious_chars': 0, 'suspicious_ratio': 0.0,
                'reasons': ['no_alphabetic_text']}
    hangul = sum('\uac00' <= c <= '\ud7a3' for c in letters)
    unexpected = sum(any(s in unicodedata.name(c, '') for s in BAD_SCRIPTS) for c in letters)
    forbidden = len(dangerous_characters(text))
    controls = sum(unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in text)
    single_word_chars = len(re.findall(r'(?<!\w)[A-Za-z\uac00-\ud7a3](?!\w)', text))
    ratio = unexpected / n
    # Coverage bonus deliberately capped: longer corrupted output cannot win automatically.
    score = 40 + 19 * min(n / 60, 1) + 13 * min(hangul / 20, 1)
    score += 10 * min(len(re.findall(r'[\w\uac00-\ud7a3]{2,}', text)) / 12, 1)
    score -= 64 * min(ratio / 0.12, 1)
    score -= 35 if forbidden else 0
    score -= 20 if controls else 0
    if n >= 20 and single_word_chars / n > 0.45:
        score -= 12
    reasons=[]
    if unexpected >= 3 and ratio >= 0.03: reasons.append('unexpected_unicode_scripts')
    if forbidden: reasons.append('replacement_or_private_use')
    if controls: reasons.append('control_characters')
    if n < 12: reasons.append('insufficient_alphabetic_content')
    if n >= 20 and single_word_chars / n > .45: reasons.append('fragmented_text')
    score = round(max(0, min(100, score)), 2)
    status = ('CORRUPT_SUSPECT' if forbidden or controls or (unexpected >= 3 and ratio >= .03)
              else 'LOW_CONTENT' if n < 12
              else 'ACCEPTABLE_CANDIDATE' if score >= 55 else 'REVIEW_REQUIRED')
    return {'score':score, 'status':status, 'alphabetic_chars':n,
            'hangul_syllables':hangul, 'suspicious_chars':unexpected+forbidden+controls,
            'suspicious_ratio':round(ratio,4), 'reasons':reasons}

def similarity(a,b):
    """Agreement across reading-order permutations; numeric and lexical differences still count."""
    a=clean_compare(a);b=clean_compare(b)
    if not a and not b:return 1.0
    if not a or not b:return 0.0
    if a==b:return 1.0
    ca,cb=char_bag(a),char_bag(b)
    ta,tb=token_bag(a),token_bag(b)
    numerics=counter_f1(numeric_bag(a),numeric_bag(b))
    char_overlap=counter_f1(ca,cb)
    token_overlap=counter_f1(ta,tb)
    if char_overlap>=.995 and numerics>=.995:return 1.0
    if token_overlap>=.96 and numerics>=.99:return round(token_overlap,4)
    seq=SequenceMatcher(None,a[:2000],b[:2000],autojunk=False).ratio()
    return round(min(.91, .55*char_overlap+.35*token_overlap+.10*seq)*(.5+.5*numerics),4)

def select(primary_text, secondary_text=None, secondary_available=True, score_gap=9):
    """Return selected engine and audit. Prefer layout-bearing PyMuPDF on near ties.
    A weak or disputed text remains review-only, not automatically coach-eligible.
    """
    a = assess(primary_text)
    b = assess(secondary_text) if secondary_text is not None else None
    overlap = similarity(primary_text, secondary_text) if b is not None else None
    chosen='pymupdf'; reason='primary_only'; review=False
    if b is not None:
        if b['score'] > a['score'] + score_gap:
            chosen='pypdf'; reason='secondary_quality_higher'
        elif a['score'] > b['score'] + score_gap:
            reason='primary_quality_higher'
        else:
            reason='quality_tie_prefers_pymupdf_layout'
            # When two plausible, conflicting outputs have no reliable winner,
            # preserve primary for human review but never silently elevate as evidence.
            if min(a['score'],b['score']) >= 55 and overlap < .35:
                review=True;reason='parser_disagreement_review'
    elif not secondary_available:
        reason='secondary_unavailable'
    chosen_q=a if chosen=='pymupdf' else b
    if chosen_q['status'] != 'ACCEPTABLE_CANDIDATE' or chosen_q['score'] < 55:
        review=True
        reason += ';below_quality_threshold'
    return {'selected_parser':chosen,'selected_score':chosen_q['score'],
            'selected_quality_status':chosen_q['status'], 'reason':reason,
            'requires_review':review,'agreement':overlap,
            'scores':{'pymupdf':a,'pypdf':b}, 'comparison_performed':b is not None}

def should_compare(page_index,total,primary,mode='adaptive'):
    if mode == 'full':return True
    if mode == 'primary-only':return False
    if mode != 'adaptive':raise ValueError('Unknown parser mode')
    q=assess(flatten(primary))
    return (q['status'] != 'ACCEPTABLE_CANDIDATE' or q['score'] < 72 or
            page_index in (0, total-1) or page_index % max(1,total//16) == 0)

def arbitrate(path,pages,mode='adaptive'):
    """Compare PyMuPDF to pypdf using a single PdfReader, only on selected pages.
    Fallback-only cannot compare: explicitly labels all pages unverified.
    """
    if not pages:return [], {'comparison_mode':mode,'pages_compared':0,'pypdf_available':False}
    if pages[0].get('visual_stats',{}).get('inspection_unavailable'):
        audit=[]
        for page in pages:
            quality=assess(flatten(page))
            page['parser_selection']={'selected_parser':'pypdf','selected_score':quality['score'],
                'selected_quality_status':quality['status'],'reason':'pypdf_limited_no_cross_parser',
                'requires_review':quality['status'] != 'ACCEPTABLE_CANDIDATE',
                'comparison_performed':False,'scores':{'pymupdf':None,'pypdf':quality}}
            audit.append({'page':page['page'], **page['parser_selection']})
        return audit, {'comparison_mode':'pypdf_limited','pages_compared':0,'pypdf_available':True,
                       'comparison_unavailable':True}
    reader=None;reader_error=None
    if mode != 'primary-only':
        try:
            from pypdf import PdfReader
            reader=PdfReader(str(path), strict=False)
        except (ImportError, OSError, ValueError) as exc:
            reader_error=type(exc).__name__
    compared=0; fallback_count=0; audit=[]
    for i,page in enumerate(pages):
        first=flatten(page)
        second=None
        attempted=bool(reader is not None and should_compare(i,len(pages),page,mode))
        if attempted:
            try:second=repair_text((reader.pages[i].extract_text() or '').strip())[0];compared+=1
            except Exception as exc:
                reader_error=type(exc).__name__
        record=select(first, second, reader is not None)
        if record['selected_parser']=='pypdf':
            # Preserve the full original extraction from one engine, never text-fuse.
            page['blocks']=[{'id':'py0000','type':'text','text':re.sub(r'\s+',' ',second).strip(),
                'bbox':None,'origin':'native_fallback','source_type':'pdf_text_pypdf',
                'source_page':page['page'],'parser_name':'pypdf'}]
            fallback_count+=1
        else:
            for block in page['blocks']:
                block.setdefault('parser_name','pymupdf')
        page['parser_selection']={k:v for k,v in record.items() if k!='agreement'}
        if record['requires_review']:
            page['parser_selection']['coaching_hold']=True
        audit.append({'page':page['page'],**record,'secondary_attempted':attempted})
    return audit, {'comparison_mode':mode,'pages_compared':compared,
                   'pypdf_available':reader is not None,'pypdf_error':reader_error,
                   'pypdf_selected_pages':fallback_count,'comparison_unavailable':reader is None}
