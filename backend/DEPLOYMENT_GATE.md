# 배포 검증 상태

현재 실행 구조는 Northflank API와 DB 폴링 워커입니다. [현재 배포 가이드](NORTHFLANK_DEPLOY.md)를 우선 참조하세요. 아래는 2026-11-17 당시의 감사 기록으로, GitHub Actions 워커와 미설정 변수에 관한 상태는 당시 기준입니다.

최근 실서버에서는 합성 PDF의 직접 업로드·파싱·근거 서명 승인을 확인했습니다. 원본 대용량 교안·시각 의미 분석·실제 LLM 품질의 전체 검증은 남아 있습니다.

# LearningX — 배포 전 P0 안정화 게이트 (2026-11-17)

**진행 중인 개발 브랜치:** `feat/neon-durable-course-artifacts`. 이 문서의 "코드 있음"은 프로덕션 통과를 의미하지 않습니다.

## 1. 실물 교안 회귀 기준

- 32.9MB 집단극단화 PPTX **원본 파일이 현재 검증 세션에 없어** 13개 오디오 실물 탐지는 **미검증**.
- 합성 13개 MP3 ZIP/PPTX 단위테스트로 탐지 함수의 개수 계산만 검증. 원본 PPTX에서 `embedded_audio_assets=13` 확인 전 배포 불가.
- 파일럿 교수자 화면의 `media_coverage.audio_status`는 오디오 **분석 제외**로 표시해야 함. 탐지는 전사·의미 이해와 무관.
- 실제 367페이지 PDF 및 32.9MB PPTX에 대해 출처 해시, 분석 시간, 메모리, Canonical JSON 생성, 마감 검증을 별도로 실시해야 함.

## 2. 남은 4개 결함 — 자동 "수정 완료" 주장 금지

| 결함 | 개발 브랜치 조치 | 실제 자료 통과 조건 |
| --- | --- | --- |
| 재생 아이콘으로 Vision 오탐 11건 | `pptx_layout_resolver.is_media_control`이 실제 media action이 연결된 아이콘을 비학습 시각요소로 분류 | 원본에서 11개 위치 대조, 미분류 재생 이미지와 실제 교육 이미지 오분류 0건 |
| 시각 상태값 불일치 | `document.vision_execution.status=NOT_RUN`, `completed_jobs=0`; 계획과 실행/검증을 분리 | PDF/PPTX의 page routing, jobs, report, manifest 상태 교차검증; 실제 Vision 미실행 표시 |
| 1차시 미인식 | `lesson_boundary`의 `1차시`, `제1차시`, 파일명 탐지 범위 확장 | 원본 1차시 시작·끝 페이지 및 후속 차시 경계 검수 |
| U+F0E0 의미 손실 | 단순 bullet 치환 대상에서 제외하고 PUA 기호 보존, 텍스트 Quality Gate에서 격리 | 원본 PowerPoint의 렌더링/폰트 대조 후 교수가 화살표 의미 승인 |

PPTX `parser-audit`는 PDF arbitration 없이 `not_applicable_pptx`를 보고, 실제 오디오는 `NOT_ANALYZED`로 보고한다.
**LibreOffice를 설치했다고 Vision/OCR이 실행된 것은 아니다.** 별도 시각 검사 도구와 모델 실행 기록 없이는 `NOT_RUN` 유지.

## 3. 대용량 직접 업로드 및 서버 검증

- `initiate`: 수업 교수자 권한을 확인하고 15분 유효 Neon S3 `PUT` 서명 URL만 발급
- 브라우저 `PUT`: 본문이 FastAPI/Vercel에 들어오지 않음 (최대 **100MB** 단일 객체)
- `complete`: S3 `HEAD.ContentLength`와 최초 신고 바이트 수 비교 후 `QUEUED` 전환
- **Worker**: Neon에서 바이트를 새로 다운로드하면서 SHA-256을 직접 누적 계산; 크기·파일 시그니처 확인 후 NIT 처리. 클라이언트 해시는 사용하지 않음
- 결과는 비공개 S3에 저장, DB에 원본의 Worker 해시와 분석 범위 메타데이터 기록
- **교수자 승인 이전에** 학생 코칭 근거로 사용 불가

## 4. 버킷 CORS — 실제 적용 전 미검증

현재 버킷 `learningx-private-lessons` 생성만 확인했으며 **CORS를 원격 버킷에 적용했다고 주장하지 않음**.
전용 스크립트로 명시적으로 설정·확인한다. 실제 S3 자격증명은 비밀 저장소에서만 사용:

```bash
cd backend
export NIT_CORS_ORIGINS='https://learning-x-one.vercel.app'
python -m learningx.configure_cors --apply
python -m learningx.configure_cors --check
```

CORS 요구: `AllowedOrigins`에 서비스 origin만 등록, `AllowedMethods: PUT,HEAD`, `AllowedHeaders: Content-Type`, `ExposeHeaders: ETag`. 절대 `*` origin을 사용하지 않음. 배포 도메인에서 **OPTIONS 프리플라이트 → PUT → HEAD** 실제 실행.

## 5. 분석 Worker — Vercel Functions에서 분리

**파일럿 선택:** GitHub Actions Ubuntu Python 작업. Vercel은 API만 운영하고 별도 실행기에서 대형 문서와 LibreOffice 렌더링 환경을 구성한다.

- 이벤트: `repository_dispatch: learningx-ingest` (API가 job ID만 전달), `workflow_dispatch`, 5분 간격 `schedule` 백로그 복구
- 데이터: Neon PostgreSQL 작업 행(`QUEUED/RUNNING/READY/FAILED`) 및 Private S3, 조회·실패 재시도 관리
- 동시 작업은 Actions `concurrency`와 Neon DB row lock/lease로 차단
- 문서 비밀은 GitHub 아티팩트·로그에 출력하지 않음
- Runner에는 Python 3.12과 LibreOffice Impress 설치. **실제 Vision 단계**는 이후 별도 구현으로 명시

GitHub Actions는 실제 Runner 대기 시간이 발생할 수 있지만 Render 무료 인스턴스 콜드 스타트와 별개다. 대량 PPTX 분해·Vision 확장은 별도 워커 설계를 다시 검토할 수 있다.

### 배포 계정 설정 (아직 실제 주입 미완료)

GitHub Actions repository variables:
- `NIT_INGEST_WORKER_ENABLED=true` (검증 엔진 이식과 키 입력 후 활성화)
- `NEON_STORAGE_ENDPOINT=<branch S3 endpoint>`

GitHub Actions repository secrets:
- `LEARNINGX_DATABASE_URL`
- `NIT_JWT_SECRET`
- `NIT_REVIEW_SIGNING_KEY`
- `NEON_STORAGE_ACCESS_KEY_ID`, `NEON_STORAGE_SECRET_ACCESS_KEY`

Vercel 백엔드 서버 환경변수:
- `NIT_WORKER_BACKEND=github_actions`
- `NIT_WORKER_REPOSITORY=zxmk9123-creator/LearningX`
- `NIT_WORKER_GITHUB_TOKEN=<fine-grained token>` (GitHub repository_dispatch의 **Contents:write** 권한, 서버 전용)
- Neon DB/S3 credentials, JWT/signing keys

**제한:** 44개 엔진 소스의 버전·완전성 검증, 위 비밀값 등록, 실물 회귀, 원격 CORS 적용, GitHub Worker 최초 성공까지 완료되기 전에는 실제 교육 파일럿 배포를 승인하지 않는다.
