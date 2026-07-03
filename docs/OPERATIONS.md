# AIVIS 운영 매뉴얼 (OPERATIONS.md)

> 현장 산업용 PC 단일 호스트 운영 기준 (CLAUDE.md §3·§4, §12 인수 산출물).
> 대상 독자: 설치/운영 담당자. 시스템 개념은 [`ARCHITECTURE.md`](./ARCHITECTURE.md),
> 화면 사용법은 [`USER_GUIDE.md`](./USER_GUIDE.md) 참조.

## 1. 사전 요구사항

- Docker Engine + Docker Compose v2 (`docker compose ...`).
- (GPU 가속 시) NVIDIA 드라이버 + NVIDIA Container Toolkit.
- 디스크: 이미지 스토리지(MinIO) 용량 확보(검사 이미지 누적).

## 2. 설치 / 기동

```bash
# 1) 환경변수 준비 — 비밀번호/시크릿을 현장 값으로 수정
cp .env.example .env
#    최소 변경 권장: POSTGRES_PASSWORD, MINIO_ROOT_PASSWORD, JWT_SECRET,
#                   AIVIS_SEED_ADMIN_PASSWORD, DATABASE_URL(비밀번호 동기화)

# 2) 전체 스택 기동 (postgres / minio / api / vision / hmi / dashboard)
docker compose up -d

# 3) 상태 확인 — 모든 서비스 healthy 인지
docker compose ps

# 4) (운영 postgres) DB 스키마 마이그레이션 — 최초 1회 + 업그레이드 시
docker compose exec api alembic upgrade head
```

> 참고: 개발/테스트(sqlite)는 api 가 기동 시 테이블을 자동 생성하지만, **운영 postgres
> 는 자동 생성하지 않는다.** 반드시 `alembic upgrade head` 를 실행한다. 최초 기동 시
> `AIVIS_SEED_ADMIN_USER`/`AIVIS_SEED_ADMIN_PASSWORD` 로 admin 계정이 시드된다
> (기본 `admin` / `.env` 값). 운영 전 반드시 비밀번호를 변경한다.

### 접속 주소 (기본 포트)

| 서비스 | URL | 설명 |
|---|---|---|
| API (FastAPI) | http://localhost:8000 | REST + WS, `/health`, Swagger `/docs` |
| HMI (작업자) | http://localhost:5173 | 실시간 검사화면/NG 알람/재확인 |
| Dashboard (관리자) | http://localhost:5174 | LOT 이력·통계·KPI 리포트 |
| MinIO 콘솔 | http://localhost:9001 | 이미지 객체 스토리지 콘솔 |
| MinIO API(S3) | http://localhost:9000 | S3 호환 엔드포인트 |
| PostgreSQL | localhost:5432 | 검사결과·기준정보·KPI |

### 종료 / 재기동

```bash
docker compose stop                 # 중지(데이터·컨테이너 유지)
docker compose up -d                # 재기동
docker compose down                 # 컨테이너 제거(볼륨 데이터 유지)
docker compose down -v              # 볼륨까지 삭제 (주의: 데이터 영구 소거)
docker compose restart api          # 단일 서비스 재시작
```

모든 서비스는 `restart: unless-stopped` 라 호스트 재부팅/크래시 후 자동 재기동된다.

## 3. 환경변수 (.env)

전체 키와 기본값은 `.env.example` 주석 참조. 핵심 그룹:

| 그룹 | 키 | 기본 | 비고 |
|---|---|---|---|
| Postgres | `POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL` | aivis | DATABASE_URL 의 비밀번호를 POSTGRES_PASSWORD 와 일치시킬 것 |
| MinIO | `MINIO_ROOT_USER/PASSWORD`, `MINIO_ACCESS/SECRET_KEY`, `MINIO_BUCKET_RAW/RESULT/REVIEW` | aivis-minio / raw·result·review | 이미지 버킷 분리(§6.4) |
| 카메라/HAL | `AIVIS_CAMERA`(sim\|genicam), `AIVIS_DATASET_DIR`, `AIVIS_TRIGGER`(timer\|filewatch\|dio\|mqtt) | sim / /data/dataset / timer | 실카메라는 §6 참조 |
| 추론 | `AIVIS_ONNX_PROVIDERS`, `AIVIS_SURFACE_ONNX` | CPU / (미설정→고전 CV 폴백) | GPU 는 override 가 cuda 설정 |
| 인증 | `JWT_SECRET/ALGORITHM/EXPIRE_MINUTES`, `AIVIS_SEED_ADMIN_USER/PASSWORD`, `AIVIS_SEED_ON_STARTUP` | (변경필수) / admin / true | RBAC operator/quality/admin |
| 내부호출 | `AIVIS_SERVICE_TOKEN` | (미설정=무인증 화이트리스트) | 설정 시 vision→api 내부호출에 X-Service-Token 요구 |
| 알람 | `AIVIS_CONSEC_NG_THRESHOLD` | 3 | cam 단위 연속 NG 임계(M6) |
| 저장백업 | `AIVIS_LOCAL_QUEUE_DIR` | services/api/local_queue | DB 저장 실패 시 로컬 큐 백업(M7) |
| MES | `MES_MODE`(table\|rest), `MES_REST_URL`, `MES_WATCHDOG_INTERVAL_S`, `MES_MAX_RETRY`, `MES_BACKOFF_*` | table / 10s / 8 | 연계율 100% 워치독(§7.3) |
| 로깅 | `LOG_LEVEL` | INFO | |

