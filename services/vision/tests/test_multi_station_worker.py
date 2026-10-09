"""파이 여러 대 동시 운영(2026-10-09) — 워커 쪽.

1. 파일명에 카메라가 들어간다: 같은 오더를 두 대가 같은 ms 에 찍어도 안 겹친다.
2. AIVIS_STORAGE_BACKEND=api: 2호기가 허브 API 에 사진을 올린다(실패 시 스풀).
3. 하트비트에 이 파이 자신의 상태(온도·CPU·메모리·디스크·전원)가 실린다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.imaging import build_filename, save_inspection_images  # noqa: E402
from vision.imaging.save import save_batch_images  # noqa: E402
from vision.imaging.storage import (  # noqa: E402
    API,
    ApiStorage,
    LocalStorage,
    StorageSettings,
    build_backend,
)
from vision.worker.config import WorkerConfig  # noqa: E402
from vision.worker.hostinfo import HostInfo  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_ms_{name}", Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


_st = _load("test_storage")
_tw = _load("test_worker")
_frame, _verdict, _item, _ts = _st._frame, _st._verdict, _st._item, _st._ts


# --- 1. 파일명 -------------------------------------------------------------

def test_filename_carries_camera_and_keeps_verdict_suffix():
    fn = build_filename("LOT1", "HP12", _ts(), "NG", "PI-CAM2")
    assert fn == "LOT1_HP12_PI-CAM2_20260609141233456_NG.jpg"
    # 보관기한 정리는 끝의 _OK/_NG 로 판단한다 — 그대로여야 한다.
    assert fn.endswith("_NG.jpg")


def test_same_order_same_millisecond_two_cameras_do_not_collide():
    a = build_filename("LOT1", "HP12", _ts(), "OK", "PI-CAM1")
    b = build_filename("LOT1", "HP12", _ts(), "OK", "PI-CAM2")
    assert a != b, "두 대의 사진이 한곳에서 서로 덮어쓴다"


def test_without_camera_the_old_name_is_unchanged():
    assert build_filename("LOT1", "HP12", _ts(), "OK") == "LOT1_HP12_20260609141233456_OK.jpg"


def test_camera_token_is_sanitized():
    fn = build_filename("L", "I", _ts(), "OK", "../cam 1")
    assert "/" not in fn and " " not in fn and ".." not in fn


def test_local_save_uses_camera_in_both_raw_and_result(tmp_path):
    out = save_inspection_images(
        _frame(), _verdict(), images_dir=str(tmp_path), lot="LOT1", item_code="HP12",
        inspected_at=_ts(), item=_item(), cam_id="PI-CAM2",
    )
    assert out.error is None
    assert "_PI-CAM2_" in out.raw_image_path and "_PI-CAM2_" in out.result_image_path
    assert (tmp_path / out.raw_image_path).exists()


# --- 2. api 백엔드 --------------------------------------------------------

class _Hub:
    """허브 API 를 흉내 — PUT /inspection/images/{key} 를 기록."""

    def __init__(self, status: int = 201, *, raise_exc: bool = False) -> None:
        self.status = status
        self.raise_exc = raise_exc
        self.puts: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.raise_exc:
            raise httpx.ConnectError("허브 꺼짐", request=request)
        self.puts.append(request)
        return httpx.Response(self.status, json={"key": request.url.path})


def _api(hub: _Hub, token: str | None = "TOK") -> ApiStorage:
    return ApiStorage("http://pi1:8000/", token,
                      client=httpx.Client(transport=httpx.MockTransport(hub)))


def test_api_put_url_headers_body():
    hub = _Hub()
    key = _api(hub).put("raw/a_OK.jpg", b"\xff\xd8jpeg")
    assert key == "raw/a_OK.jpg"
    req = hub.puts[0]
    assert req.method == "PUT"
    assert str(req.url) == "http://pi1:8000/inspection/images/raw/a_OK.jpg"
    assert req.headers["Content-Type"] == "image/jpeg"
    assert req.headers["X-Service-Token"] == "TOK"
    assert req.headers["Authorization"] == "Bearer TOK"
    assert req.content == b"\xff\xd8jpeg"


def test_api_put_without_token_sends_no_auth_headers():
    hub = _Hub()
    _api(hub, token=None).put("raw/a_OK.jpg", b"x")
    assert "X-Service-Token" not in hub.puts[0].headers
    assert "Authorization" not in hub.puts[0].headers


@pytest.mark.parametrize("hub", [_Hub(status=503), _Hub(raise_exc=True)])
def test_api_put_failure_is_oserror(hub):
    with pytest.raises(OSError):
        _api(hub).put("raw/a_OK.jpg", b"x")


def test_upload_failure_keeps_bytes_in_spool_and_reports_pending():
    """허브가 잠깐 꺼져도 사진을 잃지 않는다 — 스풀에 보존하고 나중에 올린다."""
    kept: dict[str, bytes] = {}

    def sink(key: str, jpeg: bytes):
        kept[key] = jpeg
        return key

    out = save_inspection_images(
        _frame(), _verdict(), lot="LOT1", item_code="HP12", inspected_at=_ts(),
        item=_item(), storage=_api(_Hub(raise_exc=True)), pending_sink=sink,
        cam_id="PI-CAM2",
    )
    assert out.error is None
    assert set(out.pending_images) == {out.raw_image_path, out.result_image_path}
    assert all("_PI-CAM2_" in k for k in kept)


def test_batch_images_upload_to_hub_with_camera_name():
    hub = _Hub()

    class _Batch:
        batch_verdict = "OK"
        tubes = []

    import vision.imaging.save as save_mod
    orig = save_mod.render_batch_overlay
    save_mod.render_batch_overlay = lambda frame, br: frame.copy()
    try:
        out = save_batch_images(_frame(), _Batch(), lot="LOT1", item_code="HP12",
                                inspected_at=_ts(), storage=_api(hub), cam_id="PI-CAM2")
    finally:
        save_mod.render_batch_overlay = orig
    assert out.error is None
    paths = sorted(r.url.path for r in hub.puts)
    assert paths == [
        "/inspection/images/raw/LOT1_HP12_PI-CAM2_20260609141233456_OK.jpg",
        "/inspection/images/result/LOT1_HP12_PI-CAM2_20260609141233456_OK.jpg",
    ]


def test_settings_from_env_api(monkeypatch):
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "API")
    monkeypatch.setenv("AIVIS_API_URL", "http://pi1:8000")
    monkeypatch.setenv("AIVIS_SERVICE_TOKEN", "TOK")
    st = StorageSettings.from_env(images_dir="/x")
    assert st.backend == API and st.is_remote and st.is_api
    assert st.api_url == "http://pi1:8000" and st.service_token == "TOK"
    assert isinstance(build_backend(st), ApiStorage)


def test_api_backend_without_url_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "api")
    monkeypatch.delenv("AIVIS_API_URL", raising=False)
    st = StorageSettings.from_env(images_dir="/x")
    assert isinstance(build_backend(st), LocalStorage)


def test_env_api_mode_save_goes_to_hub(monkeypatch, tmp_path):
    """env 만으로(storage 주입 없이) api 모드가 켜지는지 — 실제 워커 경로."""
    hub = _Hub()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: real_client(transport=httpx.MockTransport(hub)))
    monkeypatch.setenv("AIVIS_STORAGE_BACKEND", "api")
    monkeypatch.setenv("AIVIS_API_URL", "http://pi1:8000")
    out = save_inspection_images(
        _frame(), _verdict(), images_dir=str(tmp_path), lot="LOT1", item_code="HP12",
        inspected_at=_ts(), item=_item(), cam_id="PI-CAM2",
    )
    assert out.error is None
    assert len(hub.puts) >= 2
    assert not any(tmp_path.rglob("*.jpg")), "허브로 올렸으면 로컬에 쓰지 않는다"


def test_worker_spool_uploader_uses_hub_in_api_mode(tmp_path):
    cfg = WorkerConfig(camera_mode="sim", api_url="http://pi1:8000", storage_backend="api",
                       ready_file=str(tmp_path / "r"), images_dir=str(tmp_path))
    w = Worker(cfg, client=_tw._client(_tw.FakeBackend()))
    up = w._spool_uploader()
    assert up is not None and getattr(up, "__self__", None).__class__ is ApiStorage


def test_worker_config_warns_api_without_url(caplog):
    import logging
    cfg = WorkerConfig(storage_backend="api", api_url="")
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_misconfigured(logging.getLogger("t"))
    assert "AIVIS_API_URL" in caplog.text


# --- 3. 하트비트 자기 상태 -------------------------------------------------

def _fake_fs(stat_lines: list[str]):
    """/proc/stat 은 호출마다 다음 값을, 나머지는 고정값을 돌려주는 read()."""
    seq = iter(stat_lines)
    files = {
        "/sys/class/thermal/thermal_zone0/temp": "61234\n",
        "/proc/meminfo": "MemTotal: 4000000 kB\nMemFree: 1 kB\nMemAvailable: 1000000 kB\n",
        "/sys/devices/platform/soc/soc:firmware/get_throttled": "0x50005\n",
    }

    def read(path: str) -> str:
        if path == "/proc/stat":
            return next(seq)
        return files[path]

    return read


def test_hostinfo_reads_pi_health_and_cpu_from_stat_delta():
    t = [0.0]
    read = _fake_fs([
        "cpu  100 0 100 800 0 0 0 0\n",     # 기준(생성자)
        "cpu  175 0 125 900 0 0 0 0\n",     # +100 바쁨 / +100 idle → 50%
    ])
    h = HostInfo("/", clock=lambda: t[0], read=read)
    snap = h.snapshot()
    assert snap["cpu_temp_c"] == 61.2
    assert snap["mem_percent"] == 75.0
    assert snap["cpu_percent"] == 50.0
    assert snap["throttled"] is True, "0x50005 = 저전압 발생"
    assert snap["disk_percent"] is not None and snap["disk_free_gb"] is not None


def test_hostinfo_caches_between_calls_and_never_raises():
    t = [0.0]
    calls = {"n": 0}

    def broken(path: str) -> str:
        calls["n"] += 1
        raise OSError("파이 아님")

    h = HostInfo("/nonexistent", clock=lambda: t[0], read=broken, min_interval_s=5)
    a = h.snapshot()
    assert a["cpu_temp_c"] is None and a["throttled"] is None and a["cpu_percent"] is None
    n = calls["n"]
    t[0] = 3.0
    assert h.snapshot() is a and calls["n"] == n, "5초 안에는 다시 읽지 않는다"
    t[0] = 6.0
    h.snapshot()
    assert calls["n"] > n


def test_worker_heartbeat_carries_host_and_files_carry_camera(tmp_path):
    backend = _tw.FakeBackend()
    images = tmp_path / "images"
    w = Worker(_tw._cfg(tmp_path, max_iterations=1, images_dir=str(images), cam_id="PI-CAM7"),
               client=_tw._client(backend))
    assert w.startup() is True
    w.run_once()
    st = backend.statuses[-1]
    assert isinstance(st.get("host"), dict)
    assert {"cpu_temp_c", "cpu_percent", "mem_percent", "disk_percent",
            "disk_free_gb", "throttled"} <= set(st["host"])
    assert backend.posted, "시뮬레이터 1사이클이면 결과가 올라가야 한다"
    raw = backend.posted[-1]["raw_image_path"]
    assert raw and "_PI-CAM7_" in raw
    assert (images / raw).exists()
    w.shutdown()


# --- 4. 결과 사진 글자: 모드가 안 본 항목은 "안 봤다" -----------------------

def test_overlay_says_not_inspected_instead_of_len_ok_in_surface_mode():
    from vision.imaging.save import NOT_INSPECTED, overlay_lines
    from vision.pipeline import InspectionPipeline

    f = np.full((240, 640, 3), 30, np.uint8)
    f[100:140, 120:520] = 220
    item = _item()
    surf = InspectionPipeline().run(f, item, stage="POST_WASH_SURFACE")
    lines = overlay_lines(surf, item)
    assert lines[0] == f"LEN: {NOT_INSPECTED}", lines
    assert not any(line.startswith("LEN OK") for line in lines), "재지도 않은 길이를 OK 로 찍었다"
    assert any(line.startswith("OIL") for line in lines)

    length = InspectionPipeline().run(f, item, stage="CUT_LENGTH")
    lines = overlay_lines(length, item)
    assert lines[0].startswith("LEN ")
    assert f"SURFACE: {NOT_INSPECTED}" in lines


def test_overlay_still_reports_edge_failure_as_measured_problem():
    """끝단을 못 찾은 것은 '안 봤다' 가 아니다 — 봤는데 실패했다."""
    from aivis_types import LengthResult, Verdict
    from vision.imaging.save import overlay_lines

    v = _verdict().model_copy(update={"length": LengthResult(
        ref_length_mm=125.0, meas_length_mm=None, deviation_mm=None,
        length_verdict=Verdict.NG, edge_detected=False)})
    assert overlay_lines(v, _item())[0].endswith("(no-edge)")
