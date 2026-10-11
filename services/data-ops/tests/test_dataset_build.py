"""라벨 → 학습용 데이터셋 폴더 빌드 (부록 A.4, M16).

라벨링 화면과 학습 CLI 사이를 잇는 부분이다. 여기서 지키는 계약:
1) 학습에는 **원본(raw)** 을 쓴다 — 판정 오버레이를 넣으면 모델이 그림을 배운다.
2) 복합불량은 개별 코드 폴더에 모두 + MULTI 에도 들어간다(라벨은 배열).
3) 경계 샘플은 BORDER 로도 모인다(정확도 95% 돌파의 핵심).
4) 원본을 옮기지 않고 복사한다 — 검사 이미지는 품질 증빙이다.
5) 다발 사진은 튜브 하나씩 잘라 넣는다 — 사진 전체를 넣으면 다른 튜브들이 이
   라벨을 뒤집어써 OK 폴더가 불량으로 오염된다(2026-10-11).
6) 파일마다 라벨 사이드카를 써서 groundtruth(=FAT 하니스)가 그대로 읽는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from labeling.dataset_build import build_dataset, classes_for
from labeling.groundtruth import build_groundtruth, load_item


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


def _real_img(base: Path, rel: str, w: int = 200, h: int = 100) -> Path:
    """튜브마다 밝기가 다른 진짜 이미지(잘린 칸이 맞는지 픽셀로 확인)."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(5):  # 가로 튜브 5줄, 줄마다 밝기 다르게
        img[i * 20:(i + 1) * 20, :, :] = 40 * (i + 1)
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(p), img)
    return p


def _bundle_item(iid: int, idx: int, labels: list[str], bbox, **kw) -> dict:
    return {
        "inspection_id": iid, "item_code": "HP12", "lot": "TRAIN-OIL-01",
        "inspected_at": "2026-10-11T01:00:00+00:00", "cam_id": "CAM-01",
        "inspection_stage": "POST_WASH_SURFACE", "view": "SIDE",
        "raw_image_path": "raw/bundle.png", "tube_index": idx, "frame_total": 5,
        "tube_bbox": bbox, "labels": labels, "border": False, **kw,
    }


def test_bundle_tube_is_cropped_not_whole_frame(tmp_path: Path) -> None:
    """다발 튜브 #3 의 라벨은 #3 칸에만 붙는다 — 사진 전체가 OK 로 들어가면 안 된다."""
    cv2 = pytest.importorskip("cv2")
    images = tmp_path / "images"
    _real_img(images, "raw/bundle.png")
    out = tmp_path / "dataset"

    rep = build_dataset({"items": [
        _bundle_item(31, 2, ["OIL"], [0, 40, 200, 60]),
        _bundle_item(32, 3, [], [0, 60, 200, 80]),
    ]}, images, out)

    assert rep.cropped == 2 and rep.uncroppable == 0
    oil = out / "OIL" / "31_t03_bundle.png"
    ok = out / "OK" / "32_t04_bundle.png"
    assert oil.is_file() and ok.is_file()
    a = cv2.imread(str(oil))
    assert a.shape[:2] == (20, 200)          # 튜브 한 칸 크기
    assert int(a.mean()) == 120              # #3 줄의 밝기(40×3) — 옆 줄이 섞이지 않았다
    assert int(cv2.imread(str(ok)).mean()) == 160
    # OK 폴더에는 OK 로 라벨된 튜브 한 칸뿐 — 사진 전체 사본은 어디에도 없다.
    assert sorted(p.name for p in (out / "OK").glob("*.png")) == ["32_t04_bundle.png"]
    assert not list(out.rglob("3?_bundle.png"))


def test_vertical_tube_rotated_like_inference(tmp_path: Path) -> None:
    """세로로 긴 칸은 추론(vision/multi/batch)처럼 시계방향 90° 돌려 가로로 넣는다."""
    cv2 = pytest.importorskip("cv2")
    images = tmp_path / "images"
    _real_img(images, "raw/bundle.png")
    out = tmp_path / "dataset"

    build_dataset({"items": [_bundle_item(41, 0, [], [10, 0, 30, 100])]}, images, out)
    a = cv2.imread(str(out / "OK" / "41_t01_bundle.png"))
    assert a.shape[:2] == (20, 100)


def test_bundle_without_position_is_left_out(tmp_path: Path) -> None:
    """위치가 없는 다발 행(이전 적재분)은 넣지 않는다 — 전체 사진이 들어가면 오염."""
    images = tmp_path / "images"
    _img(images, "raw/bundle.png")
    out = tmp_path / "dataset"

    rep = build_dataset({"items": [_bundle_item(51, 0, [], None)]}, images, out)
    assert rep.uncroppable == 1
    assert rep.copied == 0
    assert not out.exists() or not list(out.rglob("*.png"))


def test_sidecar_readable_by_groundtruth(tmp_path: Path) -> None:
    """출력 폴더를 그대로 AIVIS_DATASET_DIR 로 쓰면 하니스가 정답을 읽는다."""
    pytest.importorskip("cv2")
    images = tmp_path / "images"
    _real_img(images, "raw/bundle.png")
    _img(images, "raw/single.jpg")
    out = tmp_path / "dataset"

    build_dataset({"items": [
        _bundle_item(61, 0, ["OIL", "DIS"], [0, 0, 200, 20], border=True,
                     labeled_by="qa1"),
        {"inspection_id": 62, "item_code": "HP12", "labels": [], "border": False,
         "raw_image_path": "raw/single.jpg", "view": "SIDE", "frame_total": 1,
         "length_mm_gt": 250.02},
    ]}, images, out)

    it = load_item(str(out / "OIL" / "61_t01_bundle.png"))
    assert it.source == "sidecar"
    assert it.labels == ["OIL", "DIS", "MULTI"]
    assert it.border is True
    assert it.meta["inspection_id"] == 61 and it.meta["tube_index"] == 0
    assert it.meta["inspector"] == "qa1"

    side = json.loads((out / "OK" / "62_single.json").read_text(encoding="utf-8"))
    assert side["labels"] == [] and side["length_mm_gt"] == 250.02

    items, errors = build_groundtruth(str(out), view="SIDE")
    assert not errors
    # OIL/DIS/MULTI/BORDER 사본 4 + OK 1 — 이름이 부록 A.4 규칙이 아니어도 사이드카로 읽는다.
    assert len(items) == 5
    assert all(i.source == "sidecar" for i in items)


def test_relabel_moves_item_between_folders(tmp_path: Path) -> None:
    """라벨을 고치고 다시 돌리면 예전 폴더 사본을 지운다(같은 튜브가 OIL·OK 에 동시에 있으면 모순)."""
    images = tmp_path / "images"
    _img(images, "raw/c.jpg")
    out = tmp_path / "dataset"
    item = {"inspection_id": 71, "labels": ["OIL"], "raw_image_path": "raw/c.jpg"}

    build_dataset({"items": [item]}, images, out)
    assert (out / "OIL" / "71_c.jpg").is_file()

    rep = build_dataset({"items": [{**item, "labels": []}]}, images, out)
    assert rep.removed == 1
    assert not (out / "OIL" / "71_c.jpg").exists()
    assert not (out / "OIL" / "71_c.json").exists()
    assert (out / "OK" / "71_c.jpg").is_file()
    assert json.loads((out / "OK" / "71_c.json").read_text(encoding="utf-8"))["labels"] == []
