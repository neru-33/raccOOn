# 계약 v1

엔진 모듈과 과제 설정이 주고받는 JSON 형식이다. 모든 파일은 UTF-8 JSON이고 스키마는 JSON Schema 2020-12로 작성한다. `schemas/`가 기준이며, 코드와 문서가 스키마와 다르면 스키마를 따른다.

## 파일 구성

| 스키마 | 작성자 | 용도 |
| --- | --- | --- |
| `task.schema.json` | 출제자 | 과제 설정 `task.json`. 논리 동작, 지표 선언, 기능 배점 규칙, 품질 루브릭, 리뷰 지침 |
| `mapping-candidate.schema.json` | 평가 에이전트 | `probe_mapping`에 제출하는 API 호출 후보 |
| `mapping.schema.json` | 실행기 | `mapping.json`. 문서에 적힌 호출법, 확인된 호출법, 시험 이력 |
| `observations.schema.json` | 실행기 | `observations.json`. 검사 회차별 관찰값 |
| `ai-review-model-output.schema.json` | 리뷰 모델 | 리뷰 모델의 최종 JSON 응답 (구조화 출력 strict 호환) |
| `ai-review.schema.json` | 리뷰 모듈 | `ai_review.json`. 모델 응답에 공통 봉투와 모델 정보를 붙인 산출물 |
| `report.schema.json` | 평가 코어 | `report.json`. 기능·품질 점수, 근거, 가설, 오류 |
| `tools.schema.json` | 엔진 | 모델 도구의 인자(`<이름>_args`)와 결과(`<이름>_result`) |
| `runtime.schema.json` | 엔진 | 모듈 간 전달 객체 `run_meta`, `orchestration_result`, `review_result` |
| `common.schema.json` | 엔진 | 공통 정의 |

`contracts/examples/reservation/`에는 계약 형식을 보여 주는 예약 과제 설정과 실행별 예시 산출물이 있다. 파일 이름은 `<종류>.<실행>.json`이다.

| 실행 | 상황 | 결과 |
| --- | --- | --- |
| `correct` | 첫 후보로 매핑 확인, 세 회차 모두 확정 1건 | 기능 40, 품질 48, 총점 88 |
| `oversell` | README의 생성 경로가 404, OpenAPI 대안으로 확인. 2·3회차 확정 2건 | 기능 16, 품질 17, 총점 33 |
| `unresolved` | 2회 시험 모두 실패 | `incomplete`, 점수 null |
| `evidence-gap` | `correct`와 같은 실행 결과. 리뷰가 파일 줄 수를 넘는 범위를 인용 | 기능 40, 해당 품질 항목 보류, 품질·총점 null |
| `start-failed` | 실행기 인프라는 정상, 앱이 헬스 경로에 응답하지 않음 | `incomplete`, `submission_start_failed`, 점수 null |

`orchestration-result.<실행>.json`은 코어에 들어가는 `orchestration_result`이고, `ai-review.<실행>.json`과 함께 같은 실행의 `report.<실행>.json`을 재현하는 입력이다. 코드 위치 확인에는 스냅샷 파일이 필요하므로 코어 테스트는 인용된 경로와 줄 수를 갖춘 고정 스냅샷을 함께 둔다(`evidence-gap`에서는 `tests/test_capacity.py`가 40줄).

예약 과제에서 실행기는 제출물의 테이블을 읽거나 쓰지 않는다. 시험용 이벤트는 제출물의 이벤트 등록 API로 만들고, 확정 건수는 이벤트 조회 API의 남은 자리로 계산한다(`runner_config.accounting`). 제출물은 저장 구조를 자유롭게 설계한다.

## 공통 봉투

실행 산출물(`mapping.json`, `observations.json`, `ai_review.json`, `report.json`)과 `run_meta`는 다음 네 필드를 갖는다. 값이 하나라도 다르면 같은 실행의 자료로 병합하지 않는다.

| 필드 | 값 |
| --- | --- |
| `schema_version` | `1` |
| `run_id` | CLI가 실행마다 발급하는 ID |
| `task_id` | `task.json`의 `task_id` |
| `submission_sha256` | 제출본 스냅샷 ID |

**스냅샷 ID.** 제출 디렉터리의 파일 중 `.git/`, 심볼릭 링크, `.env`와 `.env.*`를 제외한다. 남은 파일마다 `<POSIX 상대 경로>\t<파일 SHA-256 소문자 hex>\n` 한 줄을 만들고, 상대 경로의 UTF-8 바이트 순으로 정렬해 이어 붙인 바이트의 SHA-256을 소문자 hex로 쓴다.

## 실행 흐름과 산출물

```text
task.json ─┐
제출본 ────┼─> agent ──(tools)──> runner ──> mapping.json, observations.json
           │                                         │
           │                       review <──────────┤
           │                         │               │
           │                   ai_review.json        │
           └──────────────> core <───┴───────────────┘
                              │
                        report.json ──> reporting ──> report.md
```

