"""카메라 캘리브레이션 (M3, §6.2).

렌즈 왜곡 보정만 다룬다. px→mm 환산계수는 품목 기준정보
(`item_master.px_to_mm_scale`)의 몫이고 여기 두지 않는다 — 왜곡은 **카메라의
성질**이라 품목이 바뀌어도 그대로이고, 환산계수는 설치 높이·품목마다 다르다.
"""
from vision.calib.lens import (
    LensCalibration,
    calibrate_from_checkerboard,
    load_calibration,
    resolve_calibration,
)

__all__ = [
    "LensCalibration",
    "calibrate_from_checkerboard",
    "load_calibration",
    "resolve_calibration",
]
