# OPEN_MODELS — GitHub 공개 AI 모델(ONNX) 적용 가이드 (현장용)

> 대상 하드웨어: 라즈베리파이 4 (ARM CPU, GPU 없음). CLAUDE.md §6.3
> "학습은 PyTorch, 배포는 ONNX" 원칙에 따라, **어떤 GitHub 공개 모델이든
> "ONNX 파일 + 사이드카 json" 형식으로 만들면** AIVIS 표면 판정 파이프라인에
> 코드 수정 없이 꽂을 수 있다. 로드/실행에 실패하면 항상 고전 CV 폴백으로
> 내려가므로 검사(자동검사율 100%)는 멈추지 않는다.

## 0. 개요 — 무엇을 준비하면 되나

| 파일 | 설명 |
|---|---|
| `<모델>.onnx` | ONNX 로 export 한 모델(예: `surface.onnx`) |
| `<모델>.json` | **같은 basename** 의 사이드카 메타데이터(입출력 계약) |

배치 위치(둘 중 하나):
- 기본 경로: `services/vision/models/surface.onnx` (+ `surface.json`)
- 임의 경로 + 환경변수: `AIVIS_SURFACE_ONNX=/path/to/model.onnx`

모델 선택 우선순위(환경변수 `AIVIS_SURFACE_MODEL`, 기본 `auto`):

```
auto      : ONNX(로드 성공 시) > 이상탐지 npz(AIVIS_SURFACE_ANOMALY 존중) > 고전 CV
onnx      : 항상 ONNX(로드 실패 시 내부적으로 고전 CV 폴백)
anomaly   : 항상 이상탐지 npz(PaDiM-lite)
classical : 항상 고전 CV
```

지원 계약(kind)은 두 가지다. 그 외 출력 형식은 **명시적으로 로드 실패**
처리되어 고전 CV 폴백한다(조용한 오동작 금지).

- `surface3` : 유분기/변색/스크래치 3점수(0~1) 분류기.
  점수는 **기준정보(item_master) 임계값**으로 OK/NG 판정된다(하드코딩 없음).
- `anomaly`  : anomalib 계열 이상탐지(점수 스칼라 또는 anomaly map).
  고전 CV 점수/판정은 그대로 유지하고, 이상점수가 임계 이상이면 **재확인
  대상(review)** 으로만 표시한다. `force_ng=true` 로 명시했을 때만 NG 강제.

## 1. 사이드카 json 필드 표 (전체)

```json
{
  "kind": "surface3",
  "input_size": [256, 256],
  "layout": "NCHW",
  "color": "RGB",
  "scale": 0.00392156862745098,
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "input_name": null,
  "apply_sigmoid": false,
  "outputs": {"oil": 0, "discolor": 1, "scratch": 2},
  "anomaly": {"threshold": 0.5, "force_ng": false}
}
```

| 필드 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `kind` | O | — | `"surface3"` 또는 `"anomaly"` |
| `input_size` | O | — | `[H, W]` 모델 입력 크기(양의 정수 2개) |
| `layout` | X | `"NCHW"` | `"NCHW"` 또는 `"NHWC"` |
| `color` | X | `"RGB"` | `"RGB"`(torch 계열 관례) 또는 `"BGR"` |
| `scale` | X | `1/255` | 픽셀 스케일(uint8 → 곱함) |
| `mean` | X | `[0,0,0]` | 정규화 평균(스케일 적용 **후** 뺌), 3개 |
| `std` | X | `[1,1,1]` | 정규화 표준편차(뺀 뒤 나눔), 3개(0 금지) |
| `input_name` | X | `null` | `null` 이면 ONNX 세션 첫 입력 사용 |
| `apply_sigmoid` | X | `false` | 출력이 logit 이면 `true`(sigmoid 적용) |
| `outputs` | X* | `{"oil":0,"discolor":1,"scratch":2}` | `surface3` 전용. 값이 **정수**면 첫 출력 텐서(예: `[1,3]`)의 평탄화 인덱스, **문자열**이면 ONNX 출력 이름(스칼라) |
| `anomaly.threshold` | X* | `0.5` | `anomaly` 전용. 이상점수 판정 임계(양수) |
| `anomaly.force_ng` | X* | `false` | `anomaly` 전용. 임계 초과 시 MULTI + NG 강제. **현장 검증 후에만 켤 것** |

