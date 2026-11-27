<img width="490" height="144" alt="raccOOn-pixel-dot-arcade" src="./assets/raccOOn-pixel-dot-arcade.svg" />

## 개발 환경

Python 3.11 이상이 필요합니다.

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## 문서

- [평가 엔진 설계](docs/engine-design.md): 모듈 책임, 불변 원칙, 계약 안정성 등급, 확장 방법, 변경 절차
- [계약 v1](contracts/README.md): 과제 설정과 산출물의 JSON 형식
- [실행기 compose-postgres-k6](docs/runners/compose-postgres-k6.md): Docker Compose·PostgreSQL·k6 실행기의 설정과 동작, Docker 통합 테스트

## 예제 앱

`examples/`에는 같은 예약 과제(`tasks/reservation/task.json`)를 서로 다르게 구현한 제출물 세 개가 있습니다. 세 앱 모두 실행 규약(Compose에는 앱만, 포트 8080, `GET /health`, `DATABASE_URL`)을 따르고, API 경로·필드·응답 구조는 서로 다릅니다.

| 앱 | 생성 API | 동시성 처리 | 실행기 검사 결과 |
| --- | --- | --- | --- |
| [`basic`](examples/basic) | `POST /reservations` | 트랜잭션 안에서 `SELECT ... FOR UPDATE`로 이벤트 행 잠금 | 세 회차 모두 확정 1건 |
| [`local-lock`](examples/local-lock) | `POST /enrollments` | 프로세스 안의 `asyncio.Lock`. 워커 4개로 실행되어 워커 사이에서는 직렬화되지 않음. README의 생성 예시 경로(`/reserve`)가 실제와 다름 | 회차에 따라 2~4건 확정(정원 초과) |
| [`robust`](examples/robust) | `POST /v1/events/{eventId}/bookings` | 트랜잭션 수준 advisory lock, 사용자 중복 예약 방지, DB에 저장하는 `Idempotency-Key`, RFC 9457 오류 형식, 커넥션 풀과 시간 제한 | 세 회차 모두 확정 1건 |

Docker로 세 앱을 실행기에 넣어 확인하려면 다음을 실행합니다.

```powershell
$env:RACCOON_DOCKER_TESTS = "1"; python -m pytest tests/test_example_apps_docker.py
```

## 라이선스

raccOOn의 자체 개발 코드는 [Apache License 2.0](LICENSE)을 따릅니다. 적용 범위와 외부 도구의 라이선스는 [라이선스 안내](LICENSE_NOTICE.md)에서 확인할 수 있습니다.
