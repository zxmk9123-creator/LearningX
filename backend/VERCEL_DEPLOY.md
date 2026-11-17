# LearningX FastAPI → Vercel 배포

목표: **프론트엔드와 백엔드가 동일한 GitHub 저장소를 사용하되, 서로 다른 Vercel 프로젝트로 배포**. 기존 Oil&Fat API나 기존 LearningX 정적 사이트 프로젝트의 설정은 변경하지 않습니다.

## 프로젝트 설정

[Vercel 새 프로젝트](https://vercel.com/new)에서 GitHub `zxmk9123-creator/LearningX`를 Import:

| 설정 | 값 |
| --- | --- |
| Project Name | `learningx-api` (기존 `learning-x` 정적 웹과 구별) |
| Root Directory | **`backend`** |
| Framework | **FastAPI** (자동 감지; Other로 강제하지 않음) |
| Install/Build Command | Override 해제 |
| Output Directory | Override 해제 |
| Production Branch | `main` |
| Functions region | `sin1` (Neon Singapore와 동일, `backend/vercel.json`) |
| Python | `3.12` (`backend/.python-version`) |

`backend/app.py`는 실제 앱 `learningx.api:app`를 재사용하는 Vercel 공식 진입점입니다. `backend/vercel.json`은 함수 실행 리전만 지정하며 FastAPI 경로를 재작성하지 않습니다.

## 서버 환경변수

Vercel → learningx-api → Settings → Environment Variables에서 Production에 등록:

| 이름 | 값/획득 방법 |
| --- | --- |
| `DATABASE_URL` | Neon `learningx-pilot` → Connect → database `learningx` → **Direct** URL 복사; 현재 백엔드가 시작 시 SQLAlchemy `create_all`을 호출하므로 파일럿 초기에는 직접 연결 사용 |
| `NIT_JWT_SECRET` | 서로 다른 강력한 랜덤 키 생성 (`openssl rand -hex 32`) |
| `NIT_REVIEW_SIGNING_KEY` | JWT 키와 **다른** 랜덤 키 (`openssl rand -hex 32`) |
| `NIT_CORS_ORIGINS` | `https://learning-x-one.vercel.app` |
| `NIT_LLM_BASE_URL` | 본인의 FreeLLMAPI 라우터 OpenAI-compatible base URL (예: `https://<router>/v1`) |
| `NIT_LLM_MODEL` | `auto` (라우터가 지원하는 모델) |
| `NIT_LLM_API_KEY` | FreeLLMAPI Keys 페이지에서 발급한 현재 유효한 통합 API 키 |

**Vercel 정적 프론트엔드 환경변수로 비밀키를 보내지 마세요.** 프론트는 `/app.html` 서버 연결 모드에서 백엔드 URL과 **교수자/학생별 단기 JWT**만 메모리로 사용합니다.

`backend/app.py`가 `NIT_SERVERLESS_RUNTIME=1`을 내부에서 설정하므로 운영자가 이 변수를 직접 입력할 필요가 없습니다.

## 연결 검증 순서

배포가 READY인 후 생성된 `https://learningx-api-...vercel.app`에서:

1. `GET /health` → `{"status":"ok","service":"learningx-backend"}`
2. `GET /health/db` → `{"status":"ok","database":"connected"}` (실제 Neon SELECT 1)
3. 교수자 및 학생 계정 발급은 [backend/README.md](README.md)의 `learningx.admin` 운영 CLI 절차 사용. **로컬 관리자 작업에도 동일한 Neon 연결 주소와 JWT 서명키 필요**
4. Vercel 프론트엔드 `https://learning-x-one.vercel.app/app.html` → **서버 연결** → `learningx-api` 도메인과 JWT 입력

환경변수 변경은 새로운 배포를 해야 적용됩니다. SQLite가 실수로 사용되지 않도록 Vercel 서버 런타임에서는 `DATABASE_URL`이 없으면 서버 초기화를 거부합니다.

## 현재의 강제 차단 및 향후 필수 작업

**이 PR은 Vercel API + Neon 연결의 배포 준비이지, 교안 기반 AI 파일럿 전체 완성이 아닙니다.**

- Vercel Functions 파일시스템은 읽기 전용이고 `/tmp`만 임시로 쓸 수 있으므로, 현재 업로드 API는 `503 Private durable lesson storage is required for Vercel`로 **의도적으로 차단**됩니다.
- 검증된 NIT v1.6.2 + Pilot Patch 교안 엔진은 현재 저장소/배포에 포함되지 않아, 교수자 서명과 코칭 관련 API를 아직 사용할 수 없습니다.
- 다음 구현은 **비공개 영구 객체 저장소**(예: Neon Object Storage 또는 Vercel Blob)를 통해 원본 PDF/PPTX와 Canonical JSON 및 서명 아티팩트를 저장/조회하는 어댑터입니다. 백엔드가 서명 파일을 복원하고 검증해야 코칭을 활성화할 수 있습니다.
- 서버리스 함수 콜드 스타트가 0초인 것은 아닙니다. Render 무료 인스턴스의 장시간 재기동 대기를 피하면서, 온디맨드 FastAPI를 운영하는 선택입니다.
- 인증 토큰과 학생 데이터가 있으므로 **Oil&Fat 기존 Vercel API 프로젝트와 결합하지 않습니다.** 동일 Vercel 계정에서 별도 `learningx-api` 프로젝트로 격리합니다.

참고: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi), [Vercel Python Runtime](https://vercel.com/docs/functions/runtimes/python), [Functions Limitations](https://vercel.com/docs/functions/limitations).
