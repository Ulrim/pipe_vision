"""FAT/SAT 공통 실행 엔진 — 4지표를 자립 산출한다.

지표1 자동검사율: 전건 파이프라인 판정 완료(예외/미판정 0) 비율.
지표2 항목별 정확도: 정답셋 라벨 대비 LEN/OIL/DIS/SCR 항목별 일치 + 혼동행렬.
지표3 처리속도: 1,000장(또는 지정 N) 배치 proc_time_ms p50/p95/p99/max.
지표4 저장·연계율: 파이프라인 결과를 backend(POST /inspection, 내부토큰)로
  N건 적재 → DB 조회 건수 일치 + MES watchdog 1회 실행 후 연계율 100% 확인.

services/* 는 읽기 전용. data-ops groundtruth.build_groundtruth 로 정답셋을 읽는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import cv2

from aivis_types import ItemMaster

from vision.pipeline import InspectionPipeline, to_inspection_result

# data-ops 정답셋 빌더(부록 A.4/A.5) 재사용.
from labeling.groundtruth import GroundTruthItem, build_groundtruth

from . import dataset as ds
from . import metrics as mt


@dataclass
class SampleRun:
    """샘플 1건 실행 결과(판정 + 정답)."""

    path: str
    gt_labels: List[str]
    gt_length_mm: Optional[float]
    pred_codes: List[str]
    pred_final: str
    pred_length_mm: Optional[float]
    proc_time_ms: int
    completed: bool                # AI 자동판정 완료(예외 없이 verdict 산출)
    error: Optional[str] = None


@dataclass
class CoreResult:
    """파이프라인 단계(지표1/2/3) 산출 묶음."""

    runs: List[SampleRun] = field(default_factory=list)
    item_accuracy: mt.ItemAccuracyReport = field(default_factory=mt.ItemAccuracyReport)

    @property
    def sample_count(self) -> int:
        return len(self.runs)

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self.runs if r.completed)

    @property
    def auto_rate_pct(self) -> float:
        n = self.sample_count
        return (self.completed_count / n * 100.0) if n else 0.0


def _pred_item_flags(codes: Sequence[str]) -> Dict[str, bool]:
    s = set(codes)
    return {it: (it in s) for it in mt.DEFECT_ITEMS}


def load_groundtruth(dataset_dir: str | Path, view: Optional[str] = "SIDE") -> List[GroundTruthItem]:
    """data-ops 빌더로 정답셋 로드. 사이드카 우선, 파일명 폴백."""
    items, _errors = build_groundtruth(str(dataset_dir), view=view, strict=False)
    return items


def run_pipeline_over(
    gt_items: Sequence[GroundTruthItem],
    item: ItemMaster,
    *,
    pipeline: Optional[InspectionPipeline] = None,
) -> CoreResult:
    """정답셋 전건에 파이프라인을 돌려 지표1/2/3 입력을 모은다."""
    pipe = pipeline or InspectionPipeline()
    # 워밍업(CLAHE/캐시 등 1회 초기화로 처리속도 측정 안정화).
    if gt_items:
        warm = cv2.imread(gt_items[0].path)
        if warm is not None:
            pipe.run(warm, item)

    result = CoreResult()
    for gt in gt_items:
        img = cv2.imread(gt.path)
        completed = False
        err = None
        codes: List[str] = []
        final = "NG"
        meas = None
        proc = 0
        try:
            if img is None:
                raise RuntimeError(f"이미지 로드 실패: {gt.path}")
            v = pipe.run(img, item)
            codes = [getattr(c, "value", c) for c in v.defect_codes]
            final = getattr(v.final_verdict, "value", v.final_verdict)
            meas = v.length.meas_length_mm
            proc = v.proc_time_ms
            completed = True
        except Exception as exc:  # noqa: BLE001 - 미판정/예외는 자동검사율 미달로 집계
            err = str(exc)

        run = SampleRun(
            path=gt.path,
            gt_labels=list(gt.labels),
            gt_length_mm=gt.length_mm_gt,
            pred_codes=codes,
            pred_final=final,
            pred_length_mm=meas,
            proc_time_ms=proc,
            completed=completed,
            error=err,
        )
        result.runs.append(run)

        # 지표2: 완료된 건만 항목별 정확도에 반영(미완료는 지표1에서 차단).
        if completed:
            gt_flags = mt.gt_pred_to_item_flags(gt.labels)
            pred_flags = _pred_item_flags(codes)
            for it in mt.DEFECT_ITEMS:
                result.item_accuracy.update(it, gt_flags[it], pred_flags[it])

    return result


# ---------------------------------------------------------------------------
# 지표3 — 1,000장 배치 처리속도 (정답셋을 반복 순환해 N장 확보).
# ---------------------------------------------------------------------------
def latency_batch(
    gt_items: Sequence[GroundTruthItem],
    item: ItemMaster,
    *,
    n: int = 1000,
    pipeline: Optional[InspectionPipeline] = None,
) -> mt.LatencyReport:
    """N장 배치 proc_time_ms 백분위. 정답셋이 N보다 적으면 순환 재사용."""
    pipe = pipeline or InspectionPipeline()
    # 이미지 사전 디코드(디스크 I/O 를 측정에서 제외 — 처리속도 KPI 는 추론 시간).
    decoded = []
    for gt in gt_items:
        img = cv2.imread(gt.path)
        if img is not None:
            decoded.append(img)
    if not decoded:
        return mt.latency_report([])

    # 워밍업.
    pipe.run(decoded[0], item)

    proc_times: List[float] = []
    i = 0
    while len(proc_times) < n:
        img = decoded[i % len(decoded)]
        v = pipe.run(img, item)
        proc_times.append(float(v.proc_time_ms))
        i += 1
    return mt.latency_report(proc_times)


# ---------------------------------------------------------------------------
# 지표4 — 저장·연계율 (backend TestClient + MES watchdog).
# ---------------------------------------------------------------------------
def seed_item_master(item: ItemMaster) -> None:
    """기준정보(item_master) 행을 보장한다.

    inspection.item_code 는 item_master FK 이므로(§7.1), 적재 전에 품목이
    존재해야 한다(없으면 sqlite FK 제약 위반으로 저장 실패→로컬 큐 백업).
    QA 하니스는 정답셋과 동일 ItemMaster 를 시드한다.
    """
    from datetime import datetime, timezone

    from db.base import SessionLocal, init_db
    from db.models import ItemMaster as ItemMasterRow

    init_db()
    db = SessionLocal()
    try:
        if db.get(ItemMasterRow, item.item_code) is None:
            db.add(
                ItemMasterRow(
                    item_code=item.item_code,
                    item_name=item.item_name,
                    ref_length_mm=item.ref_length_mm,
                    tol_plus_mm=item.tol_plus_mm,
                    tol_minus_mm=item.tol_minus_mm,
                    px_to_mm_scale=item.px_to_mm_scale,
                    oil_threshold=item.oil_threshold,
                    discolor_threshold=item.discolor_threshold,
                    scratch_threshold=item.scratch_threshold,
                    capture_recipe=item.capture_recipe,
                    version=1,
                    updated_by="qa-harness",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
    finally:
        db.close()


@dataclass
class StorageMesResult:
    injected: int
    stored: int
    mes_synced: int
    storage_rate_pct: float
    mes_rate_pct: float
    passed: bool

    def as_dict(self) -> dict:
        return {
            "injected": self.injected,
            "stored": self.stored,
            "mes_synced": self.mes_synced,
            "storage_rate_pct": round(self.storage_rate_pct, 4),
            "mes_rate_pct": round(self.mes_rate_pct, 4),
            "threshold_pct": mt.STORAGE_MES_RATE_MIN,
            "passed": self.passed,
        }


def verify_storage_and_mes(
    runs: Sequence[SampleRun],
    item: ItemMaster,
    *,
    lots: Sequence[str] = ("LOT-A",),
    shifts: Sequence[str] = ("DAY",),
    cam_id: str = "CAM1",
    work_order: Optional[str] = "WO-QA",
) -> StorageMesResult:
    """파이프라인 결과를 backend 로 적재 후 DB 조회 + MES 연계율 검증.

    - 내부토큰(AIVIS_SERVICE_TOKEN)으로 POST /inspection.
    - 고유 LOT 으로 주입해 조회 격리(다중 호출/재실행 충돌 방지).
    - watchdog 1회 실행으로 table 모드 스테이징을 mes_synced=true 로 전환.
    """
    # backend 는 conftest 가 sqlite 로 굳혀둔 상태에서만 import.
    from fastapi.testclient import TestClient
    from main import app
    from core.config import get_settings
    from db.base import SessionLocal, init_db
    from mes.watchdog import get_linkage_status, run_watchdog_once

    init_db()
    seed_item_master(item)  # FK(inspection.item_code → item_master) 충족.
    settings = get_settings()
    token = settings.service_token
    headers = {"X-Service-Token": token} if token else {}

    # 격리용 고유 LOT 접두사(타임스탬프 기반).
    run_tag = datetime.now(timezone.utc).strftime("QA%Y%m%d%H%M%S%f")
    injected_lots: List[str] = []

    base_ts = datetime.now(timezone.utc)
    injected = 0
    with TestClient(app) as client:
        for idx, run in enumerate(runs):
            lot = f"{run_tag}-{lots[idx % len(lots)]}"
            if lot not in injected_lots:
                injected_lots.append(lot)
            shift = shifts[idx % len(shifts)]
            # VerdictResult 가 없으므로 SampleRun -> InspectionResult 를 직접 구성.
            from aivis_types import InspectionResult, Verdict

            ins = InspectionResult(
                lot=lot,
                work_order=work_order,
                item_code=item.item_code,
                cam_id=cam_id,
                inspected_at=base_ts + timedelta(milliseconds=idx),
                shift=shift,
                operator="qa",
                ref_length_mm=item.ref_length_mm,
                meas_length_mm=run.pred_length_mm,
                deviation_mm=(
                    round(run.pred_length_mm - item.ref_length_mm, 4)
                    if run.pred_length_mm is not None
                    else None
                ),
                length_verdict=(
                    Verdict.NG if "LEN" in run.pred_codes else Verdict.OK
                ),
                final_verdict=(
                    Verdict.NG if run.pred_final == "NG" else Verdict.OK
                ),
                defect_codes=list(run.pred_codes),
                proc_time_ms=run.proc_time_ms,
                review_flag=False,
            )
            r = client.post(
                "/inspection",
                json=ins.model_dump(mode="json"),
                headers=headers,
            )
            assert r.status_code == 201, f"POST /inspection 실패: {r.status_code} {r.text}"
            body = r.json()
            assert body.get("status") == "stored", f"미저장(큐백업?): {body}"
            injected += 1

    # DB 조회: 주입 LOT 건수 합산(저장율).
    db = SessionLocal()
    try:
        from sqlalchemy import select
        from db.models import Inspection

        stored = db.execute(
            select_count_for_lots(Inspection, injected_lots)
        ).scalar_one()

        # MES 워치독 1회(미전송 배치 처리). batch 가 작으면 여러 번.
        guard = 0
        while True:
            res = run_watchdog_once(db)
            guard += 1
            if res.scanned == 0 or guard > 100:
                break

        synced = db.execute(
            select_count_synced_for_lots(Inspection, injected_lots)
        ).scalar_one()
        status = get_linkage_status(db)  # 전체 스냅샷(진단용)
    finally:
        db.close()

    storage_rate = (stored / injected * 100.0) if injected else 0.0
    mes_rate = (synced / injected * 100.0) if injected else 0.0
    passed = (
        storage_rate >= mt.STORAGE_MES_RATE_MIN
        and mes_rate >= mt.STORAGE_MES_RATE_MIN
    )
    return StorageMesResult(
        injected=injected,
        stored=int(stored),
        mes_synced=int(synced),
        storage_rate_pct=storage_rate,
        mes_rate_pct=mes_rate,
        passed=passed,
    )


def select_count_for_lots(model, lots):
    from sqlalchemy import func, select

    return (
        select(func.count())
        .select_from(model)
        .where(model.lot.in_(list(lots)))
    )


def select_count_synced_for_lots(model, lots):
    from sqlalchemy import func, select

    return (
        select(func.count())
        .select_from(model)
        .where(model.lot.in_(list(lots)), model.mes_synced.is_(True))
    )
