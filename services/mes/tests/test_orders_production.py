"""수주·작업지시·생산실적 (docs/MES.md §4·§6.1).

생산실적 규칙: 단계는 더하지 않고 단계별로 센다(같은 튜브가 길이·표면에서 각각 한 행).
최종 판정 = 재확인 결과 > AI 판정. 양품 = 크레이트 개수 확인 합계 > 마지막 단계 양품.
"""
from __future__ import annotations

from .conftest import iface_row, insert_iface, token


def _so(client, **over):
    body = {"customer": "한온시스템", "item_code": "HP12", "qty": 100,
            "order_date": "2026-10-01", "due_date": "2026-10-08"} | over
    r = client.post("/orders", headers=token(), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _wo(client, **over):
    r = client.post("/work-orders", headers=token(), json={"plan_qty": 40} | over)
    assert r.status_code == 201, r.text
    return r.json()


def test_numbers_are_generated_and_order_flows_to_work_order(client):
    so = _so(client)
    assert so["so_no"] == "SO-20261001-001" and so["status"] == "OPEN"
    assert _so(client)["so_no"] == "SO-20261001-002"

    wo = _wo(client, so_id=so["id"], plan_date="2026-10-02")
    assert wo["item_code"] == "HP12", "수주를 고르면 품목을 따라간다"
    assert wo["wo_no"] == "WO-20261002-001" and wo["lot"] == "L-20261002-01"
    assert wo["so_no"] == so["so_no"] and wo["customer"] == "한온시스템"
    assert wo["status"] == "PLANNED" and wo["progress_pct"] == 0.0

    lst = client.get("/orders", headers=token("op1", "operator")).json()
    assert lst[0]["work_order_count"] + lst[1]["work_order_count"] == 1


def test_duplicate_lot_is_rejected(client):
    _wo(client, item_code="HP12", lot="L-DUP")
    r = client.post("/work-orders", headers=token(),
                    json={"item_code": "HP12", "lot": "L-DUP", "plan_qty": 5})
    assert r.status_code == 409


def test_work_order_without_item_is_rejected(client):
    r = client.post("/work-orders", headers=token(), json={"plan_qty": 5})
    assert r.status_code == 422


def test_roles(client):
    op = token("op1", "operator")
    assert client.post("/orders", headers=op, json={
        "customer": "x", "item_code": "HP12", "qty": 1, "order_date": "2026-10-01"}
    ).status_code == 403
    wo = _wo(client, item_code="HP12", lot="L-ROLE")
    # 라인에서 시작·완료는 작업자도 한다.
    r = client.patch(f"/work-orders/{wo['id']}", headers=op, json={"status": "RUNNING"})
    assert r.status_code == 200 and r.json()["status"] == "RUNNING"
    assert r.json()["started_at"] is not None
    # 수량은 품질관리자 이상.
    assert client.patch(f"/work-orders/{wo['id']}", headers=op,
                        json={"plan_qty": 999}).status_code == 403
    r = client.patch(f"/work-orders/{wo['id']}", headers=token(), json={"status": "DONE"})
    assert r.json()["finished_at"] is not None


def test_production_per_stage_with_review_and_crate(client):
    wo = _wo(client, item_code="HP12", lot="L-P", plan_qty=4)
    rows = [
        # 길이 검사 5개: NG 2(그중 1개는 재확인 OK)
        iface_row(1, lot="L-P"),
        iface_row(2, lot="L-P"),
        iface_row(3, lot="L-P"),
        iface_row(4, lot="L-P", final_verdict="NG", defect_codes=["LEN"]),
        iface_row(5, lot="L-P", final_verdict="NG", defect_codes=["LEN"], manual_verdict="OK"),
        # 표면 검사 4개: AI OK 인데 재확인 NG 1(미검), 변색 1
        iface_row(6, lot="L-P", inspection_stage="POST_WASH_SURFACE"),
        iface_row(7, lot="L-P", inspection_stage="POST_WASH_SURFACE"),
        iface_row(8, lot="L-P", inspection_stage="POST_WASH_SURFACE", manual_verdict="NG"),
        iface_row(9, lot="L-P", inspection_stage="POST_WASH_SURFACE", final_verdict="NG",
                  defect_codes=["DIS"]),
    ]
    insert_iface(*rows)
    client.post("/intake/poll", headers=token())

    d = client.get(f"/work-orders/{wo['id']}", headers=token("op1", "operator")).json()
    p = d["production"]
    stages = {s["stage"]: s for s in p["stages"]}
    assert [s["stage"] for s in p["stages"]] == ["CUT_LENGTH", "POST_WASH_SURFACE"]
    assert (stages["CUT_LENGTH"]["inspected"], stages["CUT_LENGTH"]["ai_ng"],
            stages["CUT_LENGTH"]["final_ng"], stages["CUT_LENGTH"]["reviewed"]) == (5, 2, 1, 1)
    assert stages["POST_WASH_SURFACE"]["final_ng"] == 2
    assert stages["POST_WASH_SURFACE"]["by_code"] == {"MANUAL": 1, "DIS": 1}
    assert p["defect_total"] == 3, "단계별 최종 불량의 합"
    assert p["by_code"] == {"LEN": 1, "MANUAL": 1, "DIS": 1}
    # 크레이트가 없으면 마지막 단계(표면) 양품
    assert (p["good_qty"], p["good_source"]) == (2, "stage:POST_WASH_SURFACE")
    assert d["work_order"]["progress_pct"] == 50.0

    # 크레이트 개수 확인이 들어오면 실제로 담긴 수가 실적
    insert_iface(iface_row(10, lot="L-P", inspection_stage="CRATE_COUNT", count_detected=3,
                           count_expected=4, final_verdict="NG", defect_codes=["COUNT"]))
    client.post("/intake/poll", headers=token())
    p = client.get(f"/work-orders/{wo['id']}", headers=token()).json()["production"]
    assert (p["good_qty"], p["good_source"]) == (3, "crate")
    assert (p["crate_count"], p["crate_tubes"], p["crate_mismatch"]) == (1, 3, 1)
    assert p["defect_total"] == 3, "개수 확인 행은 제품 불량에 넣지 않는다"


def test_work_order_matches_by_code_even_with_other_lot(client):
    wo = _wo(client, item_code="HP12", lot="L-X", wo_no="WO-MANUAL-1")
    insert_iface(iface_row(1, lot="L-TYPO", work_order="WO-MANUAL-1"))
    client.post("/intake/poll", headers=token())
    lst = {w["wo_no"]: w for w in client.get("/work-orders", headers=token()).json()}
    assert lst["WO-MANUAL-1"]["inspected_rows"] == 1
    assert client.get(f"/work-orders/{wo['id']}", headers=token()).status_code == 200


def test_unassigned_lots_are_listed(client):
    _wo(client, item_code="HP12", lot="L-KNOWN")
    insert_iface(iface_row(1, lot="L-KNOWN"), iface_row(2, lot="L-ORPHAN"),
                 iface_row(3, lot="L-ORPHAN", final_verdict="NG", defect_codes=["LEN"]))
    client.post("/intake/poll", headers=token())
    un = client.get("/production/unassigned", headers=token("op1", "operator")).json()
    assert [u["lot"] for u in un] == ["L-ORPHAN"]
    assert un[0]["production"]["defect_total"] == 1

    # 그 LOT 으로 작업지시를 만들면 자동으로 이어지고, 결과가 이미 있으니 바로 진행 중이다.
    wo = _wo(client, item_code="HP12", lot="L-ORPHAN")
    assert wo["status"] == "RUNNING" and wo["inspected_rows"] == 2
    assert wo["started_at"].startswith("2026-10-14T00:00:02")
    assert client.get("/production/unassigned", headers=token()).json() == []


def test_status_filter(client):
    a = _wo(client, item_code="HP12", lot="L-A")
    _wo(client, item_code="HP12", lot="L-B")
    client.patch(f"/work-orders/{a['id']}", headers=token(), json={"status": "DONE"})
    active = client.get("/work-orders", headers=token(),
                        params={"status": "PLANNED,RUNNING"}).json()
    assert [w["lot"] for w in active] == ["L-B"]
