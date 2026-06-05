"""e2e 스모크 — 트리거→취득(sim)→파이프라인→판정→저장→조회→KPI 한 흐름.

라이브 카메라/Postgres 없이(SimulatorCamera + sqlite) 전 경로가 한 번 흐르는지
확인한다(§4 7단계 파이프라인의 ①~⑦ 골격).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.harness import dataset as ds
from tests.harness import metrics as mt


@pytest.fixture(scope="module")
def sim_dataset(tmp_path_factory):
    """SimulatorCamera 가 리플레이할 합성 데이터셋(SIDE)."""
    item = mt.make_item_master()
    d = tmp_path_factory.mktemp("e2e_ds")
    ds.write_groundtruth_dataset(
        d, class_counts={"OK": 2, "SCR": 1, "DIS": 1}, item_code=item.item_code
    )
    return d


def test_e2e_full_flow(sim_dataset):
    item = mt.make_item_master()

    # ① 취득(sim): trigger 없이 grab — AIVIS_CAMERA=sim (conftest).
    from vision.acquisition import AcquisitionService, create_camera
    from vision.pipeline import InspectionPipeline, to_inspection_result

    cam = create_camera(dataset_dir=str(sim_dataset), view_filter="SIDE")
    svc = AcquisitionService(camera=cam)
    g = svc.grab_with_retry()
    assert g.ok, f"sim 취득 실패: {g.error}"

    # ②③④ 전처리→길이/표면→종합 판정.
    pipe = InspectionPipeline()
    v = pipe.run(g.frame, item)
    assert v.final_verdict in ("OK", "NG")
    assert v.proc_time_ms >= 0

    ins = to_inspection_result(
        v, lot="E2E-LOT", item_code=item.item_code, cam_id="CAM-E2E",
        inspected_at=datetime.now(timezone.utc), shift="DAY", operator="qa",
    )

    # ⑤⑥ 저장 + MES 스테이징(table 모드) — POST /inspection 내부토큰.
    from fastapi.testclient import TestClient
    from main import app
    from core.config import get_settings
    from core.security import hash_password
    from db.base import SessionLocal, init_db
    from db.models import AppUser
    from mes.watchdog import run_watchdog_once, get_linkage_status

    init_db()
    from tests.harness.runner import seed_item_master
    seed_item_master(item)  # FK(inspection.item_code → item_master) 충족.
    db = SessionLocal()
    try:
        if not db.get(AppUser, "e2e_op"):
            db.add(AppUser(username="e2e_op", pw_hash=hash_password("pw12345"),
                           role="operator", active=True))
            db.commit()
    finally:
        db.close()

    token = get_settings().service_token
    headers = {"X-Service-Token": token} if token else {}

    with TestClient(app) as client:
        r = client.post("/inspection", json=ins.model_dump(mode="json"), headers=headers)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "stored"
        insp_id = r.json()["id"]

        # ⑦ 조회 (operator 로그인 가드).
        rlogin = client.post("/auth/login", json={"username": "e2e_op", "password": "pw12345"})
        assert rlogin.status_code == 200
        h = {"Authorization": f"Bearer {rlogin.json()['access_token']}"}

        rget = client.get(f"/inspection/{insp_id}", headers=h)
        assert rget.status_code == 200, rget.text
        assert rget.json()["lot"] == "E2E-LOT"

        # 이미지 경로 조회(M8).
        rimg = client.get(f"/inspection/{insp_id}/images", headers=h)
        assert rimg.status_code == 200

        # KPI 요약(당월 산출식 동작) — operator+.
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        rk = client.get("/kpi/summary", headers=h, params={"period": period})
        assert rk.status_code == 200, rk.text
        assert rk.json()["total_inspected"] >= 1

    # MES 연계 워치독 1회 → 연계 반영.
    db = SessionLocal()
    try:
        run_watchdog_once(db)
        status = get_linkage_status(db)
        assert status.total >= 1
    finally:
        db.close()
