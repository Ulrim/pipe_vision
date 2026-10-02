"""체커보드 사진 → 렌즈 왜곡 캘리브레이션 파일 (M3, §6.2).

라즈베리파이 카메라는 광각이라 길이 측정 전에 왜곡 보정이 필요하다. 보정이
없으면 왜곡만으로 ±0.5mm 공차를 넘는다(calib/lens.py 참조).

촬영 방법:
  - 체커보드를 **제품이 놓일 평면과 같은 높이**에 두고 찍는다. 높이가 다르면
    보정은 되지만 px→mm 환산이 틀어진다.
  - 보드를 화면 **구석까지** 옮겨가며 10~20장. 왜곡은 가장자리에서 크므로
    가운데만 찍으면 그 부분이 추정되지 않는다.
  - 기울기도 섞는다(정면만 찍으면 내부 파라미터가 잘 안 풀린다).
  - 초점·줌을 **운영과 똑같이 고정**하고 찍는다. 초점이 바뀌면 배율이 바뀐다.

사용:
  python -m vision.tools.calibrate_lens --images calib/ --cols 9 --rows 6 \
      --square-mm 25 --out lens.json
  (cols/rows 는 **내부 코너 수**다. 10x7 칸 보드면 9x6)

적용:
  AIVIS_LENS_CALIB=/etc/aivis/lens.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import cv2

_EXT = (".jpg", ".jpeg", ".png", ".bmp")

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.calib.lens import CalibrationError, calibrate_from_checkerboard  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calibrate_lens")
    ap.add_argument("--images", required=True, type=Path, help="체커보드 사진 폴더")
    ap.add_argument("--cols", type=int, required=True, help="가로 내부 코너 수")
    ap.add_argument("--rows", type=int, required=True, help="세로 내부 코너 수")
    ap.add_argument("--square-mm", type=float, default=1.0, help="칸 한 변(mm)")
    ap.add_argument("--out", type=Path, default=Path("lens.json"))
    a = ap.parse_args(argv)

    paths = sorted(p for p in a.images.rglob("*") if p.suffix.lower() in _EXT)
    if not paths:
        print(f"사진이 없다: {a.images}", file=sys.stderr)
        return 2
    imgs = [cv2.imread(str(p)) for p in paths]
    imgs = [i for i in imgs if i is not None]

    try:
        calib, used = calibrate_from_checkerboard(
            imgs, cols=a.cols, rows=a.rows, square_mm=a.square_mm
        )
    except CalibrationError as exc:
        print(f"캘리브레이션 실패: {exc}", file=sys.stderr)
        return 1

    calib.save(str(a.out))
    report = {
        "images_found": len(paths),
        "boards_detected": used,
        "rms_px": round(calib.rms, 4),
        "image_size": list(calib.image_size),
        "dist_coeffs": [round(float(c), 6) for c in calib.dist_coeffs],
        "out": str(a.out),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if used < 10:
        print(
            f"경고: 체커보드를 {used}장에서만 찾았다. 가장자리 왜곡이 덜 추정된다 "
            "— 구석까지 옮겨가며 10장 이상 권장.",
            file=sys.stderr,
        )
    if calib.rms > 1.0:
        print(f"경고: 재투영 오차 {calib.rms:.2f}px — 흔들림/초점을 확인하라.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
