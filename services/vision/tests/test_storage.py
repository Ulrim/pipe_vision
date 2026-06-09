"""검사 이미지 스토리지 백엔드 테스트 (M7 일부, §4 ⑤ — 클라우드 보완).

클라우드(Render) 분리 배포에서 워커가 디스크에만 쓰면 api 가 못 읽으므로
Supabase Storage 업로드 백엔드를 추가했다. 이 테스트는 계약(backend/devops 합의)을
실 네트워크 없이 httpx.MockTransport 로 검증한다:

- supabase 모드: POST {URL}/storage/v1/object/{bucket}/{key} 가 올바른 URL·헤더·
  바이트(JPEG)로 일어난다(raw/result, review_flag면 review/도).
- 반환 상대경로(키)는 local 모드와 **동일**하다(§6.4 파일명 유지).
- 업로드 실패(4xx/5xx, 네트워크 예외)는 graceful — 검사결과 적재를 막지 않는다.
- local 모드(기본)는 기존 디스크 동작을 그대로 유지한다.
- 미설정(supabase 인데 URL/KEY 없음)이면 경고 후 local 폴백.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import (  # noqa: E402
    DefectCode,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)

from vision.imaging import save_inspection_images  # noqa: E402
from vision.imaging.storage import (  # noqa: E402
    DEFAULT_BUCKET,
    LocalStorage,
    StorageSettings,
    SupabaseStorage,
    build_backend,
    encode_jpeg,
)
from vision.worker.config import WorkerConfig  # noqa: E402

_URL = "https://proj.supabase.co"
_KEY = "service-role-secret"


def _ts() -> datetime:
    return datetime(2026, 6, 9, 14, 12, 33, 456789, tzinfo=timezone.utc)


def _frame() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(120, 200, 3), dtype=np.uint8)


def _verdict(*, review=False) -> VerdictResult:
    return VerdictResult(
        final_verdict=Verdict.NG,
        defect_codes=[DefectCode.SCR],
        confidence=0.87,
        review_flag=review,
        length=LengthResult(
            ref_length_mm=125.0,
            meas_length_mm=124.7,
            deviation_mm=-0.3,
            length_verdict=Verdict.OK,
            edge_detected=True,
            proc_time_ms=4,
        ),
        surface=SurfaceResult(
            oil_score=0.11,
            discolor_score=0.05,
            scratch_score=0.42,
            surface_verdict=Verdict.NG,
            defect_codes=[DefectCode.SCR],
            proc_time_ms=6,
        ),
        proc_time_ms=42,
    )


def _item() -> ItemMaster:
    return ItemMaster(
        item_code="HP12",
        item_name="Header Pipe 12",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
        oil_threshold=0.30,
        discolor_threshold=0.20,
        scratch_threshold=0.15,
    )


class _Recorder:
    """업로드된 요청을 기록하는 MockTransport 핸들러."""

    def __init__(self, *, status: int = 200, fail_keys=None) -> None:
        self.status = status
        self.fail_keys = set(fail_keys or [])
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(request.content)
        # 특정 키(경로 일부)만 실패시키는 시나리오.
        for k in self.fail_keys:
            if k in request.url.path:
                return httpx.Response(500, text="boom")
        return httpx.Response(self.status, json={"Key": request.url.path})


def _supabase_backend(recorder: _Recorder, bucket: str = DEFAULT_BUCKET) -> SupabaseStorage:
    client = httpx.Client(transport=httpx.MockTransport(recorder))
    return SupabaseStorage(_URL, _KEY, bucket, client=client)


# --- encode_jpeg ---
def test_encode_jpeg_returns_valid_jpeg_bytes():
    data = encode_jpeg(_frame())
    assert isinstance(data, (bytes, bytearray))
    assert bytes(data[:3]) == b"\xff\xd8\xff"  # JPEG SOI 매직.


# --- SupabaseStorage.put: URL/헤더/바이트 ---
def test_supabase_put_correct_url_headers_body():
    rec = _Recorder()
    backend = _supabase_backend(rec)
    jpeg = encode_jpeg(_frame())
    key = "raw/LOT1_HP12_20260609141233456_NG.jpg"

    returned = backend.put(key, jpeg)

    assert returned == key  # 반환 키 = 입력 키(상대경로 그대로).
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req.method == "POST"
    assert (
        str(req.url)
        == f"{_URL}/storage/v1/object/{DEFAULT_BUCKET}/{key}"
    )
    assert req.headers["Authorization"] == f"Bearer {_KEY}"
    assert req.headers["apikey"] == _KEY
    assert req.headers["Content-Type"] == "image/jpeg"
    assert req.headers["x-upsert"] == "true"
    # 업로드 바디 = 정확히 그 jpeg 바이트.
    assert rec.bodies[0] == jpeg


def test_supabase_put_respects_custom_bucket():
    rec = _Recorder()
    backend = _supabase_backend(rec, bucket="my-bucket")
    backend.put("result/x.jpg", encode_jpeg(_frame()))
    assert str(rec.requests[0].url).endswith(
        "/storage/v1/object/my-bucket/result/x.jpg"
    )


def test_supabase_put_raises_on_http_error():
    rec = _Recorder(status=403)
    backend = _supabase_backend(rec)
    with pytest.raises(OSError):
        backend.put("raw/x.jpg", encode_jpeg(_frame()))


# --- save_inspection_images(supabase 주입): raw+result+review 업로드 ---
def test_save_inspection_images_supabase_uploads_raw_result_review():
    rec = _Recorder()
    backend = _supabase_backend(rec)

    out = save_inspection_images(
        _frame(),
        _verdict(review=True),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
        storage=backend,
    )

    assert out.error is None
    # 반환 상대경로(키)는 local 모드와 동일.
    assert out.raw_image_path == "raw/LOT1_HP12_20260609141233456_NG.jpg"
    assert out.result_image_path == "result/LOT1_HP12_20260609141233456_NG.jpg"

    paths = [r.url.path for r in rec.requests]
    # raw, result, review 3건 업로드(POST), 모두 동일 파일명.
    assert any(p.endswith("/raw/LOT1_HP12_20260609141233456_NG.jpg") for p in paths)
    assert any(p.endswith("/result/LOT1_HP12_20260609141233456_NG.jpg") for p in paths)
    assert any(p.endswith("/review/LOT1_HP12_20260609141233456_NG.jpg") for p in paths)
    assert len(rec.requests) == 3
    # 모든 요청이 POST + 올바른 헤더.
    for req in rec.requests:
        assert req.method == "POST"
        assert req.headers["x-upsert"] == "true"
        assert req.headers["Content-Type"] == "image/jpeg"
    # 업로드된 바이트는 유효 JPEG.
    for body in rec.bodies:
        assert body[:3] == b"\xff\xd8\xff"


def test_save_inspection_images_supabase_no_review_skips_review_key():
    rec = _Recorder()
    backend = _supabase_backend(rec)
    out = save_inspection_images(
        _frame(),
        _verdict(review=False),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
        storage=backend,
    )
    assert out.error is None
    paths = [r.url.path for r in rec.requests]
    assert not any("/review/" in p for p in paths)
    assert len(rec.requests) == 2  # raw + result 만.


# --- graceful: 업로드 실패가 검사결과 적재를 막지 않는다 ---
def test_save_inspection_images_supabase_upload_failure_graceful():
    # raw 업로드부터 500 → 전체 실패지만 죽지 않고 error 보고.
    rec = _Recorder(fail_keys=["/raw/"], status=200)
    backend = _supabase_backend(rec)
    out = save_inspection_images(
        _frame(),
        _verdict(),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
        storage=backend,
    )
    assert out.error is not None and "OSError" in out.error
    assert out.raw_image_path is None
    assert out.result_image_path is None


def test_save_inspection_images_supabase_review_failure_is_ignored():
    # review 사본만 실패 → 주 raw/result 경로는 유지(보조 정책).
    rec = _Recorder(fail_keys=["/review/"], status=200)
    backend = _supabase_backend(rec)
    out = save_inspection_images(
        _frame(),
        _verdict(review=True),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
        storage=backend,
    )
    assert out.error is None
    assert out.raw_image_path == "raw/LOT1_HP12_20260609141233456_NG.jpg"
    assert out.result_image_path == "result/LOT1_HP12_20260609141233456_NG.jpg"


def test_save_inspection_images_supabase_network_exception_graceful():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = httpx.Client(transport=httpx.MockTransport(boom))
    backend = SupabaseStorage(_URL, _KEY, client=client)
    out = save_inspection_images(
        _frame(),
        _verdict(),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
        storage=backend,
    )
    assert out.error is not None
    assert out.raw_image_path is None


# --- local 모드(기본): 기존 디스크 동작 유지 ---
def test_save_inspection_images_local_mode_writes_disk(tmp_path):
    out = save_inspection_images(
        _frame(),
        _verdict(review=True),
        images_dir=str(tmp_path),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
    )
    assert out.error is None
    assert out.raw_image_path == "raw/LOT1_HP12_20260609141233456_NG.jpg"
    assert (tmp_path / out.raw_image_path).exists()
    assert (tmp_path / out.result_image_path).exists()
    assert list((tmp_path / "review").glob("*.jpg"))


def test_save_inspection_images_env_supabase_uses_supabase(monkeypatch, tmp_path):
    """AIVIS_STORAGE_BACKEND=supabase + 자격 env → 디스크에 안 쓰고 업로드한다."""
    rec = _Recorder()
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _KEY)
    monkeypatch.setattr(
        "vision.imaging.save.build_backend",
        lambda settings: _supabase_backend(rec),
    )
    out = save_inspection_images(
        _frame(),
        _verdict(),
        images_dir=str(tmp_path),
        lot="LOT1",
        item_code="HP12",
        inspected_at=_ts(),
        item=_item(),
    )
    assert out.error is None
    assert len(rec.requests) == 2
    # 디스크에는 아무것도 쓰지 않았다(클라우드 전용).
    assert not (tmp_path / "raw").exists()


# --- StorageSettings / build_backend ---
def test_settings_default_is_local(monkeypatch):
    monkeypatch.delenv("AIVIS_STORAGE_BACKEND", raising=False)
    s = StorageSettings.from_env(images_dir="/x")
    assert s.backend == "local"
    assert not s.is_supabase
    assert isinstance(build_backend(s), LocalStorage)


def test_settings_unknown_backend_falls_back_local(monkeypatch):
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "azure")
    s = StorageSettings.from_env(images_dir="/x")
    assert s.backend == "local"


def test_build_backend_supabase_missing_creds_falls_back_local(monkeypatch):
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    s = StorageSettings.from_env(images_dir="/x")
    assert s.is_supabase
    # 자격 누락 → local 폴백(업로드 안 함).
    assert isinstance(build_backend(s), LocalStorage)


# --- WorkerConfig storage env ---
def test_worker_config_storage_env(monkeypatch):
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", _KEY)
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "imgs")
    cfg = WorkerConfig.from_env()
    assert cfg.storage_backend == "supabase"
    assert cfg.supabase_url == _URL
    assert cfg.supabase_key == _KEY
    assert cfg.supabase_bucket == "imgs"
    assert cfg.supabase_configured is True


def test_worker_config_storage_defaults(monkeypatch):
    for k in (
        "AIVIS_STORAGE_BACKEND",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORAGE_BUCKET",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = WorkerConfig.from_env()
    assert cfg.storage_backend == "local"
    assert cfg.supabase_url is None
    assert cfg.supabase_bucket == "inspection-images"
    assert cfg.supabase_configured is False


def test_worker_config_warns_when_supabase_misconfigured(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "supabase")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    cfg = WorkerConfig.from_env()
    log = logging.getLogger("aivis.test.storage")
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_misconfigured(log)
    assert any("supabase" in r.message.lower() for r in caplog.records)
