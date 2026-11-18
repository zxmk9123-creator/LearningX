"""Deterministic, conservative educational content roles. Unknowns do not bypass gates."""
import re, unicodedata
# Legacy output labels remain supported for existing v1.6.1 clients.
CANONICAL_ROLES=('lesson_content','class_activity_question','class_operation','break','preview','visual_case','review_question','provided_answer','instructor_note')
ROLE_ALIASES={'learning_material':'lesson_content','administrative_appendix':'class_operation',
              'teacher_note':'instructor_note','lecture_cover':'class_operation',
              'course_overview':'class_operation','table_of_contents':'class_operation'}
BLOCKED_ROLES={'review_question','provided_answer','administrative_appendix','teacher_note','course_overview','unknown','table_of_contents','lecture_cover',
               'class_activity_question','class_operation','break','preview','instructor_note','visual_case'}

def canonical_role(role):
    return ROLE_ALIASES.get(role,role) if role else 'unknown'


# Avoid treating routine references to dates or weeks as lecture boundaries.
REVIEW=re.compile(r'생각해\s*볼\s*문제|복습\s*문제|연습\s*문제|정답\s*확인',re.I)
ADMIN=re.compile(r'수업\s*운영|강의\s*소개|교과목\s*정보|출석\s*안내|시험\s*일정|팀\s*프로젝트\s*진행표|팀프로젝트\s*[-–]|오리엔테이션\s*끝난',re.I)
ANSWER_LABEL=re.compile(r'(?:^|[\n\v])\s*(?:정답|해설|모범\s*답안|예시\s*답안|answer\s*key|solution)\s*[:：]',re.I)
ANSWER=re.compile(r'(?<=\v)\s*:\s*[^\v\n]+|(?<=\n)\s*:\s*[^\v\n]+')

def get_text(page):
    return '\n'.join(b.get('raw_text',b.get('text','')) for b in page.get('blocks',[]) if b.get('type')=='text')

# Page-level role detection uses only text/structural attributes, never filenames/pages
# as special-case rule inputs. Ambiguous pages are held as unknown.
BREAK=re.compile(r'(?:\b(?:10|5|15)[- ]?minute\s*break\b|잠시\s*휴식|휴식\s*시간)',re.I)
PREVIEW=re.compile(r'(?:다음\s*(?:시간|강의|주차)\s*(?:에는|예고)|next\s*(?:lecture|week))',re.I)
OPERATION=re.compile(r'(?:조별\s*발표\s*(?:조)?구성|조\s*구성|팀\s*편성|발표\s*조\s*편성|준비되셨나요\s*\??|교과목\s*정보|수업\s*운영)',re.I)
ACTIVITY=re.compile(r'(?:이럴\s*때\s*당신은|당신이\s*느끼기에|어떤\s*사람인가요|어떻게\s*대응하나요|조별\s*토론|함께\s*토론|생각을\s*나누|My\s*Traits\??|나는\s*어떤\s*사람인가)',re.I)
VISUAL_CONCEPT=re.compile(r'(?:네트워크\s*\(|사슬형|수레바퀴형|Y형|원형|개방형|Big\s*5|OCEAN|9가지\s*기질|9가지\s*요인|기질\s*유형|비교표|흐름도|구조도|분포도|점수\s*그래프|특성요인)',re.I)

def visual_concept_required(page,text):
    stats=page.get('visual_stats',{})
    if stats.get('inspection_unavailable'):
        return False  # unknown, NOT proof that no visual exists
    images=stats.get('image_refs',0)+stats.get('images',0)
    chart=stats.get('charts',0)>0
    return bool((images or chart) and VISUAL_CONCEPT.search(text))

def classify_page(page,filename=''):
    txt=get_text(page)
    compact=' '.join(txt.split())
    first=compact[:850]
    slide=page['page'];filename=unicodedata.normalize('NFKC',filename)
    if page.get('hidden_slide'):return 'administrative_appendix'
    if ANSWER_LABEL.search(txt):return 'provided_answer'
    if REVIEW.search(first):return 'review_question'
    if BREAK.search(first):return 'break'
    if PREVIEW.search(first):return 'preview'
    if ADMIN.search(first) or (first.count('월')>=4 and first.count('강의')>=2):return 'administrative_appendix'
    if OPERATION.search(first):return 'class_operation'
    if ACTIVITY.search(first):return 'class_activity_question'
    if re.search(r'^(목차|Table of contents)',first,re.I):return 'table_of_contents'
    if slide==1 and (re.search(r'\b\d+\s*(?:주차|강)\b',first) or '03주차' in filename or '주차' in filename):
        return 'lecture_cover'
    if page.get('visual_stats',{}).get('inspection_unavailable') and VISUAL_CONCEPT.search(txt):
        return 'unknown'
    if visual_concept_required(page,txt):return 'visual_case'
    if not compact:return 'unknown'
    # sparse image/text labels are ambiguous, not educational evidence
    if len(compact)<12 and page.get('visual_stats',{}).get('image_refs',0):return 'unknown'
    return 'learning_material'  # stable legacy alias of lesson_content

def split_review_answer(page):
    """Split answer lines on explicit line-leading colon; review page remains blocked."""
    if page.get('content_role')!='review_question':return
    out=[]
    for b in page['blocks']:
        if b.get('type')!='text':out.append(b);continue
        raw=b.get('raw_text',b.get('text',''))
        matches=list(ANSWER.finditer(raw))
        if not matches:
            out.append({**b,'content_role':'review_question'})
            continue
        offset=0;counter=0
        for match in matches:
            preceding=raw[offset:match.start()].strip()
            if preceding:
                out.append({**b,'id':f'{b["id"]}_q{counter}','text':' '.join(preceding.split()),'raw_text':preceding,
                            'content_role':'review_question'})
            answer=match.group(0).strip()
            if answer:
                out.append({**b,'id':f'{b["id"]}_a{counter}','text':' '.join(answer.split()),'raw_text':answer,
                            'content_role':'provided_answer'})
            offset=match.end();counter+=1
        trailing=raw[offset:].strip()
        if trailing:
            out.append({**b,'id':f'{b["id"]}_q{counter}','text':' '.join(trailing.split()),'raw_text':trailing,
                        'content_role':'review_question'})
    page['blocks']=out

def annotate_roles(pages,filename=''):
    for p in pages:
        p['content_role']=classify_page(p,filename)
        p['canonical_role']=canonical_role(p['content_role'])
        split_review_answer(p)
        for b in p['blocks']:
            b.setdefault('content_role',p['content_role'])
            b['canonical_role']=canonical_role(b['content_role'])
