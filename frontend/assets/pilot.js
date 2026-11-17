/* LearningX static frontend. API tokens live ONLY in memory: no storage/cookies/logging. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {mode:'demo',role:'student',url:'',token:'',courses:[],facultyCourse:'',studentCourse:'',materials:[],questionId:'',history:[],demoApproved:false};
  const examples = [
    {page:2,block_id:'p2:example',block_sha256:'demo-not-a-signature',role:'learning_content',preview:'커뮤니케이션은 사람 사이에 정보와 생각·감정을 공유하는 과정입니다.'},
    {page:5,block_id:'p5:example',block_sha256:'demo-not-a-signature',role:'learning_content',preview:'대인·소집단·공중·매스 커뮤니케이션처럼 여러 수준을 비교할 수 있습니다.'},
    {page:30,block_id:'p30:example',block_sha256:'demo-not-a-signature',role:'learning_content',preview:'Big Five에는 개방성, 성실성, 외향성, 동의성, 신경성의 다섯 요인이 있습니다.'}
  ];
  const esc = value => String(value ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  function status(text,error=false){$('pilot-status').textContent=text;$('pilot-status').className='pilot-status '+(error?'error':'success');}
  function busy(element,flag){if(element)element.disabled=flag;}
  function setRole(role){
    state.role=role;
    $('role-student').setAttribute('aria-pressed',String(role==='student'));
    $('role-faculty').setAttribute('aria-pressed',String(role==='faculty'));
    $('student-work').hidden=role!=='student';$('faculty-work').hidden=role!=='faculty';
    if(state.mode==='live'&&state.token){refreshCourses().catch(showError);}
  }
  function fillSelect(id,items,placeholder){
    const node=$(id); node.replaceChildren();
    if(!items.length){const opt=document.createElement('option');opt.value='';opt.textContent=placeholder;node.append(opt);return;}
    for(const item of items){const opt=document.createElement('option');opt.value=item.id;opt.textContent=item.name;node.append(opt);}
  }
  function resetMode(mode){
    state.mode=mode;state.url='';state.token='';state.courses=[];state.materials=[];state.questionId='';state.history=[];state.demoApproved=false;
    $('api-token').value=''; $('disconnect').disabled=true;
    $('connection').hidden=mode==='demo';
    $('demo-mode').classList.toggle('active',mode==='demo');$('api-mode').classList.toggle('active',mode==='live');
    $('demo-mode').setAttribute('aria-pressed',String(mode==='demo'));$('api-mode').setAttribute('aria-pressed',String(mode==='live'));
    $('mode-notice').classList.toggle('live',mode==='live');
    $('mode-notice').textContent=mode==='demo'
      ? '데모 모드: 질문·근거·승인·이력은 가상 예시입니다. 실제 교수자 서명이나 LLM 호출은 발생하지 않습니다.'
      : '실서버 연동 모드: 토큰으로 사용자 신원과 수업 권한을 확인합니다. 실제 엔진이 미설치된 서버에서는 교안·코칭 기능이 차단됩니다.';
    $('feedback').hidden=true;$('candidates').hidden=true;
    $('conn-status').textContent='연결되지 않았습니다.';
    fillSelect('student-course',mode==='demo'?[{id:'demo-course',name:'커뮤니케이션의 유형과 성격 특성 (데모)'}]:[],'먼저 연결하세요');
    fillSelect('faculty-course',mode==='demo'?[{id:'demo-course',name:'커뮤니케이션의 유형과 성격 특성 (데모)'}]:[],'먼저 연결하세요');
    fillSelect('student-material',mode==='demo'?[{id:'demo-material',name:'2강 커뮤니케이션 유형 (예시)'}]:[],'수업을 선택하세요');
    fillSelect('faculty-material',mode==='demo'?[{id:'demo-material',name:'2강 커뮤니케이션 유형 (예시)'}]:[],'수업을 선택하세요');
    renderHistory(); status(mode==='demo'?'데모 환경이 초기화됐습니다.':'백엔드 주소와 토큰을 입력해 주세요.');
  }
  function normalizeURL(value){
    const parsed=new URL(value.trim());
    const local=['localhost','127.0.0.1'].includes(parsed.hostname);
    if(parsed.username||parsed.password||parsed.search||parsed.hash||(parsed.protocol!=='https:'&&!(local&&parsed.protocol==='http:'))){throw new Error('HTTPS 주소(또는 localhost HTTP)만 사용할 수 있습니다.');}
    if(parsed.pathname!=='/'&&parsed.pathname!==''){throw new Error('백엔드 URL은 도메인 주소만 입력해 주세요.');}
    return parsed.origin;
  }
  async function api(path,options={}){
    if(!state.token||!state.url)throw new Error('백엔드 연결이 필요합니다.');
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
    const headers={Authorization:'Bearer '+state.token,Accept:'application/json'};
    if(options.body && !(options.body instanceof FormData))headers['Content-Type']='application/json';
    try{
      const response=await fetch(state.url+path,{method:options.method||'GET',headers,body:options.body instanceof FormData?options.body:options.body?JSON.stringify(options.body):undefined,signal:controller.signal,credentials:'omit',mode:'cors',redirect:'error',cache:'no-store'});
      const json=(response.headers.get('content-type')||'').includes('json')?await response.json().catch(()=>({})):{};
      if(!response.ok){
        if(response.status===401){state.token='';$('api-token').value='';$('disconnect').disabled=true;}
        throw new Error(json.detail && typeof json.detail==='string'?json.detail:'서버 요청 실패 (HTTP '+response.status+')');
      }
      return json;
    }catch(e){if(e.name==='AbortError')throw new Error('서버 응답이 30초를 초과했습니다.');throw e;}finally{clearTimeout(timer);}
  }
  function showError(e){status(e instanceof Error?e.message:'요청에 실패했습니다.',true);}
  async function connect(e){
    e.preventDefault();const btn=e.submitter;busy(btn,true);
    try{
      state.url=normalizeURL($('api-url').value);
      state.token=$('api-token').value.trim();if(!state.token)throw new Error('토큰을 입력해 주세요.');
      const result=await api('/v1/courses/mine');
      if(!Array.isArray(result.items))throw new Error('수업 API 응답 형식이 올바르지 않습니다.');
      $('api-token').value='';$('disconnect').disabled=false;
      $('conn-status').textContent='서버 연결됨 · '+result.items.length+'개 수업 조회';
      await refreshCourses(result.items);status('서버 연결에 성공했습니다.');
    }catch(error){state.token='';state.url='';showError(error);}finally{busy(btn,false);}
  }
  async function refreshCourses(preloaded){
    if(state.mode!=='live'||!state.token)return;
    const courses=preloaded|| (await api('/v1/courses/mine')).items;
    state.courses=courses;
    const selected=courses.filter(c=>c.role===state.role||c.role==='admin');
    fillSelect(state.role==='student'?'student-course':'faculty-course',selected.map(c=>({id:c.id,name:c.title})), '이 역할의 수업이 없습니다');
    if(state.role==='student')await refreshMaterials('student');else await refreshMaterials('faculty');
  }
  async function refreshMaterials(role){
    const cid=$(role+'-course').value;
    if(state.mode==='demo')return;
    if(!cid){fillSelect(role+'-material',[],'수업을 먼저 선택하세요');return;}
    const result=await api('/v1/courses/'+encodeURIComponent(cid)+'/materials');
    const items=Array.isArray(result.items)?result.items:[];
    fillSelect(role+'-material',items.map(m=>({id:m.id,name:m.filename+' · '+(m.status==='FACULTY_SIGNED'?'서명 확인':'교수자 검토 대기')})),'등록된 교안이 없습니다');
  }
  function suggest(question){
    if(/그래프|도표|14쪽|31쪽|휴식/.test(question))return {follow_up_question:'자료에서 직접 확인할 수 있는 텍스트 근거와 아직 검증되지 않은 부분을 어떻게 구분할까요?',evidence_refs:[],mode:'DEMO_FALLBACK'};
    if(/갈등|회피/.test(question))return {follow_up_question:'갈등 회피 성향과 의사소통 능력을 각각 어떤 행동 지표로 정의하고 측정할 수 있을까요?',evidence_refs:[{page:19,id:'p19:demo'}],mode:'DEMO'};
    if(/외향|Big Five|성격/.test(question))return {follow_up_question:'그 특성이 실제 행동과 연결된다는 주장을 검증하려면 어떤 상황과 반례를 고려해야 할까요?',evidence_refs:[{page:30,id:'p30:demo'}],mode:'DEMO'};
    if(/차이|비교|유형/.test(question))return {follow_up_question:'두 개념을 비교할 때 공통으로 적용할 기준은 무엇이며, 어떤 사례에서 그 차이가 드러날까요?',evidence_refs:[{page:5,id:'p5:demo'}],mode:'DEMO'};
    return {follow_up_question:'이 질문에 숨어 있는 전제는 무엇이고, 이를 확인하려면 어떤 구체적인 사례가 필요할까요?',evidence_refs:[{page:2,id:'p2:demo'}],mode:'DEMO'};
  }
  let activeQuestion='';
  function displayFeedback(coaching){
    $('feedback').hidden=false;
    $('followup').textContent=coaching.follow_up_question||'질문의 전제를 확인하려면 무엇을 살펴봐야 할까요?';
    const refs=Array.isArray(coaching.evidence_refs)?coaching.evidence_refs:[];
    const label=state.mode==='demo'?'예시 근거 (실제 승인·인용 아님)': '서버가 검증한 근거 위치';
    $('evidence').textContent=label+' · '+(refs.length?refs.map(x=>'p.'+x.page).join(', '):'인용 없음 / 안전한 대체 질문');
  }
  async function ask(e){e.preventDefault();const btn=e.submitter;busy(btn,true);
    try{
      const q=$('question').value.trim();if(q.length<10)throw new Error('질문을 10자 이상 작성해 주세요.');
      let coaching,id;
      if(state.mode==='demo'){
        coaching=suggest(q);id='demo-'+String(Date.now());
      }else{
        const course=$('student-course').value,material=$('student-material').value;
        if(!course||!material)throw new Error('수업과 교안을 먼저 선택해 주세요.');
        const data=await api('/v1/courses/'+encodeURIComponent(course)+'/questions',{method:'POST',body:{material_id:material,question:q}});
        coaching=data.coaching;id=data.question_id;
        if(!coaching||typeof coaching.follow_up_question!=='string')throw new Error('코칭 응답 형식이 잘못됐습니다.');
      }
      state.questionId=id;activeQuestion=q;displayFeedback(coaching);
      state.history.unshift({id,question:q,coaching,revision:''});renderHistory();
      status(state.mode==='demo'?'예시 역질문이 생성됐습니다. 실제 AI 요청은 없습니다.':'교수자 승인 근거 기반 코칭 응답을 수신했습니다.');
      $('revision').focus();
    }catch(err){showError(err);}finally{busy(btn,false);}
  }
  function renderHistory(){
    const items=state.history;
    $('history-count').textContent=items.length+'건';
    if(!items.length){$('history-list').innerHTML='<p class="small-muted">아직 작성한 질문이 없습니다.</p>';return;}
    $('history-list').innerHTML=items.map(x=>'<article class="history-item"><b>처음 질문</b><p>'+esc(x.question)+'</p><b>코칭</b><p>'+esc(x.coaching?.follow_up_question||'')+'</p>'+(x.revision?'<div class="revisions">수정 · '+esc(x.revision)+'</div>':'')+'</article>').join('');
  }
  async function revise(e){e.preventDefault();const btn=e.submitter;busy(btn,true);
    try{
      const text=$('revision').value.trim();if(text.length<10)throw new Error('수정 질문을 10자 이상 작성해 주세요.');
      if(!state.questionId)throw new Error('먼저 역질문을 요청해 주세요.');
      if(state.mode==='live')await api('/v1/questions/'+encodeURIComponent(state.questionId)+'/revisions',{method:'POST',body:{text}});
      const item=state.history.find(x=>x.id===state.questionId);if(item)item.revision=text;
      renderHistory();$('revision').value='';
      status(state.mode==='demo'?'수정 질문이 이 화면의 예시 이력에 기록됐습니다. 새로고침하면 사라집니다.':'수정 질문을 서버에 저장했습니다.');
    }catch(err){showError(err);}finally{busy(btn,false);}
  }
  async function createCourse(e){e.preventDefault();const btn=e.submitter;busy(btn,true);try{
    if(state.mode==='demo')throw new Error('데모에서는 수업을 실제로 생성하지 않습니다. 서버 연결 모드를 이용해 주세요.');
    const title=$('course-title').value.trim();
    await api('/v1/courses',{method:'POST',body:{title,policy:'RESTRICTED_HINT_ONLY',teacher_approved:false}});
    await refreshCourses();status('수업을 생성했습니다. 학생은 별도로 등록해야 접근할 수 있습니다.');$('course-title').value='';
  }catch(err){showError(err);}finally{busy(btn,false);}}
  async function upload(e){e.preventDefault();const btn=e.submitter;busy(btn,true);try{
    if(state.mode==='demo')throw new Error('선택한 파일은 전송하지 않았습니다. 실제 교안 등록은 서버 연결 모드에서만 가능합니다.');
    const cid=$('faculty-course').value,file=$('material-file').files[0];if(!cid||!file)throw new Error('수업과 파일을 선택해 주세요.');
    if(!/\.(pdf|pptx)$/i.test(file.name)||file.size>25*1024*1024)throw new Error('25MB 이하 PDF 또는 PPTX만 가능합니다.');
    const body=new FormData();body.append('file',file,file.name);
    const result=await api('/v1/courses/'+encodeURIComponent(cid)+'/materials',{method:'POST',body});
    await refreshMaterials('faculty');
    status('교안 '+(result.filename||'')+' 분석 요청 완료. 교수자 검토·서명 전까지 근거로 사용할 수 없습니다.');
  }catch(err){showError(err);}finally{busy(btn,false);}}
  async function loadCandidates(){busy($('load-candidates'),true);try{
    let data;
    if(state.mode==='demo'){data=examples;}else{
      const material=$('faculty-material').value;if(!material)throw new Error('교안을 선택해 주세요.');
      const response=await api('/v1/materials/'+encodeURIComponent(material)+'/candidates');data=response.eligible_candidates||[];
    }
    $('candidates').hidden=false;
    if(!data.length){$('candidate-list').innerHTML='<p class="small-muted">승인 가능한 후보가 없습니다. 미검증 자료는 강제로 승인할 수 없습니다.</p>';return;}
    $('candidate-list').innerHTML=data.map((x,i)=>'<label class="candidate"><input type="checkbox" data-i="'+i+'"><span><b>p.'+esc(x.page)+' · '+esc(x.role||'block')+'</b><small>'+esc(x.preview||'텍스트 미리보기 없음')+'</small></span></label>').join('');
    $('candidate-list').dataset.candidates=JSON.stringify(data);
    status(state.mode==='demo'?'예시 후보를 표시했습니다. 실제 교안 추출·검토 결과가 아닙니다.':'승인 후보를 조회했습니다. 원문을 직접 검토한 뒤 서명하세요.');
  }catch(err){showError(err);}finally{busy($('load-candidates'),false);}}
  async function approve(){const btn=$('approve-selected');busy(btn,true);try{
    const arr=JSON.parse($('candidate-list').dataset.candidates||'[]');
    const checked=[...$('candidate-list').querySelectorAll('input:checked')].map(el=>arr[Number(el.dataset.i)]).filter(Boolean);
    if(!checked.length)throw new Error('승인할 후보를 선택해 주세요.');
    if(state.mode==='demo'){state.demoApproved=true;status('데모 승인 표시만 적용됐습니다. 암호학적 서명은 생성되지 않았습니다.');return;}
    const material=$('faculty-material').value;
    const items=checked.map(x=>({page:x.page,block_id:x.block_id,block_sha256:x.block_sha256}));
    const result=await api('/v1/materials/'+encodeURIComponent(material)+'/approve',{method:'POST',body:{items}});
    status('서버의 교수자 서명 처리 결과: 승인 '+(result.approved_count||0)+'개 블록.');
    await refreshMaterials('faculty');
  }catch(err){showError(err);}finally{busy(btn,false);}}
  async function updatePolicy(e){e.preventDefault();const btn=e.submitter;busy(btn,true);try{
    if(state.mode==='demo')throw new Error('데모에서는 정책을 저장하지 않습니다. 실제 교수자 승인 서명과 혼동하지 마세요.');
    const course=$('faculty-course').value;if(!course)throw new Error('수업을 선택하세요.');
    const data=await api('/v1/courses/'+encodeURIComponent(course)+'/policy',{method:'PUT',body:{policy:$('policy-value').value,teacher_approved:$('policy-approved').checked}});
    status('수업 정책을 저장했습니다. 근거 블록은 별도 서명이 필요합니다. ('+data.policy+')');
  }catch(err){showError(err);}finally{busy(btn,false);}}
  async function enroll(e){e.preventDefault();const btn=e.submitter;busy(btn,true);try{
    if(state.mode==='demo')throw new Error('데모에서는 실제 학생 계정을 등록하지 않습니다.');
    const cid=$('faculty-course').value,user=$('student-id').value.trim();
    if(!cid||!user)throw new Error('수업 및 학생 ID를 입력하세요.');
    await api('/v1/courses/'+encodeURIComponent(cid)+'/members',{method:'POST',body:{user_id:user}});
    status('서버가 학생 등록을 확인했습니다.');$('student-id').value='';
  }catch(err){showError(err);}finally{busy(btn,false);}}
  $('demo-mode').addEventListener('click',()=>resetMode('demo'));
  $('api-mode').addEventListener('click',()=>resetMode('live'));
  $('disconnect').addEventListener('click',()=>resetMode('live'));
  $('role-student').addEventListener('click',()=>setRole('student'));
  $('role-faculty').addEventListener('click',()=>setRole('faculty'));
  $('connect-form').addEventListener('submit',connect);
  $('student-course').addEventListener('change',()=>refreshMaterials('student').catch(showError));
  $('faculty-course').addEventListener('change',()=>refreshMaterials('faculty').catch(showError));
  $('ask-form').addEventListener('submit',ask);
  $('revise-form').addEventListener('submit',revise);
  $('course-form').addEventListener('submit',createCourse);
  $('upload-form').addEventListener('submit',upload);
  $('load-candidates').addEventListener('click',loadCandidates);
  $('approve-selected').addEventListener('click',approve);
  $('policy-form').addEventListener('submit',updatePolicy);
  $('enroll-form').addEventListener('submit',enroll);
  resetMode('demo');
  if(new URLSearchParams(location.search).get('demo')==='faculty')setRole('faculty');
})();
