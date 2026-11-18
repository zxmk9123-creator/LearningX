#!/usr/bin/env python3
"""Rebuild deterministic indexes after OCR, never changing source file hash."""
import argparse, json
from pathlib import Path
from ingest import chunk_pages, sections, write_jsonl, write_json
def reindex(out, section_size=8):
    out=Path(out)
    doc=json.loads((out/'document.json').read_text(encoding='utf-8'))
    from quality_gate import annotate_document,audit
    annotate_document(doc)
    from evidence_gate import enforce
    enforce(doc)
    from canonical_ir import save as save_ir
    save_ir(out,doc)
    write_json(out/'document.json',doc)
    audit(out)
    chunks=chunk_pages(doc['pages'])
    for c in chunks:c['source_sha256']=doc['source']['sha256']
    write_jsonl(out/'retrieval_chunks.jsonl', chunks)
    write_jsonl(out/'semantic_jobs.jsonl', sections(doc['pages'],section_size,doc.get('source',{}).get('filename','')))
    from build_section_chunks import build as section_build
    from build_coaching_index import build as coaching_build
    sc=section_build(out)
    coaching_path=out/'coaching_index.json'
    if coaching_path.exists():
        existing=json.loads(coaching_path.read_text(encoding='utf-8'))
        coaching_build(out,existing['policy'],existing.get('teacher_approved',False))
    else:coaching_build(out)
    manifest_path=out/'manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    manifest.update({'chunks':len(chunks),'semantic_jobs':len(sections(doc['pages'],section_size,doc.get('source',{}).get('filename',''))),
                     'section_chunks':sc, 'ocr_completed_pages':sum(p.get('ocr_state')=='complete' for p in doc['pages'])})
    write_json(manifest_path,manifest)
    return {'retrieval_chunks':len(chunks),'section_chunks':sc}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);a.add_argument('--section-size',type=int,default=8)
    args=a.parse_args(); print(json.dumps(reindex(args.out,args.section_size),indent=2))
