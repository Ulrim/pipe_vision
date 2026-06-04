# AIVIS — AI Vision Inspection System

AI 머신비전 기반 Clad AL Header Pipe 품질검사 & 품질데이터 관리 시스템. 절단 직후
구간에서 제품 1개 단위로 길이·표면(유분기/변색/스크래치) 부적합을 실시간 전수 자동
판정하고, 검사 결과를 DB에 적재하여 MES 품질관리와 연계한다. 프로젝트 헌법(아키텍처·
스코프·기능 명세)은 [`CLAUDE.md`](./CLAUDE.md)를 참조한다.

## 빠른 시작 (Quick Start)

단일 산업용 PC에 Docker Compose로 전 스택을 한 번에 기동한다 (CLAUDE.md §4).

```bash
# 1) 환경변수 준비
cp .env.example .env          # 비밀번호/시크릿을 현장 값으로 수정

# 2) 전체 스택 기동 (postgres / minio / api / vision / hmi / dashboard)
docker compose up -d

# 3) 상태 확인 (모든 서비스 healthy 여부)
docker compose ps

# 4) 종료
docker compose down            # 데이터 유지
docker compose down -v         # 볼륨까지 삭제 (주의: 데이터 소거)
```

### 접속 주소 (기본 포트)

| 서비스 | URL | 설명 |
|---|---|---|
| API (FastAPI) | http://localhost:8000 | REST + WebSocket, `/health` 헬스체크 |
| HMI (작업자 UI) | http://localhost:5173 | 실시간 검사화면, NG 알람, 재확인 |
| Dashboard (관리자) | http://localhost:5174 | LOT 이력·통계·KPI 리포트 |
| MinIO 콘솔 | http://localhost:9001 | 이미지 객체 스토리지 콘솔 |
| MinIO API (S3) | http://localhost:9000 | S3 호환 엔드포인트 |
| PostgreSQL | localhost:5432 | 검사결과·기준정보·KPI |

### GPU 프로파일 (선택)

GPU가 있는 호스트에서는 vision 워커를 CUDA / onnxruntime-gpu로 가속한다. NVIDIA 드라이버
+ NVIDIA Container Toolkit이 필요하다.

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

## AIVIS_CAMERA=sim (하드웨어 없이 동작)

카메라·조명·트리거는 도입기업이 자체 구축하는 하드웨어다(스코프 외, CLAUDE.md §2.2).
모든 소프트웨어는 하드웨어 추상화 계층(HAL) 뒤에서 동작하며, 실물 카메라 없이도
**이미지 리플레이 시뮬레이터**로 전 파이프라인을 검증한다(§6.1).

- `AIVIS_CAMERA=sim` (기본값): `SimulatorCamera`가 `AIVIS_DATASET_DIR`(기본 `/data/dataset`,
  호스트 `./dataset` 마운트)의 샘플 이미지를 트리거마다 순차 리플레이한다.
- `AIVIS_CAMERA=genicam`: 통합 단계에서 실 산업용 카메라(GigE/USB3 Vision)에 결선. HAL
  인터페이스는 동일하게 유지된다(§6.1, P7).

```bash
# 호스트 데이터셋 폴더를 시뮬레이터에 연결 (raw/OK, raw/LEN ... 부록 A.4 규격)
AIVIS_DATASET_HOST_DIR=/path/to/dataset docker compose up -d vision
```

## 디렉터리 구조 (CLAUDE.md §3.1)

```
pipe_vision/
├── CLAUDE.md                 # 프로젝트 헌법 (아키텍처·스코프·기능 명세)
├── docker-compose.yml        # 런타임 토폴로지 (vision/api/postgres/minio/hmi/dashboard)
├── docker-compose.gpu.yml    # vision GPU(CUDA/onnxruntime-gpu) override
├── .env.example              # 환경변수 템플릿 (cp .env.example .env)
├── .github/workflows/ci.yml  # CI (python lint+test / node build / compose 검증)
├── docs/                     # ARCHITECTURE / API / DATA_MODEL / MES_INTERFACE
├── services/
│   ├── vision/               # [vision-ai] 취득→전처리→길이/표면→판정 파이프라인
│   ├── api/                  # [backend] FastAPI, DB, WS, KPI, MES 어댑터
│   └── data-ops/             # [data-mes] 라벨링 보조 / 재학습 데이터셋 빌드
├── apps/
│   ├── hmi/                  # [hmi-frontend] 작업자 HMI (nginx 정적 서빙)
│   └── dashboard/            # [dashboard-frontend] 관리자 대시보드 (nginx 정적 서빙)
├── packages/shared-types/    # [backend+frontend] TS/Python 공용 스키마
└── tests/                    # [qa] e2e / FAT / SAT 검증 하니스
```

## 오프라인(현장) 설치

현장 산업용 PC는 인터넷이 제한될 수 있다. 인터넷이 가능한 빌드 머신에서 이미지를 사전
빌드·저장하고, 현장 PC로 옮겨 로드한다.

```bash
# 빌드 머신 (인터넷 가능)
docker compose build
docker save -o aivis-images.tar \
  $(docker compose config --images)

# 현장 산업용 PC (오프라인)
docker load -i aivis-images.tar
docker compose up -d
```

## 볼륨 / 데이터 백업

| 명명 볼륨 | 내용 |
|---|---|
| `postgres-data` | 검사결과·기준정보·KPI (PostgreSQL) |
| `minio-data` | 원본/결과/리뷰 이미지 (MinIO) |
| `images` | api/vision 공유 이미지 작업 영역 |

```bash
# DB 백업
docker compose exec -T postgres pg_dump -U aivis aivis > backup_$(date +%Y%m%d).sql

# 이미지(MinIO) 볼륨 백업
docker run --rm -v pipe_vision_minio-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/minio_$(date +%Y%m%d).tar.gz -C /data .
```

---

> 본 저장소의 인프라 파일(compose, Dockerfile, nginx, CI, README, .env.example)은 devops
> 에이전트가 소유한다. 각 서비스의 애플리케이션 소스는 CLAUDE.md §3.1의 디렉터리 소유권에
> 따라 담당 에이전트가 채운다.
