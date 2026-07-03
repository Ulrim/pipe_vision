"""라벨링 보조 모듈 (부록 A.4/A.5).

dataset/raw/<CLASS>/{품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{seq}.jpg 와
동일명 사이드카 .json 을 읽어 정답셋(GroundTruth)을 구성한다. QA 에이전트가
이 정답셋을 소비해 항목별 정확도/혼동행렬을 산출한다(§1.2).
"""
from labeling.groundtruth import (
    GroundTruthItem,
    build_groundtruth,
    parse_filename,
    write_manifest,
)

__all__ = [
    "GroundTruthItem",
    "build_groundtruth",
    "parse_filename",
    "write_manifest",
]
