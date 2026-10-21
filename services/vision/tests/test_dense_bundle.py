"""붙어 누운 다발(밝은 트레이 위) — 개수·튜브별 길이 (2026-10-21 현장 사진 대응, dense.py).

현장 사진(밝은 회색 트레이에 튜브 19개를 붙여 눕힘, 휴대폰으로 여러 각도)에서 종전 분할은 0~5개를
찾았다. 여기서는 그 조건을 합성으로 재현한다(현장 사진은 공개 저장소에 넣지 않는다).

- 트레이: 밝은 바닥 + 어두운 테두리(튜브와 나란한 벽 — 바깥 튜브와 헷갈리는 자리).
- 튜브: 서로 닿은 원통(가장자리 어둡게 → 사이에 어두운 틈), 반사 띠, 끝을 조금씩 엇갈리게.
- 원근: 사다리꼴 변환(가까운 쪽 튜브가 굵고 길어 보인다), 회전, 세로.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from aivis_types import ItemMaster  # noqa: E402

from vision.multi import inspect_batch, tube_to_inspection  # noqa: E402
from vision.multi.batch import BatchMeta  # noqa: E402
from vision.multi.dense import DenseBundle, axis_angle, find_dense_bundle  # noqa: E402

SCALE = 0.25
N = 19
PITCH = 34.0       # 튜브 지름(px) — 닿아 있으므로 간격 = 지름
LENGTH = 700.0     # px


def _item(expected: int = N) -> ItemMaster:
    return ItemMaster(
        item_code="HP12", item_name="t", ref_length_mm=LENGTH * SCALE,
        tol_plus_mm=3.0, tol_minus_mm=3.0, px_to_mm_scale=SCALE,
        oil_threshold=0.30, discolor_threshold=0.20, scratch_threshold=0.15,
        expected_count=expected,
    )


def _tray_scene(n: int = N, *, size=(1100, 1300), seed: int = 3):
    """밝은 트레이 위 붙은 다발(가로). 반환: (BGR 영상, 튜브별 (x0, x1) 참값)."""
    rng = np.random.default_rng(seed)
    h, w = size
    img = np.full((h, w), 205.0) + cv2.GaussianBlur(rng.normal(0, 3.0, (h, w)), (0, 0), 1.0)
    # 트레이 테두리(튜브와 나란한 벽 포함): 어두운 띠
    top = h / 2 - n * PITCH / 2
    for y0, y1 in ((top - 70, top - 50), (top + n * PITCH + 50, top + n * PITCH + 70)):
        img[int(y0):int(y1), 120:w - 120] = 150.0
    img[int(top - 70):int(top + n * PITCH + 70), 120:140] = 150.0
    img[int(top - 70):int(top + n * PITCH + 70), w - 140:w - 120] = 150.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    truth = []
    for i in range(n):
        cy = top + (i + 0.5) * PITCH
        shift = rng.uniform(-12, 12)
        x0 = w / 2 - LENGTH / 2 + shift
        x1 = x0 + LENGTH
        v = ys - cy
        cov = np.clip(PITCH / 2 - np.abs(v) + 0.5, 0, 1) * np.clip(np.minimum(xs - x0, x1 - xs) + 0.5, 0, 1)
        vn = v / (PITCH / 2)
        tube = 120.0 + 115.0 * np.exp(-((vn + 0.25) / 0.18) ** 2) - 70.0 * (np.abs(vn) > 0.72)
        img = img * (1 - cov) + tube * cov
        truth.append((x0, x1))
    g = np.clip(img, 0, 255).astype(np.uint8)
    return np.dstack([g, g, g]), truth


def _keystone(img: np.ndarray, k: float = 0.12) -> np.ndarray:
    """사다리꼴(원근) — 위쪽이 좁다."""
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[k * w, 0], [w - k * w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)


def _rotate(img: np.ndarray, deg: float) -> np.ndarray:
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    c, s = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * s + w * c), int(h * c + w * s)
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(img, M, (nw, nh), borderMode=cv2.BORDER_REPLICATE)


def test_touching_bundle_counted_and_measured():
    """종전 분할은 덩어리를 못 나눴다. 다발 분석: 19개, 튜브마다 길이(±1mm)."""
    frame, truth = _tray_scene()
    b = inspect_batch(frame, _item(), axis="auto", expected_count=N, stage="CUT_LENGTH")
    assert b.dense is True
    assert b.count_detected == N and not b.count_mismatch and not b.count_uncertain
    got = [t.length_mm for t in b.tubes]
    assert sum(1 for x in got if x is not None) >= N - 1, got
    for x in got:
        if x is not None:
            assert abs(x - LENGTH * SCALE) <= 1.0, got
    # 튜브는 위에서 아래로 번호, 모서리 네 점·측정선이 결과 사진 좌표로 실린다.
    assert all(t.quad is not None and len(t.quad) == 4 for t in b.tubes)
    cy = [0.5 * (t.quad[0][1] + t.quad[3][1]) for t in b.tubes]
    assert cy == sorted(cy)


def test_bundle_with_perspective():
    """사다리꼴 원근(가까운 쪽이 굵다) — 소실점 모형으로 펴서 센다."""
    frame, _ = _tray_scene()
    b = inspect_batch(_keystone(frame), _item(), axis="auto", expected_count=N, stage="CUT_LENGTH")
    assert b.dense is True
    assert b.count_detected == N
    assert sum(1 for t in b.tubes if t.length_mm is not None) >= N - 2


def test_bundle_rotated():
    """30° 기운 다발 — 모서리 방향으로 방향을 잡아 돌린 뒤 센다. 라벨링용 tube_bbox 는 비운다."""
    frame, _ = _tray_scene()
    b = inspect_batch(_rotate(frame, 30.0), _item(), axis="auto", expected_count=N, stage="CUT_LENGTH")
    assert b.dense is True
    assert b.count_detected == N
    assert abs(abs(b.rotation_deg) - 30.0) <= 3.0
    from datetime import datetime, timezone
    meta = BatchMeta(lot="L", item_code="HP12", cam_id="CAM01",
                     inspected_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
    assert tube_to_inspection(b.tubes[0], batch_meta=meta).tube_bbox is None


def test_bundle_vertical():
    frame, _ = _tray_scene()
    b = inspect_batch(cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE), _item(), axis="auto",
                      expected_count=N, stage="CUT_LENGTH")
    assert b.axis == "vertical"
    assert b.dense is True and b.count_detected == N


def test_count_mismatch_is_reported():
    """기대 개수와 다르면 그대로 불일치 — 다발 분석은 기대 개수에 맞추지 않는다."""
    frame, _ = _tray_scene(n=17, seed=5)
    b = inspect_batch(frame, _item(), axis="auto", expected_count=N, stage="CUT_LENGTH")
    assert b.dense is True
    assert b.count_detected == 17 and b.count_mismatch


def test_find_dense_bundle_directly():
    frame, truth = _tray_scene()
    bundle = find_dense_bundle(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    assert isinstance(bundle, DenseBundle)
    assert bundle.count == N
    assert abs(bundle.pitch - PITCH) <= 2.0
    assert bundle.margin >= 0.12


def test_axis_angle_on_stripes():
    frame, _ = _tray_scene()
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    assert abs(axis_angle(g)) <= 2.0
    a = axis_angle(cv2.cvtColor(_rotate(frame, 30.0), cv2.COLOR_BGR2GRAY))
    # 반시계 30° 회전 → 축은 화면에서 오른쪽 위로(영상 좌표 각 −30°)
    assert abs(a - (-30.0)) <= 2.0
    assert axis_angle(np.full((100, 100), 128, np.uint8)) is None


def test_separated_tubes_keep_blob_path():
    """떨어져 놓인 튜브(밝은 바닥)는 종전 분할 그대로 — 다발 분석은 쓰지 않는다."""
    from test_light_background import _rows, _scene

    frame = _scene([0] * 4, _rows())
    b = inspect_batch(frame, _item(4), axis="auto", expected_count=4, stage="CUT_LENGTH")
    assert b.dense is False and b.count_detected == 4


def test_dark_background_not_dense():
    """어두운 배경(현장 권장)은 종전 경로 — 다발 분석을 거치지 않는다."""
    from vision.tools.gen_synthetic import make_multi_image

    img, _ = make_multi_image(6)
    b = inspect_batch(img, _item(6), axis="auto", expected_count=6, stage="CUT_LENGTH")
    assert b.dense is False


def test_uncertain_property():
    b = DenseBundle(tubes=[], pitch=30.0, a=0.0, b=0.0, xc=0.0, yc=0.0, box=(0, 0, 1, 1),
                    count_check=0, margin=0.05)
    assert b.uncertain is True
    b2 = DenseBundle(tubes=[], pitch=30.0, a=0.0, b=0.0, xc=0.0, yc=0.0, box=(0, 0, 1, 1),
                     count_check=0, margin=0.3)
    assert b2.uncertain is False


def test_ribbon_is_isometric():
    """띠 영상은 회전만 한 1:1 — 띠에서 잰 px 길이 = 원본 px 길이."""
    frame, truth = _tray_scene()
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bundle = find_dense_bundle(g)
    t = bundle.tubes[N // 2]
    crop, mask, length, _cen, d = bundle.ribbon(frame, t, margin=40)
    assert crop.shape[1] == int(round(length)) + 80
    assert abs(float(np.hypot(*d)) - 1.0) < 1e-9
    assert mask[:, 40 + 5].all() and not mask[:, 10].any()
    assert math.isfinite(length)
