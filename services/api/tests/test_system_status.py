"""시스템 모니터링 API (GET /system/status) — 현장 파이 모니터 계약 검증.

파이 터미널 모니터와 대시보드 모니터링 페이지가 같은 응답을 소비하므로
필드명/타입 계약을 고정 검증한다. 핵심 요구:
- operator+ 인증(무인증 401).
- 검사 집계는 최근 1시간 / 오늘(UTC 0시 이후) 경계를 정확히 지킨다.
- 워커 하트비트 경과로 up/stale/down 전이.
- **호스트 지표 읽기가 모두 실패해도 200** (모니터가 죽으면 현장이 눈을 잃는다).
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone

import pytest

from core import heartbeat
from routers import system


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_item(client, auth, code="SYS"):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": code,
            "item_name": f"System monitor {code}",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


def _post_inspection(client, *, at: datetime, lot: str, verdict="OK", **over):
    body = {
        "lot": lot,
        "item_code": "SYS",
        "cam_id": "CAMSYS",
        "inspected_at": at.isoformat(),
        "final_verdict": verdict,
        "defect_codes": ["LEN"] if verdict == "NG" else [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 120,
    }
    body.update(over)
    r = client.post("/inspection", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _status(client, auth, username="op1"):
    r = client.get("/system/status", headers=auth(username))
    assert r.status_code == 200, r.text
    return r.json()


# ---- 권한 ------------------------------------------------------------------


def test_requires_auth(client):
    assert client.get("/system/status").status_code == 401


def test_operator_allowed(client, auth):
    r = client.get("/system/status", headers=auth("op1"))
    assert r.status_code == 200, r.text


# ---- 응답 스키마 계약 -------------------------------------------------------


def test_response_schema_contract(client, auth):
    """계약 필드가 전부 존재하고 타입이 맞는지(다른 팀원 클라이언트와 합의된 형태)."""
    body = _status(client, auth)

    assert set(body) == {
        "ts", "system", "services", "inspection", "active_order", "recent_errors",
    }
    assert isinstance(body["ts"], str)

    sysm = body["system"]
    assert set(sysm) == {
        "cpu_temp_c", "cpu_percent", "load_1m",
        "mem_total_mb", "mem_used_mb", "mem_percent",
        "disk_total_gb", "disk_used_gb", "disk_percent",
        "throttled",
    }
    for key in (k for k in sysm if k != "throttled"):
        assert sysm[key] is None or isinstance(sysm[key], (int, float))
    assert sysm["throttled"] is None or isinstance(sysm["throttled"], bool)

    svc = body["services"]
    assert set(svc) == {"db", "worker", "worker_last_seen_s"}
    assert svc["db"] in ("up", "down")
    assert svc["worker"] in ("up", "stale", "down")
    assert svc["worker_last_seen_s"] is None or isinstance(
        svc["worker_last_seen_s"], (int, float)
    )

    insp = body["inspection"]
    assert set(insp) == {
        "last_hour", "today", "avg_proc_time_ms", "p95_proc_time_ms",
        "last_inspected_at", "mes_pending",
    }
    for window in ("last_hour", "today"):
        w = insp[window]
        assert set(w) == {"total", "ng", "ng_rate_pct"}
        assert isinstance(w["total"], int) and isinstance(w["ng"], int)
        assert isinstance(w["ng_rate_pct"], float)
    assert isinstance(insp["mes_pending"], int)
    assert insp["avg_proc_time_ms"] is None or isinstance(
        insp["avg_proc_time_ms"], (int, float)
    )
    assert insp["p95_proc_time_ms"] is None or isinstance(
        insp["p95_proc_time_ms"], (int, float)
    )
    assert insp["last_inspected_at"] is None or isinstance(
        insp["last_inspected_at"], str
    )

    assert isinstance(body["recent_errors"], list)


# ---- 검사 통계 --------------------------------------------------------------


def test_inspection_windows_and_boundaries(client, auth):
    """최근 1시간/오늘 집계 + 1시간 초과 건 제외 + mes_pending 카운트.

    다른 테스트가 같은 sqlite 를 공유하므로 절대값이 아니라 **증분**으로 검증한다.
    """
    _ensure_item(client, auth)
    before = _status(client, auth)["inspection"]

    now = _now()
    recent = [now - timedelta(minutes=30), now - timedelta(minutes=20),
              now - timedelta(minutes=10)]
    old = now - timedelta(hours=2)  # 1시간 창 밖

    _post_inspection(client, at=recent[0], lot="SYSW1", verdict="OK")
    _post_inspection(client, at=recent[1], lot="SYSW2", verdict="NG")
    _post_inspection(client, at=recent[2], lot="SYSW3", verdict="OK")
    _post_inspection(client, at=old, lot="SYSW4", verdict="NG")

    after = _status(client, auth)["inspection"]

    # 최근 1시간: 3건(NG 1). 2시간 전 NG 는 제외되어야 한다.
    assert after["last_hour"]["total"] - before["last_hour"]["total"] == 3
    assert after["last_hour"]["ng"] - before["last_hour"]["ng"] == 1

    # 오늘(UTC 0시 이후): 2시간 전 건이 오늘이면 4건, 자정을 넘겼으면 3건.
    today_start = datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)
    expected_today = 4 if old >= today_start else 3
    expected_today_ng = 2 if old >= today_start else 1
    assert after["today"]["total"] - before["today"]["total"] == expected_today
    assert after["today"]["ng"] - before["today"]["ng"] == expected_today_ng

    # ng_rate_pct 는 해당 창의 ng/total×100 (소수1자리 반올림)과 일치.
    for window in ("last_hour", "today"):
        w = after[window]
        expected = round(w["ng"] / w["total"] * 100.0, 1) if w["total"] else 0.0
        assert w["ng_rate_pct"] == expected

    # mes_synced=false 로 4건 넣었으므로 백로그 +4.
    assert after["mes_pending"] - before["mes_pending"] == 4

    # 처리속도(최근 1시간 표본)와 마지막 검사시각이 채워진다.
    assert after["avg_proc_time_ms"] is not None
    assert after["p95_proc_time_ms"] is not None
    assert after["last_inspected_at"] is not None


def test_ng_rate_zero_division_guard():
    """검사 0건이면 NG율은 0.0 (0 나눗셈 보호)."""
    assert system._rate_pct(0, 0) == 0.0
    assert system._rate_pct(0, 10) == 0.0
    assert system._rate_pct(3, 10) == 30.0


def test_mes_pending_excludes_synced(client, auth):
    """mes_synced=true 건은 백로그에 잡히지 않는다."""
    _ensure_item(client, auth)
    before = _status(client, auth)["inspection"]["mes_pending"]
    _post_inspection(
        client, at=_now() - timedelta(minutes=5), lot="SYSSYNC", mes_synced=True
    )
    after = _status(client, auth)["inspection"]["mes_pending"]
    assert after == before


# ---- 워커 하트비트 ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_heartbeat():
    """테스트 간 하트비트 상태 격리(다른 테스트의 POST /inspection/status 영향 제거)."""
    heartbeat.reset()
    yield
    heartbeat.reset()


def test_worker_down_without_heartbeat(client, auth):
    """기동 후 하트비트가 없으면 down + worker_last_seen_s=null."""
    svc = _status(client, auth)["services"]
    assert svc["worker"] == "down"
    assert svc["worker_last_seen_s"] is None


def test_worker_up_after_status_post(client, auth):
    """POST /inspection/status 직후 up (워커 하트비트 경로가 연결돼 있다)."""
    _ensure_item(client, auth)
    r = client.post(
        "/inspection/status",
        json={
            "cam_id": "CAMSYS",
            "item_code": "SYS",
            "expected": 1,
            "detected": 1,
            "ts": _now().isoformat(),
        },
    )
    assert r.status_code == 202, r.text

    svc = _status(client, auth)["services"]
    assert svc["worker"] == "up"
    assert svc["worker_last_seen_s"] is not None
    assert svc["worker_last_seen_s"] <= system.WORKER_UP_MAX_S


@pytest.mark.parametrize(
    "elapsed_s, expected",
    [
        (1.0, "up"),
        (system.WORKER_UP_MAX_S - 1, "up"),
        (system.WORKER_UP_MAX_S + 1, "stale"),
        (system.WORKER_STALE_MAX_S - 1, "stale"),
        (system.WORKER_STALE_MAX_S + 5, "down"),
    ],
)
def test_worker_state_transitions(client, auth, elapsed_s, expected):
    """하트비트 시각을 과거로 옮겨 up→stale→down 전이를 확인."""
    heartbeat.record("CAMSYS", ts=_now() - timedelta(seconds=elapsed_s))
    svc = _status(client, auth)["services"]
    assert svc["worker"] == expected
    assert svc["worker_last_seen_s"] >= elapsed_s - 1


# ---- active_order -----------------------------------------------------------


def test_active_order_null_then_set(client, auth):
    """미설정이면 null, PUT /master/active 후 반영된다."""
    _ensure_item(client, auth)
    client.delete("/master/active", headers=auth("qa1"))
    assert _status(client, auth)["active_order"] is None

    r = client.put(
        "/master/active",
        headers=auth("qa1"),
        json={"item_code": "SYS", "lot": "LOT-SYS", "work_order": None},
    )
    assert r.status_code == 200, r.text

    active = _status(client, auth)["active_order"]
    assert active == {"item_code": "SYS", "lot": "LOT-SYS", "work_order": None}

    client.delete("/master/active", headers=auth("qa1"))
    assert _status(client, auth)["active_order"] is None


# ---- recent_errors ----------------------------------------------------------


def test_recent_errors_latest_five(client, auth):
    """sys_log 오류를 최신순 최대 5건 반환한다."""
    from core.logging import write_log
    from aivis_types import LogCategory
    from db.base import SessionLocal

    db = SessionLocal()
    try:
        for i in range(7):
            write_log(
                db,
                category=LogCategory.ERROR,
                level="ERROR",
                message=f"sysmon-err-{i}",
            )
    finally:
        db.close()

    errors = _status(client, auth)["recent_errors"]
    assert len(errors) == 5
    assert [e["message"] for e in errors] == [f"sysmon-err-{i}" for i in (6, 5, 4, 3, 2)]
    assert all(e["ts"] for e in errors)


def test_recent_errors_ignores_non_error_logs(client, auth):
    """user/db 카테고리 INFO 로그는 오류 목록에 섞이지 않는다."""
    from core.logging import write_log
    from aivis_types import LogCategory
    from db.base import SessionLocal

    db = SessionLocal()
    try:
        write_log(db, category=LogCategory.USER, message="sysmon-not-an-error")
    finally:
        db.close()

    errors = _status(client, auth)["recent_errors"]
    assert all(e["message"] != "sysmon-not-an-error" for e in errors)


# ---- 장애 내성(절대 500 금지) ----------------------------------------------


def test_metric_read_failures_still_200(client, auth, monkeypatch):
    """/proc,/sys 읽기·loadavg·디스크가 전부 예외를 던져도 200 + None 필드."""

    def boom(*_a, **_kw):
        raise OSError("simulated read failure")

    monkeypatch.setattr(system, "_read_text", boom)
    monkeypatch.setattr(system.os, "getloadavg", boom, raising=False)
    monkeypatch.setattr(system.shutil, "disk_usage", boom)

    body = _status(client, auth)
    sysm = body["system"]
    assert all(sysm[k] is None for k in sysm), sysm
    # 지표가 없어도 서비스/검사 블록은 정상 산출돼야 한다.
    assert body["services"]["db"] == "up"


def test_db_failure_degrades_without_500(client, auth, monkeypatch):
    """DB 조회가 실패하면 500 대신 db=down + 통계 0/null 로 낮춰 응답한다."""

    def boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(system, "_inspection_stats", boom)

    body = _status(client, auth)
    assert body["services"]["db"] == "down"
    assert body["inspection"]["last_hour"] == {
        "total": 0, "ng": 0, "ng_rate_pct": 0.0,
    }
    assert body["inspection"]["last_inspected_at"] is None
    assert body["active_order"] is None
    assert body["recent_errors"] == []
