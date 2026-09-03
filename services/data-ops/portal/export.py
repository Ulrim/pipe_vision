"""데이터포털 제출용 내보내기 (원시/가공/AI분석) — 협약서 제16조 데이터의 수집·활용.

AIVIS 운영 데이터(검사결과 DB + 원본/결과 이미지)와 학습 데이터(부록 A.4 촬영본 +
사이드카 라벨)를 `portal.layout` 규격의 폴더로 내보낸다. 내보낸 폴더는 그대로
전남TP `upload.sh`(A안) 또는 `portal.upload`(B안 API 직접)로 전송한다.

- 원시(raw): 검사 원본 이미지(inspection/) + 학습 촬영 원본(capture/, 선택) +
  캘리브레이션 촬영(calib/, 선택) + 촬영 메타 인덱스(index/*.jsonl)
- 가공(processed): 라벨(labels/), 정답셋 매니페스트(groundtruth/), 작업자 재확인
  라벨(review/), 판정 기준정보 스냅샷(master/)
- AI분석(ai-analysis): 판정 결과 레코드(inspections/*.jsonl), 결과 오버레이 이미지
  (result/), 월간 KPI(kpi/), 검증 리포트(reports/)

원칙:
- 원본 불변: 파일은 복사(가능하면 하드링크)하고 내용은 절대 수정하지 않는다.
- 개인정보 미포함: operator/inspector/updated_by 는 레코드에서 제외한다(layout).
- 증분 내보내기: since/until(검사 시각) 창으로 운영 데이터를 나눠 보낸다. 스냅샷 성격
  (라벨/정답셋/재확인 라벨/기준정보/KPI/리포트)은 매 실행 전량 재생성한다(포털은 같은
  경로 재전송 시 최신 내용으로 갱신).
- DB 는 backend 모델을 import 만 한다(정의 변경 금지).
"""
from __future__ import annotations

import json
import os
import shutil
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.models import Inspection, ItemMaster, KpiManual
from labeling.groundtruth import LabelParseError, build_groundtruth, parse_filename
from portal.layout import (
    AI_INSPECTIONS_DIR,
    AI_KPI_DIR,
    AI_REPORTS_DIR,
    AI_RESULT_DIR,
    DATASET_AI,
    DATASET_PROCESSED,
    DATASET_RAW,
    GT_MANIFEST_NAME,
    ITEM_MASTER_NAME,
    PROC_GT_DIR,
    PROC_LABELS_DIR,
    PROC_MASTER_DIR,
    PROC_REVIEW_DIR,
    RAW_CALIB_DIR,
    RAW_CAPTURE_DIR,
    RAW_INDEX_DIR,
    RAW_INSPECTION_DIR,
    REVIEW_LABELS_NAME,
    SCHEMA_VERSION,
    InspectionRecord,
    LabelRecord,
    RawImageRecord,
    ReviewLabelRecord,
)

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
_REPORT_EXTS = {".json", ".md"}

