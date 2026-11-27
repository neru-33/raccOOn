# 실행기 `compose-postgres-k6`

Docker Compose로 제출 앱을 띄우고 PostgreSQL을 제공하며, 동시 요청은 k6로 보낸다. 시험 데이터 준비와 집계는 **제출물의 API로** 한다. 실행기는 제출물의 테이블을 읽거나 쓰지 않으므로 제출물은 저장 구조를 자유롭게 설계할 수 있다. 구현은 `src/evaluator/runners/compose_postgres_k6.py`이고, `task.json`의 `runner_id`가 `compose-postgres-k6`이면 `evaluator.runners.open_session`이 이 실행기를 만든다.

## 필요 환경

- Docker Engine과 Docker Compose v2
- 실행 중 내려받거나 사용하는 이미지: `runner_config.tools`의 `postgres`, `k6` 이미지
- 소스 자동 빌드에는 Linux 컨테이너를 실행하는 Docker와 Docker Buildx가 필요하다. 실행기가 Railpack v0.44.0 이미지와 빌더·런타임 이미지를 준비한다.
- 파이썬 추가 의존성 없음. HTTP는 표준 라이브러리로 한다.

## 실행 순서

| 단계 | 동작 | 실패 시 |
| --- | --- | --- |
| 1 | Docker 데몬 확인 | `environment` |
| 2 | 제출본 기동 방식 선택 및 자동 빌드(필요 시) | 도구·기본 이미지 준비 실패는 `environment`, 스택 판별·제출물 빌드 실패는 `submission` |
| 3 | 도구 이미지 준비(없으면 pull) | `environment` |
| 4 | 덮어쓰기 파일 작성 후 `docker compose config` | `submission` |
| 5 | DB 서비스 기동(`--wait`) | `environment` |
| 6 | 앱 빌드·기동(`--no-deps`), 앱 포트를 `127.0.0.1`의 임의 포트로 공개 | `submission` |
| 7 | `ready_timeout_seconds` 안에 `GET health_path`가 200이면 준비 완료. 앱 컨테이너가 먼저 종료되면 즉시 실패 | `submission` |
| 8 | 앱에서 OpenAPI 문서를 찾아 `discovered`에 담음(`/openapi.json`, `/v3/api-docs`, `/swagger.json`, `/docs/openapi.json`) | 없으면 빈 배열 |

`start()`가 실패하면 `start_submission_result.error`에 `stage`, `cause`, 로그 발췌(DB 비밀번호는 가림)를 담는다. Compose 프로젝트 이름은 `raccoon-<run_id>`이고, `close()`는 `down -v --remove-orphans`로 이 실행의 컨테이너·볼륨·네트워크를 모두 지운다.

### 기동 방식 선택

