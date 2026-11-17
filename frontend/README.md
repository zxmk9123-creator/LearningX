# LearningX 프론트엔드 프로토타입

사용자가 업로드한 `question-coach-v3.zip`의 여섯 페이지를 그대로 기반으로 구성했습니다. 외부 라이브러리 및 별도 빌드가 없는 정적 HTML/CSS/JS 사이트입니다.

## 기능 경계
- `index.html`, `why.html`, `cards.html`, `flow.html`, `levels.html`, `principles.html`: 기존 디자인의 소개·체험 페이지.
- `app.html`: 질문 작성 → 역질문 → 질문 재작성, 교수자 수업·교안·승인 화면.
- **데모 모드**: 가상 교안·근거·승인·역질문을 메모리에서 생성. 실제 분석·서명·LLM 호출·서버 저장은 하지 않습니다.
- **서버 연결 모드**: 사용자가 지정한 HTTPS 백엔드와 서버 발급 JWT를 사용하여 실제 LearningX `backend/learningx/api.py` API에 연결합니다. JWT는 메모리에만 보관하며 localStorage/sessionStorage/cookie에 저장하지 않습니다.
- 교수자 승인과 학생 질문은 실제 백엔드 및 frozen v1.6.2 엔진이 설치된 경우에만 작동합니다. 백엔드 미배포·SSO 미구현·실물 교안 승인 미완료 상태이므로 **이 화면 자체가 운영 가능한 제품은 아닙니다**.
- 원래 v3 시안에 있는 6자리 수업 코드·학생 익명 공개·질문 군집화·실시간 질문 분류는 아직 서버 API에 없으므로 데모/예정으로만 표기합니다.

## Vercel

- GitHub: `zxmk9123-creator/LearningX`
- Vercel 프로젝트 설정: Framework Preset **Other**, Root Directory **`frontend`**, Build Command **없음**, Output Directory **없음**.
- 정적 파일을 그대로 게시하며 `frontend/vercel.json`의 보안 헤더 적용.
- **프론트엔드 환경변수에 FreeLLMAPI Key, JWT 서명키, 교수자 승인 서명키를 넣지 않습니다.** 모든 민감 키는 실제 백엔드에서만 관리합니다.

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
