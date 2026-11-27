# 평가 엔진 설계

평가 엔진은 과제 설정(`task.json`)과 제출물을 받아 다음을 수행한다.

1. 제출물의 사용법을 찾는다.
2. 출제자가 정한 검사를 실행한다.
3. 기능 점수와 품질 판정을 근거와 함께 리포트로 남긴다.

이 문서는 모듈의 책임, 어떤 변경에도 유지해야 하는 원칙, 계약의 안정성 등급, 확장 방법, 변경 절차를 설명한다. JSON 형식의 세부 정의는 [계약 v1](../contracts/README.md)과 `contracts/schemas/`를 따른다.

## 1. 구성 요소와 책임

```text
task.json ─┐
제출본 ────┼─> agent ──(tools)──> runner ──> mapping.json, observations.json
           │     │                                   │
           │   models                review <────────┤
           │                           │             │
           │                     ai_review.json      │
           └──────────────> core <─────┴─────────────┘
                              │
                        report.json ──> reporting ──> report.md
```

| 모듈 | 책임 | 하지 않는 일 |
| --- | --- | --- |
| `cli` | 입력 검증, `run_meta` 발급, 모듈 호출 순서 관리, 실행 리소스 정리 | 채점, 모델 호출 |
| `snapshot` | 제출본 복사와 스냅샷 ID 계산, `read_source` 도구(스냅샷 밖·비밀 파일 차단) | 제출물 실행 |
| `agent` | 모델과 함께 제출물의 사용법을 찾고 도구를 호출. 문서에 적힌 호출법을 실행기에 전달 | 관찰값 작성, 점수 지정 |
| `runners` | 실행 규약대로 제출물 기동, 매핑 후보 시험, 검사 실행, 관찰값·매핑 기록, 정리 | 점수 계산, 품질 판단, 제출물 문서 해석 |
| `models` | `ModelClient` 인터페이스로 모델 제공자 연결 | 평가 규칙 적용 |
| `review` | 독립 세션에서 루브릭 판정과 근거 참조를 구조화해 반환 | 점수 계산, 검사 실행 |
| `core` | 산출물 검증, 배점 규칙 적용, 리뷰 근거 확인, 리포트 객체 생성 | 외부 실행, 모델 호출 |
| `reporting` | 리포트 객체를 JSON과 Markdown으로 기록 | 점수나 판정의 재계산 |

과제 고유의 내용(동작 이름, 지표, 배점, 루브릭, 실행 환경)은 `task.json`과 실행기 설정에만 둔다. 엔진 코드와 공통 스키마에는 특정 과제의 용어를 넣지 않는다.

## 2. 불변 원칙

아래 원칙은 엔진이 내는 점수와 근거를 신뢰할 수 있는 이유다. 어떤 기능을 추가하더라도 유지한다.

| 원칙 | 계약에서 보장하는 방식 |
| --- | --- |
| **관찰값은 실행기만 작성한다** | `observations.json`은 실행기 산출물이며, 모델 응답에는 관찰값 필드가 없다 |
| **점수는 관찰값과 규칙으로만 계산한다** | 기능 점수는 `rules`를 관찰값에 적용한 결과다. 품질 점수는 코어가 확인한 판정을 `points_by_rating`에 대입한 결과다. 리뷰 산출물에는 점수 필드가 없다 |
| **AI 판정은 제안이며 근거는 코어가 확인한다** | 인용한 코드 위치나 관측값이 하나라도 확인되지 않으면 `insufficient_evidence`로 배점을 보류한다 |
| **같은 입력은 같은 점수를 낸다** | 코어는 모델 호출과 외부 실행 없이 동작한다 |
| **채점하지 못한 것은 0점이 아니라 null이다** | 매핑 실패, 실행 오류, 근거 부족은 해당 점수와 총점을 null로 두고 사유를 기록한다 |
| **모델은 실행 환경을 지정할 수 없다** | 모델 도구는 상대 경로 호출과 스냅샷 내부 파일 읽기만 허용한다. 셸 명령, 임의 호스트, DB 질의, 배점은 지정할 수 없다 |
| **비밀값은 산출물에 남기지 않는다** | 접속 정보와 API 키는 환경 변수로만 전달하고, `model_info`에는 클라이언트 이름, 엔드포인트 호스트, 모델 이름만 기록한다 |

## 3. 계약 안정성 등급

계약의 각 부분은 아래 세 등급 중 하나에 속한다. 등급에 따라 허용되는 변경이 다르다.

### 고정

이름, 구조, 의미를 바꾸지 않는다. 바꿔야 한다면 새 `schema_version`이 필요하다.

