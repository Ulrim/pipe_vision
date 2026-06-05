# 표면 모델 배포 (`services/vision/models`)

§6.3 점진 고도화: **데이터 부족 초기에는 고전 CV 폴백**으로 동작하고, 데이터가
축적되면 PyTorch 학습 → ONNX export 산출물을 여기에 배치해 추론을 교체한다.
파이프라인/인터페이스 변경 없이 `OnnxSurfaceModel` 이 자동 전환한다.

## 배치 방법

1. 학습 환경에서 export(골격: `export_surface_onnx.py`):
   ```bash
   python -m services.vision.models.export_surface_onnx \
       --weights runs/surface_best.pt \
       --out services/vision/models/surface.onnx \
       --input-size 256 --opset 17
   ```
2. 모델 파일을 다음 중 한 곳에 둔다:
   - 기본 경로: `services/vision/models/surface.onnx` (자동 인식)
   - 또는 임의 경로 + 환경변수: `export AIVIS_SURFACE_ONNX=/path/to/surface.onnx`
3. 파이프라인에서 ONNX 백엔드 사용:
   ```python
   from vision.surface import OnnxSurfaceModel
   from vision.pipeline import InspectionPipeline
   pipe = InspectionPipeline(surface_model=OnnxSurfaceModel())
   ```
   - 모델이 없거나 `onnxruntime` 미설치/로드 실패면 **자동으로 고전 CV 폴백**
     (자동검사율 100% 유지, 미판정 0).

## 동작 분기 (OnnxSurfaceModel)

| 상황 | 동작 |
|---|---|
| 모델 파일 존재 + onnxruntime OK | ONNX 추론(`_infer`) — 결선 후 |
| 모델 파일 없음 | 고전 CV 폴백(`analyze_surface`) |
| onnxruntime 미설치 | 고전 CV 폴백(+`_load_error` 기록) |
| 모델 로드 실패(손상 등) | 고전 CV 폴백(+`_load_error` 기록) |

- 모델 경로 우선순위: 생성자 인자 > `AIVIS_SURFACE_ONNX` > 기본 경로.
- 임계값은 모델이 아니라 **ItemMaster**(oil/discolor/scratch_threshold)에서
  읽는다(하드코딩 금지). ONNX 는 score(0~1)만 산출하고 판정은 임계로 한다.
- 실행 프로바이더: `AIVIS_ONNX_PROVIDERS`(쉼표구분, 예
  `CUDAExecutionProvider,CPUExecutionProvider`) > CPU.

## INT8 / 양자화 (엣지 가속, 300ms KPI)

- `export_surface_onnx.py --int8 --calib-dir <대표이미지폴더>` 로 정적 양자화
  (QDQ) 골격을 제공한다. CalibrationDataReader 결선은 학습/배포 환경에서 한다.
- GPU 가용 시 FP16 + CUDA/TensorRT EP 도 후보. 1,000장 배치 p50/p95/p99 를
  프로파일링(QA §1.2)해 가장 빠른 조합을 채택한다.

> 현재 저장소에는 학습 데이터/가중치가 없으므로 `.onnx` 산출물을 커밋하지 않는다.
> 모델 미배포가 정상 상태이며 고전 CV 폴백으로 전 파이프라인이 동작한다.
