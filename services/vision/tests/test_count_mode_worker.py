"""개수 확인 모드(CRATE_COUNT) 워커 경로.

길이도 표면도 보지 않고 단면 개수만 센다. 결과는 행 1건(tube_index=0),
검출≠기준이면 NG+COUNT, 하트비트에 stage 와 detected/expected 가 실린다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import GrabResult  # noqa: E402
from vision.imaging import render_count_overlay  # noqa: E402
from vision.multi.bundle import count_bundle  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402

# 같은 폴더의 워커 테스트 스텁을 재사용한다. 'tests' 는 패키지로 import 되지
# 않는 실행 경로가 있어(services/vision 에서 pytest), 파일 경로로 직접 로드한다.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_test_worker_stubs", Path(__file__).with_name("test_worker.py")
)
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)
FakeBackend, _cfg, _client = _tw.FakeBackend, _tw._cfg, _tw._client


def crate_image(n_cols: int, n_rows: int, *, pitch: int = 60, r_bore: int = 14) -> np.ndarray:
    """밝은 단면(금속) 위에 어두운 보어가 격자로 — 크레이트 위에서 본 모습."""
    h, w = n_rows * pitch + 80, n_cols * pitch + 80
    img = np.full((h, w, 3), 190, np.uint8)
    for i in range(n_rows):
        for j in range(n_cols):
            cx, cy = 40 + j * pitch + pitch // 2, 40 + i * pitch + pitch // 2
            cv2.circle(img, (cx, cy), r_bore + 10, (215, 215, 215), -1)  # 벽
            cv2.circle(img, (cx, cy), r_bore, (25, 25, 25), -1)          # 보어
    return img


class _FrameAcq:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame

    def grab_with_retry(self) -> GrabResult:
        return GrabResult(frame=self._frame, attempts=1, proc_time_ms=1, error=None)


def test_synthetic_crate_counts_itself():
    """합성 입력이 검출기와 맞는지 먼저 — 이게 안 되면 아래가 무의미하다."""
    img = crate_image(5, 4)
    assert count_bundle(img).count == 20


def _count_worker(tmp_path, *, expected: int, frame: np.ndarray):
    backend = FakeBackend(master_requires_auth=True)
    worker = Worker(_cfg(tmp_path, max_iterations=1,
                         inspection_stage="CRATE_COUNT"), client=_client(backend))
    assert worker.startup() is True
    worker.item = worker.item.model_copy(update={"expected_count": expected})
    worker.acq = _FrameAcq(frame)
    return worker, backend


def test_count_match_posts_ok_row(tmp_path):
    worker, backend = _count_worker(tmp_path, expected=20, frame=crate_image(5, 4))
    assert worker.run_once() is True
    assert len(backend.posted) == 1
    row = backend.posted[0]
    assert row["inspection_stage"] == "CRATE_COUNT"
    assert row["final_verdict"] == "OK"
    assert row["defect_codes"] == []
    assert row["tube_index"] == 0
    # 길이·표면은 보지 않았다 — 값이 없어야 한다.
    assert row["meas_length_mm"] is None and row["length_verdict"] is None
    assert row["oil_score"] is None
    st = backend.statuses[-1]
    assert st["stage"] == "CRATE_COUNT"
    assert st["detected"] == 20 and st["expected"] == 20
    assert st["mismatch"] is False
    worker.shutdown()


def test_count_mismatch_posts_ng_with_count_code(tmp_path):
    worker, backend = _count_worker(tmp_path, expected=24, frame=crate_image(5, 4))
    worker.run_once()
    row = backend.posted[0]
    assert row["final_verdict"] == "NG"
    assert row["defect_codes"] == ["COUNT"]
    assert row["review_flag"] is True, "개수 불일치는 사람이 다시 센다"
    st = backend.statuses[-1]
    assert st["detected"] == 20 and st["expected"] == 24 and st["mismatch"] is True
    assert st["ng"] == 1
    worker.shutdown()


def test_count_mode_switch_from_active_order(tmp_path):
    """env 는 길이 모드여도 활성 오더가 CRATE_COUNT 면 개수 경로로 간다."""
    backend = FakeBackend(master_requires_auth=True)
    worker = Worker(_cfg(tmp_path, max_iterations=1, inspection_stage="CUT_LENGTH"),
                    client=_client(backend))
    assert worker.startup() is True
    worker.item = worker.item.model_copy(update={"expected_count": 20})
    worker.acq = _FrameAcq(crate_image(5, 4))
    worker._apply_active_meta({"lot": "L1", "inspection_stage": "CRATE_COUNT"})
    assert worker._cur_stage() == "CRATE_COUNT"
    worker.run_once()
    assert backend.posted[0]["inspection_stage"] == "CRATE_COUNT"
    worker.shutdown()


def test_bad_stage_from_order_is_ignored(tmp_path):
    backend = FakeBackend(master_requires_auth=True)
    worker = Worker(_cfg(tmp_path, max_iterations=1), client=_client(backend))
    assert worker.startup() is True
    worker._apply_active_meta({"inspection_stage": "COUNTING"})
    assert worker._cur_stage() == worker.cfg.inspection_stage
    worker.shutdown()


def test_count_overlay_marks_every_detection_and_states_the_numbers():
    img = crate_image(3, 2)
    res = count_bundle(img)
    out = render_count_overlay(img, res, expected=6)
    assert out.shape == img.shape
    assert not np.array_equal(out, img), "원이 그려져야 한다"
    bad = render_count_overlay(img, res, expected=9)
    # 불일치면 빨강 채널이 더 많이 쓰인다(색+문자 이중 표기).
    assert int((bad[..., 2].astype(int) - bad[..., 0].astype(int) > 100).sum()) > \
        int((out[..., 2].astype(int) - out[..., 0].astype(int) > 100).sum())