| 파일 | 생성 조건 |
| --- | --- |
| `mapping.json` | 매핑 시험을 한 번 이상 시작한 경우 |
| `observations.json` | `run_checks`를 시작한 경우 |
| `ai_review.json` | `--review live`이고 모델 응답이 스키마와 봉투 검사를 통과한 경우 |
| `report.json`, `report.md` | 항상 |

## 모델 도구

읽기 도구 세 개는 평가 에이전트와 리뷰 세션이 함께 쓰고, 리뷰 세션은 읽기 도구만 쓴다. 어떤 파일을 얼마나 읽을지는 모델이 정하며, 호스트는 도구 호출 예산과 결과 크기만 제한한다.

| 도구 | 세션 | 구현 위치 | 인자 | 결과 |
| --- | --- | --- | --- | --- |
| `list_files` | 에이전트, 리뷰 | `evaluator.snapshot.list_files` | 선택 `path_prefix` | 파일 경로와 줄 수(최대 2,000개). Git 저장소면 추적 파일만 |
| `search_code` | 에이전트, 리뷰 | `evaluator.snapshot.search_code` | `query`, 선택 `path_prefix` | 일치한 경로·줄 번호·줄 내용(최대 100건). 고정 문자열, 대소문자 무시 |
| `read_source` | 에이전트, 리뷰 | `evaluator.snapshot.read_source` | `path`, 선택 `start_line`·`end_line` | 줄 번호를 붙인 내용(한 번에 최대 400줄), 전체 줄 수, 잘림 여부 |
| `start_submission` | 에이전트 | 실행기 `start()` | 없음 | 준비 상태, 실행 중인 제출물에서 얻은 사용법 자료(`discovered`, 예: OpenAPI 문서), 평가 도구 상태. 실패 시 원인(`environment`/`submission`)과 로그 발췌 |
| `probe_mapping` | 에이전트 | 실행기 `probe_mapping()` | `candidate`, 선택 `documented_operations` | `verified` + `mapping_id`, 또는 실패 단계·HTTP 상태 |
| `run_checks` | 에이전트 | 실행기 `run_checks()` | `mapping_id` | 검사 완료 여부와 관측 행 수. 호출 후 세션 종료 |

**줄 번호 형식.** `read_source`의 `content`는 각 줄을 `<줄 번호>\t<내용>`으로 이어 붙인다. 줄 번호는 원본 파일 기준으로 1부터 세며, 범위를 지정하거나 잘라도 원본 번호를 유지한다. 리뷰가 인용하는 `source_refs`와 코어의 줄 범위 확인이 같은 기준을 쓴다.

읽기 도구는 스냅샷 밖 경로, 절대 경로, `..`, 스냅샷에서 제외된 파일과 비밀 파일을 `path_denied`로 거부한다. 도구 호출이 실패하면 `{"error": {"code", "message"}}`를 돌려준다. `evaluator.contracts.tool_definitions("agent" | "review")`는 참조를 펼친 Chat Completions `tools` 형식의 함수 정의를 만든다. 일부 엔드포인트가 받지 않는 키워드(`pattern`, `propertyNames` 등)는 빼고 보내며, 호스트는 받은 인자를 원래 스키마로 다시 검증한다.

## 매핑 후보

`candidate`는 논리 동작 이름을 키로 갖고 `task.json.operations`의 모든 동작을 포함한다. 호출법은 `protocol`로 구분하며 v1은 `http`만 정의한다. 다른 호출 방식은 `mapping-candidate.schema.json`에 정의를 추가하고 그 방식을 지원하는 실행기를 등록해 확장한다.

- `path_template`은 `/`로 시작하는 상대 경로다. 스킴과 호스트는 쓸 수 없다. 경로 변수 `{이름}`은 URL 인코딩한다.
- `query_template`, `headers_template`(선택), `body_template`의 문자열 값 전체가 `"{이름}"`이면 변수의 원래 타입으로 치환하고, 문자열 일부에 쓰이면 문자열로 치환한다. 변수는 해당 동작의 `inputs`에 선언된 것만 쓸 수 있다. `{{이름}}`, `${이름}`, `{a.b}`처럼 변수 형식이 아닌 자리표시자는 쓸 수 없다.
- `response_fields`의 키는 해당 동작의 `outputs` 이름과 같다. `pointer`는 응답 본문 기준 RFC 6901 JSON Pointer다. `bool` 출력은 `equals`가 있으면 값 비교 결과, 없으면 응답 값 자체가 불리언이어야 한다.
- `inputs.<이름>.from`이 `<동작>.<출력>`이면 앞 동작의 응답 값을 입력으로 쓴다. `from`으로 연결된 입력은 그 동작의 템플릿에 변수로 써야 한다. 실행기는 동작을 선언 순서대로 호출하고 ID 연결을 확인한다.
- 실패 단계(`failed_stage`): `candidate_schema`, `request`, `transport`, `status_code`, `response_field`, `id_link`.

