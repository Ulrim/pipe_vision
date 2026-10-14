"""출하·클레임·월 KPI (docs/MES.md §5). 손으로 넣던 리드타임·Claim·출하수량을 기록에서 센다."""
from __future__ import annotations

from .conftest import token


def _so(client, order_date, due_date=None, **over):
    body = {"customer": "고객A", "item_code": "HP12", "qty": 100,
            "order_date": order_date, "due_date": due_date} | over
    return client.post("/orders", headers=token(), json=body).json()


def test_shipments_claims_and_monthly_kpi(client):
    so1 = _so(client, "2026-09-25", "2026-10-03")
    so2 = _so(client, "2026-10-01", "2026-10-05")

    s1 = client.post("/shipments", headers=token(), json={
        "so_id": so1["id"], "qty": 60, "ship_date": "2026-10-02"}).json()
    assert (s1["customer"], s1["item_code"]) == ("고객A", "HP12"), "수주에서 따라온다"
    assert (s1["lead_time_days"], s1["on_time"]) == (7, True)

    s2 = client.post("/shipments", headers=token(), json={
        "so_id": so2["id"], "qty": 40, "ship_date": "2026-10-08", "lot": "L-9"}).json()
    assert (s2["lead_time_days"], s2["on_time"]) == (7, False)
    # 수주 없는 출하(견본 등) — 출하수량에는 들어가고 리드타임 표본은 아니다.
    s3 = client.post("/shipments", headers=token(), json={
        "customer": "고객B", "item_code": "HP20", "qty": 100, "ship_date": "2026-10-20"})
    assert s3.status_code == 201
    # 다음 달 출하는 빠진다
    client.post("/shipments", headers=token(), json={
        "so_id": so2["id"], "qty": 10, "ship_date": "2026-11-01"})

    c1 = client.post("/claims", headers=token(), json={
        "customer": "고객A", "lot": "L-9", "defect_qty": 3, "defect_type": "scr",
        "occurred_date": "2026-10-15", "description": "스크래치"}).json()
    assert c1["claim_no"] == "CL-20261015-001" and c1["defect_type"] == "SCR"
    client.post("/claims", headers=token(), json={
        "customer": "고객A", "defect_qty": 2, "occurred_date": "2026-09-30"})

    k = client.get("/kpi/monthly", headers=token("op1", "operator"),
                   params={"period": "2026-10"}).json()
    assert k["shipment_count"] == 3 and k["shipped_qty"] == 200
    assert (k["lead_time_days"], k["lead_time_samples"]) == (7.0, 2)
    assert (k["on_time_rate_pct"], k["on_time_samples"]) == (50.0, 2)
    assert (k["claim_count"], k["leak_defect_qty"]) == (1, 3)
    assert k["leak_ppm"] == 15000.0

    so = {o["so_no"]: o for o in client.get("/orders", headers=token()).json()}
    assert so[so2["so_no"]]["shipped_qty"] == 50

    month = client.get("/shipments", headers=token(), params={"period": "2026-10"}).json()
    assert len(month) == 3


def test_empty_month_has_no_rates(client):
    k = client.get("/kpi/monthly", headers=token(), params={"period": "2031-01"}).json()
    assert k["shipped_qty"] == 0 and k["lead_time_days"] is None
    assert k["leak_ppm"] is None and k["on_time_rate_pct"] is None


def test_bad_period(client):
    assert client.get("/kpi/monthly", headers=token(),
                      params={"period": "2026-13"}).status_code == 422
    assert client.get("/shipments", headers=token(),
                      params={"period": "oct"}).status_code == 422


def test_claim_close_and_roles(client):
    c = client.post("/claims", headers=token(), json={
        "customer": "고객A", "defect_qty": 1, "occurred_date": "2026-10-01"}).json()
    op = token("op1", "operator")
    assert client.patch(f"/claims/{c['id']}", headers=op,
                        json={"status": "CLOSED"}).status_code == 403
    r = client.patch(f"/claims/{c['id']}", headers=token(), json={"status": "CLOSED"}).json()
    assert r["status"] == "CLOSED" and r["closed_at"] is not None
    assert client.get("/claims", headers=op, params={"status": "OPEN"}).json() == []


def test_shipment_needs_item_and_customer(client):
    r = client.post("/shipments", headers=token(), json={"qty": 1, "ship_date": "2026-10-01"})
    assert r.status_code == 422


def test_times_come_back_as_utc(client):
    """sqlite 도 시각을 UTC(+00:00)로 돌려준다 — tz 없으면 브라우저가 9시간 어긋나게 읽는다."""
    from .conftest import iface_row, insert_iface

    insert_iface(iface_row(1))
    client.post("/intake/poll", headers=token())
    q = client.get("/quality", headers=token()).json()[0]
    assert q["inspected_at"].endswith(("+00:00", "Z")), q["inspected_at"]
    assert q["received_at"].endswith(("+00:00", "Z")), q["received_at"]
    assert q["inspected_at"].startswith("2026-10-14T00:00:01")