# JPEG SOF 마커(프레임 헤더: 정밀도/높이/너비). DHT(C4)/JPG(C8)/DAC(CC) 제외.
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_run_id(now: datetime | None = None) -> str:
    """회차 ID: UTC 시각 기반(예 20260903T021700Z). 파일명·폴더명에 그대로 쓴다."""
    now = now or utc_now()
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _num(v: Any) -> float | int | None:
    """NUMERIC(Decimal) → float. JSON 직렬화용."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


def _db_dt(dt: datetime | None, db: Session) -> datetime | None:
    """DB 방언에 맞춘 비교용 datetime. sqlite 는 tz 없는 UTC, 그 외는 aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if db.get_bind().dialect.name == "sqlite":
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def image_dimensions(path: str | os.PathLike[str]) -> tuple[int | None, int | None]:
    """JPEG/PNG 헤더에서 (가로, 세로) px 를 읽는다. 외부 의존 없음. 실패 시 (None, None)."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:2] != b"\xff\xd8":
                return None, None
            f.seek(2)
            while True:
                b = f.read(1)
                if not b:
                    return None, None
                if b != b"\xff":
                    continue
                marker = f.read(1)
                if not marker:
                    return None, None
                m = marker[0]
                if m == 0xFF:            # 패딩 0xFF — 다음 바이트가 마커일 수 있음
                    f.seek(-1, os.SEEK_CUR)
                    continue
                if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:   # 길이 없는 독립 마커
                    continue
                if m == 0xD9:            # EOI
                    return None, None
                lb = f.read(2)
                if len(lb) < 2:
                    return None, None
                seglen = struct.unpack(">H", lb)[0]
                if m in _JPEG_SOF:
                    data = f.read(5)
                    if len(data) < 5:
                        return None, None
                    h, w = struct.unpack(">HH", data[1:5])
                    return int(w), int(h)
                f.seek(max(seglen - 2, 0), os.SEEK_CUR)
    except OSError:
        return None, None


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _partition(prefix: str, ts: datetime, name: str) -> str:
    """{prefix}/YYYY/MM/DD/{name} (UTC 날짜 파티션)."""
    ts = _as_utc(ts)
    return f"{prefix}/{ts:%Y}/{ts:%m}/{ts:%d}/{name}"


def _link_or_copy(src: Path, dst: Path) -> None:
    """원본 불변 복사. 같은 볼륨이면 하드링크(용량 절약), 아니면 copy2."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _resolve_dataset_dirs(dataset_dir: str | None) -> tuple[Path | None, Path | None]:
    """AIVIS_DATASET_DIR 해석: (raw/ 폴더, calib/ 폴더).

    루트(`dataset/`)와 raw 폴더(`dataset/raw`) 어느 쪽을 가리켜도 동작한다.
    """
    if not dataset_dir:
        return None, None
    p = Path(dataset_dir)
    if (p / "raw").is_dir():
        return p / "raw", p / "calib"
    if p.is_dir():
        return p, p.parent / "calib"
    return None, None


# ---------------------------------------------------------------------------
# 옵션 / 요약
# ---------------------------------------------------------------------------

@dataclass
class ExportOptions:
    """내보내기 옵션. 시각은 검사 시각(inspected_at) 기준 (since, until] 창."""

    images_dir: str                      # AIVIS_IMAGES_DIR (raw/ result/ review/)
    dataset_dir: str | None = None       # AIVIS_DATASET_DIR (부록 A.4 학습 촬영본)
    reports_dir: str | None = None       # FAT/SAT/MSA 리포트 폴더(tests/fat/report 등)
    since: datetime | None = None        # 미지정 = 처음부터
    until: datetime | None = None        # 미지정 = 지금
    include_capture: bool = False        # 학습 촬영 원본(대용량, 일괄 1회) 포함
    include_calib: bool = False          # 캘리브레이션 촬영 포함
    view: str = "SIDE"                   # 운영 검사 촬영 구도(현행 측면 단일 카메라)
    run_id: str | None = None


@dataclass
class ExportSummary:
    dataset: str
    out_dir: str
    run_id: str
    files: int = 0
    bytes: int = 0
    records: int = 0
    since: str | None = None
    until: str | None = None
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "out_dir": self.out_dir,
            "run_id": self.run_id,
            "files": self.files,
            "bytes": self.bytes,
            "records": self.records,
            "since": self.since,
            "until": self.until,
            "skipped": len(self.skipped),
            "skipped_detail": self.skipped[:50],
        }


