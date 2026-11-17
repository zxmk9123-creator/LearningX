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


## 실서버 보안 경계: 학생 응답에는 승인 블록만 포함

> **필수 API 계약 / 아직 미구현.** 데모의 `data/demo-nit.json`은 공개된 가상 자료이며, 학생 브라우저가 파일 전체를 가져온 후 DOM에서 제외 블록을 숨기는 방식은 운영 서비스에 절대로 재사용하지 않는다.

### 서버 측 응답 최소화

1. PDF/PPTX 원문과 NIT Canonical JSON, 교수자 노트·제공 답안·검증 전 도표·오디오 메타데이터는 **교수자 전용 비공개 저장소**에 둔다. 학생에게 저장소 URL, 다운로드 주소, 전체 `blocks` 목록이나 전체 문서 JSON을 반환하지 않는다.
2. 학생용 수업 입장·교안 조회·코칭·출처 펼치기 등의 **모든 API**는 JWT 또는 수업 세션으로 신원을 검증하고 수업 소속 및 코드 만료를 확인한 후, 서버에서 별도의 **allowlist serializer**를 적용한다. UI의 `hidden` 속성이나 클라이언트 필터링에 의존하지 않는다.
3. 학생에게 보낼 수 있는 근거 블록은 `content_role === "lesson_content"` **그리고** `quality.status === "PASS"` **그리고** 해당 원문 해시가 포함된 **유효한 교수자 서명 승인 목록**에 존재하는 블록뿐이다. 하나라도 불일치하면 응답에서 제외한다. 학생이 요청한 `block_id`를 그대로 조회·직렬화하지 않는다.
4. 응답은 수업·질문 처리에 필요한 최소 필드만 허용한다. 예: `{ "page": 1, "block_id": "p1:concept", "approved_text": "..." }`. 교안의 미승인 텍스트, `provided_answer`, `instructor_note`, `visual_case`, `NOT_ANALYZED` 원문 및 해설은 **성공·실패 응답 모두**에 포함하지 않는다.
5. 승인 블록이 0개이거나 관련 승인을 찾지 못하면 `evidence: []`와 근거 없는 일반적인 질문 줄기만 반환한다. 근거를 임의로 생성하지 않는다. 서명 무효·권한 없음·코드 만료는 적절한 403/409/410 상태로 처리한다.
6. 실제 학생 화면에는 API가 내려준 **승인된 근거만** 전달하며, 미승인 본문을 `localStorage`, `BroadcastChannel`, 클라이언트 상태, 캐시, 웹소켓·SSE, 네트워크 응답, 콘솔 로그에도 실어 보내지 않는다. 교수자 전용 페이로드와 학생용 페이로드를 같은 객체로 재사용하지 않는다.
7. 데모의 `localStorage` + `BroadcastChannel`은 같은 브라우저의 가상 교안으로만 허용한다. 실제 교안으로 교체하는 것만으로 실서버를 구성했다고 판단해서는 안 된다.

### 제안 응답 계약 (구현 전)

`POST /v1/sessions/{session_id}/coach` (학생 세션 토큰):

요청:
```json
{"question":"의제설정 이론은 무엇을 설명하나요?"}
```

응답 예시 — **서버가 서명 검증을 완료한 승인 블록만**:
```json
{
  "level":"얕은",
  "stems":[
    {"label":"비교하기","template":"___와 ___는 어떤 점이 다를까요?"}
  ],
  "evidence":[
    {"page":1,"block_id":"p1:concept","approved_text":"교수자가 승인한 학습 내용 일부"}
  ],
  "classification_method":"server_policy_or_model"
}
```

`GET /v1/sessions/{session_id}/approved-evidence/{block_id}`는 동일한 세션·수업·서명 조건을 재검증해 **해당 승인 블록만** 반환한다. 임의 블록 ID 접근은 403 또는 404로 거부하며, 전체 문서 다운로드는 제공하지 않는다.

### HTTP 응답 본문 기준 인수 테스트 — 실서버 배포 게이트

현재의 `frontend/tests/demo_e2e.py::test_06_student_dom_never_contains_unapproved_blocks`는 **데모 DOM만 확인**하므로 실서버 보안 검증으로 간주하면 안 된다. 백엔드 구현 시 아래 테스트를 반드시 추가하고 실행한다.

- **`test_student_context_response_only_approved`**: 승인 p.1, 미승인 품질 통과 p.2, 제외 7종의 텍스트가 있는 교안을 준비한다. 학생 세션으로 코칭/교안 맥락/출처 조회 API를 호출하고 `response.content` 및 `response.json()`의 **실제 응답 본문**을 검사한다. p.1만 존재하고 p.2 및 제외 7종의 원문/답안/노트는 존재하지 않아야 한다.
- **`test_student_cannot_request_unapproved_block`**: 학생이 임의로 p.2, 제공 답안, 교수자 노트의 `block_id`를 지정해도 본문이 반환되지 않아야 한다(403/404). 실패 응답의 오류 문자열에도 원문이 포함되면 실패다.
- **`test_zero_approval_returns_no_evidence`**: 승인 0건이면 `evidence=[]`이며 임의 출처나 본문을 생성하지 않아야 한다.
- **`test_expired_or_cross_course_session_cannot_fetch_evidence`**: 만료 코드·다른 수업 세션·권한 없는 토큰으로 자료 조회 시 요청을 거부하고 본문을 유출하지 않아야 한다.
- **`test_student_network_responses_never_contain_raw_nit`**: Playwright의 `page.on("response")` 또는 HAR 캡처를 사용해 학생 세션의 모든 `/v1/` JSON 응답 본문을 검사한다. `document.json` 전체나 미승인 블록이 전달되는 순간 실패한다. DOM에서 숨겨진 상태여도 실패다.

**승인 기준:** 서버가 실제로 동작하는 환경에서 HTTP 응답 본문 검사가 성공할 것. 시뮬레이션·데모 fixture에 대한 검사나 DOM 검사만으로 이 항목을 통과 처리하지 않는다.
