"""데이터포털 제출 데이터셋 레이아웃·레코드 규격 (단일 진실원).

전남테크노파크 「AI솔루션 데이터 정의서」는 데이터셋별(원시/가공/AI분석) 로 작성하며,
"수집 내역서 ↔ 데이터 명세" 가 1:1 로 매칭돼야 한다. 이 모듈이 정의하는 폴더 구성과
레코드 필드가 `docs/DATA_DEFINITION.md` 의 3-2/3-3(원시), 4-2/4-3/4-5(가공),
5-2/5-3(AI분석) 과 그대로 대응한다. 변경 시 문서를 함께 갱신한다.

포털 업로드 코드(원시/가공/AI모델용 3종)는 데이터셋 3종에 각각 대응한다.

개인정보 방침: 작업자/검수자 식별 필드는 제출본에서 제외한다(정의서 "개인정보 미포함").
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

SCHEMA_VERSION = "1.0"

# ----- 데이터셋 식별자 (포털 업로드 코드 3종과 1:1) -----
DATASET_RAW = "raw"                 # 원시: 검사 원본 이미지 + 촬영 메타 인덱스
DATASET_PROCESSED = "processed"     # 가공: 라벨링/정답셋/재확인 라벨/기준정보
DATASET_AI = "ai-analysis"          # AI분석: 판정 결과/결과 이미지/KPI/검증 리포트
DATASETS = (DATASET_RAW, DATASET_PROCESSED, DATASET_AI)

# 포털 매뉴얼의 설정 파일명(원시/가공/AI모델) ↔ 데이터셋
CONF_NAME_BY_DATASET = {
    DATASET_RAW: "jntp-raw.conf",
    DATASET_PROCESSED: "jntp-processed.conf",
    DATASET_AI: "jntp-ai-model.conf",
}

# ----- 원시(raw) 폴더 구성 -----
RAW_INSPECTION_DIR = "inspection"   # inspection/YYYY/MM/DD/{LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg
RAW_CAPTURE_DIR = "capture"         # capture/{CLASS}/{품목}_{END|SIDE}_{클래스}_{YYYYMMDD-HHmmss}_{seq}.jpg
RAW_CALIB_DIR = "calib"             # calib/*.jpg (스케일 기준자 캘리브레이션 촬영)
RAW_INDEX_DIR = "index"             # index/raw_images_{YYYYMMDD}.jsonl (촬영 메타 인덱스)

# ----- 가공(processed) 폴더 구성 -----
PROC_LABELS_DIR = "labels"          # labels/{CLASS}/{stem}.json (사이드카 라벨, 검수자 제외)
PROC_GT_DIR = "groundtruth"         # groundtruth/gt_manifest.json (정답셋 매니페스트)
PROC_REVIEW_DIR = "review"          # review/review_labels.jsonl (작업자 재확인 라벨: 오검/미검)
PROC_MASTER_DIR = "master"          # master/item_master.json (판정 기준정보 스냅샷)
GT_MANIFEST_NAME = "gt_manifest.json"
REVIEW_LABELS_NAME = "review_labels.jsonl"
ITEM_MASTER_NAME = "item_master.json"

# ----- AI분석(ai-analysis) 폴더 구성 -----
AI_INSPECTIONS_DIR = "inspections"  # inspections/YYYY/MM/inspections_{YYYYMMDD}.jsonl
AI_RESULT_DIR = "result"            # result/YYYY/MM/DD/{...}.jpg (판정 오버레이 이미지)
AI_KPI_DIR = "kpi"                  # kpi/kpi_{YYYY-MM}.json (§1.1 월간 KPI 요약)
AI_REPORTS_DIR = "reports"          # reports/*.json|*.md (FAT/SAT/MSA 검증 리포트)

# 개인정보 후보 필드 — 제출본에서 제외(정의서: 개인정보 미포함 / 비식별화 해당없음).
EXCLUDED_PERSONAL_FIELDS = ("operator", "inspector", "updated_by")

# 포털 허용 확장자(매뉴얼 §5). 사전 검사로 서버 거절을 줄인다.
ALLOWED_EXTENSIONS = frozenset({
    "csv", "xls", "xlsx", "json", "ndjson", "jsonl", "tsv", "parquet", "txt", "md",
    "log", "html", "xml", "docx", "hwp", "hwpx", "jpg", "jpeg", "png", "tiff", "tif",
    "bmp", "gif", "webp", "svg", "mp4", "avi", "mov", "webm", "pdf", "pcm", "wav",
    "mp3", "flac", "m4a", "obj", "stl", "ply",
})


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """개인정보 후보 필드를 방어적으로 제거(레코드 직렬화 공통 경로)."""
    return {k: v for k, v in d.items() if k not in EXCLUDED_PERSONAL_FIELDS}


@dataclass
class RawImageRecord:
    """원시 데이터 명세(3-3): 원본 이미지 1장 = 인덱스 1행."""

    file_path: str                       # 데이터셋 루트 기준 상대경로(예: inspection/2026/09/02/L1_HP12_..._OK.jpg)
    file_name: str                       # 파일명
    file_type: str                       # 확장자(jpg/png)
    file_size: int                       # 바이트
    source: str                          # inspection(운영 검사) | capture(학습 촬영) | calib(캘리브레이션)
    captured_at: str | None              # 촬영 시각 ISO-8601
    cam_id: str | None                   # 카메라 ID
    item_code: str | None                # 품목 코드
    view: str | None                     # 촬영 구도 END | SIDE
    width: int | None = None             # 이미지 가로(px)
    height: int | None = None            # 이미지 세로(px)
    lot: str | None = None               # LOT 번호(운영 검사)
    work_order: str | None = None        # 작업지시 번호(운영 검사)
    inspection_id: int | None = None     # 연계 검사결과 ID(운영 검사, AI분석 레코드와 조인 키)
    tube_count: int = 1                  # 한 프레임에 담긴 튜브 수(다중 튜브 배치)
    capture_class: str | None = None     # 학습 촬영 폴더 클래스(OK/LEN/OIL/DIS/SCR/MULTI/BORDER)
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class LabelRecord:
    """가공 데이터 명세(4-3/4-5): 학습 이미지 1장의 라벨(사이드카 JSON, 부록 A.4/A.5)."""

    image_path: str                      # 원시 데이터셋 내 이미지 경로(capture/{CLASS}/{file})
    item_code: str | None
    view: str | None                     # END | SIDE
    labels: list[str] = field(default_factory=list)   # 불량 코드 배열 {LEN,OIL,DIS,SCR,MULTI}, 정상=[]
    border: bool = False                 # 경계 샘플 여부(부록 A.2)
    length_mm_gt: float | None = None    # 길이 정답값(mm, 측면 구도)
    scale_ref_mm: float | None = None    # 스케일 기준자 길이(mm)
    lighting: str | None = None          # 조명 조건(diffuse/raking 등)
    captured_at: str | None = None       # 촬영 시각 ISO-8601
    note: str | None = None              # 비고(결함 설명)
    label_source: str = "sidecar"        # sidecar | filename (라벨 출처)
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class ReviewLabelRecord:
    """가공 데이터 명세(4-3): 작업자 재확인 라벨(오검·미검, §5 M16)."""

    inspection_id: int
    lot: str
    item_code: str | None
    inspected_at: str
    final_verdict: str                   # AI 최종 판정 OK/NG
    manual_verdict: str | None           # 작업자 재확인 판정 OK/NG
    miss_kind: str | None                # system_ng_human_ok(오검) | system_ok_human_ng(미검) | None
    review_flag: bool
    defect_codes: list[str] = field(default_factory=list)
    raw_image_path: str | None = None    # 원시 데이터셋 내 원본 경로(inspection/...)
    result_image_path: str | None = None # AI분석 데이터셋 내 결과 경로(result/...)
    oil_score: float | None = None
    discolor_score: float | None = None
    scratch_score: float | None = None
    deviation_mm: float | None = None
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class InspectionRecord:
    """AI분석 데이터 명세(5-3): 제품 1개(튜브 1개) 판정 결과 = 1행(inspection 테이블, §7.1)."""

    inspection_id: int
    lot: str
    work_order: str | None
    item_code: str | None
    cam_id: str
    inspected_at: str                    # 판정 처리 시각 ISO-8601 (= predicted_at)
    tube_index: int                      # 배치 내 튜브 순번(0=단일)
    shift: str | None
    ref_length_mm: float | None
    meas_length_mm: float | None
    deviation_mm: float | None
    length_verdict: str | None           # OK/NG
    oil_score: float | None              # 0~1
    discolor_score: float | None         # 0~1
    scratch_score: float | None          # 0~1
    final_verdict: str                   # OK/NG (= prediction_result)
    defect_codes: list[str] = field(default_factory=list)   # {LEN,OIL,DIS,SCR,MULTI}
    confidence: float | None = None      # 0~1
    proc_time_ms: int | None = None      # 처리속도(KPI ≤300ms)
    review_flag: bool = False
    manual_verdict: str | None = None
    mes_synced: bool = False
    raw_image_path: str | None = None    # 원시 데이터셋 내 원본 경로(inspection/...)
    result_image_path: str | None = None # 본 데이터셋 내 결과 오버레이 경로(result/...)
    analysis_purpose: str = "header_pipe_quality_inspection"   # 분석 목적(고정)
    prediction_type: str = "length_and_surface_defect_verdict"  # 예측 유형(고정)
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


def field_names(record_cls: type) -> list[str]:
    """레코드 필드명 목록(문서/CLI `schema` 출력용)."""
    return [f.name for f in fields(record_cls)]


def describe_schema() -> dict[str, Any]:
    """데이터셋별 폴더 구성 + 레코드 필드 요약(정의서 명세와 대조용)."""
    return {
        "schema_version": SCHEMA_VERSION,
        DATASET_RAW: {
            "folders": [RAW_INSPECTION_DIR, RAW_CAPTURE_DIR, RAW_CALIB_DIR, RAW_INDEX_DIR],
            "records": {"RawImageRecord": field_names(RawImageRecord)},
        },
        DATASET_PROCESSED: {
            "folders": [PROC_LABELS_DIR, PROC_GT_DIR, PROC_REVIEW_DIR, PROC_MASTER_DIR],
            "records": {
                "LabelRecord": field_names(LabelRecord),
                "ReviewLabelRecord": field_names(ReviewLabelRecord),
            },
        },
        DATASET_AI: {
            "folders": [AI_INSPECTIONS_DIR, AI_RESULT_DIR, AI_KPI_DIR, AI_REPORTS_DIR],
            "records": {"InspectionRecord": field_names(InspectionRecord)},
        },
        "excluded_personal_fields": list(EXCLUDED_PERSONAL_FIELDS),
    }