class _Writer:
    """out_dir 하위 파일 생성 + 집계."""

    def __init__(self, out_dir: Path, summary: ExportSummary) -> None:
        self.out = out_dir
        self.summary = summary

    def place(self, src: Path, rel: str) -> None:
        _link_or_copy(src, self.out / rel)
        self.summary.files += 1
        self.summary.bytes += src.stat().st_size

    def write_json(self, rel: str, payload: Any) -> None:
        dst = self.out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        dst.write_text(data, encoding="utf-8")
        self.summary.files += 1
        self.summary.bytes += len(data.encode("utf-8"))

    def write_jsonl(self, rel: str, rows: Iterable[dict[str, Any]]) -> int:
        dst = self.out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(dst, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        self.summary.files += 1
        self.summary.bytes += dst.stat().st_size
        return n


def _window(db: Session, opts: ExportOptions) -> tuple[datetime | None, datetime]:
    until = opts.until or utc_now()
    return opts.since, until


def _inspection_rows(db: Session, since: datetime | None, until: datetime) -> list[Inspection]:
    stmt = select(Inspection).where(Inspection.inspected_at <= _db_dt(until, db))
    if since is not None:
        stmt = stmt.where(Inspection.inspected_at > _db_dt(since, db))
    stmt = stmt.order_by(Inspection.inspected_at.asc(), Inspection.id.asc())
    return list(db.execute(stmt).scalars().all())


def _portal_raw_path(row: Inspection) -> str | None:
    """운영 원본 이미지의 원시 데이터셋 내 경로(inspection/YYYY/MM/DD/name)."""
    if not row.raw_image_path:
        return None
    return _partition(RAW_INSPECTION_DIR, row.inspected_at, Path(row.raw_image_path).name)


def _portal_result_path(row: Inspection) -> str | None:
    if not row.result_image_path:
        return None
    return _partition(AI_RESULT_DIR, row.inspected_at, Path(row.result_image_path).name)


# ---------------------------------------------------------------------------
# 원시(raw)
# ---------------------------------------------------------------------------

def _iter_images(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _IMG_EXTS and not p.name.startswith(".")
    )


def _sidecar(img: Path) -> dict[str, Any] | None:
    sc = img.with_suffix(".json")
    if not sc.is_file():
        return None
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def export_raw(db: Session, out_dir: str | os.PathLike[str], opts: ExportOptions) -> ExportSummary:
    """원시 데이터셋 내보내기."""
    since, until = _window(db, opts)
    run_id = opts.run_id or make_run_id(until)
    out = Path(out_dir)
    summary = ExportSummary(DATASET_RAW, str(out), run_id, since=_iso(since), until=_iso(until))
    w = _Writer(out, summary)
    records: list[RawImageRecord] = []

    # 1) 운영 검사 원본 (DB inspection.raw_image_path → images_dir 상대경로)
    seen: dict[str, RawImageRecord] = {}
    for row in _inspection_rows(db, since, until):
        rel = row.raw_image_path
        if not rel:
            summary.skipped.append({"inspection_id": row.id, "reason": "raw_image_path 없음"})
            continue
        if rel in seen:                       # 다중 튜브 배치: 프레임 1장 = 행 N개
            seen[rel].tube_count += 1
            continue
        src = Path(opts.images_dir) / rel
        if not src.is_file():
            summary.skipped.append({"inspection_id": row.id, "path": rel, "reason": "원본 파일 없음"})
            continue
        dst_rel = _portal_raw_path(row) or ""
        w.place(src, dst_rel)
        width, height = image_dimensions(src)
        rec = RawImageRecord(
            file_path=dst_rel, file_name=src.name, file_type=_ext(src),
            file_size=src.stat().st_size, source="inspection",
            captured_at=_iso(row.inspected_at), cam_id=row.cam_id, item_code=row.item_code,
            view=opts.view, width=width, height=height, lot=row.lot,
            work_order=row.work_order, inspection_id=row.id,
        )
        seen[rel] = rec
    records.extend(seen.values())

    # 2) 학습 촬영 원본(부록 A.4) — 대용량이므로 옵션(일괄 1회 권장)
    raw_dir, calib_dir = _resolve_dataset_dirs(opts.dataset_dir)
    if opts.include_capture and raw_dir is not None:
        for img in _iter_images(raw_dir):
            cls = img.parent.name if img.parent != raw_dir else "UNSORTED"
            dst_rel = f"{RAW_CAPTURE_DIR}/{cls}/{img.name}"
            w.place(img, dst_rel)
            item_code = view = captured_at = None
            try:
                parsed = parse_filename(img.name)
                item_code, view = parsed["item"], parsed["view"]
                captured_at = datetime.strptime(parsed["ts"], "%Y%m%d-%H%M%S").isoformat()
            except (LabelParseError, ValueError):
                summary.skipped.append({"path": str(img), "reason": "파일명 규칙 불일치(부록 A.4) — 메타 일부 누락"})
            sc = _sidecar(img) or {}
            width, height = image_dimensions(img)
            records.append(RawImageRecord(
                file_path=dst_rel, file_name=img.name, file_type=_ext(img),
                file_size=img.stat().st_size, source="capture",
                captured_at=sc.get("captured_at") or captured_at,
                cam_id=sc.get("cam_id"), item_code=sc.get("item_code") or item_code,
                view=sc.get("view") or view, width=width, height=height, capture_class=cls,
            ))

    # 3) 캘리브레이션 촬영(스케일 기준자)
    if opts.include_calib and calib_dir is not None and calib_dir.is_dir():
        for img in _iter_images(calib_dir):
            dst_rel = f"{RAW_CALIB_DIR}/{img.relative_to(calib_dir).as_posix()}"
            w.place(img, dst_rel)
            sc = _sidecar(img) or {}
            width, height = image_dimensions(img)
            records.append(RawImageRecord(
                file_path=dst_rel, file_name=img.name, file_type=_ext(img),
                file_size=img.stat().st_size, source="calib",
                captured_at=sc.get("captured_at"), cam_id=sc.get("cam_id"),
                item_code=sc.get("item_code"), view=sc.get("view"), width=width, height=height,
            ))

    # 4) 촬영 메타 인덱스(회차별 파일 → 같은 날 재실행 시 덮어쓰기 방지)
    summary.records = w.write_jsonl(
        f"{RAW_INDEX_DIR}/raw_images_{run_id}.jsonl", (r.as_dict() for r in records)
    )
    return summary


# ---------------------------------------------------------------------------
# 가공(processed)
# ---------------------------------------------------------------------------

def miss_kind(final_verdict: str | None, manual_verdict: str | None) -> str | None:
    """오검/미검 분류(retrain.review 와 동일 정의). 일치·미입력이면 None."""
    if manual_verdict is None or final_verdict is None or manual_verdict == final_verdict:
        return None
    if final_verdict == "NG" and manual_verdict == "OK":
        return "system_ng_human_ok"      # 오검(과검출)
    if final_verdict == "OK" and manual_verdict == "NG":
        return "system_ok_human_ng"      # 미검(누락)
    return "mismatch"


def _review_rows(db: Session) -> list[Inspection]:
    mismatch = (
        Inspection.manual_verdict.is_not(None)
        & (Inspection.manual_verdict != Inspection.final_verdict)
    )
    stmt = (
        select(Inspection)
        .where(or_(mismatch, Inspection.review_flag.is_(True)))
        .order_by(Inspection.inspected_at.asc(), Inspection.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def _item_master_snapshot(db: Session) -> list[dict[str, Any]]:
    items = db.execute(select(ItemMaster).order_by(ItemMaster.item_code)).scalars().all()
    out = []
    for it in items:
        out.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "ref_length_mm": _num(it.ref_length_mm),
            "tol_plus_mm": _num(it.tol_plus_mm),
            "tol_minus_mm": _num(it.tol_minus_mm),
            "px_to_mm_scale": _num(it.px_to_mm_scale),
            "oil_threshold": _num(it.oil_threshold),
            "discolor_threshold": _num(it.discolor_threshold),
            "scratch_threshold": _num(it.scratch_threshold),
            "capture_recipe": it.capture_recipe,
            "expected_count": it.expected_count,
            "outer_diameter_mm": _num(it.outer_diameter_mm),
            "version": it.version,
            "updated_at": _iso(it.updated_at),
        })
    return out


def export_processed(db: Session, out_dir: str | os.PathLike[str], opts: ExportOptions) -> ExportSummary:
    """가공 데이터셋 내보내기(스냅샷: 매 실행 전량)."""
    since, until = _window(db, opts)
    run_id = opts.run_id or make_run_id(until)
    out = Path(out_dir)
    summary = ExportSummary(DATASET_PROCESSED, str(out), run_id, since=_iso(since), until=_iso(until))
    w = _Writer(out, summary)
    generated_at = _iso(until)

    # 1) 라벨(사이드카) + 정답셋 매니페스트 — 학습 데이터셋이 있을 때
    raw_dir, _calib = _resolve_dataset_dirs(opts.dataset_dir)
    if raw_dir is not None:
        items, errors = build_groundtruth(str(raw_dir))
        gt_items: list[dict[str, Any]] = []
        for it in items:
            p = Path(it.path)
            cls = p.parent.name if p.parent != raw_dir else "UNSORTED"
            rec = LabelRecord(
                image_path=f"{RAW_CAPTURE_DIR}/{cls}/{p.name}",
                item_code=it.item_code, view=it.view, labels=list(it.labels),
                border=it.border, length_mm_gt=it.length_mm_gt, scale_ref_mm=it.scale_ref_mm,
                lighting=it.meta.get("lighting"), captured_at=it.meta.get("captured_at"),
                note=it.meta.get("note"), label_source=it.source,
            )
            d = rec.as_dict()
            w.write_json(f"{PROC_LABELS_DIR}/{cls}/{p.stem}.json", d)
            gt_items.append(d)
            summary.records += 1
        w.write_json(f"{PROC_GT_DIR}/{GT_MANIFEST_NAME}", {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "count": len(gt_items),
            "ok_count": sum(1 for d in gt_items if not d["labels"]),
            "ng_count": sum(1 for d in gt_items if d["labels"]),
            "border_count": sum(1 for d in gt_items if d["border"]),
            "errors": [{"path": Path(e["path"]).name, "error": e["error"]} for e in errors],
            "items": gt_items,
        })
        for e in errors:
            summary.skipped.append({"path": e["path"], "reason": e["error"]})

    # 2) 작업자 재확인 라벨(오검·미검) — DB 전량 스냅샷(작은 파일)
    review_records = []
    for row in _review_rows(db):
        review_records.append(ReviewLabelRecord(
            inspection_id=row.id, lot=row.lot, item_code=row.item_code,
            inspected_at=_iso(row.inspected_at) or "", final_verdict=row.final_verdict,
            manual_verdict=row.manual_verdict,
            miss_kind=miss_kind(row.final_verdict, row.manual_verdict),
            review_flag=bool(row.review_flag), defect_codes=list(row.defect_codes or []),
            raw_image_path=_portal_raw_path(row), result_image_path=_portal_result_path(row),
            oil_score=_num(row.oil_score), discolor_score=_num(row.discolor_score),
            scratch_score=_num(row.scratch_score), deviation_mm=_num(row.deviation_mm),
        ).as_dict())
    summary.records += w.write_jsonl(f"{PROC_REVIEW_DIR}/{REVIEW_LABELS_NAME}", review_records)

    # 3) 판정 기준정보 스냅샷(품목별 기준길이/공차/임계값/촬영 레시피)
    w.write_json(f"{PROC_MASTER_DIR}/{ITEM_MASTER_NAME}", {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "items": _item_master_snapshot(db),
    })
    return summary


# ---------------------------------------------------------------------------
# AI분석(ai-analysis)
# ---------------------------------------------------------------------------

def _rate(num: float, den: float, scale: float) -> float:
    return (num / den) * scale if den else 0.0


def compute_kpi(rows: Sequence[Inspection], period: str, manual: KpiManual | None = None) -> dict[str, Any]:
    """§1.1 산출식 그대로(api routers/kpi._compute_summary 와 동일 정의).

    - 공정불량률(ppm) = NG ÷ 총검사 × 1,000,000
    - 검사불량률(%) = (오검 + 미검) ÷ 총검사 × 100
      오검 = manual_verdict 입력 & ≠ final_verdict, 미검 = review_flag & manual 미입력
    - 자동검사율(%) = final_verdict 존재 ÷ 총검사 × 100
    - 저장&MES 연계율(%) = mes_synced ÷ 저장 × 100
    """
    total = len(rows)
    defect = sum(1 for r in rows if r.final_verdict == "NG")
    auto = sum(1 for r in rows if r.final_verdict)
    misjudge = sum(1 for r in rows if r.manual_verdict is not None and r.manual_verdict != r.final_verdict)
    miss = sum(1 for r in rows if r.review_flag and r.manual_verdict is None)
    synced = sum(1 for r in rows if r.mes_synced)
    times = [r.proc_time_ms for r in rows if r.proc_time_ms is not None]
    avg_ms = (sum(times) / len(times)) if times else None
    return {
        "schema_version": SCHEMA_VERSION,
        "period": period,
        "total_inspected": total,
        "defect_count": defect,
        "process_defect_ppm": round(_rate(defect, total, 1_000_000.0), 3),
        "auto_inspected": auto,
        "auto_inspection_rate_pct": round(_rate(auto, total, 100.0), 3),
        "misjudge_count": misjudge,
        "miss_count": miss,
        "inspection_defect_rate_pct": round(_rate(misjudge + miss, total, 100.0), 3),
        "stored_count": total,
        "mes_synced_count": synced,
        "storage_mes_rate_pct": round(_rate(synced, total, 100.0), 3),
        "avg_proc_time_ms": round(avg_ms, 2) if avg_ms is not None else None,
        "claim_count": manual.claim_count if manual else None,
        "workload_index": _num(manual.workload_index) if manual else None,
        "lead_time_days": _num(manual.lead_time_days) if manual else None,
    }


def _month_rows(db: Session, year: int, month: int) -> list[Inspection]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    stmt = select(Inspection).where(
        Inspection.inspected_at >= _db_dt(start, db), Inspection.inspected_at < _db_dt(end, db)
    )
    return list(db.execute(stmt).scalars().all())


def _to_inspection_record(row: Inspection) -> InspectionRecord:
    return InspectionRecord(
        inspection_id=row.id, lot=row.lot, work_order=row.work_order, item_code=row.item_code,
        cam_id=row.cam_id, inspected_at=_iso(row.inspected_at) or "", tube_index=row.tube_index,
        shift=row.shift, ref_length_mm=_num(row.ref_length_mm), meas_length_mm=_num(row.meas_length_mm),
        deviation_mm=_num(row.deviation_mm), length_verdict=row.length_verdict,
        oil_score=_num(row.oil_score), discolor_score=_num(row.discolor_score),
        scratch_score=_num(row.scratch_score), final_verdict=row.final_verdict,
        defect_codes=list(row.defect_codes or []), confidence=_num(row.confidence),
        proc_time_ms=row.proc_time_ms, review_flag=bool(row.review_flag),
        manual_verdict=row.manual_verdict, mes_synced=bool(row.mes_synced),
        raw_image_path=_portal_raw_path(row), result_image_path=_portal_result_path(row),
    )


def export_ai_analysis(db: Session, out_dir: str | os.PathLike[str], opts: ExportOptions) -> ExportSummary:
    """AI분석 데이터셋 내보내기."""
    since, until = _window(db, opts)
    run_id = opts.run_id or make_run_id(until)
    out = Path(out_dir)
    summary = ExportSummary(DATASET_AI, str(out), run_id, since=_iso(since), until=_iso(until))
    w = _Writer(out, summary)

    rows = _inspection_rows(db, since, until)

    # 1) 판정 결과 레코드(JSONL, 검사일자 파티션 + 회차 접미사)
    by_day: dict[str, list[dict[str, Any]]] = {}
    months: set[tuple[int, int]] = set()
    for row in rows:
        ts = _as_utc(row.inspected_at)
        by_day.setdefault(f"{ts:%Y}/{ts:%m}/inspections_{ts:%Y%m%d}_{run_id}.jsonl", []).append(
            _to_inspection_record(row).as_dict()
        )
        months.add((ts.year, ts.month))
    for rel, recs in sorted(by_day.items()):
        summary.records += w.write_jsonl(f"{AI_INSPECTIONS_DIR}/{rel}", recs)

    # 2) 결과 오버레이 이미지
    seen: set[str] = set()
    for row in rows:
        rel = row.result_image_path
        if not rel or rel in seen:
            continue
        seen.add(rel)
        src = Path(opts.images_dir) / rel
        if not src.is_file():
            summary.skipped.append({"inspection_id": row.id, "path": rel, "reason": "결과 파일 없음"})
            continue
        w.place(src, _portal_result_path(row) or "")

    # 3) 월간 KPI(§1.1) — 창에 걸린 각 월 전량 재산출(스냅샷)
    for year, month in sorted(months):
        period = f"{year:04d}-{month:02d}"
        manual = db.get(KpiManual, datetime(year, month, 1))
        w.write_json(f"{AI_KPI_DIR}/kpi_{period}.json", compute_kpi(_month_rows(db, year, month), period, manual))

    # 4) 검증 리포트(FAT/SAT/MSA) 스냅샷
    if opts.reports_dir and Path(opts.reports_dir).is_dir():
        for p in sorted(Path(opts.reports_dir).iterdir()):
            if p.is_file() and p.suffix.lower() in _REPORT_EXTS:
                w.place(p, f"{AI_REPORTS_DIR}/{p.name}")
    return summary


# ---------------------------------------------------------------------------
# 통합
# ---------------------------------------------------------------------------

EXPORTERS = {
    DATASET_RAW: export_raw,
    DATASET_PROCESSED: export_processed,
    DATASET_AI: export_ai_analysis,
}


def export_dataset(dataset: str, db: Session, out_dir: str | os.PathLike[str], opts: ExportOptions) -> ExportSummary:
    if dataset not in EXPORTERS:
        raise ValueError(f"알 수 없는 데이터셋: {dataset} (허용: {', '.join(EXPORTERS)})")
    return EXPORTERS[dataset](db, out_dir, opts)
