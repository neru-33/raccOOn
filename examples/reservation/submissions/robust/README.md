# Booking API

정원이 있는 이벤트를 등록하고 좌석을 예약하는 API입니다. 마지막 좌석에 요청이 몰려도 한 건만 확정되고, 같은 사용자의 중복 예약과 네트워크 재시도로 인한 이중 처리를 막습니다.

## 실행

```bash
docker compose up --build
```

| 항목 | 값 |
| --- | --- |
| 포트 | 8080 |
| DB | `DATABASE_URL` (PostgreSQL 접속 정보) |
| 상태 확인 | `GET /health` — 커넥션 풀에서 DB 연결을 확인해 200, 실패 시 503 |
| 워커 | `WEB_CONCURRENCY`(기본 4개 프로세스) |
| 시간 제한 | `DB_LOCK_TIMEOUT_MS`(기본 3000), `DB_STATEMENT_TIMEOUT_MS`(기본 5000), `DB_POOL_MAX_SIZE`(기본 10) |

시작할 때 테이블(`events`, `bookings`, `idempotency_keys`)과 인덱스를 만듭니다(`IF NOT EXISTS`).

## API

### 이벤트 등록

```http
POST /v1/events
Content-Type: application/json

{"capacity": 100}
```

```http
201 Created
Location: /v1/events/0b7e...

{"event": {"eventId": "0b7e...", "capacity": 100, "remainingSeats": 100, "createdAt": "..."}}
```

### 이벤트 조회

```http
GET /v1/events/{eventId}
```

```http
200 OK

{"event": {"eventId": "0b7e...", "capacity": 100, "remainingSeats": 99, "createdAt": "..."}}
```

### 예약 생성

```http
POST /v1/events/{eventId}/bookings
Content-Type: application/json
Idempotency-Key: 5f0c2b1e-8a7d-4a51-9a39-0d7b8a1c2f44

{"userId": "user-1"}
```

```http
201 Created
Location: /v1/bookings/7d3f...

{"booking": {"bookingId": "7d3f...", "eventId": "evt-1", "userId": "user-1", "status": "confirmed", "createdAt": "2026-11-28T09:00:00.123456+00:00"}}
```

`Idempotency-Key`는 선택입니다. 보내면 같은 키로 다시 요청했을 때 처음 응답을 그대로 돌려줍니다(`Idempotent-Replayed: true` 헤더).

### 예약 조회

```http
GET /v1/bookings/{bookingId}
```

```http
200 OK

{"booking": {"bookingId": "7d3f...", "eventId": "evt-1", "userId": "user-1", "status": "confirmed", "createdAt": "..."}}
```

### 실패 응답

모든 오류는 [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) Problem Details(`application/problem+json`)로 돌려줍니다.

| 상태 | `type` | 상황 |
| --- | --- | --- |
| 404 | `/problems/event-not-found` | 이벤트가 없음 |
| 409 | `/problems/sold-out` | 남은 좌석 없음 |
| 409 | `/problems/already-booked` | 같은 사용자가 이미 좌석을 가짐 |
| 400 | `/problems/invalid-idempotency-key` | 키 길이가 1~255자를 벗어남 |
| 422 | `/problems/idempotency-key-reused` | 같은 키를 다른 요청에 사용 |
| 422 | `/problems/invalid-request` | 요청 본문 형식 오류 |
| 503 | `/problems/temporarily-unavailable` | 잠금 대기·쿼리 시간 초과, 커넥션 풀 고갈. `Retry-After: 1`, 같은 키로 재시도 가능 |

```json
{"type": "/problems/sold-out", "title": "Sold out", "status": 409, "detail": "event evt-1 has no seats left", "instance": "/v1/events/evt-1/bookings"}
```

## 설계

| 문제 | 선택 | 이유 |
| --- | --- | --- |
| 마지막 좌석 경쟁 | 요청마다 한 트랜잭션. 이벤트 ID로 트랜잭션 수준 advisory lock(`pg_advisory_xact_lock`)을 잡은 뒤 확정 건수를 세고 저장 | 잠금이 DB에 있어 워커·인스턴스가 늘어나도 같은 이벤트의 예약이 직렬화된다. 트랜잭션이 끝나면 잠금이 자동으로 풀려 커밋 전에 잠금이 풀리는 문제가 없다 |
| 프로세스 메모리 락을 쓰지 않음 | `asyncio.Lock`, 전역 변수, 메모리 캐시로 동시성을 제어하지 않음 | 워커 프로세스마다 메모리가 분리되어 있어 다중 워커에서 효과가 없다 |
| 같은 사용자 중복 예약 | 같은 잠금 안에서 사용자 예약을 확인하고 409. 부분 유니크 인덱스(`(event_id, user_id) WHERE status = 'confirmed'`)로 DB에서도 막음 | 확인과 저장 사이에 다른 요청이 끼어들 틈이 없고, 잠금을 거치지 않는 경로가 생겨도 DB가 마지막으로 막는다 |
| 재시도로 인한 이중 처리 | `Idempotency-Key`를 예약과 같은 트랜잭션에 저장. 같은 키·같은 요청이면 저장된 응답 재생, 다른 요청이면 422 | 예약과 키 기록이 함께 커밋되거나 함께 롤백된다. 성공뿐 아니라 409 같은 최종 결과도 저장해 재시도해도 결과가 바뀌지 않는다. 5xx는 저장하지 않아 같은 키로 재시도할 수 있다 |
| 같은 키 동시 요청 | 두 번째 요청은 첫 트랜잭션이 끝날 때까지 기다린 뒤 저장된 응답을 받음 | 기본 키 충돌 시 PostgreSQL이 앞선 트랜잭션의 결과를 기다린다. 대기는 `lock_timeout`으로 제한된다 |
| 느린 DB와 긴 잠금 | 커넥션 풀, `lock_timeout`, `statement_timeout`. 초과 시 503과 `Retry-After` | 무한 대기 대신 빠르게 실패하고 재시도를 안내한다 |
| 다중 워커 동시 기동 | 앱 전용 테이블 생성 시 advisory lock으로 직렬화 | 여러 프로세스의 `CREATE TABLE IF NOT EXISTS`가 동시에 실행되면 충돌할 수 있다 |

참고:
- PostgreSQL 13.3 [Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html) — 행 잠금과 advisory lock
- IETF [The Idempotency-Key HTTP Header Field](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header), [Stripe Idempotent requests](https://docs.stripe.com/api/idempotent_requests), [토스페이먼츠 멱등키](https://docs.tosspayments.com/reference/using-api/idempotency-key), [Implementing Stripe-like Idempotency Keys in Postgres](https://brandur.org/idempotency-keys)
- FastAPI [Server Workers](https://fastapi.tiangolo.com/deployment/server-workers/) — 워커는 별도 프로세스

## 한계

- 멱등키 기록의 만료·정리는 구현하지 않았다. 운영에서는 24시간 이상 지난 기록을 지우는 작업이 필요하다.
- 같은 이벤트의 예약은 직렬화되므로 이벤트 하나에 요청이 극단적으로 몰리면 대기 시간이 늘어난다. 이 경우 `lock_timeout`을 넘긴 요청은 503으로 끝난다.
- 예약 취소와 정원 변경은 범위 밖이다.

## 테스트

```bash
pytest tests/test_fingerprint.py                 # DB 없이
DATABASE_URL=postgresql://... pytest             # 동시 요청, 중복 예약, 멱등키 재생 포함
```
