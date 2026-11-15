"""기준정보·공정 진행·자재/재고·설비 가동 (docs/MES.md §11)."""
from __future__ import annotations

from datetime import timedelta

from mes_app.db import SessionLocal
from mes_app.models import EquipmentEvent
from mes_app.plant import now_utc

from .conftest import insert_iface, iface_row, token

OP = token("op1", "operator")
QA = token()


def _item(client, code, name, item_type="RAW", **over):
    r = client.post("/items", headers=QA, json={
        "item_code": code, "name": name, "item_type": item_type} | over)
    assert r.status_code == 201, r.text
    return r.json()


def _wo(client, **over):
    r = client.post("/work-orders", headers=QA, json={
        "item_code": "HP12", "lot": "L-1", "plan_qty": 100} | over)
    assert r.status_code == 201, r.text
    return r.json()


def _ops(client, wo_id):
    return client.get(f"/work-orders/{wo_id}/operations", headers=OP).json()


# ---- 기준정보 ----------------------------------------------------------------------

def test_items_crud_and_roles(client):
    assert client.post("/items", headers=OP, json={
        "item_code": "AL-TUBE", "name": "원자재 튜브"}).status_code == 403
    it = _item(client, "AL-TUBE", "원자재 튜브", unit="m", safety_stock=500)
    assert (it["item_type"], it["unit"], it["active"]) == ("RAW", "m", True)
    assert client.post("/items", headers=QA, json={
        "item_code": "AL-TUBE", "name": "x"}).status_code == 409
    _item(client, "HP12", "헤더 파이프 12", "FG")

    assert [i["item_code"] for i in client.get(
        "/items", headers=OP, params={"item_type": "raw"}).json()] == ["AL-TUBE"]
    r = client.patch("/items/AL-TUBE", headers=QA, json={"active": False, "safety_stock": 300})
    assert (r.json()["active"], r.json()["safety_stock"]) == (False, 300)
    assert client.get("/items", headers=OP, params={"active": True}).json()[0]["item_code"] == "HP12"
    assert client.patch("/items/NOPE", headers=QA, json={"name": "x"}).status_code == 404


def test_bom_replace_and_validation(client):
    _item(client, "AL-TUBE", "원자재 튜브", unit="m")
    _item(client, "BOX", "포장 박스", "SUB")
    r = client.put("/bom/HP12", headers=QA, json={"lines": [
        {"component_code": "AL-TUBE", "qty_per": 0.26, "scrap_pct": 2},
        {"component_code": "BOX", "qty_per": 0.01}]})
    assert r.status_code == 200, r.text
    assert [(b["component_code"], b["component_name"], b["unit"]) for b in r.json()] == [
        ("AL-TUBE", "원자재 튜브", "m"), ("BOX", "포장 박스", "EA")]
    # 통째로 교체 — 한 줄만 남는다
    client.put("/bom/HP12", headers=QA, json={"lines": [
        {"component_code": "AL-TUBE", "qty_per": 0.25}]})
    assert len(client.get("/bom", headers=OP, params={"parent": "HP12"}).json()) == 1

    bad = lambda lines: client.put("/bom/HP12", headers=QA, json={"lines": lines}).status_code  # noqa: E731
    assert bad([{"component_code": "NOPE", "qty_per": 1}]) == 404
    assert bad([{"component_code": "HP12", "qty_per": 1}]) == 422
    assert bad([{"component_code": "BOX", "qty_per": 1}, {"component_code": "BOX", "qty_per": 2}]) == 422
    assert bad([{"component_code": "BOX", "qty_per": 0}]) == 422
    assert len(client.get("/bom", headers=OP).json()) == 1, "거절된 저장은 기존 BOM 을 건드리지 않는다"
    assert client.put("/bom/HP12", headers=OP, json={"lines": []}).status_code == 403
    assert client.put("/bom/HP12", headers=QA, json={"lines": []}).json() == []


