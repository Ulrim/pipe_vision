"""배치(다중 튜브) 워커 연동 + 매핑 + 오버레이 폴리시 테스트.

검증 대상(vision-ai 담당):
  - tube_to_inspection 매핑: tube_index 0..N-1, 공유 이미지경로/검사시각,
    튜브별 판정값이 단일 to_inspection_result 와 정합.
  - 워커 배치 모드: item.expected_count>1 → 튜브 N개를 각각 POST(이미지 1회 저장).
  - 워커 단일 모드: expected_count=1 → 현행 동작 유지(1건 POST, tube_index=0).
  - render_batch_overlay: 반투명 헤더가 #1 튜브 박스를 가리지 않음.

모든 입력은 합성(make_multi_image)이며 AIVIS_CAMERA 무관 결정적. 네트워크는
httpx.MockTransport 로 대체한다(실 카메라/서버 없이 전 경로 검증 — §6.1).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import GrabResult  # noqa: E402
from vision.imaging import render_batch_overlay  # noqa: E402
from vision.imaging.save import _GREEN  # noqa: E402
from vision.multi import BatchMeta, inspect_batch, tube_to_inspection  # noqa: E402
from vision.tools.gen_synthetic import make_multi_image  # noqa: E402
from vision.worker.client import ApiClient  # noqa: E402
from vision.worker.config import WorkerConfig  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402


# ---------------- 매핑 헬퍼(tube_to_inspection) ----------------

def test_tube_to_inspection_maps_index_and_shared_meta(item):
    img, _ = make_multi_image(6, defects={2: "SCR", 4: "LEN_PLUS"})
    batch = inspect_batch(img, item, expected_count=6)
    ts = datetime(2026, 6, 9, 14, 12, 33, 456789, tzinfo=timezone.utc)
    meta = BatchMeta(
        lot="LOTB",
        item_code="HP12",
        cam_id="CAM1",
        inspected_at=ts,
        ref_length_mm=float(item.ref_length_mm),
        raw_image_path="raw/LOTB_HP12_20260609141233456_NG.jpg",
        result_image_path="result/LOTB_HP12_20260609141233456_NG.jpg",
    )
    results = [tube_to_inspection(t, batch_meta=meta) for t in batch.tubes]

    # tube_index 0..N-1 (roi.index 1..N → 0-base).
    assert [r.tube_index for r in results] == list(range(len(batch.tubes)))
    # 공유 메타: 검사시각/이미지경로/lot 은 모든 행이 동일.
    assert {r.inspected_at for r in results} == {ts}
    assert {r.raw_image_path for r in results} == {meta.raw_image_path}
    assert {r.result_image_path for r in results} == {meta.result_image_path}
    assert {r.lot for r in results} == {"LOTB"}
    # 튜브별 값은 TubeResult 를 그대로 반영(길이/판정/proc/score).
    for tube, r in zip(batch.tubes, results):
        assert r.meas_length_mm == tube.length_mm
        assert r.deviation_mm == tube.deviation_mm
        assert str(r.final_verdict) == tube.final_verdict
        assert r.proc_time_ms == tube.proc_time_ms
        assert r.review_flag == tube.review_flag
        assert [str(c) for c in r.defect_codes] == list(tube.defect_codes)
        assert r.ref_length_mm == float(item.ref_length_mm)


def test_tube_to_inspection_defect_tube_flags_ng(item):
    img, _ = make_multi_image(4, defects={1: "SCR"})
    batch = inspect_batch(img, item, expected_count=4)
    meta = BatchMeta(
        lot="L", item_code="HP12", cam_id="CAM1",
        inspected_at=datetime.now(timezone.utc),
        ref_length_mm=float(item.ref_length_mm),
    )
    results = [tube_to_inspection(t, batch_meta=meta) for t in batch.tubes]
    # 결함 주입 튜브(#2 = index 2, tube_index 1)는 NG.
    ng = [r for r in results if str(r.final_verdict) == "NG"]
    assert ng, "결함 튜브가 NG 로 매핑되어야 한다"
    assert any(r.tube_index == 1 for r in ng)


# ---------------- 오버레이 폴리시: #1 튜브 미가림 ----------------

def test_batch_overlay_header_does_not_cover_first_tube(item):
    # #1(index1) 은 OK(초록), 하위 튜브에 결함 → 배치 NG(헤더 빨강)로 대비 확보.
    img, _ = make_multi_image(6, defects={2: "SCR"})
    batch = inspect_batch(img, item, expected_count=6)
    ov = render_batch_overlay(img, batch)
    assert ov.shape == img.shape  # 캔버스 크기 불변(기존 계약 유지).

    h, w = img.shape[:2]
    bar_h = max(30, h // 12)
    t1 = next(t for t in batch.tubes if t.index == 1)
    assert str(t1.final_verdict).upper() == "OK"
    x0, _y0, x1, _y1 = t1.bbox

    green_exact = np.all(ov == np.array(_GREEN), axis=2)
    # 헤더 밴드(상단 bar_h) 안, #1 튜브 열 구간에 초록(박스) 픽셀이 존재해야 한다.
    # 헤더가 반투명이라 밴드 배경은 순수 _GREEN 이 아니므로, 여기의 초록은 헤더
    # 위에 그려진 #1 박스뿐이다 → #1 이 가려지지 않았음을 증명한다.
    assert green_exact[0:bar_h, x0:x1].any()


# ---------------- 워커 연동(httpx MockTransport) ----------------

class _BatchBackend:
    """/health, /master/items, /auth/login, /inspection 계약 stub.

    master 는 expected_count 를 실어 배치/단일 분기를 제어한다. POST /inspection
    본문을 posted 로 수집한다(자연키 tube_index 포함).
    """

    def __init__(self, *, item_code="HP12", expected_count=1) -> None:
        self.item_code = item_code
        self.expected_count = expected_count
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "db": "up"})
        if path == "/auth/login":
            return httpx.Response(
                200, json={"access_token": "T", "role": "admin", "username": "admin"}
            )
        if path == f"/master/items/{self.item_code}":
            return httpx.Response(
                200,
                json={
                    "item_code": self.item_code,
                    "item_name": "Header Pipe 12",
                    "ref_length_mm": 125.0,
                    "tol_plus_mm": 3.0,
                    "tol_minus_mm": 3.0,
                    "px_to_mm_scale": 0.25,
                    "oil_threshold": 0.30,
                    "discolor_threshold": 0.20,
                    "scratch_threshold": 0.15,
                    "expected_count": self.expected_count,
                    "version": 1,
                },
            )
        if path == "/inspection" and request.method == "POST":
            self.posted.append(json.loads(request.content))
            return httpx.Response(201, json={"status": "stored", "id": len(self.posted)})
        return httpx.Response(404, json={"detail": f"no route {path}"})


class _StubAcq:
    """AcquisitionService 대체 — 고정 다중 튜브 프레임을 취득."""

    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame

    def grab_with_retry(self) -> GrabResult:
        return GrabResult(frame=self._frame, attempts=1, proc_time_ms=1)


def _client(backend: _BatchBackend) -> ApiClient:
    return ApiClient("", transport=httpx.MockTransport(backend.handler))


def _cfg(tmp_path: Path, **overrides) -> WorkerConfig:
    base = dict(
        camera_mode="sim",
        dataset_dir=None,
        api_url="",
        item_code="HP12",
        cam_id="CAM1",
        lot="LOTB",
        interval_ms=0,
        api_wait_timeout_s=5,
        item_wait_timeout_s=5,
        ready_file=str(tmp_path / "vision_ready"),
        max_iterations=1,
        images_dir=str(tmp_path / "images"),
    )
    base.update(overrides)
    return WorkerConfig(**base)


def test_worker_batch_posts_per_tube(tmp_path):
    n = 6
    backend = _BatchBackend(expected_count=n)
    worker = Worker(_cfg(tmp_path), client=_client(backend))
    assert worker.startup() is True
    assert int(worker.item.expected_count) == n
    # 합성 데이터셋은 단일 튜브라 배치 프레임을 직접 주입한다.
    img, _ = make_multi_image(n, defects={2: "SCR", 4: "LEN_PLUS"})
    worker.acq = _StubAcq(img)

    worker.run_once()

    # 튜브 N개 각각 1건씩 POST.
    assert len(backend.posted) == n
    assert sorted(b["tube_index"] for b in backend.posted) == list(range(n))
    # 배치 이미지 1회 저장 → 모든 행이 동일 raw/result 경로 + 동일 검사시각 공유.
    assert len({b["raw_image_path"] for b in backend.posted}) == 1
    assert len({b["result_image_path"] for b in backend.posted}) == 1
    assert len({b["inspected_at"] for b in backend.posted}) == 1
    # 결함 튜브가 NG 로 반영(튜브별 판정 보존).
    assert any(b["final_verdict"] == "NG" for b in backend.posted)
    # 저장 파일도 배치당 1쌍(raw 1 + result 1).
    raws = list((tmp_path / "images" / "raw").glob("*.jpg"))
    results = list((tmp_path / "images" / "result").glob("*.jpg"))
    assert len(raws) == 1 and len(results) == 1
    worker.shutdown()


def test_worker_single_mode_unchanged(tmp_path):
    backend = _BatchBackend(expected_count=1)
    worker = Worker(_cfg(tmp_path), client=_client(backend))
    assert worker.startup() is True
    ok = worker.run_once()
    assert ok is True
    # 단일 모드: 정확히 1건, tube_index 기본 0.
    assert len(backend.posted) == 1
    assert backend.posted[0]["tube_index"] == 0
    worker.shutdown()
