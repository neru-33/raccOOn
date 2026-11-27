# Enrollment Service

세션(이벤트)에 회원을 등록하는 서비스입니다.

## 실행

```bash
docker compose up --build
```

- 포트: 8080
- DB: `DATABASE_URL` 환경 변수(평가 환경이 제공)
- 상태 확인: `GET /health`

## 사용법

등록 요청:

```http
POST /reserve
Content-Type: application/json

{"session": "s-100", "member": "m-1"}
```

응답(201):

```json
{"data": {"reference": "a1b2...", "session": "s-100", "member": "m-1", "state": "accepted"}}
```

등록 조회:

```http
GET /enrollments/{reference}
```

응답(200):

```json
{"data": {"reference": "a1b2...", "session": "s-100", "member": "m-1", "state": "accepted"}}
```

## 동시성

등록 처리는 `asyncio.Lock`으로 한 번에 하나씩 실행되므로 마지막 자리를 두 명이 동시에 가져가지 않습니다.

## 테스트

```bash
cd src && DATABASE_URL=postgresql://... pytest ../tests
```