def test_default_processes_and_routing_resolution(client):
    procs = client.get("/processes", headers=OP).json()
    assert [p["code"] for p in procs] == ["CUT", "DEBURR", "WASH", "INSPECT", "PACK"]
    assert [p["code"] for p in procs if p["auto_from_inspection"]] == ["INSPECT"]

    # 라우팅이 없으면 사용 중인 공정 전부
    r = client.get("/routings/HP12", headers=OP).json()
    assert r["source"] == "processes" and [s["seq"] for s in r["steps"]] == [10, 20, 30, 40, 50]

    client.post("/equipment", headers=QA, json={"code": "SAW-1", "name": "톱 절단기 1", "process_code": "CUT"})
    d = client.put("/routings/*", headers=QA, json={"steps": [
        {"process_code": "CUT", "equipment_code": "SAW-1", "std_sec_per_ea": 1.5},
        {"process_code": "INSPECT"}, {"process_code": "PACK"}]}).json()
    assert d["source"] == "default" and [s["process_code"] for s in d["steps"]] == ["CUT", "INSPECT", "PACK"]
    assert client.get("/routings/HP12", headers=OP).json()["source"] == "default"

    own = client.put("/routings/HP12", headers=QA, json={"steps": [
        {"process_code": "CUT"}, {"process_code": "WASH"}, {"process_code": "INSPECT"}]}).json()
    assert own["source"] == "item" and own["steps"][1]["process_name"] == "세척"
    assert client.get("/routings/HP20", headers=OP).json()["source"] == "default"
    # 전용 라우팅을 지우면 공통으로 돌아간다
    assert client.put("/routings/HP12", headers=QA, json={"steps": []}).json()["source"] == "default"

    assert client.put("/routings/HP12", headers=QA, json={
        "steps": [{"process_code": "NOPE"}]}).status_code == 404
    assert client.put("/routings/HP12", headers=QA, json={
        "steps": [{"process_code": "CUT", "equipment_code": "NOPE"}]}).status_code == 404
    assert client.put("/routings/HP12", headers=OP, json={"steps": []}).status_code == 403

    # 쓰지 않는 공정은 '공정 전부' 에서 빠진다
    client.put("/routings/*", headers=QA, json={"steps": []})
    client.patch("/processes/DEBURR", headers=QA, json={"active": False})
    assert [s["process_code"] for s in client.get("/routings/HP12", headers=OP).json()["steps"]] == [
        "CUT", "WASH", "INSPECT", "PACK"]
    assert client.post("/processes", headers=QA, json={"code": "cut", "name": "x", "seq": 1}).status_code == 409


# ---- 공정 진행 ----------------------------------------------------------------------

def test_operations_copied_from_routing_at_creation(client):
    client.put("/routings/HP12", headers=QA, json={"steps": [
        {"process_code": "CUT"}, {"process_code": "INSPECT"}]})
    wo = _wo(client)
    ops = _ops(client, wo["id"])
    assert [(o["seq"], o["process_code"], o["status"], o["auto"]) for o in ops] == [
        (10, "CUT", "PENDING", False), (20, "INSPECT", "PENDING", True)]
    # 그 뒤 라우팅을 바꿔도 진행 중인 지시는 그대로
    client.put("/routings/HP12", headers=QA, json={"steps": [{"process_code": "PACK"}]})
    assert [o["process_code"] for o in _ops(client, wo["id"])] == ["CUT", "INSPECT"]
    assert client.get("/work-orders/99999/operations", headers=OP).status_code == 404


