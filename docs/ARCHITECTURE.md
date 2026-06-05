# AIVIS 아키텍처 (ARCHITECTURE.md)

> CLAUDE.md §3(스택)·§4(7단계 파이프라인·런타임 토폴로지)·§3.1(디렉터리 소유권)의 구현 정리.
> 본 문서는 운영/통합 관점의 시스템 지도다. 모듈 기능 명세는 CLAUDE.md §5, API 는
> [`API.md`](./API.md), 데이터 모델은 [`DATA_MODEL.md`](./DATA_MODEL.md) 참조.

## 1. 런타임 토폴로지 (단일 산업용 PC)

전 스택을 단일 호스트에 Docker Compose 로 기동한다. `docker compose up -d` 한 번으로
6개 컨테이너가 뜬다.

```
                 ┌──────────────────────── 단일 산업용 PC (Docker Compose) ─────────────────────────┐
 (트리거/카메라) │                                                                                    │
  HW(스코프 외) ─┼─▶ vision ──POST /inspection──▶ api ──┬──▶ postgres  (검사결과/기준정보/KPI)        │
                 │   (검사워커)                          ├──▶ minio     (원본/결과/리뷰 이미지)        │
                 │   AIVIS_CAMERA=sim|genicam            ├──WS /ws/live──▶ hmi (작업자 UI, nginx)      │
                 │                                       └──MES table/REST──▶ (MES, 외부)              │
                 │                                          api ◀──REST──── dashboard (관리자 UI, nginx)│
                 └────────────────────────────────────────────────────────────────────────────────────┘
```

| 컨테이너 | 이미지/빌드 | 역할 | 포트(호스트) | 헬스체크 |
|---|---|---|---|---|
| postgres | `postgres:16` | 검사결과·기준정보·KPI·로그 | 5432 | `pg_isready` |
| minio | `minio/minio` | S3 호환 이미지 스토리지(raw/result/review) | 9000(API), 9001(콘솔) | `/minio/health/live` |
| api | build `services/api/Dockerfile` (root context) | FastAPI: 검사저장·조회·KPI·인증·MES·WS | 8000 | `GET /health` |
| vision | build `services/vision/Dockerfile` (root context) | 검사워커(취득→전처리→추론→판정) | (없음) | `/tmp/vision_ready` |
| hmi | build `apps/hmi/Dockerfile` (root context) | 작업자 HMI 정적 서빙(nginx) | 5173→80 | `GET /healthz` |
| dashboard | build `apps/dashboard/Dockerfile` (root context) | 관리자 대시보드 정적 서빙(nginx) | 5174→80 | `GET /healthz` |

- 모든 컨테이너 `restart: unless-stopped` (자동재시작). `depends_on` + `condition: service_healthy`
  로 기동 순서(postgres/minio → api → vision/hmi/dashboard)를 보장한다.
- 내부 네트워크 `aivis-net`(bridge). 서비스 간 통신은 컨테이너명으로 해석된다
  (예: `postgres:5432`, `minio:9000`, `api:8000`).
- 명명 볼륨: `postgres-data`, `minio-data`, `images`(api↔vision 공유 작업영역).

### GPU 프로파일
GPU 가용 시 vision 워커만 CUDA/onnxruntime-gpu 로 가속한다(`docker-compose.gpu.yml` override).
NVIDIA 드라이버 + NVIDIA Container Toolkit 필요. `AIVIS_ONNX_PROVIDER=cuda` 로 전환되고
GPU 디바이스를 예약한다. 자세한 절차는 [`OPERATIONS.md`](./OPERATIONS.md) §GPU.

## 2. 7단계 검사 파이프라인 (CLAUDE.md §4)

| 단계 | 내용 | 구현 위치 | 소유 |
|---|---|---|---|
| ① 이미지 취득 | 트리거→카메라/조명, 원본 프레임 | `services/vision/acquisition` (HAL: CameraAdapter/TriggerSource) | vision-ai |
| ② 영상 전처리 | ROI 분리, 밝기·반사 보정, 노이즈 제거 | `services/vision/preprocess` | vision-ai |
| ③ AI 분석 | 길이 측정(고전 CV 서브픽셀) + 표면 판정(유분기/변색/스크래치) | `services/vision/length`, `services/vision/surface` | vision-ai |
| ④ 종합 판정 | 길이+표면 통합 → OK/NG → 불량유형 코드 | `services/vision/verdict`, `pipeline.py` | vision-ai |
| ⑤ 데이터 저장 | 결과·이미지 적재(DB 경로 + MinIO 객체) | `services/api/db` + MinIO | backend |
| ⑥ MES 연계 | LOT/WorkOrder/Item/Cam/Time 품질 적재(table/REST) | `services/api/mes` | data-mes |
| ⑦ 사용자 서비스 | 작업자 HMI(실시간/알람/재확인), 관리자 대시보드(이력/통계/KPI) | `apps/hmi`, `apps/dashboard` | hmi/dashboard-frontend |

