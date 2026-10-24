"""현장 사진에서 튜브 질감 잘라 쓰기 — 실제 금속 겉모습을 학습 데이터에 섞는다.

붙은 다발 사진(트레이 등)은 다발 분석(multi/dense.py)으로 튜브마다 중심선을 따라 편 띠를,
떨어진 튜브 사진은 일반 분할 상자를 쓴다. 띠는 축 정렬이므로 그대로 TubePatch 가 된다.

**사진과 잘라낸 질감은 저장소에 올리지 않는다**(공개 저장소). 현장 파이에서 자기 사진 폴더로
다시 만들 수 있게 경로만 받는다: `python -m vision.tools.build_training_data --real-dir <사진폴더>`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from .tube import TubePatch


@dataclass
class RealTube:
    """현장 사진 한 튜브의 띠(축 정렬, 끝 바깥 여백 포함)와 튜브 구간."""

    rgb: np.ndarray        # BGR uint8, 높이 = 튜브 폭
    u0: int                # 튜브 시작 열
    u1: int                # 튜브 끝 열
    source: str            # 원본 파일 이름


def _canon(img: np.ndarray):
    from ..multi.batch import _orient
    from ..pipeline import InspectionPipeline

    pipe = InspectionPipeline()
    prep = pipe.prepare_frame(img, axis="horizontal")
    axis, frame, prep, area, _rot = _orient(prep.frame, prep, None)
    canon = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE) if axis == "vertical" else frame
    return canon


def extract_tubes(path: str, *, frac: float = 0.92, max_tubes: int = 64) -> List[RealTube]:
    """사진 한 장 → 튜브 띠 목록. 붙은 다발만(다발 분석이 3개 이상 찾을 때)."""
    from ..multi.dense import find_dense_bundle

    img = cv2.imread(path)
    if img is None:
        return []
    canon = _canon(img)
    bundle = find_dense_bundle(cv2.cvtColor(canon, cv2.COLOR_BGR2GRAY))
    if bundle is None or bundle.uncertain:
        return []
    out: List[RealTube] = []
    for t in bundle.tubes[:max_tubes]:
        if t.end_adjusted:
            continue           # 끝이 이웃으로 맞춘 값 — 질감으로 쓰기엔 끝 위치가 불확실
        L = (t.x1 - t.x0) * float(np.hypot(1.0, bundle.slope(0.5 * (t.u0 + t.u1))))
        margin = max(6, int(0.04 * L))
        crop, mask, length, _c, _d = bundle.ribbon(canon, t, margin=margin, frac=frac)
        cols = np.flatnonzero(mask.max(axis=0) > 0)
        if cols.size < 20:
            continue
        out.append(RealTube(rgb=crop, u0=int(cols[0]), u1=int(cols[-1]) + 1, source=Path(path).name))
    return out


def load_bank(real_dir: str) -> List[RealTube]:
    """폴더의 사진 전부에서 튜브 띠를 모은다(jpg/png)."""
    bank: List[RealTube] = []
    for p in sorted(Path(real_dir).glob("*")):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            bank.extend(extract_tubes(str(p)))
    return bank


def patch_from_real(rt: RealTube, length: float, diameter: float, rng: np.random.Generator,
                    *, pad: int = 3, flip: Optional[bool] = None) -> TubePatch:
    """현장 띠를 원하는 길이·지름으로 늘려 TubePatch 로(실루엣은 직사각형 + 부분 화소)."""
    body = rt.rgb[:, rt.u0:rt.u1].astype(np.float32)
    if flip if flip is not None else rng.random() < 0.5:
        body = body[:, ::-1]
    if rng.random() < 0.5:
        body = body[::-1, :]
    rows = int(np.ceil(diameter)) + 2
    cols = int(np.ceil(length)) + 2 * pad
    L = int(round(length))
    body = cv2.resize(body, (max(2, L), rows), interpolation=cv2.INTER_AREA if body.shape[1] > L else cv2.INTER_LINEAR)
    gain = float(rng.uniform(0.85, 1.15))
    rgb = np.zeros((rows, cols, 3), np.float32)
    rgb[:, pad:pad + body.shape[1]] = body * gain
    # 끝 바깥은 가장 가까운 열로 채운다(합성 때 알파로 가려진다)
    rgb[:, :pad] = rgb[:, pad:pad + 1]
    rgb[:, pad + body.shape[1]:] = rgb[:, pad + body.shape[1] - 1:pad + body.shape[1]]
    y = np.arange(rows, dtype=np.float32)[:, None] - (rows - 1) / 2.0
    x = np.arange(cols, dtype=np.float32)[None, :] - pad
    cov_v = np.clip(diameter / 2.0 - np.abs(y) + 0.5, 0, 1)
    cov_u = np.clip(np.minimum(x + 0.5, length - x + 0.5), 0, 1)
    vn = np.broadcast_to(y / (diameter / 2.0), (rows, cols)).astype(np.float32)
    un = np.broadcast_to(x / max(length, 1.0), (rows, cols)).astype(np.float32)
    return TubePatch(rgb=np.clip(rgb, 0, 255), alpha=(cov_v * cov_u).astype(np.float32), un=un, vn=vn,
                     length=float(length), diameter=float(diameter), pad=pad, look=None)


__all__ = ["RealTube", "extract_tubes", "load_bank", "patch_from_real"]
