"""검사 워커 런타임 루프 테스트 (CLAUDE.md §4).

실 네트워크 없이 httpx.MockTransport 로 backend(/health, /master/items, /auth/login,
/inspection) 계약을 흉내내어 검증한다:
  - python -m worker import 가 죽지 않음(부트스트랩).
  - 합성 데이터셋 자립(AIVIS_DATASET_DIR 미설정/빈 폴더).
  - master GET 인증 가드(operator+) → 로그인 폴백으로 Bearer 확보.
  - 검사 1~N 루프가 실제로 POST 되어 201 을 받음(proc_time 포함).
  - API 다운 시 죽지 않고 재시도(타임아웃 후 graceful False).
  - SIGTERM graceful 종료.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.worker.client import ApiClient  # noqa: E402
from vision.worker.config import WorkerConfig  # noqa: E402
from vision.worker.dataset import ensure_dataset  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402


# --- 가짜 backend (httpx.MockTransport) ---
class FakeBackend:
    """services/api 계약을 흉내내는 인메모리 stub.

    require_service_token=True 면 POST /inspection 은 X-Service-Token/Bearer 일치 필요.
    master_requires_auth=True 면 GET /master/items 는 Bearer 필요(operator+ 가드 모사).
    """

    def __init__(
        self,
        *,
        item_code: str = "HP12",
        require_service_token: str | None = None,
        master_requires_auth: bool = True,
        healthy_after: int = 0,
    ) -> None:
        self.item_code = item_code
        self.require_service_token = require_service_token
        self.master_requires_auth = master_requires_auth
        self.healthy_after = healthy_after  # 이 횟수만큼 health 를 'down'으로.
        self.health_calls = 0
        self.posted: list[dict] = []
        self.login_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            self.health_calls += 1
            if self.health_calls <= self.healthy_after:
                return httpx.Response(503, json={"status": "degraded"})
            return httpx.Response(200, json={"status": "ok", "db": "up"})

        if path == "/auth/login":
            self.login_calls += 1
            body = json.loads(request.content or b"{}")
            if body.get("username") == "admin" and body.get("password") == "admin1234":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "JWT-OP-TOKEN",
                        "role": "admin",
                        "username": "admin",
                    },
                )
            return httpx.Response(401, json={"detail": "bad creds"})

        if path == f"/master/items/{self.item_code}":
            auth = request.headers.get("Authorization", "")
            if self.master_requires_auth and auth != "Bearer JWT-OP-TOKEN":
                # operator+ JWT 가드: service token Bearer 는 거부(JWT 디코드 실패).
                return httpx.Response(401, json={"detail": "인증 토큰 없음"})
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
            if self.require_service_token is not None:
                tok = request.headers.get("X-Service-Token") or request.headers.get(
                    "Authorization", ""
                ).removeprefix("Bearer ")
                if tok != self.require_service_token:
                    return httpx.Response(401, json={"detail": "service token 필요"})
            body = json.loads(request.content)
            self.posted.append(body)
            return httpx.Response(
                201, json={"status": "stored", "id": len(self.posted)}
            )

        return httpx.Response(404, json={"detail": f"no route {path}"})


def _client(backend: FakeBackend, **kw) -> ApiClient:
    transport = httpx.MockTransport(backend.handler)
    return ApiClient("", transport=transport, **kw)


def _cfg(tmp_path: Path, **overrides) -> WorkerConfig:
    base = dict(
        camera_mode="sim",
        dataset_dir=None,
        api_url="",
        item_code="HP12",
        cam_id="CAM1",
        lot="LOTTEST",
        interval_ms=0,
        api_wait_timeout_s=5,
        item_wait_timeout_s=5,
        ready_file=str(tmp_path / "vision_ready"),
        max_iterations=3,
    )
    base.update(overrides)
    return WorkerConfig(**base)


# --- import 부트스트랩 ---
def test_worker_module_imports():
    import importlib

    mod = importlib.import_module("vision.worker")
    assert hasattr(mod, "Worker") and hasattr(mod, "main")


# --- 데이터셋 자립 ---
def test_ensure_dataset_generates_when_missing(tmp_path):
    target = tmp_path / "empty_ds"
    out = ensure_dataset(str(target))
    imgs = list(Path(out).rglob("*.jpg"))
    assert imgs, "합성 이미지가 생성되어야 한다"


# --- health 폴링 ---
class _FakeClock:
    """sleep_fn 호출 시 단조 시계를 진행시키는 결정적 가짜 클럭."""

    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, dt) -> None:
        self.t += float(dt)

    def now(self) -> float:
        return self.t


def test_wait_for_api_recovers_after_downtime(monkeypatch):
    import vision.worker.client as cm

    clock = _FakeClock()
    monkeypatch.setattr(cm.time, "monotonic", clock.now)
    backend = FakeBackend(healthy_after=2)
    client = _client(backend)
    assert client.wait_for_api(timeout_s=10, sleep_fn=clock.sleep) is True
    assert backend.health_calls >= 3


def test_wait_for_api_times_out_when_down(monkeypatch):
    import vision.worker.client as cm

    clock = _FakeClock()
    monkeypatch.setattr(cm.time, "monotonic", clock.now)
    backend = FakeBackend(healthy_after=10_000)
    client = _client(backend)
    assert client.wait_for_api(timeout_s=2, sleep_fn=clock.sleep) is False
    assert clock.now() >= 2


# --- master 인증 폴백 ---
def test_fetch_item_uses_login_fallback_when_get_guarded():
    backend = FakeBackend(master_requires_auth=True)
    client = _client(backend, service_token="svc-xyz")
    item = client.fetch_item("HP12", timeout_s=5, sleep_fn=lambda *_: None)
    assert item is not None and item.item_code == "HP12"
    assert backend.login_calls >= 1  # 로그인 폴백이 일어났다.


# --- POST /inspection 단위 ---
def test_post_inspection_attaches_service_token():
    backend = FakeBackend(require_service_token="svc-xyz", master_requires_auth=False)
    client = _client(backend, service_token="svc-xyz")
    item = client.fetch_item("HP12", timeout_s=5, sleep_fn=lambda *_: None)
    from vision.pipeline import InspectionPipeline, to_inspection_result
    from vision.tools.gen_synthetic import make_image

    img, _ = make_image("OK")
    verdict = InspectionPipeline().run(img, item)
    result = to_inspection_result(
        verdict, lot="L1", item_code="HP12", cam_id="CAM1"
    )
    ok, detail = client.post_inspection(result)
    assert ok, detail
    assert backend.posted and backend.posted[0]["item_code"] == "HP12"
    assert backend.posted[0]["proc_time_ms"] >= 0


def test_post_inspection_rejects_wrong_token():
    backend = FakeBackend(require_service_token="right", master_requires_auth=False)
    client = _client(backend, service_token="wrong")
    item = client.fetch_item("HP12", timeout_s=5, sleep_fn=lambda *_: None)
    from vision.pipeline import InspectionPipeline, to_inspection_result
    from vision.tools.gen_synthetic import make_image

    verdict = InspectionPipeline().run(make_image("OK")[0], item)
    result = to_inspection_result(verdict, lot="L1", item_code="HP12", cam_id="CAM1")
    ok, detail = client.post_inspection(result)
    assert ok is False and "401" in detail


def test_post_inspection_survives_network_error():
    def boom(_req):
        raise httpx.ConnectError("refused")

    client = ApiClient("", transport=httpx.MockTransport(boom))
    from vision.pipeline import InspectionPipeline, to_inspection_result
    from vision.tools.gen_synthetic import make_image
    from aivis_types import ItemMaster

    item = ItemMaster(
        item_code="HP12",
        item_name="x",
        ref_length_mm=125.0,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=0.25,
    )
    verdict = InspectionPipeline().run(make_image("OK")[0], item)
    result = to_inspection_result(verdict, lot="L1", item_code="HP12", cam_id="CAM1")
    ok, detail = client.post_inspection(result)
    assert ok is False and "예외" in detail


# --- 전체 루프 통합(스모크) ---
def test_worker_runs_loops_and_posts(tmp_path):
    backend = FakeBackend(
        require_service_token="svc-xyz", master_requires_auth=True
    )
    client = _client(backend, service_token="svc-xyz")
    cfg = _cfg(tmp_path, service_token="svc-xyz", max_iterations=3)
    worker = Worker(cfg, client=client)
    rc = worker.run()
    assert rc == 0
    assert worker.success == 3, f"3 루프 모두 적재되어야 한다: {worker.__dict__}"
    assert len(backend.posted) == 3
    # readiness 파일은 루프 중 생성되고 종료 시 정리된다.
    assert not Path(cfg.ready_file).exists()


def test_worker_ready_file_written_during_loop(tmp_path):
    backend = FakeBackend(master_requires_auth=True)
    client = _client(backend)
    cfg = _cfg(tmp_path, max_iterations=1)
    worker = Worker(cfg, client=client)
    assert worker.startup() is True
    worker._write_ready()
    assert Path(cfg.ready_file).exists()
    worker.run_once()
    assert len(backend.posted) == 1
    worker.shutdown()
    assert not Path(cfg.ready_file).exists()


# --- API 다운 시 기동 중단(죽지 않음) ---
def test_worker_startup_aborts_gracefully_when_api_down(tmp_path):
    backend = FakeBackend(healthy_after=10_000)
    client = _client(backend)
    cfg = _cfg(tmp_path, api_wait_timeout_s=1)
    worker = Worker(cfg, client=client)
    # sleep 을 no-op 로 만들기 위해 client.wait_for_api 의 sleep_fn 은 내부 time.sleep.
    # api_wait_timeout_s=1 이라 실제로 ~1s 안에 False 반환 → run() 은 1 반환.
    rc = worker.run()
    assert rc == 1


# --- SIGTERM graceful ---
def test_worker_request_stop_breaks_loop(tmp_path):
    backend = FakeBackend(master_requires_auth=True)
    client = _client(backend)
    cfg = _cfg(tmp_path, max_iterations=0)  # 무한이지만 stop 으로 종료.
    worker = Worker(cfg, client=client)
    assert worker.startup()
    worker._write_ready()

    # run_once 를 몇 번 돌린 뒤 stop 을 트리거하는 효과를 직접 검증.
    worker.run_once()
    worker.request_stop()
    assert worker._stop is True
    worker.shutdown()
    assert not Path(cfg.ready_file).exists()
