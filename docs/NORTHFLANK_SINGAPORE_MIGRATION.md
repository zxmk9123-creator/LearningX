# Northflank 싱가포르 지역 이전 절차

## 왜 옮기나

2026-11-25 운영 시범 운영에서 학생 질문 한 번에 7~9초가 걸렸다(AI 호출이 없는 차단·원장 재생도 7초 이상).

- 브라우저와 API 사이 왕복은 약 0.2초다.
- 질문 한 번에 DB 조회가 약 37회 순서대로 일어난다(복제 환경 Server-Timing 기준).
- 37회 × 약 0.2초 ≈ 7.4초로, DB(Neon, 싱가포르 ap-southeast-1)와 API가 서로 다른 지역에 있을 때의 값과 맞다.

Northflank는 프로젝트를 만든 뒤에는 지역을 바꿀 수 없다. 그래서 asia-southeast(싱가포르)에 새 프로젝트를 만들어 다시 배포해야 한다. 무료 Developer Sandbox는 프로젝트를 1개만 만들 수 있으므로, 기존 프로젝트를 지운 뒤 다시 만든다.

## 0. 시작 전 확인

1. **현재 지역 확인:** 프로젝트 learningx → Settings에서 region을 본다. 이미 asia-southeast라면 이전하지 않고 다른 원인을 찾는다.
2. **운영 측정:** 배포된 Server-Timing으로 운영 질문 요청의 `db;dur`와 `app;dur`를 잰다. db가 대부분이면 이전 효과가 크다.
3. **시간 정하기:** 수업이 없는 시간을 고른다. 중단 시간은 약 30분~1시간이다.

## 1. 기존 설정 기록 (값은 기록하지 않고 이름만)

서비스 두 개의 설정을 화면으로 기록한다.

- **learningx-shared-api:** 공개 HTTP 8080, CMD는 기본값(uvicorn).
- **learningx-ingest-worker:** 공개 포트 없음, 시작 명령 `python -m learningx.northflank_worker`.
- **공통 빌드 설정:**
  - GitHub zxmk9123-creator/LearningX, 브랜치 `main`, 자동 배포 켬
  - 빌드 유형 Dockerfile
  - 빌드 컨텍스트 `/backend`
  - Dockerfile 경로 `/backend/Dockerfile`
- **헬스 체크:** API `/health` 8080.
- **플랜과 인스턴스 수:** 지금과 같게 둔다.

비밀 그룹과 환경 변수는 **이름만** 기록한다. 값은 운영진이 가진 원본에서 새 프로젝트에 직접 입력한다. 아래 목록은 코드가 읽는 변수다. 실제로 쓰는 것만 옮긴다.

- **DB·스토리지:** `DATABASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `NIT_STORAGE_BUCKET`
- **서명·인증:** `NIT_JWT_SECRET`, `NIT_REVIEW_SIGNING_KEY`, `NIT_NEON_AUTH_URL`, `NIT_MASTER_EMAILS`, `NIT_GOOGLE_ALLOWED_DOMAINS`, `NIT_BETA_FACULTY_INVITES`
- **모델:** `NIT_LLM_BASE_URL`, `NIT_LLM_MODEL`, `NIT_LLM_API_KEY`, `NIT_LLM_SEED`, `GEMINI_API_KEY`
- **운영 설정:**
  - `NIT_CORS_ORIGINS`(https://learning-x-one.vercel.app), `NIT_WORKER_BACKEND`(northflank_poll)
  - `NIT_WORKER_POLL_SECONDS`, `NIT_WORKER_IDLE_POLL_SECONDS`, `NIT_WORKER_QUIET_HOURS`, `NIT_WORKER_TZ_OFFSET`
  - `API_THREADS`, `LIVE_READ_POOL`, `LIVE_POOL_RECYCLE`
  - `NIT_ARTIFACT_CACHE_DIR`, `NIT_ARTIFACT_CACHE_BYTES`, `NIT_OCR_LANGUAGE`

`NIT_JWT_SECRET`과 `NIT_REVIEW_SIGNING_KEY`는 **반드시 같은 값**을 넣는다.

- `NIT_REVIEW_SIGNING_KEY`가 바뀌면 이미 승인한 교안 근거의 서명 검증이 실패한다.
- `NIT_JWT_SECRET`이 바뀌면 로그인한 사용자가 모두 로그아웃된다.

## 2. 교체

1. 기존 프로젝트 삭제. 되돌릴 수 없으므로 실행 직전에 운영진이 확인한다. Neon DB와 R2 저장소는 지우지 않는다.
2. **새 프로젝트:** 이름 learningx, 지역 Asia Southeast(asia-southeast).
3. 비밀 그룹을 만들고, 운영진이 값을 직접 입력한다.
4. 1에서 기록한 설정 그대로 서비스 두 개를 만든다(같은 커밋).
5. 새 API 주소(`https://p01--learningx-shared-api--<새 ID>.code.run`)를 확인한다.

## 3. 프론트 주소 전환

새 주소로 아래 두 곳을 바꿔 PR → 병합 → Vercel 배포:

- `frontend/assets/qu-api.js`의 `DEFAULT_API`
- `frontend/assets/faculty-beta.js`의 `api`

R2 버킷 CORS와 Neon Auth 신뢰 도메인은 프론트 주소 기준이라 바꿀 필요가 없다.

## 4. 확인

1. `GET /health` 200, `GET /health/db`가 connected인지 본다.
2. 워커 로그에 시작 오류가 없는지 본다.
3. 교수자 로그인 → 기존 수업 목록 → 기존 승인 교안으로 수업 시작이 되는지 본다(서명 검증 확인).
4. 학생 질문 Server-Timing의 `db;dur`가 수백 ms 수준으로 떨어졌는지 잰다.
5. 새 교안 업로드 → 분석 완료(워커 확인).

## 되돌리기

새 프로젝트에서 문제가 생기면 같은 절차로 이전 지역에 다시 만든다. DB와 저장소는 그대로이므로 데이터 손실은 없다.
