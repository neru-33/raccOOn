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

## 사용 예제

엔진은 특정 과제에 묶이지 않습니다. 과제는 `task.json`(과제 설정)과 실행기 설정으로 주어지고, 제출물은 그 설정으로 평가됩니다. [`examples/reservation/`](examples/reservation)은 이 엔진으로 예약 과제를 출제하고 평가하는 사용 예제입니다.

| 경로 | 내용 |
| --- | --- |
| `examples/reservation/task.json` | 예약 과제 설정 |
| `examples/reservation/submissions/` | 같은 과제를 서로 다르게 구현한 예시 제출물 |

자세한 내용은 [예약 사용 예제](examples/reservation/README.md)를 참고하세요.

## 라이선스

raccOOn의 자체 개발 코드는 [Apache License 2.0](LICENSE)을 따릅니다. 적용 범위와 외부 도구의 라이선스는 [라이선스 안내](LICENSE_NOTICE.md)에서 확인할 수 있습니다.
