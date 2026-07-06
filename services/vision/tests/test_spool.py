"""오프라인 스풀(디스크 버퍼) + 자동 재전송 테스트 (worker/spool.py).

모드 A(Pi→클라우드)에서 인터넷 단절 시 POST /inspection·이미지 업로드 실패로
검사결과가 유실되지 않음을 검증한다. 실 네트워크 없이 httpx.MockTransport 로
backend 를 흉내낸다(AIVIS_CAMERA=sim 원칙 유지).

검증 항목:
  - enqueue 원자성(tmp 잔류물 없음)/파일명({ms}_{cam_id}.json)/충돌 접미.
  - 4xx 영구 오류는 스풀하지 않음(worker 통합).
  - 용량 상한 초과 시 oldest-first 드롭(SD 카드 보호).
  - flush: 성공 시 삭제, oldest-first, 배치 상한, 연결 오류 즉시 중단,
    4xx → dead/ 이동, pending 이미지 선업로드 후 POST.
  - 재시작 후 디렉터리 재로드 flush.
  - worker 통합: 오프라인 스풀 → 복구 후 재전송(서버 멱등 전제).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import InspectionResult  # noqa: E402

from vision.worker.client import ApiClient  # noqa: E402
from vision.worker.config import WorkerConfig  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402
from vision.worker.spool import (  # noqa: E402
    PENDING_IMAGES_KEY,
    SpoolQueue,
    is_permanent_status,
    is_retryable_status,
)


def _ts(sec: int) -> datetime:
    return datetime.fromtimestamp(sec, tz=timezone.utc)


def make_result(sec: int = 1_700_000_000, cam: str = "CAM1", **kw) -> InspectionResult:
    base = dict(
        lot="LOTSPOOL",
        item_code="HP12",
        cam_id=cam,
        inspected_at=_ts(sec),
        final_verdict="OK",
    )
    base.update(kw)
    return InspectionResult(**base)


# ---------------------------------------------------------------- 분류 헬퍼
def test_status_classification():
    assert is_retryable_status(0)          # 연결 오류/타임아웃
    assert is_retryable_status(500)
    assert is_retryable_status(503)
    assert not is_retryable_status(201)
    for code in (400, 401, 403, 404, 422):
        assert is_permanent_status(code)
        assert not is_retryable_status(code)


# ---------------------------------------------------------------- enqueue
def test_enqueue_atomic_filename_and_content(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    r = make_result(sec=1, cam="CAM1")
    path = q.enqueue(r)
    assert path is not None
    assert path.parent == q.pending_path
    assert path.name == "1000_CAM1.json"  # inspected_at_ms + cam_id
    # 원자성: tmp/ 에 반쪽 파일이 남지 않는다.
    assert not any(q.tmp_path.glob("*")), "tmp 잔류물이 없어야 한다"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["item_code"] == "HP12"
    assert payload["cam_id"] == "CAM1"
    assert PENDING_IMAGES_KEY not in payload  # 이미지 없으면 메타도 없음
    # 파일명 충돌(동일 ms+cam) 시 접미로 회피.
    p2 = q.enqueue(make_result(sec=1, cam="CAM1"))
    assert p2.name == "1000_CAM1-1.json"
    assert q.pending_count() == 2
    assert q.enqueued == 2


def test_enqueue_records_pending_images_meta(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    path = q.enqueue(make_result(), pending_images=["raw/a.jpg", "result/a.jpg"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[PENDING_IMAGES_KEY] == ["raw/a.jpg", "result/a.jpg"]


def test_save_image_writes_under_images_dir(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    key = q.save_image("raw/LOT_HP12_x_OK.jpg", b"JPEGBYTES")
    assert key == "raw/LOT_HP12_x_OK.jpg"
    f = q.images_path / "raw" / "LOT_HP12_x_OK.jpg"
    assert f.read_bytes() == b"JPEGBYTES"
    # 경로 탈출 금지.
    assert q.save_image("../evil.jpg", b"x") is None


# ---------------------------------------------------------------- 용량 상한
def test_capacity_cap_drops_oldest(tmp_path):
    def payload(sec):
        return {
            "cam_id": "CAM1",
            "inspected_at": _ts(sec).isoformat(),
            "pad": "a" * 400,
        }

    # 항목 1개 크기를 실측해 상한 = 2.5개 분량으로 잡는다(결정적).
    probe = SpoolQueue(tmp_path / "probe")
    size = probe.enqueue(payload(1)).stat().st_size

    q = SpoolQueue(tmp_path / "spool", max_bytes=int(size * 2.5))
    q.enqueue(payload(1))
    q.enqueue(payload(2))
    q.enqueue(payload(3))  # 초과 → 가장 오래된 1000_* 드롭
    names = sorted(p.name for p in q.pending_path.glob("*.json"))
    assert names == ["2000_CAM1.json", "3000_CAM1.json"]
    assert q.dropped == 1


def test_capacity_cap_drops_item_images_too(tmp_path):
    q = SpoolQueue(tmp_path / "spool", max_bytes=1)  # 무조건 초과
    q.save_image("raw/x.jpg", b"J" * 100)
    q.enqueue(make_result(sec=1), pending_images=["raw/x.jpg"])
    # 상한 1바이트 → 방금 항목까지 드롭되고 참조 이미지도 함께 삭제된다.
    assert q.pending_count() == 0
    assert not (q.images_path / "raw" / "x.jpg").exists()
    assert q.dropped == 1


# ---------------------------------------------------------------- flush
def test_flush_sends_oldest_first_with_batch_limit(tmp_path):
    q = SpoolQueue(tmp_path / "spool", flush_batch=3)
    for sec in (5, 1, 3, 2, 4):  # 순서 섞어 적재
        q.enqueue(make_result(sec=sec))
    sent_ms = []

    def post_fn(payload):
        sent_ms.append(payload["inspected_at"])
        assert PENDING_IMAGES_KEY not in payload  # 전송 전 메타 제거
        return 201, "stored"

    rep = q.flush(post_fn)
    assert rep.sent == 3 and rep.dead == 0 and not rep.aborted
    # oldest-first: 1,2,3초 항목이 먼저 나간다(직렬화 표기 Z/+00:00 무관).
    sent_secs = [
        int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        for s in sent_ms
    ]
    assert sent_secs == [1, 2, 3]
    # 성공 항목은 삭제, 배치 상한으로 2개 잔류.
    assert q.pending_count() == 2
    rep2 = q.flush(post_fn)
    assert rep2.sent == 2 and q.pending_count() == 0
    assert q.sent == 5


def test_flush_aborts_immediately_on_connection_error(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    for sec in (1, 2, 3):
        q.enqueue(make_result(sec=sec))
    calls = []

    def post_fn(payload):
        calls.append(payload["inspected_at"])
        return 0, "POST 예외: ConnectError"  # 네트워크 다운

    rep = q.flush(post_fn)
    assert rep.aborted is True
    assert len(calls) == 1, "연결 오류면 즉시 중단(낭비 금지)"
    assert q.pending_count() == 3, "항목은 보존되어야 한다"


def test_flush_moves_permanent_4xx_to_dead(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    q.enqueue(make_result(sec=1))
    q.enqueue(make_result(sec=2))
    rep = q.flush(lambda payload: (422, "validation error"))
    assert rep.dead == 2 and rep.sent == 0
    assert q.pending_count() == 0
    assert sorted(p.name for p in q.dead_path.glob("*.json")) == [
        "1000_CAM1.json",
        "2000_CAM1.json",
    ]


def test_flush_holds_item_on_5xx(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    q.enqueue(make_result(sec=1))
    rep = q.flush(lambda payload: (503, "unavailable"))
    assert rep.held == 1 and rep.sent == 0 and rep.dead == 0
    assert q.pending_count() == 1  # 다음 기회에 재시도


def test_flush_uploads_pending_images_before_post(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    raw_key = "raw/LOTSPOOL_HP12_x_OK.jpg"
    result_key = "result/LOTSPOOL_HP12_x_OK.jpg"
    q.save_image(raw_key, b"RAWJPEG")
    q.save_image(result_key, b"RESJPEG")
    q.enqueue(
        make_result(raw_image_path=raw_key, result_image_path=result_key),
        pending_images=[raw_key, result_key],
    )
    events = []

    def upload_fn(key, jpeg):
        events.append(("up", key, jpeg))

    def post_fn(payload):
        events.append(("post", payload["raw_image_path"]))
        assert PENDING_IMAGES_KEY not in payload
        return 201, "stored"

    rep = q.flush(post_fn, upload_fn=upload_fn)
    assert rep.sent == 1
    # 이미지 업로드가 POST 보다 먼저, 키/바이트 그대로.
    assert events == [
        ("up", raw_key, b"RAWJPEG"),
        ("up", result_key, b"RESJPEG"),
        ("post", raw_key),
    ]
    # 성공 후 스풀 이미지·payload 모두 정리.
    assert not (q.images_path / raw_key).exists()
    assert q.pending_count() == 0


def test_flush_holds_item_when_image_upload_fails(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    q.save_image("raw/a.jpg", b"J")
    q.enqueue(make_result(sec=1), pending_images=["raw/a.jpg"])
    posts = []

    def bad_upload(key, jpeg):
        raise OSError("Supabase 업로드 실패 500")

    rep = q.flush(lambda p: posts.append(p) or (201, "x"), upload_fn=bad_upload)
    assert rep.held == 1 and rep.sent == 0
    assert posts == [], "이미지 미복구 항목은 POST 하지 않는다"
    assert q.pending_count() == 1 and (q.images_path / "raw" / "a.jpg").exists()


def test_flush_aborts_when_image_upload_hits_network_error(tmp_path):
    q = SpoolQueue(tmp_path / "spool")
    q.save_image("raw/a.jpg", b"J")
    q.enqueue(make_result(sec=1), pending_images=["raw/a.jpg"])
    q.enqueue(make_result(sec=2))

    def down(key, jpeg):
        raise httpx.ConnectError("network down")

    posts = []
    rep = q.flush(lambda p: posts.append(p) or (201, "x"), upload_fn=down)
    assert rep.aborted is True
    assert posts == [], "연결 오류면 이후 항목도 시도하지 않는다"
    assert q.pending_count() == 2


def test_flush_after_restart_reloads_directory(tmp_path):
    spool_dir = tmp_path / "spool"
    q1 = SpoolQueue(spool_dir)
    q1.enqueue(make_result(sec=1))
    q1.enqueue(make_result(sec=2))
    del q1  # 프로세스 재시작 모사 — 상태는 디스크에만 있다.

    q2 = SpoolQueue(spool_dir)
    assert q2.pending_count() == 2
    rep = q2.flush(lambda payload: (201, "stored"))
    assert rep.sent == 2 and q2.pending_count() == 0


# ------------------------------------------------- 이미지 업로드 실패 → 스풀 연동
def test_save_inspection_images_spools_failed_uploads(tmp_path, item):
    from vision.imaging.save import save_inspection_images
    from vision.imaging.storage import StorageBackend
    from vision.pipeline import InspectionPipeline
    from vision.tools.gen_synthetic import make_image

    class DownBackend(StorageBackend):
        def put(self, key: str, jpeg: bytes) -> str:
            raise OSError("Supabase 업로드 실패: connection refused")

    q = SpoolQueue(tmp_path / "spool")
    frame, _ = make_image("OK")
    verdict = InspectionPipeline().run(frame, item)
    saved = save_inspection_images(
        frame,
        verdict,
        images_dir=str(tmp_path / "images"),
        lot="LOTSPOOL",
        item_code="HP12",
        inspected_at=_ts(1),
        item=item,
        storage=DownBackend(),
        pending_sink=q.save_image,
    )
    # 키(상대경로)는 유지되고, 바이트는 스풀 images/ 에 보존된다.
    assert saved.error is None
    assert saved.raw_image_path and saved.raw_image_path.startswith("raw/")
    assert saved.result_image_path and saved.result_image_path.startswith("result/")
    assert set(saved.pending_images) == {
        saved.raw_image_path,
        saved.result_image_path,
    }
    for key in saved.pending_images:
        assert (q.images_path / key).stat().st_size > 0


# ---------------------------------------------------------------- worker 통합
class SpoolFakeBackend:
    """POST /inspection 동작을 시나리오로 제어하는 backend stub.

    post_behaviors: 항목별 int status 또는 "conn"(연결 오류). 소진 후 201.
    """

    def __init__(self, post_behaviors=None, item_code: str = "HP12") -> None:
        self.item_code = item_code
        self.posted: list[dict] = []
        self.post_behaviors = list(post_behaviors or [])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
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
                    "capture_recipe": None,
                    "version": 1,
                },
            )
        if path == "/inspection" and request.method == "POST":
            beh = self.post_behaviors.pop(0) if self.post_behaviors else 201
            if beh == "conn":
                raise httpx.ConnectError("network down")
            if isinstance(beh, int) and 200 <= beh < 300:
                self.posted.append(json.loads(request.content))
                return httpx.Response(
                    beh, json={"status": "stored", "id": len(self.posted)}
                )
            return httpx.Response(int(beh), json={"detail": "err"})
        return httpx.Response(404, json={"detail": f"no route {path}"})


def _worker(tmp_path, backend, **cfg_overrides) -> Worker:
    cfg = WorkerConfig(
        camera_mode="sim",
        dataset_dir=None,
        api_url="",
        item_code="HP12",
        cam_id="CAM1",
        lot="LOTSPOOL",
        interval_ms=0,
        api_wait_timeout_s=5,
        item_wait_timeout_s=5,
        ready_file=str(tmp_path / "vision_ready"),
        images_dir=str(tmp_path / "images"),
        spool_dir=str(tmp_path / "spool"),
        **cfg_overrides,
    )
    client = ApiClient("", transport=httpx.MockTransport(backend.handler))
    return Worker(cfg, client=client)


def test_worker_spools_on_connection_error_then_flushes(tmp_path):
    backend = SpoolFakeBackend(post_behaviors=["conn"])
    worker = _worker(tmp_path, backend)
    assert worker.startup() is True

    # 1) 오프라인: POST 연결 오류 → 스풀 적재(유실 금지), failure 아님.
    assert worker.run_once() is False
    assert worker.spooled == 1 and worker.failure == 0
    assert worker.spool.pending_count() == 1

    # 2) 네트워크 복구: 라이브 검사 + flush 재전송 모두 성공.
    assert worker.run_once() is True
    worker.flush_spool()
    assert worker.spool.pending_count() == 0
    assert len(backend.posted) == 2
    # 재전송 payload 는 스키마 그대로(_pending_images 금지).
    for body in backend.posted:
        assert PENDING_IMAGES_KEY not in body
        assert body["item_code"] == "HP12"
    worker.shutdown()


def test_worker_spools_on_5xx(tmp_path):
    backend = SpoolFakeBackend(post_behaviors=[503])
    worker = _worker(tmp_path, backend)
    assert worker.startup() is True
    assert worker.run_once() is False
    assert worker.spooled == 1 and worker.failure == 0
    assert worker.spool.pending_count() == 1
    worker.shutdown()


def test_worker_does_not_spool_permanent_4xx(tmp_path):
    backend = SpoolFakeBackend(post_behaviors=[422])
    worker = _worker(tmp_path, backend)
    assert worker.startup() is True
    assert worker.run_once() is False
    assert worker.failure == 1 and worker.spooled == 0
    assert worker.spool.pending_count() == 0, "4xx 는 스풀하지 않는다"
    worker.shutdown()


def test_worker_spools_result_when_images_pending(tmp_path, monkeypatch):
    """이미지 업로드 실패 시 결과를 즉시 POST 하지 않고 스풀로 우회한다."""
    import vision.worker.runner as runner_mod
    from vision.imaging.save import ImageSaveResult

    q_keys = ("raw/x.jpg", "result/x.jpg")

    def fake_save(frame, verdict, **kw):
        sink = kw.get("pending_sink")
        for key in q_keys:
            sink(key, b"JPEG")
        return ImageSaveResult(
            raw_image_path=q_keys[0],
            result_image_path=q_keys[1],
            pending_images=q_keys,
        )

    monkeypatch.setattr(runner_mod, "save_inspection_images", fake_save)
    backend = SpoolFakeBackend()
    worker = _worker(tmp_path, backend)
    assert worker.startup() is True
    assert worker.run_once() is False
    assert worker.spooled == 1
    assert backend.posted == [], "이미지 미복구 상태에서 라이브 POST 금지"
    assert worker.spool.pending_count() == 1

    # flush: 이미지 선업로드 → POST → 정리.
    uploads = []
    worker._image_uploader = lambda key, jpeg: uploads.append(key)
    worker._uploader_built = True
    worker.flush_spool()
    assert uploads == list(q_keys)
    assert len(backend.posted) == 1
    assert backend.posted[0]["raw_image_path"] == q_keys[0]
    assert worker.spool.pending_count() == 0
    worker.shutdown()


def test_worker_startup_flush_recovers_previous_session(tmp_path):
    """재시작 시(run 진입) 이전 세션 스풀 잔량이 자동 재전송된다."""
    spool_dir = tmp_path / "spool"
    SpoolQueue(spool_dir).enqueue(make_result(sec=1))

    backend = SpoolFakeBackend()
    worker = _worker(tmp_path, backend, max_iterations=1)
    rc = worker.run()
    assert rc == 0
    # 이전 세션 1건(flush) + 이번 세션 라이브 1건.
    assert len(backend.posted) == 2
    assert worker.spool.pending_count() == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
