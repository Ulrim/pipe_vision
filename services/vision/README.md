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
| `worker/`      | §4 | **검사 워커 런타임 루프**(`python -m worker`): 트리거→grab→pipeline→POST /inspection. 기동 시퀀스(API readiness·ItemMaster 확보)·재시도·graceful 종료·헬스파일 |
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

## 검사 워커 (`worker/`, `python -m worker`)

`services/vision/Dockerfile` 의 `CMD ["python","-m","worker"]` 가 기동하는 **검사
런타임 루프**다(CLAUDE.md §4). 컨테이너는 `services/vision/*` 를 `/app` 에 flat
COPY 하므로 `worker/_bootstrap.py` 가 두 import 레이아웃을 흡수한다:
dev/테스트는 `vision.*` 패키지, 컨테이너 flat 은 현재 디렉터리를 `vision` 패키지로
합성 등록(상대 import `from .length import ...` 가 동작하도록).

루프: `트리거(주기) → camera.grab() → InspectionPipeline.run(frame, item) →
to_inspection_result(...) → POST {AIVIS_API_URL}/inspection`. `proc_time_ms` 포함.
**어떤 단계 예외에도 루프는 죽지 않고**(로그 후 계속) 성공/실패 카운트를 주기 로깅한다.

기동 시퀀스(견고):
1. **API readiness 폴링** — `GET /health` 가 `status=ok` 될 때까지 지수 백오프
   재시도(무한 대기 금지, `AIVIS_API_WAIT_TIMEOUT_S` 초과 시 종료코드 1).
2. **ItemMaster 확보** — `GET /master/items/{code}`. 이 GET 은 operator+ JWT
   가드라 service token 으로 안 통하면 `AIVIS_SEED_ADMIN_USER/PASSWORD` 로
   `POST /auth/login` 하여 Bearer 를 확보해 재시도한다. 404(미시드)면 backend 가
   데모 품목을 시드한다고 가정해 잠시 재시도(역시 타임아웃 한계 존재).
3. **합성 데이터셋 자립** — `AIVIS_DATASET_DIR` 가 비었거나 없으면
   `tools.gen_synthetic` 으로 합성 이미지 폴더를 자동 생성해 데모가 항상 돈다.
4. **헬스 파일** — 첫 루프 준비 시 `/tmp/vision_ready` 생성(Dockerfile
   healthcheck 계약 `test -f /tmp/vision_ready`), 종료 시 제거.
5. **graceful 종료** — `SIGTERM`/`SIGINT` 수신 시 진행 중 루프를 마치고 정리 후 종료.

### 환경변수

| 변수 | 기본 | 설명 |
|---|---|---|
| `AIVIS_CAMERA` | `sim` | `sim`\|`genicam`. factory 로 어댑터 선택 |
| `AIVIS_DATASET_DIR` | (없음) | SimulatorCamera 리플레이 폴더. 비었으면 합성 자동 생성 |
| `AIVIS_API_URL` | `http://api:8000` | 결과 POST/조회 대상 backend |
| `AIVIS_SERVICE_TOKEN` | (없음) | 설정 시 `POST /inspection` 에 `X-Service-Token`+`Bearer` 첨부 |
| `AIVIS_ITEM_CODE` | `HP12` | 검사 대상 품목(ItemMaster 조회 키) |
| `AIVIS_WORKER_INTERVAL_MS` | `1500` | 트리거 간격(ms). 0=최고속(배치/스모크) |
| `AIVIS_CAMERA_GRAB_TIMEOUT_S` | `5.0` | 취득 워치독: `camera.grab()` 이 이 시간 내 미반환 시 `CameraError` 승격+재연결 유도(0/음수=비활성) |
| `AIVIS_LOT` | `LOT{YYYYMMDD}` | 미설정 시 날짜 기반 자동 |
| `AIVIS_CAM_ID` | `CAM1` | 카메라 식별자 |
| `AIVIS_SHIFT` / `AIVIS_OPERATOR` | (없음) | 선택 메타 |
| `AIVIS_SEED_ADMIN_USER` / `AIVIS_SEED_ADMIN_PASSWORD` | `admin` / `admin1234` | master GET 인증 폴백용 로그인 |
| `AIVIS_API_WAIT_TIMEOUT_S` / `AIVIS_ITEM_WAIT_TIMEOUT_S` | `120` | readiness/ItemMaster 폴링 한계 |
| `AIVIS_HTTP_TIMEOUT_MS` | `5000` | httpx 요청 타임아웃 |
| `AIVIS_WORKER_LOG_EVERY` | `10` | 진행 로그 주기(루프 수) |
| `AIVIS_READY_FILE` | `/tmp/vision_ready` | healthcheck 파일 경로 |
| `AIVIS_WORKER_MAX_ITER` | `0` | 최대 루프 수(0=무한). 데모/스모크 유한 종료용 |

### 실행

