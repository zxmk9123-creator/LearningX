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
// Entry: one school Google sign-in for both roles; anonymous join and the faculty trial stay available.
for(const id of ['google-start','google-role','google-cancel','login-other','join-form'])assert.match(entry,new RegExp(`id="${id}"`));
{ const sc=[...entry.matchAll(/<script src="assets\/([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(sc,['qu-api.js','qu-google.js','qu-entry.js'],'entry loads the API client, Google module, then entry');
  assert.ok(entry.includes('data-role="student"')&&entry.includes('data-role="faculty"'),'role choice on the entry page');
  assert.ok(entry.includes('href="teacher.html"'),'faculty trial still reachable'); }
assert.match(entry,/teacher\.html/);
const teacher=fs.readFileSync(path.join(root,'teacher.html'),'utf8');
for(const id of ['guest-start','course-list','upload-form','candidate-list','ai-consent','approve-start','board','level-filter','projector','export-csv'])assert.match(teacher,new RegExp(`id="${id}"`));
const student=fs.readFileSync(path.join(root,'student.html'),'utf8');
for(const id of ['join-form','thread','composer','send-now','screen-sent','mine-list'])assert.match(student,new RegExp(`id="${id}"`));
for(const html of [entry,teacher,student]){
 assert.doesNotMatch(html,/\sstyle=/,'CSP forbids inline style attributes');
 assert.doesNotMatch(html,/<script>(?!<\/script>)|<script(?![^>]*\bsrc=)[^>]*>/,'CSP forbids inline scripts');
}
for(const file of ['qu-api.js','qu-entry.js','qu-teacher.js','qu-student.js','qu-plant.js','qu-live.js','qu-live-protocol.js','qu-objective.js','qu-google.js']){
 const src=fs.readFileSync(path.join(root,'assets',file),'utf8');
 assert.doesNotMatch(src,/localStorage|sessionStorage|document\.cookie|indexedDB/,`${file} must keep tokens in memory only`);
 assert.doesNotMatch(src,/innerHTML|insertAdjacentHTML|eval\(|new Function/,`${file} must not inject HTML or code`);
 assert.doesNotMatch(src,/setAttribute\(\s*['"]style/,`${file} must not set style attributes (CSP)`);
}
for(const html of [teacher,student]){
 const order=[...html.matchAll(/<script src="assets\/([^"]+)"/g)].map(m=>m[1]);
 assert.ok(order.indexOf('qu-api.js')<order.indexOf('qu-live-protocol.js')&&order.indexOf('qu-live-protocol.js')<order.indexOf('qu-live.js'),
  'qu-live-protocol.js must load after qu-api.js and before qu-live.js: '+order.join(', '));
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
 // A question whose judgment failed has no level: neutral label, own filter, no plant growth.
 const ctx = vm.createContext({window:{}, location:{hostname:'localhost', search:''}, URL, URLSearchParams,
  AbortController, setTimeout, clearTimeout, fetch: async () => { throw new Error('offline'); }, document:{}, Node:class {}});
 vm.runInContext(quApi, ctx);
 const {levelText} = ctx.window.QU;
 assert.equal(levelText('unassessed'), '수준 판정 보류');
 assert.equal(levelText('deep'), '깊은 질문');
 assert.match(teacher, /data-level="unassessed"[^>]*>판정 보류 <span data-count="unassessed">/);
 assert.ok(quTeacher.includes("['all', 'deep', 'mid', 'shallow', 'unassessed']"), 'teacher paints the unassessed count');
 const start = quStudent.indexOf('  function renderCoaching(');
 const end = quStudent.indexOf('  function useTemplate(', start);
 const stages = [];
 const messages = [];
 const guideCards = [];
 const el = (tag, attrs = {}, ...kids) => ({tag, attrs, kids, append(...more){ kids.push(...more); }});
 const student = vm.createContext({el, levelText, LEVEL: ctx.window.QU.LEVEL, plant: stage => ({plant: stage}),
  batchSupported: false, RANK: {seed: 0, shallow: 1, mid: 2, deep: 3}, GROWTH: {}, TEMPLATES: {shallow: {items: [['비교', '___와 ___는 무엇이 다른가요?']]}}, st: {stage: 'mid'},
  setStage: stage => stages.push(stage), coachMsg: (...parts) => messages.push(parts),
  $: id => ({append(card){ if (id === 'thread') guideCards.push(card); }}), scrollThread(){}, submitCurrent(){}, updateComposer(){}, useTemplate(){},
  document: {createTextNode: text => ({text})}});
 vm.runInContext(quStudent.slice(start, end) + '\nthis.renderCoaching = renderCoaching;', student);
 student.renderCoaching('unassessed', {follow_up_question: '어떤 기준으로 구별할 수 있을까요?', feedback: '고정 문구'});
 assert.deepEqual(stages, [], 'an unassessed answer never moves the plant');
 assert.equal(guideCards[0].attrs.class, 'opts');
 assert.match(guideCards[0].kids[0].attrs.text, /질문을 한 단계 발전시키기/);
 assert.match(guideCards[0].kids[1].attrs.text, /교안의 개념/);
 assert.equal(guideCards[0].kids[2].tag, 'button');
 const texts = JSON.stringify(messages);
 assert.ok(texts.includes('수준 판정 보류') && texts.includes('어떤 기준으로 구별할 수 있을까요?'));
 assert.ok(!texts.includes('얕은') && !texts.includes('중간') && !texts.includes('깊은'), 'no level words for an unassessed answer');
 student.renderCoaching('deep', {follow_up_question: '다른 해석은 무엇일까요?'});
 assert.deepEqual(stages, ['deep'], 'validated levels still move the plant');
 console.log('OK: unassessed questions show no level, keep their own filter and never grow the plant');
}

{
 // Unit learning objective step: its own screen after evidence approval (the preview coaches from approved evidence).
 const order=[...teacher.matchAll(/<script src="assets\/([^"]+)"/g)].map(m=>m[1]);
 assert.ok(order.indexOf('qu-api.js')<order.indexOf('qu-objective.js')&&order.indexOf('qu-objective.js')<order.indexOf('qu-teacher.js'));
 assert.ok(teacher.indexOf('id="screen-prepare"') < teacher.indexOf('id="screen-objective"'), 'approval screen precedes the objective');
 assert.ok(!teacher.includes('objective-panel'), 'objective is no longer bundled into the approval screen');
 for (const id of ['objective-form','objective-status','objective-bar','objective-next','objective-skip','objective-error','approval-summary-text','approval-edit'])
  assert.ok(teacher.includes(`id="${id}"`), id);
 assert.deepEqual([...teacher.matchAll(/<span data-step="\d"><i>\d<\/i>([^<]+)<\/span>/g)].map(m=>m[1]), ['교안 올리기','교안 승인','학습목표','수업 시작']);
 assert.ok(quTeacher.includes("case 'objective'") && quTeacher.includes('go(`prepare/${u.courseId}/${job.material_id}`)'), 'upload continues to approval');
 assert.ok(quTeacher.includes('go(`objective/${p.course.id}/${p.material.id}`)'), 'approval continues to the objective step');
 assert.ok(quTeacher.includes("material.status !== 'FACULTY_SIGNED'"), 'the objective step sends unapproved materials back to approval');
 assert.ok(!teacher.includes('id="objective-summary"'), 'approval no longer summarizes a later step');
 assert.ok(quTeacher.includes('objectiveForm.save()') && quTeacher.includes('objectiveForm.discard()'), 'next confirms, skip discards');
 assert.ok(quTeacher.includes('await startClass(btn)') && quTeacher.includes('startClass(btn);'), 'both objective buttons open the class');
 assert.ok(!quTeacher.includes('objectivePanel'), 'old panel wiring removed');
 const quObjective = fs.readFileSync(path.join(root,'assets/qu-objective.js'),'utf8');
 assert.ok(quObjective.includes("card('단원 학습목표 *'") && quObjective.includes("card('확인할 수행 증거 *'"), 'required fields are marked with *');
 assert.ok(!quObjective.includes("card('목표 사고 과정 *'") && !quObjective.includes("card('코칭 지원 정도 *'"), 'defaulted fields are not marked');
 assert.ok(teacher.includes('class="objective-legend"'), 'the * legend is shown');
 // School Google sign-in (Neon Auth): shared module, lazy client, one-time role choice on both pages.
 const quGoogle = fs.readFileSync(path.join(root,'assets/qu-google.js'),'utf8');
 const studentHtml = fs.readFileSync(path.join(root,'student.html'),'utf8');
 const quStudentSrc = fs.readFileSync(path.join(root,'assets/qu-student.js'),'utf8');
 for (const page of [teacher, studentHtml]) {
  assert.ok(page.includes('id="google-start"') && page.includes('id="google-role"') && page.includes('data-role="faculty"') && page.includes('data-role="student"'), 'role choice on both pages');
  assert.ok(!/<script src="assets\/vendor\/neon-auth\.js"/.test(page), 'the Neon Auth bundle is not loaded up front');
  const scripts = [...page.matchAll(/<script src="assets\/([^"]+)"/g)].map(m=>m[1]);
  assert.ok(scripts.indexOf('qu-api.js') < scripts.indexOf('qu-google.js'), 'qu-google.js loads after the API client');
 }
 assert.ok(!teacher.includes('google-invite'), 'no invite code step any more');
 assert.ok(fs.existsSync(path.join(root,'assets/vendor/neon-auth.js')), 'vendored Neon Auth bundle');
 assert.ok(quGoogle.includes("'role_required'") && quGoogle.includes('/v1/auth/neon/session'), 'first sign-in asks for a role');
 assert.ok(quTeacher.includes('const authApi = client(() => null)') && quStudentSrc.includes('const authApi = client(() => null)'),
  'the exchange cannot loop through the 401 handler');
 assert.ok(quTeacher.includes("google.goTo('student.html', 'student')") && quStudentSrc.includes("google.goTo('teacher.html', 'faculty')"), 'each role is sent to its page with the chosen role');
 assert.ok(quGoogle.includes('roleHint()') && quGoogle.includes('detail.master'), 'masters choose a role on every sign-in without a second prompt');
 assert.ok(quStudentSrc.includes('client(() => joinTicket)'), 'a Google student joins with the join ticket');
 // Faculty board: a Google student's name is masked until clicked; the projector never shows it.
 assert.ok(quTeacher.includes('function studentChip(q)') && quTeacher.includes('revealedNames'), 'masked name chip with a remembered reveal');
 assert.ok(!/proj-[a-z]+'\)\.textContent = [^;]*student/.test(quTeacher), 'the projector never renders a student name');
 assert.ok(studentHtml.includes('id="google-privacy"'), 'students are told their name reaches the faculty');
 const ctx = vm.createContext({window:{QU:{el(){}}}});
 vm.runInContext(fs.readFileSync(path.join(root,'assets/qu-objective.js'),'utf8'), ctx);
 const R = ctx.window.QU.objectiveRules;
 const plain = value => JSON.parse(JSON.stringify(value));
 assert.deepEqual(plain(R.PROCESSES.map(([k, t]) => [k, t])), [['remember','기억'],['understand','이해'],['apply','적용'],['analyze','분석'],['evaluate','평가'],['create','창안']]);
 assert.equal(R.LEVELS.length, 5, 'each process has a 5-level scale');
 const mid = R.defaultWeights();
 assert.ok(Object.values(mid).every(v => v === 3), 'every process starts at the middle level');
 assert.equal(R.weightedMean(mid), 3.5); assert.equal(R.focus(mid), 'analyze', 'all-middle levels aim at 분석');
 const ones = Object.fromEntries(Object.keys(mid).map(k => [k, 1]));
 assert.equal(R.focus({...ones, remember:5}), 'remember', 'level 1 carries no weight');
 assert.equal(R.focus({...ones, create:5}), 'create');
 assert.equal(R.focus(ones), 'analyze', 'nothing emphasised is neutral');
 assert.equal(R.focus({...mid, remember:1, understand:1, apply:2, evaluate:5, create:5}), 'evaluate');
 assert.deepEqual(plain(R.weightsOf({process:'evaluate'})), {...ones, evaluate:5}, 'legacy single-process profiles keep their process');
 assert.deepEqual(plain(R.SUPPORT.map(([k]) => k)), ['minimal','directional','stepwise']);
 const empty = R.emptyDraft();
 assert.equal(R.focus(empty.weights), 'analyze', 'a new draft starts at the middle on every process');
 assert.equal(empty.support, 'directional', 'support defaults to directional');
 assert.deepEqual(plain(R.missing(empty)), ['학습목표','확인할 수행 증거']);
 const draft = {...empty, objective:'  학생은 분포를 보고 평균 변화의 원인을 설명할 수 있다. ', weights:{...mid, evaluate:4},
  evidence:'평균과 분포를 각각 근거로 사용한다.', other_raw:' ', other_items:[{kind:'guideline', text:' 자료와 해석을 나눠 말하도록 요청 '}, {kind:'goal', text:'  '}]};
 assert.deepEqual(plain(R.missing(draft)), []);
 assert.deepEqual(plain(R.payload(draft)), {objective:'학생은 분포를 보고 평균 변화의 원인을 설명할 수 있다.',
  process_weights:{remember:3, understand:3, apply:3, analyze:3, evaluate:4, create:3}, evidence:'평균과 분포를 각각 근거로 사용한다.', support:'directional',
  other_items:[{kind:'guideline', text:'자료와 해석을 나눠 말하도록 요청'}]}, 'unset optional fields and blank items are omitted');
 const withOptional = R.payload({...draft, knowledge_type:'conceptual', application_context:'new_situation', other_raw:'메모'});
 assert.deepEqual(plain([withOptional.knowledge_type, withOptional.application_context, withOptional.other_raw]), ['conceptual','new_situation','메모']);
 const saved = {version:2, objective:draft.objective.trim(), process:'analyze', process_weights:{...mid, evaluate:4}, evidence:draft.evidence, support:'directional',
  knowledge_type:null, application_context:null, other_raw:null, other_items:[{kind:'guideline', text:'자료와 해석을 나눠 말하도록 요청'}]};
 assert.ok(R.same(R.fromProfile(saved), draft), 'whitespace-only differences are not unsaved changes');
 assert.ok(!R.same(R.fromProfile(saved), {...draft, support:'stepwise'}));
 assert.equal(R.summary(null), '미설정 · 기존 코칭 흐름을 사용합니다');
 assert.equal(R.summary(saved), 'v2 · 분석 중심 · 방향 제시 · 학생은 분포를 보고 평균 변화의 원인을 설명할 수 있다.');
 assert.ok(R.REASON.answer_disclosure && R.REASON.evidence_unclear && R.RELEVANCE.prerequisite);
 console.log('OK: learning objective step follows approval, sets a 5-level emphasis per process (default middle) whose weighted average picks the focus and builds clean requests');
}

{
 const liveSource = fs.readFileSync(path.join(root,'assets/qu-live.js'),'utf8');
 const protocolSource = fs.readFileSync(path.join(root,'assets/qu-live-protocol.js'),'utf8');
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
 const menu = panel.querySelector('[data-live-actions]');
 const menuClasses = new Set();
 menu.classList = {add: name => menuClasses.add(name), remove: name => menuClasses.delete(name)};
 menu.contains = target => target === menu;
 panel.getBoundingClientRect = () => ({left:0, top:0, width:640, height:480});
 let textSelection = null;
 const response = {arrayBuffer: async () => new ArrayBuffer(10)};
 const error = status => Object.assign(new Error('fixture HTTP '+status),{status});
 const slide = (page, version, after) => ({page, version, after, total:9, pdf:true});
 let latest = slide(1,'v1',2);
 const timeline = [latest];
 const painted = [];
 let confusion;
 const css = fs.readFileSync(path.join(root, 'assets/qu.css'), 'utf8');
 assert.doesNotMatch(css, /:fullscreen[^{}]*\.live-marks[^{}]*\{[^{}]*display:\s*none/);
 let failedLatest = false;
 let releaseStalled;
 const stalled = new Promise(resolve => { releaseStalled = resolve; });
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
 const workers = [];
 const api = async (path, {readBody} = {}) => {
  const url = new URL(path,'https://fixture.local');
  if (!url.pathname.includes('/page/')) {
   await new Promise(resolve => setTimeout(resolve,8));
   const cursor = timeline.findIndex(item=>item.version===url.searchParams.get('cursor'));
   const data = {status:'OPEN', slide:latest, index:[], revision:latest.version, confusion,
    transitions: cursor >= 0 ? timeline.slice(cursor+1) : []};
   if (latest.version === 'v7' && url.searchParams.get('version') !== 'v7') {
    assert.equal(url.searchParams.get('inline_page'), 'true');
    const metadata = new TextEncoder().encode(JSON.stringify(data));
    const framed = new Uint8Array(4 + metadata.length + 10);
    new DataView(framed.buffer).setUint32(0, metadata.length);
    framed.set(metadata, 4);
    return readBody({headers: new Headers({'Content-Type':'application/vnd.question-up.live-page'}),
      arrayBuffer: async () => framed.buffer});
   }
   return data;
  }
  calls.push([url.pathname.split('/').pop(),Number(url.searchParams.get('page'))]);
  if (url.pathname.endsWith('/v6')) return readBody(await stalled);
  if (url.pathname.endsWith('/v4') && !failedLatest) {
   failedLatest = true;
   throw error(503);
  }
  return readBody(response);
 };
 const page = {getViewport:({scale})=>({width:100*scale,height:100*scale,scale}),
  render:()=>({promise:Promise.resolve()}),streamTextContent(){}};
 const windowListeners = new Map();
 const htmlClasses = new Set();
 const mockWindow = {QU:{el:node,LEVEL:{}}, scrollX:0, scrollY:120, getSelection:()=>textSelection,
  scrollTo(x,y){this.scrollX=x; this.scrollY=y;},
  addEventListener(type,fn){windowListeners.set(type,fn);}, removeEventListener(type){windowListeners.delete(type);}};
 const ctx = vm.createContext({
  window:mockWindow,
  document:{addEventListener(){},removeEventListener(){},documentElement:{classList:{add:x=>htmlClasses.add(x),remove:x=>htmlClasses.delete(x)}}},
  innerHeight:720, devicePixelRatio:1,
  URLSearchParams,TextDecoder,setTimeout,clearTimeout,AbortController,performance:{now:()=>clock+=400},
  loadFixtureRenderer: async ()=>({
   GlobalWorkerOptions:{},TextLayer:class {async render(){}},
   PDFWorker:class {
    constructor(){ this.promise=Promise.resolve(); workers.push(this); }
   },
   getDocument:({worker})=>{
    assert.equal(worker,workers[0]);
    return {promise:Promise.resolve({getPage:async()=>page,destroy:async()=>{},numPages:1})};
   },
  }),
 });
 vm.runInContext(protocolSource,ctx);
 vm.runInContext(stubbed,ctx);
 const view = ctx.window.QU.createLiveView({api,session:'fixture',panel,
  initial:{status:'OPEN',slide:latest,index:[]},onState(){},onQuestion(){}});
 async function until(condition, label) {
  const deadline = Date.now()+1500;
  while (!condition() && Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,5));
  assert.ok(condition(),label);
 }
 try {
  await until(()=>view.page()===1,'first page rendered');
  assert.deepEqual(calls,[['v1',1]],'no adjacent page request for the student');
  latest = slide(2,'v4',3);
  timeline.push(slide(6,'v2',7),slide(8,'v3',9),latest);
  await until(()=>view.page()===2,'student retries the latest page without another teacher transition');
  assert.deepEqual(painted,['1 / 9','2 / 9']);
  assert.ok(!calls.some(([version])=>version==='v2'||version==='v3'));
  assert.equal(calls.filter(([v,p])=>v==='v4'&&p===2).length,2);
  await new Promise(resolve=>setTimeout(resolve,25));
  const sameFrame = content.children[0];
  const selection = {left:.2, top:.3, right:.4, bottom:.35};
  confusion = {spots:[{x:.3, y:.3, count:1, rects:[selection]}]};
  await until(()=>content.querySelector('.live-highlight')?.style.left==='20%','selected phrase gets an overlay');
  confusion = {spots:[{x:.3, y:.3, count:2, rects:[selection, {left:.23, top:.3, right:.31, bottom:.35},
   {left:.2, top:.38, right:.85, bottom:.43}]}]};
  await until(()=>content.querySelector('.live-mark')?.textContent===2,'same-page live count updates');
  const badge = content.querySelector('.at-selection-corner');
  assert.equal(badge.style.left,'40%','badge uses the first line, even when a later line extends farther right');
  assert.equal(content.querySelector('.live-highlight-group').children.length,3,'overlaps share a single opacity layer');
  assert.equal(badge.style.top,'30%');
  assert.equal(content.children[0],sameFrame,'reactions update the overlay without replacing the slide');
  const oldDraw = painted.length;
  latest = slide(1,'v5');
  timeline.push(latest);
  await until(()=>painted.length>oldDraw && view.page()===1,'return to a previously rendered page');
  assert.ok(calls.some(([version, number])=>version==='v5'&&number===1),'a new broadcast version is authorized by the server');
  latest = slide(5,'v6');
  timeline.push(latest);
  await until(()=>calls.some(([version])=>version==='v6'),'intermediate page request is stalled');
  latest = slide(8,'v7');
  timeline.push(latest);
  await until(()=>view.page()===8,'latest page catches up while past download is stalled');
  assert.deepEqual(painted.slice(-2),['1 / 9','8 / 9']);
  assert.ok(!calls.some(([version])=>version==='v7'),'inline PDF avoids a second HTTP request');
  assert.equal(workers.length,1,'successive slides reuse one PDF worker');
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
  const frame = content.querySelector('.live-pdf');
  const layer = frame.querySelector('.live-pdf-text');
  const anchor = {};
  layer.contains = node => node === anchor;
  frame.getBoundingClientRect = () => ({left:0,top:0,width:640,height:480});
  const rect = {left:130,top:90,right:220,bottom:114,width:90,height:24};
  const range = {startContainer:anchor,endContainer:anchor,getBoundingClientRect:()=>rect,
   getClientRects:()=>[rect],toString:()=> 'fixture selection'};
  textSelection = {anchorNode:anchor,isCollapsed:false,rangeCount:1,getRangeAt:()=>({cloneRange:()=>range})};
  ctx.document.fullscreenElement = panel;
  content.listeners.get('pointerdown')(drag);
  windowListeners.get('pointerup')(drag);
  assert.ok(menuClasses.has('selection-actions'));
  assert.equal(menu.style.top,'122px');
  panel.listeners.get('pointerdown')({target:menu});
  assert.ok(menuClasses.has('selection-actions'),'menu click preserves selected anchor');
  panel.listeners.get('pointerdown')({target:content});
  assert.ok(!menuClasses.has('selection-actions'),'outside press dismisses menu');
 } finally {
  releaseStalled(response);
  view.stop();
 }
 console.log('OK: live slide skips stale transitions and retries the latest page');
}

{
 // Student page errors: 409 means the shared page moved on (fetch the latest
 // state, no retry of the stale page); 401/403 means access ended (stop and
 // clear, and a slow render finishing later must not repaint the lesson).
 const liveSource = fs.readFileSync(path.join(root,'assets/qu-live.js'),'utf8')
  .replace("import(pdfBase + 'build/pdf.min.mjs')", 'loadFixtureRenderer()');
 function node(attrs = {}) {
  return {children:[], classes:(attrs.class || '').split(' '), style:{setProperty(){}}, clientWidth:640,
   textContent:attrs.text ?? '', classList:{add(){}, remove(){}, contains:() => false}, contains:() => false,
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
   }, addEventListener(){}, removeEventListener(){}};
 }
 const error = status => Object.assign(new Error('fixture HTTP ' + status), {status});
 const slide = (page, version) => ({page, version, total:9, pdf:true});
 const page = {getViewport:({scale})=>({width:100*scale,height:100*scale,scale}),
  render:()=>({promise:Promise.resolve()}), streamTextContent(){}};
 async function until(condition, label) {
  const deadline = Date.now() + 1500;
  while (!condition() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 5));
  assert.ok(condition(), label);
 }
 function harness(pageResponse, {parkMs = 40} = {}) {
  const parts = new Map();
  const panel = node();
  panel.querySelector = key => {
   if (key === '[data-live-confused]' || key === '[data-live-ask]') return null;
   if (!parts.has(key)) parts.set(key, node());
   return parts.get(key);
  };
  panel.getBoundingClientRect = () => ({left:0, top:0, width:640, height:480});
  const state = {latest: slide(1, 'v1'), polls: [], pages: [], cancelled: 0, panel};
  const api = async (pathname, {readBody, signal} = {}) => {
   const url = new URL(pathname, 'https://fixture.local');
   if (!url.pathname.includes('/page/')) {
    state.polls.push(Object.fromEntries(url.searchParams));
    // A poll with `after` parks until its timeout: this server never wakes it,
    // as when the presenter's change reached another API instance.
    await new Promise((resolve, reject) => {
     const wait = setTimeout(resolve, url.searchParams.has('after') ? parkMs : 4);
     signal?.addEventListener('abort', () => {
      clearTimeout(wait);
      state.cancelled += 1;
      reject(Object.assign(new Error('cancelled'), {status:0, detail:'cancelled'}));
     });
    });
    return {status:'OPEN', slide:state.latest, index:[], revision:'r-' + state.latest.version};
   }
   const version = url.pathname.split('/').pop();
   state.pages.push(version);
   return pageResponse(version, readBody, state);
  };
  const ctx = vm.createContext({
   window:{QU:{el:(tag, attrs, ...kids) => { const n = node(attrs); n.append(...kids); return n; }, LEVEL:{}},
    scrollX:0, scrollY:0, getSelection:() => null, addEventListener(){}, removeEventListener(){}},
   document:{addEventListener(){}, removeEventListener(){}, documentElement:{classList:{add(){}, remove(){}}}},
   innerHeight:720, devicePixelRatio:1, URLSearchParams, TextDecoder, setTimeout, clearTimeout, AbortController,
   loadFixtureRenderer: async () => ({GlobalWorkerOptions:{}, TextLayer:class {async render(){}},
    PDFWorker:class { constructor(){ this.promise = Promise.resolve(); } destroy(){} },
    getDocument:() => ({promise:Promise.resolve({getPage:async () => page, destroy:async () => {}, numPages:1})})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root,'assets/qu-live-protocol.js'),'utf8'), ctx);
  vm.runInContext(liveSource, ctx);
  state.view = ctx.window.QU.createLiveView({api, session:'fixture', panel,
   initial:{status:'OPEN', slide:state.latest, index:[]}, onState(){}, onQuestion(){}});
  state.content = panel.querySelector('[data-live-content]');
  state.status = panel.querySelector('[data-live-status]');
  return state;
 }
 const pdfBody = readBody => readBody({headers:new Headers(), arrayBuffer:async () => new ArrayBuffer(10)});

 // 409: the presenter moved on before the student's page request was answered.
 const moved = harness((version, readBody, state) => {
  if (version === 'v1') { state.latest = slide(2, 'v2'); throw error(409); }
  return pdfBody(readBody);
 });
 try {
  await until(() => moved.view.page() === 2, 'student shows the latest page after a 409');
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(moved.pages.filter(v => v === 'v1').length, 1, 'the stale page is not retried');
  assert.ok(moved.polls.some((poll, i) => i > 0 && !('after' in poll)), 'a 409 polls the latest state without waiting');
 } finally { moved.view.stop(); }

 // 409 while a poll is already parked on the old revision and is never woken.
 let staleAt;
 const parked = harness(async (version, readBody, state) => {
  if (version === 'v1') {
   await until(() => state.polls.some(poll => 'after' in poll), 'a poll parks before the page answers');
   state.latest = slide(2, 'v2');
   staleAt = performance.now();
   throw error(409);
  }
  return pdfBody(readBody);
 }, {parkMs: 1000});
 try {
  await until(() => parked.view.page() === 2, 'latest page shown after a 409 during a parked poll');
  const elapsed = performance.now() - staleAt;
  assert.equal(parked.cancelled, 1, 'the parked poll is cancelled');
  assert.ok(elapsed < 300, `latest page took ${elapsed.toFixed(0)} ms; the parked poll would wait 1000 ms`);
  assert.equal(parked.pages.filter(v => v === 'v1').length, 1, 'the stale page is not retried');
  parked.parkedMs = elapsed;
 } finally { parked.view.stop(); }

 // 403: access revoked while a slow page download is in flight.
 let finishSlow;
 const slow = new Promise(resolve => { finishSlow = resolve; });
 const revoked = harness(async (version, readBody) => {
  if (version === 'v1') { await slow; return pdfBody(readBody); }
  throw error(403);
 });
 try {
  await until(() => revoked.pages.includes('v1'), 'slow first page requested');
  revoked.latest = slide(3, 'v3');
  await until(() => revoked.pages.includes('v3'), 'next page requested after revocation');
  await until(() => revoked.status.textContent === '교안 접근 권한이 없습니다.', 'revocation stops the view');
  const pollsAtStop = revoked.polls.length;
  finishSlow();
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(revoked.content.children.length, 0, 'a render finishing after revocation does not repaint');
  assert.equal(revoked.view.page(), null);
  assert.equal(revoked.polls.length, pollsAtStop, 'no polling after access ends');
  assert.equal(revoked.pages.filter(v => v === 'v3').length, 1, 'a revoked page is not retried');
 } finally { finishSlow(); revoked.view.stop(); }
 console.log('OK: student view refetches on 409 (parked poll cancelled, latest page in',
  parked.parkedMs.toFixed(0), 'ms) and stops on 401/403 without late repaint');
}

{
 // Live protocol rules on their own: decoding, poll query and version decisions.
 const ctx = vm.createContext({window:{QU:{el(){}}}, URLSearchParams, TextDecoder});
 vm.runInContext(fs.readFileSync(path.join(root,'assets/qu-live-protocol.js'),'utf8'), ctx);
 const P = ctx.window.QU.liveProtocol;
 assert.ok(ctx.window.QU.el, 'protocol keeps existing QU members');
 const plain = value => JSON.parse(JSON.stringify(value));

 assert.ok(P.isLivePage('application/vnd.question-up.live-page'));
 assert.ok(!P.isLivePage('application/json') && !P.isLivePage(null));
 const meta = new TextEncoder().encode(JSON.stringify({status:'OPEN', slide:{page:3, version:'v3', sequence:4}}));
 const pdf = new TextEncoder().encode('%PDF-1.7 page');
 const frame = new Uint8Array(4 + meta.length + pdf.length);
 new DataView(frame.buffer).setUint32(0, meta.length);
 frame.set(meta, 4); frame.set(pdf, 4 + meta.length);
 const decoded = P.decodeLiveFrame(frame.buffer);
 assert.deepEqual(plain({status:decoded.status, slide:decoded.slide}), {status:'OPEN', slide:{page:3, version:'v3', sequence:4}});
 assert.equal(new TextDecoder().decode(decoded.inlinePdf), '%PDF-1.7 page');
 assert.throws(() => P.decodeLiveFrame(frame.buffer.slice(0, 4 + meta.length)), /Incomplete/, 'metadata without a page');
 assert.throws(() => P.decodeLiveFrame(new ArrayBuffer(2)), /Incomplete/, 'truncated length prefix');

 const query = text => Object.fromEntries(new URLSearchParams(text));
 assert.deepEqual(query(P.pollQuery({})), {version:''});
 assert.deepEqual(query(P.pollQuery({version:'v1', stamp:'r1', indexVersion:'i1', inline:true})),
  {version:'v1', after:'r1', index_version:'i1', inline_page:'true', partial_page:'true'});
 assert.deepEqual(query(P.pollQuery({version:'v1', stamp:'r1', indexVersion:'i1', inline:true, fresh:true})),
  {version:'v1', index_version:'i1', inline_page:'true', partial_page:'true'}, 'right after a 409 the poll does not park');
 assert.deepEqual(query(P.pollQuery({version:'v1', stamp:'', inline:false})), {version:'v1', after:''});

 const s = (version, sequence) => ({version, sequence, page:sequence});
 let r = P.applyUpdate({slide:s('a',2), confirmed:s('a',2)}, {revision:'r9', slide:s('b',3)});
 assert.deepEqual(plain(r), {stamp:'r9', delay:0, stale:false, confirmed:s('b',3), changed:true});
 r = P.applyUpdate({slide:s('b',3), confirmed:s('b',3)}, {revision:'r9', slide:s('a',2)});
 assert.equal(r.stale, true, 'an older sequence never replaces the slide on screen');
 assert.deepEqual(plain(r.confirmed), s('b',3));
 r = P.applyUpdate({slide:s('a',2), confirmed:s('a',2)}, {partial:true, revision:'r9', slide:s('b',3)});
 assert.deepEqual([r.stamp, r.delay, r.changed], [undefined, 0, true], 'partial responses poll again at once without a stamp');
 r = P.applyUpdate({slide:s('a',2), confirmed:s('c',5)}, {slide:s('b',3)});
 assert.deepEqual([r.stamp, r.delay, r.stale, r.changed], [undefined, 2000, false, true]);
 assert.deepEqual(plain(r.confirmed), s('c',5), 'a newer confirmed publish is kept');
 r = P.applyUpdate({slide:s('a',2), confirmed:s('a',2)}, {revision:'r1', slide:s('a',2)});
 assert.equal(r.changed, false);
 r = P.applyUpdate({slide:s('a',2), confirmed:s('a',2)}, {revision:'r1', status:'CLOSED', slide:null});
 assert.deepEqual([r.stale, r.changed], [false, true], 'closing clears the slide');
 assert.deepEqual(plain(P.applyUpdate({slide:null, confirmed:null}, {revision:'r1', slide:{version:'x'}}).confirmed), {version:'x'});

 assert.ok(P.acceptsAcknowledgement(s('a',3), s('b',3)) && P.acceptsAcknowledgement(null, {}));
 assert.ok(!P.acceptsAcknowledgement(s('a',4), s('b',3)));

 const n = args => P.neighborPage({visiblePages:[], navigation:null, pageCount:5, ...args});
 assert.equal(n({visiblePages:[1,3,4], current:3, delta:1}), 4);
 assert.equal(n({visiblePages:[1,3,4], current:1, delta:-1}), undefined);
 assert.equal(n({navigation:{page:3, pdf:true, before:1, after:4}, current:3, delta:-1}), 1);
 assert.equal(n({navigation:{page:3, pdf:true, before:1, after:4}, current:2, delta:1}), 3);
 assert.equal(n({current:5, delta:1}), null);
 console.log('OK: live protocol decodes frames, builds poll queries and orders slide versions');
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
 const started = performance.now();
 ctx.project('first');
 const firstPaintMs = performance.now() - started;
 assert.equal($('proj-text').textContent, 'first');
 assert.equal(writes.length, 0, 'initial projection renders before any network request');
 assert.equal(state.live.prevId, 'third', 'previous wraps to the last question');
 while (!writes.length) await new Promise(resolve => setTimeout(resolve, 5));
 const nextStarted = performance.now();
 ctx.project(state.live.nextId);
 const nextPaintMs = performance.now() - nextStarted;
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
 console.log('OK: question projection renders immediately and persists the latest selection in order',
  {firstUpdateMs: firstPaintMs.toFixed(2), nextUpdateMs: nextPaintMs.toFixed(2)});
}

{
 const start = quStudent.indexOf('  const DRAFT_LIMIT =');
 const end = quStudent.indexOf('  /* ---------- 입장', start);
 const elements = new Map();
 const el = (tag, attrs = {}, ...kids) => ({
  tag, attrs, kids, disabled: !!attrs.disabled, contains: () => false,
  replaceChildren(...children) { this.kids = children; },
  addEventListener(name, handler) { this[name] = handler; },
 });
 const $ = id => {
  if (!elements.has(id)) elements.set(id, el('div'));
  return elements.get(id);
 };
 const requests = [];
 const coaching = [];
 const replies = [];
 const ctx = vm.createContext({$, el, token: 'test', document: {}, clearTimeout(){}, setTimeout(){},
  st: {session: {id:'class', status:'OPEN'}, sentCount:0}, crypto: {randomUUID: () => 'batch-id'},
  updateComposer(){}, coachMsg(...parts){ replies.push(parts); },
  renderCoaching(...parts){ coaching.push(parts); },
  api: async (path, options) => {
   if (path.endsWith('/mine')) return {
    suitability: {policy_version: 'test'}, items: [
     {id: 'd', revision_id: 'rev-d', suitability: {status: 'needs_revision'}},
    ],
   };
   requests.push({path, ...options});
   if (requests.length === 1) throw new Error('network error');
   return {submitted: options.body.items};
  },
 });
 vm.runInContext(quStudent.slice(start, end) + `
   batchSupported = true;
   for (const [id, status] of [['a','eligible'],['b','eligible'],['c','needs_revision'],['d','pending']]) {
    drafts.set(id, {id, text: id, revision_id: 'rev-' + id, suitability: {status, primary_reason: 'off_topic'}});
   }
   paintDrafts();`, ctx);
 const boxes = () => $('question-options').kids.map(card => card.kids[0].kids[0]);
 assert.deepEqual(Array.from(boxes(), box => box.disabled), [false, false]);
 for (const box of boxes().slice(0,2)) box.attrs.onchange({target:{checked:true}});
 assert.equal($('question-batch-send').textContent, '선택한 질문 보내기');
 $('question-edit').click();
 assert.match($('question-batch-status').textContent, /다듬기는 한 번에 하나만/);
 assert.equal(ctx.st.current, undefined, 'selecting multiple questions aborts editing');
 await $('question-batch-send').click();
 await $('question-batch-send').click();
 assert.equal(requests[0].path, '/v1/sessions/class/questions/submit-batch');
 assert.equal(JSON.stringify(requests[0].body), JSON.stringify(requests[1].body));
 assert.deepEqual(Array.from(requests[1].body.items, item => item.revision_id), ['rev-a','rev-b']);
 assert.deepEqual(Array.from(boxes(), box => box.attrs.id), []);
 assert.equal(ctx.st.sentCount, 2);
 replies.length = 0;
 await ctx.refreshDrafts();
 assert.equal($('question-options').kids.length, 0);
 assert.deepEqual(coaching, []);
 assert.equal(replies.length, 1);
 assert.equal(replies[0][0].attrs.class, 'question-rejection');
 assert.equal(replies[0][0].kids[0].attrs.text, '!');
 assert.equal(replies[0][0].kids[1].attrs.text, '반응만 적기보다 수업에서 궁금하거나 더 알아보고 싶은 내용을 적어 주세요.');
 await ctx.refreshDrafts();
 assert.equal(replies.length, 1, 'a subsequent poll does not repeat the rejection');
 console.log('OK: eligible drafts submit together, blocked and pending drafts stay out of the list, and network retries retain the batch ID');
}

{
 const card = student.match(/<details id="question-disclosure"[^>]*>/)?.[0];
 assert.ok(card && !/\bopen\b/.test(card), 'draft card starts collapsed');

 const nodes = new Map();
 const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {
   value: '', style: {}, addEventListener(event, handler) { this[event] = handler; },
  });
  return nodes.get(id);
 };
 const requests = [];
 const drafts = new Map();
 const outcomes = [];
 const ctx = vm.createContext({
  $, drafts, token: 'test', st: {session: {id: 'class'}}, questionAnchor: null,
  liveView: null, pendingAsk: null, draftRead: 0, batchSupported: true, selected: new Map(),
  crypto: {randomUUID: () => String(requests.length)}, updateComposer(){}, paintDrafts(){},
  meMsg(){}, scrollThread(){}, renderCoaching(){}, scheduleDraftPoll(){}, coachMsg(){},
  showDraftOutcome(draft){ outcomes.push(draft); },
  el: () => ({remove(){}}),
  api: async (url, options) => {
   requests.push({url, options});
   const id = url.includes('/revisions') ? 'q1' : 'q' + requests.length;
   const text = options.body.question ?? options.body.text;
   const status = text === '차단 예시' ? 'needs_revision' : 'eligible';
   return {id, level: 'mid', revision_id: 'rev' + requests.length, suitability: {status}};
  },
 });
 $('thread').append = () => {};
 const start = quStudent.indexOf("  $('composer').addEventListener('submit'");
 const end = quStudent.indexOf('  function renderCoaching(', start);
 vm.runInContext(quStudent.slice(start, end), ctx);
 const submit = async text => {
  $('q-input').value = text;
  await $('composer').submit({preventDefault(){}});
 };
 await submit('처음 작성한 질문입니다.');
 await submit('두 번째 질문도 저장합니다.');
 assert.deepEqual(requests.map(({url}) => url), [
  '/v1/sessions/class/questions', '/v1/sessions/class/questions',
 ]);
 assert.deepEqual([...drafts.keys()], ['q1', 'q2']);
 ctx.st.current = {...drafts.get('q1')};
 await submit('첫 번째 질문을 더 구체적으로 썼습니다.');
 assert.equal(requests[2].url, '/v1/session-questions/q1/revisions');
 assert.deepEqual([...drafts.keys()], ['q1', 'q2'], 'editing keeps the original position');
 assert.equal(drafts.get('q2').text, '두 번째 질문도 저장합니다.');
 assert.equal(ctx.st.current, null, 'the next entry starts a new draft after a revision');
 await submit('차단 예시');
 assert.deepEqual([...drafts.keys()], ['q1', 'q2']);
 assert.equal(outcomes.at(-1).suitability.status, 'needs_revision');
 console.log('OK: the card starts collapsed, new questions accumulate, and a revision changes only one draft');
}
