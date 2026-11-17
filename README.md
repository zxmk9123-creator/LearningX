# LearningX · NIT

교수자 승인 교안 기반 질문 코칭 플랫폼의 전용 저장소입니다.

## 폴더

- [프론트엔드](frontend/README.md): 질문 코치 v3 시안 기반 정적 UI + 데모/실백엔드 연동
- [실습실](frontend/app.html): 학생 질문 → 역질문 → 수정, 교수자 교안 검토·서명 UX
- [백엔드 가이드](backend/README.md): FastAPI, 수업별 인증·권한, NIT frozen engine adapter
- [백엔드 API](backend/learningx/api.py): 교수자 승인 및 학생 코칭 API
- [자동 테스트](.github/workflows): backend + frontend 별도 검사

## 배포 경계

- Vercel은 `frontend/`만 GitHub에 연결하여 정적 사이트로 배포합니다.
- **데모**에서는 어떤 실제 승인·코칭·저장도 하지 않습니다.
- **서버 연결**은 별도 배포된 LearningX 백엔드 URL과 서버 발급 JWT가 필요합니다.
- 기존 frozen NIT v1.6.2 교안 처리 엔진은 현재 백엔드 소스에 포함되지 않습니다. 설치 검증 전에는 실제 코칭 API가 열리지 않습니다.
- 수업 자료·교수자 서명키·API 키·토큰은 공개 GitHub나 Vercel 프론트 환경변수에 올리지 않습니다.

운영 파일럿에는 실제 교수자 검토·서명, 전용 영구 스토리지, 수업별 접근권한, 서버 E2E 검증이 추가로 필요합니다.

## 백엔드 Render 배포

- [Render Blueprint](render.yaml): FastAPI 서비스만 Singapore 무료 플랜으로 구성
- [배포 및 Neon 연결 가이드](backend/RENDER_DEPLOY.md): DB 환경변수, 헬스체크, 교수자·학생 JWT 발급
- [Render Blueprint 신규 생성](https://dashboard.render.com/select-repo?type=blueprint)

**주의:** 무료 Render 서버에는 영구 디스크가 없어 실제 교안 파일 업로드는 차단됩니다. 검증된 NIT 엔진도 서버에 아직 설치되지 않았으므로 `/health/db` 연결 검사와 코칭 파일럿 가동은 다른 단계입니다.
