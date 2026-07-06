"""검사결과 저장 서비스 (CLAUDE.md §5 M7, §7.3 MES).

핵심: 한 트랜잭션 안에서 inspection INSERT + mes_quality_if 스테이징 INSERT 를
함께 커밋해 무결성을 보장한다. 저장 실패 시 호출자가 로컬 큐(local_queue)에
백업하고 재시도해 저장 성공률 100% 를 노린다.

멱등성(엣지 오프라인 스풀 재전송 대응): POST /inspection 재전송이 행을 중복
생성하지 않도록 자연키 = MES 멱등키와 동일한 (lot, item_code, inspected_at,
cam_id) 로 기존 행을 찾고, 있으면 INSERT 를 생략하고 기존 행을 반환한다.
동시성 레이스는 유니크 인덱스 `ux_insp_natkey` + IntegrityError 재조회로 방어.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aivis_types import InspectionResult
from db.models import Inspection, MesQualityIf


def make_idem_key(r: InspectionResult) -> str:
    """MES 멱등키 = lot+item+inspected_at+cam_id+tube_index (§7.3).

    tube_index 포함: 같은 프레임(배치)의 튜브 N개가 동일 lot/item/cam/시각을
    공유해도 튜브별로 별도 MES 스테이징 행이 되도록 한다(중복 병합 방지).
    단일 튜브(tube_index=0)는 기존 키 뒤에 |0 만 붙어 동작은 동일하다.
    """
    ts = r.inspected_at.isoformat()
    return f"{r.lot}|{r.item_code}|{ts}|{r.cam_id}|{r.tube_index}"


def _verdict_value(v) -> str | None:
    if v is None:
        return None
    return getattr(v, "value", v)


def find_by_natural_key(db: Session, result: InspectionResult) -> Inspection | None:
    """자연키 (lot, item_code, inspected_at, cam_id, tube_index) 로 기존 행 조회.

    자연키는 MES 멱등키(make_idem_key, §7.3)와 동일 구성이다. cam_id/inspected_at
    /lot/item_code 만으로도 ms 정밀도 덕에 사실상 유일하지만, tube_index 를 포함해
    같은 프레임(배치)의 튜브 N개(0..N-1)를 서로 다른 제품으로 구분한다. 단일 튜브
    (tube_index=0)는 기존과 동일하게 dedup 된다. (sqlite/postgres 공통: SQLAlchemy
    가 None 비교를 IS NULL 로 변환하므로 item_code 미지정 payload 도 동작.)
    """
    stmt = (
        select(Inspection)
        .where(
            Inspection.cam_id == result.cam_id,
            Inspection.inspected_at == result.inspected_at,
            Inspection.lot == result.lot,
            Inspection.item_code == result.item_code,
            Inspection.tube_index == result.tube_index,
        )
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def save_inspection(
    db: Session, result: InspectionResult, *, mes_mode: str = "table"
) -> tuple[Inspection, bool]:
    """검사결과를 트랜잭션으로 저장. table 모드면 MES 스테이징도 동시 INSERT.

    반환: (행, created). 동일 자연키 행이 이미 있으면 INSERT 를 생략하고
    (기존 행, False) 를 반환한다 — 엣지 스풀 재전송(서버는 저장했으나 응답
    유실) 시 중복 적재 방지. MES 스테이징도 기존 idem_key 로직이 중복을 막는다.

    예외 발생 시 호출자가 롤백 후 로컬 큐 백업하도록 예외를 전파한다.
    멱등성: 동일 idem_key 의 mes_quality_if 가 이미 있으면 스테이징은 건너뛴다.
    """
    existing = find_by_natural_key(db, result)
    if existing is not None:
        return existing, False

    try:
        row = _insert_inspection(db, result, mes_mode=mes_mode)
        db.commit()
    except IntegrityError:
        # 동시 재전송 레이스: 유니크 인덱스(ux_insp_natkey / mes idem_key) 충돌.
        # 롤백 후 자연키로 재조회 — 있으면 상대 트랜잭션이 이긴 것(멱등 성공).
        db.rollback()
        existing = find_by_natural_key(db, result)
        if existing is not None:
            return existing, False
        raise  # 자연키 충돌이 아닌 다른 무결성 오류(FK 등)는 그대로 전파.

    db.refresh(row)
    return row, True


def _insert_inspection(db: Session, result: InspectionResult, *, mes_mode: str) -> Inspection:
    """inspection INSERT + (table 모드) mes_quality_if 멱등 스테이징. commit 은 호출자."""
    defect_codes = [getattr(c, "value", c) for c in (result.defect_codes or [])]

    row = Inspection(
        lot=result.lot,
        work_order=result.work_order,
        item_code=result.item_code,
        cam_id=result.cam_id,
        inspected_at=result.inspected_at,
        tube_index=result.tube_index,
        shift=result.shift,
        operator=result.operator,
        ref_length_mm=result.ref_length_mm,
        meas_length_mm=result.meas_length_mm,
        deviation_mm=result.deviation_mm,
        length_verdict=_verdict_value(result.length_verdict),
        oil_score=result.oil_score,
        discolor_score=result.discolor_score,
        scratch_score=result.scratch_score,
        final_verdict=_verdict_value(result.final_verdict),
        defect_codes=defect_codes,
        confidence=result.confidence,
        raw_image_path=result.raw_image_path,
        result_image_path=result.result_image_path,
        proc_time_ms=result.proc_time_ms,
        review_flag=result.review_flag,
        manual_verdict=_verdict_value(result.manual_verdict),
        mes_synced=result.mes_synced,
    )
    db.add(row)
    db.flush()  # row.id 확보

    if mes_mode == "table":
        idem = make_idem_key(result)
        exists = db.execute(
            select(MesQualityIf.id).where(MesQualityIf.idem_key == idem)
        ).first()
        if not exists:
            db.add(
                MesQualityIf(
                    inspection_id=row.id,
                    lot=result.lot,
                    item_code=result.item_code,
                    inspected_at=result.inspected_at,
                    cam_id=result.cam_id,
                    idem_key=idem,
                    work_order=result.work_order,
                    final_verdict=_verdict_value(result.final_verdict),
                    defect_codes=defect_codes,
                    meas_length_mm=result.meas_length_mm,
                    deviation_mm=result.deviation_mm,
                )
            )

    return row
