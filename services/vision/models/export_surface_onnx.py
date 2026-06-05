"""표면 모델 PyTorch → ONNX export 골격 (§6.3, M16 점진 고도화).

데이터가 축적되면(부록 A.2 최소 수량 + 경계 샘플) PyTorch 로 학습한 표면
분류/세그 모델을 ONNX 로 export 하여 services/vision/models/surface.onnx 로
배포한다. 배포되면 OnnxSurfaceModel 이 자동으로 ONNX 추론을 사용하고,
미배포 시에는 고전 CV 폴백으로 동작한다("동작하는 폴백 → 점진 고도화").

이 파일은 **결선 지점 골격**이다. torch 는 추론 런타임 의존성이 아니므로
(requirements.txt 에 없음) 동적 import 로 감싸 학습 환경에서만 실행한다.

사용 예(학습 환경):
    python -m services.vision.models.export_surface_onnx \
        --weights runs/surface_best.pt --out services/vision/models/surface.onnx \
        --input-size 256 --opset 17 [--int8 --calib-dir dataset/raw]

배포 후:
    export AIVIS_SURFACE_ONNX=services/vision/models/surface.onnx
    # 또는 기본 경로(services/vision/models/surface.onnx)에 두면 자동 인식.

INT8/양자화(엣지 가속, KPI 300ms):
    --int8 지정 시 onnxruntime.quantization 의 정적 양자화(QDQ)를 적용한다.
    calib_dir(대표 이미지 폴더)로 캘리브레이션 데이터를 공급한다. GPU 가용 시에는
    FP16 + CUDA/TensorRT EP 로도 충분할 수 있다(프로파일링 후 결정).
"""
from __future__ import annotations

import argparse
from pathlib import Path


def export(
    weights: str,
    out: str,
    *,
    input_size: int = 256,
    opset: int = 17,
    int8: bool = False,
    calib_dir: str | None = None,
) -> str:
    """PyTorch 체크포인트 → ONNX. (학습 환경 전용; torch 동적 import)

    반환: 생성된 onnx 경로. torch 미설치 시 명확한 안내 예외.
    """
    try:
        import torch  # type: ignore
    except ImportError as exc:  # pragma: no cover - 학습 환경에서만 실행
        raise RuntimeError(
            "export_surface_onnx 는 학습 환경에서 실행한다(torch 필요). "
            "`pip install torch` 후 재시도. 추론 런타임에는 torch 가 없어도 된다."
        ) from exc

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # TODO(P7): 학습 정의에 맞춰 모델 클래스/입력 텐서 결선.
    #   model = build_surface_model(); model.load_state_dict(torch.load(weights))
    #   model.eval()
    #   dummy = torch.randn(1, 3, input_size, input_size)
    #   torch.onnx.export(
    #       model, dummy, str(out_path), opset_version=opset,
    #       input_names=["input"], output_names=["oil", "discolor", "scratch"],
    #       dynamic_axes={"input": {0: "batch"}},
    #   )
    raise NotImplementedError(
        "표면 모델 ONNX export 는 학습 정의 확정 후 결선한다(§6.3). "
        f"인자: weights={weights}, out={out}, size={input_size}, "
        f"opset={opset}, int8={int8}, calib_dir={calib_dir}."
    )


def quantize_int8(  # pragma: no cover - 학습/배포 환경 전용
    onnx_fp32: str, onnx_int8: str, calib_dir: str
) -> str:
    """ONNX FP32 → INT8 정적 양자화(QDQ). onnxruntime.quantization 사용.

    TODO(P7): CalibrationDataReader 구현(calib_dir 의 대표 이미지 전처리) 후
    quantize_static(onnx_fp32, onnx_int8, reader, quant_format=QDQ).
    """
    raise NotImplementedError(
        "INT8 양자화 결선은 export 정의 확정 후 구현(calibration reader 필요)."
    )


def _main() -> None:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="표면 모델 ONNX export (§6.3)")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", default="services/vision/models/surface.onnx")
    ap.add_argument("--input-size", type=int, default=256)
    ap.add_argument("--opset", type=int, default=17)
    ap.add_argument("--int8", action="store_true")
    ap.add_argument("--calib-dir")
    args = ap.parse_args()
    path = export(
        args.weights, args.out,
        input_size=args.input_size, opset=args.opset,
        int8=args.int8, calib_dir=args.calib_dir,
    )
    print(f"exported: {path}")


if __name__ == "__main__":  # pragma: no cover
    _main()
