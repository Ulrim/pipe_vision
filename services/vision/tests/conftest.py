"""테스트 픽스처: 합성 데이터셋 + 기준 ItemMaster.

합성 이미지의 기준 파이프 길이 = 500px(gen_synthetic.DEFAULT_PIPE_LEN_PX).
끝단 에지(1px 라인 포함)를 고려해 px_to_mm_scale 를 잡아 OK 이미지가
기준 길이에 떨어지도록 한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aivis_types import ItemMaster

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.tools.gen_synthetic import (  # noqa: E402
    DEFAULT_PIPE_LEN_PX,
    write_dataset,
)

# OK 파이프 = 500px. scale 0.25 → 125mm 목표.
SCALE = 0.25
REF_MM = round(DEFAULT_PIPE_LEN_PX * SCALE, 3)  # 125.0


@pytest.fixture(scope="session")
def dataset_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("ds_raw")
    write_dataset(
        d,
        classes=["OK", "LEN", "OIL", "DIS", "SCR", "MULTI"],
        per_class=3,
        item_code="HP12",
        view="SIDE",
    )
    return d


@pytest.fixture
def item() -> ItemMaster:
    return ItemMaster(
        item_code="HP12",
        item_name="Header Pipe 12",
        ref_length_mm=REF_MM,
        tol_plus_mm=3.0,
        tol_minus_mm=3.0,
        px_to_mm_scale=SCALE,
        oil_threshold=0.30,
        discolor_threshold=0.20,
        scratch_threshold=0.15,
    )