`.env` 변경 후에는 영향 서비스를 재기동한다: `docker compose up -d <service>`.

## 4. 헬스체크 / 상태 진단

```bash
docker compose ps                       # 각 서비스 STATUS(healthy/unhealthy)
curl -fsS http://localhost:8000/health  # {"status":"ok","db":"up","mes_mode":"table"}
curl -fsS http://localhost:5173/healthz # hmi nginx → "ok"
curl -fsS http://localhost:5174/healthz # dashboard nginx → "ok"
```

| 서비스 | 헬스 판정 | 비고 |
|---|---|---|
| postgres | `pg_isready` | start_period 20s |
| minio | `/minio/health/live` | start_period 20s |
| api | `GET /health` (DB 연결 확인) | DB 다운 시 `degraded` |
| vision | `/tmp/vision_ready` 하트비트 파일 | 워커 루프 기동 후 생성 |
| hmi/dashboard | `GET /healthz` (nginx) | |

## 5. 로그

```bash
docker compose logs -f api              # 실시간(서비스별)
docker compose logs --tail=200 vision
docker compose logs                     # 전체
```

- 애플리케이션 로그: 컨테이너 stdout (Docker 로그 드라이버). `LOG_LEVEL` 로 상세도 조정.
- 검사/저장/MES/오류/사용자 조작 감사 로그(M15)는 DB `sys_log` 테이블에 적재되며
  `GET /logs?category=` (quality+ 권한) 또는 대시보드에서 조회한다.
  category: `inspect`/`db`/`mes`/`error`/`user`.

## 6. sim ↔ genicam 전환 (HAL §6.1)

- **sim (기본)**: `SimulatorCamera` 가 `AIVIS_DATASET_DIR`(기본 `/data/dataset`, 호스트
  `./dataset` 마운트, 읽기전용) 의 샘플 이미지를 트리거마다 순차 리플레이. 하드웨어 불필요.
  ```bash
  AIVIS_DATASET_HOST_DIR=/path/to/dataset docker compose up -d vision
  ```
  데이터셋 폴더 규격은 CLAUDE.md 부록 A.4 (`raw/OK`, `raw/LEN`, `SIDE`/`END` …).

- **genicam (P7, 실카메라)**: `.env` 에서 전환.
  ```ini
  AIVIS_CAMERA=genicam
  AIVIS_TRIGGER=dio              # 또는 mqtt (현장 트리거 방식)
  AIVIS_GENICAM_BACKEND=harvesters
  AIVIS_GENICAM_CTI=/opt/.../mvGenTLProducer.cti
  AIVIS_GENICAM_DEVICE=<serial-or-id>
  ```
  벤더 SDK/CTI 는 도입기업 카메라 사양에 맞춰 결선(스코프 경계: SDK 본체는 OUT, 어댑터는 IN).
  전환 후 `docker compose up -d vision` 로 재기동. 상위 파이프라인/검증은 불변.

## 7. GPU 프로파일

```bash
# NVIDIA 드라이버 + nvidia-container-toolkit 설치된 호스트에서:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

- vision 컨테이너만 CUDA 베이스(`Dockerfile.gpu`) + onnxruntime-gpu 로 재빌드되고 GPU 를 예약한다.
- `AIVIS_ONNX_PROVIDER=cuda` 로 추론 백엔드를 전환한다.
- 검증: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml ps` + `nvidia-smi` 로
  vision 프로세스의 GPU 점유 확인.

## 8. 백업 / 복구 (볼륨)

상태 저장소는 두 곳: PostgreSQL(검사결과/기준정보/KPI), MinIO(이미지). 스크립트 제공.