def test_operation_flow_drives_work_order_and_equipment(client):
    client.post("/equipment", headers=QA, json={"code": "SAW-1", "name": "톱 절단기 1"})
    client.post("/equipment", headers=QA, json={"code": "SAW-2", "name": "톱 절단기 2"})
    client.put("/routings/HP12", headers=QA, json={"steps": [
        {"process_code": "CUT", "equipment_code": "SAW-1"}, {"process_code": "INSPECT"}]})
    wo = _wo(client)
    cut, insp = _ops(client, wo["id"])
    base = f"/work-orders/{wo['id']}/operations"

    ops = client.post(f"{base}/{cut['id']}/start", headers=OP, json={}).json()
    assert (ops[0]["status"], ops[0]["equipment_code"], ops[0]["worker"]) == ("RUNNING", "SAW-1", "op1")
    assert client.get(f"/work-orders/{wo['id']}", headers=OP).json()["work_order"]["status"] == "RUNNING"
    eq = {e["code"]: e for e in client.get("/equipment", headers=OP).json()}
    assert (eq["SAW-1"]["state"], eq["SAW-1"]["wo_no"]) == ("RUN", wo["wo_no"])
    assert eq["SAW-2"]["state"] is None

    assert client.post(f"{base}/{cut['id']}/start", headers=OP, json={}).status_code == 409
    # 수량 없이 완료할 수 없다(검사 공정이 아니다)
    assert client.post(f"{base}/{cut['id']}/complete", headers=OP, json={}).status_code == 422
    ops = client.post(f"{base}/{cut['id']}/complete", headers=OP,
                      json={"good_qty": 98, "defect_qty": 2}).json()
    assert (ops[0]["status"], ops[0]["good_qty"], ops[0]["defect_qty"]) == ("DONE", 98, 2)
    assert ops[0]["ended_at"] is not None
    assert client.get("/equipment", headers=OP).json()[0]["state"] == "IDLE"

    # 검사 공정: 수량을 비우고 완료 — 값은 검사결과에서 온다
    insert_iface(*[iface_row(i) for i in range(1, 6)],
                 iface_row(6, final_verdict="NG", defect_codes=["LEN"]))
    client.post("/intake/poll", headers=QA)
    ops = client.post(f"{base}/{insp['id']}/complete", headers=OP, json={}).json()
    assert (ops[1]["status"], ops[1]["good_qty"]) == ("DONE", None)
    assert (ops[1]["auto_good_qty"], ops[1]["auto_defect_qty"]) == (5, 1)
    assert client.post(f"{base}/{insp['id']}/skip", headers=QA).status_code == 409


def test_start_refuses_broken_or_busy_equipment_and_skip_needs_quality(client):
    client.post("/equipment", headers=QA, json={"code": "SAW-1", "name": "톱 절단기 1"})
    client.put("/routings/*", headers=QA, json={"steps": [
        {"process_code": "CUT", "equipment_code": "SAW-1"}, {"process_code": "PACK"}]})
    a, b = _wo(client), _wo(client, lot="L-2")
    a_cut, a_pack = _ops(client, a["id"])
    b_cut = _ops(client, b["id"])[0]

    client.post("/equipment/SAW-1/state", headers=OP, json={"state": "DOWN", "reason": "톱날 파손"})
    r = client.post(f"/work-orders/{a['id']}/operations/{a_cut['id']}/start", headers=OP, json={})
    assert r.status_code == 409 and "고장" in r.json()["detail"]
    client.post("/equipment/SAW-1/state", headers=OP, json={"state": "IDLE"})

    assert client.post(f"/work-orders/{a['id']}/operations/{a_cut['id']}/start",
                       headers=OP, json={}).status_code == 200
    r = client.post(f"/work-orders/{b['id']}/operations/{b_cut['id']}/start", headers=OP, json={})
    assert r.status_code == 409 and "다른 작업지시" in r.json()["detail"]
    assert client.post(f"/work-orders/{b['id']}/operations/{b_cut['id']}/start", headers=OP,
                       json={"equipment_code": "NOPE"}).status_code == 404
    # 남의 작업지시 공정 번호로는 못 한다
    assert client.post(f"/work-orders/{b['id']}/operations/{a_cut['id']}/complete", headers=OP,
                       json={"good_qty": 1}).status_code == 404

    skip = f"/work-orders/{a['id']}/operations/{a_pack['id']}/skip"
    assert client.post(skip, headers=OP).status_code == 403
    assert client.post(skip, headers=QA).json()[1]["status"] == "SKIPPED"

    # 완료된 작업지시는 공정을 못 움직인다
    client.patch(f"/work-orders/{b['id']}", headers=OP, json={"status": "DONE"})
    assert client.post(f"/work-orders/{b['id']}/operations/{b_cut['id']}/complete", headers=OP,
                       json={"good_qty": 1}).status_code == 409


# ---- 자재·재고 ----------------------------------------------------------------------

