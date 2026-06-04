"""정답셋(ground-truth) 빌더 (부록 A.4/A.5, §1.2 검증용).

입력: dataset/raw/<CLASS>/ 구조 + 동일명 사이드카 .json.
  파일명 규칙: {품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{seq}.jpg
  사이드카 .json: item_code, view(END|SIDE), labels[], border,
                 length_mm_gt, scale_ref_mm, lighting, inspector, captured_at, note
출력: GroundTruthItem 목록 + 매니페스트 JSON.

라벨은 배열(복합불량) → §7.2 defect_codes 와 그대로 매핑. 사이드카가 없으면
파일명/폴더의 클래스 코드로 라벨을 보충한다(폴백). 사이드카가 있으면 우선한다.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# aivis_types 의 enum 으로 코드 화이트리스트 검증(단일 진실원).
from aivis_types.enums import CameraView, DefectCode

_VALID_CODES = {c.value for c in DefectCode}
_OK_CODE = "OK"  # 정상은 defect 코드가 아니라 라벨 부재로 표현.
_VALID_VIEWS = {v.value for v in CameraView}

# {품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{seq}
_FILENAME_RE = re.compile(
    r"^(?P<item>[^_]+)_(?P<view>END|SIDE)_(?P<cls>[A-Z]+)_"
    r"(?P<ts>\d{8}-\d{6})_(?P<seq>\d+)$"
)

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


@dataclass
class GroundTruthItem:
    """정답셋 1건. QA 가 모델 출력과 대조한다."""

    path: str                       # 이미지 절대/상대 경로
    item_code: str | None           # 품목 코드
    view: str | None                # END | SIDE
    labels: list[str] = field(default_factory=list)  # 불량 코드 배열(정상=[])
    border: bool = False            # 경계 샘플(부록 A.2)
    length_mm_gt: float | None = None
    scale_ref_mm: float | None = None
    source: str = "sidecar"         # sidecar | filename (라벨 출처)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return len(self.labels) == 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class LabelParseError(Exception):
    """파일명/사이드카 파싱 실패."""


def parse_filename(name: str) -> dict[str, str]:
    """파일명(확장자 제외 가능)을 부록 A.4 규칙으로 파싱.

    반환: {item, view, cls, ts, seq}. 규칙 불일치 시 LabelParseError.
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    m = _FILENAME_RE.match(stem)
    if not m:
        raise LabelParseError(f"파일명 규칙 불일치(부록 A.4): {name}")
    d = m.groupdict()
    if d["view"] not in _VALID_VIEWS:
        raise LabelParseError(f"알 수 없는 구도: {d['view']}")
    return d


def _normalize_labels(raw: Iterable[Any]) -> list[str]:
    """라벨 배열 정규화: 대문자화, OK 제거, 코드 화이트리스트 검증."""
    out: list[str] = []
    for v in raw or []:
        code = str(v).strip().upper()
        if code in ("", _OK_CODE):
            continue
        if code == "BORDER":  # 경계 태그는 라벨이 아니라 플래그로 처리.
            continue
        if code not in _VALID_CODES:
            raise LabelParseError(f"알 수 없는 불량 코드: {code} (§7.2)")
        if code not in out:
            out.append(code)
    return out


def _sidecar_path(image_path: str) -> str:
    return os.path.splitext(image_path)[0] + ".json"


def load_item(image_path: str) -> GroundTruthItem:
    """단일 이미지의 정답 항목 구성. 사이드카 우선, 없으면 파일명 폴백."""
    sidecar = _sidecar_path(image_path)
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            data = json.load(f)
        labels = _normalize_labels(data.get("labels", []))
        # MULTI 는 2종 이상일 때 보강(§7.2). 명시돼 있으면 유지.
        if len(labels) >= 2 and "MULTI" not in labels:
            labels.append("MULTI")
        return GroundTruthItem(
            path=image_path,
            item_code=data.get("item_code"),
            view=data.get("view"),
            labels=labels,
            border=bool(data.get("border", False)),
            length_mm_gt=data.get("length_mm_gt"),
            scale_ref_mm=data.get("scale_ref_mm"),
            source="sidecar",
            meta={
                k: data[k]
                for k in ("lighting", "inspector", "captured_at", "note")
                if k in data
            },
        )

    # 폴백: 파일명에서 클래스 코드.
    parsed = parse_filename(image_path)
    cls = parsed["cls"].upper()
    labels = [] if cls == _OK_CODE else _normalize_labels([cls])
    return GroundTruthItem(
        path=image_path,
        item_code=parsed["item"],
        view=parsed["view"],
        labels=labels,
        source="filename",
    )


def _iter_images(root: str, view: str | None = None) -> list[str]:
    """root 하위 이미지 경로 수집(정렬). view 필터(END|SIDE) 옵션."""
    found: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _IMG_EXTS:
                continue
            if view is not None:
                try:
                    if parse_filename(fn)["view"] != view:
                        continue
                except LabelParseError:
                    continue
            found.append(os.path.join(dirpath, fn))
    return sorted(found)


def build_groundtruth(
    dataset_dir: str,
    *,
    view: str | None = None,
    strict: bool = False,
) -> tuple[list[GroundTruthItem], list[dict[str, str]]]:
    """dataset_dir(raw/) 전체에서 정답셋을 구성.

    반환: (정답셋 항목 리스트, 건너뛴/오류 항목 리스트).
    strict=True 면 파싱 오류 시 예외, False 면 errors 로 수집하고 계속.
    """
    items: list[GroundTruthItem] = []
    errors: list[dict[str, str]] = []
    for path in _iter_images(dataset_dir, view=view):
        try:
            items.append(load_item(path))
        except LabelParseError as exc:
            if strict:
                raise
            errors.append({"path": path, "error": str(exc)})
    return items, errors


def write_manifest(
    items: list[GroundTruthItem],
    out_path: str,
    *,
    errors: list[dict[str, str]] | None = None,
) -> str:
    """정답셋 매니페스트(JSON)를 출력. QA 가 소비하는 표준 포맷."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    payload = {
        "count": len(items),
        "ok_count": sum(1 for it in items if it.is_ok),
        "ng_count": sum(1 for it in items if not it.is_ok),
        "border_count": sum(1 for it in items if it.border),
        "errors": errors or [],
        "items": [it.as_dict() for it in items],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path
