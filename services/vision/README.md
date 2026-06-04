# AIVIS Vision Service (`services/vision`)

검사 추론 엔진 — CLAUDE.md §4(7단계 파이프라인) ①~④ 담당.
이미지 취득(HAL) → 전처리 → 길이/표면 분석 → 종합 판정. 모든 추론은
**결정적**이며 단계별 `proc_time_ms` 를 계측해 반환한다.

공용 출력 스키마는 `aivis_types`(packages/shared-types)를 그대로 import 한다.
이 서비스는 새 스키마를 정의하지 않는다.

## 모듈 구성

| 모듈 | M# | 책임 |
|---|---|---|
| `acquisition/` | M1 | CameraAdapter/TriggerSource HAL, SimulatorCamera 리플레이, 3회 재시도+오류 이벤트, GenICam 스텁 |
| `preprocess/`  | M2 | ROI 자동분리(파이프), 길이/표면 영역 구분, 반사 보정+CLAHE, 노이즈 제거 (ROI 편차 ≤2px) |
| `length/`      | M3 | 서브픽셀 끝단 검출 → px 거리 × `px_to_mm_scale` → 공차 OK/NG. 실패 시 `edge_detected=False`, NG |
| `surface/`     | M4 | 유분기/변색/스크래치 고전 CV 폴백(+ONNX 인터페이스). 임계는 ItemMaster에서 |
| `verdict/`     | M5 | 길이+표면 통합 OK/NG, defect_codes 합집합(2종↑ MULTI), confidence, review_flag |
| `pipeline.py`  | —  | 오케스트레이션 + `to_inspection_result` 매핑(HTTP 전송은 backend 책임) |
| `tools/gen_synthetic.py` | — | 데이터셋 없이 테스트 자립용 합성 파이프 이미지 생성 |
| `models/`      | §6.3 | 학습/ONNX export 산출물 배포 위치(미배포 시 고전 CV 폴백) |

## 설치

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e packages/shared-types/python          # aivis_types 제공
pip install -r services/vision/requirements.txt
```

## 실행 (시뮬레이터, 실카메라 불필요)

모든 동작은 `AIVIS_CAMERA=sim` 으로 검증한다.

```bash
# 1) 합성 데이터셋 생성(실데이터 없을 때 자립 동작)
python -m services.vision.tools.gen_synthetic /tmp/ds/raw --per-class 3

# 2) 시뮬레이터 카메라 + 파이프라인
export AIVIS_CAMERA=sim
export AIVIS_DATASET_DIR=/tmp/ds/raw
python - <<'PY'
from services.vision.acquisition import create_camera, AcquisitionService
from services.vision.pipeline import InspectionPipeline, to_inspection_result
from aivis_types import ItemMaster

item = ItemMaster(
    item_code="HP12", item_name="Header Pipe 12",
    ref_length_mm=125.0, tol_plus_mm=2.0, tol_minus_mm=2.0,
    px_to_mm_scale=0.25,
    oil_threshold=0.30, discolor_threshold=0.25, scratch_threshold=0.20,
)
cam = create_camera(view_filter="SIDE")      # AIVIS_CAMERA=sim → SimulatorCamera
svc = AcquisitionService(camera=cam)
pipe = InspectionPipeline()

g = svc.grab_with_retry()
v = pipe.run(g.frame, item)
print(v.final_verdict, v.defect_codes, "conf", v.confidence, v.proc_time_ms, "ms")
print(to_inspection_result(v, lot="L1", item_code="HP12", cam_id="CAM1"))
PY
```

`AIVIS_CAMERA` 스위치: `sim`(기본, SimulatorCamera) / `genicam`(P7 실카메라, 벤더 SDK 결선).

## 카메라/트리거 HAL (§6.1)

- `CameraAdapter`(ABC): `configure(recipe)`, `grab() -> BGR np.ndarray`, `close()`.
- `SimulatorCamera`: `AIVIS_DATASET_DIR`/`dataset/raw` 폴더를 정렬 순서로 순차 리플레이,
  `view_filter=SIDE|END`(부록 A.4 파일명 토큰), 끝 도달 시 순환. 결정적.
- `GenICamCamera`: 인터페이스만 — P7 통합 단계에서 Basler pylon / HIKROBOT MVS 결선.
- `TriggerSource`: `TimerTrigger`/`FileWatchTrigger`(시뮬), `DigitalIOTrigger`/`MqttTrigger`(P7 스텁).
- `AcquisitionService.grab_with_retry()`: 실패 3회 재시도 후 오류 이벤트 발행(M1 DoD).

## 임계값/보정계수 정책

하드코딩 금지. 길이 공차(`tol_plus_mm`/`tol_minus_mm`), `px_to_mm_scale`,
표면 임계(`oil/discolor/scratch_threshold`)는 모두 **ItemMaster(기준정보)** 에서 읽는다.
파이프라인은 ItemMaster 를 **주입**받으며 DB 에 직접 접근하지 않는다.

## 테스트

```bash
AIVIS_CAMERA=sim pytest services/vision/tests -q
```

테스트는 `tools/gen_synthetic` 로 합성 데이터를 만들어 **자립적으로** 통과한다
(실카메라/실데이터 불필요). 결정성, proc_time 계측, 끝단검출 실패 경로,
임계값 분기, 전체 <300ms 처리속도를 커버한다.