def test_receipts_stock_and_adjustments(client):
    _item(client, "AL-TUBE", "원자재 튜브", unit="m", safety_stock=100)
    assert client.post("/stock/receipts", headers=OP, json={
        "item_code": "NOPE", "qty": 1}).status_code == 404
    assert client.post("/stock/receipts", headers=OP, json={
        "item_code": "AL-TUBE", "qty": 0}).status_code == 422
    r = client.post("/stock/receipts", headers=OP, json={
        "item_code": "AL-TUBE", "qty": 60.5, "lot": "RM-001"})
    assert r.status_code == 201 and (r.json()["txn_type"], r.json()["qty"]) == ("RECEIPT", 60.5)

    row = client.get("/stock", headers=OP).json()[0]
    assert (row["item_code"], row["on_hand"], row["below_safety"]) == ("AL-TUBE", 60.5, True)
    client.post("/stock/receipts", headers=OP, json={"item_code": "AL-TUBE", "qty": 50})
    row = client.get("/stock", headers=OP).json()[0]
    assert (row["on_hand"], row["below_safety"]) == (110.5, False)

    adj = {"item_code": "AL-TUBE", "qty": -10.5, "note": "실사 차이"}
    assert client.post("/stock/adjustments", headers=OP, json=adj).status_code == 403
    assert client.post("/stock/adjustments", headers=QA, json=adj | {"qty": 0}).status_code == 422
    assert client.post("/stock/adjustments", headers=QA, json=adj | {"note": ""}).status_code == 422
    assert client.post("/stock/adjustments", headers=QA, json=adj | {"item_code": "NOPE"}).status_code == 404
    assert client.post("/stock/adjustments", headers=QA, json=adj).status_code == 201
    assert client.get("/stock", headers=OP).json()[0]["on_hand"] == 100

    txns = client.get("/stock/txns", headers=OP, params={"item_code": "AL-TUBE"}).json()
    assert [t["txn_type"] for t in txns] == ["ADJUST", "RECEIPT", "RECEIPT"], "최근 순"
    assert txns[-1]["lot"] == "RM-001" and txns[0]["created_by"] == "qa1"
    # 기준정보에 등록만 하고 수불이 없는 품목도 0 으로 나온다
    _item(client, "BOX", "포장 박스", "SUB")
    assert {r["item_code"]: r["on_hand"] for r in client.get("/stock", headers=OP).json()} == {
        "AL-TUBE": 100, "BOX": 0}
    assert [r["item_code"] for r in client.get(
        "/stock", headers=OP, params={"item_type": "SUB"}).json()] == ["BOX"]


def test_material_requirement_and_issue(client):
    _item(client, "AL-TUBE", "원자재 튜브", unit="m")
    _item(client, "BOX", "포장 박스", "SUB")
    client.put("/bom/HP12", headers=QA, json={"lines": [
        {"component_code": "AL-TUBE", "qty_per": 0.25, "scrap_pct": 4},
        {"component_code": "BOX", "qty_per": 0.01}]})
    wo = _wo(client)  # 100개
    client.post("/stock/receipts", headers=OP, json={"item_code": "AL-TUBE", "qty": 20})
    client.post("/stock/receipts", headers=OP, json={"item_code": "BOX", "qty": 5})

    m = client.get(f"/work-orders/{wo['id']}/materials", headers=OP).json()
    tube, box = m["lines"]
    assert (tube["required"], tube["issued"], tube["on_hand"], tube["shortage"]) == (26, 0, 20, 6)
    assert (box["required"], box["shortage"]) == (1, 0)

    url = f"/work-orders/{wo['id']}/materials/issue"
    r = client.post(url, headers=OP, json={})
    assert r.status_code == 409 and r.json()["detail"]["shortage"] == {"AL-TUBE": 6}
    assert client.get("/stock/txns", headers=OP, params={"wo_id": wo["id"]}).json() == [], \
        "부족하면 아무것도 투입하지 않는다"

    # 있는 만큼만, 자재 LOT 을 적어 투입
    m = client.post(url, headers=OP, json={"lines": [
        {"item_code": "AL-TUBE", "qty": 20, "lot": "RM-001"}]}).json()
    assert (m["lines"][0]["issued"], m["lines"][0]["on_hand"], m["lines"][0]["shortage"]) == (20, 0, 6)
    client.post("/stock/receipts", headers=OP, json={"item_code": "AL-TUBE", "qty": 10})
    # 비우면 남은 필요량 전부(튜브 6, 박스 1)
    m = client.post(url, headers=OP, json={}).json()
    assert [(ln["issued"], ln["on_hand"], ln["shortage"]) for ln in m["lines"]] == [(26, 4, 0), (1, 4, 0)]
    assert client.post(url, headers=OP, json={}).status_code == 409, "더 투입할 것이 없다"

    txns = client.get("/stock/txns", headers=OP, params={"wo_id": wo["id"]}).json()
    assert sorted((t["item_code"], t["qty"], t["lot"]) for t in txns) == [
        ("AL-TUBE", -20, "RM-001"), ("AL-TUBE", -6, None), ("BOX", -1, None)]
    assert all(t["txn_type"] == "ISSUE" and t["wo_no"] == wo["wo_no"] for t in txns)

    # 모자라도 강행하면 음수 재고
    client.post(url, headers=OP, json={"allow_negative": True, "lines": [
        {"item_code": "BOX", "qty": 10}]})
    assert {r["item_code"]: r["on_hand"] for r in client.get("/stock", headers=OP).json()}["BOX"] == -6
    assert client.post(url, headers=OP, json={"lines": [
        {"item_code": "NOPE", "qty": 1}]}).status_code == 404


