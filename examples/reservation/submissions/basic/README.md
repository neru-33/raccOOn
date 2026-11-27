# 예약 API

정원이 있는 이벤트를 등록하고, 좌석을 예약하고, 예약 상태와 남은 자리를 조회하는 API입니다.

## 실행

`DATABASE_URL`로 PostgreSQL 접속 정보를 받습니다. 앱이 시작할 때 필요한 테이블(`events`, `reservations`)을 만듭니다.

```bash
docker compose up --build
```

소스만 제출할 때는 루트 `main.py`가 FastAPI 진입점이며 `requirements.txt`에 의존성이 선언되어 있습니다.

앱은 8080 포트에서 동작하며, `GET /health`가 DB 연결을 확인해 200을 돌려줍니다.

## API

### 이벤트 등록

```http
POST /events
Content-Type: application/json

{"capacity": 100}
```

```http
201 Created

{"id": "0b7e...", "capacity": 100, "remaining": 100}
```

### 이벤트 조회

```http
GET /events/{id}
```

```http
200 OK

{"id": "0b7e...", "capacity": 100, "remaining": 99}
```

### 예약 생성

```http
POST /reservations
Content-Type: application/json

{"eventId": "0b7e...", "userId": "user-1"}
```

```http
201 Created

{"id": "6f1c...", "eventId": "0b7e...", "userId": "user-1", "status": "CONFIRMED"}
```

정원이 찼으면 `409 Conflict`, 이벤트가 없으면 `404 Not Found`를 돌려줍니다.

### 예약 조회

```http
GET /reservations/{id}
```

```http
200 OK

{"id": "6f1c...", "eventId": "0b7e...", "userId": "user-1", "status": "CONFIRMED"}
```

## 동시성

예약 생성은 한 트랜잭션 안에서 `SELECT ... FOR UPDATE`로 이벤트 행을 잠근 뒤 확정 건수를 세고 저장합니다. 같은 이벤트에 동시에 들어온 요청은 잠금을 기다리므로, 마지막 좌석에는 한 건만 확정됩니다.

## 테스트

```bash
DATABASE_URL=postgresql://... pytest
```