```bash
# 백업 (기본 ./backups 에 db_*.sql.gz + minio_*.tar.gz 생성)
./scripts/backup.sh [OUTPUT_DIR]

# 복구
./scripts/restore.sh --db    backups/db_YYYYMMDD-HHMMSS.sql.gz
./scripts/restore.sh --minio backups/minio_YYYYMMDD-HHMMSS.tar.gz
```

- `backup.sh`: 실행 중 postgres 에 `pg_dump`(gzip), MinIO 명명 볼륨을 tar.gz 로 보존.
- `restore.sh`: 덮어쓰기(주의). MinIO 복구는 일시적으로 minio 를 정지 후 볼륨 교체한다.
- 정기 백업은 호스트 cron 에 `scripts/backup.sh /backup/aivis` 등록 권장.

| 명명 볼륨 | 내용 |
|---|---|
| `postgres-data` | 검사결과·기준정보·KPI·로그 |
| `minio-data` | 원본/결과/리뷰 이미지 |
| `images` | api↔vision 공유 이미지 작업영역 |

## 9. 오프라인(현장) 설치 — 인터넷 제한 PC

인터넷이 되는 빌드 머신에서 이미지를 사전 빌드·저장하고, 현장 PC 로 옮겨 로드한다.

```bash
# (빌드 머신, 인터넷 O) 앱 이미지 빌드 + 베이스 이미지 pull → 단일 tar 로 저장
./scripts/offline-save.sh aivis-offline-images.tar          # CPU
./scripts/offline-save.sh aivis-offline-images.tar --gpu    # +vision GPU 이미지

# 전송물: aivis-offline-images.tar, docker-compose.yml(+gpu), .env, scripts/offline-load.sh
#         (프론트/파이썬 소스는 이미지에 포함되므로 별도 전송 불필요)

# (현장 PC, 인터넷 X) 이미지 로드 + 스택 기동
./scripts/offline-load.sh aivis-offline-images.tar          # load + up -d
./scripts/offline-load.sh aivis-offline-images.tar --gpu
./scripts/offline-load.sh aivis-offline-images.tar --no-up  # 로드만
```

수동 절차(스크립트 미사용 시):
```bash
docker compose build && docker compose pull postgres minio
docker save -o aivis-offline-images.tar $(docker compose config --images | sort -u)
# 현장:
docker load -i aivis-offline-images.tar && docker compose up -d
```

## 10. 트러블슈팅

| 증상 | 점검 | 조치 |
|---|---|---|
| api `unhealthy` / `/health` degraded | `docker compose logs api`, postgres healthy 여부 | DATABASE_URL/비밀번호 일치 확인, `alembic upgrade head` 실행 |
| api 기동되나 테이블 없음(운영 postgres) | sqlite 가 아니면 자동생성 안 함 | `docker compose exec api alembic upgrade head` |
| 로그인 안 됨 | admin 시드 여부 | `AIVIS_SEED_ON_STARTUP=true`, `.env` 의 시드 계정/비번 확인 |
| vision `unhealthy` | `/tmp/vision_ready` 미생성 | `docker compose logs vision`, sim 데이터셋 마운트(`AIVIS_DATASET_HOST_DIR`) 확인 |
| HMI 실시간 미갱신 | `/ws/live` 연결 | api 헬시 여부, 브라우저 콘솔 WS 재연결 로그 확인 |
| MES 연계 누락 | `mes_synced=false` 누적 | `MES_MODE`/`MES_REST_URL` 확인, 워치독 로그, `POST /inspection/retry-queue` |
| 저장 실패 누적 | 로컬 큐 백업 발생 | DB 복구 후 `POST /inspection/retry-queue` (quality+) 로 재적재 |
| GPU 미인식 | override 사용 여부 | nvidia-container-toolkit 설치, `docker compose ... -f docker-compose.gpu.yml` 사용 |
| 프론트 빌드 실패(개발) | 워크스페이스 설치 | 루트에서 `npm install` 후 `npm run build` (앱 단독 설치 금지) |
| 디스크 부족 | MinIO/이미지 누적 | 백업 후 오래된 이미지 정리, 볼륨 용량 모니터 |

## 11. 운영 점검 루틴(권장)

- 일: `docker compose ps`(healthy), `/health`, 디스크 여유, `sys_log` error 카운트.
- 주: 백업(`scripts/backup.sh`) + 복구 리허설(스테이징), MES 연계율(대시보드) 확인.
- 변경: `.env`/compose 변경 시 `docker compose config -q` 로 검증 후 반영.