사이드카가 없으면: 출력 이름이 정확히 `oil` / `discolor` / `scratch` 인
모델(초기 export 골격 관례)만 `surface3` 로 자동 인식된다. 그 외에는
로드 실패(로그에 "사이드카 <경로>.json 필요") 후 고전 CV 폴백.

전처리 순서(결정적): BGR ROI → `color` 변환 → `input_size` 리사이즈
(INTER_AREA) → `x*scale` → `(x-mean)/std` → `layout` 변환 → float32 배치 1.

## 2. 레시피 A (권장) — anomalib 로 OK 이미지만으로 이상탐지 모델

라벨(불량 분류) 데이터가 없어도 **정상(OK) 이미지만으로** 학습할 수 있어
초기 도입에 가장 현실적이다. 학습은 개발 PC(학습 환경)에서, 추론은 파이에서.

```bash
# [개발 PC] 1) 설치 (Apache-2.0 라이선스 — 상용 OK)
pip install anomalib

# 2) OK 이미지 폴더로 학습 (Padim: 학습 빠름 / EfficientAD: 정확도↑)
anomalib train --model Padim \
  --data anomalib.data.Folder \
  --data.name aivis_pipe --data.normal_dir dataset/raw/OK \
  --data.root . 

# 3) ONNX export
anomalib export --model Padim \
  --ckpt_path results/Padim/aivis_pipe/latest/weights/lightning/model.ckpt \
  --export_type onnx
# → results/.../weights/onnx/model.onnx 생성
```

사이드카 작성(`model.json`, 모델과 같은 폴더·같은 basename):

```json
{
  "kind": "anomaly",
  "input_size": [256, 256],
  "layout": "NCHW",
  "color": "RGB",
  "scale": 0.00392156862745098,
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "anomaly": {"threshold": 0.5, "force_ng": false}
}
```

> `threshold` 는 anomalib 학습 로그의 정규화 임계(보통 0.5 근처)에서
> 시작해 **현장 검증 데이터로 보정**한다. 처음에는 `force_ng=false` 로
> 두고(재확인만 유도), 오검이 없다고 확인된 뒤에만 `true` 를 검토한다.

파이에 적용:

```bash
# [라즈베리파이] 4) 모델 복사 후 검증 게이트(반드시 실행)
scp model.onnx model.json pi@aivis:/opt/aivis/models/
export AIVIS_SURFACE_ONNX=/opt/aivis/models/model.onnx
cd services/vision
python -m tools.check_onnx --model /opt/aivis/models/model.onnx \
  --image /opt/aivis/samples/ok_side.jpg --repeats 50

# 5) p95 ≤ 300ms 합격이면 워커 재시작
docker compose restart vision   # (또는 systemd 서비스 재시작)
```

## 3. 레시피 B — 라벨 데이터 축적 후 3-클래스 분류기(surface3)

오검·미검 태깅(M16)으로 OIL/DIS/SCR 라벨이 충분히 모이면(부록 A.2 수량),
경량 분류기를 직접 학습한다. 파이 CPU 에서는 MobileNetV3-Small,
ShuffleNetV2 급을 권장한다.

```python
# [개발 PC] train_surface3.py 개요 (torchvision, BSD 라이선스)
import torch, torchvision
model = torchvision.models.mobilenet_v3_small(num_classes=3)
# ... dataset/raw/{OIL,DIS,SCR,OK} 멀티라벨(BCE) 학습 ...
model.eval()
dummy = torch.randn(1, 3, 224, 224)
torch.onnx.export(model, dummy, "surface.onnx", opset_version=17,
                  input_names=["input"], output_names=["scores"])
```

