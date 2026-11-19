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
for(const id of ['guest-start','course-list','upload-form','candidate-list','ai-consent','approve-start','board','level-filter','projector','export-csv'])assert.match(teacher,new RegExp(`id="${id}"`));
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
for(const ep of ['/v1/faculty/guest','/uploads/initiate','/candidates','/approve','/policy','/sessions','/board','/project','/export'])assert.ok(quTeacher.includes(ep),`teacher flow calls ${ep}`);
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
 await ctx.window.QU.client(()=> 'test-only-token')('/v1/beta/faculty/session',{method:'POST',body:{code:'test-only-code'}});
 assert.equal(calls[0].url,expected+'/v1/beta/faculty/session');
 assert.equal(calls[0].options.headers.Authorization,'Bearer test-only-token');
}
console.log('OK: API origin confinement protects invite codes and tokens');

{
 const liveSource = fs.readFileSync(path.join(root,'assets/qu-live.js'),'utf8');
 const stubbed = liveSource.replace("import(pdfBase + 'build/pdf.min.mjs')", 'loadFixtureRenderer()');
 assert.notEqual(stubbed, liveSource);
 function node(tag, attrs = {}, ...children) {
  const fixture = {children:[], classes: (attrs.class || '').split(' '), style:{setProperty(){}},
   clientWidth:640, textContent:attrs.text ?? '',
   append(...nodes){for (const n of nodes.filter(Boolean)) { n.parent = this; this.children.push(n); }},
   replaceChildren(...nodes){this.children=[]; this.append(...nodes);},
   remove(){this.parent.children=this.parent.children.filter(n=>n!==this);},
   querySelector(selector){
    for (const n of this.children) {
     if (n.classes.includes(selector.slice(1))) return n;
     const inner = n.querySelector(selector);
     if (inner) return inner;
    }
    return null;
   }, listeners:new Map(), addEventListener(type, fn){this.listeners.set(type,fn);}, removeEventListener(type){this.listeners.delete(type);}};
  fixture.append(...children);
  return fixture;
 }
 const children = new Map();
 const panel = node();
 panel.querySelector = key => {
  if (key === '[data-live-confused]' || key === '[data-live-ask]') return null;
  if (!children.has(key)) children.set(key,node());
  return children.get(key);
 };
 const response = {arrayBuffer: async () => new ArrayBuffer(10)};
 const error = status => Object.assign(new Error('fixture HTTP '+status),{status});
 const slide = (page, version, after) => ({page, version, after, total:9, pdf:true});
 let latest = slide(1,'v1',2);
 const timeline = [latest];
 const painted = [];
 let confusion;
 const css = fs.readFileSync(path.join(root, 'assets/qu.css'), 'utf8');
 assert.doesNotMatch(css, /:fullscreen[^{}]*\.live-marks[^{}]*\{[^{}]*display:\s*none/);
 let releaseOld;
 let failedLatest = false;
 let releaseStalled;
 const stalled = new Promise(resolve => { releaseStalled = resolve; });
 const oldPrefetch = new Promise(resolve => { releaseOld = resolve; });
 const calls = [];
 let lastRendered = null;
 const content = panel.querySelector('[data-live-content]');
 const replace = content.replaceChildren.bind(content);
 content.replaceChildren = (...nodes) => {
  replace(...nodes);
  if (nodes.length) {
   lastRendered = panel.querySelector('[data-live-page]').textContent;
   painted.push(lastRendered);
  }
 };
 let clock = 0;
 const api = async path => {
  const url = new URL(path,'https://fixture.local');
  if (!url.pathname.includes('/page/')) {
   await new Promise(resolve => setTimeout(resolve,8));
   const cursor = timeline.findIndex(item=>item.version===url.searchParams.get('cursor'));
   return {status:'OPEN', slide:latest, index:[], revision:latest.version, confusion,
    transitions: cursor >= 0 ? timeline.slice(cursor+1) : []};
  }
  calls.push([url.pathname.split('/').pop(),Number(url.searchParams.get('page'))]);
  if (url.pathname.endsWith('/v1') && url.searchParams.get('page') === '2') return oldPrefetch;
  if (url.pathname.endsWith('/v6')) return stalled;
  if (url.pathname.endsWith('/v4') && !failedLatest) {
   failedLatest = true;
   throw error(503);
  }
  return response;
 };
 const page = {getViewport:({scale})=>({width:100*scale,height:100*scale,scale}),
  render:()=>({promise:Promise.resolve()}),streamTextContent(){}};
 const windowListeners = new Map();
 const htmlClasses = new Set();
 const mockWindow = {QU:{el:node,LEVEL:{}}, scrollX:0, scrollY:120, getSelection:()=>null,
  scrollTo(x,y){this.scrollX=x; this.scrollY=y;},
  addEventListener(type,fn){windowListeners.set(type,fn);}, removeEventListener(type){windowListeners.delete(type);}};
 const ctx = vm.createContext({
  window:mockWindow,
  document:{addEventListener(){},removeEventListener(){},documentElement:{classList:{add:x=>htmlClasses.add(x),remove:x=>htmlClasses.delete(x)}}},
  innerHeight:720, devicePixelRatio:1,
  URLSearchParams,setTimeout,clearTimeout,performance:{now:()=>clock+=400},
  loadFixtureRenderer: async ()=>({
   GlobalWorkerOptions:{},TextLayer:class {async render(){}},
   getDocument:()=>({promise:Promise.resolve({getPage:async()=>page,destroy:async()=>{},numPages:1})}),
  }),
 });
 vm.runInContext(stubbed,ctx);
 const view = ctx.window.QU.createLiveView({api,session:'fixture',panel,
  initial:{status:'OPEN',slide:latest,index:[]},onState(){},onQuestion(){}});
 async function until(condition, label) {
  const deadline = Date.now()+1500;
  while (!condition() && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,5));
  assert.ok(condition(),label);
 }
 try {
  await until(()=>calls.some(([v,p])=>v==='v1'&&p===2) && view.page()===1,'old prefetch remains in flight');
  latest = slide(2,'v4',3);
  timeline.push(slide(6,'v2',7),slide(8,'v3',9),latest);
  await until(()=>view.page()===2,'student retries the latest page without another teacher transition');
  assert.deepEqual(painted,['1 / 9','2 / 9']);
  assert.ok(!calls.some(([version])=>version==='v2'||version==='v3'));
  assert.equal(calls.filter(([v,p])=>v==='v4'&&p===2).length,2);
  releaseOld(response);
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(lastRendered,'2 / 9','old prefetch does not overwrite the current page');
  const sameFrame = content.children[0];
  const selection = {left:.2, top:.3, right:.4, bottom:.35};
  confusion = {spots:[{x:.3, y:.3, count:1, rects:[selection]}]};
  await until(()=>content.querySelector('.live-highlight')?.style.left==='20%','selected phrase gets an overlay');
  confusion = {spots:[{x:.3, y:.3, count:2, rects:[selection]}]};
  await until(()=>content.querySelector('.live-mark')?.textContent===2,'same-page live count updates');
  const badge = content.querySelector('.at-selection-corner');
  assert.equal(badge.style.left,'40%');
  assert.equal(badge.style.top,'30%');
  assert.equal(content.children[0],sameFrame,'reactions update the overlay without replacing the slide');
  const oldDraw = painted.length;
  latest = slide(1,'v5');
  timeline.push(latest);
  await until(()=>painted.length>oldDraw && view.page()===1,'return to a previously rendered page');
  assert.ok(!calls.some(([version, number])=>version==='v5'&&number===1),'warm canvas reused across transition versions');
  latest = slide(5,'v6');
  timeline.push(latest);
  await until(()=>calls.some(([version])=>version==='v6'),'intermediate page request is stalled');
  latest = slide(8,'v7');
  timeline.push(latest);
  await until(()=>view.page()===8,'latest page catches up while past download is stalled');
  assert.deepEqual(painted.slice(-2),['1 / 9','8 / 9']);
  releaseStalled(response);
  await new Promise(resolve=>setTimeout(resolve,25));
  assert.equal(lastRendered,'8 / 9','late old frame does not roll back the visible page');
  const drag = {pointerId:1,pointerType:'mouse',button:0,target:{closest:()=>true}};
  content.listeners.get('pointerdown')(drag);
  assert.ok(htmlClasses.has('live-selecting'));
  mockWindow.scrollY=180;
  windowListeners.get('scroll')();
  assert.equal(mockWindow.scrollY,120);
  latest = slide(9,'v8');
  timeline.push(latest);
  await until(()=>calls.some(([version])=>version==='v8'),'new page is fetched during text selection');
  assert.equal(view.page(),8,'selection keeps the current slide in place');
  windowListeners.get('pointerup')(drag);
  await until(()=>view.page()===9,'latest page resumes after selection');
  assert.ok(!htmlClasses.has('live-selecting'));
 } finally {
  releaseOld(response);
  releaseStalled(response);
  view.stop();
 }
 console.log('OK: live slide skips stale transitions and retries the latest page');
}

