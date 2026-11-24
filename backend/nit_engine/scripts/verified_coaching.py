#!/usr/bin/env python3
"""Authenticated server-bound evidence approval and deterministic coaching gate.
Signature ensures a student cannot edit review JSON to self-approve. The host
service MUST authenticate reviewer and securely manage the secret.
"""
import argparse,hashlib,hmac,json,os,datetime,re,unicodedata
from pathlib import Path
from policy import authorize
# Shown when no model can coach (guest classes, no model configured): plain words a
# student can answer without knowing the lesson's vocabulary.
STEMS={'clarify':'이 개념이 어디에 쓰이는지와 왜 중요한지 중 어느 쪽이 더 궁금한가요?',
'apply':'이 개념을 내 주변의 어떤 예에 써 볼 수 있을까요?',
'analyze':'이런 일이 왜 생기는지와 무엇과 다른지 중 어느 쪽이 더 궁금한가요?',
'evaluate':'어떤 기준으로 보면 더 낫다고 말할 수 있을까요?',
'create':'조건 하나를 바꾸면 어떤 새로운 방법을 생각할 수 있을까요?'}

def canonical(obj):return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')
def block_text(b):return b.get('text') or ' / '.join(' | '.join(map(str,r)) for r in b.get('rows',[]))
def block_digest(page,block):
    return hashlib.sha256(canonical({'page':page,'block_id':block['id'],'text':block_text(block),'origin':block.get('origin'),'source_type':block.get('source_type'),'type':block.get('type')})).hexdigest()
def load_blocks(out):
    doc=json.loads((Path(out)/'document.json').read_text(encoding='utf-8'))
    return doc,{(p['page'],b['id']):b for p in doc['pages'] for b in p['blocks']}

def approve(out,review,authenticated_reviewer_id,signing_secret):
    """Only server code after successful reviewer authentication should call."""
    if not authenticated_reviewer_id or len(signing_secret)<32:raise PermissionError('Authenticated reviewer and strong signing secret required')
    doc,blocks=load_blocks(out)
    pages={p['page']:p for p in doc['pages']}
    if review.get('source_sha256')!=doc['source']['sha256']:raise ValueError('Source SHA mismatch')
    if review.get('reviewer_id')!=authenticated_reviewer_id:raise PermissionError('Review identity mismatch')
    approvals=[]
    for item in review.get('items',[]):
        if item.get('decision')!='approve':continue
        key=(item.get('page'),item.get('block_id'))
        b=blocks.get(key)
        if not b:raise ValueError('Review references non-existent block')
        if b.get('origin')=='ocr' or b.get('verification') in ('unverified_ocr','unverified_ai_interpretation'):
            raise ValueError('Cannot approve OCR/AI block without separate verified source correction workflow')
        from evidence_gate import is_block_candidate
        if (not is_block_candidate(pages[item['page']],b,allow_legacy_missing=not doc.get('schema_version'))
                or b.get('quality',{}).get('status') not in ('NATIVE_UNREVIEWED',)
                or not b.get('quality',{}).get('eligible_for_coaching',False)
                or pages[item['page']].get('parser_selection',{}).get('coaching_hold')):
            raise ValueError('Quality or parser arbitration gate failed or missing')
        actual=block_digest(item['page'],b)
        if actual!=item.get('block_sha256'):raise ValueError('Block text changed since review')
        approvals.append({'page':item['page'],'block_id':item['block_id'],'block_sha256':actual})
    record={'schema_version':'nit_approved_evidence_v1_5','source_sha256':doc['source']['sha256'],
      'reviewer_id':authenticated_reviewer_id,'approved':approvals,'approved_count':len(approvals),
      'approval_kind':'authenticated_faculty_source_review'}
    record['signature']=hmac.new(signing_secret.encode('utf-8'),canonical(record),hashlib.sha256).hexdigest()
    path=Path(out)/'approved_evidence.json';path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'approved':len(approvals),'path':str(path)}

def verify(out,signing_secret):
    path=Path(out)/'approved_evidence.json'
    if not path.exists():raise PermissionError('No faculty-approved evidence')
    rec=json.loads(path.read_text(encoding='utf-8'));sig=rec.pop('signature',None)
    if not sig or not hmac.compare_digest(sig,hmac.new(signing_secret.encode('utf-8'),canonical(rec),hashlib.sha256).hexdigest()):
        raise PermissionError('Review signature invalid')
    doc,blocks=load_blocks(out)
    pages={p['page']:p for p in doc['pages']}
    if rec['source_sha256']!=doc['source']['sha256']:raise PermissionError('Stale approval source')
    verified=[]
    for a in rec['approved']:
        block=blocks.get((a['page'],a['block_id']))
        if not block or block_digest(a['page'],block)!=a['block_sha256']:
            raise PermissionError('Evidence modified since faculty review')
        from evidence_gate import is_block_candidate
        if (not is_block_candidate(pages[a['page']],block,allow_legacy_missing=not doc.get('schema_version'))
                or block.get('origin') not in ('native','native_fallback')
                or block.get('quality',{}).get('status')!='NATIVE_UNREVIEWED'
                or not block.get('quality',{}).get('eligible_for_coaching',False)
                or pages[a['page']].get('parser_selection',{}).get('coaching_hold')):
            raise PermissionError('Quality or parser arbitration state changed')
        verified.append({'page':a['page'],'block_id':a['block_id'],'text':block_text(block),'source_sha256':rec['source_sha256']})
    return verified

def select_relevant(question,evidence,limit=5):
    def terms(s):
        return set(t.lower() for t in re.findall(r'[가-힣A-Za-z0-9]{2,}',unicodedata.normalize('NFKC',s)) if len(t)>=2)
    query=terms(question)
    ranks=[]
    for e in evidence:
        match=query & terms(e['text'])
        if match:ranks.append((len(match),e['page'],e))
    return [x[-1] for x in sorted(ranks,key=lambda x:(-x[0],x[1]))[:limit]]

def coach(out,question,policy,teacher_approved,signing_secret,authenticated_student_id=None):
    if not authenticated_student_id:raise PermissionError('Student authentication required')
    if not authorize(policy,'question_coach','student',teacher_approved):raise PermissionError('AI course policy denies coaching')
    if not question or len(question)>2000:raise ValueError('Question length out of bounds')
    evidence=verify(out,signing_secret)
    if not evidence:raise PermissionError('No faculty-verified evidence available; fail closed')
    relevant=select_relevant(question,evidence)
    return {'status':'verified_evidence_available' if relevant else 'no_matching_verified_evidence','student_question':question,'student_id':authenticated_student_id,
      'evidence':relevant,'generic_scaffolds':STEMS,'student_must_write_revision':True,
      'note':'Lexical verified-evidence retrieval only. No model-derived factual response without matching approved evidence; not dynamic question-depth classification'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=('verify',));p.add_argument('--out',required=True)
    a=p.parse_args();secret=os.environ.get('NIT_REVIEW_SIGNING_KEY','')
    if len(secret)<32:raise SystemExit('NIT_REVIEW_SIGNING_KEY >=32 chars is required')
    print(json.dumps({'verified_count':len(verify(a.out,secret))},ensure_ascii=False))
