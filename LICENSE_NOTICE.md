# 라이선스 안내

[Apache License 2.0](LICENSE)은 raccOOn이 배포 권한을 가진 자체 개발 코드와 그 문서, 팀이 제작한 로고(`assets/raccOOn-pixel-dot-arcade.svg`, `assets/raccOOn-cli_ascii_logo.txt`)에 적용됩니다. 대학 프로젝트 템플릿은 별도 라이선스 표기를 확인할 수 없어 이 부여 범위에서 제외합니다.

## 설계상 사용하는 외부 도구

| 구성 요소 | 사용 방식 | 라이선스 |
| --- | --- | --- |
| Python | CLI 및 표준 라이브러리 | [PSF License](https://docs.python.org/3/license.html) |
| OpenAI Python SDK | 모델 API 클라이언트 | [Apache-2.0](https://github.com/openai/openai-python/blob/main/LICENSE) |
| Docker Compose | 제출 앱과 테스트 DB 기동 | [Apache-2.0](https://github.com/docker/compose/blob/main/LICENSE) |
| PostgreSQL | 테스트 DB 서버 | [PostgreSQL License](https://www.postgresql.org/about/licence/) |
| Grafana k6 | 별도 프로세스 또는 컨테이너에서 부하 검사 실행 | [AGPL-3.0](https://github.com/grafana/k6#license) |
| psycopg | 실행기 검증용 테스트 앱(`tests/runner_app`) 컨테이너 빌드 시 설치 | [LGPL-3.0](https://github.com/psycopg/psycopg/blob/master/LICENSE.txt) |

외부 도구에는 각 배포본의 조건이 적용됩니다. k6 바이너리나 컨테이너 이미지를 재배포할 경우 해당 배포본의 라이선스 고지 및 소스 제공 조건을 확인합니다.
