# AIVIS QA 검증 하니스 (tests/)

CLAUDE.md **§1.2 인수 합격기준 4지표**를 자동 검증하는 FAT/SAT/MSA/e2e 하니스.
라이브 카메라/Postgres 없이 **합성 정답셋(gen_synthetic + 사이드카) + 폴백 CV +
sqlite** 로 자립 실행된다. 지표 미달 시 **pytest FAIL 로 차단**한다.

> 소유 경계: 본 디렉터리(tests/*)만 QA 가 소유한다. services/*, apps/*,
> packages/* 는 읽기 전용으로 import 만 한다(변경 금지).

## 검증 지표 (§1.2)

| No | 지표 | 합격 기준 | 차단 조건 |
|---|---|---|---|
| 1 | 자동검사율 | 100% | 미판정/예외 1건이라도 발생 시 FAIL |
| 2 | 항목별 판정 정확도 | ≥95% (LEN/OIL/DIS/SCR) | 최저 항목 정확도 <95% 시 FAIL + 혼동행렬 산출 |
| 3 | 검사 처리속도 | ≤300ms | 1,000장 배치 p95 >300ms 시 FAIL (p50/p95/p99/max 리포트) |
| 4 | 데이터 저장 & MES 연계율 | 100% | 저장율 또는 연계율 <100% 시 FAIL |
| MSA | 길이 반복성/재현성(GR&R) | %GR&R ≤30% | 동일 샘플 30회 반복 측정 (§5 M3) |

## 디렉터리

```
tests/
├── conftest.py            # 경로(services/vision/api/data-ops) + sqlite/MES env 부트스트랩
├── pytest.ini             # rootdir 고정 + 경고 필터
├── requirements.txt       # 실행 의존성 안내
├── harness/               # 공통 라이브러리(QA 소유)
│   ├── dataset.py         # 정답셋(라벨 사이드카) 생성 — 부록 A.4/A.5
│   ├── metrics.py         # 4지표 산출 + 혼동행렬 + 백분위 + ItemMaster 팩토리
│   ├── runner.py          # 파이프라인 실행(1/2/3) + backend 적재/연계 검증(4)
│   ├── msa.py             # 길이 반복성/재현성/GR&R
│   └── report.py          # JSON+MD 리포트 산출
├── fat/
│   ├── test_fat.py        # FAT 4지표 (클래스별 40장 × 7 = 280, 처리속도 1,000장)
│   ├── test_msa.py        # MSA 30회 반복
│   └── report/            # fat_metrics.{json,md}, msa_length.{json,md}
├── sat/
│   ├── test_sat.py        # SAT 4지표 + 사용성 스모크 (클래스별 80장, 혼합 LOT/교대)
│   └── report/            # sat_metrics.{json,md}
└── e2e/
    └── test_e2e_smoke.py  # 트리거→취득(sim)→파이프라인→판정→저장→조회→KPI
```

## 실행 방법

### 1) 가상환경 구성 (1회)

```bash
python3 -m venv .venv_qa
.venv_qa/bin/pip install -U pip
.venv_qa/bin/pip install -e packages/shared-types/python \
    -r services/api/requirements.txt \
    -r services/vision/requirements.txt \
    -r tests/requirements.txt
```

### 2) 전체 검증 (FAT + SAT + MSA + e2e)

```bash
.venv_qa/bin/python -m pytest tests/ -q
```

### 3) 개별 실행

```bash
.venv_qa/bin/python -m pytest tests/fat/         # FAT + MSA
.venv_qa/bin/python -m pytest tests/sat/         # SAT
.venv_qa/bin/python -m pytest tests/e2e/         # e2e 스모크
.venv_qa/bin/python -m pytest tests/fat/test_fat.py::test_metric3_latency_p95_under_300
```

리포트는 실행 시 `tests/fat/report/`, `tests/sat/report/` 에 JSON+MD 로 갱신된다.

## 실데이터 사용 (선택, 부록 A.6)

SAT 는 `AIVIS_DATASET_DIR` 가 설정되고 존재하면 합성 대신 **실데이터**를 우선
사용한다(부록 A.4 폴더/파일명 + 사이드차 .json 규격). 미설정 시 합성으로 모사.

```bash
AIVIS_DATASET_DIR=/path/to/dataset/raw .venv_qa/bin/python -m pytest tests/sat/
```

## 동작 원리 / 자립성

- **카메라**: `AIVIS_CAMERA=sim` 강제 → `SimulatorCamera` 가 합성 이미지를 리플레이.
- **정답셋**: `harness/dataset.py` 가 클래스별 결정적 합성 이미지 + 라벨 사이드카를
  생성하고, `data-ops` 의 `labeling.groundtruth.build_groundtruth` 가 그대로 읽는다.
- **DB**: 임시 sqlite 파일(conftest 가 import 전 env 세팅). `item_master` FK 충족을
  위해 하니스가 동일 ItemMaster 를 시드한 뒤 `POST /inspection`(내부토큰) 적재.
- **MES**: `MES_MODE=table`. 적재 후 `run_watchdog_once` 를 반복 실행해
  `mes_synced=true` 로 전환 → `get_linkage_status` 로 연계율 100% 확인.
- **결정성**: 파이프라인이 결정적이라 MSA 반복성(EV)이 0 에 수렴한다.

## 합격 근거 (현재 합성 데이터 기준)

- 합성 결함은 라벨이 명확(LEN=길이 변형, OIL/DIS/SCR=표면 주입)하여 폴백 CV 가
  ItemMaster 임계값(oil 0.30 / dis 0.20 / scr 0.15, 공차 ±3mm)으로 100% 분리.
- ItemMaster 기준값은 `services/vision/tests/conftest.py` 픽스처와 동일(단일 진실원).
- 실데이터 투입 시 경계 샘플(BORDER)로 정확도가 변동할 수 있으며, 그때는
  §6.3 점진 고도화(ONNX 모델 교체)와 임계 보정으로 95% 목표를 맞춘다.
