"""재학습 추출 + 임계 보정 테스트 (CLAUDE.md §5 M16)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from db.models import Inspection
from retrain.review import (
    build_retrain_manifest,
    extract_review_candidates,
)
from retrain.threshold import suggest_thresholds


_seq = 0


def _insp(db, **over) -> Inspection:
    # 호출마다 inspected_at 을 1초씩 뒤로 밀어 자연키(cam_id+inspected_at+lot+item)
    # 유일성 보장(ux_insp_natkey). 테스트 단언은 시각과 무관.
    global _seq
    base = dict(
        lot="LOT1", item_code="HP12", cam_id="CAM1",
        inspected_at=datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
        + timedelta(seconds=_seq),
        final_verdict="OK", defect_codes=[], review_flag=False, mes_synced=False,
    )
    _seq += 1
    base.update(over)
    row = Inspection(**base)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_extract_false_positive_and_negative(db):
    # 오검: 시스템 NG, 사람 OK
    _insp(db, final_verdict="NG", manual_verdict="OK", defect_codes=["SCR"])
    # 미검: 시스템 OK, 사람 NG
    _insp(db, final_verdict="OK", manual_verdict="NG")
    # 일치: 후보 아님
    _insp(db, final_verdict="OK", manual_verdict="OK")

    cands = extract_review_candidates(db)
    kinds = sorted(c.miss_kind for c in cands)
    assert kinds == ["system_ng_human_ok", "system_ok_human_ng"]


def test_review_flag_included(db):
    _insp(db, review_flag=True)  # manual 미입력이지만 flag
    cands = extract_review_candidates(db)
    assert len(cands) == 1
    assert cands[0].review_flag is True
    assert cands[0].miss_kind is None


def test_only_mismatch_excludes_plain_flag(db):
    _insp(db, review_flag=True)
    cands = extract_review_candidates(db, include_flagged=False, only_mismatch=True)
    assert cands == []


def test_build_manifest(db, tmp_path):
    _insp(db, final_verdict="NG", manual_verdict="OK", raw_image_path="raw/a.jpg")
    _insp(db, final_verdict="OK", manual_verdict="NG", raw_image_path="raw/b.jpg")
    out = tmp_path / "retrain"
    manifest = build_retrain_manifest(db, str(out), item_code="HP12")
    assert manifest["total"] == 2
    assert manifest["false_positive"] == 1
    assert manifest["false_negative"] == 1
    data = json.loads((out / "retrain_manifest.json").read_text())
    assert data["total"] == 2


def test_build_manifest_copies_images(db, tmp_path):
    # 실제 이미지 파일 생성 후 복사 검증.
    img_root = tmp_path / "store"
    (img_root / "raw").mkdir(parents=True)
    (img_root / "raw" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    _insp(db, final_verdict="NG", manual_verdict="OK", raw_image_path="raw/a.jpg")

    out = tmp_path / "retrain"
    build_retrain_manifest(db, str(out), copy=True, image_root=str(img_root))
    copied = out / "review" / "system_ng_human_ok" / "a.jpg"
    assert copied.exists()


def test_suggest_thresholds_insufficient_samples(db):
    _insp(db, manual_verdict="OK", scratch_score=0.2)
    sugg = {s.feature: s for s in suggest_thresholds(db, "HP12", min_samples=10)}
    assert sugg["scratch"].suggested is None
    assert "표본 부족" in sugg["scratch"].note


def test_suggest_thresholds_finds_separating_value(db):
    t0 = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    # 사람 OK 표본: 낮은 스크래치 점수 / 사람 NG 표본: 높은 점수.
    for i in range(8):
        _insp(db, cam_id=f"C{i}", inspected_at=t0 + timedelta(seconds=i),
              manual_verdict="OK", scratch_score=0.1 + i * 0.01)
    for i in range(8):
        _insp(db, cam_id=f"D{i}", inspected_at=t0 + timedelta(seconds=100 + i),
              manual_verdict="NG", final_verdict="NG", scratch_score=0.8 + i * 0.01)

    sugg = {s.feature: s for s in suggest_thresholds(db, "HP12", min_samples=10)}
    s = sugg["scratch"]
    assert s.samples == 16
    assert s.suggested is not None
    # 분리 임계는 0.17(OK 최대 0.17) ~ 0.8(NG 최소) 사이.
    assert 0.17 < s.suggested < 0.8
    assert s.accuracy_suggested == 1.0
