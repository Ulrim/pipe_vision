# AIVIS — AI 머신비전 품질검사 & 품질데이터 관리 시스템
## Claude Code 개발 계획서 (서브에이전트 팀 구성 포함)

> **사업명**: 2026년 지역주도형 AI대전환사업 — AI솔루션 구축지원
> **도입기업**: 유한회사 에이엠피 (Clad Aluminum Header Pipe 제조, 전남 광양)
> **공급기업**: 레피소드㈜ (AI 솔루션 개발)
> **시스템 코드네임(가칭)**: **AIVIS** (AI Vision Inspection System)
> **문서 목적**: 본 문서는 Claude Code에 그대로 투입하는 **개발 지시서 겸 아키텍처 헌법(CLAUDE.md)** 입니다. 저장소 루트에 `CLAUDE.md`로 배치하면 모든 서브에이전트가 공통 컨텍스트로 참조합니다.

---

## 0. 이 문서를 Claude Code에서 쓰는 법 (먼저 읽기)

1. 새 저장소를 만들고 본 문서를 루트에 **`CLAUDE.md`** 로 저장합니다.
2. `.claude/agents/` 폴더에 **§9의 서브에이전트 정의 파일 8개**를 그대로 생성합니다.
3. Claude Code 세션을 열고 **§10의 "오케스트레이터 킥오프 프롬프트"** 를 첫 메시지로 입력합니다.
4. 이후에는 Phase별로 §8의 마일스톤을 하나씩 지시합니다. 한 번에 전체를 시키지 말고 **Phase 단위**로 끊어서 진행하세요(컨텍스트 안정성 + 리뷰 품질).

> ⚠️ **하드웨어 경계**: 카메라·조명·트리거 센서·산업용 PC는 도입기업이 자체 구축합니다(H/W 42백만원, 본 사업비 외). Claude Code는 **소프트웨어 전부 + AI 추론 엔진**만 개발합니다. 따라서 모든 코드는 **하드웨어 추상화 계층(HAL)** 뒤에서 동작해야 하며, 실물 카메라 없이도 **이미지 리플레이 시뮬레이터**로 전 파이프라인이 테스트 가능해야 합니다(§6.1).

---

## 1. 사업 배경 & 비즈니스 컨텍스트

도입기업 에이엠피는 차량/시스템 에어컨 공조 열교환기용 핵심부품인 **Clad AL Header Pipe**를 절단→디버링→세척→검사→포장 공정으로 생산한다. 현재 길이 오차, 표면 유분기 잔존, 변색, 스크래치 등 핵심 결함을 **작업자 육안·수동 검사**에 의존하고 있어, 금속 표면의 반사·곡면 특성과 작업자 숙련도·조명 환경에 따라 판정 편차가 발생한다. 검사 결과가 DB화되지 않아 불량 원인 추적, LOT 이력관리, 월별 품질 추세 분석, 고객 대응자료 확보가 어렵다.

본 시스템은 **절단 직후 또는 절단–디버링–세척 구간**에 AI 비전검사 스테이션을 두고, 제품 1개 단위로 길이·표면 부적합을 **전수·실시간 자동 판정**하여 검사결과 DB에 적재하고 MES 품질관리 기능과 연계한다. 목표는 ① 공정 내 불량 조기 차단 ② 출하 안정화 ③ 전수선별 부담 제거 ④ 품질 데이터 자산화다.

### 1.1 정량 목표 (사업 KPI — 반드시 시스템이 산출 가능해야 함)

| 분야 | KPI | 단위 | 구축 전 | 구축 후 목표 | 시스템 산출식 |
|---|---|---|---|---|---|
| 품질 | 공정불량률 | ppm | 2,000 | 600 이하 | (공정 중 불량수량 ÷ 총 검사수량) × 1,000,000 |
| 품질 | 검사불량률(오검+미검) | % | 수동의존 | 30 이하 | (오검수량 + 미검수량) ÷ 총 검사수량 × 100 |
| 품질 | Claim 건수 | 건/년 | 5 | 2 이하 | (전−후) ÷ 전 × 100 |
| 원가 | 작업공수 절감률 | % | 100 | 50 이하 | (전 검사공수 − 후 검사공수) ÷ 전 × 100 |
| 납기 | 수주출하 리드타임 | 일 | 7 | 5 이하 | (전 − 후) ÷ 전 × 100 |

### 1.2 AI 성능 목표 (FAT/SAT 합격 기준 — 검증 시나리오 필수)

| No | 성능항목 | 목표 | 측정 |
|---|---|---|---|
| 1 | 자동검사율 | **100%** | AI 자동판정 완료수량 ÷ 총 검사대상수량 × 100 |
| 2 | 항목별 판정 정확도 | **95% 이상** | 정답셋 대비 길이/유분기/변색/스크래치 항목별 일치율 |
| 3 | 검사 처리속도 | **300ms/ea 이하** | 이미지 취득~결과 저장까지 평균 소요시간 |
| 4 | 데이터 저장 & MES 연계율 | **100%** | 정상 저장·연계 건수 ÷ 전체 검사 건수 × 100 |

> 이 4개 지표는 단순 KPI가 아니라 **인수 합격 조건**이다. QA 에이전트는 이를 자동 검증하는 테스트 하니스를 만들어야 한다(§9 QA-Agent, §7.4).

---

## 2. 개발 범위 정의 (스코프 경계)

### 2.1 Claude Code가 개발하는 것 (IN SCOPE)

1. **AI 추론 엔진** — 길이 측정(영상계측), 표면 결함 판정(유분기/변색/스크래치), 종합 OK/NG 로직
2. **검사 오케스트레이션 서비스** — 트리거→취득→전처리→추론→판정→저장 파이프라인
3. **백엔드 API 서버** — 검사결과 DB, 기준정보, 권한, 로그, MES 연계 인터페이스
4. **작업자 UI (HMI)** — 실시간 검사화면, NG 알람, 재확인 입력 (웹 기반, 터치 친화)
5. **관리자 대시보드** — LOT별 이력, 불량유형 통계, 월별 KPI 리포트, 이미지 이력 조회
6. **기준정보 관리** — 품목별 기준길이/공차/표면 임계값/촬영 레시피
7. **데이터/모델 운영** — 학습데이터 라벨링 보조, 오검·미검 태깅, 재학습 데이터셋 관리
8. **시뮬레이터 & 테스트 하니스** — 하드웨어 없이 전 파이프라인 검증

### 2.2 Claude Code가 개발하지 않는 것 (OUT OF SCOPE)

