# Enrollment Service

세션(이벤트)을 열고 회원을 등록하는 서비스입니다.

## 실행

```bash
docker compose up --build
```

- 포트: 8080
- DB: `DATABASE_URL` 환경 변수. 시작할 때 테이블을 만듭니다.
- 상태 확인: `GET /health`

## 사용법

세션 열기:

```http
POST /sessions
Content-Type: application/json

{"seats": 30}
```

응답(201):

```json
{"data": {"id": "3f9a1c2b7d4e", "seats": 30, "available": 30}}
```

세션 상태(남은 자리):

```http
GET /sessions/{id}
```

```json
{"data": {"id": "3f9a1c2b7d4e", "seats": 30, "available": 29}}
```

등록 요청:

```http
POST /reserve
Content-Type: application/json

{"session": "3f9a1c2b7d4e", "member": "m-1"}
```

응답(201):

```json
{"data": {"reference": "a1b2...", "session": "3f9a1c2b7d4e", "member": "m-1", "state": "accepted"}}
```

등록 조회:

```http
GET /enrollments/{reference}
```

응답(200):

```json
{"data": {"reference": "a1b2...", "session": "3f9a1c2b7d4e", "member": "m-1", "state": "accepted"}}
```

## 동시성

등록 처리는 `asyncio.Lock`으로 한 번에 하나씩 실행되므로 마지막 자리를 두 명이 동시에 가져가지 않습니다.

## 테스트

```bash
cd src && DATABASE_URL=postgresql://... pytest ../tests
```
