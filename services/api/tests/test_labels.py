"""정답 라벨링 API (부록 A.2/A.5, M16).

라벨링은 사람 시간이 드는 일이라 **순서가 곧 비용**이다. 여기서 지키는 계약:
1) 큐는 값이 큰 것부터 준다 — 재확인 대상 → NG → 나머지.
2) 이미지가 없는 행은 큐에 넣지 않는다(라벨을 붙여도 학습에 못 쓴다).
3) 라벨은 배열이고 코드 화이트리스트를 벗어나면 거부한다.
4) 저장은 품질담당 이상(부록 A.5 검수 주체).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _post(client, **over):
    base = {
        "lot": "L1",
        "item_code": "LB",
        "cam_id": "C1",
        "inspected_at": datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "result_image_path": "result/a_OK.jpg",
        "proc_time_ms": 100,
    }
    base.update(over)
    r = client.post("/inspection", json=base)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ensure_item(client, auth):
    client.post("/master/items", headers=auth("qa1"), json={
        "item_code": "LB", "item_name": "label", "ref_length_mm": 100.0,
        "tol_plus_mm": 0.5, "tol_minus_mm": 0.5, "px_to_mm_scale": 0.05,
    })


def test_queue_orders_by_value(client, auth):
    """재확인 대상 → NG → 정상 순. 모델이 헷갈린 것이 가장 값이 크다."""
    _ensure_item(client, auth)
    t = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
    ok = _post(client, cam_id="Q1", inspected_at=t.isoformat())
    ng = _post(client, cam_id="Q2", final_verdict="NG", defect_codes=["SCR"],
               inspected_at=(t + timedelta(seconds=1)).isoformat())
    rev = _post(client, cam_id="Q3", review_flag=True,
                inspected_at=(t + timedelta(seconds=2)).isoformat())

    q = client.get("/labels/queue?limit=50", headers=auth("op1")).json()
    ids = [x["inspection_id"] for x in q]
    assert ids.index(rev) < ids.index(ng) < ids.index(ok)


def test_queue_skips_rows_without_image(client, auth):
    """이미지가 정리되어 사라진 행은 큐에 넣지 않는다(검수자 시간 낭비)."""
    _ensure_item(client, auth)
    noimg = _post(client, cam_id="NOIMG", result_image_path=None,
                  inspected_at=datetime(2026, 3, 4, 9, tzinfo=timezone.utc).isoformat())
    q = client.get("/labels/queue?limit=200", headers=auth("op1")).json()
    assert noimg not in [x["inspection_id"] for x in q]


def test_put_and_get_label(client, auth):
    """복합불량은 배열로 저장되고 정렬·중복제거된다."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="P1",
                inspected_at=datetime(2026, 3, 5, 9, tzinfo=timezone.utc).isoformat())

    r = client.put(f"/labels/{iid}", headers=auth("qa1"), json={
        "labels": ["dis", "OIL", "OIL"], "border": True, "note": "경계",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["labels"] == ["DIS", "OIL"], "대문자화 + 중복제거 + 정렬"
    assert body["border"] is True
    assert body["labeled_by"] == "qa1"

    got = client.get(f"/labels/{iid}", headers=auth("op1")).json()
    assert got["labels"] == ["DIS", "OIL"]


def test_labeled_rows_leave_the_queue(client, auth):
    """한 번 라벨을 붙이면 큐에서 빠진다(같은 사진을 두 번 보지 않는다)."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="P2",
                inspected_at=datetime(2026, 3, 6, 9, tzinfo=timezone.utc).isoformat())
    client.put(f"/labels/{iid}", headers=auth("qa1"), json={"labels": []})
    q = client.get("/labels/queue?limit=200", headers=auth("op1")).json()
    assert iid not in [x["inspection_id"] for x in q]


def test_unknown_defect_code_rejected(client, auth):
    """§7.2 코드표에 없는 값은 거부 — 오타가 학습셋을 오염시키면 안 된다."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="P3",
                inspected_at=datetime(2026, 3, 7, 9, tzinfo=timezone.utc).isoformat())
    r = client.put(f"/labels/{iid}", headers=auth("qa1"), json={"labels": ["RUST"]})
    assert r.status_code == 422


def test_operator_cannot_label(client, auth):
    """검수 주체는 품질담당(부록 A.5). 작업자는 읽기만."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="P4",
                inspected_at=datetime(2026, 3, 8, 9, tzinfo=timezone.utc).isoformat())
    r = client.put(f"/labels/{iid}", headers=auth("op1"), json={"labels": []})
    assert r.status_code == 403


def test_label_on_missing_inspection_is_404(client, auth):
    r = client.put("/labels/99999999", headers=auth("qa1"), json={"labels": []})
    assert r.status_code == 404


def test_progress_counts_ok_as_empty_labels(client, auth):
    """빈 라벨 = 정상(OK). 별도 OK 코드를 두면 ['OK','SCR'] 같은 모순이 생긴다."""
    _ensure_item(client, auth)
    a = _post(client, cam_id="G1",
              inspected_at=datetime(2026, 3, 9, 9, tzinfo=timezone.utc).isoformat())
    b = _post(client, cam_id="G2",
              inspected_at=datetime(2026, 3, 9, 9, 1, tzinfo=timezone.utc).isoformat())
    client.put(f"/labels/{a}", headers=auth("qa1"), json={"labels": []})
    client.put(f"/labels/{b}", headers=auth("qa1"),
               json={"labels": ["SCR"], "border": True})

    p = client.get("/labels/progress", headers=auth("op1")).json()
    assert p["by_class"]["OK"]["count"] >= 1
    assert p["by_class"]["SCR"]["count"] >= 1
    assert p["by_class"]["OK"]["target"] > 0, "부록 A.2 목표수량을 함께 보여준다"
    assert p["border_count"] >= 1


def test_export_pairs_truth_with_system_verdict(client, auth):
    """정확도(§1.2)를 재려면 정답과 시스템 판정이 같은 행에 있어야 한다."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="E1", final_verdict="NG", defect_codes=["OIL"],
                inspected_at=datetime(2026, 3, 10, 9, tzinfo=timezone.utc).isoformat())
    client.put(f"/labels/{iid}", headers=auth("qa1"), json={"labels": ["DIS"]})

    out = client.get("/labels/export?item_code=LB", headers=auth("qa1")).json()
    row = [x for x in out["items"] if x["inspection_id"] == iid][0]
    assert row["labels"] == ["DIS"]
    assert row["system_defect_codes"] == ["OIL"]
    assert row["is_ok"] is False


def test_delete_label_keeps_inspection(client, auth):
    """라벨을 지워도 검사결과는 남는다(사람 실수 되돌리기)."""
    _ensure_item(client, auth)
    iid = _post(client, cam_id="D1",
                inspected_at=datetime(2026, 3, 11, 9, tzinfo=timezone.utc).isoformat())
    client.put(f"/labels/{iid}", headers=auth("qa1"), json={"labels": ["SCR"]})
    assert client.delete(f"/labels/{iid}", headers=auth("qa1")).status_code == 204
    assert client.get(f"/labels/{iid}", headers=auth("op1")).status_code == 404
    assert client.get(f"/inspection/{iid}", headers=auth("op1")).status_code == 200
