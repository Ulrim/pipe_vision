"""라벨 → 학습용 데이터셋 폴더 빌드 (부록 A.4, M16).

라벨링 화면과 학습 CLI 사이를 잇는 부분이다. 여기서 지키는 계약:
1) 학습에는 **원본(raw)** 을 쓴다 — 판정 오버레이를 넣으면 모델이 그림을 배운다.
2) 복합불량은 개별 코드 폴더에 모두 + MULTI 에도 들어간다(라벨은 배열).
3) 경계 샘플은 BORDER 로도 모인다(정확도 95% 돌파의 핵심).
4) 원본을 옮기지 않고 복사한다 — 검사 이미지는 품질 증빙이다.
"""
from __future__ import annotations

from pathlib import Path

from labeling.dataset_build import build_dataset, classes_for


def _img(base: Path, rel: str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"jpeg")
    return p


def test_classes_for_rules() -> None:
    assert classes_for([], False) == ["OK"]
    assert classes_for(["SCR"], False) == ["SCR"]
    # 2종 이상이면 개별 코드 + MULTI
    assert classes_for(["OIL", "DIS"], False) == ["DIS", "OIL", "MULTI"]
    # 경계는 어디에 속하든 BORDER 에도
    assert classes_for([], True) == ["OK", "BORDER"]
    assert classes_for(["SCR"], True) == ["SCR", "BORDER"]


def test_copies_raw_not_result(tmp_path: Path) -> None:
    """판정 오버레이가 아니라 원본을 복사한다."""
    images = tmp_path / "images"
    _img(images, "raw/a.jpg")
    _img(images, "result/a.jpg")
    out = tmp_path / "dataset"

    rep = build_dataset(
        {"items": [{
            "inspection_id": 7, "labels": [], "border": False,
            "raw_image_path": "raw/a.jpg", "result_image_path": "result/a.jpg",
        }]},
        images, out,
    )
    assert rep.copied == 1
    assert (out / "OK" / "7_a.jpg").is_file()
    assert not (out / "result").exists()
    # 원본은 그대로 남아 있어야 한다(복사이지 이동이 아니다).
    assert (images / "raw/a.jpg").is_file()


def test_multi_label_lands_in_every_bucket(tmp_path: Path) -> None:
    images = tmp_path / "images"
    _img(images, "raw/b.jpg")
    out = tmp_path / "dataset"

    rep = build_dataset(
        {"items": [{
            "inspection_id": 9, "labels": ["OIL", "DIS"], "border": True,
            "raw_image_path": "raw/b.jpg",
        }]},
        images, out,
    )
    for cls in ("OIL", "DIS", "MULTI", "BORDER"):
        assert (out / cls / "9_b.jpg").is_file(), cls
    assert rep.copied == 4


def test_missing_raw_is_counted_not_fatal(tmp_path: Path) -> None:
    """보관정리로 원본이 사라진 항목은 건너뛴다(전체가 실패하면 안 된다)."""
    images = tmp_path / "images"
    images.mkdir()
    out = tmp_path / "dataset"

    rep = build_dataset(
        {"items": [
            {"inspection_id": 1, "labels": [], "raw_image_path": "raw/gone.jpg"},
            {"inspection_id": 2, "labels": [], "raw_image_path": None},
        ]},
        images, out,
    )
    assert rep.missing == 2
    assert rep.copied == 0


def test_rerun_is_safe(tmp_path: Path) -> None:
    """두 번 돌려도 중복 복사하지 않는다."""
    images = tmp_path / "images"
    _img(images, "raw/c.jpg")
    out = tmp_path / "dataset"
    item = {"items": [{"inspection_id": 3, "labels": ["SCR"], "raw_image_path": "raw/c.jpg"}]}

    build_dataset(item, images, out)
    rep = build_dataset(item, images, out)
    assert rep.copied == 0
    assert rep.skipped == 1


def test_path_escape_rejected(tmp_path: Path) -> None:
    """매니페스트의 상대경로가 이미지 폴더를 벗어나면 무시한다."""
    images = tmp_path / "images"
    images.mkdir()
    (tmp_path / "secret.jpg").write_bytes(b"x")
    out = tmp_path / "dataset"

    rep = build_dataset(
        {"items": [{"inspection_id": 4, "labels": [], "raw_image_path": "../secret.jpg"}]},
        images, out,
    )
    assert rep.copied == 0
    assert rep.missing == 1
