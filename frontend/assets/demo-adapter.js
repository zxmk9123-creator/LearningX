/* Offline-only classroom demo adapter: no real parser, tokens or model calls. */
const KEY='learningx-demo-session-v1';
const CHANNEL='learningx-demo-sync-v1';
const ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const empty=()=>({version:1,upload:null,approved:[],code:null,questions:[],featuredId:null});
const clone=v=>JSON.parse(JSON.stringify(v));
export const eligible=b=>b.content_role==='lesson_content'&&b.quality?.status==='PASS';
export const normalizeCode=value=>String(value||'').replace(/[\s-]/g,'').toUpperCase();
export function levelOf(question){
  if(/왜|어떻게|적용|근거|만약|가정|영향|반례|검증|가능성/.test(question))return '깊은';
  if(/차이|정의|비교|관계|구분|공통|유사/.test(question))return '중간';
  return '얕은';
}
export function questionStems(level){
  if(level==='얕은')return [['비교하기','___와 ___는 어떤 점이 다를까요?'],['연결하기','___가 ___와 어떻게 연결되나요?'],['구분하기','___를 ___ 기준으로 나누면?']];
  if(level==='중간')return [['적용하기','___를 ___ 상황에 적용하면?'],['근거 점검','___라는 판단을 뒷받침할 근거는 ___인가요?'],['반례 탐색','만약 ___라면 ___라는 설명도 성립할까요?']];
  return [['조건 바꾸기','___라는 조건을 바꾸면 ___는 어떻게 달라질까요?'],['반례 확인','___에 반하는 사례가 있다면 ___를 어떻게 다시 설명할까요?'],['검증 설계','___라는 주장을 검증하려면 ___를 비교해야 할까요?']];
}
export class DemoAdapter{
  constructor(){
    this.listeners=new Set();this.state=empty();this.shared=false;
    try{
      const probe=KEY+'-probe';localStorage.setItem(probe,'1');localStorage.removeItem(probe);
      this.shared=true;this.state=this.read();
    }catch(_){this.shared=false;}
    try{
      if(this.shared&&typeof BroadcastChannel!=='undefined'){
        this.channel=new BroadcastChannel(CHANNEL);
        this.channel.onmessage=()=>this.pull();
      }
    }catch(_){this.channel=null;}
    window.addEventListener('storage',e=>{if(e.key===KEY)this.pull();});
  }
  read(){
    if(!this.shared)return this.state;
    try{
      const raw=localStorage.getItem(KEY);const s=raw?JSON.parse(raw):empty();
      return s&&s.version===1&&Array.isArray(s.approved)&&Array.isArray(s.questions)?s:empty();
    }catch(_){return this.state||empty();}
  }
  snapshot(){return clone(this.state);}
  subscribe(callback){this.listeners.add(callback);return()=>this.listeners.delete(callback);}
  emit(){for(const fn of this.listeners)fn(this.snapshot());}
  pull(){this.state=this.read();this.emit();}
  commit(change){
    const next=clone(this.shared?this.read():this.state);change(next);
    this.state=next;
    if(this.shared)try{localStorage.setItem(KEY,JSON.stringify(next));}catch(_){this.shared=false;}
    try{this.channel?.postMessage('changed');}catch(_){}
    this.emit();return this.snapshot();
  }
  reset(){
    this.state=empty();
    if(this.shared)try{localStorage.removeItem(KEY);}catch(_){this.shared=false;}
    try{this.channel?.postMessage('reset');}catch(_){}
    this.emit();
  }
  async example(){
    if(!this.examplePromise)this.examplePromise=fetch(new URL('../data/demo-nit.json',import.meta.url),{cache:'no-store'})
      .then(r=>{if(!r.ok)throw Error('가상 교안을 불러올 수 없습니다.');return r.json();});
    return this.examplePromise;
  }
  async digest(file){
    if(!globalThis.crypto?.subtle?.digest)throw Error('파일 해시는 HTTPS 또는 localhost에서만 계산할 수 있습니다.');
    const bytes=await file.arrayBuffer();
    const digest=await crypto.subtle.digest('SHA-256',bytes);
    return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');
  }
  recordUpload(metadata){this.commit(s=>{s.upload=metadata;s.approved=[];s.code=null;s.questions=[];s.featuredId=null;});}
  approve(ids,document){
    const allowed=new Set(document.pages.flatMap(p=>p.blocks.filter(eligible).map(b=>b.id)));
    const approved=[...new Set(ids.filter(id=>allowed.has(id)))];
    this.commit(s=>{if(s.upload){s.approved=approved;s.code=null;s.questions=[];s.featuredId=null;}});
    return approved;
  }
  start(){
    if(!this.state.upload||!this.state.approved.length)throw Error('학습 근거를 1쪽 이상 승인해야 수업을 시작할 수 있습니다.');
    if(this.state.code)return this.state.code;
    const bytes=crypto.getRandomValues(new Uint8Array(6));
    const code=[...bytes].map(x=>ALPHABET[x%ALPHABET.length]).join('');
    this.commit(s=>{s.code=code;});return code;
  }
  join(input){
    const code=normalizeCode(input),s=this.shared?this.read():this.state;
    if(code.length!==6||code!==s.code||!s.code||!s.approved.length)
      throw Error('수업 코드를 확인해 주세요. 존재하지 않거나 시작 전인 수업입니다.');
    return {code,title:'미디어와 사회 3주차 · 의제설정 이론'};
  }
  evidence(question,document){
    const approved=new Set(this.state.approved);const matches=[];
    for(const page of document.pages)for(const block of page.blocks){
      if(!eligible(block)||!approved.has(block.id))continue;
      if((block.keywords||[]).some(k=>question.toLowerCase().includes(k.toLowerCase())))
        matches.push({page:page.page,block_id:block.id,text:block.text});
    }
    return matches.slice(0,2);
  }
  submit({original,revised,level,evidence}){
    if(!this.state.code||!this.state.approved.length)throw Error('수업이 시작되지 않았습니다.');
    if(!revised.trim()||revised.includes('___'))throw Error('빈칸을 내 생각으로 채워 주세요');
    this.commit(s=>s.questions.push({id:crypto.randomUUID(),original,revised,level,
      evidence:evidence.map(e=>({page:e.page,block_id:e.block_id})),
      createdAt:new Date().toISOString(),anonymous:true}));
  }
  feature(id){this.commit(s=>{s.featuredId=s.questions.some(q=>q.id===id)?id:null;});}
}