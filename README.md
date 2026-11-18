# LearningX · NIT

교수자가 승인한 교안 근거를 사용해 학생의 질문 개선을 돕는 FastAPI·정적 웹 플랫폼입니다.

- 프런트엔드 — `frontend/`, Vercel 정적 배포
- API·분석 워커 — `backend/`, Northflank 독립 프로세스
- 데이터 — Neon PostgreSQL과 비공개 S3 호환 객체 저장소
- 분석 엔진 — `backend/nit_engine/`, PDF/PPTX 추출·품질 판정·교수자 서명 검증

## 교안 처리

서명 URL 발급 → 저장소 직접 업로드 → 크기 확인 → DB 작업 큐 → 워커 해시 검증·기본 추출 → 비공개 산출물 저장 → 교수자 검토·서명 → 승인 근거 검색·질문 코칭.

작업의 `READY`는 기본 추출 산출물을 사용할 수 있다는 의미입니다. `analysis_coverage`는 native extraction, OCR, Vision, 의미 분석 및 교수자 승인 필요 여부를 별도로 제공합니다. 기본 워커는 native 추출만 수행합니다. [선택적 OCR·Vision](backend/DEEP_ANALYSIS.md)은 전용 이미지와 환경변수로 활성화하며, 결과는 교수자 서명 근거로 자동 승격되지 않습니다. 의미 분석과 오디오 전사는 미연결 상태입니다. 스캔·도표 중심 자료는 사용 가능한 근거가 제한될 수 있습니다.

정답·해설 표기가 있는 페이지, 미검증 OCR·AI 해석, 교수자 노트는 학생 근거로 승인하지 않습니다. 과목 권한과 승인 서명은 서버에서 검증합니다.

## 실행·검증

배포 변수와 두 프로세스의 실행 방법은 [Northflank 가이드](backend/NORTHFLANK_DEPLOY.md)를 참조하세요. 실제 교안·비밀키·토큰을 저장소에 커밋하지 마세요.

```bash
python -m pip install -r backend/requirements.txt
cd backend
python -m unittest discover -s tests -v
```

실물 PPTX 회귀는 `NIT_GOLDEN_PPTX_PATH`로 원본 파일 경로를 지정해야 실행됩니다. 대용량 실물 교안과 실제 LLM 품질은 별도 검증 대상입니다. 학생 로그인·수업 코드 입장·교수자 전체 학습 기록 화면은 후속 구현 범위입니다.

[배포 검증 항목](backend/DEPLOYMENT_GATE.md) · [업로드 구조](backend/UPLOAD_ARCHITECTURE.md)

[실물 교안 기본 추출 벤치마크](docs/INGESTION_BENCHMARK_2026-11-18.md)
