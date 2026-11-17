# LearningX 데모 전용 수업 코드 → 실서버 API 계약 제안

> **제안서만 작성. 백엔드 엔드포인트는 구현하지 않았습니다.**

## 목적과 원칙

- 교수자 인증/JWT·과목 멤버십·승인 근거 1쪽 이상이 있어야 수업 코드를 발급한다.
- 6자리 코드는 대문자·숫자이며 O, 0, I, 1을 제외한다. 암호학적 난수로 생성하고 서버에는 코드 해시와 만료일을 저장한다.
- 입장 시 소문자·하이픈·공백을 정규화한다. 무차별 대입 방지를 위한 rate-limit, 지연, 임시 차단이 필요하다.
- 교수자 화면에서 학생 이름을 공개하지 않는다. 실제 익명 제출과 사용자 인증·감사를 분리한다.
- 수업별로 코드와 질문 세션을 분리하고 만료/폐기 이후 입장과 제출을 차단한다.
- 승인된 근거 블록만 인용하며 답안·교수자 노트·미검증 시각자료·오디오 전사 없는 자료는 제외한다.
- 데모의 localStorage/BroadcastChannel은 인증, 서버 기록, 다기기 동기화를 제공하지 않는다.

## REST 계약 초안

| 메서드 | 경로 | 인증 | 동작 |
|---|---|---|---|
| POST | /v1/courses/{course_id}/join-codes | 교수자 JWT | material_id, expires_in_minutes (최대 240) 전달, 코드 발급 |
| GET | /v1/courses/{course_id}/join-codes/active | 교수자 JWT | 발급·만료·폐기 상태 조회 |
| DELETE | /v1/courses/{course_id}/join-codes/{id} | 교수자 JWT | 즉시 코드 폐기 |
| POST | /v1/sessions/join | 레이트 리밋 사용자 | code 정규화 후 만료형 익명 수업 세션 생성 |
| POST | /v1/sessions/{id}/questions | 세션 토큰 | 원 질문·수정 질문, 승인 근거 참조 확인 뒤 제출 |
| GET | /v1/courses/{course_id}/questions/live | 교수자 JWT | 익명 질문 목록, SSE/폴링 |
| POST | /v1/courses/{course_id}/questions/{id}/feature | 교수자 JWT | 프로젝터 질문 선택 |

오류 계약: 400 INVALID_CODE_FORMAT, 404 CODE_NOT_FOUND, 410 CODE_EXPIRED,
409 EVIDENCE_NOT_APPROVED, 429 RATE_LIMITED.

## 데모 구현과의 차이

데모는 같은 브라우저의 2탭에서만 연동된다. 업로드 파일은 실제 분석되지 않으며,
파일명·크기·SHA-256만 메타데이터로 저장한다. 질문 코칭은 고정 규칙과
가상 교안 승인 목록을 사용한다. 서버·AI·개인정보·비밀키에는 접근하지 않는다.