### 문서에 적힌 호출법

실행기는 제출물 문서를 읽지 않는다. 에이전트가 README 등에서 읽은 호출법을 `probe_mapping`의 `documented_operations`로 문서에 적힌 그대로 전달하고(시험할 `candidate`와 달라도 된다), 실행기는 마지막으로 받은 값을 `mapping.json`의 `documented_operations`에 기록한다. 각 호출법의 `probe_status`는 같은 동작에서 `protocol`·`method`·`path_template`이 같은 호출을 시험한 이력으로 판정한다.

| `probe_status` | 조건 |
| --- | --- |
| `verified` | 그 호출이 쓰인 시도가 `verified` |
| `failed` | 그 호출이 쓰인 시도가 해당 동작에서 실패(`failed_operation`이 그 동작) |
| `untested` | 시험하지 않았거나, 앞 동작에서 시도가 끝나 그 동작까지 가지 못함 |

## 관찰값

- 지표 타입은 `int`(정수), `number`(실수 허용, 지연 시간·비율 등), `bool`이다. 문자열 숫자, 불리언과 숫자 사이의 변환은 허용하지 않는다.
- `test_id` + `round`는 고유하다. `status: complete`이면 `task.json.metrics`에 선언된 모든 회차·지표가 선언된 타입으로 있어야 한다. 빠졌거나 타입이 틀리면 코어는 `invalid_observations`로 처리하고 점수를 보류한다.
- `status: execution_error`이면 `error.stage`, `error.message`가 필수이고, 이미 완료된 관측 행은 함께 보존할 수 있다.

## 기능 배점 규칙

| 규칙 | 동작 | 최대 배점 |
| --- | --- | --- |
| `award_if` | `test_id`의 `round` 행이 `conditions`를 모두 만족하면 `points` | `points` |
| `award_per_round` | `veto_if_any_round`를 먼저 평가해 어느 회차든 모두 만족하면 0점. 아니면 선언된 회차마다 `conditions`를 모두 만족할 때 `points` | `points` × 회차 수 |

조건은 `metric`, `op`(`eq`, `ne`, `gt`, `gte`, `lt`, `lte`), `value`로 쓴다. `bool` 지표에는 `eq`, `ne`만 쓸 수 있다. `score_limits.functional`은 규칙 최대 배점의 합과, `score_limits.quality`는 `quality_rubric`의 `max_points` 합과 같아야 한다. 리포트의 `applied_conditions`에는 평가한 조건과 관측값을 남기며, veto가 성립하면 award 조건은 평가하지 않는다.

## 제출물 실행 규약

실행기가 제출물에 요구하는 조건(기동 방식, 포트, 준비 확인 경로, 주입하는 환경 변수)은 `task.json`의 `runner_config.app`에 선언하고, 문제지에도 같은 내용을 안내한다. 실행기는 이 선언대로 제출물을 기동하고 값을 주입한다. 문제지와 실행기가 같은 선언을 따르므로, 규약을 지킨 제출물이 평가 쪽 설정 차이 때문에 실패하지 않는다.

```json
"app": {
  "compose_file": "compose.yaml",
  "service": "app",
  "port": 8080,
  "health_path": "/health",
  "ready_timeout_seconds": 60,
  "env": [
    {
      "name": "DATABASE_URL",
      "format": "postgresql://<user>:<password>@<host>:<port>/<database>",
      "description": "평가 환경이 제공하는 테스트 DB 접속 정보"
    }
  ]
}
```

- 실행기는 제출물 루트의 Compose 파일(`app.compose_file`, 없으면 Docker Compose 기본 이름 순)을 사용하고 앱 서비스를 스스로 고른다(`app.service` 이름이 있으면 우선). 앱 서비스만 띄우며 DB는 실행기가 붙인다. 파일이 없고 루트 `Dockerfile`이 있으면 `build: .`인 앱 서비스를 생성한다. 두 파일 모두 없으면 Railpack이 소스의 빌드 및 시작 명령을 판별하고 생성한 이미지로 앱을 실행한다.
- 세 기동 방식 모두 DB 같은 의존 서비스는 실행기가 덮어쓰기 파일로 붙인다. 접속 정보는 `env`에 선언된 이름으로 주입하고, `PORT`에는 `app.port` 값(예약 과제는 `8080`)을 주입한다.
- 기동 방식은 `observations.json`의 `runner_info.build_mode`에 `compose`, `dockerfile`, `auto` 중 하나로 남긴다. 자동 빌드 도구·기본 이미지 준비 실패는 `environment`, 스택 판별 실패나 제출물 빌드 실패는 `submission`으로 분류하고 로그 발췌를 남긴다.
- 준비 확인: 실행기는 `ready_timeout_seconds` 안에 `GET <health_path>`가 200을 돌려주면 제출물이 준비된 것으로 본다.
- `env`에는 이름, 형식, 설명만 적는다. 실제 값은 실행기가 실행마다 만들어 주입하며 설정 파일, 모델 입력, 리포트에 남기지 않는다.
- `runner_config.database.url_env`는 방향이 반대다. 운영자가 실행기에 외부 DB 접속 정보를 넘기는 환경 변수 이름이다.
- 실행기는 자체 인프라(컨테이너, 실행기에서 DB 접속)를 먼저 확인한 뒤 제출물의 준비 상태를 기다린다. 인프라가 실패하면 `execution_error`, 인프라는 정상인데 제출물이 기동·준비되지 않으면 `submission_start_failed`로 기록하고 로그 발췌를 남긴다. 두 경우 모두 점수는 보류한다.

