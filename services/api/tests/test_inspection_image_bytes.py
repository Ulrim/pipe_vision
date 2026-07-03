"""GET /inspection/{id}/images/{kind} 이미지 바이트 서빙 (M8, §7.4).

검증:
- 더미 jpg 200 + image/jpeg 바이트 반환.
- 경로 None 시 404, 파일 부재 시 404.
- traversal(`../..` 류) 상대경로 차단(404).
- 미인증 401, kind 잘못된 값 422.
get_settings lru_cache 주의: routers.inspection.get_settings 를 monkeypatch.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# 최소 유효 JPEG(SOI..EOI). 실제 디코드 가능한 1x1 흑백 baseline JPEG.
_JPEG_BYTES = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707"
    "07090908080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c"
    "1c2837292c30313434341f27393d38323c2e333432ffc0000b08000100010101110000"
    "ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc4"
    "00b5100002010303020403050504040000017d01020300041105122131410613516107"
    "227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a34"
    "35363738393a434445464748494a535455565758595a636465666768696a737475767778"
    "797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9"
    "bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7"
    "f8f9faffda0008010100003f00fbfeffd9"
)


def _insp(**over):
    base = {
        "lot": "LOTIMGB",
        "item_code": "HP12",
        "cam_id": "CAM1",
        "inspected_at": datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc).isoformat(),
        "final_verdict": "OK",
        "defect_codes": [],
        "review_flag": False,
        "mes_synced": False,
        "proc_time_ms": 120,
    }
    base.update(over)
    return base


def _make_item(client, auth):
    client.post(
        "/master/items",
        headers=auth("qa1"),
        json={
            "item_code": "HP12",
            "item_name": "Header Pipe 12",
            "ref_length_mm": 250.0,
            "tol_plus_mm": 0.5,
            "tol_minus_mm": 0.5,
            "px_to_mm_scale": 0.05,
        },
    )


@pytest.fixture
def images_dir(tmp_path, monkeypatch):
    """임시 images_dir + routers.inspection.get_settings 오버라이드.

    get_settings 는 lru_cache 라 인스턴스가 전역 공유된다. 여기서는
    routers.inspection 가 바인딩한 get_settings 심볼만 교체해, 실제 Settings 를
    감싸 images_dir 만 임시 디렉터리로 덮어쓴다(mes_mode/consec_ng_threshold 등
    저장 경로가 쓰는 다른 속성은 그대로 위임 → POST /inspection 정상 저장).
    """
    import routers.inspection as insp_mod

    d = tmp_path / "images"
    (d / "raw").mkdir(parents=True)
    (d / "result").mkdir(parents=True)

    real = insp_mod.get_settings()

    class _S:
        images_dir = str(d)

        def __getattr__(self, name):
            return getattr(real, name)

    stub = _S()
    monkeypatch.setattr(insp_mod, "get_settings", lambda: stub)
    return d


def _store(client, auth, **over):
    _make_item(client, auth)
    r = client.post("/inspection", json=_insp(**over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_raw_and_result_bytes(client, auth, images_dir):
    (images_dir / "raw" / "a.jpg").write_bytes(_JPEG_BYTES)
    (images_dir / "result" / "a.jpg").write_bytes(_JPEG_BYTES)
    insp_id = _store(
        client, auth, raw_image_path="raw/a.jpg", result_image_path="result/a.jpg"
    )

    for kind in ("raw", "result"):
        r = client.get(f"/inspection/{insp_id}/images/{kind}", headers=auth("op1"))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == _JPEG_BYTES
        assert "cache-control" in r.headers


def test_missing_path_404(client, auth, images_dir):
    # raw 만 지정, result 경로 None.
    (images_dir / "raw" / "b.jpg").write_bytes(_JPEG_BYTES)
    insp_id = _store(client, auth, lot="LOTNOPATH", raw_image_path="raw/b.jpg")

    r = client.get(f"/inspection/{insp_id}/images/result", headers=auth("op1"))
    assert r.status_code == 404


def test_file_absent_404(client, auth, images_dir):
    # 경로는 DB 에 있으나 디스크에 파일 없음.
    insp_id = _store(client, auth, lot="LOTGHOST", raw_image_path="raw/ghost.jpg")
    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_traversal_blocked(client, auth, images_dir):
    # /etc/passwd 가 존재해도 escape 불가 → 404.
    insp_id = _store(
        client, auth, lot="LOTTRAV", raw_image_path="../../../../etc/passwd"
    )
    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_absolute_path_blocked(client, auth, images_dir):
    # 절대경로(os.path.join 이 절대경로 우선) 도 escape 불가.
    insp_id = _store(client, auth, lot="LOTABS", raw_image_path="/etc/passwd")
    r = client.get(f"/inspection/{insp_id}/images/raw", headers=auth("op1"))
    assert r.status_code == 404


def test_unauthenticated_401(client, auth, images_dir):
    (images_dir / "raw" / "c.jpg").write_bytes(_JPEG_BYTES)
    insp_id = _store(client, auth, lot="LOTUNAUTH", raw_image_path="raw/c.jpg")
    r = client.get(f"/inspection/{insp_id}/images/raw")  # 토큰 없음
    assert r.status_code == 401


def test_bad_kind_422(client, auth, images_dir):
    insp_id = _store(client, auth, lot="LOTKIND", raw_image_path="raw/x.jpg")
    r = client.get(f"/inspection/{insp_id}/images/thumbnail", headers=auth("op1"))
    assert r.status_code == 422


def test_unknown_inspection_404(client, auth, images_dir):
    r = client.get("/inspection/99999999/images/raw", headers=auth("op1"))
    assert r.status_code == 404
