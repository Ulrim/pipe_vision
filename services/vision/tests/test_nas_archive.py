"""현장 NAS(Synology) 사진 보관 (2026-10-21, imaging/archive.py).

1. NAS 가 붙어 있으면(표식 파일) YYYY/MM/DD/{raw,result,review}/운영 파일명 으로 쓴다(KST 날짜).
2. 표식이 없으면(마운트 안 됨) 마운트 지점에 아무것도 만들지 않고 파이 스풀에 모은다 —
   붙으면 오래된 것부터 옮긴다. 스풀 상한을 넘으면 버리고 센다.
3. 클라우드에 안 올리는 정상품 원본(AIVIS_REMOTE_RAW=ng)도 NAS 에는 남는다.
4. 로컬·다발·개수 저장 경로 모두. 보관이 실패해도 저장은 그대로.
5. 워커 하트비트에 NAS 상태.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.imaging.archive import MARKER, NasArchive, archive_relpath  # noqa: E402
from vision.imaging.save import (  # noqa: E402
    save_batch_images,
    save_count_images,
    save_inspection_images,
)
from vision.imaging.storage import StorageBackend  # noqa: E402

TS = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)  # KST 10-22 00:30 — 날짜가 넘어간다


def _frame(v=90):
    img = np.full((120, 200, 3), v, np.uint8)
    img[40:80, 30:170] = 200
    return img


def _nas(tmp_path, *, mounted=True, **kw):
    root = tmp_path / "nas" / "aivis"
    if mounted:
        root.mkdir(parents=True)
        (root / MARKER).write_text("")
    return NasArchive(root, tmp_path / "spool" / "archive", start_thread=False, **kw), root


def _files(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.jpg"))


# --- 1. 경로·쓰기 ----------------------------------------------------------------
def test_relpath_uses_kst_date_and_rejects_bad_keys():
    assert archive_relpath("raw/L_HP12_C1_x_OK.jpg", TS) == "2026/10/22/raw/L_HP12_C1_x_OK.jpg"
    for bad in ("other/x.jpg", "raw/", "raw/../x.jpg", "raw/.hidden.jpg"):
        with pytest.raises(ValueError):
            archive_relpath(bad, TS)


def test_writes_to_nas_atomically(tmp_path):
    arch, root = _nas(tmp_path)
    arch.submit("raw/a_OK.jpg", TS, image=_frame())
    arch.submit("result/a_OK.jpg", TS, jpeg=b"\xff\xd8jpeg")
    arch.close()
    assert _files(root) == ["2026/10/22/raw/a_OK.jpg", "2026/10/22/result/a_OK.jpg"]
    raw = cv2.imread(str(root / "2026/10/22/raw/a_OK.jpg"))
    assert raw.shape == (120, 200, 3)
    assert not list(root.rglob("*.tmp")), "반쯤 쓰인 파일이 남지 않는다"
    assert arch.status() == {"nas_ok": True, "nas_pending": 0, "nas_dropped": 0, "nas_error": None}


# --- 2. 끊김·스풀·재전송 ----------------------------------------------------------
def test_unmounted_nas_spools_without_touching_mountpoint(tmp_path):
    arch, root = _nas(tmp_path, mounted=False)
    arch.submit("raw/b_NG.jpg", TS, image=_frame())
    arch.close()
    assert not root.exists(), "마운트 안 된 지점에 폴더를 만들면 SD 카드가 찬다"
    st = arch.status()
    assert st["nas_ok"] is False and st["nas_pending"] == 1 and "표식" in st["nas_error"]

    root.mkdir(parents=True)
    (root / MARKER).write_text("")  # NAS 가 다시 붙었다
    assert arch.retry_spool() == 1
    assert _files(root) == ["2026/10/22/raw/b_NG.jpg"]
    assert arch.status()["nas_pending"] == 0


def test_spool_cap_drops_and_counts(tmp_path):
    arch, _root = _nas(tmp_path, mounted=False, spool_max_bytes=1)
    arch.submit("raw/c_OK.jpg", TS, jpeg=b"\xff\xd8" + b"x" * 100)
    arch.close()
    st = arch.status()
    assert st["nas_dropped"] == 1 and st["nas_pending"] == 0


def test_background_thread_writes(tmp_path):
    root = tmp_path / "nas"
    root.mkdir()
    (root / MARKER).write_text("")
    arch = NasArchive(root, tmp_path / "spool")
    for i in range(5):
        arch.submit(f"result/t{i}_OK.jpg", TS, image=_frame())
    arch.close(timeout=10)
    assert len(_files(root)) == 5


# --- 3·4. 저장 경로 -----------------------------------------------------------------
class _Cloud(StorageBackend):
    def __init__(self):
        self.keys = []

    def put(self, key, jpeg):
        self.keys.append(key)
        return key


def _result(verdict="OK", review=False):
    return SimpleNamespace(final_verdict=verdict, review_flag=review, length=None, surface=None,
                           defect_codes=[], confidence=1.0)


def test_ok_raw_skipped_by_cloud_still_goes_to_nas(tmp_path, monkeypatch):
    monkeypatch.setenv("AIVIS_REMOTE_RAW", "ng")
    arch, root = _nas(tmp_path)
    cloud = _Cloud()
    r = save_inspection_images(_frame(), _result("OK"), lot="L1", item_code="HP12", cam_id="C1",
                               inspected_at=TS, storage=cloud, archive=arch)
    arch.close()
    assert r.error is None and r.raw_image_path is None, "클라우드에는 정상품 원본을 안 올린다"
    assert [k.split("/")[0] for k in cloud.keys] == ["result"]
    kinds = sorted(p.split("/")[3] for p in _files(root))
    assert kinds == ["raw", "result"], "NAS 에는 원본까지"


def test_local_batch_and_count_paths_archive_with_review_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("AIVIS_STORAGE_BACKEND", raising=False)
    arch, root = _nas(tmp_path)
    tube = SimpleNamespace(index=1, bbox=(30, 40, 170, 80), review_flag=True, final_verdict="NG",
                           defect_codes=["LEN"], length_mm=None, deviation_mm=None,
                           length_verdict="NG", length_span=None, oil_score=None,
                           discolor_score=None, scratch_score=None, confidence=0.5)
    batch = SimpleNamespace(tubes=[tube], batch_verdict="NG", count_detected=1, count_expected=1,
                            count_mismatch=False)
    rb = save_batch_images(_frame(), batch, images_dir=str(tmp_path / "img"), lot="L2",
                           item_code="HP12", cam_id="C1", inspected_at=TS, archive=arch)
    bundle = SimpleNamespace(detections=[], count=0)
    rc = save_count_images(_frame(), bundle, expected=20, verdict="NG",
                           images_dir=str(tmp_path / "img"), lot="L3", item_code="HP12",
                           cam_id="C2", inspected_at=TS, archive=arch)
    arch.close()
    assert rb.error is None and rc.error is None
    got = _files(root)
    assert sum("/raw/" in p for p in got) == 2 and sum("/result/" in p for p in got) == 2
    assert sum("/review/" in p for p in got) == 2, "재확인 대상은 review 사본도"
    local = sorted(p.relative_to(tmp_path / "img").as_posix() for p in (tmp_path / "img").rglob("*.jpg"))
    assert [p.split("/", 1)[1] for p in local if p.startswith("raw/")] == \
        sorted(p.rsplit("/", 1)[1] for p in got if "/raw/" in p), "파일명은 운영본과 같다"


def test_archive_failure_never_breaks_saving(tmp_path):
    class Broken:
        def submit(self, *_a, **_k):
            raise RuntimeError("NAS 고장")

    r = save_inspection_images(_frame(), _result("NG"), images_dir=str(tmp_path / "img"),
                               lot="L4", item_code="HP12", inspected_at=TS, archive=Broken())
    assert r.error is None and r.raw_image_path and r.result_image_path


# --- 5. 하트비트 -----------------------------------------------------------------
def test_worker_heartbeat_reports_nas(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("_tw_nas", Path(__file__).with_name("test_worker.py"))
    tw = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(tw)
    from vision.worker.runner import Worker

    monkeypatch.setenv("AIVIS_ARCHIVE_DIR", str(tmp_path / "not-mounted"))
    backend = tw.FakeBackend(master_requires_auth=True)
    w = Worker(tw._cfg(tmp_path, max_iterations=1), client=tw._client(backend))
    assert w.archive is not None and w.startup() is True
    w._send_idle_status()
    host = backend.statuses[-1]["host"]
    assert host["nas_ok"] is False and host["nas_pending"] == 0 and host["nas_error"]
    w.shutdown()
    assert not (tmp_path / "not-mounted").exists()