처리 흐름: 트리거 → vision 워커가 ①~④ 동기 처리(목표 <300ms, `pipeline.py` 가 단계별
proc_time 합산) → `InspectionResult` 로 변환 → api `POST /inspection` → DB/MinIO 저장 →
`/ws/live` 로 HMI 푸시(+연속 NG 알람) → 백그라운드 워치독이 MES 연계.

## 3. 하드웨어 추상화 계층 (HAL, §6.1)

실물 카메라 없이 전 파이프라인을 검증하기 위해 취득·트리거를 추상화한다.

- `CameraAdapter`: `SimulatorCamera`(데이터셋 폴더 리플레이) | `GenICamCamera`(실 산업 카메라).
  - 환경변수 `AIVIS_CAMERA=sim|genicam` 으로 스위치. **모든 테스트는 sim 으로 통과**한다.
- `TriggerSource`: `AIVIS_TRIGGER=timer|filewatch|dio|mqtt`.
- 실카메라/트리거 통합(P7)은 어댑터 결선만으로 끝나며 상위 파이프라인은 불변이다.

## 4. 모노레포 / 소유권 경계 (§3.1)

```
pipe_vision/
├── CLAUDE.md, README.md
├── docker-compose.yml / docker-compose.gpu.yml / .env.example / .dockerignore  [devops]
├── package.json (npm workspaces 루트) / package-lock.json                       [devops]
├── .github/workflows/ci.yml                                                     [devops]
├── scripts/  backup.sh restore.sh offline-save.sh offline-load.sh               [devops]
├── docs/  ARCHITECTURE / OPERATIONS / USER_GUIDE / API / DATA_MODEL             [devops/backend]
├── services/
│   ├── vision/   취득→전처리→길이/표면→판정 파이프라인                          [vision-ai]
│   ├── api/      FastAPI, DB(SQLAlchemy/Alembic), WS, KPI, MES 어댑터           [backend]
│   └── data-ops/ 라벨링 보조 / 재학습 데이터셋 빌드                              [data-mes]
├── apps/
│   ├── hmi/       작업자 HMI (React+Vite → nginx)                               [hmi-frontend]
│   └── dashboard/ 관리자 대시보드 (React+Vite → nginx)                          [dashboard-frontend]
├── packages/shared-types/
│   ├── python/aivis_types  (pydantic, 단일 진실원)                              [backend]
│   └── ts/  @aivis/shared-types (TS mirror)                                     [backend+frontend]
└── tests/  e2e / fat / sat                                                       [qa]
```

### 공용 스키마 단일화 (single source of truth)
- 파이썬 `aivis_types`(pydantic) 가 원본. TS `@aivis/shared-types` 가 1:1 미러.
- **프론트(npm workspaces)**: 루트 `package.json` 의 `workspaces: ["apps/*","packages/shared-types/ts"]`.
  hmi/dashboard 모두 `@aivis/shared-types` 를 워크스페이스 패키지로 참조한다(vendored 사본 없음).
- **파이썬(Docker)**: api/vision 이미지는 루트 빌드 컨텍스트에서 `packages/shared-types/python`
  을 COPY → `pip install` 한다(`aivis_types` 임포트 보장).

## 5. 빌드 컨텍스트 정책 (왜 root context 인가)

모든 빌드 컨텍스트를 **모노레포 루트**로 둔다. 이유는 공용 스키마 공유 때문이다.
- 프론트: 워크스페이스 패키지(`@aivis/shared-types`) 를 `npm ci` 로 해석하려면 루트
  `package.json`/`package-lock.json` 과 `packages/shared-types/ts` 가 컨텍스트에 있어야 한다.
- 파이썬: api/vision 이 `packages/shared-types/python` 을 COPY+설치해야 `aivis_types` 가 임포트된다.
- 컨텍스트 비대화를 막기 위해 루트 `.dockerignore` 가 `node_modules`/`.venv`/`dataset`/`images`/
  로컬 DB/시크릿을 제외한다. 각 Dockerfile 은 필요한 경로만 COPY 한다.