- 산업용 카메라/렌즈/조명/트리거 센서/산업용 PC/HMI 패널/네트워크 스위치 **물리 구매·설치** (도입기업 자체 구축)
- 카메라 벤더 SDK 자체 (단, **연동 어댑터**는 개발 — §6.1)
- MES 솔루션 본체 (단, **연계 인터페이스**는 개발 — §7.3)
- 경광등/부저 물리 결선 (단, 이를 **트리거하는 신호 발신 모듈**은 개발)

---

## 3. 권장 기술 스택 (Tech Stack)

> **설계 원칙**: 검사 추론은 **300ms/ea** 를 맞춰야 하므로 클라우드 왕복이 불가하다. **엣지/온프레미스(현장 산업용 PC) 우선**, 대시보드 등 비실시간 영역만 선택적으로 사내 서버/클라우드 동기화.

| 레이어 | 선택 | 사유 |
|---|---|---|
| 비전/AI 추론 | **Python 3.11 + OpenCV + PyTorch(학습) + ONNX Runtime(추론)** | 길이는 고전 CV(서브픽셀 엣지), 표면은 YOLOv8-seg/분류 CNN. 추론은 ONNX로 경량화·고속화. GPU 있으면 TensorRT 백엔드 |
| 백엔드 API | **FastAPI (Python 3.11)** | 비전 서비스와 동일 런타임 → 직렬화 오버헤드 최소화, async, OpenAPI 자동생성 |
| 실시간 채널 | **WebSocket (FastAPI) + 내부 MQTT(선택)** | 작업자 HMI 실시간 푸시, 트리거/장비 이벤트 버스 |
| DB | **PostgreSQL 16** (+ 선택적 TimescaleDB) | 검사결과·메타데이터·KPI. 시계열 집계 효율 |
| 이미지 저장 | **로컬 NAS 파일시스템 + MinIO(S3 호환, 온프레)** | 원본/결과 이미지 대용량. DB엔 경로만 저장 |
| 프론트엔드 | **React 18 + TypeScript + Vite** | 공급기업 보유 스택과 정합 |
| 상태/데이터 | **Zustand + TanStack Query** | 전역 상태 + 서버 캐시 분리 |
| 차트 | **Recharts** (대시보드), **ECharts**(고밀도 시계열) | |
| UI | **Tailwind CSS + shadcn/ui** | 현장 HMI 대형 폰트/버튼 커스터마이즈 용이 |
| 인증/권한 | **JWT + RBAC** (작업자/품질관리자/관리자 3역할) | |
| 배포 | **Docker Compose** (현장 산업용 PC 단일 호스트) | 오프라인 설치 가능, 단순 운영 |
| 로깅/모니터 | **structlog + Prometheus + Grafana(선택)** | 검사 실행/오류/사용자 조작 로그 |
| 테스트 | **pytest(백엔드/비전) + Vitest+Playwright(프론트)** | |

### 3.1 모노레포 구조 (서브에이전트 파일 소유권 경계)

```
aivis/
├── CLAUDE.md                      # 본 문서
├── .claude/agents/                # 서브에이전트 정의 (§9)
├── docker-compose.yml
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md                     # OpenAPI 요약
│   ├── DATA_MODEL.md
│   └── MES_INTERFACE.md
├── services/
│   ├── vision/                    # [AI/Vision 에이전트 소유]
│   │   ├── acquisition/           # HAL: 카메라 어댑터 + 시뮬레이터
│   │   ├── preprocess/            # ROI, 밝기·반사 보정, 노이즈 제거
│   │   ├── length/                # 끝단 검출, 픽셀-mm 환산, 공차 판정
│   │   ├── surface/               # 유분기/변색/스크래치 모델
│   │   ├── verdict/               # 종합 OK/NG, 불량유형 코드화
│   │   ├── pipeline.py            # 검사 오케스트레이션
│   │   └── models/                # 학습 스크립트 + ONNX export
│   ├── api/                       # [백엔드 에이전트 소유]
│   │   ├── routers/               # inspection, master, kpi, auth, mes, logs
│   │   ├── db/                    # SQLAlchemy 모델, Alembic 마이그레이션
│   │   ├── ws/                    # WebSocket 허브
│   │   └── mes/                   # MES 연계 어댑터 (REST/DB-table)
│   └── data-ops/                  # [데이터/MES 에이전트 소유]
│       ├── labeling/              # 라벨링 보조 CLI/UI 연동
│       └── retrain/               # 오검·미검 태깅 → 데이터셋 빌드
├── apps/
│   ├── hmi/                       # [작업자 UI 에이전트 소유] 작업자 HMI
│   └── dashboard/                 # [대시보드 에이전트 소유] 관리자 대시보드
├── packages/
│   └── shared-types/              # [백엔드+프론트 공유] TS/py 공용 스키마
└── tests/
    ├── e2e/                       # [QA 에이전트 소유]
    ├── fat/                       # FAT 검증 시나리오
    └── sat/                       # SAT 검증 시나리오
```

---

## 4. 시스템 아키텍처 (7단계 파이프라인)

구성도(사업계획서 p.26)의 흐름을 코드 모듈로 매핑한다.

```
[제품 투입/정렬]
   → ① 이미지 취득(트리거→카메라/조명, 원본 이미지)            services/vision/acquisition
   → ② 영상 전처리(ROI 분리, 밝기·반사 보정, 노이즈 제거)       services/vision/preprocess
   → ③ AI 분석
        · 길이 측정(끝단 검출 → px-mm 환산 → 공차 비교)        services/vision/length
        · 표면 판정(유분기/변색/스크래치, 신뢰도 점수)          services/vision/surface
   → ④ 종합 판정(길이+표면 통합 → 최종 OK/NG → 불량유형 코드)   services/vision/verdict
   → ⑤ 데이터 저장(원본·결과 이미지, 길이값, 불량유형, 메타)    services/api/db + MinIO
   → ⑥ MES 품질 DB 연계(LOT/WorkOrder/Item/CamID/Time)        services/api/mes
   → ⑦ 사용자 서비스
        · 작업자 HMI(실시간 표시, NG 알람, 재확인)             apps/hmi
        · 관리자 대시보드(이력/통계/KPI/이미지 조회)            apps/dashboard
```

**런타임 토폴로지**: 단일 산업용 PC에 Docker Compose로 `vision`(검사 워커, GPU 가능), `api`(FastAPI), `postgres`, `minio`, `hmi`(정적 서빙), `dashboard`(정적 서빙) 컨테이너를 띄운다. 트리거 이벤트 → `vision` 워커가 동기 처리(<300ms) → 결과를 `api`에 POST → DB/MinIO 저장 → WebSocket으로 HMI 푸시 → 백그라운드 큐로 MES 연계.