```bash
# 온프레미스(compose) / 클라우드 데모 공통. AIVIS_DATASET_DIR 미설정이면 합성 자립.
export AIVIS_CAMERA=sim
export AIVIS_API_URL=http://localhost:8000
export AIVIS_ITEM_CODE=HP12
python -m worker            # 컨테이너 flat 레이아웃 기준(CMD 와 동일)
# 또는 dev 레이아웃:  PYTHONPATH=services python -m vision.worker  는 불필요 —
# worker 가 부트스트랩하므로 services/vision 에서 `python -m worker` 면 된다.
```

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
- **취득 타임아웃 워치독(`grab_timeout_s`)**: 실 하드웨어(`PiCameraAdapter` 등)에서
  `camera.grab()` 이 예외 없이 무기한 블로킹하는 사고(예: picamera2 내부 FIFO
  Job 큐 점유)를 방어한다. `grab()` 을 데몬 스레드에서 실행해
  `Thread.join(timeout=...)` 로 대기하고, 시간 초과 시 `CameraError` 로 승격해
  기존 재시도 경로에 편입시키며 카메라 핸들을 `close()` 로 버려 다음 시도가
  새 인스턴스로 재오픈하도록 유도한다(신호(signal) 미사용 — worker 가 메인
  스레드에 SIGTERM/SIGINT 핸들러를 이미 설치하므로 충돌 방지). 부수효과로
  `Worker` 메인 루프가 블로킹 없이 `_stop` 플래그를 즉시 재확인할 수 있게 된다.
  `AIVIS_CAMERA_GRAB_TIMEOUT_S`(기본 5.0s)로 조정하며, `0`/음수면 완전
  비활성(과거 동작과 100% 동일 — 시뮬레이터/테스트 회귀 없음). 타임아웃/재연결
  발생 횟수는 `AcquisitionService.timeout_count`/`reconnect_count` 로 노출되고
  워커 진행 로그(`AIVIS_WORKER_LOG_EVERY`)에 `grab_timeout=`/`grab_reconnect=`
  로 함께 찍힌다.

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

## 디버그 도구 — 길이 측정 시각화 진단 (`tools/debug_length.py`)

현장에서 촬영한 이미지 1장만으로 "왜 이 길이값이 나왔는지"(어떤 영역을
파이프로 인식했는지, 끝단을 어디로 잡았는지, 대비가 충분했는지)를 오프라인
에서 눈으로/수치로 확인하는 독립 실행형 CLI. `render_overlay()`(운영
오버레이)는 최종 판정만 보여주고 검출 내부값은 그리지 않으므로, 측정값이
이상할 때 원인을 추적하려면 이 도구를 쓴다. API/DB 연결이 필요 없다(완전
오프라인) — 표준 촬영 이미지 파일 경로만 있으면 즉시 실행된다.

```bash
cd services/vision && . ../../.venv-all/bin/activate  # venv 활성화(경로는 환경에 맞게)
python -m tools.debug_length /var/lib/aivis/images/raw/xxx.jpg
```

기본값은 데모 시드값(`ref_length_mm=125.0`, `px_to_mm_scale=0.25`)이라
바로 실행 가능하지만, 실제 품목 기준으로 진단하려면 옵션을 넘긴다:

```bash
python -m tools.debug_length raw.jpg \
    --scale 0.1832 --ref-length-mm 248.5 --tol-plus-mm 0.5 --tol-minus-mm 0.5 \
    --min-contrast 20.0

# 촬영 레시피(AF/AE 고정 여부)까지 진단하려면:
python -m tools.debug_length raw.jpg \
    --capture-recipe '{"af_mode":"manual","exposure_us":8000}'

# 다중튜브(다객체) 진단 — 기대 튜브 개수를 지정:
python -m tools.debug_length raw.jpg --multi 5
```

산출물:
- `<입력파일명>_debug.jpg` — 원본 위에 Otsu 마스크(반투명), `length_roi`
  bbox, 좌/우 끝단(정수 위치 점선 vs 서브픽셀 보정 위치 실선), 최종
  OK/NG·수치 패널, 하단 밝기 프로파일+그래디언트 라인 그래프(argmax/argmin
  마커)를 그린 이미지. `--multi N` 이면 개요(`_debug.jpg`, seam+튜브 bbox)
  + 튜브별(`_tube{N}_debug.jpg`) 이미지를 각각 생성한다.
- stdout 한국어 진단 텍스트 — 검출 bbox/컨투어 면적, 대비 vs `min_contrast`
  게이트 통과 여부, 좌/우 에지의 정수·서브픽셀 위치와 보정 적용 여부, 최종
  `meas_length_mm`/`deviation_mm`/판정, 그리고 정적 임계 비교로 판별 가능한
  경고(마스크 폴라리티 의심, 컨투어가 프레임 경계에 닿음, 종횡비 이상, 대비
  부족, `capture_recipe` AF/AE 미고정, 데모 시드값 미교정 등).

측정 로직(`length.measure.measure_length`/`_find_edges`/`_parabolic_subpixel`,
`preprocess.roi.preprocess`/`segment_pipe_roi`)은 그대로 재사용하며 이 도구
안에서 재구현하지 않는다(단일 진실원). shared-types 는 변경하지 않으므로
`EdgeDebugInfo` 는 `tools/debug_length.py` 내부 로컬 dataclass 로만 존재한다
(추후 `render_overlay()`/`LengthResult` 확장 시 재사용 가능하도록 필드명을
맞춰 두었다 — 스키마 반영은 오케스트레이터 승인 후 별도 작업).

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
