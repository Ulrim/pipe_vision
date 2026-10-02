"""렌즈 왜곡 보정 — 라즈베리파이 카메라로 길이를 재려면 필수다.

**왜 필요한가.** 길이는 `픽셀거리 × px_to_mm_scale` 로 계산한다(§6.2). 이 식은
화면 어디서나 배율이 같다고 가정하는데, 광각 렌즈는 그렇지 않다. 중심에서
멀수록 눌려 보인다. 그런데 길이 측정은 **제품의 양 끝단**을 쓰므로 하필
왜곡이 가장 큰 가장자리를 본다.

합성 영상으로 재보면(800x300, 튜브가 폭의 대부분을 차지):

    k1=-0.05 → -0.50mm,  k1=-0.10 → -1.00mm
    k1=-0.20 → -2.50mm,  k1=-0.30 → -3.50mm

±0.5mm 공차 기준으로 1~7배다. 보정하면 잔차 0.000mm 로 복원된다. 즉 이건
센서나 알고리즘의 한계가 아니라 **빠져 있던 보정 단계**다.

라즈베리파이 카메라 모듈은 광각(대각 75도 내외)이라 이 보정 없이는 길이
검사가 성립하지 않는다.

사용:
    python -m vision.tools.calibrate_lens --images calib/ --cols 9 --rows 6 \
        --square-mm 25 --out lens.json
    AIVIS_LENS_CALIB=/etc/aivis/lens.json 로 파이프라인에 적용.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

#: 캘리브레이션 파일 경로 환경변수.
ENV_LENS_CALIB = "AIVIS_LENS_CALIB"


class CalibrationError(Exception):
    """캘리브레이션 입력/파일 오류."""


@dataclass
class LensCalibration:
    """렌즈 왜곡 계수 + 보정 맵 캐시.

    맵 생성은 비싸므로(해상도만큼의 float 배열 2개) 해상도별로 한 번만 만들어
    재사용한다. 파이에서 매 프레임 다시 만들면 처리시간 예산을 깎아먹는다.
    """

    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    image_size: Tuple[int, int]  # (w, h) — 캘리브레이션 당시
    rms: float = 0.0
    created_at: str = ""
    _maps: dict = field(default_factory=dict, repr=False, compare=False)

    def _map_for(self, w: int, h: int):
        key = (w, h)
        m = self._maps.get(key)
        if m is None:
            K = self.camera_matrix.copy()
            if (w, h) != tuple(self.image_size):
                # 다른 해상도로 찍었으면 내부 파라미터를 비례 축소한다.
                sx = w / float(self.image_size[0])
                sy = h / float(self.image_size[1])
                K[0, 0] *= sx
                K[0, 2] *= sx
                K[1, 1] *= sy
                K[1, 2] *= sy
            m = cv2.initUndistortRectifyMap(
                K, self.dist_coeffs, None, K, (w, h), cv2.CV_16SC2
            )
            self._maps[key] = m
        return m

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        """왜곡 보정. 입력 해상도가 캘리브레이션과 달라도 비례 적용한다."""
        if frame is None or frame.size == 0:
            return frame
        h, w = frame.shape[:2]
        mx, my = self._map_for(w, h)
        return cv2.remap(frame, mx, my, cv2.INTER_LINEAR)

    def as_dict(self) -> dict:
        return {
            "camera_matrix": self.camera_matrix.tolist(),
            "dist_coeffs": self.dist_coeffs.ravel().tolist(),
            "image_size": list(self.image_size),
            "rms": round(float(self.rms), 5),
            "created_at": self.created_at,
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, ensure_ascii=False, indent=2)
            fh.write("\n")


def load_calibration(path: str) -> LensCalibration:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return LensCalibration(
            camera_matrix=np.array(d["camera_matrix"], dtype=np.float64),
            dist_coeffs=np.array(d["dist_coeffs"], dtype=np.float64),
            image_size=(int(d["image_size"][0]), int(d["image_size"][1])),
            rms=float(d.get("rms", 0.0)),
            created_at=str(d.get("created_at", "")),
        )
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise CalibrationError(f"{path}: 캘리브레이션을 읽을 수 없다 ({exc})") from exc


def resolve_calibration(path: Optional[str] = None) -> Optional[LensCalibration]:
    """경로 또는 환경변수에서 캘리브레이션을 찾는다. 없으면 None(보정 안 함).

    **없다고 예외를 던지지 않는다.** 시뮬레이터·합성 테스트는 왜곡이 없으므로
    보정이 필요 없고, 현장도 캘리브레이션 전에는 보정 없이 돌아야 한다.
    다만 길이 정확도는 보장되지 않는다 — 그래서 호출부가 경고를 남긴다.
    """
    p = path or os.environ.get(ENV_LENS_CALIB)
    if not p:
        return None
    if not os.path.exists(p):
        raise CalibrationError(f"{p}: 캘리브레이션 파일이 없다")
    return load_calibration(p)


def calibrate_from_checkerboard(
    images: Sequence[np.ndarray],
    *,
    cols: int,
    rows: int,
    square_mm: float = 1.0,
) -> Tuple[LensCalibration, int]:
    """체커보드 사진들 → 왜곡 계수. (캘리브레이션, 사용된 장수) 반환.

    `cols`/`rows` 는 **내부 코너 수**다(칸 수가 아니다). 10x7 칸 보드면 9x6.
    """
    if cols < 3 or rows < 3:
        raise CalibrationError("내부 코너는 가로·세로 각각 3 이상이어야 한다")
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= float(square_mm)

    obj_points: list = []
    img_points: list = []
    size: Optional[Tuple[int, int]] = None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    for img in images:
        if img is None or img.size == 0:
            continue
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if size is None:
            size = (gray.shape[1], gray.shape[0])
        elif (gray.shape[1], gray.shape[0]) != size:
            raise CalibrationError("모든 사진의 해상도가 같아야 한다")
        ok, corners = cv2.findChessboardCorners(
            gray, (cols, rows),
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
        )
        if not ok:
            continue
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
        obj_points.append(objp)
        img_points.append(corners)

    if len(obj_points) < 3 or size is None:
        raise CalibrationError(
            f"체커보드를 찾은 사진이 {len(obj_points)}장뿐이다 — 최소 3장(권장 10장 이상)"
        )
    rms, K, D, _rv, _tv = cv2.calibrateCamera(
        obj_points, img_points, size, None, None
    )
    return (
        LensCalibration(
            camera_matrix=np.asarray(K, dtype=np.float64),
            dist_coeffs=np.asarray(D, dtype=np.float64).ravel(),
            image_size=size,
            rms=float(rms),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        ),
        len(obj_points),
    )
