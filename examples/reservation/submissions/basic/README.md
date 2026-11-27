# 예약 API

정원이 있는 이벤트의 좌석을 예약하고, 예약 상태를 조회하는 API입니다.

## 실행

평가 환경은 `DATABASE_URL`로 PostgreSQL 접속 정보를 주입하고 `events`, `reservations` 테이블을 미리 만들어 둡니다.

```bash
docker compose up --build
```

앱은 8080 포트에서 동작하며, `GET /health`가 DB 연결을 확인해 200을 돌려줍니다.

## API

### 예약 생성

```http
POST /reservations
Content-Type: application/json

{"eventId": "evt-1", "userId": "user-1"}
```

```http
201 Created

{"id": "6f1c...", "eventId": "evt-1", "userId": "user-1", "status": "CONFIRMED"}
```

정원이 찼으면 `409 Conflict`, 이벤트가 없으면 `404 Not Found`를 돌려줍니다.

### 예약 조회

```http
GET /reservations/{id}
```

```http
200 OK

{"id": "6f1c...", "eventId": "evt-1", "userId": "user-1", "status": "CONFIRMED"}
```

## 동시성

예약 생성은 한 트랜잭션 안에서 `SELECT ... FOR UPDATE`로 이벤트 행을 잠근 뒤 확정 건수를 세고 저장합니다. 같은 이벤트에 동시에 들어온 요청은 잠금을 기다리므로, 마지막 좌석에는 한 건만 확정됩니다.

## 테스트

```bash
DATABASE_URL=postgresql://... pytest
```
