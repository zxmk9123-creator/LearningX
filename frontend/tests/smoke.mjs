import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const root=path.resolve(import.meta.dirname,'..');
const pages=['index.html','why.html','cards.html','flow.html','levels.html','principles.html','app.html'];
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
assert.ok(cfg.headers[0].headers.some(h=>h.key==='Content-Security-Policy'));
console.log(`OK: ${pages.length} linked HTML pages, app contract hooks, token hygiene, Vercel headers`);
