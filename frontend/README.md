# LearningX 프론트엔드 프로토타입

사용자가 업로드한 `question-coach-v3.zip`의 여섯 페이지를 그대로 기반으로 구성했습니다. 외부 라이브러리 및 별도 빌드가 없는 정적 HTML/CSS/JS 사이트입니다.

## 기능 경계
- `index.html`, `why.html`, `cards.html`, `flow.html`, `levels.html`, `principles.html`: 기존 디자인의 소개·체험 페이지.
- `app.html`: 질문 작성 → 역질문 → 질문 재작성, 교수자 수업·교안·승인 화면.
- **데모 모드**: 가상 교안·근거·승인·역질문을 메모리에서 생성. 실제 분석·서명·LLM 호출·서버 저장은 하지 않습니다.
- **서버 연결 모드**: 사용자가 지정한 HTTPS 백엔드와 서버 발급 JWT를 사용하여 실제 LearningX `backend/learningx/api.py` API에 연결합니다. JWT는 메모리에만 보관하며 localStorage/sessionStorage/cookie에 저장하지 않습니다.
- 교수자 승인과 학생 질문은 실제 백엔드 및 frozen v1.6.2 엔진이 설치된 경우에만 작동합니다. 백엔드 미배포·SSO 미구현·실물 교안 승인 미완료 상태이므로 **이 화면 자체가 운영 가능한 제품은 아닙니다**.
- 원래 v3 시안에 있는 6자리 수업 코드·학생 익명 공개·질문 군집화·실시간 질문 분류는 아직 서버 API에 없으므로 데모/예정으로만 표기합니다.

## Question-up 리디자인 화면 (feat/question-up-redesign-mvp)

| 파일 | 역할 |
|---|---|
| `index.html` | 진입: 학생 코드 입장 / 교수자 시작 (기존 소개는 `intro.html`) |
| `teacher.html` + `assets/qu-teacher.js` | Google 로그인 → 내 수업 → 교안 올리기 → 검토·승인(AI 코칭 동의) → 수업 중 질문 보드(5초 폴링, 수준 필터, 숨기기) → 화면에 띄우기 → 종료·CSV |
| `student.html` + `assets/qu-student.js`, `assets/qu-plant.js` | 코드 입장(익명) → 대화형 질문 코치 + 콩나무 → 교수자에게 보내기 → 내 질문 |
| `assets/qu.css`, `assets/qu-api.js` | 디자인 사양 토큰(세이지 팔레트, Freesentation) · 공용 API 클라이언트 |

새 백엔드 엔드포인트는 `backend/learningx/classroom.py`(`/v1/courses/{id}/sessions`, `/v1/join`, `/v1/sessions/{id}/board` 등)에 있습니다.
토큰은 메모리에만 두며, CSP를 지키기 위해 인라인 스크립트·style 속성을 쓰지 않습니다.
아직 포함하지 않은 기능: 실시간 슬라이드 송출·동기화, 헷갈려요/사례 더 게이지, 드래그 하이라이트, AI 자동 필터, 실명·SSO, 실시간 푸시(현재 폴링).

## Vercel

- GitHub: `zxmk9123-creator/LearningX`
- Vercel 프로젝트 설정: Framework Preset **Other**, Root Directory **`frontend`**, Build Command **없음**, Output Directory **없음**.
- 정적 파일을 그대로 게시하며 `frontend/vercel.json`의 보안 헤더 적용.
- **프론트엔드 환경변수에 FreeLLMAPI Key, JWT 서명키, 교수자 승인 서명키를 넣지 않습니다.** 모든 민감 키는 실제 백엔드에서만 관리합니다.

> **배포 재실행:** Vercel 프로젝트 Root Directory를 `frontend`로 변경한 뒤 GitHub `main`에 새 커밋을 푸시하면 연결된 Vercel Git Integration이 현재 설정으로 새로운 배포를 생성합니다. 프론트엔드 프로젝트만 배포하며 `backend/`는 포함하지 않습니다.

## Vercel 배포 오류: FASTAPI_ENTRYPOINT_NOT_FOUND

이 에러가 `learningx/api.py`를 찾았다고 표시하면 Vercel이 **정적 프론트엔드가 아닌 `backend/`에서 FastAPI를 빌드**하려는 상태일 가능성이 높습니다. 해당 에러를 해결하려고 백엔드에 `main.py`를 새로 만들지 마세요.

1. Vercel 프로젝트 → **Settings → Build and Deployment**
2. **Root Directory: `frontend`** (반드시 정확한 상대 경로)
3. **Framework Preset: Other** (FastAPI가 아님)
4. **Build Command: 공란/Override 해제**, **Output Directory: 기본값/Override 해제**
5. 설정 저장 후 **Deployments → Redeploy** (설정 변경은 다음 배포에 적용)
6. 사이트의 `/`과 `/app.html`이 모두 HTML 200을 반환하는지 확인

`frontend/vercel.json`의 `"framework": null`도 해당 디렉터리에서 배포할 때 Other 프레임워크를 명시합니다. **이 파일은 잘못된 Root Directory가 `backend`로 잡힌 문제를 자동 해결하지 못합니다.** 대시보드의 Root Directory를 먼저 수정해야 합니다.

프론트엔드와 백엔드를 둘 다 Vercel에 배포하고 싶다면 **서로 다른 Vercel 프로젝트**가 필요합니다. 그러나 현재 백엔드는 영구 스토리지, Frozen NIT Engine, 인증 비밀키가 준비되지 않았으므로 FastAPI를 프론트엔드 대신 배포하지 않습니다.

## 로컬 점검

```bash
cd frontend
python -m http.server 8001
# http://localhost:8001/
node --check assets/app.js
node --check assets/pilot.js
node tests/smoke.mjs
```

## 백엔드 연결 점검

1. FastAPI 백엔드와 Frozen NIT 엔진·DB·스토리지를 별도 환경에 설치합니다.
2. 백엔드 `NIT_CORS_ORIGINS`에 정식 Vercel 배포 도메인을 등록합니다.
3. 서버 관리자가 `python -m learningx.admin issue-token ...`으로 단기 토큰을 발급합니다.
4. `/app.html`의 **백엔드 연결**에서 주소와 토큰을 넣습니다.
5. 교수자 수업 생성·학생 등록·교안 처리·후보 승인·수업 정책 설정 후 학생 계정으로 역질문과 수정 기록을 확인합니다.

임시 토큰 발급은 파일럿 관리자 전용이며 추후 학교 SSO/OIDC로 교체해야 합니다. 학생 식별 및 권한은 서버에서만 결정합니다.
