"""정답셋 빌더 테스트: 파일명 파싱, 사이드카 우선, 복합불량, 폴백 (부록 A.4)."""
from __future__ import annotations

import json

import pytest

from labeling.groundtruth import (
    LabelParseError,
    build_groundtruth,
    load_item,
    parse_filename,
    write_manifest,
)


def test_parse_filename_ok():
    d = parse_filename("HP12_SIDE_SCR_20260610-141233_007.jpg")
    assert d["item"] == "HP12"
    assert d["view"] == "SIDE"
    assert d["cls"] == "SCR"
    assert d["ts"] == "20260610-141233"
    assert d["seq"] == "007"


def test_parse_filename_invalid():
    with pytest.raises(LabelParseError):
        parse_filename("badname.jpg")


def test_sidecar_takes_priority(tmp_dataset):
    _root, add = tmp_dataset
    path = add(
        "SCR", "HP12_SIDE_SCR_20260610-141233_007.jpg",
        sidecar={
            "item_code": "HP12", "view": "SIDE", "labels": ["SCR"],
            "border": True, "length_mm_gt": 248.5, "scale_ref_mm": 100.0,
            "lighting": "raking", "inspector": "kim",
        },
    )
    item = load_item(path)
    assert item.source == "sidecar"
    assert item.labels == ["SCR"]
    assert item.border is True
    assert item.length_mm_gt == 248.5
    assert item.view == "SIDE"
    assert item.meta["lighting"] == "raking"
    assert not item.is_ok


def test_filename_fallback_when_no_sidecar(tmp_dataset):
    _root, add = tmp_dataset
    path = add("OK", "HP12_SIDE_OK_20260610-101010_001.jpg")
    item = load_item(path)
    assert item.source == "filename"
    assert item.labels == []  # OK 는 빈 라벨
    assert item.is_ok


def test_multi_label_adds_multi_code(tmp_dataset):
    _root, add = tmp_dataset
    path = add(
        "MULTI", "HP12_END_MULTI_20260610-120000_002.jpg",
        sidecar={"item_code": "HP12", "view": "END", "labels": ["OIL", "DIS"]},
    )
    item = load_item(path)
    assert "OIL" in item.labels and "DIS" in item.labels
    assert "MULTI" in item.labels  # 2종 이상 → MULTI 보강


def test_invalid_label_code_raises(tmp_dataset):
    _root, add = tmp_dataset
    path = add(
        "X", "HP12_SIDE_SCR_20260610-141233_009.jpg",
        sidecar={"item_code": "HP12", "view": "SIDE", "labels": ["NOPE"]},
    )
    with pytest.raises(LabelParseError):
        load_item(path)


def test_build_groundtruth_and_manifest(tmp_dataset, tmp_path):
    root, add = tmp_dataset
    add("OK", "HP12_SIDE_OK_20260610-101010_001.jpg")
    add("SCR", "HP12_SIDE_SCR_20260610-141233_007.jpg",
        sidecar={"item_code": "HP12", "view": "SIDE", "labels": ["SCR"], "border": True})
    add("DIS", "HP12_END_DIS_20260610-150000_003.jpg")

    items, errors = build_groundtruth(str(root))
    assert errors == []
    assert len(items) == 3
    ok = [i for i in items if i.is_ok]
    assert len(ok) == 1

    out = tmp_path / "gt.json"
    write_manifest(items, str(out), errors=errors)
    data = json.loads(out.read_text())
    assert data["count"] == 3
    assert data["ok_count"] == 1
    assert data["ng_count"] == 2
    assert data["border_count"] == 1


def test_build_view_filter(tmp_dataset):
    root, add = tmp_dataset
    add("OK", "HP12_SIDE_OK_20260610-101010_001.jpg")
    add("DIS", "HP12_END_DIS_20260610-150000_003.jpg")
    items, _ = build_groundtruth(str(root), view="SIDE")
    assert len(items) == 1
    assert items[0].view == "SIDE"


def test_build_non_strict_collects_errors(tmp_dataset):
    root, add = tmp_dataset
    add("OK", "badname.jpg")  # 규칙 위반 + 사이드카 없음
    items, errors = build_groundtruth(str(root), strict=False)
    assert items == []
    assert len(errors) == 1
