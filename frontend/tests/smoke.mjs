import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import vm from 'node:vm';
const root=path.resolve(import.meta.dirname,'..');
const pages=['index.html','intro.html','teacher.html','student.html','why.html','cards.html','flow.html','levels.html','principles.html','app.html'];
for(const page of pages){
 const html=fs.readFileSync(path.join(root,page),'utf8');
 assert.match(html,/<html lang="ko">/);
 assert.match(html,/<main\b/);
 for(const match of html.matchAll(/(?:href|src)="([^"]+)"/g)){
  const link=match[1];
  if(link.startsWith('http')||link.startsWith('#')||link.startsWith('data:'))continue;
  if(link.startsWith('mailto:'))continue;
  const clean=link.split(/[?#]/)[0];
  assert.ok(fs.existsSync(path.join(root,clean)),`${page} references missing ${clean}`);
 }
}
const app=fs.readFileSync(path.join(root,'app.html'),'utf8');
for(const id of ['demo-mode','api-mode','connect-form','student-work','faculty-work','ask-form','revise-form','course-form','upload-form','candidate-list','policy-form','enroll-form'])assert.match(app,new RegExp(`id="${id}"`));
const js=fs.readFileSync(path.join(root,'assets/pilot.js'),'utf8');
assert.doesNotMatch(js,/localStorage|sessionStorage|document\.cookie/);
assert.match(js,/credentials:'omit'/);
assert.match(js,/NIT|LearningX|교수자/);
assert.match(js,/evidence_refs/);
assert.match(js,/\/v1\/courses\/mine/);
assert.match(js,/\/revisions/);
const cfg=JSON.parse(fs.readFileSync(path.join(root,'vercel.json'),'utf8'));
assert.strictEqual(cfg.framework,null,'Static frontend must explicitly select Vercel Other preset');
assert.ok(cfg.headers[0].headers.some(h=>h.key==='Content-Security-Policy'));
// Question-up redesign: entry, teacher and student flows.
const entry=fs.readFileSync(path.join(root,'index.html'),'utf8');
for(const id of ['join-code'])assert.match(entry,new RegExp(`id="${id}"`));
assert.match(entry,/teacher\.html/);
const teacher=fs.readFileSync(path.join(root,'teacher.html'),'utf8');
for(const id of ['google-signin','course-list','upload-form','candidate-list','ai-consent','approve-start','board','level-filter','projector','export-csv'])assert.match(teacher,new RegExp(`id="${id}"`));
const student=fs.readFileSync(path.join(root,'student.html'),'utf8');
for(const id of ['join-form','thread','composer','send-now','screen-sent','mine-list'])assert.match(student,new RegExp(`id="${id}"`));
for(const html of [entry,teacher,student]){
 assert.doesNotMatch(html,/\sstyle=/,'CSP forbids inline style attributes');
 assert.doesNotMatch(html,/<script>(?!<\/script>)|<script(?![^>]*\bsrc=)[^>]*>/,'CSP forbids inline scripts');
}
for(const file of ['qu-api.js','qu-entry.js','qu-teacher.js','qu-student.js','qu-plant.js']){
 const src=fs.readFileSync(path.join(root,'assets',file),'utf8');
 assert.doesNotMatch(src,/localStorage|sessionStorage|document\.cookie|indexedDB/,`${file} must keep tokens in memory only`);
 assert.doesNotMatch(src,/innerHTML|insertAdjacentHTML|eval\(|new Function/,`${file} must not inject HTML or code`);
 assert.doesNotMatch(src,/setAttribute\(\s*['"]style/,`${file} must not set style attributes (CSP)`);
}
const quApi=fs.readFileSync(path.join(root,'assets/qu-api.js'),'utf8');
assert.match(quApi,/credentials: 'omit'/);
const quTeacher=fs.readFileSync(path.join(root,'assets/qu-teacher.js'),'utf8');
for(const ep of ['/v1/faculty/auth/google','/uploads/initiate','/candidates','/approve','/policy','/sessions','/board','/project','/export'])assert.ok(quTeacher.includes(ep),`teacher flow calls ${ep}`);
const quStudent=fs.readFileSync(path.join(root,'assets/qu-student.js'),'utf8');
for(const ep of ['/v1/join','/questions','/revisions','/submit','/mine'])assert.ok(quStudent.includes(ep),`student flow calls ${ep}`);
console.log(`OK: ${pages.length} linked HTML pages, app + Question-up contract hooks, token hygiene, CSP hygiene, Vercel headers`);

// Exercise the actual request client without network: a shared link must never
// redirect the faculty invite code or bearer token to another API origin.
for (const [hostname, query, expected] of [
 ['learning-x-one.vercel.app','https://outside.example','https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run'],
 ['learning-x-one.vercel.app','http://localhost:8000','https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run'],
 ['localhost','http://localhost:8000','http://localhost:8000'],
 ['localhost','http://localhost.evil.example:8000','https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run'],
 ['localhost','https://user:password@localhost:8000','https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run'],
 ['learning-x-one.vercel.app','https://outside.example/path','https://p01--learningx-shared-api--6j4w9s7dpfzw.code.run'],
]) {
 const calls=[];
 const ctx=vm.createContext({window:{},location:{hostname,search:'?api='+encodeURIComponent(query)},URL,URLSearchParams,AbortController,setTimeout,clearTimeout,
  fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({ok:true})};}});
 vm.runInContext(quApi,ctx);
 await ctx.window.QU.client(()=> 'test-only-token')('/v1/faculty/auth/google',{method:'POST',body:{code:'test-only-code'}});
 assert.equal(calls[0].url,expected+'/v1/faculty/auth/google');
 assert.equal(calls[0].options.headers.Authorization,'Bearer test-only-token');
}
console.log('OK: API origin confinement protects invite codes and tokens');