| 대상 | 정의 위치 | 고정 내용 |
| --- | --- | --- |
| 공통 봉투 | `common.schema.json#/$defs/envelope` | `schema_version`, `run_id`, `task_id`, `submission_sha256` 네 필드. 하나라도 다르면 같은 실행의 자료로 병합하지 않는다 |
| 스냅샷 ID | [계약 v1](../contracts/README.md#공통-봉투) | 제외 대상, 줄 형식(`경로\t해시\n`), 정렬 기준, 해시 알고리즘 |
| 코드 위치 | `common.schema.json#/$defs/file_ref` | `{path, start_line, end_line}`. 스냅샷 기준 POSIX 상대 경로, 1부터 시작, 양 끝 포함 |
| 관측 행 | `common.schema.json#/$defs/observation_row` | `test_id` + `round`가 고유. `metrics`는 이름에서 숫자 또는 불리언으로의 대응이며 타입 변환을 하지 않는다 |
| 관측 참조 | `common.schema.json#/$defs/observation_ref` | `{test_id, round, metric}` |
| 판정값 | `common.schema.json#/$defs/rating` | 5종과 그 의미. `insufficient_evidence`는 보류, `needs_improvement`는 배점 대상 |
| 점수 null 규칙 | `report.schema.json` | `base_score`, `quality_score`, `total_score`가 null이 되는 조건 |
| 종료 코드 | `evaluator.cli.ExitCode` | `0` 채점 완료, `2` 계약 오류, `3` 채점 미완료 |

### 추가만 허용

v1 안에서 값이나 정의를 추가할 수 있다. 기존 값의 이름이나 의미를 바꾸거나 삭제할 수는 없다.

| 대상 | 정의 위치 | 현재 값 |
| --- | --- | --- |
| 호출 방식 `protocol` | `mapping-candidate.schema.json` | `http` |
| 탐색 자료 종류 `discovered[].kind` | `tools.schema.json` | `openapi` |
| 지표 타입 | `common.schema.json#/$defs/metric_type` | `int`, `number`, `bool` |
| 비교 연산 | `common.schema.json#/$defs/comparison_op` | `eq`, `ne`, `gt`, `gte`, `lt`, `lte` |
| 배점 규칙 종류 | `task.schema.json#/$defs/rule` | `award_if`, `award_per_round` |
| 오류 코드 | `common.schema.json#/$defs/error_code` | 8종 |
| 제출물 실행 규약 | `runner_config.app` (`env` 포함) | 기동 방식, 포트, 주입 환경 변수의 이름·형식 |
| 매핑 실패 단계 | `mapping.schema.json#/$defs/probe_stage` | 6종 |
| 모델 도구 | `tools.schema.json` | 에이전트 4종. 리뷰 세션은 도구 없음 |
| 실행기 설정 | `task.schema.json#/$defs/runner_config` | 공통 `database`, `tools`. 나머지 키는 실행기 소유 |

리포트에 나타나는 값(오류 코드, 실패 단계, 상태값)을 추가할 때는 `reporting`이 그 값을 표시할 수 있는지 함께 확인한다.

### 미고정

구현과 통합 과정에서 바뀔 수 있다. 바꿀 때는 영향을 받는 모듈과 예시, 테스트를 같은 변경에서 갱신한다.

- 도구 결과의 세부 필드(파일 잘림 방식, 응답 발췌 길이 등)
- 리포트 표현용 필드(`calls`, `applied_conditions`의 기록 방식)
- 에이전트 예산(모델 턴 수, 매핑 시험 횟수)
- 예시 과제의 배점, 루브릭, 실행 환경 값

## 4. 확장 방법

| 추가하려는 것 | 수정할 곳 |
| --- | --- |
| 새 과제 | `task.json` 작성. 기존 실행기와 도구로 충분하면 엔진 코드는 수정하지 않는다 |
| 새 실행 환경 | `runners`에 실행기를 구현하고 `runner_id`로 등록한다. 실행기 고유 설정은 `runner_config`의 자체 키로 받는다 |
| 새 호출 방식 | `mapping-candidate.schema.json`의 `operation_call`에 `protocol` 정의를 추가하고, 그 방식을 지원하는 실행기를 구현한다 |
| 새 지표 타입 또는 비교 연산 | `common.schema.json`에 값을 추가하고 `core`의 조건 평가를 확장한다 |
| 새 배점 규칙 종류 | `task.schema.json#/$defs/rule`에 정의를 추가하고 `core`에 평가 로직과 최대 배점 계산을 구현한다 |
| 새 모델 도구 | `tools.schema.json`에 `<이름>_args`와 `<이름>_result`를 정의하고 `x-tool-sets`의 해당 세션에 등록한다. 도구가 반환한 자료를 근거로 인용하게 하려면 코어의 근거 확인도 함께 확장한다 |
| 새 모델 제공자 | OpenAI 호환 엔드포인트면 `EVALUATOR_BASE_URL`만 지정한다. 그 밖의 경우 `ModelClient`를 구현해 `EVALUATOR_MODEL_CLIENT`로 지정한다 |
| 실행 검사 없는 과제 | `runner_id: null`, 빈 `operations`·`metrics`·`rules`, 기능 만점 0으로 작성한다 |
| 제출물에 주입할 값 | `runner_config.app.env`에 이름·형식·설명을 선언하고 문제지에 같은 내용을 안내한다. 실행기가 실행마다 값을 만들어 주입한다 |

회차 합계, 최댓값, 백분위 같은 집계는 실행기가 지표로 계산해 기록한다. 코어는 집계 연산을 갖지 않는다.

## 5. 변경 절차

계약을 바꾸는 변경은 아래 순서로 확인한다.

1. 바꾸려는 대상의 등급을 3장에서 확인한다.
2. **고정** 대상이면 v1 안에서 바꾸지 않는다. 새 `schema_version`과 이전 버전 처리 방법을 함께 설계한다.
3. **추가만 허용** 대상이면 값을 추가하고, 그 값을 읽는 모듈(`core`, `reporting`, 해당 실행기)을 같은 변경에서 갱신한다.
4. `contracts/examples/`와 `tests/test_contracts.py`를 갱신하고 `python -m pytest`가 통과하는지 확인한다.
5. 영향을 받는 모듈 담당자에게 바뀐 예시 입력과 출력을 먼저 공유한다.

### 버전 규칙

- 모든 산출물과 과제 설정은 `schema_version`을 갖는다. 엔진은 지원하지 않는 버전의 입력을 `contract_error`(종료 코드 2)로 거부한다.
- 고정 대상을 바꾸거나 기존 값을 삭제·변경하면 버전을 올린다. 추가만으로 끝나는 변경은 버전을 유지한다.
- 버전을 올린 뒤에도 이전 버전으로 만든 리포트는 그 버전의 스키마로 읽을 수 있어야 한다.
