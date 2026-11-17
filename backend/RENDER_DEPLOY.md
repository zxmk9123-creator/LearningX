# LearningX → Render + Neon 배포 가이드

이 브랜치는 기존 Vercel 프론트엔드를 변경하지 않고 **Render FastAPI 웹 서비스**만 구성합니다.
DB는 이미 생성된 Neon `learningx-pilot / learningx`를 이용합니다. 비밀키와 교안 원문을 GitHub에 커밋하지 않습니다.

## 1. Render Blueprint 가져오기

1. [Render Dashboard](https://dashboard.render.com/)에서 **New → Blueprint** 선택
2. GitHub 저장소 `zxmk9123-creator/LearningX`를 연결
3. 저장소 루트의 `render.yaml`을 사용해 `learningx-api` 서비스 생성
4. 무료 인스턴스로 시작. 이 단계는 **API/DB 연결을 위한 인프라 데모**입니다.

Blueprint 설정:
- Region: Singapore
- Runtime: Python 3.12
- Root Directory: `backend`
- Build: `pip install -r requirements.txt`
- Start: `uvicorn learningx.api:app --host 0.0.0.0 --port $PORT`
- Health Check: `/health`
- Automatic deploy: commits to main

## 2. Render 환경변수 지정

| 이름 | 값/입력 방법 |
| --- | --- |
| `DATABASE_URL` | Neon `learningx-pilot` → **Connect** → `learningx` → Pooled connection에서 복사. 백엔드에서 `postgresql://`을 자동 변환함 |
| `NIT_JWT_SECRET` | Render가 생성한 고유 랜덤 값. 로컬 관리자 CLI에서 JWT 생성 시 같은 값 필요 |
| `NIT_REVIEW_SIGNING_KEY` | Render가 생성한 또 다른 랜덤 값. 승인 서명용이므로 JWT 키와 재사용하지 않음 |
| `NIT_CORS_ORIGINS` | `https://learning-x-one.vercel.app` (Blueprint 기본값) |
| `NIT_LLM_BASE_URL` | FreeLLMAPI 인스턴스의 OpenAI 호환 URL `https://<your-router>/v1` |
| `NIT_LLM_MODEL` | `auto` |
| `NIT_LLM_API_KEY` | FreeLLMAPI 대시보드 Keys에서 재발급한 Unified API Key (서버 측에서만) |
| `NIT_STORAGE_DIR` | 무료 서버 임시 경로 `/tmp/learningx-lessons` (교안 업로드 **차단**). 영구 디스크 준비 후 경로 교체 |
| `PYTHON_VERSION` | `3.12.9` |

Blueprint에 지정된 `sync: false` 변수는 **최초 생성 화면에서만 입력을 요청**합니다.
Render의 Environment 화면에서도 설정할 수 있습니다. 인증 키·DB 비밀번호를 브라우저 프론트엔드나 GitHub에 추가하지 마세요.

## 3. 서버 구동 확인

배포 상태가 **Live**로 전환된 뒤 생성된 `https://<render-service>.onrender.com` 주소에서:

- `GET /health` → `{"status":"ok","service":"learningx-backend"}`
- `GET /health/db` → `{"status":"ok","database":"connected"}`

두 번째 주소까지 성공해야 Neon 연결이 성립합니다.
`/health`만 성공했더라도 LLM 코칭과 업로드가 준비됐다는 뜻은 아닙니다.

## 4. 교수자·학생 계정 준비

초기 파일럿은 학교 SSO 대신 서버 발급 단기 JWT를 사용합니다.
서버와 동일한 `DATABASE_URL`, `NIT_JWT_SECRET`을 **관리자 로컬 실행 환경**에서만 설정한 뒤:

```bash
cd backend
pip install -r requirements.txt
python -m learningx.admin create-user --email faculty@example.edu --role faculty
python -m learningx.admin create-user --email student@example.edu --role student
python -m learningx.admin issue-token --email faculty@example.edu --minutes 60
python -m learningx.admin issue-token --email student@example.edu --minutes 60
```

출력된 토큰은 승인된 테스트 사용자에게만 전달하고 사용 후 폐기합니다.
`/app.html`에서 서버 연결 모드를 선택해 Render URL 및 단기 JWT를 입력합니다.
토큰은 프론트엔드의 메모리에만 유지되며 새로고침하면 지워집니다.

## 5. 실제 교안 분석·질문 코칭 활성화 — **현재 미완료**

검증된 `NIT_Pilot_Readiness_Minimal_Patch.zip` 엔진은 공개 저장소에 들어 있지 않습니다.
현재 배포한 API는 엔진 없이 `/health`, `/health/db`, 수업/권한 API까지만 작동하고, 업로드·서명·코칭은 **503으로 차단**합니다.

실제 교안 데모에는 다음 두 조건을 먼저 충족해야 합니다.

- **영구 비공개 저장소**: Render 유료 인스턴스의 persistent disk 등으로 `NIT_STORAGE_DIR` 설정. Render 무료 인스턴스의 임시 파일 시스템에 교안/서명 산출물을 보관하면 재시작 시 유실됩니다.
- **검증된 NIT 엔진 설치**: SHA-256이 고정된 설치 스크립트 `backend/scripts/install_frozen_engine.py`로 기존 ZIP 설치, `NIT_ENGINE_ROOT`를 설치 경로에 지정. 교안 원문과 API 키를 Git에 올리지 않습니다.

임시 저장을 운영 승인으로 간주하지 않습니다. `render.yaml`에서 무료 플랜을 유료 플랜으로 임의 변경하지 않습니다.

## 6. 파일럿 최종 점검

- 권한이 없는 학생은 수업 자료에 접근 불가
- 교수자 검토 후에만 근거 서명, 학생은 사전 승인한 증거 블록만 이용
- 25초 API 타임아웃 안전한 Fallback
- PDF/슬라이드 처리 결과의 비공개 저장, 재배포 후 데이터 지속성
- FreeLLMAPI 실모델 20문항 평가(원문 누설, 관계 정확성, 코칭 수준)

지금 단계의 Render Blueprints는 **실행 가능한 API 서버 구성**입니다. 교수자 승인·AI 실제 파일럿 완료를 뜻하지 않습니다.
