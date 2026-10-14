"""AIVIS → MES 종단 연계 (2026-10-14).

인수 지표 4 '데이터 저장 & MES 연계율 100%' 를 **상대가 실제로 받았는지**로 보인다.
종전엔 MES 가 없어 연계율이 'AIVIS 자기 테이블 적재율' 이었다(10/10 점검 지적).

흐름: AIVIS POST /inspection → mes_quality_if 적재 → MES 가 같은 DB 의 연계 테이블을
폴링해 품질이력에 적재하고 consumed 표시 → AIVIS KPI 의 'MES 수신 확인' 이 올라간다.
재확인(AI NG → 사람 OK)은 연계 행을 되돌려 MES 생산실적까지 바뀐다.
로그인은 하나 — AIVIS 가 발급한 토큰으로 MES 를 그대로 쓴다.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_MES_DIR = Path(__file__).resolve().parents[2] / "services" / "mes"
if str(_MES_DIR) not in sys.path:
    sys.path.insert(0, str(_MES_DIR))

# MES 설정은 import 시점에 굳는다 — 먼저 정한다. 인터페이스 DB = AIVIS DB(독립형 파이와 같은 구성).
os.environ["MES_DATABASE_URL"] = (
    f"sqlite:///{os.path.join(tempfile.mkdtemp(prefix='aivis_mes_e2e_'), 'mes.db')}"
)
os.environ["MES_AIVIS_DB_URL"] = os.environ["DATABASE_URL"]
os.environ["MES_POLLER_ENABLED"] = "false"

_T0 = datetime(2036, 5, 2, 1, 0, tzinfo=timezone.utc)
_LOT = "E2E-MES-L1"


def _ins(i: int, **over) -> dict:
    body = {
        "lot": _LOT, "work_order": "WO-E2E-1", "item_code": "HPE2E", "cam_id": "E2E-CAM",
        "inspected_at": (_T0 + timedelta(seconds=i)).isoformat(), "final_verdict": "OK",
        "defect_codes": [], "review_flag": False, "mes_synced": False, "tube_index": i,
        "inspection_stage": "CUT_LENGTH",
    }
    body.update(over)
    return body


def test_aivis_to_mes_end_to_end():
    from fastapi.testclient import TestClient

    from core.config import get_settings
    from core.security import hash_password
    from db.base import SessionLocal, init_db
    from db.models import AppUser
    from main import app as aivis_app

    from mes_app.main import app as mes_app

    init_db()
    db = SessionLocal()
    try:
        if not db.get(AppUser, "e2e_qa"):
            db.add(AppUser(username="e2e_qa", pw_hash=hash_password("pw12345"),
                           role="quality", active=True))
            db.commit()
    finally:
        db.close()
    svc = get_settings().service_token
    internal = {"X-Service-Token": svc} if svc else {}

    with TestClient(aivis_app) as aivis, TestClient(mes_app) as mes:
        login = aivis.post("/auth/login", json={"username": "e2e_qa", "password": "pw12345"})
        h = {"Authorization": f"Bearer {login.json()['access_token']}"}
        r = aivis.post("/master/items", headers=h, json={
            "item_code": "HPE2E", "item_name": "e2e", "ref_length_mm": 250.0,
            "tol_plus_mm": 0.1, "tol_minus_mm": 0.1, "px_to_mm_scale": 0.1,
            "expected_count": 3})
        assert r.status_code in (201, 409), r.text

        # MES: 작업지시(같은 토큰 — 로그인 하나)
        r = mes.post("/work-orders", headers=h, json={
            "wo_no": "WO-E2E-1", "item_code": "HPE2E", "lot": _LOT, "plan_qty": 3})
        assert r.status_code == 201, r.text
        wo_id = r.json()["id"]

        # AIVIS: 길이 검사 3개(NG 1) + 크레이트 개수 확인 1판(3개)
        ng_id = None
        for i, over in enumerate([{}, {"final_verdict": "NG", "defect_codes": ["LEN"],
                                       "review_flag": True}, {}]):
            r = aivis.post("/inspection", headers=internal, json=_ins(i, **over))
            assert r.status_code == 201, r.text
            if over:
                ng_id = r.json()["id"]
        r = aivis.post("/inspection", headers=internal, json=_ins(
            10, tube_index=0, inspection_stage="CRATE_COUNT", count_detected=3,
            count_expected=3))
        assert r.status_code == 201, r.text

        kpi = aivis.get("/kpi/summary", headers=h, params={"period": "2036-05"}).json()
        assert kpi["mes_mode"] == "table" and kpi["mes_consumed_count"] == 0, "아직 안 가져갔다"

        # MES 가 가져간다
        res = mes.post("/intake/poll", headers=h).json()
        # 같은 DB 에 다른 시험이 남긴 행도 있을 수 있다 — 우리 4건 이상, 가져간 것은 전부 표시.
        assert res["error"] is None and res["stored"] >= 4
        assert res["marked"] == res["stored"]

        kpi = aivis.get("/kpi/summary", headers=h, params={"period": "2036-05"}).json()
        assert kpi["stored_count"] == 4
        assert kpi["mes_consumed_count"] == 4, "MES 수신 확인 = 상대가 실제로 가져간 건수"
        assert kpi["storage_mes_rate_pct"] == 100.0

        d = mes.get(f"/work-orders/{wo_id}", headers=h).json()
        p = d["production"]
        assert d["work_order"]["status"] == "RUNNING", "첫 검사결과로 자동 시작"
        assert (p["good_qty"], p["good_source"], p["defect_total"]) == (3, "crate", 1)
        assert p["stages"][0]["stage"] == "CUT_LENGTH" and p["stages"][0]["final_ng"] == 1

        # 작업자 재확인: 그 NG 는 사실 양품 → MES 실적의 불량에서 빠져야 한다
        r = aivis.patch(f"/inspection/{ng_id}/review", headers=h, json={"manual_verdict": "OK"})
        assert r.status_code == 200, r.text
        res = mes.post("/intake/poll", headers=h).json()
        assert (res["updated"], res["marked"]) == (1, 1)
        p = mes.get(f"/work-orders/{wo_id}", headers=h).json()["production"]
        assert p["defect_total"] == 0 and p["stages"][0]["reviewed"] == 1

        st = mes.get("/intake/status", headers=h).json()
        assert st["interface_pending"] == 0 and st["records_total"] >= 4
