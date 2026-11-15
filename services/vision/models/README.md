# 표면 모델 배포 (`services/vision/models`)

§6.3 점진 고도화: **데이터 부족 초기에는 고전 CV 폴백**으로 동작하고, 데이터가
축적되면 PyTorch 학습 → ONNX export 산출물을 여기에 배치해 추론을 교체한다.
파이프라인/인터페이스 변경 없이 `OnnxSurfaceModel` 이 자동 전환한다.

## 배치 방법

1. 학습 환경(torch 필요)에서 학습하면 ONNX 까지 한 번에 나온다(`train_surface.py`):
   ```bash
   python -m vision.models.train_surface \
       --train <data>/train --test <data>/test --out runs/surface_v1
   ```
   산출물: `surface.onnx` + `surface.json`(사이드카: kind=surface3) + `metrics.json`(권장 임계).
   **기준정보 임계를 `metrics.json` 권장값으로 바꿔야 한다** — 고전 CV 점수와 척도가 다르다.
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

## 속도

표면 모델은 작은 CNN(약 0.1M 파라미터, 입력 3×64×512)이라 파이 CPU 에서 수 ms 다 — 양자화 없이
300ms 예산 안에 든다. 더 큰 모델로 바꾸게 되면 그때 INT8 양자화나 GPU 프로바이더를 검토한다
(종전 `export_surface_onnx.py` 는 구현 없는 골격이어서 2026-11-15 에 지웠다).

> 현재 저장소에는 학습 데이터/가중치가 없으므로 `.onnx` 산출물을 커밋하지 않는다.
> 모델 미배포가 정상 상태이며 고전 CV 폴백으로 전 파이프라인이 동작한다.
