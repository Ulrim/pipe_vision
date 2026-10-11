"""라벨 매니페스트 → 학습용 데이터셋 디렉터리 (부록 A.4/A.6, M16).

**연결하는 두 끝**: 대시보드 라벨링 화면이 만든 정답(GET /labels/export 의
매니페스트)과, 학습 CLI 가 요구하는 폴더 구조(dataset/raw/<CLASS>/) 사이가
비어 있었다. 사람이 라벨을 붙여도 학습을 돌릴 수 없으면 아무 일도 일어나지
않는다. 이 모듈이 그 사이를 잇는다.

  dataset/raw/OK/    ← 정상(라벨 빈 배열)  → train_anomaly.py --ok-dir 이 먹는다
  dataset/raw/LEN/ OIL/ DIS/ SCR/ MULTI/   → 항목별 정확도 측정·지도학습용
  dataset/raw/BORDER/ ← 경계 샘플(부록 A.2). 별도 사본으로도 모은다.

복합불량은 개별 코드 폴더에 **모두** 들어간다(부록 A.5: 라벨은 배열). MULTI
폴더에도 함께 넣어 복합 사례만 따로 볼 수 있게 한다.

원본을 옮기지 않고 **복사**한다. 검사 이미지는 품질 증빙이라 학습 준비 때문에
사라지면 안 된다. 같은 이유로 이미 있는 파일은 건너뛴다(재실행 안전).

**다발 사진은 튜브 하나씩 잘라 넣는다(2026-10-11)**. 다발 한 장은 튜브마다
한 행·한 라벨인데, 예전에는 사진 전체를 복사했다. 그러면 튜브 #3 하나에만
유분기가 있는 사진이 나머지 19개 튜브의 'OK' 라벨로 OK 폴더에도 19번 들어가
정상 학습셋이 불량으로 오염됐다. 이제는 매니페스트의 ``tube_bbox`` (원본 프레임
픽셀)로 그 튜브만 잘라 ``{id}_t{번호}_{원본이름}.png`` 로 저장한다.
- 잘라내기는 추론과 **똑같이** 한다(vision/multi/batch: bbox 그대로, 세로로 긴
  칸은 시계방향 90° 회전). 학습과 추론의 입력이 달라지면 모델이 엉뚱한 걸 배운다.
- PNG 로 저장한다 — JPEG 를 한 번 더 압축하면 미세 결함(스크래치)이 뭉개진다.
- 다발인데 위치(tube_bbox)가 없는 행(이 기능 이전 적재분)은 **넣지 않는다**.
  사진 전체를 넣느니 빠지는 편이 낫다. 보고서 ``uncroppable`` 로 센다.

**라벨 사이드카(.json)를 파일마다 함께 쓴다**. groundtruth.load_item 이 이것을
먼저 읽으므로 이 폴더를 그대로 ``AIVIS_DATASET_DIR`` 로 지정하면 FAT 하니스가
항목별 정확도를 잰다(부록 A.6). 사이드카는 매번 다시 쓰고, 라벨이 바뀌어 다른
폴더로 옮겨 간 항목은 예전 폴더의 사본을 지운다 — 같은 튜브가 OIL 과 OK 에
동시에 있으면 정답셋이 스스로 모순된다.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_OK = "OK"
_BORDER = "BORDER"
_MULTI = "MULTI"
#: 이 도구가 만드는 폴더 전부 — 라벨이 바뀐 항목의 예전 사본을 찾을 곳.
_ALL_CLASSES = (_OK, "LEN", "OIL", "DIS", "SCR", "COUNT", _MULTI, _BORDER)


@dataclass
class BuildReport:
    """빌드 결과(로그·CI 확인용)."""

    copied: int = 0
    skipped: int = 0
    missing: int = 0
    cropped: int = 0       # 다발에서 튜브 하나를 잘라 넣은 수(사본 기준)
    uncroppable: int = 0   # 다발인데 위치가 없어 뺀 항목 수
    removed: int = 0       # 라벨이 바뀌어 예전 폴더에서 지운 사본 수
    by_class: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "copied": self.copied,
            "skipped": self.skipped,
            "missing": self.missing,
            "cropped": self.cropped,
            "uncroppable": self.uncroppable,
            "removed": self.removed,
            "by_class": dict(sorted(self.by_class.items())),
            "errors": self.errors[:10],
        }


def classes_for(labels: Iterable[str], border: bool) -> list[str]:
    """이 항목이 들어갈 폴더 목록.

    - 라벨이 없으면 OK 한 곳.
    - 라벨이 있으면 각 코드 폴더 모두. 2종 이상이면 MULTI 에도 함께(§7.2).
    - 경계 표시가 있으면 BORDER 에도 함께(부록 A.2 — 정확도 95% 돌파의 핵심).
    """
    codes = sorted({str(c).strip().upper() for c in labels if str(c).strip()})
    out = list(codes) if codes else [_OK]
    if len(codes) >= 2:
        out.append(_MULTI)
    if border:
        out.append(_BORDER)
    return out


def _source_path(item: dict[str, Any], images_dir: Path) -> Path | None:
    """학습에 쓸 원본 파일 경로.

    판정 오버레이(result)에는 측정선·수치가 그려져 있어 학습에 넣으면 모델이
    **그 그림을 배운다**. 반드시 원본(raw)을 쓴다. 원본이 보관정리로 사라진
    항목은 건너뛴다.
    """
    rel = item.get("raw_image_path")
    if not rel:
        return None
    p = (images_dir / str(rel)).resolve()
    try:
        p.relative_to(images_dir.resolve())
    except ValueError:
        return None  # 경로 탈출 방어
    return p if p.is_file() else None


def _is_bundle(item: dict[str, Any]) -> bool:
    """다발 사진의 튜브 1개 행인가. 예전 매니페스트(frame_total 없음)는 단일로 본다."""
    try:
        return int(item.get("frame_total") or 1) > 1
    except (TypeError, ValueError):
        return False


def _valid_bbox(bbox: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in bbox)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def crop_tube(src: Path, bbox: tuple[int, int, int, int]):
    """원본에서 튜브 칸을 추론과 같은 방식으로 잘라 낸다(BGR ndarray).

    cv2 는 여기서만 쓰므로 지연 import 한다(채점·매니페스트 도구는 cv2 없이 돈다).
    반환: (crop, 실제 잘린 박스). 프레임 밖으로 나간 박스는 프레임 안으로 자른다.
    """
    import cv2  # noqa: WPS433 - 지연 import

    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise OSError(f"이미지를 열 수 없다: {src.name}")
    h, w = img.shape[:2]
    x0, y0, x1, y1 = bbox
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"튜브 위치가 프레임 밖이다: {bbox} (프레임 {w}x{h})")
    crop = img[y0:y1, x0:x1]
    if (y1 - y0) > (x1 - x0):
        # 세로 튜브 — vision/multi/batch 가 추론 전에 하는 회전과 같다.
        crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    return crop, [x0, y0, x1, y1]


def _write_png(dst: Path, img) -> None:
    import cv2  # noqa: WPS433 - crop_tube 가 이미 불러왔다

    if not cv2.imwrite(str(dst), img):
        raise OSError("PNG 저장 실패")


def _sidecar(item: dict[str, Any], crop_box: list[int] | None) -> dict[str, Any]:
    """groundtruth.load_item 이 읽는 사이드카(부록 A.4) + 되짚기용 식별자."""
    return {
        "item_code": item.get("item_code"),
        "inspection_stage": item.get("inspection_stage"),
        "view": item.get("view") or "SIDE",
        "labels": [str(c).strip().upper() for c in (item.get("labels") or [])],
        "border": bool(item.get("border")),
        "length_mm_gt": item.get("length_mm_gt"),
        "inspector": item.get("labeled_by"),
        "captured_at": item.get("inspected_at"),
        "inspection_id": item.get("inspection_id"),
        "lot": item.get("lot"),
        "cam_id": item.get("cam_id"),
        "tube_index": item.get("tube_index"),
        "frame_total": item.get("frame_total"),
        "crop_box": crop_box,
        "source_image": item.get("raw_image_path"),
    }


def _write_sidecar(dst: Path, data: dict[str, Any]) -> None:
    with open(dst.with_suffix(".json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _remove_stale(out: Path, name: str, keep: list[str], report: BuildReport) -> None:
    """라벨이 바뀐 항목의 예전 폴더 사본(+사이드카)을 지운다. 이 도구가 만든 것만."""
    for cls in _ALL_CLASSES:
        if cls in keep:
            continue
        old = out / cls / name
        if old.exists():
            old.unlink()
            side = old.with_suffix(".json")
            if side.exists():
                side.unlink()
            report.removed += 1


def build_dataset(
    manifest: dict[str, Any] | list[dict[str, Any]],
    images_dir: str | os.PathLike,
    out_dir: str | os.PathLike,
) -> BuildReport:
    """매니페스트의 항목을 클래스별 폴더로 넣는다(다발은 튜브 단위로 잘라서)."""
    items = manifest["items"] if isinstance(manifest, dict) else list(manifest)
    images = Path(images_dir)
    out = Path(out_dir)
    report = BuildReport()

    for item in items:
        src = _source_path(item, images)
        if src is None:
            report.missing += 1
            continue
        classes = classes_for(item.get("labels") or [], bool(item.get("border")))
        iid = item.get("inspection_id")

        bbox = None
        if _is_bundle(item):
            bbox = _valid_bbox(item.get("tube_bbox"))
            if bbox is None:
                # 사진 전체를 넣으면 다른 튜브들이 이 라벨을 뒤집어쓴다 — 뺀다.
                report.uncroppable += 1
                report.errors.append(f"{iid}: 다발 사진인데 튜브 위치(tube_bbox)가 없다")
                continue
            # 번호는 판정 이미지의 '#n' 과 같다(tube_index 0-base + 1).
            n = int(item.get("tube_index") or 0) + 1
            name = f"{iid}_t{n:02d}_{src.stem}.png"
        else:
            # 파일명은 원본 그대로 — 검사 id 로 DB 와 되짚을 수 있어야 한다.
            name = f"{iid}_{src.name}"

        _remove_stale(out, name, classes, report)

        crop = crop_box = None
        for cls in classes:
            dst_dir = out / cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / name
            try:
                if dst.exists():
                    report.skipped += 1
                else:
                    if bbox is not None:
                        if crop is None:
                            crop, crop_box = crop_tube(src, bbox)
                        _write_png(dst, crop)
                        report.cropped += 1
                    else:
                        shutil.copy2(src, dst)
                    report.copied += 1
                    report.by_class[cls] = report.by_class.get(cls, 0) + 1
                # 라벨은 바뀔 수 있으므로 사이드카는 매번 다시 쓴다.
                _write_sidecar(dst, _sidecar(item, crop_box or (list(bbox) if bbox else None)))
            except (OSError, ValueError, ImportError) as exc:
                report.errors.append(f"{name} -> {cls}: {exc}")
                break
    return report


def load_manifest(path: str | os.PathLike) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
