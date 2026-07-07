"""길이 측정 디버그 시각화 CLI (독립 실행형, 진단 전용) — CLAUDE.md §6.2.

현장(라즈베리파이) 담당자가 촬영한 이미지 1장만으로, 시스템이 무엇을
'파이프(전경)'로 인식했는지, 끝단을 어디로 잡았는지, 왜 그 지점을 골랐는지를
눈으로/수치로 확인할 수 있게 한다. `render_overlay()`(imaging/save.py)는
최종 판정만 표시하고 검출 내부값(마스크/끝단 좌표/프로파일)은 전혀 그리지
않아 "끝단을 어떻게 정하는지 이해가 안 된다"는 현장 보고의 직접 원인이었다
(코드상 확인됨 — 이 도구가 그 공백을 메운다).

설계 원칙(반드시 지킬 것):
  - services/vision/length/measure.py, services/vision/preprocess/roi.py 는
    이 파일에서 **수정하지 않는다**. 판정에 쓰이는 실제 함수
    (segment_pipe_roi/preprocess, measure_length, 그리고 private
    `_find_edges`/`_parabolic_subpixel`)를 그대로 호출해 단일 진실원을
    유지한다. 시각화를 위해 필요한 '중간값'(그래디언트 등)만 동일 수식으로
    재현하며, 재현한 값은 실제 판정에 관여하지 않는다(표시 전용).
  - packages/shared-types 는 변경하지 않는다. `EdgeDebugInfo` 는 이 모듈
    내부 dataclass 로만 존재한다(services/vision/multi/batch.py 의
    BatchResult/TubeResult 와 동일한 선례 — "오케스트레이터 승인 전" 정책).
    필드명은 향후 LengthResult 확장/render_overlay 재사용을 염두에 두고
    지었다(예: length_roi bbox, left/right edge px) — 실제 스키마 반영은
    오케스트레이터 승인 후 별도 작업.
  - 완전 오프라인: API/DB 연결 없이 이미지 경로 + 수동 옵션만으로 즉시 동작한다.
  - matplotlib 등 신규 의존성 금지 — OpenCV(cv2)만으로 그래프까지 그린다.
  - 결정적: 동일 입력 → 동일 출력(그림/텍스트 모두).

실행(venv 활성화 후, services/vision 디렉터리에서):

    cd services/vision
    python -m tools.debug_length /var/lib/aivis/images/raw/HP12_SIDE_..._001.jpg

    # 데모 기본값이 아니라 실제 캘리브레이션 값을 넣고 싶다면:
    python -m tools.debug_length raw.jpg --scale 0.1832 --ref-length-mm 248.5 \
        --tol-plus-mm 0.5 --tol-minus-mm 0.5

    # 다중튜브(--multi) 진단:
    python -m tools.debug_length raw.jpg --multi 5

출력: `<입력파일명>_debug.jpg`(시각화) + stdout 한국어 진단 텍스트.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


def _ensure_vision_importable() -> None:
    """`vision.*` 절대 import 가능하도록 sys.path 를 보강한다.

    worker/_bootstrap.py 와 동일 전략: standalone 실행
    (`cd services/vision && python -m tools.debug_length ...`, 이때
    __package__ 는 'tools')과 패키지 실행(pytest 의 `vision.tools.debug_length`,
    또는 `python -m services.vision.tools.debug_length`) 양쪽에서 동일하게
    동작하도록 services/ 를 sys.path 에 얹는다. 이미 'vision' 패키지가
    (pytest 등에 의해) import 되어 있으면 아무것도 하지 않는다.
    """
    import importlib.util

    here = Path(__file__).resolve()
    vision_root = here.parents[1]      # services/vision
    services_root = here.parents[2]    # services

    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))

    if importlib.util.find_spec("vision") is None:
        import importlib.machinery

        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec("vision", loader=None, is_package=True)
        )
        pkg.__path__ = [str(vision_root)]  # type: ignore[attr-defined]
        sys.modules.setdefault("vision", pkg)


_ensure_vision_importable()

from aivis_types import ItemMaster, LengthResult  # noqa: E402
from vision.imaging.save import (  # noqa: E402
    _BLACK,
    _FONT,
    _GREEN,
    _RED,
    _WHITE,
)
from vision.length.measure import (  # noqa: E402  (private 재사용 — 재구현 금지)
    _find_edges,
    _parabolic_subpixel,
    measure_length,
)
from vision.multi.segment import TubeROI, segment_tubes  # noqa: E402
from vision.preprocess.roi import Roi, preprocess  # noqa: E402

__all__ = [
    "EdgeDebugInfo",
    "compute_edge_debug",
    "build_diagnostics",
    "render_debug_overlay",
    "render_multi_overview",
    "format_report",
    "build_arg_parser",
    "main",
]

# ---- 데모 시드값(services/api/main.py _seed_demo_item 과 동일 — rank1/rank7) ----
DEMO_SCALE = 0.25
DEMO_REF_MM = 125.0

# ---- 색(BGR). OK/NG 계열은 imaging/save.py 관례 재사용(색약 고려 일관성). ----
_MASK_COLOR = (255, 0, 255)      # Otsu 마스크 오버레이(마젠타)
_BBOX_COLOR = (0, 220, 255)      # length_roi bbox(주황/노랑)
_EDGE_INT_COLOR = (0, 140, 255)  # 정수 그래디언트 극값 위치(주황)
_EDGE_SUB_COLOR = (255, 255, 0)  # 서브픽셀 보정 위치(청록)
_SEAM_COLOR = (0, 220, 255)

_PANEL_H = 170
_PANEL_BG = (24, 24, 24)

# 정적 임계(진단용 참고치 — 판정에는 관여하지 않는다).
_ASPECT_RATIO_MIN = 1.5
_MASK_BORDER_FG_WARN = 0.5
_MIN_CONTOUR_AREA_PX = 50.0  # roi.py segment_pipe_roi() 내부 임계와 동일(안내용).


@dataclass
class EdgeDebugInfo:
    """길이 측정 디버그 정보(원본 프레임 좌표계, 이 모듈 전용 로컬 데이터).

    shared-types 를 변경하지 않으므로 LengthResult 를 대체하지 않고 감싼다
    (`length` 필드가 measure_length() 의 원본 결과 그대로).
    """

    frame_shape: Tuple[int, int]                # (h, w)
    mask: Optional[np.ndarray]                   # segment_pipe_roi 산출 마스크
    length_roi: Optional[Roi]                     # 원본 좌표 bbox(None=미검출)
    contour_area_px: Optional[float]
    roi_touches_border: bool
    mask_border_fg_ratio: float
    profile: np.ndarray                            # 1D 밝기 프로파일(ROI 내부)
    gradient: np.ndarray                            # 프로파일 그래디언트(표시용 재현)
    roi_x0: int
    roi_y0: int
    roi_y1: int
    left_idx: Optional[int]                          # 그래디언트 argmax(로컬 idx)
    right_idx: Optional[int]                          # 그래디언트 argmin(로컬 idx)
    left_sub_local: Optional[float]                    # 서브픽셀 보정 후(로컬 idx)
    right_sub_local: Optional[float]
    left_delta: Optional[float]                          # 포물선 보정량(표시용)
    right_delta: Optional[float]
    left_applied: bool                                    # |delta|<=1 로 보정 반영됐는지
    right_applied: bool
    left_abs: Optional[float]                              # 전체 프레임 절대 x(서브픽셀)
    right_abs: Optional[float]
    contrast: float
    min_contrast: float
    length: LengthResult                                     # measure_length() 원본 결과


def _profile_and_gradient(profile: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """measure._find_edges 의 평활+그래디언트 계산을 표시용으로 재현한다.

    실제 끝단 판정(성공/실패, 서브픽셀 보정값)은 절대 여기서 재구현하지 않고
    `_find_edges`/`_parabolic_subpixel` 을 그대로 호출한다(단일 진실원). 이
    함수는 그 산출 과정의 중간값(smooth/grad)을 그래프로 보여주기 위해 동일
    수식만 되풀이한다.
    """
    if profile.size < 2:
        return profile.astype(np.float32), np.zeros_like(profile, dtype=np.float32)
    smooth = np.convolve(profile, np.ones(3, dtype=np.float32) / 3.0, mode="same")
    grad = np.gradient(smooth)
    return smooth, grad


def _mask_border_fg_ratio(mask: np.ndarray) -> float:
    """마스크 테두리(상하좌우 1px) 전경 비율 — 폴라리티 반전 의심 지표."""
    if mask is None or mask.size == 0:
        return 0.0
    border = np.concatenate([mask[0, :], mask[-1, :], mask[:, 0], mask[:, -1]])
    return float((border > 0).mean())


def compute_edge_debug(
    img_bgr: np.ndarray, item: ItemMaster, min_contrast: float
) -> EdgeDebugInfo:
    """preprocess() + measure_length() 를 그대로 호출해 디버그 정보를 만든다.

    pipeline.py `_run_core` 와 동일한 흐름(length_roi 크롭 → measure_length)을
    재현해, 실제 운영 파이프라인과 동일한 입력으로 진단한다.
    """
    h, w = img_bgr.shape[:2]
    pre = preprocess(img_bgr)
    roi = pre.length_roi
    if roi is not None:
        gray_roi = roi.crop(pre.gray_corrected)
        roi_x0, roi_y0, roi_y1 = roi.x0, roi.y0, roi.y1
    else:
        gray_roi = pre.gray_corrected
        roi_x0, roi_y0, roi_y1 = 0, 0, h

    profile = np.array([], dtype=np.float32)
    if gray_roi is not None and gray_roi.ndim == 2 and gray_roi.shape[1] >= 1:
        profile = gray_roi.astype(np.float32).mean(axis=0)
    contrast = float(profile.max() - profile.min()) if profile.size else 0.0
    _smooth, grad = _profile_and_gradient(profile)

    # 공식 판정 경로(measure._find_edges)를 그대로 호출한다 — 단일 진실원.
    # 성공 시 반환되는 (left, right) 서브픽셀 좌표는 아래 measure_length() 가
    # 내부적으로 산출하는 값과 100% 동일 입력에서 나온 것이라 항상 일치한다.
    edges = _find_edges(profile, min_contrast) if profile.size else None

    left_idx = right_idx = None
    left_sub_local = right_sub_local = None
    left_delta = right_delta = None
    left_applied = right_applied = False
    if grad.size >= 2:
        # _find_edges 는 정수 극값 위치(보정 전)를 반환하지 않으므로, 화면에
        # 보여줄 "정수 위치"만 동일 수식(argmax/argmin)으로 재현한다.
        left_idx = int(np.argmax(grad))
        right_idx = int(np.argmin(grad))
        abs_grad = np.abs(grad)
        if edges is not None:
            # 성공: 실제 판정에 쓰인 서브픽셀 값을 그대로 표시(진실원 일치).
            left_sub_local, right_sub_local = edges
        else:
            # 실패(게이트 미달 등): _parabolic_subpixel 을 직접 재사용해
            # "만약 채택됐다면" 값을 참고용으로만 보여준다(공식 결과 아님).
            left_sub_local = _parabolic_subpixel(abs_grad, left_idx)
            right_sub_local = _parabolic_subpixel(abs_grad, right_idx)
        left_delta = left_sub_local - float(left_idx)
        right_delta = right_sub_local - float(right_idx)
        left_applied = abs(left_delta) > 1e-9
        right_applied = abs(right_delta) > 1e-9

    left_abs = None if left_sub_local is None else roi_x0 + left_sub_local
    right_abs = None if right_sub_local is None else roi_x0 + right_sub_local

    # 컨투어 면적 복원: pre.mask 는 segment_pipe_roi() 가 채운 최종 마스크와
    # 동일 배열이다(성공 시 최대 컨투어 필, 실패 시 원시 Otsu 이진화). 여기서
    # Otsu/모폴로지 로직을 재구현하지 않고 findContours 1회만 추가 호출해
    # 정확한 면적을 복원한다.
    contour_area_px: Optional[float] = None
    if pre.mask is not None and pre.mask.any():
        cnts, _ = cv2.findContours(pre.mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            contour_area_px = float(cv2.contourArea(max(cnts, key=cv2.contourArea)))

    mask_border_fg_ratio = (
        _mask_border_fg_ratio(pre.mask) if pre.mask is not None else 0.0
    )
    roi_touches_border = False
    if roi is not None:
        roi_touches_border = (
            roi.x0 <= 1 or roi.y0 <= 1 or roi.x1 >= w - 1 or roi.y1 >= h - 1
        )

    length = measure_length(gray_roi, item, min_contrast=min_contrast)

    return EdgeDebugInfo(
        frame_shape=(h, w),
        mask=pre.mask,
        length_roi=roi,
        contour_area_px=contour_area_px,
        roi_touches_border=roi_touches_border,
        mask_border_fg_ratio=mask_border_fg_ratio,
        profile=profile,
        gradient=grad,
        roi_x0=roi_x0,
        roi_y0=roi_y0,
        roi_y1=roi_y1,
        left_idx=left_idx,
        right_idx=right_idx,
        left_sub_local=left_sub_local,
        right_sub_local=right_sub_local,
        left_delta=left_delta,
        right_delta=right_delta,
        left_applied=left_applied,
        right_applied=right_applied,
        left_abs=left_abs,
        right_abs=right_abs,
        contrast=contrast,
        min_contrast=float(min_contrast),
        length=length,
    )


def build_diagnostics(
    dbg: EdgeDebugInfo,
    item: ItemMaster,
    *,
    capture_recipe: Optional[Dict[str, Any]],
) -> Tuple[List[str], List[str]]:
    """정적 임계 비교로 판별 가능한 것만 경고한다(과잉 추론 금지).

    반환: (warnings, notes). warnings 는 이 이미지에서 실제로 의심되는 문제,
    notes 는 참고용 안내(문제 단정 아님).
    """
    warnings: List[str] = []
    notes: List[str] = []

    if dbg.length_roi is None:
        if dbg.contour_area_px is not None and dbg.contour_area_px < _MIN_CONTOUR_AREA_PX:
            warnings.append(
                f"가장 큰 컨투어 면적이 {dbg.contour_area_px:.0f}px^2로 내부 최소 임계"
                f"({_MIN_CONTOUR_AREA_PX:.0f}px^2, roi.py segment_pipe_roi) 미만 → "
                "잡음으로 기각되어 파이프 미검출 처리됨."
            )
        else:
            warnings.append(
                "파이프 미검출: Otsu 이진화에서 유효한 전경 컨투어를 찾지 못했습니다 "
                "(배경 대비 부족, 조명/무광 배경판 미설치, 또는 프레임에 파이프가 "
                "없을 가능성)."
            )
    else:
        if dbg.mask_border_fg_ratio >= _MASK_BORDER_FG_WARN:
            warnings.append(
                f"마스크 폴라리티 의심: 프레임 테두리의 {dbg.mask_border_fg_ratio * 100:.0f}"
                "%가 전경(흰색)으로 판정됨 → Otsu 극성 반전(밝은 배경/책상) 또는 배경 "
                "오염 의심."
            )
        if dbg.roi_touches_border:
            warnings.append("컨투어가 프레임 경계에 닿음 → 배경 물체와 병합 의심.")
        h_roi = max(1, dbg.length_roi.height)
        aspect = dbg.length_roi.width / h_roi
        if aspect < _ASPECT_RATIO_MIN:
            warnings.append(
                f"컨투어 종횡비 {aspect:.2f}:1(<{_ASPECT_RATIO_MIN}:1) → 가늘고 긴 파이프 "
                "형상으로 보기 어려움(반사/이물/다른 물체 혼입 의심)."
            )

    if dbg.contrast < dbg.min_contrast:
        warnings.append(
            f"대비 {dbg.contrast:.1f} < 임계 {dbg.min_contrast:.1f} → 조명/배경 대비 부족."
        )

    if not dbg.length.edge_detected:
        warnings.append(
            "끝단 검출 실패(edge_detected=False) → meas_length_mm 없음, 강제 NG."
        )

    if (
        abs(float(item.px_to_mm_scale) - DEMO_SCALE) < 1e-9
        and abs(float(item.ref_length_mm) - DEMO_REF_MM) < 1e-9
    ):
        warnings.append(
            f"px_to_mm_scale={DEMO_SCALE}, ref_length_mm={DEMO_REF_MM} — item_master 데모 "
            "시드값과 일치합니다. 웹 자기보정(POST /master/items/{item_code}/calibrate) "
            "미실시로 임의값이 그대로 쓰이고 있을 가능성이 높습니다."
        )

    if capture_recipe is None:
        notes.append(
            "--capture-recipe 미입력: JSON으로 넘기면 af_mode/exposure_us 존재 여부로 "
            'AF/AE 고정 여부까지 진단합니다(예: --capture-recipe '
            "'{\"af_mode\":\"manual\",\"exposure_us\":8000}').",
        )
    else:
        missing = [k for k in ("af_mode", "exposure_us") if k not in capture_recipe]
        if missing:
            warnings.append(
                f"capture_recipe에 {', '.join(missing)} 없음 → AF/AE(오토포커스/자동노출) "
                "미고정 가능성 → 반복 촬영 시 배율/밝기 흔들림(반복성 저하) 의심."
            )

    return warnings, notes


# ---------------- 시각화(오버레이) ----------------

def _dashed_vline(
    canvas: np.ndarray, x: float, y0: int, y1: int, color, *, dash=6, gap=5, thickness=1
) -> None:
    xi = int(round(x))
    y = int(y0)
    end = int(y1)
    while y < end:
        y2 = min(y + dash, end)
        cv2.line(canvas, (xi, y), (xi, y2), color, thickness, cv2.LINE_AA)
        y = y2 + gap


def _dashed_hline(
    canvas: np.ndarray, y: float, x0: int, x1: int, color, *, dash=8, gap=6, thickness=1
) -> None:
    yi = int(round(y))
    x = int(x0)
    end = int(x1)
    while x < end:
        x2 = min(x + dash, end)
        cv2.line(canvas, (x, yi), (x2, yi), color, thickness, cv2.LINE_AA)
        x = x2 + gap


def _fmt(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.3f}"


def _render_profile_panel(width: int, dbg: EdgeDebugInfo) -> np.ndarray:
    """밝기 프로파일 + 그래디언트를 라인 그래프로 그린 하단 패널(cv2 전용)."""
    panel = np.full((_PANEL_H, width, 3), _PANEL_BG, dtype=np.uint8)
    profile, grad = dbg.profile, dbg.gradient
    if profile.size < 2:
        cv2.putText(
            panel, "profile unavailable (ROI too small)", (10, 30), _FONT, 0.5,
            _WHITE, 1, cv2.LINE_AA,
        )
        return panel

    top_y0, top_y1 = 8, 78
    bot_y0, bot_y1 = 92, 162
    cv2.putText(
        panel, "brightness profile (argmax/argmin marked below)",
        (min(width - 4, dbg.roi_x0 + 2), top_y0 + 10), _FONT, 0.4, _WHITE, 1, cv2.LINE_AA,
    )
    cv2.putText(
        panel, "gradient", (min(width - 4, dbg.roi_x0 + 2), bot_y0 + 10), _FONT, 0.4,
        _WHITE, 1, cv2.LINE_AA,
    )

    n = profile.size
    xs = np.clip(dbg.roi_x0 + np.arange(n), 0, width - 1)

    pmin, pmax = float(profile.min()), float(profile.max())
    prange = max(1e-6, pmax - pmin)
    py = top_y1 - (profile - pmin) / prange * (top_y1 - top_y0)
    pts = np.stack([xs, py.astype(np.int32)], axis=1).astype(np.int32)
    cv2.polylines(panel, [pts], False, (200, 200, 200), 1, cv2.LINE_AA)

    if grad.size == n and n > 0:
        gmax = max(1e-6, float(np.max(np.abs(grad))))
        mid = (bot_y0 + bot_y1) / 2.0
        gy = mid - (grad / gmax) * ((bot_y1 - bot_y0) / 2.0)
        gpts = np.stack([xs, gy.astype(np.int32)], axis=1).astype(np.int32)
        cv2.line(
            panel, (int(xs[0]), int(mid)), (int(xs[-1]), int(mid)), (90, 90, 90), 1,
            cv2.LINE_AA,
        )
        cv2.polylines(panel, [gpts], False, (0, 165, 255), 1, cv2.LINE_AA)

        if dbg.left_idx is not None:
            lx, ly = int(xs[dbg.left_idx]), int(gy[dbg.left_idx])
            cv2.circle(panel, (lx, ly), 4, _EDGE_INT_COLOR, -1, cv2.LINE_AA)
            cv2.putText(
                panel, "argmax(grad)=L", (max(0, lx - 45), max(12, ly - 8)), _FONT, 0.38,
                _EDGE_INT_COLOR, 1, cv2.LINE_AA,
            )
        if dbg.right_idx is not None:
            rx, ry = int(xs[dbg.right_idx]), int(gy[dbg.right_idx])
            cv2.circle(panel, (rx, ry), 4, _EDGE_SUB_COLOR, -1, cv2.LINE_AA)
            cv2.putText(
                panel, "argmin(grad)=R", (max(0, rx - 45), min(_PANEL_H - 4, ry + 16)),
                _FONT, 0.38, _EDGE_SUB_COLOR, 1, cv2.LINE_AA,
            )

    return panel


def render_debug_overlay(
    img_bgr: np.ndarray, dbg: EdgeDebugInfo, item: ItemMaster
) -> np.ndarray:
    """원본 위에 마스크/bbox/에지/최종값을 그리고 하단에 프로파일 패널을 붙인다."""
    canvas = img_bgr.copy()
    h, w = canvas.shape[:2]

    # (1) Otsu 마스크 반투명 오버레이.
    if dbg.mask is not None and dbg.mask.any():
        tint = canvas.copy()
        tint[dbg.mask > 0] = _MASK_COLOR
        canvas = cv2.addWeighted(tint, 0.35, canvas, 0.65, 0)

    # (2) length_roi bbox.
    if dbg.length_roi is not None:
        r = dbg.length_roi
        cv2.rectangle(
            canvas, (r.x0, r.y0), (max(r.x0, r.x1 - 1), max(r.y0, r.y1 - 1)),
            _BBOX_COLOR, thickness=2, lineType=cv2.LINE_AA,
        )

    # (3) 좌/우 끝단: 정수 위치(점선) vs 서브픽셀 보정 위치(실선).
    y0, y1 = dbg.roi_y0, dbg.roi_y1
    for idx, sub, applied, label in (
        (dbg.left_idx, dbg.left_abs, dbg.left_applied, "L"),
        (dbg.right_idx, dbg.right_abs, dbg.right_applied, "R"),
    ):
        if idx is None:
            continue
        x_int = dbg.roi_x0 + idx
        _dashed_vline(canvas, x_int, y0, y1, _EDGE_INT_COLOR, thickness=1)
        if sub is not None:
            x_sub = int(round(sub))
            cv2.line(
                canvas, (x_sub, y0), (x_sub, max(y0, y1 - 1)), _EDGE_SUB_COLOR, 2,
                cv2.LINE_AA,
            )
        tag = f"{label} sub" if applied else f"{label} int(no-corr)"
        tag_color = _EDGE_SUB_COLOR if applied else _EDGE_INT_COLOR
        cv2.putText(
            canvas, tag, (max(0, x_int - 20), max(12, y0 - 4)), _FONT, 0.42, tag_color, 1,
            cv2.LINE_AA,
        )

    # (4) 최종 산출값 헤더바.
    ok = str(dbg.length.length_verdict).upper() == "OK" and bool(dbg.length.edge_detected)
    color = _GREEN if ok else _RED
    bar_h = max(30, h // 10)
    bar = canvas.copy()
    cv2.rectangle(bar, (0, 0), (w, bar_h), color, -1)
    canvas = cv2.addWeighted(bar, 0.55, canvas, 0.45, 0)
    sym = "[OK]" if ok else "[NG]"
    if dbg.left_abs is not None and dbg.right_abs is not None:
        px_txt = f"{(dbg.right_abs - dbg.left_abs):.2f}px"
    else:
        px_txt = "--"
    hdr = f"{sym} LEN edge_detected={dbg.length.edge_detected} pixel_distance={px_txt}"
    cv2.putText(
        canvas, hdr, (10, int(bar_h * 0.7)), _FONT, max(0.5, bar_h / 48), _WHITE, 2,
        cv2.LINE_AA,
    )

    # (5) 하단 텍스트 패널(수치 요약).
    L = dbg.length
    lines = [
        f"meas {_fmt(L.meas_length_mm)}mm  ref {_fmt(L.ref_length_mm)}mm  "
        f"dev {_fmt(L.deviation_mm)}mm  scale {item.px_to_mm_scale:.6f}",
        f"contrast {dbg.contrast:.1f} / min {dbg.min_contrast:.1f}"
        + ("  [PASS]" if dbg.contrast >= dbg.min_contrast else "  [FAIL]"),
    ]
    txt_scale = max(0.45, w / 1400.0)
    line_h = int(24 * txt_scale) + 8
    panel_h = line_h * len(lines) + 16
    y_top = h - panel_h
    ov = canvas.copy()
    cv2.rectangle(ov, (0, y_top), (w, h), _BLACK, -1)
    canvas = cv2.addWeighted(ov, 0.6, canvas, 0.4, 0)
    y = y_top + 20
    for ln in lines:
        cv2.putText(canvas, ln, (10, y), _FONT, txt_scale, _WHITE, 1, cv2.LINE_AA)
        y += line_h

    panel = _render_profile_panel(w, dbg)
    return np.vstack([canvas, panel])


def render_multi_overview(
    img_bgr: np.ndarray, tubes: Sequence[TubeROI], *, requested_n: int, auto_n: int
) -> np.ndarray:
    """다중튜브 개요: 세그멘테이션 seam 경계 + 튜브별 crop 경계 시각화."""
    canvas = img_bgr.copy()
    h, w = canvas.shape[:2]
    mismatch = auto_n != requested_n

    boundary_ys = sorted({t.y0 for t in tubes} | {t.y1 for t in tubes})
    for y in boundary_ys:
        _dashed_hline(canvas, y, 0, w, _SEAM_COLOR)

    for t in tubes:
        cv2.rectangle(
            canvas, (t.x0, t.y0), (max(t.x0, t.x1 - 1), max(t.y0, t.y1 - 1)), _BBOX_COLOR,
            2, cv2.LINE_AA,
        )
        cv2.putText(
            canvas, f"#{t.index} conf={t.confidence:.2f}", (t.x0 + 4, max(14, t.y0 + 16)),
            _FONT, 0.45, _BBOX_COLOR, 1, cv2.LINE_AA,
        )

    bar_h = max(28, h // 12)
    bar = canvas.copy()
    bar_color = _RED if mismatch else _GREEN
    cv2.rectangle(bar, (0, 0), (w, bar_h), bar_color, -1)
    canvas = cv2.addWeighted(bar, 0.55, canvas, 0.45, 0)
    hdr = f"MULTI requested={requested_n} segmented={len(tubes)} auto_detected={auto_n}"
    if mismatch:
        hdr += "  ! MISMATCH"
    cv2.putText(
        canvas, hdr, (10, int(bar_h * 0.7)), _FONT, max(0.5, bar_h / 44), _WHITE, 2,
        cv2.LINE_AA,
    )
    return canvas


# ---------------- 진단 텍스트(stdout) ----------------

def format_report(
    dbg: EdgeDebugInfo,
    warnings: List[str],
    notes: List[str],
    item: ItemMaster,
    *,
    image_path: str,
    out_path: str,
) -> str:
    """한국어 진단 텍스트(결정적 — 동일 입력이면 동일 문자열)."""
    L = dbg.length
    lines: List[str] = []
    lines.append("=== AIVIS 길이 측정 디버그 진단 ===")
    lines.append(f"입력 이미지: {image_path}")
    lines.append(f"시각화 저장: {out_path}")
    lines.append("")
    lines.append("[1] 파이프 검출(ROI)")
    if dbg.length_roi is None:
        lines.append("  - 검출 실패(found=False)")
    else:
        r = dbg.length_roi
        lines.append(
            f"  - bbox: x=[{r.x0},{r.x1}) y=[{r.y0},{r.y1}) size={r.width}x{r.height}px"
        )
        area_txt = "--" if dbg.contour_area_px is None else f"{dbg.contour_area_px:.0f}px^2"
        lines.append(f"  - 컨투어 면적: {area_txt}")
    lines.append(f"  - 마스크 테두리 전경비율: {dbg.mask_border_fg_ratio * 100:.1f}%")
    lines.append("")
    lines.append("[2] 밝기 대비 vs 게이트")
    pass_txt = "PASS" if dbg.contrast >= dbg.min_contrast else "FAIL"
    lines.append(
        f"  - contrast(max-min)={dbg.contrast:.2f}  min_contrast={dbg.min_contrast:.2f}"
        f"  [{pass_txt}]"
    )
    lines.append("")
    lines.append("[3] 끝단(에지) 검출")
    lines.append(f"  - edge_detected: {L.edge_detected}")
    if dbg.left_idx is not None and dbg.left_sub_local is not None:
        lines.append(
            f"  - 좌(L): int_idx(ROI기준)={dbg.left_idx} sub={dbg.left_sub_local:.3f} "
            f"delta={dbg.left_delta:+.3f} applied={dbg.left_applied} "
            f"abs_x={dbg.left_abs:.2f}px"
        )
    if dbg.right_idx is not None and dbg.right_sub_local is not None:
        lines.append(
            f"  - 우(R): int_idx(ROI기준)={dbg.right_idx} sub={dbg.right_sub_local:.3f} "
            f"delta={dbg.right_delta:+.3f} applied={dbg.right_applied} "
            f"abs_x={dbg.right_abs:.2f}px"
        )
    if dbg.left_abs is not None and dbg.right_abs is not None:
        lines.append(
            f"  - pixel_distance(서브픽셀 기준, 참고): "
            f"{dbg.right_abs - dbg.left_abs:.3f}px"
        )
    lines.append("")
    lines.append("[4] 최종 산출값")
    lines.append(f"  - px_to_mm_scale: {item.px_to_mm_scale:.6f}")
    lines.append(
        f"  - meas_length_mm: {_fmt(L.meas_length_mm)}mm   "
        f"ref_length_mm: {_fmt(L.ref_length_mm)}mm"
    )
    lines.append(
        f"  - tol: +{item.tol_plus_mm:.3f}/-{item.tol_minus_mm:.3f}mm   "
        f"deviation_mm: {_fmt(L.deviation_mm)}mm"
    )
    lines.append(f"  - length_verdict: {L.length_verdict}   proc_time_ms: {L.proc_time_ms}")
    lines.append("")
    if warnings:
        lines.append("[경고]")
        for w_ in warnings:
            lines.append(f"  ! {w_}")
    else:
        lines.append("[경고] 없음(정적 임계 기준 특이사항 없음)")
    if notes:
        lines.append("")
        lines.append("[참고]")
        for n_ in notes:
            lines.append(f"  - {n_}")
    return "\n".join(lines)


# ---------------- CLI ----------------

def _default_out(image_path: Path, out: Optional[str], *, suffix: str = "_debug") -> Path:
    if out:
        return Path(out)
    return image_path.with_name(image_path.stem + suffix + ".jpg")


def _run_single(
    img_bgr: np.ndarray,
    item: ItemMaster,
    min_contrast: float,
    image_path: Path,
    out: Optional[str],
    capture_recipe: Optional[Dict[str, Any]],
) -> Tuple[int, str, List[str]]:
    dbg = compute_edge_debug(img_bgr, item, min_contrast)
    warnings, notes = build_diagnostics(dbg, item, capture_recipe=capture_recipe)
    out_path = _default_out(image_path, out)
    overlay = render_debug_overlay(img_bgr, dbg, item)
    cv2.imwrite(str(out_path), overlay)
    report = format_report(
        dbg, warnings, notes, item, image_path=str(image_path), out_path=str(out_path)
    )
    return 0, report, [str(out_path)]


def _run_multi(
    img_bgr: np.ndarray,
    n: int,
    item: ItemMaster,
    min_contrast: float,
    image_path: Path,
    out: Optional[str],
    capture_recipe: Optional[Dict[str, Any]],
) -> Tuple[int, str, List[str]]:
    tubes = segment_tubes(img_bgr, expected_count=n)
    auto_tubes = segment_tubes(img_bgr, expected_count=None)
    auto_n = len(auto_tubes)

    overview_path = _default_out(image_path, out)
    overview = render_multi_overview(img_bgr, tubes, requested_n=n, auto_n=auto_n)
    cv2.imwrite(str(overview_path), overview)

    report_lines = [
        "=== AIVIS 다중튜브 디버그 진단(--multi) ===",
        f"입력 이미지: {image_path}",
        f"요청 개수(N): {n}   세그멘테이션 결과: {len(tubes)}개   "
        f"자동검출(참고): {auto_n}개",
        f"개요 시각화: {overview_path}",
        "",
    ]
    if auto_n != n:
        report_lines.append(
            f"[경고] --multi {n} 지정과 자동 검출 개수({auto_n})가 다릅니다 → "
            "expected_count 설정 오류이거나 단일 파이프를 다중모드로 잘못 지정했을 "
            "가능성."
        )
        report_lines.append("")
    if not tubes:
        report_lines.append("[경고] 세그멘테이션 결과가 비어 있습니다(튜브 미검출).")

    out_paths = [str(overview_path)]
    for t in tubes:
        crop = t.crop(img_bgr)
        dbg = compute_edge_debug(crop, item, min_contrast)
        warnings, notes = build_diagnostics(dbg, item, capture_recipe=capture_recipe)
        tube_out = image_path.with_name(f"{image_path.stem}_tube{t.index}_debug.jpg")
        overlay = render_debug_overlay(crop, dbg, item)
        cv2.imwrite(str(tube_out), overlay)
        out_paths.append(str(tube_out))
        report_lines.append(f"--- Tube #{t.index} (conf={t.confidence:.2f}, bbox={t.bbox}) ---")
        report_lines.append(
            format_report(
                dbg, warnings, notes, item,
                image_path=f"{image_path}[tube{t.index}]", out_path=str(tube_out),
            )
        )
        report_lines.append("")

    return 0, "\n".join(report_lines), out_paths


def _parse_capture_recipe(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--capture-recipe JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("--capture-recipe 는 JSON 객체({...})여야 합니다")
    return data


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="debug_length",
        description=(
            "AIVIS 길이 측정 디버그 시각화 CLI — 촬영 이미지 1장으로 마스크/ROI/"
            "끝단검출/밝기프로파일/최종 산출값을 오프라인에서 진단한다."
        ),
    )
    ap.add_argument("image", help="촬영 이미지 경로(raw/ 아래 jpg/png 등)")
    ap.add_argument(
        "--ref-length-mm", type=float, default=DEMO_REF_MM, help="기준 길이(mm)"
    )
    ap.add_argument("--tol-plus-mm", type=float, default=0.5, help="허용 공차 +(mm)")
    ap.add_argument("--tol-minus-mm", type=float, default=0.5, help="허용 공차 -(mm)")
    ap.add_argument(
        "--scale", type=float, default=DEMO_SCALE, dest="px_to_mm_scale",
        help="px_to_mm_scale(품목별 보정계수)",
    )
    ap.add_argument("--min-contrast", type=float, default=20.0, help="끝단검출 대비 임계")
    ap.add_argument(
        "--multi", type=int, default=None, metavar="N",
        help="다중튜브 모드: 기대 튜브 개수(주어지면 스트립별 진단)",
    )
    ap.add_argument(
        "--capture-recipe", default=None,
        help='촬영 레시피 JSON(선택, 예: \'{"af_mode":"manual","exposure_us":8000}\')',
    )
    ap.add_argument(
        "--out", default=None,
        help="출력 파일(단일) 또는 개요파일(다중) 경로. 기본: <입력>_debug.jpg",
    )
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    image_path = Path(args.image)
    img_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img_bgr is None:
        print(f"오류: 이미지를 읽을 수 없습니다: {image_path}", file=sys.stderr)
        return 2

    capture_recipe = _parse_capture_recipe(args.capture_recipe)
    item = ItemMaster(
        item_code="DEBUG",
        item_name="debug-cli",
        ref_length_mm=args.ref_length_mm,
        tol_plus_mm=args.tol_plus_mm,
        tol_minus_mm=args.tol_minus_mm,
        px_to_mm_scale=args.px_to_mm_scale,
        capture_recipe=capture_recipe,
    )

    if args.multi:
        code, report, _paths = _run_multi(
            img_bgr, args.multi, item, args.min_contrast, image_path, args.out,
            capture_recipe,
        )
    else:
        code, report, _paths = _run_single(
            img_bgr, item, args.min_contrast, image_path, args.out, capture_recipe
        )
    print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
