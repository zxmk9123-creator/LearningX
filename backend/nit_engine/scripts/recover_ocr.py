#!/usr/bin/env python3
"""Selective OCR worker. Optional; no network calls. OCR artifacts remain unverified.

Uses local Tesseract via PyMuPDF, or RapidOCR if installed. Each page is atomic;
cache keyed by source SHA + page + engine/params. Source integrity is checked.
"""
import argparse, json, subprocess, shutil, time
from pathlib import Path
from ingest import digest, compact, write_json
from reindex import reindex


def run_tesseract(page, language, dpi):
    # PyMuPDF OCR engine reads Tesseract traineddata; local binary/data required.
    tp=page.get_textpage_ocr(flags=3, language=language, dpi=dpi, full=True)
    return compact(page.get_text('text',textpage=tp))


def run_rapidocr(page,dpi):
    try: from rapidocr import RapidOCR
    except ImportError as e: raise RuntimeError('rapidocr not installed; pip install rapidocr onnxruntime') from e
    import numpy as np
    engine=RapidOCR()
    pix=page.get_pixmap(matrix=__import__('fitz').Matrix(dpi/72,dpi/72), alpha=False)
    rgb=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
    result=engine(rgb)
    # RapidOCR return type differs by version: support RapidOCROutput and legacy tuple.
    if hasattr(result,'txts'): return compact(' '.join(result.txts or []))
    if isinstance(result,tuple) and result:
        return compact(' '.join(row[1] for row in (result[0] or []) if len(row)>1))
    raise RuntimeError('Unsupported RapidOCR output format; pin and verify API compatibility')


def recover(source,out,engine='tesseract',language='kor+eng',dpi=160,max_pages=None,force=False, auto_reindex=True):
    source=Path(source).resolve();out=Path(out).resolve()
    docpath=out/'document.json';doc=json.loads(docpath.read_text(encoding='utf-8'))
    if source.suffix.lower()!='.pdf':raise ValueError('OCR stage supports PDF only; PPTX embedded images use Vision jobs')
    sha=digest(source)
    if doc['source']['sha256']!=sha:raise ValueError('source SHA mismatch; re-ingest before OCR')
    import fitz
    cache=out/'ocr_cache';cache.mkdir(exist_ok=True)
    jobs=[json.loads(l) for l in (out/'ocr_jobs.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    if max_pages is not None: jobs=jobs[:max_pages]
    report={'processed':0,'cached':0,'empty':0,'failed':[], 'source_sha256':sha,'engine':engine,'language':language}
    # Preserve successfully extracted native text; OCR is supplementary evidence.
    with fitz.open(source) as pdf:
        for job in jobs:
            n=job['page'];entry=doc['pages'][n-1]
            key=f'{sha[:16]}-p{n:04d}-{engine}-{language.replace("+","_")}-{dpi}.json'
            dst=cache/key
            try:
                if dst.exists() and not force:
                    result=json.loads(dst.read_text(encoding='utf-8'));report['cached']+=1
                else:
                    page=pdf[n-1]
                    txt=run_tesseract(page,language,dpi) if engine=='tesseract' else run_rapidocr(page,dpi)
                    result={'page':n,'source_sha256':sha,'text':txt,'engine':engine,'language':language,'dpi':dpi,
                            'verification':'unverified_ocr'}
                    tmp=dst.with_suffix('.tmp');write_json(tmp,result);tmp.replace(dst)
                    report['processed']+=1
                if result['source_sha256']!=sha:raise ValueError('OCR cache SHA mismatch')
                txt=result['text']
                if not txt:
                    entry['ocr_state']='empty';report['empty']+=1;continue
                entry['blocks']=[b for b in entry['blocks'] if b.get('origin')!='ocr']
                entry['blocks'].append({'id':f'ocr{n:04d}','type':'text','text':txt,'bbox':None,
                    'origin':'ocr','source_type':'pdf_ocr','source_page':n,
                    'verification':'unverified_ocr','ocr_engine':engine})
                entry['ocr_state']='complete'
            except Exception as e:
                entry['ocr_state']='failed';report['failed'].append({'page':n,'error':type(e).__name__})
    # Atomic JSON write. Rebuilding derived indexes is necessary after evidence changes.
    temp=docpath.with_suffix('.tmp');write_json(temp,doc);temp.replace(docpath)
    if auto_reindex:report['indexes']=reindex(out)
    write_json(out/'ocr_report.json',report)
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('file');ap.add_argument('--out',required=True)
    ap.add_argument('--engine',choices=['tesseract','rapidocr'],default='tesseract')
    ap.add_argument('--language',default='kor+eng');ap.add_argument('--dpi',type=int,default=160)
    ap.add_argument('--max-pages',type=int);ap.add_argument('--force',action='store_true')
    args=ap.parse_args()
    if args.dpi<72 or args.dpi>400 or args.max_pages is not None and args.max_pages<0:ap.error('invalid dpi/pages')
    print(json.dumps(recover(args.file,args.out,args.engine,args.language,args.dpi,args.max_pages,args.force),ensure_ascii=False,indent=2))
