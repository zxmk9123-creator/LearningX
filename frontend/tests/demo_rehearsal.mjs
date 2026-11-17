/* Fixed rehearsal cases, evaluated against the actual offline demo adapter.
 * This is a rule-engine test, NOT an end-to-end production API security test.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const code=fs.readFileSync(path.join(root,'assets/demo-adapter.js'),'utf8');
const {DemoAdapter,eligible,levelOf,questionStems}=await import(
  'data:text/javascript;base64,'+Buffer.from(code,'utf8').toString('base64')
);
const doc=JSON.parse(fs.readFileSync(path.join(root,'data/demo-nit.json'),'utf8'));
const approved=['p1:concept'];
const prompts=[
  {level:'얕은',question:'의제설정 이론은 무엇을 설명하나요?',firstChip:'비교하기'},
  {level:'중간',question:'뉴스 보도와 의제설정 이론의 차이는 무엇인가요?',firstChip:'적용하기'},
  {level:'깊은',question:'왜 뉴스가 의제설정에 영향을 준다고 볼 수 있나요?',firstChip:'조건 바꾸기'},
];
const allowed=new Set(doc.pages.flatMap(p=>p.blocks.filter(eligible).map(b=>b.id)));
assert.equal(doc.pages.length,10,'fictional example has ten pages');
assert.equal(allowed.size,3,'three blocks are quality-passed learning content');
assert.equal(doc.pages.flatMap(p=>p.blocks).length-allowed.size,7,'seven blocks must remain excluded');
for(const row of prompts){
  const classification=levelOf(row.question);
  assert.equal(classification,row.level,row.question);
  const stems=questionStems(classification);
  assert.ok(stems.length>=2&&stems.length<=3);
  assert.equal(stems[0][0],row.firstChip);
  assert.ok(stems.every(([,pattern])=>pattern.includes('___')),'never pre-fill student answers');
  const refs=DemoAdapter.prototype.evidence.call({state:{approved}},row.question,doc);
  assert.deepEqual(refs.map(r=>r.page),[1],'one approved page is the only permitted citation');
  assert.ok(refs.every(r=>approved.includes(r.block_id)));
  console.log('PASS '+row.level+': '+row.question+' → '+stems[0][0]+' · p.1');
}
const noRefs=DemoAdapter.prototype.evidence.call(
  {state:{approved}},'달 탐사선의 추진 방식은 무엇인가요?',doc
);
assert.deepEqual(noRefs,[],'unrelated question does not invent a document source');
const closed=DemoAdapter.prototype.evidence.call(
  {state:{approved:[]}},prompts[0].question,doc
);
assert.deepEqual(closed,[],'without faculty approval, no source can be cited');
console.log('OK: 3 fixed rehearsal classifications, stems, approved-only citations, no fabricated sources');
