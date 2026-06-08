"""데모 데이터셋 자립 보장 — AIVIS_DATASET_DIR 가 비었으면 합성 이미지 자동 생성.

CLAUDE.md 부록 A.6: AIVIS_CAMERA=sim 으로 SimulatorCamera 가 폴더를 리플레이.
실데이터가 아직 없어도 클라우드 데모/온프레미스 compose 양쪽에서 검사 루프가
항상 돌도록, 폴더가 없거나 이미지가 없으면 tools.gen_synthetic 으로 채운다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ._bootstrap import write_dataset

log = logging.getLogger("aivis.vision.worker.dataset")

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp")
_DEFAULT_SYNTH_DIR = "/tmp/aivis_synthetic/raw"


def _has_images(path: Path) -> bool:
    if not path.exists():
        return False
    for p in path.rglob("*"):
        if p.is_file() and p.suffix.lower() in _IMG_EXTS:
            return True
    return False


def ensure_dataset(dataset_dir: str | None) -> str:
    """리플레이 가능한 이미지 폴더를 보장하고 그 경로를 반환한다.

    - dataset_dir 에 이미지가 있으면 그대로 사용.
    - 없거나(None/빈 폴더/미존재) 비었으면 합성 데이터셋을 생성해 사용.
    """
    if dataset_dir:
        target = Path(dataset_dir)
        if _has_images(target):
            log.info("데이터셋 사용: %s", target)
            return str(target)
        log.warning(
            "AIVIS_DATASET_DIR=%s 에 이미지 없음 — 합성 데이터셋을 생성한다", target
        )
        synth = target
    else:
        log.warning(
            "AIVIS_DATASET_DIR 미설정 — 데모용 합성 데이터셋을 생성한다"
        )
        synth = Path(_DEFAULT_SYNTH_DIR)

    try:
        synth.mkdir(parents=True, exist_ok=True)
        files = write_dataset(
            synth,
            classes=["OK", "LEN", "OIL", "DIS", "SCR", "MULTI"],
            per_class=3,
            item_code="HP12",
            view="SIDE",
        )
        log.info("합성 이미지 %d장 생성: %s", len(files), synth)
    except Exception as exc:  # noqa: BLE001
        # 쓰기 권한 없으면 /tmp 로 폴백.
        log.warning("합성 생성 실패(%s) — /tmp 폴백", exc)
        synth = Path(_DEFAULT_SYNTH_DIR)
        synth.mkdir(parents=True, exist_ok=True)
        write_dataset(
            synth,
            classes=["OK", "LEN", "OIL", "DIS", "SCR", "MULTI"],
            per_class=3,
            item_code="HP12",
            view="SIDE",
        )
    return str(synth)
