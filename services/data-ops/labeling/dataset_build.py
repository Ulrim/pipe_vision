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


@dataclass
class BuildReport:
    """빌드 결과(로그·CI 확인용)."""

    copied: int = 0
    skipped: int = 0
    missing: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "copied": self.copied,
            "skipped": self.skipped,
            "missing": self.missing,
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


def build_dataset(
    manifest: dict[str, Any] | list[dict[str, Any]],
    images_dir: str | os.PathLike,
    out_dir: str | os.PathLike,
) -> BuildReport:
    """매니페스트의 항목을 클래스별 폴더로 복사한다."""
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
        # 파일명은 원본 그대로 — 검사 id 로 DB 와 되짚을 수 있어야 한다.
        name = f"{item.get('inspection_id')}_{src.name}"
        for cls in classes:
            dst_dir = out / cls
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / name
            if dst.exists():
                report.skipped += 1
                continue
            try:
                shutil.copy2(src, dst)
                report.copied += 1
                report.by_class[cls] = report.by_class.get(cls, 0) + 1
            except OSError as exc:
                report.errors.append(f"{name} -> {cls}: {exc}")
    return report


def load_manifest(path: str | os.PathLike) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