def test_done_work_order_produces_stock_and_shipment_consumes_it(client):
    wo = _wo(client)
    insert_iface(*[iface_row(i) for i in range(1, 9)],
                 iface_row(9, final_verdict="NG", defect_codes=["LEN"]))
    client.post("/intake/poll", headers=QA)
    stock = lambda: {r["item_code"]: r["on_hand"] for r in client.get("/stock", headers=OP).json()}  # noqa: E731
    assert stock() == {}

    client.patch(f"/work-orders/{wo['id']}", headers=OP, json={"status": "DONE"})
    assert stock() == {"HP12": 8}, "검사결과의 양품 수"
    client.patch(f"/work-orders/{wo['id']}", headers=QA, json={"note": "메모"})
    assert stock() == {"HP12": 8}, "다시 저장해도 한 번만"
    t = client.get("/stock/txns", headers=OP).json()
    assert len(t) == 1 and (t[0]["txn_type"], t[0]["lot"], t[0]["wo_no"]) == ("PRODUCE", "L-1", wo["wo_no"])

    s = client.post("/shipments", headers=QA, json={
        "customer": "고객A", "item_code": "HP12", "qty": 5, "ship_date": "2026-10-20", "lot": "L-1"})
    assert s.status_code == 201
    assert stock() == {"HP12": 3}
    ship = client.get("/stock/txns", headers=OP).json()[0]
    assert (ship["txn_type"], ship["qty"], ship["shipment_id"]) == ("SHIP", -5, s.json()["id"])

    # 완료를 되돌리면 생산 입고가 사라진다(출하는 남는다)
    client.patch(f"/work-orders/{wo['id']}", headers=OP, json={"status": "RUNNING"})
    assert stock() == {"HP12": -5}
    # 품목 기준정보에 없는 제품도 수불이 있으면 조정할 수 있다
    assert client.post("/stock/adjustments", headers=QA, json={
        "item_code": "HP12", "qty": 5, "note": "되돌림 보정"}).status_code == 201


def test_done_without_inspection_uses_last_completed_operation(client):
    client.put("/routings/HP12", headers=QA, json={"steps": [
        {"process_code": "CUT"}, {"process_code": "PACK"}]})
    wo = _wo(client)
    cut, pack = _ops(client, wo["id"])
    base = f"/work-orders/{wo['id']}/operations"
    client.post(f"{base}/{cut['id']}/complete", headers=OP, json={"good_qty": 97, "defect_qty": 3})
    client.post(f"{base}/{pack['id']}/complete", headers=OP, json={"good_qty": 95})
    client.patch(f"/work-orders/{wo['id']}", headers=OP, json={"status": "DONE"})
    assert client.get("/stock", headers=OP).json()[0]["on_hand"] == 95
    # 취소는 입고를 만들지 않는다
    other = _wo(client, lot="L-2")
    client.patch(f"/work-orders/{other['id']}", headers=OP, json={"status": "CANCELLED"})
    assert len(client.get("/stock/txns", headers=OP).json()) == 1


# ---- 설비 가동 ----------------------------------------------------------------------

