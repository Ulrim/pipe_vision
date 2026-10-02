"""라벨링 보조 모듈 (부록 A.4/A.5).

dataset/raw/<CLASS>/{품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{seq}.jpg 와
동일명 사이드카 .json 을 읽어 정답셋(GroundTruth)을 구성한다. QA 에이전트가
이 정답셋을 소비해 항목별 정확도/혼동행렬을 산출한다(§1.2).

크레이트 단면 구도는 한 프레임에 단면이 수백 개라 프레임 라벨로는 계수 정확도를
잴 수 없다. 그 구도는 `points` 모듈이 **단면별 점 라벨**로 따로 다룬다.
"""
from labeling.groundtruth import (
    GroundTruthItem,
    build_groundtruth,
    parse_filename,
    write_manifest,
)
from labeling.points import (
    PointLabel,
    PointSet,
    auto_path,
    gt_path,
    load_points,
    match_points,
    save_points,
    score_dir,
)

__all__ = [
    "GroundTruthItem",
    "build_groundtruth",
    "parse_filename",
    "write_manifest",
    "PointLabel",
    "PointSet",
    "auto_path",
    "gt_path",
    "load_points",
    "match_points",
    "save_points",
    "score_dir",
]
