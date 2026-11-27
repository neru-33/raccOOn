# 예약 과제 사용 예제

엔진으로 "정원이 있는 이벤트 예약" 과제를 평가하는 예제입니다. 엔진 코드는 이 폴더를 알지 못하며, 다른 과제도 같은 구성으로 추가할 수 있습니다.

## 구성

| 경로 | 내용 |
| --- | --- |
| `task.json` | 과제 설정: 논리 동작(예약 생성·조회), 지표, 배점, 품질 루브릭, 실행기 `compose-postgres-k6` 설정 |
| `fixtures/schema.sql` | 실행기가 기동 전에 적용하고 회차마다 초기화·시드·집계에 쓰는 DB 스키마 |
| `submissions/` | 같은 과제를 서로 다르게 구현한 예시 제출물 |

## 예시 제출물

세 제출물 모두 실행기 설정(Compose에는 앱만, 포트 8080, `GET /health`, `DATABASE_URL`, 공유 스키마)을 따르고, API 경로·필드·응답 구조는 서로 다릅니다.

| 제출물 | 생성 API | 동시성 처리 | 실행기 검사 결과 |
| --- | --- | --- | --- |
| [`basic`](submissions/basic) | `POST /reservations` | 트랜잭션 안에서 `SELECT ... FOR UPDATE`로 이벤트 행 잠금 | 세 회차 모두 확정 1건 |
| [`local-lock`](submissions/local-lock) | `POST /enrollments` | 프로세스 안의 `asyncio.Lock`. 워커 4개로 실행되어 워커 사이에서는 직렬화되지 않음. README의 생성 예시 경로(`/reserve`)가 실제와 다름 | 회차에 따라 2~4건 확정(정원 초과) |
| [`robust`](submissions/robust) | `POST /v1/events/{eventId}/bookings` | 트랜잭션 수준 advisory lock, 사용자 중복 예약 방지, DB에 저장하는 `Idempotency-Key`, RFC 9457 오류 형식, 커넥션 풀과 시간 제한 | 세 회차 모두 확정 1건 |

## 실행

Docker로 세 제출물을 실행기에 넣어 확인합니다.

```powershell
$env:RACCOON_DOCKER_TESTS = "1"; python -m pytest tests/test_example_apps_docker.py
```
