"""데모 품목 시드(_seed_demo_item) 단위 테스트 (item_master FK 충족).

- 시드 on: 데모 품목 1건 생성 + 멱등(중복 호출 시 추가 안 됨).
- 시드 off: 미생성.
§7.1 필드 값 검증.
"""
from __future__ import annotations

import main
from core.config import Settings
from db.base import SessionLocal
from db.models import ItemMaster


def _settings(seed: bool, code: str) -> Settings:
    s = Settings.__new__(Settings)
    s.seed_demo_item = seed
    s.demo_item_code = code
    return s


def _cleanup(code: str) -> None:
    db = SessionLocal()
    try:
        obj = db.get(ItemMaster, code)
        if obj:
            db.delete(obj)
            db.commit()
    finally:
        db.close()


def test_seed_demo_item_on_creates_and_idempotent(monkeypatch):
    code = "DEMOSEED1"
    _cleanup(code)
    try:
        monkeypatch.setattr(main, "get_settings", lambda: _settings(True, code))

        main._seed_demo_item()
        db = SessionLocal()
        try:
            obj = db.get(ItemMaster, code)
            assert obj is not None
            assert float(obj.ref_length_mm) == 125.0
            assert float(obj.tol_plus_mm) == 0.5
            assert float(obj.tol_minus_mm) == 0.5
            assert float(obj.px_to_mm_scale) == 0.25
            assert float(obj.oil_threshold) == 0.5
            assert float(obj.discolor_threshold) == 0.5
            assert float(obj.scratch_threshold) == 0.5
            assert obj.capture_recipe == {}
            assert obj.version == 1
            assert obj.updated_by == "seed"
        finally:
            db.close()

        # 멱등: 재호출해도 1건만 존재.
        main._seed_demo_item()
        db = SessionLocal()
        try:
            cnt = db.query(ItemMaster).filter(ItemMaster.item_code == code).count()
            assert cnt == 1
        finally:
            db.close()
    finally:
        _cleanup(code)


def test_seed_demo_item_off_no_create(monkeypatch):
    code = "DEMOSEED2"
    _cleanup(code)
    try:
        monkeypatch.setattr(main, "get_settings", lambda: _settings(False, code))
        main._seed_demo_item()
        db = SessionLocal()
        try:
            assert db.get(ItemMaster, code) is None
        finally:
            db.close()
    finally:
        _cleanup(code)
