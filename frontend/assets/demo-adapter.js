/* LearningX demo adapter only: zero server/AI calls, zero file contents persisted. */
const STORAGE_KEY = 'learningx-demo-session-v1';
const CHANNEL = 'learningx-demo-sync-v1';
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const blank = () => ({version:1,upload:null,approved:[],code:null,questions:[],featuredId:null});
const clone = value => JSON.parse(JSON.stringify(value));

export const eligible = block => block.content_role === 'lesson_content' && block.quality?.status === 'PASS';
export const normalizeCode = value => String(value || '').replace(/[\s-]/g,'').toUpperCase();
export const levelOf = question => {
  if (/왜|어떻게|적용|근거|만약|가정|영향|반례|검증|가능성/.test(question)) return '깊은';
  if (/차이|정의|비교|관계|구분|공통|유사/.test(question)) return '중간';
  if (/누가|언제|무엇|어디|몇|어떤|뭐/.test(question)) return '얕은';
  return '얕은';
};
export const questionStems = level => (level === '얕은'
  ? [ ['비교하기', '___와 ___는 어떤 점이 다를까요?'], ['연결하기', '___가 ___와 어떻게 연결되나요?'], ['구분하기', '___를 ___ 기준으로 나누면?'] ]
  : level === '중간'
  ? [ ['적용하기', '___를 ___ 상황에 적용하면?'], ['근거 점검', '___라는 판단을 뒷받침할 근거는 ___인가요?'], ['반례 탐색', '만약 ___라면 ___라는 설명도 성립할까요?'] ]
  : [ ['조건 바꾸기', '___라는 조건을 바꾸면 ___는 어떻게 달라질까요?'], ['반례 확인', '___에 반하는 사례가 있다면 ___를 어떻게 다시 설명할까요?'], ['검증 설계', '___라는 주장을 검증하려면 ___를 비교해야 할까요?'] ]
);

export class DemoAdapter {
  constructor() {
    this.listeners = new Set();
    this.state = blank();
    this.shared = false;
    try {
      const probe = STORAGE_KEY + '-probe';
      localStorage.setItem(probe,'ok');
      localStorage.removeItem(probe);
      this.shared = true;
      this.state = this.read();
    } catch (_) { this.shared = false; }
    try {
      if (this.shared && typeof BroadcastChannel !== 'undefined') {
        this.channel = new BroadcastChannel(CHANNEL);
        this.channel.onmessage = () => this.pull();
      }
    } catch (_) { this.channel = null; }
    window.addEventListener('storage', event => {if (event.key === STORAGE_KEY) this.pull();});
  }
  read() {
    if (!this.shared) return this.state;
    try {
      const text = localStorage.getItem(STORAGE_KEY);
      const value = text ? JSON.parse(text) : blank();
      return value && value.version === 1 && Array.isArray(value.questions) && Array.isArray(value.approved)
        ? value : blank();
    } catch (_) { return this.state || blank(); }
  }
  snapshot() { return clone(this.state); }
  subscribe(listener) {this.listeners.add(listener); return () => this.listeners.delete(listener);}
  emit() {for (const listener of this.listeners) listener(this.snapshot());}
  pull() {this.state = this.read(); this.emit();}
  commit(mutator) {
    const next = clone(this.shared ? this.read() : this.state);
    mutator(next);
    this.state = next;
    if (this.shared) {
      try {localStorage.setItem(STORAGE_KEY,JSON.stringify(next));}
      catch (_) {this.shared = false;}
    }
    try {this.channel?.postMessage('changed');}catch (_) {}
    this.emit();
    return this.snapshot();
  }
  reset() {
    this.state = blank();
    if (this.shared) {
      try {localStorage.removeItem(STORAGE_KEY);} catch (_) {this.shared = false;}
    }
    try {this.channel?.postMessage('reset');}catch (_) {}
    this.emit();
  }
  async example() {
    if (!this.examplePromise) this.examplePromise = fetch(new URL('../data/demo-nit.json',import.meta.url),{cache:'no-store'})
      .then(response => {if (!response.ok) throw new Error('가상 교안 데이터를 불러올 수 없습니다.');return response.json();});
    return this.examplePromise;
  }
  async digest(file) {
    if (!crypto?.subtle?.digest) throw new Error('SHA-256을 계산하려면 HTTPS 또는 localhost가 필요합니다.');
    const buffer=await file.arrayBuffer();
    const hash=await crypto.subtle.digest('SHA-256',buffer);
    return Array.from(new Uint8Array(hash),byte => byte.toString(16).padStart(2,'0')).join('');
  }
  recordUpload({name,size,sha256}) {
    this.commit(state => {
      state.upload = {name,size,sha256};
      state.approved=[];state.code=null;state.questions=[];state.featuredId=null;
    });
  }
  approve(blockIds,document) {
    const valid = new Set(document.pages.flatMap(page => page.blocks.filter(eligible).map(block => block.id)));
    const approved = Array.from(new Set(blockIds.filter(id => valid.has(id))));
    this.commit(state => {if (state.upload) {state.approved=approved;state.code=null;state.questions=[];state.featuredId=null;}});
    return approved;
  }
  start() {
    if (!this.state.upload || !this.state.approved.length) throw new Error('학습 근거를 1쪽 이상 승인해야 수업을 시작할 수 있습니다.');
    if (this.state.code) return this.state.code;
    const bytes=new Uint8Array(6);crypto.getRandomValues(bytes);
    const code=Array.from(bytes,n => ALPHABET[n % ALPHABET.length]).join('');
    this.commit(state => {state.code=code;});
    return code;
  }
  join(input) {
    const code = normalizeCode(input);
    const state=this.shared?this.read():this.state;
    if (code.length !== 6 || !state.code || code !== state.code || !state.approved.length) {
      throw new Error('수업 코드를 확인해 주세요. 존재하지 않거나 시작 전인 수업입니다.');
    }
    return {code, title:'미디어와 사회 3주차 · 의제설정 이론'};
  }
  evidence(question,document) {
    const approved=new Set(this.state.approved);
    const matches=[];
    for (const page of document.pages) {
      for (const block of page.blocks) {
        if (!eligible(block) || !approved.has(block.id)) continue;
        if ((block.keywords || []).some(keyword => question.toLowerCase().includes(keyword.toLowerCase()))) {
          matches.push({page:page.page,block_id:block.id,text:block.text});
        }
      }
    }
    return matches.slice(0,2);
  }
  submit({original,revised,level,evidence}) {
    if (!this.state.code || !this.state.approved.length) throw new Error('수업이 시작되지 않았습니다.');
    if (!revised.trim() || revised.includes('___')) throw new Error('빈칸을 내 생각으로 채워 주세요');
    this.commit(state => state.questions.push({
      id:crypto.randomUUID(),original,revised,level,
      evidence:evidence.map(item => ({page:item.page,block_id:item.block_id})),
      createdAt:new Date().toISOString(),anonymous:true
    }));
  }
  feature(id) {this.commit(state => {state.featuredId=state.questions.some(q=>q.id===id)?id:null;});}
}