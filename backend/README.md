# LearningX / NIT 백엔드 MVP

교수자 서명 교안만을 근거로 학생의 **질문 → 역질문 → 직접 수정**을 지원하는 FastAPI 백엔드입니다.

## 현재 지원 범위
- **서버 신원 검증**: HS256 단기 액세스 토큰, DB에 존재하는 활성 사용자만 허용
- **과목 ACL**: 교수자 자료 등록/승인, 학생은 자신이 등록된 과목의 코칭만 가능
- **교안 업로드**: PDF/PPTX (25MB), SHA-256 확인 및 v1.6.2 ingest adapter
- **교수자 서명**: v1.6.2 `verified_coaching.approve` 호출. 수업 AI 허용 플래그는 서명과 별개
- **코칭**: Frozen runtime의 허용 근거 검색/미승인 페이지 차단/JSON 검증/타임아웃 Fallback
- **데이터**: PostgreSQL 권장, 개발용 SQLite; 질문/수정 이력과 감사 이벤트 기록

## 중요: Frozen Engine 의존성

이 저장소에는 v1.6.2 전체 엔진 코드 **아직 포함하지 않았습니다**. 검증이 끝난 원본 NIT 패키지를 서버의 비공개 경로에 설치하고 `NIT_ENGINE_ROOT`를 지정해야 합니다. 아래 구조가 존재해야 합니다.

```text
NIT_ENGINE_ROOT/
  scripts/ingest.py
  scripts/evidence_gate.py
  scripts/verified_coaching.py
  runtime/coaching.py
  runtime/gateway.py
```

파일을 교체하거나 간이 파서로 우회하지 마세요. 엔진 파일이 없으면 업로드·승인·코칭 API는 **503 fail-closed**입니다. 기존 교안과 사용자 업로드 파일은 공개 GitHub 저장소에 올리지 않습니다.

## 로컬 실행

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NIT_JWT_SECRET='CHANGE_TO_STRONG_RANDOM_SECRET_AT_LEAST_32_BYTES'
export NIT_REVIEW_SIGNING_KEY='CHANGE_TO_ANOTHER_STRONG_RANDOM_SECRET_AT_LEAST_32_BYTES'
export NIT_ENGINE_ROOT='/absolute/path/to/nit-runtime-pilot-readiness'
export NIT_STORAGE_DIR='/private/learningx-lessons'
export DATABASE_URL='sqlite:///./learningx_dev.sqlite3'
export NIT_LLM_BASE_URL='https://YOUR_PROVIDER/v1'
export NIT_LLM_MODEL='auto'
export NIT_LLM_API_KEY='SET_SERVER_SIDE_ONLY'
python -m learningx.admin create-user --email professor@example.edu --role faculty
python -m learningx.admin create-user --email student@example.edu --role student
python -m learningx.admin issue-token --email professor@example.edu --minutes 60
uvicorn learningx.api:app --host 127.0.0.1 --port 8000
```

토큰은 **운영자가 신원을 확인한 후** 안전한 환경에서 발급하는 파일럿용 임시 인증 방식입니다. 웹에서 자체 회원가입/토큰 발급은 지원하지 않습니다. 실제 서비스에는 학교 SSO/OIDC와 로그인 UI가 필요합니다. API 키 및 인증 토큰을 코드·브라우저·로그에 기록하지 마세요.

PostgreSQL 환경에는 `postgresql+psycopg://...`을 사용하세요. 디스크에 업로드 파일을 보관하므로 Vercel의 일시적 파일시스템에 그대로 배포하면 안 됩니다. 영구 스토리지와 백그라운드 문서 파싱을 갖춘 서버(Render 등)에 배포하는 것이 적절합니다.

## API

| 기능 | 메서드 및 URL | 권한 |
| --- | --- | --- |
| 상태 | GET /health | 공개 |
| 과목 생성 | POST /v1/courses | 교수자 |
| 학생 등록 | POST /v1/courses/{id}/members | 해당 과목 교수자 |
| 수업 AI 정책 변경 | PUT /v1/courses/{id}/policy | 교수자 |
| 교안 업로드 | POST /v1/courses/{id}/materials | 교수자 |
| 승인 후보 확인 | GET /v1/materials/{id}/candidates | 교수자 |
| 근거 서명 승인 | POST /v1/materials/{id}/approve | 교수자 |
| 질문 및 역질문 | POST /v1/courses/{id}/questions | 해당 과목 학생 |
| 질문 수정 | POST /v1/questions/{id}/revisions | 원 질문 작성자 |
| 내 질문 이력 | GET /v1/courses/{id}/questions/mine | 해당 학생 |

교수자는 후보 목록의 `page`, `block_id`, `block_sha256`을 직접 검토해 승인 요청해야 합니다. 학생 응답에는 교안 원문이 담기지 않습니다.

## 테스트

```bash
cd backend
python -m unittest discover -s tests -v
```

테스트에서는 외부 엔진 호출만 모의 처리하며 **실제 교원 승인 및 FreeLLMAPI 실호출을 증명하지 않습니다**.

## 파일럿 전 차단 요건

1. v1.6.2 frozen engine의 검증된 산출물을 서버에서 재현하고 실제 교수자 신원으로 승인
2. 인증 비밀키 및 FreeLLMAPI 키 새로 발급해 서버 측 보관
3. 20문항 실모델 평가(정답 누설·근거 정확성·실패율) 및 학생별 ACL 테스트
4. TLS, 백업, 요청 한도, 업로드 악성파일 검사, 운영 로그 마스킹, SSO 준비

**이 브랜치를 바로 공개 파일럿 운영 환경이라고 간주하면 안 됩니다.**
