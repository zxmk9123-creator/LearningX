"""Optional failures preserve native evidence; OCR never grants approval."""
import json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from learningx.deep_analysis import enrich
from learningx import engine_adapter

class DeepAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.out=self.root/'parsed'; self.out.mkdir()
        self.source=self.root/'source.pdf'; self.source.write_bytes(b'source')
        self.engine=self.root/'engine'; (self.engine/'scripts').mkdir(parents=True)
        self.doc={'source':{'sha256':'abc'},'pages':[{'page':1,'blocks':[]}]}
        (self.out/'document.json').write_text(json.dumps(self.doc))
        (self.out/'vision_jobs.jsonl').write_text('{"page":1}\n{"page":2}\n')
        (self.out/'ocr_jobs.jsonl').write_text('')
        env=patch.dict(os.environ,{'NIT_OCR_ENABLED':'0','NIT_VISION_ENABLED':'0'})
        env.start(); self.addCleanup(env.stop)
    def script(self,body):
        (self.engine/'scripts'/'enrich_gemini.py').write_text('import json,sys,time\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index("--out")+1])\n'+body)
    def test_default_never_calls_provider(self):
        with patch('learningx.deep_analysis.subprocess.Popen') as run:
            doc=enrich(self.source,self.out,self.engine)
        run.assert_not_called(); self.assertEqual(doc['vision_execution']['status'],'NOT_RUN')
    def fake_ocr(self, slow_from=None):
        """Stand-in for recover_ocr.py: marks the first --max-pages pages done, or hangs."""
        calls=[]
        test=self
        class Run:
            def __init__(self,command,**kw):
                self.command=command;calls.append(command);self.pid=0
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def wait(self,timeout=None):
                pages=int(self.command[self.command.index('--max-pages')+1])
                if slow_from is not None and len(calls)>=slow_from and timeout is not None:
                    raise __import__('subprocess').TimeoutExpired(self.command,timeout)
                staged=Path(self.command[self.command.index('--out')+1])
                doc=json.loads((staged/'document.json').read_text())
                for page in doc['pages'][:pages]:page['ocr_state']='complete'
                (staged/'document.json').write_text(json.dumps(doc))
                (staged/'ocr_report.json').write_text(json.dumps({'source_sha256':'abc','failed':[]}))
                return 0
            returncode=0
        return Run,calls
    def ten_scanned_pages(self):
        self.doc['pages']=[{'page':n,'blocks':[]} for n in range(1,11)]
        (self.out/'document.json').write_text(json.dumps(self.doc))
        (self.out/'ocr_jobs.jsonl').write_text(''.join(f'{{"page":{n}}}\n' for n in range(1,11)))
    def test_ocr_is_on_by_default_and_reads_korean_in_rounds(self):
        self.ten_scanned_pages()
        Run,calls=self.fake_ocr()
        seen=[]
        original_wait=Run.wait
        def wait(run,timeout=None):
            seen.append(timeout);return original_wait(run,timeout)
        Run.wait=wait
        env={k:v for k,v in os.environ.items() if k!='NIT_OCR_ENABLED'}
        with patch.dict(os.environ,env,clear=True), patch('learningx.deep_analysis.shutil.which',return_value='/usr/bin/tesseract'), \
             patch('learningx.deep_analysis.subprocess.Popen',Run):
            doc=enrich(self.source,self.out,self.engine)
        self.assertEqual(doc['deep_analysis']['ocr']['status'],'COMPLETE')
        self.assertEqual([c[c.index('--max-pages')+1] for c in calls],['8','10'])
        self.assertEqual((calls[0][calls[0].index('--language')+1],calls[0][calls[0].index('--dpi')+1]),('kor+eng','200'))
        self.assertTrue(200<seen[0]<=210,'budget = 60 s + 15 s a page for 10 pages')
    def test_a_slow_worker_keeps_the_pages_it_finished(self):
        self.ten_scanned_pages()
        Run,calls=self.fake_ocr(slow_from=2)
        with patch.dict(os.environ,{'NIT_OCR_ENABLED':'1'}), patch('learningx.deep_analysis.shutil.which',return_value='/usr/bin/tesseract'), \
             patch('learningx.deep_analysis.subprocess.Popen',Run), patch('learningx.deep_analysis.os.killpg'):
            doc=enrich(self.source,self.out,self.engine)
        ocr=doc['deep_analysis']['ocr']
        self.assertEqual((ocr['status'],ocr['completed_jobs'],ocr['remaining_jobs']),('PARTIAL',8,2))
        self.assertEqual(sum(p.get('ocr_state')=='complete' for p in doc['pages']),8)
    def test_ocr_can_be_turned_off(self):
        (self.out/'ocr_jobs.jsonl').write_text('{"page":1}\n')
        with patch('learningx.deep_analysis.subprocess.Popen') as run:
            doc=enrich(self.source,self.out,self.engine)
        run.assert_not_called(); self.assertEqual(doc['deep_analysis']['ocr']['status'],'NOT_RUN')
    def test_missing_provider_is_explicit(self):
        with patch.dict(os.environ,{'NIT_VISION_ENABLED':'1','GEMINI_API_KEY':''}):
            doc=enrich(self.source,self.out,self.engine)
        self.assertEqual(doc['vision_execution']['status'],'UNAVAILABLE')
    def test_cap_reports_partial_without_verification(self):
        self.script('assert sys.argv[sys.argv.index("--max-jobs")+1]=="1"\n(p/"vision_execution_report.json").write_text(json.dumps({"source_sha256":"abc","success":1,"cached":0,"failed":[]}))\n')
        with patch.dict(os.environ,{'NIT_VISION_ENABLED':'1','GEMINI_API_KEY':'test','NIT_VISION_MAX_JOBS':'1'}):
            doc=enrich(self.source,self.out,self.engine)
        self.assertEqual(doc['vision_execution']['status'],'PARTIAL')
        self.assertEqual(doc['vision_execution']['remaining_jobs'],1)
        self.assertFalse(doc['vision_execution']['visual_semantics_verified'])
    def test_timeout_discards_partial_mutations(self):
        self.script('(p/"document.json").write_text("broken")\ntime.sleep(10)\n')
        with patch.dict(os.environ,{'NIT_VISION_ENABLED':'1','GEMINI_API_KEY':'test','NIT_VISION_TIMEOUT_SECONDS':'1'}):
            doc=enrich(self.source,self.out,self.engine)
        self.assertEqual(doc['source'],self.doc['source'])
        self.assertEqual(doc['vision_execution']['reason'],'STAGE_TIMEOUT')
    def test_source_mismatch_is_discarded(self):
        self.script('(p/"vision_execution_report.json").write_text(json.dumps({"source_sha256":"wrong"}))\n')
        with patch.dict(os.environ,{'NIT_VISION_ENABLED':'1','GEMINI_API_KEY':'test'}):
            doc=enrich(self.source,self.out,self.engine)
        self.assertEqual(doc['vision_execution']['reason'],'SOURCE_MISMATCH')
    @unittest.skipUnless(shutil.which('tesseract'),'Local OCR runtime unavailable')
    def test_real_scanned_page_ocr_remains_unsigned(self):
        import fitz
        native=fitz.open(); page=native.new_page()
        page.insert_text((72,120),'Compare evidence and explain the mechanism.',fontsize=20)
        image=page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png')
        scan=fitz.open(); page=scan.new_page(); page.insert_image(page.rect,stream=image)
        scan.save(self.source); scan.close(); native.close()
        shutil.rmtree(self.out); engine_adapter.ingest(self.source,self.out)
        with patch.dict(os.environ,{'NIT_OCR_ENABLED':'1','NIT_OCR_LANGUAGE':'eng'}):
            doc=enrich(self.source,self.out,engine_adapter.root())
        self.assertEqual(doc['deep_analysis']['ocr']['status'],'COMPLETE')
        blocks=[b for p in doc['pages'] for b in p['blocks'] if b.get('origin')=='ocr']
        self.assertTrue(blocks); self.assertIn('evidence',blocks[0]['text'].lower())
        self.assertTrue(all(not b['quality']['eligible_for_coaching'] for b in blocks))
        self.assertTrue(all(not b['quality']['student_usable'] for b in blocks))
