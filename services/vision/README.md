# AIVIS Vision Service (`services/vision`)

검사 추론 엔진 — CLAUDE.md §4(7단계 파이프라인) ①~④ 담당.
이미지 취득(HAL) → 전처리 → 길이/표면 분석 → 종합 판정. 모든 추론은
**결정적**이며 단계별 `proc_time_ms` 를 계측해 반환한다.

공용 출력 스키마는 `aivis_types`(packages/shared-types)를 그대로 import 한다.
이 서비스는 새 스키마를 정의하지 않는다.

## 모듈 구성

| 모듈 | M# | 책임 |
|---|---|---|
| `acquisition/` | M1 | CameraAdapter/TriggerSource HAL, SimulatorCamera 리플레이, 3회 재시도+오류 이벤트, GenICam 연동 어댑터(레시피→SFNC 매핑·BGR 변환·재연결 골격, SDK 동적 import) |
| `preprocess/`  | M2 | ROI 자동분리(파이프), 길이/표면 영역 구분, 반사 보정+CLAHE, 노이즈 제거 (ROI 편차 ≤2px) |
| `length/`      | M3 | 서브픽셀 끝단 검출 → px 거리 × `px_to_mm_scale` → 공차 OK/NG. 실패 시 `edge_detected=False`, NG |
| `surface/`     | M4 | 유분기/변색/스크래치 고전 CV 폴백(+`OnnxSurfaceModel`). 임계는 ItemMaster에서. 모델 미배포/미설치/로드실패 시 자동 폴백 |
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
- `GenICamCamera`: GigE/USB3 Vision 연동 어댑터. **생성은 SDK 없이도 성공**하고,
  `configure`/`grab` 시점에 SDK/환경 미구성이면 안내 예외(`GenICamSDKError`)를
  던진다. 레시피→SFNC 노드 매핑·픽셀포맷→BGR 변환·재연결 골격은 준비되어 있고,
  실 벤더 SDK 호출만 P7 에서 결선한다(§2.2 — SDK 본체는 범위 외, 어댑터만 개발).
- `TriggerSource`: `TimerTrigger`/`FileWatchTrigger`(시뮬), `DigitalIOTrigger`(디지털 IO)·
  `MqttTrigger`(paho 동적 import)는 생성은 성공, 대기 시점에 드라이버/SDK 필요
  (`TriggerSDKError`).
- `AcquisitionService.grab_with_retry()`: 실패 3회 재시도 후 오류 이벤트 발행(M1 DoD).

### sim ↔ genicam 전환

```bash
# 개발/테스트(기본): 실카메라 불필요
export AIVIS_CAMERA=sim

# 실카메라(현장, P7): GenTL/SDK 결선 후
export AIVIS_CAMERA=genicam
export AIVIS_GENICAM_BACKEND=harvesters        # 또는 pypylon(Basler 전용)
export AIVIS_GENICAM_CTI=/opt/.../mvGenTLProducer.cti   # harvesters 필수
export AIVIS_GENICAM_DEVICE=<serial|user-id|index>      # 선택(미지정=0번)
export AIVIS_GENICAM_TIMEOUT_MS=1000                    # grab 타임아웃
export AIVIS_TRIGGER=dio                         # 실 트리거: dio | mqtt (genicam 모드)
```

`create_camera()`/`create_trigger()` 가 위 환경변수로 어댑터를 선택한다.
파이프라인 코드는 sim/genicam 을 구별하지 않는다(HAL 경계).

### GenICam 실카메라 통합 체크리스트 (P7)

1. **GenTL producer(.cti) 설치**: 벤더 런타임(Basler pylon / HIKROBOT MVS /
   MATRIX VISION mvIMPACT 등)을 설치하면 `*.cti` 가 제공된다. 경로를
   `AIVIS_GENICAM_CTI` 로 지정(harvesters 경로). Basler 전용이면 `pypylon` 백엔드.
2. **Python SDK 설치(통합 환경)**: `pip install harvesters` 또는 `pip install pypylon`.
   (추론 런타임 기본 requirements 에는 포함하지 않는다 — 미설치여도 import/생성 OK.)
3. **디바이스 선택**: `AIVIS_GENICAM_DEVICE` 에 serial/user-id/index.
   GigE 는 네트워크(점보프레임/서브넷), USB3 는 대역폭 확인.
4. **촬영 레시피(capture_recipe, ItemMaster)** 키 → SFNC 노드 매핑은
   `map_recipe_to_genicam()` 에 정의되어 있다(exposure_us→ExposureTime,
   gain_db→Gain, pixel_format→PixelFormat, trigger_mode/source, ROI 등).
   조명/스트로브 키(lighting 등)는 `extract_strobe_config()` 로 분리 — 별도
   조명 컨트롤러/IO 소관.
5. **결선 지점(TODO 표시)**: `camera.py` 의 `_open_backend`/`configure`/`grab`/
   `_reconnect` 와 `trigger.py` 의 `DigitalIOTrigger._open_driver`/
   `MqttTrigger.connect` 에 통합 작업 목록을 docstring 으로 명시.
6. **픽셀포맷**: Mono8/Bayer*/RGB8/BGR8 → `_to_bgr()` 가 OpenCV BGR 로 변환.
   파이프라인은 BGR(HxWx3 uint8) 을 계약으로 가정한다.
7. **검증**: 실카메라 결선 후에도 `length`/`px_to_mm_scale` 캘리브레이션(스케일
   기준자, 부록 A.3)을 다시 잡고, MSA 반복성(QA)을 재측정한다.

## 표면 ONNX 모델 배치 (§6.3 점진 고도화)

데이터가 부족한 초기에는 고전 CV 폴백으로 동작하고, 데이터 축적 후 PyTorch
학습 → ONNX export 로 교체한다(파이프라인 무변경). 자세한 내용은
`models/README.md` 참조.

```python
from vision.surface import OnnxSurfaceModel
from vision.pipeline import InspectionPipeline
pipe = InspectionPipeline(surface_model=OnnxSurfaceModel())  # 모델 없으면 폴백
```

- 모델 경로: 생성자 인자 > `AIVIS_SURFACE_ONNX` > 기본 `models/surface.onnx`.
- 모델 없음/`onnxruntime` 미설치/로드 실패 → **자동 고전 CV 폴백**(미판정 0).
- export 골격: `python -m services.vision.models.export_surface_onnx --weights ... --out models/surface.onnx [--int8 --calib-dir ...]`.
- 임계는 모델이 아니라 ItemMaster 에서(하드코딩 금지). ONNX 는 score 만 산출.

## 안정화 / 자동검사율 100% (미판정 0)

`InspectionPipeline.run()` 은 **어떤 입력/예외에도 raise 하지 않고** 결정적
`VerdictResult` 를 반환한다. 전처리/길이/표면/종합 각 단계는 예외 격리되어,
실패 시 해당 단계를 '검출 실패'로 처리하고 최종적으로 **NG + review_flag=True
+ confidence=0** 로 강제한다(오검 방지 → 재확인 대상). 오류 사유가 필요하면
`run_safe(frame, item) -> (VerdictResult, reason)` 를 쓴다(`reason` 은 별도
채널 — shared-types 스키마는 변경하지 않는다).

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
