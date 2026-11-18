#!/usr/bin/env python3
"""Optional Gemini enrichment. Explicit CLI call; does not run on ingest."""
import argparse, json, os, subprocess, tempfile, time, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def read_jobs(p):
    if not p.exists():return []
    return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]

def image_for_page(source,page_no,dpi,render_pdf=None):
    import fitz
    doc=fitz.open(str(render_pdf or source))
    try:
        p=doc.load_page(page_no-1)
        pix=p.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
        return pix.tobytes('png')
    finally:doc.close()

def process_job(job,kind,source,pdf_render,model,context):
    from google import genai
    from google.genai import types
    client=genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    prompt=(ROOT/'prompts'/('vision_extraction.md' if kind=='vision' else 'section_semantics.md')).read_text(encoding='utf-8')
    parts=[prompt,json.dumps(job,ensure_ascii=False)]
    if kind=='vision':
        parts.append(types.Part.from_bytes(data=image_for_page(source,job['page'],110,pdf_render),mime_type='image/png'))
        # Provide native extracted blocks to facilitate source conflict detection.
        page=context['pages'][job['page']-1]
        parts.append(json.dumps({'native_blocks':page['blocks']},ensure_ascii=False)[:20000])
    for attempt in range(3):
        try:
            resp=client.models.generate_content(model=model,contents=parts,
                config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0))
            val=json.loads(resp.text)
            if not isinstance(val,dict):raise ValueError('AI response must be JSON object')
            return {'job':{k:v for k,v in job.items() if k!='text'},'output':val,
                    'status':'unverified_ai_interpretation','model':model}
        except Exception:
            if attempt==2:raise
            time.sleep(2**attempt)

def convert_pptx(source,tempdir):
    import shutil
    exe=shutil.which('libreoffice') or shutil.which('soffice')
    if not exe:raise RuntimeError('PPTX Vision needs LibreOffice/soffice for slide rendering. Semantic-only mode works without it.')
    cmd=[exe,'-env:UserInstallation=file://'+str(Path(tempdir)/'lo-profile'),'--headless','--convert-to','pdf','--outdir',str(tempdir),str(source)]
    subprocess.run(cmd,check=True,capture_output=True,timeout=240)
    target=Path(tempdir)/(source.stem+'.pdf')
    if not target.exists():raise RuntimeError('PPTX render failed')
    return target

def run(args):
    source=Path(args.file).resolve();out=Path(args.out).resolve()
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    from ingest import digest
    if doc['source']['sha256']!=digest(source):raise RuntimeError('File hash does not match ingest manifest')
    if not os.environ.get('GEMINI_API_KEY'):raise RuntimeError('GEMINI_API_KEY is required')
    jobs=read_jobs(out/('vision_jobs.jsonl' if args.kind=='vision' else 'semantic_jobs.jsonl'))[:args.max_jobs]
    results_dir=out/('vision_results' if args.kind=='vision' else 'semantic_results');results_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        render=None
        if args.kind=='vision' and source.suffix.lower()=='.pptx' and jobs:render=convert_pptx(source,td)
        execution={'kind':args.kind,'requested':len(jobs),'success':0,'cached':0,'failed':[], 'model':args.model,
                   'source_sha256':doc['source']['sha256'], 'runtime':'google-genai'}
        def worker(job):
            name=f'p{job["page"]:04d}' if args.kind=='vision' else job['section_id']
            dest=results_dir/(name+'.json')
            if dest.exists() and not args.force:
                old=json.loads(dest.read_text(encoding='utf-8'))
                if old.get('source_sha256')==doc['source']['sha256'] and old.get('model')==args.model:
                    return name,'cached'
            result=process_job(job,args.kind,source,render,args.model,doc)
            result['source_sha256']=doc['source']['sha256']
            tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(dest)
            return name,'done'
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs={pool.submit(worker,j):j for j in jobs}
            for future in as_completed(futs):
                job=futs[future]
                try:
                    name,state=future.result();execution['cached' if state=='cached' else 'success']+=1
                    print(name,state)
                except Exception as e:
                    execution['failed'].append({'job':job.get('page',job.get('section_id')),'error':type(e).__name__})
                    print('ERROR',job.get('page',job.get('section_id')),type(e).__name__)
        path=out/f'{args.kind}_execution_report.json'
        path.write_text(json.dumps(execution,ensure_ascii=False,indent=2),encoding='utf-8')
        return 1 if execution['failed'] else 0

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('file');ap.add_argument('--out',required=True)
    ap.add_argument('--kind',choices=['vision','semantic'],required=True)
    ap.add_argument('--model',default=os.environ.get('NIT_MODEL','gemini-2.5-flash'))
    ap.add_argument('--workers',type=int,default=3);ap.add_argument('--max-jobs',type=int,default=9999)
    ap.add_argument('--force',action='store_true');a=ap.parse_args()
    if a.workers<1 or a.workers>8 or a.max_jobs<0:ap.error('workers 1..8, max-jobs >=0')
    raise SystemExit(run(a))