## 실행 검사가 없는 과제

`runner_id`가 null이면 실행 검사 없이 품질 리뷰만 수행한다. 이때 `operations`, `metrics`, `rules`는 비어 있고 `score_limits.functional`은 0이어야 한다. 리포트는 `mapping_status: not_required`, `base_score: 0`, `max_score: 0`으로 기록한다. `operations`만 비어 있는 과제는 매핑 단계를 건너뛰고 `run_checks(mapping_id=null)`로 바로 검사를 실행하며, `observations.json`의 `mapping_id`도 null이다.

## 품질 판정과 근거 확인

판정값은 `needs_improvement`, `meets`, `strong`, `insufficient_evidence`, `not_applicable`이다. 코어는 리뷰 제안마다 다음을 확인한다.

| 참조 | 확인 방법 |
| --- | --- |
| `source_refs` | 스냅샷에 파일이 있고 `1 ≤ start_line ≤ end_line ≤ 파일 줄 수` |
| `observation_refs` | 같은 실행의 `observations.json`에 해당 `test_id`·`round`·`metric`이 있음. 값은 `observation_evidence`에 복사 |

참조 하나라도 확인되지 않거나 판정이 `insufficient_evidence`이면 그 항목은 `status: insufficient_evidence`로 배점을 보류하고 `issues`에 사유를 남긴다. `not_applicable`은 항목의 `points_by_rating`에 `not_applicable`이 있을 때만 허용된다. `extra_feature` 항목은 `verification_rules` 중 하나라도 만족하면 `runtime_verified`, 아니면 `static_only`다.

가설은 제출물의 결함 원인에 대한 검증되지 않은 설명이다. 코드 위치가 확인되면 `hypothesis`, 아니면 `insufficient_evidence`로 표시한다.

리뷰 세션은 읽기 도구로 제출물을 탐색한 뒤 구조화 출력으로 판정을 반환한다. 품질 근거는 코어가 결정적으로 확인할 수 있는 코드 위치와 실행 관측값으로 한정한다.

## 점수와 상태

- `base_score`: 매핑이 확인(또는 불필요)되고 관찰값이 완전할 때만 정수, 아니면 null.
- `quality_score`: 리뷰가 `ok`이고 보류된 항목이 없을 때만 정수, 아니면 null.
- `total_score`: 두 점수가 모두 정수일 때만 합계, 아니면 null.
- `run_status`: 기능 채점을 마치면 `graded`, 아니면 `incomplete`. 매핑 확인 후 검사가 실패하면 `mapping_status: verified`와 `run_status: incomplete`를 함께 쓴다.
- `review_status`: `ok`, `invalid_output`(스키마 위반 또는 다른 실행의 봉투), `unavailable`(모델 호출 실패·시간 초과), `off`, `skipped`(기능 채점 미완료).

CLI 종료 코드는 `0` 관측과 채점 완료, `2` 과제·입력 계약 오류, `3` 채점 미완료다. 기능 실패나 리뷰 실패만으로는 종료 코드가 바뀌지 않는다.

## 변경 규칙

계약의 각 부분은 **고정**, **추가만 허용**, **미고정** 중 한 등급에 속한다. 공통 봉투, 스냅샷 ID, 근거 참조 형식, 관측 행, 판정값, 점수 null 규칙, 종료 코드는 고정이며 바꾸려면 `schema_version`을 올린다. 등급별 대상과 변경 절차는 [평가 엔진 설계](../docs/engine-design.md#3-계약-안정성-등급)에 정리되어 있다.

## 검증

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

`tests/test_contracts.py`는 스키마 자체의 유효성, 예시의 스키마 적합성, 실행별 봉투 일치, 배점 합계, 리포트 점수 일관성, 도구 정의의 자기완결성을 검사한다.
