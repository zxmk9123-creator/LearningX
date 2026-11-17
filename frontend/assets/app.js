(function(){
  /* 모바일 메뉴: 현재 페이지 탭을 보이는 위치로 */
  var cur=document.querySelector('.mnav [aria-current=page]');
  if(cur&&cur.parentNode.scrollWidth>cur.parentNode.clientWidth){cur.parentNode.scrollLeft=cur.offsetLeft-(cur.parentNode.clientWidth-cur.offsetWidth)/2;}

  var timer;
  function toast(msg){
    var t=document.getElementById('toast');
    t.textContent=msg;t.classList.add('show');
    clearTimeout(timer);timer=setTimeout(function(){t.classList.remove('show')},2400);
  }
  document.querySelectorAll('[data-toast]').forEach(function(b){
    b.addEventListener('click',function(){toast(b.dataset.toast)});
  });

  if(document.querySelector('[role=tab]')){
  /* 탭: ARIA 탭 패턴 (좌우 화살표, Home/End) */
  var tabs=[].slice.call(document.querySelectorAll('[role=tab]'));
  function selectTab(tab,focus){
    tabs.forEach(function(t){
      var on=t===tab;
      t.setAttribute('aria-selected',on);t.tabIndex=on?0:-1;
      document.getElementById(t.getAttribute('aria-controls')).hidden=!on;
    });
    if(focus)tab.focus();
  }
  tabs.forEach(function(t,i){
    t.addEventListener('click',function(){selectTab(t)});
    t.addEventListener('keydown',function(e){
      var n=null;
      if(e.key==='ArrowRight')n=tabs[(i+1)%tabs.length];
      if(e.key==='ArrowLeft')n=tabs[(i-1+tabs.length)%tabs.length];
      if(e.key==='Home')n=tabs[0];
      if(e.key==='End')n=tabs[tabs.length-1];
      if(n){e.preventDefault();selectTab(n,true);}
    });
  });
  }

  if(document.getElementById('join')){
  /* 수업 코드 */
  var code=document.getElementById('code'),codeErr=document.getElementById('code-err');
  code.addEventListener('input',function(){
    code.value=code.value.replace(/[^a-z0-9]/gi,'').toUpperCase().slice(0,6);
    code.removeAttribute('aria-invalid');codeErr.textContent='';
  });
  document.getElementById('join').addEventListener('submit',function(e){
    e.preventDefault();
    if(code.value.length!==6){
      code.setAttribute('aria-invalid','true');
      codeErr.textContent='수업 코드는 영문·숫자 6자리예요';code.focus();return;
    }
    window.location.href='app.html?demo=student';
  });
  }

  if(document.getElementById('file')){
  /* 교안 업로드 (데모) */
  var file=document.getElementById('file'),drop=document.getElementById('drop');
  function picked(f){
    if(!f)return;
    if(!/\.(pdf|pptx?)$/i.test(f.name)){toast('PDF 또는 PPT 파일만 올릴 수 있어요');return;}
    document.getElementById('file-name').textContent=f.name;
    document.getElementById('file-hint').textContent='다른 파일로 바꾸려면 다시 누르세요';
    document.getElementById('steps').hidden=false;
    document.getElementById('start').disabled=false;
  }
  file.addEventListener('change',function(){picked(file.files[0])});
  ['dragenter','dragover'].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.add('over')})});
  ['dragleave','drop'].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.remove('over')})});
  drop.addEventListener('drop',function(e){picked(e.dataTransfer.files[0])});
  document.getElementById('start').addEventListener('click',function(){
    window.location.href='app.html?demo=faculty';
  });
  }

  if(document.getElementById('deck')){
  /* AI 경각심 카드: 앞뒤 뒤집기 없이 모든 정보를 한 면에 */
  var CARDS=[
    {n:'01',t:'t1',tl:'공식 표명',h:'답은 AI가, 질문은 내가',s:'책임',ss:'AI 결과물의 책임은 사용자에게',b:'AI는 1초 만에 답한다. 하지만 무엇을 물을지, 그 답이 맞는지, 결과를 누가 책임질지는 정하지 못한다.',q:'나는 지금 무엇을 알고 싶은가?',src:'교수신문 · 2026 AI 윤리 가이드라인 3종',u:'https://www.kyosu.net/news/articleView.html?idxno=211514',r:1},
    {n:'02',t:'t0',tl:'이론',h:'표면 아래를 묻는다',s:'5단계',ss:'현상 → 목적 → 원인 → 검증 → 책임',b:'좋은 질문은 좋은 해결안으로 가는 길이다. 현상에서 멈추지 말고 목적과 근본 원인까지 내려가 묻는다.',q:'왜? 그건 또 왜?',src:'듀이 · 5 Whys · 소크라테스 · 포퍼',u:'why.html',r:9},
    {n:'03',t:'t2',tl:'동료심사 연구',h:'AI는 틀리고, 틀린 줄 모른다',s:'69~88%',ss:'판례 질문에서 지어낸 답의 비율',b:'실제 판례를 구체적으로 물었을 때 LLM 답변의 69~88%가 환각이었다. 잘못된 전제를 담은 질문도 바로잡지 못했다.',q:'이 답의 근거를 직접 확인했나?',src:'Dahl 외(2024), Journal of Legal Analysis',u:'https://law.stanford.edu/publications/large-legal-fictions-profiling-legal-hallucinations-in-large-language-models',r:4},
    {n:'04',t:'t2',tl:'동료심사 연구',h:'편하게 쓰면 실력이 준다',s:'−17%',ss:'답을 주는 AI로 공부한 뒤 시험 점수',b:'고교생 약 1,000명 실험. 답을 바로 주는 AI로 연습하면 연습 점수는 48% 올랐지만, AI 없이 본 시험은 17% 낮았다. 힌트만 준 AI는 손실이 없었다.',q:'답을 받았나, 생각을 했나?',src:'Bastani 외(2025), PNAS',u:'https://knowledge.wharton.upenn.edu/article/without-guardrails-generative-ai-can-harm-education/',r:5},
    {n:'05',t:'t2',tl:'동료심사 연구',h:'믿을수록 덜 생각한다',s:'936건',ss:'지식노동자 319명의 실제 AI 사용 사례',b:'AI를 신뢰할수록 비판적 사고를 덜 했다. 사고의 중심은 ‘직접 하기’에서 ‘검증하고 책임지기’로 옮겨 가고 있다.',q:'이걸 틀리게 만드는 반례는?',src:'Lee 외(2025), CHI · Microsoft Research',u:'https://advait.org/files/lee_2025_ai_critical_thinking_survey.pdf',r:6},
    {n:'06',t:'t3',tl:'보도 사례',h:'전문가도 검증하지 않으면 틀린다',s:'AU$44만',ss:'AI 오류가 발견된 정부 용역 보고서',b:'딜로이트 호주의 정부 보고서에 존재하지 않는 인용과 가짜 판결 인용이 들어갔고, 계약금 일부를 돌려줬다.',q:'출처를 직접 열어 봤나?',src:'The Register(2025.10)',u:'https://www.theregister.com/2025/10/06/deloitte_ai_report_australia/',r:7},
    {n:'07',t:'t3',tl:'보도 사례',h:'답만 얻는 시대의 시험',s:'7초',ss:'AI 안경으로 수능 수학 문제를 푼 시연',b:'AI 안경 시연이 퍼지고 자격시험 부정행위 의혹이 나오자 정부가 수능 범정부 긴급 대응팀을 꾸렸다. 답을 묻는 평가는 흔들린다.',q:'나는 어떤 질문을 할 수 있는 사람인가?',src:'데일리안 · 유니콘팩토리(2026.11.16)',u:'https://www.dailian.co.kr/news/view/1715550',r:8,flag:'부정행위 건은 수사·확인 전 의혹 단계입니다'},
    {n:'08',t:'t1',tl:'공식 표명',h:'쓴 사실은 밝히고, 책임은 내가',s:'5대 원칙',ss:'대학 AI 활용 윤리 가이드라인(2026.8)',b:'과제·발표에 생성형 AI를 쓰면 사용 사실과 출처를 밝혀야 한다. AI는 저자가 될 수 없고, 결과의 책임은 사용자에게 있다.',q:'이 결과를 내 이름으로 책임질 수 있나?',src:'뉴스1(2026.8.28) · 교육부·대교협',u:'https://www.news1.kr/society/education/6271700',r:2}
  ];
  var deck=document.getElementById('deck'),filt='all',sort='story';
  function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
  function render(){
    var list=CARDS.slice(),shown=0;
    if(sort==='trust')list.sort(function(a,b){return a.r-b.r});
    deck.innerHTML=list.map(function(c){
      var show=filt==='all'||c.t===filt;if(show)shown++;
      var ext=c.u.charAt(0)!=='#';
      return '<article class="card ac"'+(show?'':' hidden')+' aria-labelledby="ac-'+c.n+'">'+
        '<div class="top"><span class="no">'+c.n+'</span><span class="tier '+c.t+'">'+esc(c.tl)+'</span></div>'+
        '<h3 id="ac-'+c.n+'">'+esc(c.h)+'</h3>'+
        '<div class="stat">'+esc(c.s)+'<small>'+esc(c.ss)+'</small></div>'+
        '<p>'+esc(c.b)+'</p>'+
        (c.flag?'<span class="flag">'+esc(c.flag)+'</span>':'')+
        '<div class="self"><small>나에게 던질 질문</small>“'+esc(c.q)+'”</div>'+
        '<span class="from">출처 · <a href="'+esc(c.u)+'"'+(ext?' target="_blank" rel="noopener"':'')+'>'+esc(c.src)+'</a></span>'+
      '</article>';
    }).join('');
    document.getElementById('deck-status').textContent='카드 '+shown+'장 표시';
  }
  document.querySelectorAll('.chip').forEach(function(c){
    c.addEventListener('click',function(){
      var grp=c.hasAttribute('data-f')?'[data-f]':'[data-s]';
      document.querySelectorAll('.chip'+grp).forEach(function(x){x.setAttribute('aria-pressed','false')});
      c.setAttribute('aria-pressed','true');
      if(c.dataset.f)filt=c.dataset.f;else sort=c.dataset.s;
      render();
    });
  });
  render();
  }

  if(document.getElementById('checklist')){
  /* 체크리스트 진행률 */
  var boxes=document.querySelectorAll('#checklist input');
  boxes.forEach(function(x){x.addEventListener('change',function(){
    var n=[].filter.call(boxes,function(y){return y.checked}).length;
    document.getElementById('meter-bar').style.width=(n/boxes.length*100)+'%';
    document.getElementById('meter-text').textContent=n===boxes.length?'모두 확인했어요. 이제 내 이름으로 낼 수 있습니다':n+' / '+boxes.length+' 확인';
  })});
  }

  if(document.querySelector('.cite')){
  /* 출처 칩 */
  var cite=document.querySelector('.cite'),pop=document.getElementById('cite-pop');
  cite.addEventListener('click',function(){
    var open=cite.getAttribute('aria-expanded')==='true';
    cite.setAttribute('aria-expanded',!open);pop.hidden=open;
  });

  /* 줄기 칩 → 학생이 빈칸을 직접 채워 완성 */
  var compose=document.getElementById('compose'),q2=document.getElementById('q2'),
      q2err=document.getElementById('q2-err'),q2count=document.getElementById('q2-count'),
      sent=document.getElementById('sent');
  document.querySelectorAll('.stem').forEach(function(s){
    s.addEventListener('click',function(){
      document.querySelectorAll('.stem').forEach(function(x){x.setAttribute('aria-pressed','false')});
      s.setAttribute('aria-pressed','true');
      compose.hidden=false;sent.hidden=true;
      q2.value=s.dataset.t;q2.removeAttribute('aria-invalid');q2err.textContent='';
      q2count.textContent=q2.value.length+' / 200';
      q2.focus();
      var i=q2.value.indexOf('___');if(i>-1)q2.setSelectionRange(i,i+3);
    });
  });
  q2.addEventListener('input',function(){
    q2count.textContent=q2.value.length+' / 200';
    q2.removeAttribute('aria-invalid');q2err.textContent='';
  });
  compose.addEventListener('submit',function(e){
    e.preventDefault();
    var v=q2.value.trim();
    if(v.indexOf('___')>-1){q2.setAttribute('aria-invalid','true');q2err.textContent='빈칸(___)을 내 생각으로 채워 주세요';q2.focus();return;}
    if(v.length<10){q2.setAttribute('aria-invalid','true');q2err.textContent='질문을 조금 더 구체적으로 써 주세요';q2.focus();return;}
    compose.hidden=true;sent.hidden=false;
    sent.textContent='✓ 질문을 수정한 예시입니다. 실제 제출이나 저장은 되지 않습니다.';
  });
  }

})();
