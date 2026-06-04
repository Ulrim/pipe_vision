"""ORM <-> pydantic 변환 헬퍼. Numeric->float 등 타입 정규화."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from aivis_types import InspectionResult, ItemMaster as ItemMasterSchema
from db.models import Inspection, ItemMaster


def _f(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def inspection_to_schema(row: Inspection) -> InspectionResult:
    return InspectionResult(
        id=row.id,
        lot=row.lot,
        work_order=row.work_order,
        item_code=row.item_code,
        cam_id=row.cam_id,
        inspected_at=row.inspected_at,
        shift=row.shift,
        operator=row.operator,
        ref_length_mm=_f(row.ref_length_mm),
        meas_length_mm=_f(row.meas_length_mm),
        deviation_mm=_f(row.deviation_mm),
        length_verdict=row.length_verdict,
        oil_score=_f(row.oil_score),
        discolor_score=_f(row.discolor_score),
        scratch_score=_f(row.scratch_score),
        final_verdict=row.final_verdict,
        defect_codes=list(row.defect_codes or []),
        confidence=_f(row.confidence),
        raw_image_path=row.raw_image_path,
        result_image_path=row.result_image_path,
        proc_time_ms=row.proc_time_ms,
        review_flag=bool(row.review_flag),
        manual_verdict=row.manual_verdict,
        mes_synced=bool(row.mes_synced),
    )


def item_to_schema(row: ItemMaster) -> ItemMasterSchema:
    return ItemMasterSchema(
        item_code=row.item_code,
        item_name=row.item_name,
        ref_length_mm=_f(row.ref_length_mm),
        tol_plus_mm=_f(row.tol_plus_mm),
        tol_minus_mm=_f(row.tol_minus_mm),
        px_to_mm_scale=_f(row.px_to_mm_scale),
        oil_threshold=_f(row.oil_threshold),
        discolor_threshold=_f(row.discolor_threshold),
        scratch_threshold=_f(row.scratch_threshold),
        capture_recipe=row.capture_recipe,
        version=row.version,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )
