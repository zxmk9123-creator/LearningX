/* Shared-beta landing safeguard.
 * Do not invent API paths or response formats. The beta adapter may only be
 * activated after its operator documentation and real HTTP responses match.
 * This shell makes no network requests, never accepts secrets, and does not
 * silently send a user to the offline classroom demo.
 */
import {betaConfig} from './beta-config.js';

const $=id=>document.getElementById(id);
export const normalizeClassCode=value=>String(value||'')
  .replace(/[\s-]/g,'').toUpperCase().replace(/[^A-Z2-9]/g,'').slice(0,6);
export const validClassCode=value=>/^[A-HJ-NP-Z2-9]{6}$/.test(value);
const required=[
  'sharedApiBaseUrl','apiDocumentationUrl','inviteSecretName',
  'retentionPolicy','deletionProcedure','coachingTimeoutSec',
  'apiContractVerified','corsVerified','studentResponseProjectionVerified'
];
export function unresolvedSettings(c){
  const missing=required.filter(k=>c[k]===null||c[k]===false||c[k]==='');
  if(c.sharedApiBaseUrl){
    try{
      const u=new URL(c.sharedApiBaseUrl);
      if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash)
        missing.push('sharedApiBaseUrl:invalid-https');
    }catch(_){missing.push('sharedApiBaseUrl:invalid-url');}
  }
  return [...new Set(missing)];
}

// The transport contract is intentionally *not* implemented until the
// independent operator API spec and HTTP responses are reviewed.
// Never call guessed /health, /invite or /sessions paths.
const pending=unresolvedSettings(betaConfig);
const msg=!betaConfig.sharedApiBaseUrl
  ? '베타 서버 연결 준비 중 · 공유 서버 주소 미등록'
  : pending.length
    ? '베타 서버 계약 검증 대기 · 실제 업로드 비활성'
    : '베타 서버 연동 코드 검증 대기 · 실제 업로드 비활성';

$('beta-server-message').textContent=msg;
$('beta-server-help').textContent=
  '운영진 API 문서·실제 응답·학생 데이터 보호·보관/삭제 정책 확인 전입니다. 초대 코드나 강의 자료를 입력·전송하지 마세요.';
$('beta-student-note').textContent=
  '현재 '+msg+'입니다. 데모 체험은 별도 버튼으로만 시작합니다.';
$('beta-retention').textContent=betaConfig.retentionPolicy||'운영 정책 미정';
$('beta-deletion').textContent=betaConfig.deletionProcedure||'운영 정책 미정';

// Even if somebody accidentally sets every public config field, this
// pre-integration build must remain closed until a verified API client exists.
for(const id of ['beta-invite-code','beta-invite-submit','beta-rights',
  'beta-retention-consent','beta-file','beta-upload-cancel',
  'beta-upload-retry','beta-join-submit']){
  $(id).disabled=true;
}

const student=$('beta-class-code');
const params=new URLSearchParams(location.search);
if(params.has('code'))student.value=normalizeClassCode(params.get('code'));
student.addEventListener('input',()=>{
  student.value=normalizeClassCode(student.value);
  student.removeAttribute('aria-invalid');
  $('beta-code-error').hidden=true;
});
$('beta-join-form').addEventListener('submit',event=>{
  event.preventDefault();
  const code=normalizeClassCode(student.value);
  const error=validClassCode(code)
    ? '베타 서버 연결 전에는 실제 수업에 입장할 수 없습니다. 데모는 별도 버튼에서 이용하세요.'
    : '영문 대문자·숫자 6자리 수업 코드를 입력해 주세요 (O·0·I·1 제외).';
  student.setAttribute('aria-invalid','true');
  $('beta-code-error').textContent=error;
  $('beta-code-error').hidden=false;
});
$('beta-invite-form').addEventListener('submit',event=>{
  event.preventDefault();
  $('beta-invite-error').textContent='서버 검증 전에는 초대 코드를 입력하거나 전송하지 않습니다.';
  $('beta-invite-error').hidden=false;
});
// No localStorage, cookies, fictitious responses, or server calls.