def test_equipment_state_changes_and_history(client):
    assert client.post("/equipment", headers=OP, json={"code": "SAW-1", "name": "x"}).status_code == 403
    assert client.post("/equipment", headers=QA, json={
        "code": "SAW-1", "name": "톱", "process_code": "NOPE"}).status_code == 404
    e = client.post("/equipment", headers=QA, json={
        "code": "SAW-1", "name": "톱 절단기 1", "process_code": "CUT"}).json()
    assert (e["state"], e["active"]) == (None, True)
    assert client.post("/equipment", headers=QA, json={"code": "SAW-1", "name": "x"}).status_code == 409

    st = lambda **b: client.post("/equipment/SAW-1/state", headers=OP, json=b)  # noqa: E731
    assert st(state="RUN").json()["state"] == "RUN"
    assert st(state="DOWN").status_code == 422, "고장은 사유 필수"
    assert st(state="PM", reason="  ").status_code == 422
    d = st(state="DOWN", reason="톱날 파손").json()
    assert (d["state"], d["state_reason"]) == ("DOWN", "톱날 파손")
    st(state="DOWN", reason="톱날 파손")  # 같은 상태 — 구간을 새로 열지 않는다
    st(state="IDLE")
    assert client.post("/equipment/NOPE/state", headers=OP, json={"state": "RUN"}).status_code == 404

    ev = client.get("/equipment/SAW-1/events", headers=OP).json()
    assert [(x["state"], x["ended_at"] is None) for x in ev] == [
        ("IDLE", True), ("DOWN", False), ("RUN", False)]
    assert ev[1]["created_by"] == "op1"
    assert client.patch("/equipment/SAW-1", headers=QA, json={"active": False}).json()["active"] is False


def test_utilization_math(client):
    client.post("/equipment", headers=QA, json={"code": "SAW-1", "name": "톱 절단기 1"})
    client.post("/equipment", headers=QA, json={"code": "SAW-2", "name": "톱 절단기 2"})
    now = now_utc()
    h = lambda n: now - timedelta(minutes=n)  # noqa: E731
    with SessionLocal() as db:
        # 최근 100분: 가동 60 → 고장 10 → 점검 20 → 대기 10(진행 중). 그 전 하루는 기간 밖.
        db.add_all([
            EquipmentEvent(equipment_code="SAW-1", state="RUN",
                           started_at=now - timedelta(days=3), ended_at=now - timedelta(days=2, hours=23)),
            EquipmentEvent(equipment_code="SAW-1", state="RUN", started_at=h(100), ended_at=h(40)),
            EquipmentEvent(equipment_code="SAW-1", state="DOWN", reason="x", started_at=h(40), ended_at=h(30)),
            EquipmentEvent(equipment_code="SAW-1", state="PM", reason="y", started_at=h(30), ended_at=h(10)),
            EquipmentEvent(equipment_code="SAW-1", state="IDLE", started_at=h(10)),
        ])
        db.commit()
    from mes_app.common import KST
    d0, d1 = (now - timedelta(days=1)).astimezone(KST).date(), now.astimezone(KST).date()
    u = client.get("/equipment-utilization", headers=OP,
                   params={"date_from": str(d0), "date_to": str(d1)}).json()
    saw1, saw2 = u["rows"]
    assert (saw1["run_s"], saw1["down_s"], saw1["pm_s"]) == (3600, 600, 1200)
    assert abs(saw1["idle_s"] - 600) < 5, "진행 중인 구간은 지금까지"
    assert abs(saw1["availability_pct"] - 75.0) < 0.2, "가동 60 ÷ (가동 60 + 대기 10 + 고장 10), 점검 제외"
    assert (saw1["down_count"], saw1["mttr_min"], saw1["mtbf_min"]) == (1, 10, 60)
    assert (saw2["availability_pct"], saw2["down_count"], saw2["mttr_min"]) == (None, 0, None)

    assert client.get("/equipment-utilization", headers=OP, params={
        "date_from": "2026-10-02", "date_to": "2026-10-01"}).status_code == 422
    # 기간을 안 주면 오늘
    today = client.get("/equipment-utilization", headers=OP).json()
    assert today["date_from"] == today["date_to"] == str(d1)
