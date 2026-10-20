"""라이브 뷰·1장 촬영 중계 (2026-10-20).

1. 화면이 켜면 워커 하트비트 응답에 live=true, 끄면 false.
2. 워커가 올린 프레임을 화면이 받는다(지표·초점 최고값 포함).
3. 1장 촬영 요청은 워커 응답에 한 번만 실린다(라이브 뷰가 꺼져 있어도).
4. 화면이 안 보면 기한이 지나 멈춘다 — 영상이 회선을 계속 쓰지 않는다.
5. 권한: 화면 쪽은 로그인(작업자 이상), 프레임 업로드는 서비스 토큰.
"""
from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from core import live


@pytest.fixture(autouse=True)
def _clean_live():
    live.reset()
    yield
    live.reset()


def _jpeg(v: int = 120) -> str:
    ok, buf = cv2.imencode(".jpg", np.full((48, 64), v, np.uint8))
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def _beat(client, cam="PI-L1"):
    r = client.post("/inspection/status", json={
        "cam_id": cam, "item_code": "HP12", "expected": 1, "detected": 0, "ng": 0, "mismatch": False,
        "proc_time_ms": 0, "ts": "2037-01-01T00:00:00+00:00"})
    assert r.status_code == 202
    return r.json()


def test_start_stop_reaches_worker_through_heartbeat(client, auth):
    assert _beat(client)["live"] is False
    r = client.post("/system/live/PI-L1/start", headers=auth("op1"))
    assert r.status_code == 200 and r.json()["requested"] is True
    assert _beat(client)["live"] is True
    assert _beat(client, "PI-OTHER")["live"] is False, "다른 카메라는 켜지 않는다"
    client.post("/system/live/PI-L1/stop", headers=auth("op1"))
    assert _beat(client)["live"] is False


def test_frame_relay_with_metrics_and_focus_peak(client, auth):
    client.post("/system/live/PI-L1/start", headers=auth("op1"))
    for f in (10.0, 42.5, 30.0):
        r = client.put("/system/live/PI-L1/frame", json={
            "jpeg_b64": _jpeg(), "width": 64, "height": 48, "focus": f, "brightness": 120.0,
            "saturated_pct": 0.5, "dark_pct": 0.0, "roi": [0.1, 0.2, 0.9, 0.8]})
        assert r.status_code == 200 and r.json() == {"live": True, "capture": False}
    st = client.get("/system/live/PI-L1", headers=auth("op1")).json()
    assert st["streaming"] is True and st["seq"] == 3
    assert st["focus"] == 30.0 and st["focus_peak"] == 42.5, "최고값은 지금까지의 최대"
    assert st["roi"] == [0.1, 0.2, 0.9, 0.8] and st["width"] == 64
    img = client.get("/system/live/PI-L1/frame.jpg", headers=auth("op1"))
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert img.content[:2] == b"\xff\xd8" and img.headers["x-frame-seq"] == "3"
    assert img.headers["cache-control"] == "no-store"


def test_capture_is_delivered_once_even_without_live(client, auth):
    r = client.post("/system/live/PI-L1/capture", headers=auth("op1"))
    assert r.status_code == 202
    assert _beat(client)["capture"] is True
    assert _beat(client)["capture"] is False, "한 번만"
    logs = client.get("/logs", params={"category": "user"}, headers=auth("admin1")).json()
    rows = logs["items"] if isinstance(logs, dict) else logs
    assert any("live.capture" in (x.get("message") or "") for x in rows)


def test_view_expires_when_nobody_watches(client, auth, monkeypatch):
    t = [1_000_000.0]
    monkeypatch.setattr(live, "_clock", lambda: t[0])
    client.post("/system/live/PI-L1/start", headers=auth("op1"))
    t[0] += live.START_TTL_S - 1
    client.get("/system/live/PI-L1", headers=auth("op1"))  # 보고 있다 → 연장
    t[0] += live.VIEW_TTL_S - 1
    assert live.wants("PI-L1")[0] is True
    t[0] += 2  # 화면을 닫았다
    assert live.wants("PI-L1")[0] is False
    # 끝난 뒤 상태를 읽어도 다시 켜지지 않는다.
    client.get("/system/live/PI-L1", headers=auth("op1"))
    assert live.wants("PI-L1")[0] is False


def test_session_cap(client, auth, monkeypatch):
    t = [2_000_000.0]
    monkeypatch.setattr(live, "_clock", lambda: t[0])
    client.post("/system/live/PI-L1/start", headers=auth("op1"))
    while t[0] < 2_000_000.0 + live.MAX_SESSION_S + 5:
        t[0] += 5
        client.get("/system/live/PI-L1", headers=auth("op1"))
    assert live.wants("PI-L1")[0] is False, "켜 둔 채 잊어도 상한 시간 뒤 멈춘다"


def test_auth_and_validation(client, auth):
    assert client.post("/system/live/PI-L1/start").status_code == 401
    assert client.get("/system/live/PI-L1").status_code == 401
    assert client.get("/system/live/PI-L1/frame.jpg", headers=auth("op1")).status_code == 404
    bad = client.put("/system/live/PI-L1/frame", json={"jpeg_b64": "@@@"})
    assert bad.status_code == 422
    notjpeg = client.put("/system/live/PI-L1/frame",
                         json={"jpeg_b64": base64.b64encode(b"hello").decode()})
    assert notjpeg.status_code == 422


def test_frame_upload_requires_service_token_when_configured(client, monkeypatch):
    import core.security as sec

    real = sec.get_settings()

    class _S:
        service_token = "svc-token"

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(sec, "get_settings", lambda: _S())
    body = {"jpeg_b64": _jpeg()}
    assert client.put("/system/live/PI-L1/frame", json=body).status_code == 401
    ok = client.put("/system/live/PI-L1/frame", json=body, headers={"X-Service-Token": "svc-token"})
    assert ok.status_code == 200


def test_reopen_starts_clean_and_late_frames_are_dropped(client, auth):
    """닫는 순간 날아온 장면이 남아, 다시 열면 옛 장면이 '끊김' 으로 보이던 문제."""
    client.post("/system/live/PI-L1/start", headers=auth("op1"))
    body = {"jpeg_b64": _jpeg(), "focus": 50.0, "brightness": 99.0}
    client.put("/system/live/PI-L1/frame", json=body)
    client.post("/system/live/PI-L1/stop", headers=auth("op1"))
    late = client.put("/system/live/PI-L1/frame", json=body)
    assert late.json() == {"live": False, "capture": False}
    assert client.get("/system/live/PI-L1/frame.jpg", headers=auth("op1")).status_code == 404
    st = client.post("/system/live/PI-L1/start", headers=auth("op1")).json()
    assert st["seq"] == 1 and st["brightness"] is None and st["focus_peak"] is None
    assert st["frame_at"] is None and st["streaming"] is False
    client.put("/system/live/PI-L1/frame", json=body)
    assert client.get("/system/live/PI-L1", headers=auth("op1")).json()["seq"] == 2


def test_capture_still_delivered_through_frame_reply_when_not_watching(client, auth):
    client.post("/system/live/PI-L1/capture", headers=auth("op1"))
    r = client.put("/system/live/PI-L1/frame", json={"jpeg_b64": _jpeg()})
    assert r.json() == {"live": False, "capture": True}
