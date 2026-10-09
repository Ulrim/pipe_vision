"""파이 여러 대 동시 운영을 웹에서 본다(2026-10-09).

도입기업: "동시에 여러 개의 라즈베리파이 + 카메라가 작동할 것이다. 이것을
웹페이지에서 확인하고 싶다."

1. GET /system/stations — 스테이션 카드 한 장에 필요한 것을 한 번에.
2. PUT /inspection/images/{key} — 2호기 사진을 허브(1호기)에 모아야 대시보드가 연다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import heartbeat

_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9"


@pytest.fixture(autouse=True)
def _reset_beats():
    heartbeat.reset()
    yield
    heartbeat.reset()


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    import routers.inspection as insp_mod

    d = tmp_path / "images"
    real = insp_mod.get_settings()

    class _S:
        images_dir = str(d)
        storage_backend = "local"

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(insp_mod, "get_settings", lambda: _S())
    return d


def _item(client, auth, code="HPLIVE"):
    r = client.post("/master/items", headers=auth("qa1"), json={
        "item_code": code, "item_name": code, "ref_length_mm": 430.0,
        "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1,
        "oil_threshold": 0.4, "expected_count": 3,
    })
    assert r.status_code in (201, 409), r.text


def _post(client, **over):
    body = {
        "lot": "LIVE-L1", "item_code": "HPLIVE", "cam_id": "LIVE-CAM1",
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "final_verdict": "OK", "defect_codes": [], "review_flag": False,
        "mes_synced": True, "proc_time_ms": 90, "tube_index": 0,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _stations(client, auth) -> dict:
    r = client.get("/system/stations", headers=auth("op1"))
    assert r.status_code == 200, r.text
    return {s["cam_id"]: s for s in r.json()["stations"]}


def _beat(cam, *, ago_s=1.0, stage="CUT_LENGTH", host=None, **cycle):
    heartbeat.record(
        cam, datetime.now(timezone.utc) - timedelta(seconds=ago_s), stage=stage,
        cycle={"item_code": "HPLIVE", "expected": 3, "detected": 3, "ng": 0,
               "mismatch": False, "proc_time_ms": 120, "error": None, **cycle},
        host=host,
    )


# ---- 1. /system/stations ----------------------------------------------------

def test_requires_login(client):
    assert client.get("/system/stations").status_code == 401


def test_each_station_has_its_own_state_mode_cycle_and_host(client, auth):
    _beat("LIVE-CAM1", stage="CUT_LENGTH",
          host={"cpu_temp_c": 58.0, "disk_percent": 41.0, "throttled": False})
    _beat("LIVE-CAM2", ago_s=95, stage="CRATE_COUNT", detected=18, expected=20,
          mismatch=True, error="frame timeout")
    st = _stations(client, auth)
    a, b = st["LIVE-CAM1"], st["LIVE-CAM2"]
    assert a["state"] == "up" and a["stage"] == "CUT_LENGTH"
    assert a["host"]["cpu_temp_c"] == 58.0 and a["host"]["throttled"] is False
    assert b["state"] == "down" and b["stage"] == "CRATE_COUNT"
    assert (b["detected"], b["expected"], b["mismatch"]) == (18, 20, True)
    assert b["error"] == "frame timeout"
    assert b["host"] is None, "구 워커(자기 상태 미전송)는 null"


def test_station_with_results_but_no_heartbeat_stays_listed_as_down(client, auth):
    """API 재기동 뒤 하트비트가 없어도 최근에 결과를 낸 파이는 '정지' 로 남는다."""
    _item(client, auth)
    _post(client, cam_id="LIVE-GHOST", lot="LIVE-G", inspection_stage="POST_WASH_SURFACE")
    st = _stations(client, auth)
    g = st["LIVE-GHOST"]
    assert g["state"] == "down" and g["last_seen_s"] is None
    assert g["stage"] == "POST_WASH_SURFACE", "모드는 마지막 결과에서라도 채운다"
    assert g["latest"]["lot"] == "LIVE-G"


def test_latest_frame_prefers_the_ng_tube_and_counts_the_frame(client, auth):
    _item(client, auth)
    t = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    _post(client, cam_id="LIVE-B", lot="LIVE-B1", inspected_at=t, tube_index=0)
    ng = _post(client, cam_id="LIVE-B", lot="LIVE-B1", inspected_at=t, tube_index=1,
               final_verdict="NG", defect_codes=["LEN"], meas_length_mm=430.18,
               deviation_mm=0.18, length_verdict="NG", inspection_stage="CUT_LENGTH",
               result_image_path="result/x_NG.jpg")
    _post(client, cam_id="LIVE-B", lot="LIVE-B1", inspected_at=t, tube_index=2)
    latest = _stations(client, auth)["LIVE-B"]["latest"]
    assert latest["id"] == ng["id"], "다발 중 NG 튜브가 대표로 보여야 한다"
    assert (latest["frame_total"], latest["frame_ng"]) == (3, 1)
    assert latest["deviation_mm"] == pytest.approx(0.18)
    assert latest["has_result_image"] is True and latest["has_raw_image"] is False
    lim = latest["limits"]
    assert lim["tol_plus_mm"] == pytest.approx(0.1) and lim["oil_threshold"] == pytest.approx(0.4)
    assert lim["expected_count"] == 3


def test_hour_and_today_windows_are_per_station(client, auth):
    _item(client, auth)
    now = datetime.now(timezone.utc)
    for i, v in enumerate(["OK", "NG", "OK"]):
        _post(client, cam_id="LIVE-W1", lot=f"LIVE-W{i}", final_verdict=v,
              defect_codes=["LEN"] if v == "NG" else [],
              inspected_at=(now - timedelta(minutes=5 + i)).isoformat())
    _post(client, cam_id="LIVE-W2", lot="LIVE-WX", final_verdict="NG", defect_codes=["COUNT"],
          inspected_at=(now - timedelta(minutes=3)).isoformat())
    st = _stations(client, auth)
    assert st["LIVE-W1"]["last_hour"] == {"total": 3, "ng": 1, "ng_rate_pct": 33.3}
    assert st["LIVE-W2"]["last_hour"]["total"] == 1, "다른 스테이션 실적이 섞이면 안 된다"


def test_mode_falls_back_to_station_config(client, auth):
    _item(client, auth)
    client.put("/master/active/stage", headers=auth("op1"),
               json={"item_code": "HPLIVE", "inspection_stage": "CRATE_COUNT", "cam_id": "LIVE-CFG"})
    try:
        assert _stations(client, auth)["LIVE-CFG"]["stage"] == "CRATE_COUNT"
    finally:
        client.delete("/master/stations/LIVE-CFG", headers=auth("op1"))
        client.delete("/master/active", headers=auth("qa1"))


def test_heartbeat_endpoint_stores_cycle_and_host(client):
    r = client.post("/inspection/status", json={
        "cam_id": "LIVE-HB", "item_code": "HPLIVE", "expected": 20, "detected": 19,
        "ts": datetime.now(timezone.utc).isoformat(), "ng": 1, "mismatch": True,
        "proc_time_ms": 140, "stage": "CRATE_COUNT", "error": None,
        "host": {"cpu_temp_c": 71.5, "mem_percent": 40.0, "disk_percent": 88.0,
                 "disk_free_gb": 3.1, "throttled": True, "unknown_key": 1},
    })
    assert r.status_code == 202, r.text
    b = heartbeat.get("LIVE-HB")
    assert b.cycle["detected"] == 19 and b.cycle["mismatch"] is True
    assert b.host["cpu_temp_c"] == 71.5 and b.host["throttled"] is True
    assert "unknown_key" not in b.host


# ---- 2. 사진 업로드 ---------------------------------------------------------

def test_upload_then_open_from_dashboard(client, auth, images_dir):
    """2호기가 올린 사진을 허브 대시보드가 같은 경로로 연다."""
    key = "result/LIVE_HPLIVE_PI-CAM2_20261009101010123_NG.jpg"
    r = client.put(f"/inspection/images/{key}", content=_JPEG,
                   headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 201, r.text
    assert (images_dir / key).read_bytes() == _JPEG
    assert not list((images_dir / "result").glob(".upload-*")), "임시파일이 남으면 안 된다"
    _item(client, auth)
    row = _post(client, cam_id="PI-CAM2", lot="LIVE-UP", result_image_path=key)
    g = client.get(f"/inspection/{row['id']}/images/result", headers=auth("op1"))
    assert g.status_code == 200 and g.content == _JPEG


def test_upload_is_idempotent_overwrite(client, images_dir):
    key = "raw/LIVE_A_PI-CAM2_20261009101010123_OK.jpg"
    client.put(f"/inspection/images/{key}", content=_JPEG)
    second = _JPEG[:-2] + b"1\xff\xd9"
    r = client.put(f"/inspection/images/{key}", content=second)
    assert r.status_code == 201
    assert (images_dir / key).read_bytes() == second


@pytest.mark.parametrize("key", [
    "raw/../../etc/passwd.jpg",
    "raw/sub/dir_OK.jpg",          # 하위 폴더 — 보관기한 정리가 못 본다
    "other/a_OK.jpg",
    "raw/a_OK.png",
    "raw/.hidden_OK.jpg",
])
def test_upload_rejects_bad_keys(client, images_dir, key):
    r = client.put(f"/inspection/images/{key}", content=_JPEG)
    # '..' 는 HTTP 클라이언트가 경로를 정규화해 라우트 자체가 안 맞는다(404).
    assert r.status_code in (400, 404), r.text
    assert not any(p.is_file() for p in images_dir.rglob("*")), "아무것도 쓰면 안 된다"


def test_upload_rejects_non_jpeg(client, images_dir):
    r = client.put("/inspection/images/raw/a_OK.jpg", content=b"GIF89a....")
    assert r.status_code == 400


def test_upload_too_large_is_413(client, images_dir, monkeypatch):
    import routers.inspection as insp_mod
    monkeypatch.setattr(insp_mod, "MAX_UPLOAD_BYTES", 10)
    r = client.put("/inspection/images/raw/a_OK.jpg", content=_JPEG)
    assert r.status_code == 413


def test_upload_refused_when_server_uses_supabase(client, tmp_path, monkeypatch):
    import routers.inspection as insp_mod
    real = insp_mod.get_settings()

    class _S:
        images_dir = str(tmp_path)
        storage_backend = "supabase"

        def __getattr__(self, name):
            return getattr(real, name)

    monkeypatch.setattr(insp_mod, "get_settings", lambda: _S())
    r = client.put("/inspection/images/raw/a_OK.jpg", content=_JPEG)
    assert r.status_code == 409