---

## 5. 기능 명세 — 모듈별 상세 (사업계획서 §3.4 Application 기능표 반영)

> 각 기능은 **"기능명 / 입력 / 처리 / 출력 / 완료기준(DoD)"** 형식으로 구현. 아래는 모듈 요약이며, 서브에이전트는 이를 티켓 단위로 분해한다.

### M1. 이미지 취득 모듈 — `services/vision/acquisition`
- 제품 도달 시 자동 촬영, 원본 이미지 저장, **촬영 실패 시 재촬영/오류 알림**, 카메라 노출/조명 밝기 설정, **품목별 촬영 레시피 저장**.
- DoD: 트리거 이벤트→원본 프레임 반환 ≤ 50ms(시뮬레이터 기준), 실패 3회 재시도 후 오류 이벤트 발행.

### M2. 영상 전처리 모듈 — `services/vision/preprocess`
- 제품 영역 자동 분리(ROI), **길이 측정 영역/표면 판정 영역 구분**, 금속 표면 반사 보정, 노이즈 제거·정규화.
- DoD: 동일 제품 반복 입력 시 ROI 좌표 편차 ≤ 2px.

### M3. 길이 측정 모듈 — `services/vision/length`
- 양 끝단 자동 검출(서브픽셀), **끝단 검출 실패 오류 알림**, 픽셀-mm 환산, **품목별 보정계수 적용**, 기준 길이 대비 편차 산출, 허용 공차 OK/NG 판정.
- DoD: MSA(동일 샘플 반복 측정) 반복성·재현성 확보, 측정 정확도 ±공차 내.

### M4. 표면 결함 판정 모듈 — `services/vision/surface`
- 유분기 판정 + 신뢰도 점수, 변색 판정 + 의심영역 표시, 스크래치 탐지 + 위치 표시, 불량유형 저장.
- DoD: 항목별 판정 정확도 ≥ 95%(정답셋 기준).