사이드카(`surface.json`):

```json
{
  "kind": "surface3",
  "input_size": [224, 224],
  "color": "RGB",
  "mean": [0.485, 0.456, 0.406],
  "std": [0.229, 0.224, 0.225],
  "apply_sigmoid": true,
  "outputs": {"oil": 0, "discolor": 1, "scratch": 2}
}
```

> 분류기 최종층이 sigmoid 없이 logit 을 내면 `apply_sigmoid: true` 필수.
> 판정 임계는 사이드카가 아니라 **기준정보(item_master 의
> oil/discolor/scratch_threshold)** 에서 읽으므로, 현장 보정은 관리자
> 화면(기준정보)에서 한다.

## 4. 검증 게이트 — `tools/check_onnx` (배포 전 필수)

```
python -m tools.check_onnx --model path/to/model.onnx --image sample.jpg \
    [--repeats 50] [--item HP12 --api-url http://api:8000] [--budget-ms 300]
```

출력: 사이드카 계약 요약 → 세션 로드 확인 → 1장 추론 점수 →
반복 지연 p50/p95(ms) → **300ms 예산 합격/불합격(한국어)**.

| 종료코드 | 의미 |
|---|---|
| 0 | 정상(합격) |
| 1 | 모델/사이드카 로드 실패 |
| 2 | p95 가 300ms(예산) 초과 |

`--image` 를 생략하면 합성 이미지로 지연만 확인한다. `--item` 미지정 시
기본 임계 0.5 로 "판정 표시만" 하므로, 실제 판정 확인은 `--item HP12
--api-url ...` 로 기준정보를 조회해서 한다.

## 5. 주의사항 (정직하게)

1. **사전학습 가중치는 우리 파이프를 모른다.** MVTec 등 공개 데이터셋으로
   학습된 가중치를 그대로 쓰면 판정이 무의미하다. 반드시 **자사 파이프
   이미지로 학습/파인튜닝**한 모델만 배포한다.
2. **라이선스 확인.** 모델·코드마다 다르다. Apache-2.0(anomalib),
   MIT, BSD(torchvision)는 상용 사용 가능. **AGPL-3.0(YOLOv8/ultralytics)은
   상용 배포 시 소스 공개 의무**가 있어 별도 상용 라이선스 없이는 주의.
3. **파이 CPU 는 느리다.** 큰 모델(EfficientAD-M, PatchCore 대형 백본 등)은
   300ms 를 초과하기 쉽다. 배포 전 `check_onnx` 로 **반드시 실측**하고,
   초과 시 입력 크기 축소/경량 백본/INT8 양자화를 검토한다.
4. **이미지 소스·조명 일관성.** 학습 이미지와 추론(현장 카메라) 이미지의
   구도·조명·해상도가 다르면 정확도가 급락한다(부록 A.3 촬영 표준 준수).
5. **폴백은 안전장치지 정답이 아니다.** 모델 로드 실패 시 고전 CV 로
   조용히 내려가므로, 배포 후 로그(`aivis.vision.surface` warning)와
   `check_onnx` 종료코드로 실제 ONNX 가 도는지 꼭 확인한다.

## 6. 관련 코드

- 사이드카 계약/로더: `services/vision/surface/onnx_meta.py`
- ONNX 추론(결선): `services/vision/surface/model.py` (`OnnxSurfaceModel`)
- 모델 선택 우선순위: `services/vision/surface/anomaly.py`
  (`resolve_surface_model`, env `AIVIS_SURFACE_MODEL`)
- 검증 CLI: `services/vision/tools/check_onnx.py`
- 테스트(미니 ONNX 실물 생성): `services/vision/tests/test_onnx_model.py`