1. `app.compose_file`(예약 과제는 `compose.yaml`)이 있으면 그 파일을 사용한다.
2. 없고 제출물 루트에 `Dockerfile`이 있으면 `app.service` 이름의 서비스 하나에 `build: .`을 지정한 Compose 파일을 생성한다.
3. 두 파일 모두 없으면 Railpack이 소스의 스택과 시작 명령을 판별한다. Docker Buildx로 계획을 빌드하고 생성한 이미지로 앱 서비스를 구성한다. FastAPI의 경우 루트 `main.py`에서 `app`을 노출하고 의존성에 `fastapi`, `uvicorn`을 선언한다. [Railpack FastAPI 지원](https://railpack.com/languages/python/#fastapi)

실행기가 생성하는 파일은 작업 디렉터리에 둔다. 모델은 빌드와 시작 명령에 관여하지 않는다. `observations.json`의 `runner_info.build_mode`에 각각 `compose`, `dockerfile`, `auto`를 기록한다. 실행을 정리할 때 자동 빌드로 생성한 앱 이미지 태그도 삭제한다.

Railpack은 공식 Linux 이미지를 사용해 계획을 생성하고 Buildx에 전달한다. Windows Docker Desktop에서도 같은 경로를 사용한다. Paketo Python의 기본 시작 명령은 `python`이므로 명령을 따로 선언하지 않은 FastAPI 제출물에는 Railpack을 선택했다. [Railpack BuildKit 프런트엔드](https://railpack.com/platforms/buildkit-frontend) · [Paketo Python 시작 명령](https://paketo.io/docs/reference/python-reference/#start-command)

Windows Docker Desktop에서 `basic` 소스로 측정한 첫 계획 생성은 약 16초, 빌더 레이어와 앱 의존성 다운로드를 포함한 첫 이미지 빌드는 약 121초였다. Docker가 보고한 빌더 이미지 크기는 1.69 GB, 프런트엔드는 46 MB, 생성한 앱은 588 MB였다. 다운로드 속도와 캐시 상태에 따라 시간과 디스크 사용량은 달라진다.

## 덮어쓰기 파일

실행기는 제출본 Compose 파일 위에 다음 서비스를 붙인다.

| 서비스 | 내용 |
| --- | --- |
| 앱(`app.service`) | `app.database_url_env` 이름으로 DB 접속 URL 주입, `PORT`에 `app.port` 주입, 같은 컨테이너 포트를 `127.0.0.1`의 임의 포트로 공개 |
| `raccoon-db` | `postgres` 도구 이미지, 실행마다 무작위 비밀번호, `pg_isready` 헬스체크. 비어 있는 DB이며 테이블은 제출물이 만든다 |
| `raccoon-k6` | `k6` 도구 이미지, `tools` 프로필(검사 때만 `docker compose run`으로 실행) |

## `runner_config` 키

| 키 | 내용 |
| --- | --- |
| `database.mode` | `engine_managed`만 구현되어 있다. `external`은 아직 `environment` 오류로 끝난다 |
| `tools` | `postgres`, `k6` 항목 필수. k6의 `options.batch`, `options.batchPerHost` 사용 |
| `app` | `compose_file`, `service`, `port`, `health_path`, `ready_timeout_seconds`, `env`, `database_url_env`(`env`에 선언된 이름 중 하나) |
| `inputs` | `from`으로 연결되지 않은 동작 입력의 값 출처. `scenario_capacity`(이번 시험·회차의 정원), `unique_user`(요청마다 다른 사용자 ID) |
| `accounting` | 준비와 집계에 쓰는 논리 동작. 아래 표 |
| `probe.event_capacity` | 매핑 시험에서 등록할 이벤트의 정원 |
| `scenarios.<test_id>` | 검사 방식. 아래 두 종류 |

### `accounting`

| 키 | 내용 |
| --- | --- |
| `setup_operation` | 시험·회차마다 정원 있는 대상을 만드는 동작(예: 이벤트 등록). `from` 입력을 가질 수 없다 |
| `reserve_operation` | 동시 요청으로 보낼 동작(예: 예약 생성). `from` 입력은 `setup_operation`의 출력만 가리킬 수 있다 |
| `confirmed_output` | `reserve_operation`의 출력 중 확정 여부를 나타내는 것 |
| `count_operation` | 대상의 상태를 읽는 동작(예: 이벤트 조회). `from` 입력은 `setup_operation`의 출력만 가리킬 수 있다 |
| `count_output` | `count_operation`의 정수 출력 |
| `count_mode` | `remaining`: 출력이 남은 자리라서 확정 건수 = 정원 − 출력. `confirmed`: 출력이 확정 건수 |

**매핑 시험의 집계 확인.** 매핑 시험은 모든 동작을 선언 순서대로 한 번 호출한다(새 대상 등록 → 예약 1건 → 예약 조회 → 대상 조회). 그 뒤 `count_operation`으로 계산한 확정 건수가 방금 만든 예약의 확정 여부(1 또는 0)와 다르면 매핑을 확인하지 않고 `failed_stage: response_field`로 돌려준다. 정원이나 다른 숫자 필드를 남은 자리로 잘못 연결한 후보를 걸러낸다.

### 검사 방식

**`operation_sequence`**: 회차마다 정원 `event_capacity`로 모든 동작을 선언 순서대로 한 번씩 호출한다. `metrics`는 지표 이름에서 동작 이름으로의 대응이며, 그 동작이 성공하고 `expect_outputs`의 값과 응답 출력이 같으면 `true`다. 앞 동작이 실패해 뒤 동작을 부르지 못하면 뒤 지표는 `false`다. 응답을 받지 못한 요청이 있으면 그 회차는 실행 오류다.

**`concurrent_burst`**: 회차마다 `setup_operation`으로 정원 `event_capacity`인 대상을 만들고, `reserve_operation`을 `requests`건 한 번에 보낸다(k6 `http.batch`). 응답 중 `success_codes`에 들고 `confirmed_output`이 확정으로 해석된 수를 `response_metric`, 모든 응답을 받은 뒤 `count_operation`으로 계산한 확정 건수를 `persisted_metric`에 기록한다.

- `response_metric`은 실행기가 응답을 직접 센 값이고, `persisted_metric`은 **제출물이 API로 보고한 값**이다. 제출물은 남은 자리를 0 아래로 보여 주지 않는 식으로 실제보다 적게 보고할 수 있다. 그래서 정원 초과를 0점 처리하는 규칙은 `response_metric`을 기준으로 두고, `persisted_metric`은 회차별 일치 확인에 쓴다. 집계 출처는 `observations.json`의 `runner_info.count_source`에 남는다.
- 한 요청이라도 응답이 없거나 시간 초과면 늦은 커밋 가능성 때문에 집계를 쓰지 않고 그 회차를 실행 오류(`load_no_response`)로 남긴다.
- 준비나 집계 동작이 실패하면 그 회차는 실행 오류(`setup`, `count`)다.

실행 오류가 나면 그때까지 완료된 회차의 관측 행을 보존하고 `observations.json`을 `execution_error`로 끝낸다.

## 도구 호출 오류

`probe_mapping`, `run_checks`는 호출할 수 없는 상황에서 `evaluator.runners.compose_postgres_k6.ToolError`를 던진다. `code`는 `tool_error` 코드다.

| 코드 | 상황 |
| --- | --- |
| `not_started` | `start()`가 성공하기 전에 호출 |
| `unknown_mapping` | 이번 실행에서 확인되지 않은 `mapping_id`로 `run_checks` 호출 |
| `invalid_arguments` | `documented_operations`가 객체가 아님 |

후보가 실행할 수 없는 형태(동작 누락, 절대 URL, 선언되지 않은 변수 등)이면 예외 대신 `failed_stage: candidate_schema`인 시험 결과를 돌려주고 시험 횟수에 포함한다.

## 테스트

```powershell
python -m pytest
```

Docker 없이 도는 단위 테스트가 템플릿, 매핑 시험, k6 스크립트, 덮어쓰기 파일을 검사한다. 실제 Docker로 끝까지 확인하려면 다음을 실행한다. `tests/runner_app`의 검증용 앱(정상·정원 초과·환경 변수 오구현 모드)을 빌드해 기동, 매핑 시험, 검사 3회, 정리까지 확인한다.

```powershell
$env:RACCOON_DOCKER_TESTS = "1"
python -m pytest tests/test_runner_docker.py tests/test_example_apps_docker.py
```