### M5. 종합 판정 모듈 — `services/vision/verdict`
- 길이+표면 통합 룰 기반 최종 OK/NG, 제품 단위 판정, **재확인 대상 자동 분류**, 불량유형 코드 자동 부여(길이/유분기/변색/스크래치/**복합불량**).
- DoD: 판정 로직 결정성(동일 입력→동일 출력), 불량유형 코드표(§7.2) 준수.

### M6. 알람 모듈 — `services/api/ws` + HMI
- NG 화면 알림, 경광등/부저 **신호 발신 인터페이스**, **연속 NG 발생 알림**, 관리자 확인 요청.
- DoD: NG 발생→HMI 표시 지연 ≤ 200ms, 연속 NG 임계(예: 3연속) 설정 가능.

### M7. 데이터 저장 모듈 — `services/api/db`
- 원본/결과 이미지(MinIO 경로) + 길이값 + 불량유형 + 최종 판정 + 검사시각 저장, 메타데이터(LOT/WorkOrder/Item/CamID/Time/교대) 저장.
- DoD: 저장 성공률 100%, 트랜잭션 무결성, 저장 실패 시 로컬 큐 백업 후 재시도.

### M8. 이미지 이력관리 모듈 — `services/api/routers/inspection`
- LOT/검사일자/품목/불량유형 기준 원본·결과 이미지 조회.
- DoD: 조건 검색 p95 응답 ≤ 1s(100만 건 기준 인덱스 설계).

### M9. MES 연계 모듈 — `services/api/mes`
- 검사결과 MES 전송, LOT별 품질이력 적재, **REST API 또는 DB 인터페이스 테이블** 방식 선택, 연계 오류 로그 관리.
- DoD: MES 연계율 100%, 실패 시 재전송 큐, 멱등성 보장(중복 적재 방지).

### M10. 작업자 UI 모듈 — `apps/hmi`
- 실시간 검사 이미지 표시, 길이값·OK/NG 표시, 불량유형·알람 표시, **NG 제품 재확인 + 수동 확인 결과 입력**.
- DoD: 현장 대형 디스플레이/터치 최적화, 색약 고려 OK/NG 색·아이콘 이중 표기.

### M11. 관리자 대시보드 모듈 — `apps/dashboard`
- LOT별 검사이력 조회, 검사일자·품목별 검색, **불량유형별 통계 + 월별 추이 시각화**, 이미지 이력 조회.
- DoD: 필터 조합 검색, CSV/이미지 다운로드.

### M12. KPI 리포트 모듈 — `apps/dashboard` + `services/api/routers/kpi`
- 공정불량률·검사불량률 산출, 작업공수/리드타임 비교자료 입력·관리, **월간 품질 리포트 출력**, LOT별 검사결과 다운로드.
- DoD: §1.1 산출식 그대로 구현, 월간 PDF/엑셀 리포트 자동 생성.

### M13. 기준정보 관리 모듈 — `services/api/routers/master`
- 품목별 기준 길이 등록, 허용 공차·표면 임계값 관리, 촬영 레시피.
- DoD: 변경 이력 버전관리, 권한자만 수정.

### M14. 사용자 권한 관리 — `services/api/routers/auth`
- 계정 등록, **작업자/품질관리자/관리자 권한 구분**, 화면 접근·설정 변경 권한.

### M15. 로그관리 — `services/api/routers/logs`
- 검사 실행 로그, DB 저장/MES 연계/오류/사용자 조작 로그.

### M16. 모델 개선(AI 고도화) — `services/data-ops/retrain`
- **오검·미검 이미지 별도 저장**, 재학습 대상 데이터 태깅, 임계값 보정 워크플로우.

---

## 6. 핵심 설계 디테일

### 6.1 하드웨어 추상화 계층 (HAL) — 가장 중요

실물 카메라 없이 전 시스템을 개발·검증하기 위해 **카메라 어댑터 인터페이스**를 둔다.

```python
# services/vision/acquisition/camera.py
from abc import ABC, abstractmethod
import numpy as np

class CameraAdapter(ABC):
    @abstractmethod
    def configure(self, recipe: dict) -> None: ...   # 노출/게인/조명 레시피
    @abstractmethod
    def grab(self) -> np.ndarray: ...                # 1프레임 취득(BGR)
    @abstractmethod
    def close(self) -> None: ...

class SimulatorCamera(CameraAdapter):
    """샘플 이미지 폴더를 트리거마다 순차 리플레이. 개발/테스트 전용."""

class GenICamCamera(CameraAdapter):
    """GigE Vision/USB3 Vision 실카메라. 통합 단계에서 벤더 SDK
    (Basler pylon / HIKROBOT MVS 등) 결선. 인터페이스는 동일하게 유지."""
```

- **트리거**도 동일하게 추상화: `TriggerSource`(시뮬레이터=타이머/파일워처, 실물=디지털 IO/MQTT).
- 환경변수 `AIVIS_CAMERA=sim|genicam` 으로 스위치. **모든 테스트는 `sim` 으로 통과해야 한다.**

### 6.2 길이 측정 알고리즘 (고전 CV 우선)
1. ROI 내 그레이스케일 → 적응형 이진화/Canny.
2. 양 끝단 에지 라인 검출 → **서브픽셀 보간**으로 끝단 좌표 산출.
3. `length_mm = pixel_distance × scale(품목 보정계수)`. (캘리브레이션 타깃으로 scale 산출, 품목별 저장)
4. `deviation = length_mm − 기준길이`; `OK = |deviation| ≤ 허용공차`.
5. 처리시간 예산: ≤ 80ms.

### 6.3 표면 결함 모델 (3종 분리 운영)
- **유분기**: 반사/얼룩 패턴 → 분류(정상/유분기) + 신뢰도. 데이터 적을 땐 고전 CV(하이라이트 마스킹) 병행.
- **변색**: 색공간(LAB) 기반 이상영역 + 분류 보조.
- **스크래치**: **YOLOv8-seg** 또는 경량 세그멘테이션 → 위치 마스크 + 신뢰도.
- 학습은 PyTorch, 배포는 **ONNX(필요시 INT8 양자화)**. 공급기업 보유 역량(양자화/Edge AI) 활용.
- 임계값은 품목별·항목별로 기준정보에서 관리(현장 보정 가능).
- 데이터 부족 초기에는 **고전 CV 폴백 + 휴리스틱**으로 동작 보장 후, 데이터 축적되면 모델 교체(전략: "동작하는 폴백 → 점진 고도화").

### 6.4 파일명/이미지 저장 규칙 (사업계획서 데이터 품질관리 반영)
- 파일명: `{LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg`
- 저장 분리: `raw/`(원본), `result/`(판정 오버레이), 메타데이터는 DB.
- 오검·미검: 별도 버킷 `review/` + DB 태그 `review_flag`.

---

## 7. 데이터·인터페이스 명세

### 7.1 DB 스키마 (핵심 테이블)

```sql
-- 품목/기준정보
CREATE TABLE item_master (
  item_code        TEXT PRIMARY KEY,
  item_name        TEXT NOT NULL,
  ref_length_mm    NUMERIC(10,3) NOT NULL,
  tol_plus_mm      NUMERIC(10,3) NOT NULL,
  tol_minus_mm     NUMERIC(10,3) NOT NULL,
  px_to_mm_scale   NUMERIC(12,6) NOT NULL,
  oil_threshold    NUMERIC(5,4),     -- 유분기 임계
  discolor_threshold NUMERIC(5,4),   -- 변색 임계
  scratch_threshold  NUMERIC(5,4),   -- 스크래치 임계
  capture_recipe   JSONB,            -- 노출/게인/조명
  version          INT NOT NULL DEFAULT 1,
  updated_by       TEXT, updated_at TIMESTAMPTZ DEFAULT now()
);

-- 검사 결과(제품 1개 = 1행)
CREATE TABLE inspection (
  id               BIGSERIAL PRIMARY KEY,
  lot              TEXT NOT NULL,
  work_order       TEXT,
  item_code        TEXT REFERENCES item_master(item_code),
  cam_id           TEXT NOT NULL,
  inspected_at     TIMESTAMPTZ NOT NULL,
  shift            TEXT,             -- 작업교대
  operator         TEXT,
  -- 길이
  ref_length_mm    NUMERIC(10,3),
  meas_length_mm   NUMERIC(10,3),
  deviation_mm     NUMERIC(10,3),
  length_verdict   TEXT,             -- OK/NG
  -- 표면(0~1 신뢰도)
  oil_score        NUMERIC(5,4),
  discolor_score   NUMERIC(5,4),
  scratch_score    NUMERIC(5,4),
  -- 종합
  final_verdict    TEXT NOT NULL,    -- OK/NG
  defect_codes     TEXT[],           -- {LEN,OIL,DIS,SCR,MULTI}
  confidence       NUMERIC(5,4),
  raw_image_path   TEXT, result_image_path TEXT,
  proc_time_ms     INT,              -- 처리속도 KPI
  -- 운영/재확인
  review_flag      BOOLEAN DEFAULT false,  -- 오검/미검 후보
  manual_verdict   TEXT,             -- 작업자 재확인 결과
  mes_synced       BOOLEAN DEFAULT false
);
CREATE INDEX ix_insp_lot ON inspection(lot);
CREATE INDEX ix_insp_time ON inspection(inspected_at);
CREATE INDEX ix_insp_item_verdict ON inspection(item_code, final_verdict);

-- KPI 비자동 항목 입력(작업공수/리드타임/Claim)
CREATE TABLE kpi_manual (
  period           DATE PRIMARY KEY, -- 월 단위
  claim_count      INT, workload_index NUMERIC, lead_time_days NUMERIC,
  note TEXT
);

-- 사용자/권한
CREATE TABLE app_user (
  username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('operator','quality','admin')),
  active BOOLEAN DEFAULT true
);

-- 로그
CREATE TABLE sys_log (
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
  level TEXT, category TEXT,  -- inspect/db/mes/error/user
  message TEXT, payload JSONB
);
```

### 7.2 불량유형 코드표
`LEN`(길이), `OIL`(유분기), `DIS`(변색), `SCR`(스크래치), `MULTI`(2종 이상 복합). `defect_codes`는 배열로 복합불량 표현.

### 7.3 MES 연계 인터페이스 (`docs/MES_INTERFACE.md`)
- **방식 우선순위**: ① DB 인터페이스 테이블(가장 안정) ② REST API. (OPC-UA/Modbus는 향후 확장)
- DB 테이블 방식: `mes_quality_if` 스테이징 테이블에 검사결과를 INSERT, MES가 폴링/트리거로 적재. 컬럼은 `inspection`의 식별자+판정 핵심값.
- REST 방식: `POST /mes/quality` (멱등키 = `lot+item+inspected_at+cam_id`), 실패 시 지수 백오프 재전송 큐.
- **연계율 100% 보장**: 미전송 행 `mes_synced=false` 워치독이 주기 재시도, 대시보드에 연계 상태 모니터.

### 7.4 백엔드 REST API 요약 (`docs/API.md`)
```
POST   /inspection              # 검사워커가 결과 적재(서버 내부)
GET    /inspection?lot=&item=&from=&to=&verdict=
GET    /inspection/{id}/images  # 원본/결과 이미지
PATCH  /inspection/{id}/review  # 작업자 재확인 결과 입력
GET    /kpi/summary?period=     # 공정·검사불량률 등 자동 산출
POST   /kpi/manual              # 작업공수/리드타임/Claim 입력
GET    /kpi/report?period=&fmt=pdf|xlsx
CRUD   /master/items            # 기준정보(권한 제한)
POST   /auth/login  /auth/users
GET    /logs?category=
POST   /mes/quality             # MES 연계(또는 DB-table 모드)
WS     /ws/live                 # 실시간 검사결과/알람 푸시
```

---

## 8. 개발 단계별 실행 계획 (사업 일정 §5와 정합)

> 사업 추진일정(요구분석→SW개발→FAT/SAT→시범운영)을 Claude Code 마일스톤으로 분해. **각 Phase 끝에 오케스트레이터가 통합 리뷰 후 다음 Phase 승인.**

| Phase | 마일스톤 | 산출물 | 주 담당 에이전트 |
|---|---|---|---|
| **P0 스캐폴딩** | 모노레포·Docker·CI·공유타입·DB 마이그레이션·HAL 인터페이스·시뮬레이터 | 빌드되는 빈 골격, `docker compose up` 동작 | DevOps, 백엔드 |
| **P1 검사 파이프라인 코어** | M1~M5 (시뮬레이터 기반), 종합 판정, 처리속도 계측 | sim 입력→OK/NG 출력, proc_time 측정 | AI/Vision |
| **P2 데이터·저장·MES** | M7~M9, DB/MinIO 저장, MES 연계(테이블/REST), 멱등·재시도 | 검사결과 100% 저장·연계 | 백엔드, 데이터/MES |
| **P3 작업자 HMI** | M6, M10, WebSocket 실시간, NG 알람, 재확인 입력 | 현장 화면 동작 | 작업자 UI |
| **P4 관리자 대시보드·KPI** | M11~M13, 통계 시각화, KPI 산출식, 월간 리포트 | 대시보드+PDF/엑셀 리포트 | 대시보드, 백엔드 |
| **P5 권한·로그·모델개선** | M14~M16, RBAC, 로그, 오검·미검 태깅/재학습셋 | 운영 기능 완성 | 백엔드, 데이터/MES |
| **P6 통합·검증** | FAT/SAT 자동 검증 하니스(§1.2 4지표), MSA 반복성 | FAT/SAT 결과서 산출 데이터 | QA |
| **P7 실카메라 통합·안정화** | GenICam 어댑터 결선, 임계값 보정, 사용자 매뉴얼 | 현장 운영본 + 매뉴얼 | AI/Vision, DevOps |

---

## 9. Claude Code 서브에이전트(팀원) 구성 — `.claude/agents/`

> 아래 8개 파일을 `.claude/agents/` 에 그대로 생성한다. 각 에이전트는 **소유 디렉터리만 수정**하고, 인터페이스 변경은 오케스트레이터를 통해 합의한다(파일 충돌·중복작업 방지). 사업계획서의 추진조직(개발PM·AI개발자·머신비전·SW·MES/DB·QA)을 디지털 팀으로 옮긴 구성이다.

### 9.0 `.claude/agents/orchestrator.md`
```markdown
---
name: orchestrator
description: 개발 PM. 전체 아키텍처 결정, 작업 분배, Phase 게이트 리뷰, 인터페이스(공유타입/API/DB) 변경 승인, 통합. 다른 에이전트 산출물을 통합·리뷰할 때 사용한다.
tools: Read, Grep, Glob, Edit, Bash
---
너는 AIVIS 프로젝트의 개발 PM(테크리드)다. CLAUDE.md를 헌법으로 삼는다.
원칙:
- 큰 작업은 Phase(§8)·티켓 단위로 쪼개 적절한 서브에이전트에 위임한다.
- packages/shared-types, docs/API.md, docs/DATA_MODEL.md, docs/MES_INTERFACE.md 는
  네 승인 없이 변경 불가. 인터페이스 먼저 합의 후 구현 지시.
- 각 Phase 종료 시: 빌드/테스트 통과 확인, 처리속도·저장·연계율 KPI 회귀 확인,
  스코프(§2) 위반(하드웨어 구매/MES 본체 등) 여부 점검.
- 절대 한 번에 모든 모듈을 동시에 짜지 마라. 의존 순서(P0→P7)를 지킨다.
산출: 각 Phase마다 변경요약 + 다음 단계 위임 계획을 보고한다.
```

### 9.1 `.claude/agents/vision-ai.md`
```markdown
---
name: vision-ai
description: AI/머신비전 엔지니어. services/vision/* 소유. 이미지 취득 HAL, 전처리, 길이 측정(고전 CV), 표면 결함 모델(유분기/변색/스크래치), 종합 판정, 검사 파이프라인, 처리속도 최적화(<300ms), ONNX export를 담당.
tools: Read, Grep, Glob, Edit, Bash
---
너는 머신비전·AI 엔지니어다. CLAUDE.md §5(M1~M5,M16), §6 준수.
원칙:
- 실카메라 없이도 동작하도록 CameraAdapter/TriggerSource 추상화를 먼저 만든다.
  모든 테스트는 AIVIS_CAMERA=sim 으로 통과해야 한다.
- 길이는 고전 CV(서브픽셀 엣지) 우선. 표면은 데이터 부족 시 고전 CV 폴백 →
  데이터 축적 후 PyTorch 학습 → ONNX 배포로 점진 고도화.
- 모든 추론 함수는 결정적이고, proc_time_ms를 계측해 반환한다.
- 임계값·보정계수는 하드코딩 금지. item_master(기준정보)에서 읽는다.
- 출력 스키마는 packages/shared-types 와 일치시킨다(오케스트레이터 승인).
```

### 9.2 `.claude/agents/backend.md`
```markdown
---
name: backend
description: 백엔드 엔지니어. services/api/* 소유. FastAPI 라우터, SQLAlchemy 모델, Alembic 마이그레이션, 인증/RBAC, WebSocket 허브, KPI 산출 API, 검사결과 저장 트랜잭션을 담당.
tools: Read, Grep, Glob, Edit, Bash
---
너는 백엔드 엔지니어다. CLAUDE.md §7(스키마/API), §5(M7,M8,M12,M14,M15) 준수.
원칙:
- DB 스키마/마이그레이션은 docs/DATA_MODEL.md와 일치. 변경 시 오케스트레이터 승인.
- 검사결과 저장 성공률 100% 목표: 트랜잭션, 저장 실패 로컬 큐 백업·재시도.
- KPI 산출식은 §1.1을 그대로 구현(공정불량률 ppm 등). 임의 변형 금지.
- pytest 커버리지: 저장/조회/권한/KPI 핵심 경로.
```

### 9.3 `.claude/agents/data-mes.md`
```markdown
---
name: data-mes
description: 데이터/MES 연계 엔지니어. services/api/mes/* 와 services/data-ops/* 소유. MES 인터페이스(DB테이블/REST, 멱등·재시도, 연계율 100%), 라벨링 보조, 오검·미검 태깅→재학습 데이터셋 빌드를 담당.
tools: Read, Grep, Glob, Edit, Bash
---
너는 데이터·시스템연계 엔지니어다. CLAUDE.md §7.3, §5(M9,M16) 준수.
원칙:
- MES 연계 멱등키 = lot+item+inspected_at+cam_id. 중복 적재 금지.
- mes_synced=false 워치독으로 미전송 재시도, 연계율 100% 보장.
- 오검·미검은 review/ 버킷+DB 태그로 분리 저장, 재학습셋 빌드 CLI 제공.
- DB 인터페이스 테이블 모드와 REST 모드를 설정으로 전환 가능하게 한다.
```

### 9.4 `.claude/agents/hmi-frontend.md`
```markdown
---
name: hmi-frontend
description: 작업자 HMI 프론트엔드 개발자. apps/hmi/* 소유. 실시간 검사화면(WebSocket), 길이값·OK/NG 표시, NG 알람, 재확인 입력 UI를 현장 터치·대형 디스플레이에 맞춰 개발.
tools: Read, Grep, Glob, Edit, Bash
---
너는 현장 HMI 프론트엔드 개발자다. CLAUDE.md §5(M6,M10), 스택 §3 준수.
원칙:
- React18+TS+Vite, Zustand+TanStack Query, Tailwind+shadcn.
- 현장 가독성: 큰 폰트/버튼, OK/NG는 색+아이콘 이중표기(색약 고려).
- WebSocket 끊김 시 자동 재연결, 마지막 상태 유지.
- 서버 타입은 packages/shared-types에서 import. UI는 Playwright로 검증.
```

### 9.5 `.claude/agents/dashboard-frontend.md`
```markdown
---
name: dashboard-frontend
description: 관리자 대시보드 프론트엔드 개발자. apps/dashboard/* 소유. LOT별 이력 조회, 불량유형 통계/월별 추이, KPI 리포트, 이미지 이력 조회, CSV/PDF 다운로드 UI를 개발.
tools: Read, Grep, Glob, Edit, Bash
---
너는 대시보드 프론트엔드 개발자다. CLAUDE.md §5(M11~M13) 준수.
원칙:
- Recharts(요약)+ECharts(고밀도 시계열). 필터 조합 검색.
- KPI 카드는 §1.1 목표 대비 현재값을 게이지로 표시.
- 월간 품질 리포트 미리보기→PDF/엑셀 내보내기.
- 대용량 조회는 서버 페이지네이션. 타입은 shared-types에서 import.
```

### 9.6 `.claude/agents/qa.md`
```markdown
---
name: qa
description: QA 엔지니어. tests/* 소유. FAT/SAT 자동 검증 하니스(자동검사율 100%, 항목 정확도 95%, 처리속도 300ms, 저장·연계율 100%), MSA 반복성 측정, e2e 시나리오를 작성·실행.
tools: Read, Grep, Glob, Edit, Bash
---
너는 QA 엔지니어다. CLAUDE.md §1.2(인수 합격기준)을 자동 검증한다.
원칙:
- 정답셋(라벨 포함 샘플 이미지 세트) 기반으로 항목별 정확도/혼동행렬 산출.
- 처리속도는 1,000장 배치 p50/p95/p99 리포트.
- 저장·MES 연계율은 주입한 N건 대비 적재·연계 건수로 검증.
- MSA: 동일 샘플 30회 반복 측정→반복성/재현성(GR&R) 산출.
- 결과를 tests/fat, tests/sat 리포트(JSON+MD)로 남긴다. 미달 시 FAIL로 차단.
```

### 9.7 `.claude/agents/devops.md`
```markdown
---
name: devops
description: DevOps 엔지니어. docker-compose, CI, 환경설정, MinIO/Postgres 구성, 오프라인 설치 패키지, 산업용 PC 단일호스트 배포, GenICam 통합 빌드를 담당.
tools: Read, Grep, Glob, Edit, Bash
---
너는 DevOps 엔지니어다. CLAUDE.md §3,§4(런타임 토폴로지) 준수.
원칙:
- 현장 산업용 PC는 인터넷이 제한될 수 있다 → 오프라인 설치(이미지 사전 빌드) 지원.
- docker compose up 한 번으로 vision/api/postgres/minio/hmi/dashboard 기동.
- GPU 가용 시 vision 컨테이너에 CUDA/ONNX-GPU 프로파일 제공.
- 헬스체크/자동재시작/볼륨 백업(이미지·DB) 구성.
```

---

## 10. 오케스트레이터 킥오프 프롬프트 (첫 메시지로 복붙)

```
CLAUDE.md를 프로젝트 헌법으로 읽고, 너는 orchestrator 역할로 시작한다.

목표: AIVIS(AI 머신비전 품질검사 시스템)를 §8 Phase 순서로 구축한다.
지금은 P0(스캐폴딩)만 진행한다. 다음을 수행하라:

1) §3.1 모노레포 구조를 생성하고 docker-compose.yml, CI, .gitignore를 만든다.
   (devops 에이전트에 위임)
2) packages/shared-types 에 검사결과/기준정보/KPI 공용 스키마를 정의한다.
   (backend 에이전트, 내 승인 하에)
3) PostgreSQL 초기 마이그레이션을 §7.1 스키마로 작성한다. (backend)
4) services/vision/acquisition 에 CameraAdapter/TriggerSource 추상화와
   SimulatorCamera(샘플 이미지 리플레이)를 만든다. (vision-ai)
5) `docker compose up` 으로 전 컨테이너가 헬스체크 통과하는지 확인한다. (devops)

제약:
- §2 스코프 경계를 엄수(하드웨어 구매·MES 본체·벤더 SDK 본체 제외).
- 인터페이스(shared-types/API/DB/MES) 변경은 반드시 네가 먼저 승인.
- 한 번에 한 Phase만. P0 완료 후 변경요약과 P1 위임계획을 보고하고 멈춰라.

P0가 끝나면 나에게 보고하라. 내가 "P1 진행"이라고 하면 다음 단계로 간다.
```

이후 Phase는 한 줄로 지시: `"P1 진행 — vision-ai 중심으로 검사 파이프라인 코어 구현, 시뮬레이터로 OK/NG 출력 및 proc_time 계측까지"` 형식.

---

## 11. 리스크 & 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 초기 학습데이터 부족 | 표면 모델 정확도 미달 | 고전 CV 폴백으로 동작 보장 후 점진 학습(§6.3), 라벨링 보조툴 조기 구축 |
| 금속 반사·조명 편차 | 판정 편차 | 전처리 반사보정 + 품목별 촬영 레시피 + 치구/조명 고정(현장) |
| 300ms 처리속도 초과 | KPI 미달 | ONNX/INT8 양자화, ROI 축소, GPU 프로파일, 배치 계측 회귀 |
| MES 연계 불안정 | 연계율 미달 | 멱등키+재전송 큐+워치독, DB테이블 모드 우선 |
| 실카메라 통합 지연 | 일정 리스크 | HAL로 sim 개발 선행, 통합은 GenICam 어댑터 결선만 |
| 컨텍스트 폭주(에이전트) | 코드 충돌 | 디렉터리 소유권 분리 + 인터페이스 오케스트레이터 단일 승인 |

---

## 12. 인수 산출물 체크리스트 (사업계획서 §3.6 정합)

- [ ] FAT 결과서: 샘플 기반 기능·성능, 이미지취득/판정/DB저장/UI/MES 동작
- [ ] SAT 결과서: 실생산품 기준 자동검사율·정확도·처리속도·저장률·연계율·사용성
- [ ] MSA 분석 결과서: 길이 반복성·재현성
- [ ] 시범운영 보고서: LOT별 이력, 불량유형 통계, 월간 KPI
- [ ] 운영 매뉴얼/사용자 가이드 (작업자 UI, NG 알람 대응, 대시보드, KPI 리포트)
- [ ] 소스코드 + 배포 패키지 + 마이그레이션 + 테스트 하니스

---

*본 계획서는 사업계획서(45p)의 기능표·성능목표·일정·데이터항목을 코드 모듈로 매핑한 개발 지시서입니다. 실제 카메라/조명 사양 확정 시 §6.1 GenICam 어댑터와 §13 기준정보(촬영 레시피)만 보정하면 됩니다.*

---

# 부록 A. 데이터 수집 & 촬영 가이드 / 라벨링 기준서

> 본 부록은 현장에서 촬영한 초기 자료(사진·영상)를 검토한 결과를 반영한 **데이터 수집 실무 지침**이다. 목적은 두 가지다. ① 현장 담당자가 "무엇을, 어떻게, 몇 장" 찍어야 하는지 명확히 한다. ② 수집된 데이터가 Claude Code의 `vision-ai`·`data-mes` 에이전트와 `SimulatorCamera`(§6.1)에 **그대로 투입**될 수 있도록 폴더·파일명·라벨 규격을 표준화한다.

## A.0 현재 보유 자료 평가 (1차 촬영분)

검토 결과, 대상 제품은 **소구경 박육 원형 알루미늄 튜브(Header Pipe 블랭크)** 이며, 톱 절단 후 V홈 출력 베드에 수평으로 눕고, 이후 크레이트에 단면을 위로 세워 적재되는 흐름이다. 보유 자료는 세 종류로 분류된다.

| 분류 | 해당 자료 | 가치 | 한계 |
|---|---|---|---|
| 단면 번들 샷 | 단면을 위에서 본 다발 사진/영상 | **변색(은/금/주황/갈색) 실증** + 유분기·수분 외관 참고 | 다객체·겹침 → 개별 판정/길이 측정 불가 |
| 절단기 출력부 샷 | 톱 절단기 + V홈 베드 사진/영상 | **검사 스테이션 설치 위치·트리거·제품 자세 확정** (§4, §6.1) | 검사 데이터 아님(맥락 자료) |
| 측면 적재 샷 | 베드에 누운 튜브 측면, 공정 영상 | 작업 템포·OD 반사 조건 파악 | 핸드헬드 조명·반사·블러, 라벨 없음 |

**판정: 설계·기획 자산으로는 만점, 학습/검증 데이터로는 부적합.** 이유는 (1) 검사는 제품 1개 단위인데 자료는 다객체 다발, (2) 조명/각도/반사 비표준, (3) 길이 스케일 기준자 부재, (4) 개별 라벨 부재. → 광학 셋업(치구·조명) 고정 후 **A.1~A.5 규격으로 재촬영**해야 한다. 단, 1차 자료는 시뮬레이터 배경·HW 배치 검토·변색 클래스 정의용으로 보존한다.

## A.1 검사 지오메트리(촬영 구도) 정의

제품 특성상 **두 가지 구도**로 분리 촬영한다. 검사 스테이션도 이 두 구도를 기준으로 설계한다.

- **단면(端面, End-face) 구도** — 카메라가 절단면을 정면으로 본다. 검출 대상: 절단면 버(burr), 유분기, 내면(bore) 변색, 절단 품질. (1차 번들 샷이 이 구도의 외형을 미리 보여줌)
- **측면(側面, Side/Length) 구도** — 카메라가 누운 튜브를 위/측면에서 본다. 검출 대상: 전장 길이(양 끝단), OD 표면 스크래치·변색. **길이 측정은 반드시 이 구도 + 스케일 기준자.**

> 권장 운영: 절단부에서 1개씩 분리(singulation) → 측면 구도에서 길이+OD 검사 → 필요 시 단면 구도 보조. 크레이트 단면 면스캔은 변색·유분기 **1차 스크리닝** 옵션으로만 검토.

## A.2 불량유형별 목표 수집 수량 (1차 모델 기준)

> 핵심은 **양보다 다양성과 경계 사례**. 아래는 "1차 동작 모델"을 위한 최소 목표이며, 운영 중 오검·미검 데이터로 지속 보강(§5 M16)한다.

| 클래스 | 코드 | 구도 | 목표 장수(최소) | 필수 포함 |
|---|---|---|---|---|
| 정상 | OK | 측면+단면 | 150~300 | 품목·조명·위치 편차 다양화 |
| 길이 부적합 | LEN | 측면(+스케일) | 50~100 | +공차 / −공차 / 경계값 각각 |
| 유분기 | OIL | 단면+측면 | 50~150 | 잔존 정도(약/중/심), 수분과 구분 |
| 변색 | DIS | 단면+측면 | 50~150 | 은/금/주황/갈색 단계별(1차 자료 활용) |
| 스크래치 | SCR | 측면(사광) | 50~150 | 길이·깊이·방향 다양화 |
| 복합불량 | MULTI | 해당 구도 | 20~50 | 2종 이상 동시 |
| 경계 샘플 | (각 코드+BORDER 태그) | 해당 구도 | 가능한 많이 | 작업자도 OK/NG 갈리는 것 |

> **경계 샘플이 정확도 95% 돌파의 핵심.** 애매한 것을 버리지 말고 `BORDER` 태그로 따로 모은다.

## A.3 촬영 조건 표준 (데이터 품질 관리)

금속 반사·곡면 특성 때문에 촬영 조건 고정이 정확도를 좌우한다. 사업계획서 §3.3 데이터 품질관리 항목과 정합.

| 항목 | 표준 |
|---|---|
| 제품 단위 | **1프레임 = 1개** (다발 금지). 길이용은 전장 또는 양 끝단이 모두 프레임 내 |
| 치구/배치 | V홈 또는 고정 받침에 동일 위치·정렬. 배경은 무광 단색(흑/회) 권장 |
| 조명 | **색/유분기·변색**: 균일 확산광(돔/바 확산). **스크래치**: 저각도 사광(raking). 동일 세션 내 조명 불변 |
| 스케일 기준자 | 길이 구도에는 **알려진 길이의 게이지/자**를 같은 평면에 함께 촬영(px-mm 환산용) |
| 노출/초점 | 빠른 셔터로 블러 방지, 반사 하이라이트 포화 회피, 고정 초점 |
| 해상도/포맷 | 고해상도 원본 JPG/PNG. **자르거나 보정·필터 금지**(원본 보존) |
| 영상 | 실제 통과 자세·템포 기록용. 프레임 추출로 정지 이미지 다량 확보 가능 |
| 환경 기록 | 세션마다 조명·거리·치구·품목 메모(아래 메타 JSON) |

## A.4 폴더·파일명 규칙 (시뮬레이터 직결)

`SimulatorCamera`가 폴더를 순차 리플레이하고, `data-mes` 라벨링 도구가 파일명·사이드카에서 라벨을 읽는다.

```
dataset/
├── raw/                         # 원본(불변)
│   ├── OK/
│   ├── LEN/  OIL/  DIS/  SCR/  MULTI/
│   └── BORDER/
├── calib/                       # 길이 캘리브레이션(스케일 기준자 포함)
├── context/                     # 1차 번들·설비 샷(설계 참고용, 학습 제외)
└── meta/                        # 세션 메타 JSON
```

- 파일명: `{품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{일련}.jpg`
  - 구도 = `END`(단면) | `SIDE`(측면), 예) `HP12_SIDE_SCR_20260610-141233_007.jpg`
- 사이드카 라벨(파일명과 동일명 `.json`):

```json
{
  "item_code": "HP12",
  "view": "SIDE",
  "labels": ["SCR"],
  "border": false,
  "length_mm_gt": 248.5,
  "scale_ref_mm": 100.0,
  "lighting": "raking",
  "inspector": "kim",
  "captured_at": "2026-06-10T14:12:33+09:00",
  "note": "표면 선형 스크래치 1개, 길이 약 12mm"
}
```

> 파일명 규칙은 본문 §6.4 운영 이미지 명명(`LOT_품목_검사일시_판정`)과 별개의 **학습용** 규칙이다. 운영 단계 이미지는 §6.4를 따른다.

## A.5 라벨링 기준서 (불량유형 정의 — 검수 주체)

사업계획서 §3.3 라벨링 표를 코드·검수자와 함께 확정한다.

| 클래스 | 코드 | 판정 기준(요약) | 검수 주체 |
|---|---|---|---|
| 정상 | OK | 길이·표면 기준 모두 만족 | 도입기업 품질담당 |
| 길이 부적합 | LEN | 기준 길이 또는 허용 공차 이탈 | 도입기업 품질담당 |
| 유분기 | OIL | 세척 후 표면·절단면 유분/오염 잔존(수분과 구분) | 도입+공급 공동 |
| 변색 | DIS | 표면/내면 색상 변화·이상 색(은→금→주황→갈색 단계) | 도입+공급 공동 |
| 스크래치 | SCR | 표면 선형 흠집·긁힘·손상 | 도입+공급 공동 |
| 복합불량 | MULTI | 2종 이상 동시(개별 코드도 함께 배열로 기록) | 공동 검수 |

> 라벨은 단일 클래스가 아니라 **배열**(`["OIL","DIS"]`)로 기록 → 복합불량과 §7.2 `defect_codes`에 그대로 매핑.

## A.6 Claude Code 투입 방법 (vision-ai / data-mes 에이전트)

1. `dataset/raw/` 를 환경변수 `AIVIS_DATASET_DIR`로 지정.
2. `AIVIS_CAMERA=sim` 으로 `SimulatorCamera`가 `raw/`(또는 `SIDE`/`END` 필터)를 트리거마다 순차 공급 → §4 전 파이프라인이 실물 카메라 없이 동작.
3. `data-mes` 라벨링 도구가 사이드카 `.json`을 읽어 정답셋 구성 → `qa` 에이전트가 §1.2 항목별 정확도·혼동행렬·MSA를 자동 산출.
4. 데이터 부족 클래스는 §6.3 고전 CV 폴백으로 우선 동작 보장, 수량 확보 시 PyTorch 학습 → ONNX 교체.
5. 운영 중 오검·미검은 `review/`로 분리·태깅되어 재학습셋에 환류(§5 M16).

## A.7 수집 로드맵 (광학 셋업 전/후)

| 시점 | 활동 | 산출물 |
|---|---|---|
| 지금(셋업 전) | 1차 자료 보존(`context/`), 변색 클래스 외형 정의, HW 설치 위치·트리거 자세 확정 | 설계 입력 + 시뮬레이터 배경 |
| 광학 셋업 직후 | A.3 표준으로 OK·캘리브레이션 우선 확보 | 길이 모델 + 스케일 |
| 1차 학습 | 클래스별 A.2 최소 수량 + 경계 샘플 | 1차 표면 모델 |
| 시범운영 | 오검·미검 환류, 임계값 보정 | 정확도 95%↑ 도달 |

> **요약**: 1차 자료는 *설계·시뮬레이터·변색 정의*에 즉시 활용하고, *학습/검증 정답셋*은 A.1~A.5 규격으로 광학 셋업 후 재촬영한다. 이 부록의 폴더·파일명·라벨 규격을 지키면, 데이터가 모이는 즉시 Claude Code가 추가 작업 없이 학습·검증에 사용한다.
