# 포팅 매뉴얼 (exec/)

작성일 2026-09-28 / 기준 커밋 `1b83057` (master) / 프로젝트 S15P21B109 — Journey Builder (RetireWise)

SSAFY 포팅 매뉴얼 제출 규격에 맞춰 아래 4개 항목을 이 폴더에 정리했다. 소스코드는 저장소 본문에 있으므로 별도로 넣지 않았다.

| 번호 | 요구 항목 | 파일 |
|---|---|---|
| 1 | Gitlab 소스 클론 이후 빌드 및 배포할 수 있도록 정리한 문서 (사용 제품·버전·IDE, 빌드 환경 변수, 배포 특이사항, DB 접속 정보·계정·프로퍼티 파일 목록) | [01-build-deploy.md](01-build-deploy.md) |
| 2 | 프로젝트에서 사용하는 외부 서비스 정보 | [02-external-services.md](02-external-services.md) |
| 3 | DB 덤프 파일 최신본 | [03-db-dump/](03-db-dump/) — `retirewise_db_schema.sql`(스키마), `retirewise_db_data.sql`(스키마+데이터), [README.md](03-db-dump/README.md)(생성·복원 방법) |
| 4 | 시연 시나리오 (화면별·클릭 위치별 상세) | [04-demo-scenario.md](04-demo-scenario.md) |

## 한눈에 보는 실행 요약

```bash
git clone https://lab.ssafy.com/s15-fintech-finance-sub1/S15P21B109.git
cd S15P21B109
bash deploy/make-env.sh              # 루트 .env 생성 (GOOGLE_API_KEY 는 편집기로 직접 입력)
docker compose config --quiet        # 필수 환경변수 검증
docker compose up --build -d         # postgres·redis·migrate·rag-bootstrap·backend·gateway·ai-pjt
docker compose exec backend python manage.py bootstrap_demo   # 시연 계정·시드 데이터
cd front-pjt && npm ci && npm run dev # http://localhost:5173
```

- 운영 주소: https://j15b109.p.ssafy.io (EC2, GitLab CI master 머지 시 자동 배포)
- 시연 계정: PB `admin1234@naver.com` / `admin1234`, 시드 고객 `demo001~040@retirewise.demo` / `demo-pass-2026!`

## 문서 작성 기준

- 버전은 `backend-pjt/requirements.lock`, `ai-pjt/requirements.lock`, `front-pjt/package-lock.json`, 각 `Dockerfile`, `docker-compose*.yml` 에 고정된 값을 그대로 옮겼다.
- 비밀값(`.env` 의 실제 값)은 어떤 문서에도 적지 않았다. 변수 이름과 생성 규칙만 적었다.
- DB 덤프는 로컬 개발 볼륨(`s15p21b109_postgres_data`)에 최신 마이그레이션을 적용한 뒤 2026-09-28 에 `pg_dump` 로 생성했고, 빈 PostgreSQL 에 복원되는 것을 확인했다. 재생성 가능한 대용량 데이터(RAG 벡터 청크, LangGraph 체크포인트, 세션·토큰 테이블)는 데이터에서 제외했다. 자세한 내용은 `03-db-dump/README.md` 를 본다.
