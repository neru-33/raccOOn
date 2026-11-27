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

## 라이선스

raccOOn의 자체 개발 코드는 [Apache License 2.0](LICENSE)을 따릅니다. 적용 범위와 외부 도구의 라이선스는 [라이선스 안내](LICENSE_NOTICE.md)에서 확인할 수 있습니다.
