#!/usr/bin/env python3
"""Fail-closed character/script quality signals. Heuristics flag candidates, not proof of corruption."""
import json, re, unicodedata, argparse
from text_normalization import BAD_SCRIPTS, dangerous_characters
from pathlib import Path

# Latin-1 characters that ordinary lecture text uses (units, signs, quotes); the rest of
# U+00A1-U+00FF in bulk is the signature of a font whose ToUnicode map is wrong
# (Korean drawn by Type3 fonts read back as 'Piagetß ÿÉ¼þ').
_LATIN1_ORDINARY=set('°±²³·×©®«»µ¹ºª¶§')
def _everyday(ch):
    try:return len(ch.encode('euc-kr'))==2   # one of the 2,350 KS X 1001 syllables
    except UnicodeEncodeError:return False

def latin1_noise(text):
    return sum(1 for c in text if '\u00a1'<=c<='\u00ff' and c not in _LATIN1_ORDINARY)

def evaluate(text, source_type='native', min_content_chars=12):
    text=str(text or '')
    chars=[c for c in text if c.isalpha()]
    n=len(chars)
    foreign=sum(any(script in unicodedata.name(c,'') for script in BAD_SCRIPTS) for c in chars)
    replacement=text.count('\ufffd') + text.count('\x00')
    other_marks=sum(unicodedata.category(c) in ('Co','Cs') and c in dangerous_characters(text) for c in text)
    ratio=foreign/max(n,1)
    reasons=[]
    if replacement:reasons.append('replacement_or_null_character')
    if other_marks:reasons.append('private_use_characters')
    if n>=6 and (foreign>=4 and ratio>=0.12):reasons.append('unexpected_script_mixture')
    if n>=6 and foreign>=10:reasons.append('many_unexpected_script_chars')
    hangul=[c for c in text if '\uac00'<=c<='\ud7a3']
    if len(hangul)>=12 and sum(not _everyday(c) for c in hangul)>0.15*len(hangul):
        reasons.append('implausible_hangul')  # a text map pointing at the wrong syllables
    noise=latin1_noise(text)
    if noise>=4 and noise/max(n,1)>=0.15:reasons.append('legacy_encoding_mojibake')
    if n<min_content_chars:reasons.append('low_content_text')
    # A Korean lecture can intentionally include foreign scripts; block on high suspicion and review manually.
    quarantine=any(r in reasons for r in ('replacement_or_null_character','private_use_characters','unexpected_script_mixture','many_unexpected_script_chars','legacy_encoding_mojibake','implausible_hangul'))
    status='QUARANTINED' if quarantine else ('LOW_CONTENT_REVIEW' if 'low_content_text' in reasons else 'NATIVE_UNREVIEWED')
    return {'status':status,'eligible_for_coaching':not quarantine and n>=min_content_chars and source_type not in ('ocr','vision'),
            'reasons':reasons,'unexpected_script_ratio':round(ratio,4),'alphabetic_chars':n,'method':'unicode_heuristic_v1; not factual verification'}

def annotate_document(doc):
    counts={}
    for page in doc.get('pages',[]):
        for block in page.get('blocks',[]):
            raw=block.get('text') or ' '.join(' '.join(map(str,row)) for row in block.get('rows',[]))
            q=evaluate(raw,block.get('origin','native'))
            if block.get('type')=='table':
                from evidence_gate import table_valid
                if table_valid(block) and not any(r in q['reasons'] for r in ('unexpected_script_mixture','replacement_or_null_character','private_use_characters')):
                    q['status']='NATIVE_UNREVIEWED';q['eligible_for_coaching']=True
                    q['reasons']=[x for x in q['reasons'] if x!='low_content_text']
                else:q['eligible_for_coaching']=False
            if block.get('type') in ('chart','image','placeholder','diagram') or block.get('requires_visual_confirmation'):
                q['eligible_for_coaching']=False
            block['quality']=q;counts[q['status']]=counts.get(q['status'],0)+1
        statuses=[b['quality']['status'] for b in page.get('blocks',[])]
        page['quality_state']='QUARANTINED' if 'QUARANTINED' in statuses else ('LOW_CONTENT_REVIEW' if not statuses or all(x=='LOW_CONTENT_REVIEW' for x in statuses) else 'NATIVE_UNREVIEWED')
        # Parser conflicts must survive every quality / reindex recomputation.
        if page.get('parser_selection',{}).get('coaching_hold'):
            page['quality_state']='PARSER_REVIEW_REQUIRED'
            for block in page.get('blocks',[]):
                block['quality']['eligible_for_coaching']=False
                block['quality'].setdefault('reasons',[]).append('parser_arbitration_hold')
        # Applying the shared typed/role gate after quality is mandatory.
    from evidence_gate import enforce
    enforce(doc)
    return counts

def audit(out):
    out=Path(out);path=out/'document.json';doc=json.loads(path.read_text(encoding='utf-8'))
    counts=annotate_document(doc)
    if any(p.get('visual_stats',{}).get('inspection_unavailable') for p in doc['pages']):
        for page in doc['pages']:
            page.setdefault('routing',{})['vision_state']='unknown_due_to_runtime'
    path.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    from canonical_ir import save as save_ir
    save_ir(out,doc)
    flagged=[{'page':p['page'],'status':p['quality_state'],'block_ids':[b['id'] for b in p['blocks'] if b['quality']['status']=='QUARANTINED']} for p in doc['pages'] if p['quality_state']!='NATIVE_UNREVIEWED']
    report={'schema_version':'nit_quality_v1_4','source_sha256':doc['source']['sha256'],'counts':counts,'flagged_pages':flagged,'note':'Potential corruption only; no ground truth OCR accuracy inferred'}
    (out/'text_quality_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);args=a.parse_args();print(json.dumps(audit(args.out),ensure_ascii=False,indent=2))
