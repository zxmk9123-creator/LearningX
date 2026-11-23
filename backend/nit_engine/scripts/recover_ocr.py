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


def _tesseract_files(images, td, language, dpi, psm):
    import os
    listing = Path(td)/f'pages-{psm}.txt'
    listing.write_text(''.join(f'{p}\n' for _, p in images), encoding='utf-8')
    env = dict(os.environ, OMP_THREAD_LIMIT='1')
    run = subprocess.run(['tesseract', str(listing), 'stdout', '-l', language, '--dpi', str(dpi), '--psm', psm],
                         capture_output=True, text=True, env=env, check=False)
    if run.returncode != 0:
        raise RuntimeError('tesseract failed')
    parts = run.stdout.split('\f')
    if len(parts) == len(images) + 1 and not parts[-1].strip():
        parts = parts[:-1]
    if len(parts) != len(images):
        raise RuntimeError('tesseract page count mismatch')
    return {n: compact(text) for (n, _), text in zip(images, parts)}


def run_tesseract_batch(pdf, numbers, language, dpi):
    """{page: text} for several pages with ONE Tesseract process (models load once).

    Pages go in as grayscale PNGs and Tesseract runs single-threaded: on a fractional
    vCPU, threads only add switching cost. Blank pages are answered without OCR.
    """
    import fitz, os, tempfile
    texts, images = {}, []
    with tempfile.TemporaryDirectory(prefix='nit-ocr-') as td:
        for n in numbers:
            pix = pdf[n-1].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
            if pix.is_unicolor:
                texts[n] = ''
                del pix
                fitz.TOOLS.store_shrink(100)
                continue
            path = Path(td)/f'p{n:05d}.png'
            pix.save(str(path)); images.append((n, path))
            del pix
            fitz.TOOLS.store_shrink(100)  # drop decoded scan images: the worker has little memory
        # psm 4 (one column of variable-size text) read lecture pages best in our checks;
        # pages it leaves empty (a lone title) get a second pass in sparse-text mode.
        for psm, wanted in (('4', images), ('11', None)):
            if wanted is None:
                wanted = [(n, p) for n, p in images if len(texts.get(n, '')) < 2]
            if not wanted:
                continue
            for n, text in _tesseract_files(wanted, td, language, dpi, psm).items():
                if len(text) >= len(texts.get(n, '')):
                    texts[n] = text
    return texts


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


def recover(source,out,engine='tesseract-cli',language='kor+eng',dpi=200,max_pages=None,force=False, auto_reindex=True, batch=4):
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
        batch_text={}
        if engine=='tesseract-cli':
            todo=[j['page'] for j in jobs
                  if force or not (cache/f'{sha[:16]}-p{j["page"]:04d}-{engine}-{language.replace("+","_")}-{dpi}.json').exists()]
            for i in range(0,len(todo),batch):
                chunk=todo[i:i+batch]
                try:batch_text.update(run_tesseract_batch(pdf,chunk,language,dpi))
                except Exception:
                    pass  # pages of a failed batch are reported once, below
        for job in jobs:
            n=job['page'];entry=doc['pages'][n-1]
            key=f'{sha[:16]}-p{n:04d}-{engine}-{language.replace("+","_")}-{dpi}.json'
            dst=cache/key
            try:
                if dst.exists() and not force:
                    result=json.loads(dst.read_text(encoding='utf-8'));report['cached']+=1
                else:
                    page=pdf[n-1]
                    if engine=='tesseract-cli':
                        if n not in batch_text:raise RuntimeError('batch_failed')
                        txt=batch_text[n]
                    else:
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
    ap.add_argument('--engine',choices=['tesseract-cli','tesseract','rapidocr'],default='tesseract-cli')
    ap.add_argument('--batch',type=int,default=4)
    ap.add_argument('--language',default='kor+eng');ap.add_argument('--dpi',type=int,default=160)
    ap.add_argument('--max-pages',type=int);ap.add_argument('--force',action='store_true')
    args=ap.parse_args()
    if args.dpi<72 or args.dpi>400 or args.max_pages is not None and args.max_pages<0:ap.error('invalid dpi/pages')
    print(json.dumps(recover(args.file,args.out,args.engine,args.language,args.dpi,args.max_pages,args.force,batch=max(1,min(args.batch,16))),ensure_ascii=False,indent=2))
