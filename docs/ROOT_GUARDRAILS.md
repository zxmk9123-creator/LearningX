# 루트 가드레일·컴플라이언스 (v1)

## 원칙

**모델은 제안만 하고, 판정은 플랫폼(루트)이 내린다.**

- 어떤 모델이 호출되든 학생과 교수자가 보는 문장, 수준, 적합성 판정은 루트 규칙을 통과한 것만 나간다.
- `auto` 라우팅으로 모델이 바뀌어도, 공급자가 멈춰도 판정은 같아야 한다.
- 이 보장은 프롬프트로 모델에게 부탁해서 얻는 게 아니다. 루트 코드가 강제한다. 프롬프트는 제안의 품질을 높이는 보조 수단일 뿐이다.

## 판정 우선순위 (위에서 결정되면 아래는 실행하지 않음)

| 순서 | 단계 | 코칭 (수준·역질문) | 적합성 |
|---|---|---|---|
| 1 | 결정론 차단 규칙 | 질문 형식 아님, 정보 조회 등 → `suitability_blocked` (모델 호출 없음) | `rule_reasons` — 저장된 판정보다 항상 우선 (`effective_check`) |
| 2 | **결정 원장** `root_judgments` | 같은 맥락에서 같은 질문 → 저장된 결정을 그대로 재생 (모델 호출 없음) | 같은 교안 맥락에서 같은 질문 → 저장된 판정 재생 |
| 3 | 근거 권한 검사 | 교수자 서명 근거 없음, 승인 안 된 쪽 인용 → 고정 폴백 | — |
| 4 | 모델 제안 | 스키마 → 정책 → 가드레일 → 수준 규칙 순으로 검증한 뒤에만 채택하고 원장에 기록 | 스키마·이유 코드 검증 뒤 채택하고 원장에 기록 |
| 5 | 실패 | 고정 폴백 문구 + **판정 보류**. 원장에 기록하지 않음 | 재시도 후 `check_unavailable`. 원장에 기록하지 않음 |

## 결정 원장 키

`sha256(종류, 루트 정책 버전, 맥락 지문, 정규화한 질문)`

- **정규화:** NFKC, 연속 공백을 하나로, 끝의 물음표·마침표 제거.
- **코칭 맥락 지문:** 교안 ID와 sha256, 서명 근거 집합 지문, 코스 정책, 교수자 승인 여부, 학습목표 프로필 버전.
- **적합성 맥락 지문:** 교안 ID, 코스명, 쪽, 학생이 선택한 인용, 학습목표.
- **코칭 루트 정책 버전** = `nit_coaching_v1 + coaching-prompt-v2 + level-rubric-v1 + guardrails-v1`
- **적합성 루트 정책 버전** = `suitability-v1 + suitability-prompt-v1`
- **규칙을 하나라도 바꾸면 해당 버전을 올린다.** 그러면 원장이 새로 시작되어, 옛 결정이 새 규칙 아래에서 재생되지 않는다.

## 불변식

| # | 불변식 | 강제 위치 | 검증 테스트 |
|---|---|---|---|
| I1 | 같은 맥락의 같은 질문은 모델이 바뀌어도 같은 수준과 같은 역질문을 받는다 | `learningx/judgments.py`, `classroom._coach` | `RootLedgerTests.test_any_model_after_the_first_decision_is_not_asked` |
| I2 | 공급자 장애 중에도 이미 결정된 질문은 같은 결정을 받는다 | 원장 조회가 모델 호출보다 먼저 | `test_a_provider_outage_still_replays_the_decision` |
| I3 | 수준 단서가 있는 질문은 모델과 무관하게 같은 단계로 판정된다 (이유·영향·비교·관계·적용 → 중간, 기준 판단·선택·설계 → 깊은, 정의·지칭·구성·사실 → 얕은) | `runtime/level_rules.py` | `RepeatedModelDriftTests` |
| I4 | 학생에게 가는 모델 문장은 한국어 질문 한 문장이다. 정답·해설·결론 암시, 모델 자기언급, 학생 질문 되풀이, 교안 원문 복사, URL은 없다 | `runtime/guardrails.py`, `validate_model_response` | `GuardrailTests` |
| I5 | 피드백 문구는 루트 고정 문구만 쓴다 (모델 자유 서술 없음) | `run_coaching` | `test_feedback_is_the_fixed_root_sentence` |
| I6 | 폴백은 수준을 빌리지 않는다 (판정 보류). 원장에도 남지 않는다 | `_fallback`, `judgments.coaching_decision` | `test_unassessed_level`, `test_fallbacks_are_not_decisions` |
| I7 | 같은 교안 맥락의 같은 질문은 적합성 판정이 바뀌지 않는다. 결정론 규칙은 언제나 우선한다 | `suitability.run`, `effective_check` | `test_the_root_ledger_keeps_a_verdict_whichever_model_answers_next` |
| I8 | 맥락(교안·근거·학습목표 버전)이 바뀌면 새로 판정한다 | 맥락 지문 | `test_another_class_material_decides_afresh`, `test_a_different_lesson_gets_its_own_verdict` |
| I9 | 모델 호출은 temperature 0과 고정 seed로 한다 (`NIT_LLM_SEED`, 기본 7). seed를 거부하는 공급자에게는 seed 없이 1회만 다시 호출한다 | `runtime/gateway.py` | `GatewayTests` |
| I10 | 학생·교수자 화면은 화이트리스트 필드만 받는다. 모델 이름과 원판정은 운영 기록에만 남는다 | `_student_coaching`, 보드 직렬화 | `test_classroom` (model_observability 미노출) |

## 운영 관측 (저장 코칭 JSON)

- `decision_source`: `model_proposal_accepted`(이번에 채택) 또는 `ledger`(재생).
- `root_policy`: 판정에 쓰인 루트 정책 버전.
- `level_basis`: `rule:<단서>`, `model`, 또는 원장에서 재생한 경우 원래의 근거.
- `model_observability.model`, `model_observability.model_level`: 이번 호출의 모델과 그 모델의 원판정. 재생된 결정에는 없음.
- `model_errors`: 거절 사유 코드. 예: `InvalidModelOutput: guardrail_answer_leak`. 모델 원문은 기록하지 않는다.

## 변경 절차

1. 규칙, 프롬프트, 가드레일 중 무엇이든 바꾸면 해당 버전 상수를 올린다. 코칭은 `PROMPT_VERSION`, `RUBRIC_VERSION`, `GUARDRAIL_VERSION`, 적합성은 `POLICY_VERSION`, `MODEL_PROMPT_VERSION`이다.
2. 불변식 표의 테스트를 함께 갱신하고, 전체 테스트를 통과시킨다.
3. 배포한 뒤 운영 반복 측정을 한다. 수준별 질문을 5번씩 보내 같은 결정이 나오는지 확인한다.

## 알려진 한계

- 원장은 **같은 질문**(정규화 후 동일)을 보장한다. 단서가 없으면서 표현이 크게 다른 질문은 처음 받을 때 모델 제안을 따른다.
- 원장 결정을 되돌리려면 정책 버전을 올리거나, 해당 키를 운영자가 지운다.
