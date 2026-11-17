/* Fail-closed configuration audit. Future API integration must replace this
 * check with actual documented server responses, not mock invented endpoints.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..');
const read=p=>fs.readFileSync(path.join(root,p),'utf8');
const cfgSrc=read('assets/beta-config.js');
const {betaConfig}=await import('data:text/javascript;base64,'+
  Buffer.from(cfgSrc).toString('base64'));
for(const key of ['sharedApiBaseUrl','apiDocumentationUrl','inviteSecretName',
  'retentionPolicy','deletionProcedure'])
  assert.equal(betaConfig[key],'',key+' must be unknown until operator provides it');
assert.equal(betaConfig.coachingTimeoutSec,null);
for(const key of ['apiContractVerified','corsVerified','studentResponseProjectionVerified'])
  assert.equal(betaConfig[key],false,key);
const js=read('assets/beta-home.js');
assert.doesNotMatch(js,/localStorage\.setItem|sessionStorage\.setItem|document\.cookie/);
assert.doesNotMatch(js,/fetch\(|XMLHttpRequest\(|navigator\.sendBeacon\(/);
const home=read('index.html');
for(const id of ['beta-invite-code','beta-file','beta-rights',
  'beta-retention-consent','beta-class-code','beta-join-submit',
  'beta-server-message','beta-upload-progress'])
  assert.ok(home.includes('id="'+id+'"'),id+' missing from homepage');
assert.match(home,/베타 서버|실제 베타/);
assert.match(home,/app\.html\?demo=student/);
assert.match(read('app.html'),/id="api-mode"[^>]*hidden/);
assert.match(read('app.html'),/pilot-dev\.js/);
assert.match(read('app.html'),/id="demo-workspace"/);
console.log('OK: unconfigured beta guarded; explicit demo; developer URL/JWT isolated; no fabricated API requests');
