#!/usr/bin/env python3
"""Deterministic, network-free PDF/PPTX extraction + selective AI work planning."""
import argparse, hashlib, json, re, sys, time
from collections import Counter
from pathlib import Path

SCHEMA = 'nit_lesson_v1_6_2'
from glyph_repair import VERSION as GLYPH_REPAIR
MAX_FILE_MB = 250


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''):h.update(b)
    return h.hexdigest()


def compact(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def text_blocks_pdf(page):
    blocks=[]
    for n,b in enumerate(page.get_text('blocks', sort=True)):
        if len(b)>6 and b[6]!=0:continue
        t=compact(b[4])
        if not t:continue
        blocks.append({'id':f'b{n:04d}', 'type':'text','text':t,
                       'bbox':[round(float(x),1) for x in b[:4]],'origin':'native', 'source_type':'pdf_text', 'source_page':page.number+1})
    return blocks


def pypdf_fallback_text(path, page_indices):
    """Optional, targeted fallback only. Never treat pypdf output as OCR."""
    if not page_indices: return {}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        return {i:compact(reader.pages[i].extract_text() or '') for i in page_indices}
    except (ImportError, ValueError, OSError, IndexError):
        return {}


def needs_ocr(page, min_chars=35):
    """Low-text PDF pages are OCR candidates; images/diagrams may also need Vision.

    Does not equate native glyph count to actual recognition quality.
    """
    text = ' '.join(b.get('text','') for b in page['blocks'])
    image_refs = page.get('visual_stats',{}).get('image_refs',0)
    if len(text) >= min_chars: return False
    return image_refs > 0 or len(text) == 0


def pdf_pages(path):
    try:
        import fitz
    except ImportError:
        from pypdf import PdfReader
        reader=PdfReader(str(path),strict=False)
        for i,page in enumerate(reader.pages):
            text=compact(page.extract_text() or '')
            blocks=[{'id':'py0000','type':'text','text':text,'bbox':None,'origin':'native_fallback',
                     'source_type':'pdf_text_pypdf','source_page':i+1}] if text else []
            yield {'page':i+1,'blocks':blocks,'visual_stats':{'image_refs':0,'vector_paths':0,
                   'inspection_unavailable':True},'width':float(page.mediabox.width),'height':float(page.mediabox.height)}
        return
    with fitz.open(path) as doc:
        for i,page in enumerate(doc):
            blocks=text_blocks_pdf(page)
            imgs=page.get_images(full=False)
            try:
                info=page.get_image_info()
                area=min(sum(max((v['bbox'][2]-v['bbox'][0])*(v['bbox'][3]-v['bbox'][1]),0)
                             for v in info)/max(float(page.rect.width*page.rect.height),1),1.0)
            except Exception:area=0.0
            vectors=len(page.get_cdrawings()) if hasattr(page,'get_cdrawings') else 0
            yield {'page':i+1,'blocks':blocks,'visual_stats':{'image_refs':len(imgs),'vector_paths':vectors,
                   'image_area_ratio':round(area,4)},
                   'width':round(page.rect.width,1),'height':round(page.rect.height,1)}


def pptx_media_coverage(path):
    """Detect embedded audio/video and external audio links; never transcribe."""
    import zipfile
    from xml.etree import ElementTree
    aext={'.mp3','.m4a','.wav','.aac','.wma','.ogg','.flac','.aif','.aiff'}
    vext={'.mp4','.m4v','.mov','.avi','.wmv','.webm'}
    audio=[];video=[];external_audio=0
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith('ppt/media/') or name.endswith('/'):continue
            ext=Path(name).suffix.lower()
            if ext in aext:audio.append(name)
            elif ext in vext:video.append(name)
        for name in archive.namelist():
            if not name.startswith('ppt/slides/_rels/') or not name.endswith('.rels'):continue
            try:
                root=ElementTree.fromstring(archive.read(name))
                for rel in root:
                    if rel.attrib.get('TargetMode')=='External' and rel.attrib.get('Type','').lower().endswith('/audio'):
                        external_audio+=1
            except (ElementTree.ParseError,ValueError):
                external_audio+=1
    detected=len(audio)+external_audio
    return {'audio_status':'NOT_ANALYZED' if detected else 'NOT_DETECTED',
            'embedded_audio_assets':len(audio),'external_audio_links':external_audio,
            'video_status':'NOT_ANALYZED' if video else 'NOT_DETECTED',
            'embedded_video_assets':len(video),
            'warning':('PPTX narration/audio was not transcribed or considered as evidence.'
                      if detected else 'No embedded audio detected; external recordings are not checked.')}


def pptx_pages(path):
    from pptx import Presentation
    from pptx_layout_resolver import extract_visible,collect_media_control_images
    prs=Presentation(str(path))
    playback_icons=collect_media_control_images(prs)
    for i,slide in enumerate(prs.slides):
        blocks,counts=extract_visible(slide,i+1,prs.slide_width,prs.slide_height,playback_icons)
        notes=''
        try:
            if slide.has_notes_slide:notes=compact(slide.notes_slide.notes_text_frame.text)
        except (AttributeError,ValueError):pass
        yield {'page':i+1,'blocks':blocks,'teacher_note_available':bool(notes),
               'visual_stats':counts,'width':1.0,'height':1.0,
               'slide_layout':slide.slide_layout.name,
               'hidden_slide':slide._element.get('show') in ('0','false')}


def complexity(page, min_chars=70):
    """Conservative *candidate* routing, never a statement of visual understanding."""
    st=page['visual_stats']
    if st.get('inspection_unavailable'):return (0,['visual_inspection_unavailable'])
    texts=' '.join(b.get('text','') for b in page['blocks'])
    count=len(texts)
    images=st.get('image_refs',0)+st.get('images',0)
    area=float(st.get('image_area_ratio',0) or 0)
    score=0;reasons=[]
    if count<35 and images:
        score+=5;reasons.append('image_dominant_low_text')
    elif count<35:
        score+=1;reasons.append('low_text_unverified')
    elif images and count<200:
        score+=3;reasons.append('image_with_limited_explanation')
    elif images:
        score+=1;reasons.append('image_with_explanatory_text')
    if images and area>=.22:
        score+=4;reasons.append('substantial_image_area')
    if page.get('canonical_role')=='visual_case':
        score+=8;reasons.append('visual_concept_requires_verification')
    if st.get('charts',0)>0:
        score+=5;reasons.append('unverified_chart')
    vectors=st.get('vector_paths',0)
    if vectors>35 or st.get('shapes',0)>12:
        score+=3;reasons.append('complex_diagram_candidate')
    elif vectors>=7 and count<550:
        score+=3;reasons.append('vector_diagram_candidate')
    if st.get('tables',0)>0:score+=1;reasons.append('table')
    from content_roles import BLOCKED_ROLES
    if images and page.get('content_role')=='learning_material' and any(k in texts for k in ('그림','사진','신문','광고','기사','도표','포스터','시위','역사','도식','피라미드')):
        score+=2;reasons.append('educational_visual_case')
    return score,reasons

def chunk_pages(pages, max_chars=1600):
    chunks=[]
    for p in pages:
        current=[];length=0; n=0
        for b in p['blocks']:
            from content_roles import BLOCKED_ROLES
            if (p.get('content_role') in BLOCKED_ROLES or b.get('content_role') in BLOCKED_ROLES) and b.get('origin')!='ocr':
                continue
            txt=b.get('text') or (' | '.join(' | '.join(row) for row in b.get('rows',[])))
            if not txt:continue
            if current and length+len(txt)>max_chars:
                n+=1;chunks.append(make_chunk(p,n,current));current=[];length=0
            current.append((b['id'],txt));length+=len(txt)
        if current:n+=1;chunks.append(make_chunk(p,n,current))
    return chunks


def make_chunk(page,n,items):
    return {'chunk_id':f'p{page["page"]:04d}c{n:03d}', 'page':page['page'],
            'block_ids':[x[0] for x in items],'text':'\n'.join(x[1] for x in items),
            'source_verification':'mixed_unverified' if any(b.get('origin')=='ocr' for b in page['blocks'] if b['id'] in [x[0] for x in items]) else 'native_unverified',
            'eligible_for_coaching':all(next(b for b in page['blocks'] if b['id']==bid).get('quality',{}).get('eligible_for_coaching',False) for bid,_ in items)}


def plan_vision(pages,limit=None):
    import math
    if limit is None:limit=max(20,min(120,math.ceil(len(pages)*.35)))
    jobs=[]
    for p in pages:
        score,reasons=complexity(p)
        unknown=p.get('visual_stats',{}).get('inspection_unavailable')
        state='unknown_due_to_runtime' if unknown else ('queued' if score>=3 else 'not_required')
        p['routing']={'vision_priority':score,'reasons':reasons,'vision_state':state,
                      'visual_evidence_required':bool(score>=3) or unknown is not None and bool(unknown),
                      'vision_verified':False,'visual_analysis_executed':False,'vision_execution_status':'NOT_RUN'}
        if score>=3 and not unknown:
            jobs.append({'page':p['page'],'priority':score,'reasons':reasons,
                         'task':'verify_visual_content','status':'pending_unverified'})
    jobs.sort(key=lambda j:(-j['priority'],j['page']))
    if limit is not None:
        for j in jobs[limit:]:pages[j['page']-1]['routing']['vision_state']='deferred_budget'
        jobs=jobs[:limit]
    return jobs


def detect_lessons(pages,source_filename=''):
    from lesson_boundary import detect
    return detect(pages,source_filename)

def sections(pages,group_size=8,source_filename=''):
    """Batch semantics within title-based boundaries; body and notes are excluded."""
    lessons=detect_lessons(pages,source_filename)
    boundaries=sorted(set([1]+[b['start_page'] for b in lessons]+[len(pages)+1]))
    ans=[]
    for z in range(len(boundaries)-1):
        begin,end=boundaries[z],boundaries[z+1]-1
        associated=next((l for l in lessons if l['start_page']==begin),None)
        for start in range(begin,end+1,group_size):
            group=pages[start-1:min(start+group_size-1,end)]
            text='\n'.join(f'[p{p["page"]}] '+ ' '.join(b.get('text','') or ' '.join(' / '.join(row) for row in b.get('rows',[])) for b in p['blocks'] if b.get('quality',{}).get('eligible_for_coaching',False))[:3500] for p in group)
            ans.append({'section_id':f's{len(ans)+1:03d}', 'pages':[p['page'] for p in group],
                'lesson_week_candidate': associated['week'] if associated else None,
                'lesson_boundary_status': associated['status'] if associated else 'unknown',
                'text':text[:22000], 'task':'extract_learning_objectives_concepts_misconception_candidates'})
    return ans

def write_json(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def write_jsonl(path,rows):
    with path.open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')


def ingest(source,out,vision_limit=None,section_size=8,parser_mode='adaptive'):
    source=Path(source).resolve(); out=Path(out).resolve()
    if source.suffix.lower() not in ('.pdf','.pptx'):raise ValueError('Only .pdf/.pptx supported; convert .ppt to .pptx first')
    if not source.is_file():raise FileNotFoundError(source)
    if source.stat().st_size>MAX_FILE_MB*1024*1024:raise ValueError('File exceeds size policy')
    out.mkdir(parents=True,exist_ok=True)
    sha=digest(source); cache_path=out/'document.json'
    if cache_path.exists() and (out/'canonical_ir.json').exists():
        cached=json.loads(cache_path.read_text(encoding='utf-8'))
        if (cached.get('source',{}).get('sha256')==sha and cached.get('schema_version')==SCHEMA
                and cached.get('parser_arbitration',{}).get('comparison_mode') ==
                ('not_applicable_pptx' if source.suffix.lower()=='.pptx' else parser_mode)
                and cached.get('ingest_config',{}).get('section_size') == section_size
                and cached.get('ingest_config',{}).get('vision_limit') == vision_limit
                and cached.get('ingest_config',{}).get('glyph_repair') == GLYPH_REPAIR):
            return {'cache_hit':True,'pages':len(cached['pages']),'out':str(out),
                    'parser_arbitration':cached.get('parser_arbitration',{})}
    t=time.perf_counter()
    # Hangul drawn with Type3 fonts whose ToUnicode is wrong (macOS Quartz output) extracts
    # as Latin-1 noise; parse a repaired copy instead. The original's hash stays the identity.
    glyph_repair={'version':GLYPH_REPAIR,'status':'not_applicable'}
    fallback_pages=[]; arbitration_summary={'comparison_mode':'not_applicable_pptx','pages_compared':0}
    if source.suffix.lower()=='.pdf':
        import tempfile
        from glyph_repair import repair_pdf
        from parser_arbitration import arbitrate
        # The repaired copy lives only while parsing: it is never stored with the material.
        with tempfile.TemporaryDirectory(prefix='nit-repair-') as scratch:
            repaired=Path(scratch)/'source.pdf'
            glyph_repair=repair_pdf(source,repaired)
            parse_source=repaired if glyph_repair['status']=='repaired' else source
            pages=list(pdf_pages(parse_source))
            arb_audit,arbitration_summary=arbitrate(parse_source,pages,parser_mode)
        if glyph_repair['status']=='repaired':
            for p in pages:p['text_recovery']=glyph_repair['version']
        fallback_pages=[x['page'] for x in arb_audit if x['selected_parser']=='pypdf'
                        and x.get('comparison_performed')]
        write_jsonl(out/'parser_arbitration.jsonl',arb_audit)
    else:
        pages=list(pptx_pages(source))

    from content_roles import annotate_roles
    annotate_roles(pages,source.name)

    # Lecturer notes must not be copied into student-facing document.json.
    # Native PPTX reader is invoked only here to collect private notes.
    if source.suffix.lower()=='.pptx':
        from pptx import Presentation
        prs=Presentation(str(source)); private_notes={}
        for idx,sl in enumerate(prs.slides,1):
            try:
                if sl.has_notes_slide:
                    note=compact(sl.notes_slide.notes_text_frame.text)
                    if note: private_notes[str(idx)]=note
            except (AttributeError, ValueError):pass
        private_path=out/'teacher_notes_PRIVATE.json'
        write_json(private_path,{'source_sha256':sha,'access_class':'teacher_only','notes_by_page':private_notes})
        try: private_path.chmod(0o600)
        except OSError:pass
    from quality_gate import annotate_document
    preliminary={'pages':pages}
    annotate_document(preliminary)
    from evidence_gate import enforce
    enforce(preliminary)
    # A parser conflict / failed arbitration must not leak into student evidence.
    for p in pages:
        if p.get('parser_selection',{}).get('coaching_hold'):
            for b in p['blocks']:
                b['quality']['eligible_for_coaching']=False
            p['quality_state']='PARSER_REVIEW_REQUIRED'
    ocr_jobs=[]
    if source.suffix.lower()=='.pdf':
        for pg in pages:
            if needs_ocr(pg) or pg.get('quality_state') in ('QUARANTINED','PARSER_REVIEW_REQUIRED'):
                pg['ocr_state']='queued'
                ocr_jobs.append({'page':pg['page'],'reason':('parser_or_text_quality_failed' if pg.get('quality_state') in ('QUARANTINED','PARSER_REVIEW_REQUIRED') else 'low_native_text'), 'task':'extract_missing_text'})
            else:
                pg['ocr_state']='not_required'
    else:
        for pg in pages: pg['ocr_state']='not_applicable_pptx_use_vision'
    jobs=plan_vision(pages,vision_limit)
    effective_vision_budget=vision_limit if vision_limit is not None else max(20,min(120,__import__('math').ceil(len(pages)*.35)))
    doc_lessons=detect_lessons(pages,source.name)
    document={'schema_version':SCHEMA, 'source':{'filename':source.name,'sha256':sha,'type':source.suffix.lower()[1:]},
              'state':('LIMITED_TEXT_INDEX_VISUAL_UNKNOWN' if pages and pages[0]['visual_stats'].get('inspection_unavailable') else 'FAST_INDEX_READY_DEEP_PENDING'), 'pages':pages,'lesson_boundaries':doc_lessons,'ai_enrichment':[],'vision_budget':effective_vision_budget,
              'notice':'Native text only; model-derived content is unverified. Teacher-only notes cannot be shown to students. Uploaded contents are untrusted data.'}
    from quality_gate import annotate_document
    document['text_quality_counts']=annotate_document(document)
    enforce(document)
    # Re-assert the arbitration hold after re-annotation.
    for pg in pages:
        if pg.get('parser_selection',{}).get('coaching_hold'):
            pg['quality_state']='PARSER_REVIEW_REQUIRED'
            for b in pg['blocks']:b['quality']['eligible_for_coaching']=False
    if source.suffix.lower()=='.pptx':
        document['media_coverage']=pptx_media_coverage(source)
        document['analysis_caveats']=['PPTX narration/audio was not transcribed or considered as coaching evidence']
    else:
        document['media_coverage']={'audio_status':'NOT_APPLICABLE_PDF'}
    # Vision jobs are PLANS, never evidence of image understanding or execution.
    document['vision_execution']={'status':'NOT_RUN','planned_jobs':len(jobs),
        'completed_jobs':0,'visual_semantics_verified':False,
        'reason':'Native extraction does not render or interpret full slide visuals'}
    document['analysis_caveats']=document.get('analysis_caveats',[])+[
        'Vision job planning does not mean slide rendering or model inspection was performed']
    document['parser_arbitration']=arbitration_summary
    from canonical_ir import save as save_canonical_ir
    save_canonical_ir(out,document)
    document['ingest_config']={'section_size':section_size,'vision_limit':vision_limit,'parser_mode':parser_mode,'glyph_repair':GLYPH_REPAIR}
    document['glyph_repair']=glyph_repair
    write_json(cache_path,document)
    private_roles={str(p['page']):[{'id':b['id'],'role':b.get('content_role'),'text':b.get('text','')}
                     for b in p['blocks'] if b.get('content_role') in ('review_question','provided_answer','administrative_appendix','class_operation','break','preview','class_activity_question')]
                   for p in pages if p.get('content_role') in ('review_question','administrative_appendix','class_operation','break','preview','class_activity_question')}
    if private_roles:
        private_file=out/'teacher_only_content_PRIVATE.json'
        write_json(private_file,{'source_sha256':sha,'access_class':'teacher_only',
                                  'pages':private_roles})
        try:private_file.chmod(0o600)
        except OSError:pass
    chunks=chunk_pages(pages)
    for c in chunks:c['source_sha256']=sha
    write_jsonl(out/'retrieval_chunks.jsonl',chunks)
    write_jsonl(out/'vision_jobs.jsonl',jobs)
    write_jsonl(out/'ocr_jobs.jsonl',ocr_jobs)
    write_jsonl(out/'semantic_jobs.jsonl',sections(pages,section_size,source.name))
    manifest={'schema_version':SCHEMA,'runtime_engine':('pypdf' if pages and pages[0]['visual_stats'].get('inspection_unavailable') else ('pymupdf' if source.suffix.lower()=='.pdf' else 'python-pptx')),
              'verified_executed':True,'lesson_boundaries':doc_lessons,'sha256':sha,'pages':len(pages),'chunks':len(chunks),'vision_jobs':len(jobs),'vision_budget':effective_vision_budget,'ocr_jobs':len(ocr_jobs),'pypdf_recovered_pages':fallback_pages,'parser_arbitration':arbitration_summary,
              'ingest_config':{'section_size':section_size,'vision_limit':vision_limit,'parser_mode':parser_mode,'glyph_repair':GLYPH_REPAIR},'glyph_repair':glyph_repair,
              'semantic_jobs':len(sections(pages,section_size,source.name)),
              'native_seconds':round(time.perf_counter()-t,3),
              'state':('LIMITED_TEXT_INDEX_VISUAL_UNKNOWN' if pages and pages[0]['visual_stats'].get('inspection_unavailable') else 'FAST_INDEX_READY_DEEP_PENDING'),'visual_inspection_status':('unknown' if pages and pages[0]['visual_stats'].get('inspection_unavailable') else 'inspected_routing_only'), 'not_done':['AI understanding','OCR/vision for flagged pages','embedding indexing','human QA']}
    manifest['vision_execution']=document['vision_execution']
    manifest['native_parser_executed']=True
    manifest['media_coverage']=document.get('media_coverage',{})
    if source.suffix.lower()=='.pptx':
        manifest['not_done'].append('PPTX narration/audio transcription')
    write_json(out/'manifest.json',manifest)
    from quality_gate import audit
    from recovery_plan import plan
    audit(out);plan(out)
    from parser_audit import audit as parser_audit
    parser_audit(out)
    return {'cache_hit':False,**manifest,'out':str(out)}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('file');ap.add_argument('--out',required=True)
    ap.add_argument('--vision-limit',type=int);ap.add_argument('--section-size',type=int,default=8)
    ap.add_argument('--parser-mode',choices=['adaptive','full','primary-only'],default='adaptive')
    a=ap.parse_args()
    if a.section_size<1 or a.vision_limit is not None and a.vision_limit<0:ap.error('invalid parameters')
    try:print(json.dumps(ingest(a.file,a.out,a.vision_limit,a.section_size,a.parser_mode),ensure_ascii=False,indent=2))
    except Exception as e:print(str(e),file=sys.stderr);sys.exit(1)
