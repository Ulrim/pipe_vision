"""밝은·무늬 바닥 위 튜브 — 찾기·세기·길이 (2026-10-21 현장 사진 대응).

현장 사진(밝은 회색 얼룩무늬 바닥 위 알루미늄 튜브 4개)에서 종전 분할은 바닥을 제품으로 잡아
'하나도 못 찾았다'. 여기서는 그 조건을 합성으로 재현한다(현장 사진은 공개 저장소에 넣지 않는다).

- 바닥: 밝은 얼룩무늬 + 한쪽이 그늘진 조명 기울기 + 타일 이음매(가는 선).
- 튜브: 어두운 금속 + 축 방향 반사 띠 + 어두운 가장자리 + 표면 무늬(끝단보다 날카로운 밝기 변화).
- 끝단은 해석적으로 부분 화소까지 그려 길이 참값을 안다.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import ItemMaster  # noqa: E402

from vision.length.measure import _find_edges_crossing  # noqa: E402
from vision.multi import inspect_batch, tube_to_inspection  # noqa: E402
from vision.multi.batch import BatchMeta  # noqa: E402
from vision.multi.segment import segment_tubes  # noqa: E402
from vision.pipeline import _background_contrast  # noqa: E402
from vision.preprocess.foreground import light_background  # noqa: E402

SCALE = 0.25          # mm/px
TUBE_LEN = 1000.0     # px → 250mm
TUBE_W = 44.0


def _item(expected: int = 4) -> ItemMaster:
    return ItemMaster(
        item_code="HP12", item_name="t", ref_length_mm=TUBE_LEN * SCALE,
        tol_plus_mm=3.0, tol_minus_mm=3.0, px_to_mm_scale=SCALE,
        oil_threshold=0.30, discolor_threshold=0.20, scratch_threshold=0.15,
        expected_count=expected,
    )


def _floor(h: int, w: int, seed: int = 7) -> np.ndarray:
    """밝은 얼룩무늬 바닥(왼쪽이 그늘) + 가로·세로 타일 이음매."""
    import cv2

    rng = np.random.default_rng(seed)
    base = np.linspace(135.0, 200.0, w)[None, :].repeat(h, axis=0)
    noise = cv2.GaussianBlur(rng.normal(0.0, 14.0, (h, w)), (0, 0), 1.3)
    img = base + noise
    specks = rng.random((h, w)) < 0.004
    img[specks] -= 60.0
    img[:, int(0.81 * w): int(0.81 * w) + 2] -= 35.0     # 타일 이음매(세로)
    img[int(0.88 * h): int(0.88 * h) + 2, :] -= 35.0     # 타일 이음매(가로)
    return img


def _draw_tube(img: np.ndarray, cx: float, cy: float, angle_deg: float,
               length: float = TUBE_LEN, width: float = TUBE_W, seed: int = 0) -> None:
    """금속 튜브 하나를 해석적으로 그린다(끝단·가장자리 부분 화소). angle: 가로 0, 반시계 +."""
    h, w = img.shape
    t = math.radians(angle_deg)
    ux, uy = math.cos(t), -math.sin(t)        # 축(영상 y 는 아래로)
    vx, vy = -uy, ux
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    dx, dy = xs - cx, ys - cy
    u = dx * ux + dy * uy
    v = dx * vx + dy * vy
    cov = (np.clip(length / 2 - np.abs(u) + 0.5, 0, 1)
           * np.clip(width / 2 - np.abs(v) + 0.5, 0, 1))
    if not cov.any():
        return
    vn = v / (width / 2)
    tube = 112.0 + 110.0 * np.exp(-((vn + 0.25) / 0.16) ** 2) - 45.0 * (np.abs(vn) > 0.78)
    # 표면 무늬 — 끝단보다 날카로운 밝기 변화(글자·긁힘). 끝단에서 10% 안쪽에만.
    rng = np.random.default_rng(seed)
    for _ in range(6):
        pu = rng.uniform(-0.38, 0.38) * length
        pv = rng.uniform(-0.3, 0.3) * width
        mark = (np.abs(u - pu) < 7) & (np.abs(v - pv) < 5)
        tube = np.where(mark, rng.choice([25.0, 250.0]), tube)
    img[:] = img * (1 - cov) + tube * cov


def _scene(angles, centers, *, size=(1300, 1700), seed=7) -> np.ndarray:
    img = _floor(*size, seed=seed)
    for i, (a, (cx, cy)) in enumerate(zip(angles, centers)):
        _draw_tube(img, cx, cy, a, seed=100 + i)
    g = np.clip(img, 0, 255).astype(np.uint8)
    return np.dstack([g, g, g])


def _rows(n=4, gap=150.0, cx=850.0, cy0=420.0):
    return [(cx, cy0 + i * gap) for i in range(n)]


def _lengths(batch) -> list:
    return [t.length_mm for t in batch.tubes]


def test_light_floor_detected_as_light():
    assert light_background(_scene([0] * 4, _rows())[:, :, 0]) is True


def test_flat_frame_is_not_light():
    """빈 장면(평탄)은 판단하지 않는다 — 종전 경로 그대로."""
    assert light_background(np.full((200, 300), 210, np.uint8)) is False


def test_separated_tubes_found_and_measured():
    """종전에는 바닥 전체를 제품으로 잡아 띠 4개로 잘랐다. 이제 튜브 4개 · 길이 250mm(±0.5mm)."""
    frame = _scene([0] * 4, _rows())
    b = inspect_batch(frame, _item(), axis="auto", expected_count=4, stage="CUT_LENGTH")
    assert b.axis == "horizontal"
    assert b.count_detected == 4 and not b.count_mismatch
    for t in b.tubes:
        assert t.length_mm is not None
        assert abs(t.length_mm - TUBE_LEN * SCALE) <= 0.5, _lengths(b)
    # 상자는 실제 튜브 위에 — 세로 중심이 그린 위치와 맞는다.
    centers = sorted((t.bbox[1] + t.bbox[3]) / 2 for t in b.tubes)
    for got, (_x, want) in zip(centers, _rows()):
        assert abs(got - want) <= 6


def test_vertical_tubes_auto_axis():
    """세로로 놓인 튜브 — 방향은 영상에서 정한다(레시피 기본 auto)."""
    centers = [(520 + i * 170.0, 650.0) for i in range(4)]
    frame = _scene([90] * 4, centers, size=(1300, 1300))
    b = inspect_batch(frame, _item(), axis="auto", expected_count=4, stage="CUT_LENGTH")
    assert b.axis == "vertical"
    assert b.count_detected == 4
    for t in b.tubes:
        assert t.length_mm is not None and abs(t.length_mm - TUBE_LEN * SCALE) <= 0.5, _lengths(b)


def test_diagonal_tubes_deskewed():
    """30° 기운 다발 — 프레임을 돌려 세운 뒤 잰다. 돌린 영상 좌표라 tube_bbox 는 비운다."""
    a = 30.0
    t = math.radians(a)
    nx, ny = math.sin(t), math.cos(t)      # 축에 수직(영상 좌표)
    centers = [(850 + (i - 1.5) * 150 * nx, 650 + (i - 1.5) * 150 * ny) for i in range(4)]
    frame = _scene([a] * 4, centers)
    b = inspect_batch(frame, _item(), axis="auto", expected_count=4, stage="CUT_LENGTH")
    assert abs(abs(b.rotation_deg) - a) <= 2.0
    assert b.view is not None
    assert b.count_detected == 4
    for tube in b.tubes:
        assert tube.rotated is True
        assert tube.length_mm is not None and abs(tube.length_mm - TUBE_LEN * SCALE) <= 1.0, _lengths(b)
    from datetime import datetime, timezone
    meta = BatchMeta(lot="L", item_code="HP12", cam_id="CAM01",
                     inspected_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
    row = tube_to_inspection(b.tubes[0], batch_meta=meta)
    assert row.tube_bbox is None


def test_small_tilt_compensated():
    """기울기 5°(프레임은 돌리지 않는 범위) — 수평 투영(L·cos5° = 249.0mm)이 아니라 참 길이를 잰다."""
    frame = _scene([5.0] * 4, _rows(gap=170.0, cy0=380.0))
    b = inspect_batch(frame, _item(), axis="auto", expected_count=4, stage="CUT_LENGTH")
    assert b.rotation_deg == 0.0
    assert b.count_detected == 4
    for t in b.tubes:
        assert t.length_mm is not None and abs(t.length_mm - TUBE_LEN * SCALE) <= 0.5, _lengths(b)


def test_touching_bundle_counted():
    """붙어 누운 다발 — 한 덩어리를 이음매로 나눠 4개. 바깥 가장자리 띠는 튜브로 세지 않는다."""
    frame = _scene([0] * 4, _rows(gap=TUBE_W))
    rois = segment_tubes(frame, axis="auto", expected_count=None)
    assert len(rois) == 4


def test_crossing_ignores_interior_marks():
    """표면 무늬(끝단보다 강한 변화)가 있어도 끝단은 바깥에서 들어오는 첫 50% 교차."""
    prof = np.zeros(400, np.float32)
    prof[50:350] = 60.0
    prof[120:124] = 220.0      # 날카로운 무늬
    prof[250:253] = 0.0
    lo, hi = _find_edges_crossing(prof, 20.0)
    assert abs(lo - 49.5) <= 1.0 and abs(hi - 349.5) <= 1.0
    # 마스크 끝 힌트가 약간 어긋나도 같은 끝단.
    lo2, hi2 = _find_edges_crossing(prof, 20.0, hint=(56.0, 342.0))
    assert abs(lo2 - 49.5) <= 1.0 and abs(hi2 - 349.5) <= 1.0


def test_crossing_with_weak_end_uses_local_level():
    """한쪽 끝 구간이 바닥과 비슷하면(반사 띠가 끊긴 곳) 전체 중앙값 기준은 거기서 먼저 떨어진다.
    마스크 힌트를 주면 끝단마다 그 안쪽 수준으로 잰다."""
    prof = np.zeros(1000, np.float32)
    prof[40:960] = 70.0
    prof[700:960] = 22.0       # 약한 끝 구간
    lo, hi = _find_edges_crossing(prof, 20.0, hint=(41.0, 958.0))
    assert abs(lo - 39.5) <= 1.0 and abs(hi - 959.5) <= 1.0


def test_background_contrast_follows_shading():
    """한쪽이 그늘진 바닥 — 배경을 양 끝 사이의 직선으로 잡아 튜브 밖은 0 근처."""
    row = np.linspace(126.0, 198.0, 600)
    row[100:500] = 115.0
    c = _background_contrast(np.tile(row, (10, 1)).astype(np.uint8), 8)
    assert c[:, :90].max() <= 3 and c[:, 510:].max() <= 3
    assert c[:, 450:500].mean() >= 60      # 밝은 쪽 끝단도 뚜렷하다(종전: 중간값과 비슷해 사라짐)


def test_dark_background_path_unchanged():
    """어두운 배경(현장 권장)은 종전 경로 — light 판정이 아니다."""
    from vision.tools.gen_synthetic import make_multi_image

    img, _ = make_multi_image(4)
    import cv2

    assert light_background(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)) is False


@pytest.mark.parametrize("axis", ["horizontal"])
def test_explicit_axis_still_works_on_light_floor(axis):
    frame = _scene([0] * 4, _rows())
    b = inspect_batch(frame, _item(), axis=axis, expected_count=4, stage="CUT_LENGTH")
    assert b.count_detected == 4
    assert all(t.length_mm is not None for t in b.tubes)