{
 const source = fs.readFileSync(path.join(root, 'assets/qu-teacher.js'), 'utf8');
 const nodes = new Map();
 const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {});
  return nodes.get(id);
 };
 const state = {live: {items: ['first', 'second', 'third'].map(id => ({id, text:id, level_label:'깊은'}))}};
 const proj = {hidden:true, classList:{remove(){}}};
 const writes = [];
 let release;
 const blocked = new Promise(resolve => { release = resolve; });
 const api = async (url, options) => {
  writes.push({url, ...JSON.parse(JSON.stringify(options))});
  if (writes.length === 1) await blocked;
 };
 const ctx = vm.createContext({state, $, proj, api, setTimeout, performance,
  openProjector(){proj.hidden = false;}, globalError(message){throw Error(message);}});
 const start = source.indexOf('  function project(id)');
 const end = source.indexOf("  $('proj-prev').addEventListener", start);
 vm.runInContext(source.slice(start, end), ctx);
 ctx.project('first');
 assert.equal($('proj-text').textContent, 'first');
 assert.equal(writes.length, 0, 'initial projection renders before any network request');
 assert.equal(state.live.prevId, 'third', 'previous wraps to the last question');
 while (!writes.length) await new Promise(resolve => setTimeout(resolve, 5));
 ctx.project(state.live.nextId);
 assert.equal($('proj-text').textContent, 'second');
 ctx.project(state.live.nextId);
 assert.equal($('proj-text').textContent, 'third');
 assert.equal(writes.length, 1, 'a stalled write does not block UI or start an overlapping write');
 release();
 await state.live.projectionSync;
 assert.deepEqual(writes.map(({url, body}) => [url, body.displayed_ids]), [
  ['/v1/session-questions/first/project', []],
  ['/v1/session-questions/third/project', ['second']],
 ]);
 console.log('OK: question projection renders immediately and persists the latest selection in order');
}
