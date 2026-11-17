/* View layer for the isolated LearningX offline classroom demo. */
import {DemoAdapter,eligible,levelOf,normalizeCode,questionStems} from './demo-adapter.js';

const adapter = new DemoAdapter();
const $ = id => document.getElementById(id);
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
const state = {role:new URLSearchParams(location.search).get('demo')==='faculty'?'faculty':'student',
  file:null, approvedSelection:new Set(), document:null, joinedCode:null,original:'',level:'',evidence:[],
  seenQuestions:new Set(),lastQuestionIds:new Set(),fileTask:0,lastFocus:null};

const say = message => {$('demo-status').textContent=message;};
const error = (id,message) => {const el=$(id);el.textContent=message;el.hidden=!message;};
const node = (tag,text,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const bytes = value => value < 1024 ? `${value} B` : value < 1024*1024 ? `${(value/1024).toFixed(1)} KB` : `${(value/(1024*1024)).toFixed(2)} MB`;

function updateVisibleMode() {
  const isDemo=$('demo-mode').getAttribute('aria-pressed')==='true';
  document.body.classList.toggle('demo-active',isDemo);
  $('demo-workspace').hidden=!isDemo;
  if (isDemo) renderAll();
}
function selectRole(next,focus=false) {
  state.role=next;
  const order=['faculty','student'];
  for (const type of order) {
    const selected=type===next,button=$('demo-'+type+'-tab');
    button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;
    $('demo-'+type+'-panel').hidden=!selected;
  }
  if (next==='faculty') {renderFaculty();renderQuestions();}
  else {$('demo-block-list').replaceChildren();renderStudent();closeProjector(false);}
  if (focus) $('demo-'+next+'-tab').focus();
}
function setupTabs() {
  for (const type of ['faculty','student']) {
    const tab=$('demo-'+type+'-tab');
    tab.addEventListener('click',()=>selectRole(type));
    tab.addEventListener('keydown',event=>{
      const roles=['faculty','student'];let index=roles.indexOf(type);
      if(event.key==='ArrowRight')index=(index+1)%2;
      else if(event.key==='ArrowLeft')index=(index+1)%2;
      else if(event.key==='Home')index=0;
      else if(event.key==='End')index=1;
      else return;
      event.preventDefault();selectRole(roles[index],true);
    });
  }
}
function statusSteps(step=0,thinking=false) {
  $('demo-steps').querySelectorAll('li').forEach((element,index)=>{
    element.classList.toggle('complete',index<step);
    element.classList.toggle('current',index===step&&thinking);
  });
  $('demo-thinking').hidden=!thinking;
}
function metadata() {
  const upload=adapter.state.upload;
  $('demo-file-info').hidden=!upload;
  $('demo-results').hidden=!upload;
  if (upload) {
    $('demo-file-name').textContent=upload.name;
    $('demo-file-size').textContent=bytes(upload.size);
    $('demo-file-hash').textContent=upload.sha256;
    statusSteps(3,false);
  } else statusSteps(0,false);
}
async function processFile(file) {
  if (!file) return;
  error('demo-upload-error','');
  const ext=/\.([^.]+)$/.exec(file.name)?.[1]?.toLowerCase();
  if (!['pdf','pptx'].includes(ext)) {error('demo-upload-error','PDF 또는 PPTX 파일만 업로드할 수 있습니다.');return;}
  if (file.size>100*1024*1024) {error('demo-upload-error','100MB를 초과하는 파일은 업로드할 수 없습니다.');return;}
  if (!file.size) {error('demo-upload-error','빈 파일은 사용할 수 없습니다.');return;}
  const run=++state.fileTask;
  statusSteps(0,true);
  $('demo-thinking-text').textContent='업로드 확인 중 · 파일의 실제 SHA-256을 계산합니다';
  say('파일 메타데이터를 확인하고 있습니다. 데모에서는 파일 내용은 분석하지 않습니다.');
  try {
    const sha256=await adapter.digest(file);
    if(run!==state.fileTask)return;
    statusSteps(1,true);
    $('demo-thinking-text').textContent='생각 중 표시 · 가상 교안 분석 화면 준비 중 (실제 추출 아님)';
    await pause(150);
    if(run!==state.fileTask)return;
    statusSteps(2,true);
    $('demo-thinking-text').textContent='교수자 검토 대기 · 예시 교안 목록을 준비합니다';
    await pause(150);
    if(run!==state.fileTask)return;
    state.approvedSelection.clear();
    adapter.recordUpload({name:file.name,size:file.size,sha256});
    statusSteps(3,false);
    say('SHA-256 계산이 끝났습니다. 아래 목록은 업로드한 파일이 아닌 가상 교안의 예시입니다.');
  } catch (ex) {
    statusSteps(0,false);
    error('demo-upload-error',ex?.message||'SHA-256 계산 중 오류가 발생했습니다.');
  }
}
function setupDrop() {
  const label=$('demo-drop'),input=$('demo-file');
  input.addEventListener('change',()=>{if(input.files?.[0])processFile(input.files[0]);});
  for(const name of ['dragenter','dragover'])label.addEventListener(name,e=>{
    e.preventDefault();label.classList.add('drag-over');
  });
  for(const name of ['dragleave','drop'])label.addEventListener(name,e=>{
    e.preventDefault();label.classList.remove('drag-over');
    if(name==='drop'&&e.dataTransfer?.files?.[0])processFile(e.dataTransfer.files[0]);
  });
}
function eligibleBlocks() {
  return state.document.pages.flatMap(page=>page.blocks.filter(eligible).map(block=>({page:page.page,block})));
}
function renderSummary() {
  const pages=state.document?.pages||[];
  const passes=eligibleBlocks();
  const approved=new Set(adapter.state.approved);
  $('demo-count-candidates').textContent=String(pages.length);
  $('demo-count-pass').textContent=String(new Set(passes.map(v=>v.page)).size);
  $('demo-count-approved').textContent=String(new Set(passes.filter(v=>approved.has(v.block.id)).map(v=>v.page)).size);
  $('demo-coaching-ready').textContent=approved.size?'가능':'불가';
  $('demo-start').disabled=!adapter.state.upload||!approved.size;
}
function renderFacultyBlocks() {
  const list=$('demo-block-list');list.replaceChildren();
  if(!adapter.state.upload||!state.document)return;
  const approved=new Set(adapter.state.approved);
  for (const page of state.document.pages) for (const block of page.blocks) {
    const ok=eligible(block),locked=Boolean(adapter.state.code);
    const label=node('label',undefined,'demo-block');
    label.setAttribute('aria-disabled',String(!ok));
    label.dataset.blockId=block.id;
    const check=node('input');check.type='checkbox';check.dataset.blockId=block.id;
    check.disabled=!ok||locked;
    check.checked=ok&&(state.approvedSelection.has(block.id)||approved.has(block.id));
    check.setAttribute('aria-label',`p.${page.page} ${block.content_role} 승인`);
    if(ok&&!locked) check.addEventListener('change',()=>{
      if(check.checked)state.approvedSelection.add(block.id);
      else state.approvedSelection.delete(block.id);
    });
    label.append(check);
    const body=node('div',undefined,'demo-block-body');
    body.append(node('h4',`p.${page.page} · ${block.content_role}`));
    body.append(node('small',`품질 상태: ${block.quality.status} · ${ok?'승인 가능':'승인 불가'}`));
    body.append(node('p',block.text));
    if(!ok)body.append(node('p',`승인 불가 사유: ${block.exclusion_reason}`,'demo-block-reason'));
    label.append(body);list.append(label);
  }
  $('demo-approve').disabled=locked;
}
function renderFaculty() {
  if(state.role!=='faculty')return;
  metadata();renderSummary();renderFacultyBlocks();
  const code=adapter.state.code;
  $('demo-class-code-panel').hidden=!code;
  $('demo-class-code').textContent=code||'';
}
function approve() {
  error('demo-approval-error','');
  if(!adapter.state.upload){error('demo-approval-error','먼저 교안을 선택해 주세요.');return;}
  if(adapter.state.code){error('demo-approval-error','시작된 수업에서는 승인 목록을 변경할 수 없습니다.');return;}
  const valid=new Set(eligibleBlocks().map(x=>x.block.id));
  const selected=Array.from(state.approvedSelection).filter(id=>valid.has(id));
  adapter.approve(selected,state.document);
  say(`${selected.length}쪽을 데모에서 승인 표시했습니다. 실제 암호학적 서명은 아닙니다.`);
}
function startClass() {
  try {const code=adapter.start();renderFaculty();say(`수업 시작 · ${code} · 학생 탭에서 입장할 수 있습니다.`);}
  catch(e){error('demo-approval-error',e.message);}
}
function renderQuestions() {
  const groups=$('demo-question-groups');groups.replaceChildren();
  const questions=adapter.state.questions;
  const unseen=questions.filter(q=>!state.seenQuestions.has(q.id)).length;
  $('demo-unread').textContent=String(unseen);
  $('demo-unread').setAttribute('aria-label',`새 질문 ${unseen}개`);
  $('demo-unread').hidden=!unseen;
  if (!questions.length) {groups.append(node('p','아직 제출된 질문이 없습니다.','demo-empty'));return;}
  for (const level of ['얕은','중간','깊은']) {
    const items=questions.filter(q=>q.level===level);
    if(!items.length)continue;
    const group=node('section',undefined,'demo-question-group');
    group.append(node('h4',`${level} 질문 · ${items.length}건`));
    for (const item of items) {
      const card=node('article',undefined,'demo-question-card');
      card.append(node('small','익명 · 데모용 규칙 분류'));
      card.append(node('p',`처음 질문: ${item.original}`));
      card.append(node('p',`고쳐 쓴 질문: ${item.revised}`));
      const show=node('button','수업에서 다루기','btn sm ghost');show.type='button';
      show.addEventListener('click',()=>{state.lastFocus=show;adapter.feature(item.id);showProjector();});
      card.append(show);group.append(card);
    }
    groups.append(group);
  }
}
function showProjector() {
  if(state.role!=='faculty')return;
  const question=adapter.state.questions.find(q=>q.id===adapter.state.featuredId);
  $('demo-projector').hidden=!question;
  if(question){$('demo-projector-question').textContent=question.revised;$('demo-projector-close').focus();}
}
function closeProjector(persist=true) {
  $('demo-projector').hidden=true;
  if(persist && adapter.state.featuredId)adapter.feature(null);
  if(state.lastFocus?.isConnected)state.lastFocus.focus();
}
function renderStudent() {
  if(state.role!=='student')return;
  const joined=state.joinedCode && state.joinedCode===adapter.state.code;
  if(!joined)state.joinedCode=null;
  $('demo-join-card').hidden=Boolean(joined);
  $('demo-student-session').hidden=!joined;
  if(joined)$('demo-joined-title').textContent='미디어와 사회 3주차 · 의제설정 이론 · 같은 브라우저 데모';
}
function join(event) {
  event.preventDefault();error('demo-code-error','');
  try{
    const {code}=adapter.join($('demo-code-entry').value);
    state.joinedCode=code;renderStudent();
    say('익명으로 데모 수업에 입장했습니다. 먼저 내 질문을 작성해 주세요.');
    $('demo-question').focus();
  }catch(err){error('demo-code-error',err.message);$('demo-code-entry').setAttribute('aria-invalid','true');}
}
function ask(event) {
  event.preventDefault();error('demo-question-error','');
  const question=$('demo-question').value.trim();
  if(question.length<10){error('demo-question-error','질문을 10자 이상 입력해 주세요.');return;}
  if(!state.joinedCode||state.joinedCode!==adapter.state.code){error('demo-question-error','먼저 수업에 입장해야 합니다.');return;}
  state.original=question;state.level=levelOf(question);
  state.evidence=adapter.evidence(question,state.document);
  $('demo-question-level').textContent=state.level+' 질문';
  const stems=$('demo-stems');stems.replaceChildren();
  for(const [label,template] of questionStems(state.level)){
    const chip=node('button',`${label} · ${template}`,'demo-stem');chip.type='button';
    chip.addEventListener('click',()=>chooseStem(template));stems.append(chip);
  }
  const evidence=$('demo-evidence');evidence.replaceChildren();
  if(!state.evidence.length)evidence.append(node('p','승인된 교안과 직접 관련된 근거가 없어 출처를 붙이지 않았습니다.','small-muted'));
  else for(const item of state.evidence){
    const detail=node('details');const summary=node('summary',`교안 p.${item.page} · 교수자 승인됨`);
    detail.append(summary,node('p',item.text));evidence.append(detail);
  }
  $('demo-coach-result').hidden=false;
  $('demo-rewrite-box').hidden=true;
  $('demo-submit-toast').hidden=true;
  $('demo-coach-result').scrollIntoView({block:'nearest',behavior:'instant'});
  say('질문 수준을 분류했습니다. 추천 질문 칩은 완성 문장이 아닌 빈칸 질문 줄기입니다.');
}
function chooseStem(template) {
  $('demo-rewrite-box').hidden=false;
  const box=$('demo-rewrite');box.value=template;
  const start=template.indexOf('___');
  box.focus();if(start>=0)box.setSelectionRange(start,start+3);
  error('demo-rewrite-error','');
}
function submit(event) {
  event.preventDefault();error('demo-rewrite-error','');
  const value=$('demo-rewrite').value.trim();
  if(value.includes('___')){error('demo-rewrite-error','빈칸을 내 생각으로 채워 주세요');return;}
  if(value.length<10){error('demo-rewrite-error','질문을 10자 이상 작성해 주세요.');return;}
  try{
    adapter.submit({original:state.original,revised:value,level:state.level,evidence:state.evidence});
    $('demo-submit-toast').hidden=false;
    $('demo-submit-toast').textContent='익명으로 제출했어요';
    $('demo-rewrite-box').hidden=true;
    $('demo-coach-result').querySelectorAll('.demo-stem').forEach(el=>el.disabled=true);
    say('익명 질문이 제출됐습니다. 교수자 화면에서 실시간으로 확인할 수 있습니다.');
  }catch(err){error('demo-rewrite-error',err.message);}
}
function renderAll() {
  if(!state.document)return;
  if(state.role==='faculty'){renderFaculty();renderQuestions();}
  else renderStudent();
}
function resetDemo() {
  if(!window.confirm('데모 수업·승인·익명 질문을 모두 초기화할까요?'))return;
  state.fileTask+=1;
  adapter.reset();state.approvedSelection.clear();state.joinedCode=null;
  state.original='';state.evidence=[];state.seenQuestions.clear();
  $('demo-file').value='';$('demo-code-entry').value='';$('demo-question').value='';$('demo-rewrite').value='';
  $('demo-coach-result').hidden=true;$('demo-rewrite-box').hidden=true;$('demo-submit-toast').hidden=true;
  $('demo-results').hidden=true;$('demo-file-info').hidden=true;
  for(const id of ['demo-code-error','demo-upload-error','demo-question-error','demo-rewrite-error','demo-approval-error'])error(id,'');
  renderAll();say('데모 초기화 완료 · 수업 코드와 익명 질문을 모두 지웠습니다.');
}
async function initialize() {
  const fragment=await fetch(new URL('./demo-workspace.html',import.meta.url)).then(r=>{if(!r.ok)throw new Error('데모 화면을 불러오지 못했습니다.');return r.text();});
  // Trusted, same-origin static markup authored with the site; no user HTML is inserted.
  const holder=document.createElement('template');holder.innerHTML=fragment;
  document.querySelector('.pilot-role').after(holder.content);
  state.document=await adapter.example();
  setupTabs();setupDrop();
  $('demo-approve').addEventListener('click',approve);
  $('demo-start').addEventListener('click',startClass);
  $('demo-reset').addEventListener('click',resetDemo);
  $('demo-read-questions').addEventListener('click',()=>{
    for(const q of adapter.state.questions)state.seenQuestions.add(q.id);
    renderQuestions();say('새 질문을 모두 확인했습니다.');
  });
  $('demo-code-entry').addEventListener('input',e=>{
    const next=normalizeCode(e.target.value);e.target.value=next;
    e.target.removeAttribute('aria-invalid');error('demo-code-error','');
  });
  $('demo-join-form').addEventListener('submit',join);
  $('demo-question-form').addEventListener('submit',ask);
  $('demo-rewrite-form').addEventListener('submit',submit);
  $('demo-projector-close').addEventListener('click',()=>closeProjector());
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'&&!$('demo-projector').hidden)closeProjector();
    if(e.key==='Tab'&&!$('demo-projector').hidden){
      e.preventDefault();$('demo-projector-close').focus();
    }
  });
  adapter.subscribe(()=>{renderAll(); if(state.role==='faculty'&&adapter.state.featuredId&&!$('demo-projector').hidden)showProjector();});
  $('demo-sharing-note').textContent=adapter.shared
    ? '데모 모드는 같은 브라우저에서만 연결됩니다. 새 탭과 수업·질문을 공유합니다.'
    : '브라우저 저장소가 차단되어 이 탭 안에서만 동작합니다. 위 역할 탭을 전환해 시연하세요.';
  $('demo-mode').addEventListener('click',updateVisibleMode);
  $('api-mode').addEventListener('click',updateVisibleMode);
  selectRole(state.role);
  updateVisibleMode();
  say('가상 교안 데모 준비 완료 · 교수자 화면에서 파일을 선택해 시작하세요.');
}
initialize().catch(error=>{
  // A broken demo resource cannot interfere with the existing live API path.
  console.error('LearningX demo initialization:',error);
  const banner=document.createElement('p');banner.setAttribute('role','alert');
  banner.textContent='데모 모듈을 불러올 수 없습니다. 페이지를 다시 열어 주세요.';
  document.querySelector('.pilot-heading')?.after(banner);
});