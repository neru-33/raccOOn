<img width="490" height="144" alt="raccOOn-pixel-dot-arcade" src="./assets/raccOOn-pixel-dot-arcade.svg" />

# raccOOn

raccOOn은 과제 설정과 제출물을 받아 실제 동작을 검사하고, 기능 점수와 품질 리뷰 근거를 리포트로 남기는 CLI 평가 엔진입니다. 평가 에이전트는 제출물의 문서와 소스에서 API 호출법을 찾고, 실행기가 시험 요청으로 확인한 매핑을 사용해 검사합니다.

예약 사례 외에도 과제별 `task.json`과 실행기 설정을 지정해 같은 엔진으로 평가할 수 있습니다.

## 1. 실행 준비

Python 3.11 이상, Linux 컨테이너를 실행할 수 있는 Docker, `docker compose` 명령이 필요합니다. 아래 명령은 저장소 루트에서 PowerShell로 실행합니다.

```powershell
python -m pip install -e ".[model,dev]"
docker info --format '{{.ServerVersion}}'
docker compose version
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`에 `EVALUATOR_API_KEY`와 도구 호출을 지원하는 모델 이름(`EVALUATOR_MODEL`)을 입력합니다. 별도 OpenAI 호환 엔드포인트를 사용하면 `EVALUATOR_BASE_URL`도 지정합니다. 엔드포인트가 JSON Schema 응답 형식을 지원하지 않으면 `EVALUATOR_RESPONSE_FORMAT=none`을 설정합니다. [환경 변수 예시](.env.example)

## 2. 예약 예제 평가

같은 과제 설정으로 API가 다른 `basic`, `local-lock`, `robust` 세 제출물을 평가할 수 있습니다. 아래는 `basic`을 실행하는 예입니다. 첫 실행에서는 Docker가 필요한 이미지를 내려받습니다.

```powershell
python -m evaluator run --task examples/reservation/task.json `
  --submission examples/reservation/submissions/basic `
  --out runs/basic --review live
```

다른 예제를 평가하려면 `--submission`의 마지막 폴더 이름과 `--out` 경로를 바꿉니다. 출력 디렉터리는 실행할 때마다 새 경로를 지정합니다. `--review live`는 품질 리뷰를 실행하고, 기본값인 `--review off`는 기능만 채점합니다. 예약 예제의 API 탐색에는 두 모드 모두 모델 연결이 필요합니다. [예약 과제와 예제 제출물](examples/reservation/README.md)

예약 예제는 예약 생성·조회를 검사한 뒤, 정원 1명인 이벤트에 동시 요청 50건을 보내는 검사를 3회 실행합니다. 실행기는 확정 응답 수를 직접 세고, 이벤트 조회 API가 반환한 남은 자리로 확정 건수를 계산합니다. 예약 과제의 배점은 기능 40점, 품질 60점입니다. [예약 과제 설정](examples/reservation/task.json)

대화형 터미널에서 `evaluator run`을 실행하면 로고, 진행 단계, 최종 점수와 리포트 경로가 표시됩니다. 제출물 준비·기동, 모델 응답 대기, API 매핑 시험, 검사 실행은 각 단계가 호출될 때 표시됩니다. 실행기가 검사 진행 정보를 제공하면 검사 ID와 회차도 표시됩니다.

진행 화면은 표준 오류(`stderr`)를 사용합니다. CI에서 실행하거나 `stderr`를 리다이렉트하면 진행 화면이 숨겨집니다.

## 3. 결과 확인

`--out`에 지정한 디렉터리의 `report.md`를 열어 점수와 보류 사유를 확인할 수 있습니다.

| 파일 | 내용 |
| --- | --- |
| `report.md` | 사람이 읽는 요약. 채점이 끝난 `--review live` 실행에서는 모델 해설도 생성합니다. |
| `report.json` | 기능·품질·총점과 판정 근거, 실행 상태, 오류 기록 |
| `mapping.json` | 매핑 시험 이력과 확인된 API 호출법 |
| `observations.json` | 검사 회차별 관측값과 실행 도구 정보 |
| `ai_review.json` | 검증을 통과한 품질 판정, 코드·관측 근거 참조, 원인 가설 |

매핑·관측·리뷰 파일은 해당 단계에서 유효한 산출물이 생겼을 때 기록됩니다. [리포트 형식](docs/reporting.md)

## 4. 테스트 실행

```powershell
python -m pytest
```

Docker에서 예제 앱, 실행기, CLI 흐름을 확인하려면 통합 테스트를 켭니다. CLI 테스트는 모의 모델을 사용하므로 API 키 없이 실행할 수 있습니다.

```powershell
$env:RACCOON_DOCKER_TESTS = "1"
python -m pytest tests/test_example_apps_docker.py tests/test_runner_docker.py
```

## 문서

- 모듈의 책임, 불변 원칙, 계약 안정성 등급, 과제 확장 방법과 변경 절차를 설명합니다. [평가 엔진 설계](docs/engine-design.md)
- 과제 설정과 산출물의 JSON 형식은 [계약 v1](contracts/README.md)을 참고하세요.
- 제출물 기동, PostgreSQL 연결, k6 검사 설정은 [실행기 문서](docs/runners/compose-postgres-k6.md)를 참고하세요.

## 라이선스

raccOOn의 자체 개발 코드는 [Apache License 2.0](LICENSE)을 따릅니다. 적용 범위와 외부 도구의 라이선스는 [라이선스 안내](LICENSE_NOTICE.md)에서 확인할 수 있습니다.
