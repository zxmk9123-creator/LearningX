/* Isolated LearningX classroom demo. Production API stays in pilot.js. */
import {DemoAdapter,eligible,levelOf,normalizeCode,questionStems} from './demo-adapter.js';
const adapter=new DemoAdapter();
const $=id=>document.getElementById(id);
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const state={role:new URLSearchParams(location.search).get('demo')==='faculty'?'faculty':'student',
  document:null,selected:new Set(),joinedCode:null,original:'',level:'',evidence:[],
  seen:new Set(),workId:0,previousFocus:null};
const make=(tag,text,cls)=>{const x=document.createElement(tag);if(text!==undefined)x.textContent=text;if(cls)x.className=cls;return x;};
const announce=message=>{$('demo-status').textContent=message;};
const inlineError=(id,message)=>{const x=$(id);x.textContent=message;x.hidden=!message;};
const filesize=n=>n<1024?n+' B':n<1024*1024?(n/1024).toFixed(1)+' KB':(n/(1024*1024)).toFixed(2)+' MB';

function updateMode(){
  const demo=$('demo-mode').getAttribute('aria-pressed')==='true';
  document.body.classList.toggle('demo-active',demo);
  $('demo-workspace').hidden=!demo;
  if(demo)renderAll();
}
function selectRole(role,focus){
  state.role=role;
  for(const name of ['faculty','student']){
    const chosen=name===role,tab=$('demo-'+name+'-tab');
    tab.setAttribute('aria-selected',String(chosen));
    tab.tabIndex=chosen?0:-1;
    $('demo-'+name+'-panel').hidden=!chosen;
  }
  if(role==='faculty'){renderFaculty();renderQuestions();}
  else{
    // Faculty-only text (answers, notes, unverified images/audio) is removed,
    // not merely hidden, from a tab displaying the student's demo.
    $('demo-block-list').replaceChildren();
    renderStudent();closeProjector(false);
  }
  if(focus)$('demo-'+role+'-tab').focus();
}
function setupTabs(){
  for(const role of ['faculty','student']){
    const tab=$('demo-'+role+'-tab');
    tab.addEventListener('click',()=>selectRole(role));
    tab.addEventListener('keydown',event=>{
      const options=['faculty','student'];
      let index=options.indexOf(role);
      if(event.key==='ArrowRight'||event.key==='ArrowLeft')index=1-index;
      else if(event.key==='Home')index=0;
      else if(event.key==='End')index=1;
      else return;
      event.preventDefault();selectRole(options[index],true);
    });
  }
}
function steps(done=0,thinking=false){
  $('demo-steps').querySelectorAll('li').forEach((item,index)=>{
    item.classList.toggle('complete',index<done);
    item.classList.toggle('current',thinking&&index===done);
  });
  $('demo-thinking').hidden=!thinking;
}
function metadata(){
  const upload=adapter.state.upload;
  $('demo-file-info').hidden=!upload;
  $('demo-results').hidden=!upload;
  if(upload){
    $('demo-file-name').textContent=upload.name;
    $('demo-file-size').textContent=filesize(upload.size);
    $('demo-file-hash').textContent=upload.sha256;
    steps(3);
  } else steps();
}
async function processFile(file){
  if(!file)return;
  inlineError('demo-upload-error','');
  const ext=/\.([^.]+)$/.exec(file.name)?.[1]?.toLowerCase();
  if(ext!=='pdf'&&ext!=='pptx'){
    inlineError('demo-upload-error','PDF 또는 PPTX 파일만 업로드할 수 있습니다.');return;
  }
  if(file.size>100*1024*1024){
    inlineError('demo-upload-error','100MB를 초과하는 파일은 업로드할 수 없습니다.');return;
  }
  if(!file.size){inlineError('demo-upload-error','빈 파일은 사용할 수 없습니다.');return;}
  const task=++state.workId;
  steps(0,true);
  $('demo-thinking-text').textContent='업로드 확인 중 · 실제 파일 SHA-256 계산';
  announce('파일 메타데이터를 확인합니다. 데모에서는 실제 교안을 분석하지 않습니다.');
  try{
    const hash=await adapter.digest(file);
    if(state.workId!==task)return;
    steps(1,true);
    $('demo-thinking-text').textContent='생각 중 표시 · 텍스트·구조 추출 예시 화면 준비 (실제 추출 아님)';
    await wait(150);
    if(state.workId!==task)return;
    steps(2,true);
    $('demo-thinking-text').textContent='교수자 검토 대기 · 가상 교안 준비 중';
    await wait(150);
    if(state.workId!==task)return;
    state.selected.clear();
    adapter.recordUpload({name:file.name,size:file.size,sha256:hash});
    steps(3);
    announce('SHA-256 계산 완료. 아래 내용은 업로드 파일이 아닌 직접 작성한 가상 교안입니다.');
  }catch(err){
    steps();
    inlineError('demo-upload-error',err?.message||'파일 해시 계산에 실패했습니다.');
  }
}
function setupDrop(){
  const label=$('demo-drop'),input=$('demo-file');
  input.addEventListener('change',()=>{if(input.files?.[0])processFile(input.files[0]);});
  for(const name of ['dragenter','dragover','dragleave','drop']){
    label.addEventListener(name,e=>{
      e.preventDefault();label.classList.toggle('drag-over',name==='dragenter'||name==='dragover');
      if(name==='drop'&&e.dataTransfer?.files?.[0])processFile(e.dataTransfer.files[0]);
    });
  }
}
function allowedBlocks(){
  return state.document.pages.flatMap(p=>p.blocks.filter(eligible).map(b=>({page:p.page,block:b})));
}
function renderSummary(){
  const pages=state.document?.pages||[];
  const passed=allowedBlocks(),approved=new Set(adapter.state.approved);
  $('demo-count-candidates').textContent=String(pages.length);
  $('demo-count-pass').textContent=String(new Set(passed.map(x=>x.page)).size);
  $('demo-count-approved').textContent=String(new Set(passed.filter(x=>approved.has(x.block.id)).map(x=>x.page)).size);
  $('demo-coaching-ready').textContent=approved.size?'가능':'불가';
  $('demo-start').disabled=!adapter.state.upload||!approved.size;
}
function renderBlocks(){
  const list=$('demo-block-list');list.replaceChildren();
  if(!adapter.state.upload)return;
  const approved=new Set(adapter.state.approved),started=!!adapter.state.code;
  for(const page of state.document.pages)for(const block of page.blocks){
    const canApprove=eligible(block);
    const label=make('label',undefined,'demo-block');
    label.dataset.blockId=block.id;
    label.setAttribute('aria-disabled',String(!canApprove));
    const checkbox=make('input');
    checkbox.type='checkbox';checkbox.dataset.blockId=block.id;
    checkbox.disabled=!canApprove||started;
    checkbox.checked=canApprove&&(state.selected.has(block.id)||approved.has(block.id));
    checkbox.setAttribute('aria-label','p.'+page.page+' '+block.content_role+' 승인');
    if(canApprove&&!started)checkbox.addEventListener('change',()=>{
      if(checkbox.checked)state.selected.add(block.id);
      else state.selected.delete(block.id);
    });
    label.append(checkbox);
    const body=make('div',undefined,'demo-block-body');
    body.append(make('h4','p.'+page.page+' · '+block.content_role));
    body.append(make('small','품질 상태: '+block.quality.status+' · '+(canApprove?'승인 가능':'승인 불가')));
    body.append(make('p',block.text));
    if(!canApprove)body.append(make('p','승인 불가 사유: '+block.exclusion_reason,'demo-block-reason'));
    label.append(body);list.append(label);
  }
  $('demo-approve').disabled=started;
}
function renderFaculty(){
  if(state.role!=='faculty')return;
  metadata();renderSummary();renderBlocks();
  const code=adapter.state.code;
  $('demo-class-code-panel').hidden=!code;
  $('demo-class-code').textContent=code||'';
}
function approve(){
  inlineError('demo-approval-error','');
  if(!adapter.state.upload){inlineError('demo-approval-error','먼저 파일을 선택해 주세요.');return;}
  if(adapter.state.code){inlineError('demo-approval-error','이미 시작된 수업의 승인 목록은 바꿀 수 없습니다.');return;}
  const ids=new Set(allowedBlocks().map(x=>x.block.id));
  const selected=[...state.selected].filter(x=>ids.has(x));
  adapter.approve(selected,state.document);
  announce(selected.length+'쪽을 데모에서 승인 표시했습니다. 실제 암호학적 서명은 아닙니다.');
}
function startClass(){
  inlineError('demo-approval-error','');
  try{
    const code=adapter.start();renderFaculty();
    announce('수업 시작 · '+code+' · 학생 탭에서 입장하세요.');
  }catch(e){inlineError('demo-approval-error',e.message);}
}
function renderQuestions(){
  if(state.role!=='faculty')return;
  const list=$('demo-question-groups');list.replaceChildren();
  const questions=adapter.state.questions;
  const unread=questions.filter(q=>!state.seen.has(q.id)).length;
  $('demo-unread').textContent=String(unread);
  $('demo-unread').setAttribute('aria-label','새 질문 '+unread+'개');
  $('demo-unread').hidden=unread===0;
  if(!questions.length){list.append(make('p','아직 제출된 질문이 없습니다.','demo-empty'));return;}
  for(const level of ['얕은','중간','깊은']){
    const rows=questions.filter(q=>q.level===level);
    if(!rows.length)continue;
    const group=make('section',undefined,'demo-question-group');
    group.append(make('h4',level+' 질문 · '+rows.length+'건'));
    for(const item of rows){
      const card=make('article',undefined,'demo-question-card');
      card.append(make('small','익명 · 데모용 규칙 분류'));
      card.append(make('p','처음 질문: '+item.original));
      card.append(make('p','고쳐 쓴 질문: '+item.revised));
      const button=make('button','수업에서 다루기','btn sm ghost');
      button.type='button';
      button.addEventListener('click',()=>{state.previousFocus=button;adapter.feature(item.id);showProjector();});
      card.append(button);group.append(card);
    }
    list.append(group);
  }
}
function showProjector(){
  if(state.role!=='faculty')return;
  const question=adapter.state.questions.find(q=>q.id===adapter.state.featuredId);
  $('demo-projector').hidden=!question;
  if(question){
    $('demo-projector-question').textContent=question.revised;
    $('demo-projector-close').focus();
  }
}
function closeProjector(persist=true){
  $('demo-projector').hidden=true;
  if(persist&&adapter.state.featuredId)adapter.feature(null);
  if(state.previousFocus?.isConnected)state.previousFocus.focus();
}
function renderStudent(){
  if(state.role!=='student')return;
  const joined=state.joinedCode&&state.joinedCode===adapter.state.code;
  if(!joined){
    if(state.joinedCode){
      $('demo-question').value='';$('demo-rewrite').value='';
      $('demo-coach-result').hidden=true;$('demo-rewrite-box').hidden=true;
      $('demo-submit-toast').hidden=true;$('demo-code-entry').value='';
    }
    state.joinedCode=null;
  }
  $('demo-join-card').hidden=!!joined;
  $('demo-student-session').hidden=!joined;
  if(joined)$('demo-joined-title').textContent='미디어와 사회 3주차 · 의제설정 이론 · 같은 브라우저 데모';
}
function join(event){
  event.preventDefault();inlineError('demo-code-error','');
  try{
    const result=adapter.join($('demo-code-entry').value);
    state.joinedCode=result.code;
    renderStudent();announce('익명으로 입장했습니다. 먼저 자신의 질문을 작성하세요.');
    $('demo-question').focus();
  }catch(err){
    inlineError('demo-code-error',err.message);
    $('demo-code-entry').setAttribute('aria-invalid','true');
  }
}
function ask(event){
  event.preventDefault();inlineError('demo-question-error','');
  const question=$('demo-question').value.trim();
  if(question.length<10){inlineError('demo-question-error','질문을 10자 이상 입력해 주세요.');return;}
  if(!state.joinedCode||state.joinedCode!==adapter.state.code){
    inlineError('demo-question-error','먼저 수업에 입장해야 합니다.');return;
  }
  state.original=question;state.level=levelOf(question);
  state.evidence=adapter.evidence(question,state.document);
  $('demo-question-level').textContent=state.level+' 질문';
  const stems=$('demo-stems');stems.replaceChildren();
  for(const pair of questionStems(state.level)){
    const label=pair[0],template=pair[1];
    const chip=make('button',label+' · '+template,'demo-stem');
    chip.type='button';chip.addEventListener('click',()=>chooseStem(template));stems.append(chip);
  }
  const evidence=$('demo-evidence');evidence.replaceChildren();
  if(!state.evidence.length)evidence.append(make('p','승인된 교안과 관련된 근거가 없어 출처를 붙이지 않았습니다.','small-muted'));
  else for(const item of state.evidence){
    const details=make('details');
    details.append(make('summary','교안 p.'+item.page+' · 교수자 승인됨'),make('p',item.text));
    evidence.append(details);
  }
  $('demo-coach-result').hidden=false;
  $('demo-rewrite-box').hidden=true;
  $('demo-submit-toast').hidden=true;
  $('demo-coach-result').scrollIntoView({block:'nearest',behavior:'instant'});
  announce('규칙 기반 질문 분류 완료. 추천 질문은 빈칸 줄기이며 완성된 답이 아닙니다.');
}
function chooseStem(template){
  $('demo-rewrite-box').hidden=false;
  const area=$('demo-rewrite');area.value=template;area.focus();
  const first=template.indexOf('___');
  if(first>=0)area.setSelectionRange(first,first+3);
  inlineError('demo-rewrite-error','');
}
function submit(event){
  event.preventDefault();inlineError('demo-rewrite-error','');
  const text=$('demo-rewrite').value.trim();
  if(text.includes('___')){
    inlineError('demo-rewrite-error','빈칸을 내 생각으로 채워 주세요');return;
  }
  if(text.length<10){
    inlineError('demo-rewrite-error','질문을 10자 이상 작성해 주세요.');return;
  }
  try{
    adapter.submit({original:state.original,revised:text,level:state.level,evidence:state.evidence});
    $('demo-submit-toast').hidden=false;
    $('demo-submit-toast').textContent='익명으로 제출했어요';
    $('demo-rewrite-box').hidden=true;
    $('demo-stems').querySelectorAll('button').forEach(button=>button.disabled=true);
    announce('익명 질문이 제출됐습니다. 교수자 화면에서 확인할 수 있습니다.');
  }catch(err){inlineError('demo-rewrite-error',err.message);}
}
function renderAll(){
  if(!state.document)return;
  if(state.role==='faculty'){renderFaculty();renderQuestions();}
  else renderStudent();
}
function resetDemo(){
  if(!window.confirm('데모 수업·승인·익명 질문을 모두 초기화할까요?'))return;
  state.workId++;
  adapter.reset();
  state.selected.clear();state.joinedCode=null;state.original='';state.evidence=[];state.seen.clear();
  for(const id of ['demo-file','demo-code-entry','demo-question','demo-rewrite'])$(id).value='';
  $('demo-coach-result').hidden=true;$('demo-rewrite-box').hidden=true;
  $('demo-submit-toast').hidden=true;
  $('demo-results').hidden=true;$('demo-file-info').hidden=true;
  for(const id of ['demo-code-error','demo-upload-error','demo-question-error','demo-rewrite-error','demo-approval-error'])inlineError(id,'');
  renderAll();announce('데모 초기화 완료 · 코드와 질문을 모두 지웠습니다.');
}
async function initialize(){
  const html=await fetch(new URL('./demo-workspace.html',import.meta.url))
    .then(r=>{if(!r.ok)throw Error('데모 화면을 불러오지 못했습니다.');return r.text();});
  const template=document.createElement('template');template.innerHTML=html;
  document.querySelector('.pilot-role').after(template.content);
  state.document=await adapter.example();
  setupTabs();setupDrop();
  $('demo-approve').addEventListener('click',approve);
  $('demo-start').addEventListener('click',startClass);
  $('demo-reset').addEventListener('click',resetDemo);
  $('demo-read-questions').addEventListener('click',()=>{
    for(const q of adapter.state.questions)state.seen.add(q.id);
    renderQuestions();announce('새 질문을 모두 확인했습니다.');
  });
  $('demo-code-entry').addEventListener('input',e=>{
    e.target.value=normalizeCode(e.target.value);
    e.target.removeAttribute('aria-invalid');inlineError('demo-code-error','');
  });
  $('demo-join-form').addEventListener('submit',join);
  $('demo-question-form').addEventListener('submit',ask);
  $('demo-rewrite-form').addEventListener('submit',submit);
  $('demo-projector-close').addEventListener('click',()=>closeProjector());
  document.addEventListener('keydown',e=>{
    if($('demo-projector').hidden)return;
    if(e.key==='Escape')closeProjector();
    else if(e.key==='Tab'){e.preventDefault();$('demo-projector-close').focus();}
  });
  adapter.subscribe(()=>{
    renderAll();
    if(state.role==='faculty'&&adapter.state.featuredId&&!$('demo-projector').hidden)showProjector();
  });
  $('demo-sharing-note').textContent=adapter.shared
    ?'데모 모드는 같은 브라우저에서만 연결됩니다. 새 탭과 수업·질문을 공유합니다.'
    :'브라우저 저장소가 차단되어 이 탭 안에서만 동작합니다. 위 역할 탭을 전환해 시연하세요.';
  $('demo-mode').addEventListener('click',updateMode);
  $('api-mode').addEventListener('click',updateMode);
  selectRole(state.role);updateMode();
  announce('가상 교안 데모 준비 완료 · 교수자 화면에서 파일을 선택하세요.');
}
initialize().catch(err=>{
  console.error('LearningX demo initialization:',err);
  const banner=make('p','데모 모듈을 불러올 수 없습니다. 페이지를 다시 열어 주세요.');
  banner.setAttribute('role','alert');
  document.querySelector('.pilot-heading')?.after(banner);
});
