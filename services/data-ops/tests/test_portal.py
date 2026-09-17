"""portal 모듈 테스트 — 포털 제출 레이아웃/개인정보 제외/증분 내보내기, 업로드 배치·규칙·재시도,
CLI run 워터마크·대기분 재전송. 외부 의존 없이 임시 sqlite + 가짜 전송으로 통과한다."""
from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from pathlib import Path

import pytest

from db.models import Inspection
from portal import cli as portal_cli
from portal.export import (
    ExportOptions,
    compute_kpi,
    export_ai_analysis,
    export_processed,
    export_raw,
    image_dimensions,
)
from portal.layout import (
    EXCLUDED_PERSONAL_FIELDS,
    InspectionRecord,
    dataset_raw_name,
    dataset_result_name,
    describe_schema,
    stage_token,
)
from portal.upload import (
    FakePortalTransport,
    PortalUploader,
    credentials_from_conf,
    load_conf,
    make_batches,
    plan_files,
)

UNTIL = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _jpeg_bytes(w: int = 640, h: int = 480) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x03" \
        + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _png_bytes(w: int, h: int) -> bytes:
    ihdr = struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


def _images_dir(tmp_path: Path) -> Path:
    d = tmp_path / "images"
    for name in ("L1_HP12_20260902100000000_OK.jpg", "L1_HP12_20260902100500000_NG.jpg"):
        (d / "raw").mkdir(parents=True, exist_ok=True)
        (d / "result").mkdir(parents=True, exist_ok=True)
        (d / "raw" / name).write_bytes(_jpeg_bytes())
        (d / "result" / name).write_bytes(_jpeg_bytes(320, 240))
    return d


def _add(db, ts: datetime, **kw) -> Inspection:
    row = Inspection(
        lot=kw.pop("lot", "L1"), item_code="HP12", cam_id=kw.pop("cam_id", "cam-01"),
        inspected_at=ts, final_verdict=kw.pop("final_verdict", "OK"), **kw,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed(db) -> list[Inspection]:
    a = "raw/L1_HP12_20260902100000000_OK.jpg"
    b = "raw/L1_HP12_20260902100500000_NG.jpg"
    r1 = _add(db, datetime(2026, 9, 2, 10, 0), raw_image_path=a, result_image_path=a.replace("raw/", "result/"),
              operator="김작업", inspection_stage="CUT_LENGTH", meas_length_mm=250.1,
              deviation_mm=0.1, proc_time_ms=12, mes_synced=True)
    r2 = _add(db, datetime(2026, 9, 2, 10, 5), final_verdict="NG", defect_codes=["SCR"], raw_image_path=b,
              result_image_path=b.replace("raw/", "result/"), tube_index=0, scratch_score=0.91,
              inspection_stage="CUT_LENGTH", proc_time_ms=18)
    r3 = _add(db, datetime(2026, 9, 2, 10, 5), raw_image_path=b, result_image_path=b.replace("raw/", "result/"),
              tube_index=1, inspection_stage="CUT_LENGTH", proc_time_ms=18, review_flag=True)
    r4 = _add(db, datetime(2026, 9, 2, 11, 0), raw_image_path="raw/missing.jpg", result_image_path="result/missing.jpg")
    return [r1, r2, r3, r4]


# ---------------------------------------------------------------------------
# 이미지 헤더 / 레이아웃
# ---------------------------------------------------------------------------

def test_image_dimensions_jpeg_png_and_garbage(tmp_path):
    (tmp_path / "a.jpg").write_bytes(_jpeg_bytes(2304, 1296))
    (tmp_path / "b.png").write_bytes(_png_bytes(800, 300))
    (tmp_path / "c.jpg").write_bytes(b"\xff\xd8\xff")
    assert image_dimensions(tmp_path / "a.jpg") == (2304, 1296)
    assert image_dimensions(tmp_path / "b.png") == (800, 300)
    assert image_dimensions(tmp_path / "c.jpg") == (None, None)
    assert image_dimensions(tmp_path / "none.jpg") == (None, None)


def test_schema_excludes_personal_fields():
    schema = describe_schema()
    assert set(schema["excluded_personal_fields"]) == set(EXCLUDED_PERSONAL_FIELDS)
    for f in EXCLUDED_PERSONAL_FIELDS:
        for ds in ("raw", "processed", "ai-analysis"):
            for names in schema[ds]["records"].values():
                assert f not in names
    rec = InspectionRecord(inspection_id=1, lot="L", work_order=None, item_code="HP12", cam_id="c",
                           inspection_stage="CUT_LENGTH",
                           inspected_at="2026-09-02T19:00:00+09:00", tube_index=0, shift=None,
                           ref_length_mm=None, meas_length_mm=None, deviation_mm=None, length_verdict=None,
                           oil_score=None, discolor_score=None, scratch_score=None, final_verdict="OK")
    assert "operator" not in rec.as_dict()


# ---------------------------------------------------------------------------
# 내보내기: 원시 / AI분석
# ---------------------------------------------------------------------------

def test_export_raw_layout_index_and_multitube(db, tmp_path):
    images = _images_dir(tmp_path)
    _seed(db)
    out = tmp_path / "out" / "raw"
    s = export_raw(db, out, ExportOptions(images_dir=str(images), until=UNTIL, run_id="r1"))

    # 정의서 3-2 규격명: {LOT}_{품목}_{STAGE}_{stamp(KST)}_{inspection_id}.jpg
    # 원본에는 판정(OK/NG)이 들어가지 않는다 — 학습 입력에 정답이 새면 안 된다.
    assert (out / "inspection/2026/09/02/L1_HP12_CUT_20260902190000000_1.jpg").is_file()
    assert (out / "inspection/2026/09/02/L1_HP12_CUT_20260902190500000_2.jpg").is_file()
    assert not list((out / "inspection").rglob("*_OK.jpg"))
    assert not list((out / "inspection").rglob("*_NG.jpg"))
    idx = out / "index/raw_images_r1.jsonl"
    recs = [json.loads(line) for line in idx.read_text(encoding="utf-8").splitlines()]
    assert s.records == 2 and len(recs) == 2
    by_name = {r["file_name"]: r for r in recs}
    ng = by_name["L1_HP12_CUT_20260902190500000_2.jpg"]
    assert ng["tube_count"] == 2                      # 프레임 1장 = 튜브 2행
    assert ng["width"] == 640 and ng["height"] == 480
    assert ng["file_path"].startswith("inspection/2026/09/02/")
    assert ng["source"] == "inspection" and ng["view"] == "SIDE" and ng["cam_id"] == "cam-01"
    assert "operator" not in ng
    assert any(k["reason"] == "원본 파일 없음" for k in s.skipped)   # missing.jpg
    assert s.since is None
    assert datetime.fromisoformat(s.until) == UNTIL   # 표기는 KST, 시점은 동일


def test_export_raw_incremental_window(db, tmp_path):
    images = _images_dir(tmp_path)
    _seed(db)
    out = tmp_path / "out" / "raw"
    since = datetime(2026, 9, 2, 10, 2, tzinfo=timezone.utc)
    s = export_raw(db, out, ExportOptions(images_dir=str(images), since=since, until=UNTIL, run_id="r2"))
    assert s.records == 1
    assert not (out / "inspection/2026/09/02/L1_HP12_20260902100000000_OK.jpg").exists()


def test_export_ai_analysis_records_images_kpi(db, tmp_path):
    images = _images_dir(tmp_path)
    _seed(db)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "fat_metrics.json").write_text('{"overall_passed": true}', encoding="utf-8")
    (reports / "fat_metrics.md").write_text("# FAT", encoding="utf-8")
    (reports / "notes.txt").write_text("x", encoding="utf-8")

    out = tmp_path / "out" / "ai-analysis"
    s = export_ai_analysis(db, out, ExportOptions(images_dir=str(images), reports_dir=str(reports),
                                                   until=UNTIL, run_id="r1"))
    jl = out / "inspections/2026/09/inspections_20260902_r1.jsonl"
    recs = [json.loads(line) for line in jl.read_text(encoding="utf-8").splitlines()]
    assert s.records == 4 and len(recs) == 4
    for r in recs:
        assert "operator" not in r
        assert r["schema_version"] == "1.0"
        assert r["analysis_purpose"] == "header_pipe_quality_inspection"
    ng = next(r for r in recs if r["final_verdict"] == "NG")
    assert ng["defect_codes"] == ["SCR"] and ng["scratch_score"] == pytest.approx(0.91)
    # 정의서 5-2 규격명: 판정 결과물이므로 OK/NG 를 붙인다.
    assert ng["result_image_path"] == "result/2026/09/02/L1_HP12_20260902190500000_2_NG.jpg"
    assert ng["raw_image_path"] == "inspection/2026/09/02/L1_HP12_CUT_20260902190500000_2.jpg"
    assert (out / ng["result_image_path"]).is_file()
    assert (out / "result/2026/09/02/L1_HP12_20260902190000000_1_OK.jpg").is_file()
    assert any(k["reason"] == "결과 파일 없음" for k in s.skipped)

    kpi = json.loads((out / "kpi/kpi_2026-09.json").read_text(encoding="utf-8"))
    assert kpi["total_inspected"] == 4 and kpi["defect_count"] == 1
    assert kpi["process_defect_ppm"] == pytest.approx(250000.0)
    assert kpi["miss_count"] == 1 and kpi["misjudge_count"] == 0     # r3 review_flag & manual 미입력
    assert kpi["mes_synced_count"] == 1 and kpi["storage_mes_rate_pct"] == pytest.approx(25.0)
    assert kpi["avg_proc_time_ms"] == pytest.approx(16.0)
    assert (out / "reports/fat_metrics.json").is_file() and (out / "reports/fat_metrics.md").is_file()
    assert not (out / "reports/notes.txt").exists()


def test_compute_kpi_matches_backend_formula(db):
    """backend routers/kpi._compute_summary 와 동일 산출(§1.1). api 의존 미설치 환경은 skip."""
    _seed(db)
    try:
        from routers.kpi import _compute_summary
    except Exception as exc:  # noqa: BLE001 — fastapi/jose 등 미설치(또는 환경 문제)
        pytest.skip(f"api 라우터 import 불가: {exc.__class__.__name__}")
    summary, rows = _compute_summary("2026-09", db)
    mine = compute_kpi(rows, "2026-09")
    for key in ("total_inspected", "defect_count", "process_defect_ppm", "auto_inspected",
                "auto_inspection_rate_pct", "misjudge_count", "miss_count", "inspection_defect_rate_pct",
                "stored_count", "mes_synced_count", "storage_mes_rate_pct", "avg_proc_time_ms"):
        assert mine[key] == getattr(summary, key), key


# ---------------------------------------------------------------------------
# 내보내기: 가공
# ---------------------------------------------------------------------------

def test_export_processed_labels_gt_review_master(db, tmp_dataset, tmp_path):
    root, add = tmp_dataset
    add("OK", "HP12_SIDE_OK_20260610-141000_001.jpg", {
        "item_code": "HP12", "view": "SIDE", "labels": [], "border": False,
        "length_mm_gt": 250.0, "scale_ref_mm": 100.0, "lighting": "diffuse",
        "inspector": "kim", "captured_at": "2026-06-10T14:10:00+09:00", "note": "정상",
    })
    add("SCR", "HP12_SIDE_SCR_20260610-141233_007.jpg", {
        "item_code": "HP12", "view": "SIDE", "labels": ["SCR"], "border": True,
        "inspector": "lee", "note": "선형 스크래치 1개",
    })
    add("LEN", "HP12_SIDE_LEN_20260610-141500_002.jpg")          # 사이드카 없음 → 파일명 폴백
    _seed(db)
    db.query(Inspection).filter(Inspection.final_verdict == "NG").update({"manual_verdict": "OK"})
    db.commit()

    out = tmp_path / "out" / "processed"
    s = export_processed(db, out, ExportOptions(images_dir="/nonexistent", dataset_dir=str(root),
                                                 until=UNTIL, run_id="r1"))

    scr = json.loads((out / "labels/SCR/HP12_SIDE_SCR_20260610-141233_007.json").read_text(encoding="utf-8"))
    assert scr["labels"] == ["SCR"] and scr["border"] is True
    assert scr["image_path"] == "capture/SCR/HP12_SIDE_SCR_20260610-141233_007.jpg"
    assert "inspector" not in scr and scr["note"] == "선형 스크래치 1개"
    ln = json.loads((out / "labels/LEN/HP12_SIDE_LEN_20260610-141500_002.json").read_text(encoding="utf-8"))
    assert ln["labels"] == ["LEN"] and ln["label_source"] == "filename"

    gt = json.loads((out / "groundtruth/gt_manifest.json").read_text(encoding="utf-8"))
    assert gt["count"] == 3 and gt["ok_count"] == 1 and gt["ng_count"] == 2 and gt["border_count"] == 1
    assert all("inspector" not in it for it in gt["items"])

    review = [json.loads(line) for line in (out / "review/review_labels.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = sorted((r["miss_kind"] or "flagged") for r in review)
    assert kinds == ["flagged", "system_ng_human_ok"]
    mis = next(r for r in review if r["miss_kind"] == "system_ng_human_ok")
    assert mis["raw_image_path"].startswith("inspection/2026/09/02/") and "operator" not in mis

    master = json.loads((out / "master/item_master.json").read_text(encoding="utf-8"))
    assert master["items"][0]["item_code"] == "HP12"
    assert master["items"][0]["ref_length_mm"] == pytest.approx(250.0)
    assert "updated_by" not in master["items"][0] and "capture_recipe" in master["items"][0]
    assert s.records == 3 + 2


def test_export_raw_include_capture_and_calib(db, tmp_dataset, tmp_path):
    root, add = tmp_dataset
    add("OK", "HP12_SIDE_OK_20260610-141000_001.jpg", {"item_code": "HP12", "view": "SIDE", "labels": []})
    add("SCR", "weird-name.jpg")                                    # 규칙 불일치 → 메타 일부 누락(skipped 기록)
    calib = root.parent / "calib"
    calib.mkdir()
    (calib / "gauge_100mm.jpg").write_bytes(_jpeg_bytes(1000, 200))

    out = tmp_path / "out" / "raw"
    s = export_raw(db, out, ExportOptions(images_dir=str(tmp_path / "noimg"), dataset_dir=str(root.parent),
                                          until=UNTIL, run_id="r1", include_capture=True, include_calib=True))
    recs = [json.loads(line) for line in (out / "index/raw_images_r1.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["source"] for r in recs} == {"capture", "calib"}
    cap = next(r for r in recs if r["file_name"] == "HP12_SIDE_OK_20260610-141000_001.jpg")
    assert cap["file_path"] == "capture/OK/HP12_SIDE_OK_20260610-141000_001.jpg"
    assert cap["capture_class"] == "OK" and cap["item_code"] == "HP12" and cap["captured_at"] == "2026-06-10T14:10:00+09:00"
    assert (out / "calib/gauge_100mm.jpg").is_file()
    assert any("파일명 규칙 불일치" in k["reason"] for k in s.skipped)


# ---------------------------------------------------------------------------
# 업로드 클라이언트
# ---------------------------------------------------------------------------

def _upload_tree(tmp_path: Path) -> Path:
    root = tmp_path / "ds"
    for i in range(7):
        p = root / "inspection/2026/09/02" / f"f{i}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (10 + i))
    (root / "empty.txt").write_bytes(b"")
    (root / "bad.exe").write_bytes(b"MZ")
    (root / 'we;ird.jpg').write_bytes(b"x")
    (root / ".hidden").mkdir()
    (root / ".hidden/h.jpg").write_bytes(b"x")
    return root


def test_plan_files_rules_and_batches(tmp_path):
    root = _upload_tree(tmp_path)
    files, skipped = plan_files(root)
    assert [f.rel for f in files] == [f"inspection/2026/09/02/f{i}.jpg" for i in range(7)]
    reasons = {s["path"]: s["reason"] for s in skipped}
    assert "empty.txt" in reasons and "bad.exe" in reasons and "we;ird.jpg" in reasons
    assert not any(p.startswith(".hidden") for p in reasons)
    assert [len(b) for b in make_batches(files, 3, 10**9)] == [3, 3, 1]
    assert [len(b) for b in make_batches(files, 300, 25)] == [2, 2, 1, 1, 1]  # 10+11=21, 12+13=25, 14, 15, 16


def test_uploader_batches_headers_and_result(tmp_path):
    root = _upload_tree(tmp_path)
    tr = FakePortalTransport(reject=lambda rel: "format mismatch" if rel.endswith("f6.jpg") else None)
    up = PortalUploader("https://portal.example/api/", "CODE-1", tr, batch_files=3, sleep=lambda _s: None)
    res = up.upload_dir(root)
    assert res.ok and res.batches == 3 and res.uploaded_files == 7 and res.accepted == 6
    assert res.rejected == [{"fileName": "inspection/2026/09/02/f6.jpg", "reason": "format mismatch"}]
    assert len(res.skipped) == 3 and res.warning and "제외 4건" in res.warning
    assert res.versions == ["1.0", "1.0", "1.0"]
    assert all(c["url"] == "https://portal.example/api/dataset-uploads" for c in tr.calls)
    runs = {c["headers"]["X-Upload-Run"] for c in tr.calls}
    assert len(runs) == 1 and runs.pop() == res.run_id
    assert all(c["headers"]["X-Dataset-Code"] == "CODE-1" for c in tr.calls)
    assert tr.calls[0]["files"] == [f"inspection/2026/09/02/f{i}.jpg" for i in range(3)]


def test_uploader_retry_on_5xx_and_transport_error_but_not_on_401(tmp_path):
    root = _upload_tree(tmp_path)
    slept: list[float] = []
    tr = FakePortalTransport(fail_statuses=[503, 500], raise_times=1)
    up = PortalUploader("https://p/api", "C", tr, retry=3, retry_delay_s=7, sleep=slept.append)
    res = up.upload_dir(root)
    assert res.ok and len(tr.calls) == 4 and slept == [7, 7, 7]   # 예외 1 + 503 + 500 → 4번째 성공

    tr2 = FakePortalTransport(status=401)
    res2 = PortalUploader("https://p/api", "C", tr2, retry=3, sleep=lambda _s: None).upload_dir(root)
    assert not res2.ok and len(tr2.calls) == 1 and "HTTP 401" in res2.error

    tr3 = FakePortalTransport(status=503)
    res3 = PortalUploader("https://p/api", "C", tr3, retry=2, sleep=lambda _s: None).upload_dir(root)
    assert not res3.ok and len(tr3.calls) == 3 and "HTTP 503" in res3.error

    res4 = PortalUploader("https://p/api", "C", FakePortalTransport()).upload_dir(tmp_path / "nothing")
    assert not res4.ok and res4.error


def test_load_conf_and_credentials(tmp_path, monkeypatch):
    conf = tmp_path / "jntp-raw.conf"
    conf.write_text('# 코드\nJNTP_UPLOAD_CODE="ABCDEF"\nexport JNTP_API_BASE=https://jntp-data.example/api/\n\n',
                    encoding="utf-8")
    assert load_conf(conf) == {"JNTP_UPLOAD_CODE": "ABCDEF", "JNTP_API_BASE": "https://jntp-data.example/api/"}
    monkeypatch.delenv("JNTP_UPLOAD_CODE", raising=False)
    monkeypatch.delenv("JNTP_API_BASE", raising=False)
    assert credentials_from_conf(conf) == ("https://jntp-data.example/api", "ABCDEF")
    monkeypatch.setenv("JNTP_UPLOAD_CODE", "ENV-CODE")
    assert credentials_from_conf(conf)[1] == "ENV-CODE"
    (tmp_path / "bad.conf").write_text("X=1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        credentials_from_conf(tmp_path / "bad.conf")


# ---------------------------------------------------------------------------
# CLI: run (워터마크 · 대기분 재전송 · 드라이런 업로드)
# ---------------------------------------------------------------------------

def _run(argv: list[str]) -> int:
    return portal_cli.main(argv)


def test_cli_run_no_upload_advances_watermark(db, tmp_path, capsys):
    images = _images_dir(tmp_path)
    _seed(db)
    out = tmp_path / "portal"
    rc = _run(["run", "--out", str(out), "--no-upload", "--images-dir", str(images),
               "--until", "2026-09-03T00:00:00+00:00", "--run-id", "run1"])
    assert rc == 0
    capsys.readouterr()
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert set(state["last_until"]) == {"raw", "processed", "ai-analysis"}
    assert state["last_until"]["raw"] == "2026-09-03T00:00:00+00:00"
    idx = out / "runs/run1/raw/index/raw_images_run1.jsonl"
    assert len(idx.read_text(encoding="utf-8").splitlines()) == 2

    # 새 검사 1건 추가 → 두 번째 회차는 새 행만 내보낸다.
    _add(db, datetime(2026, 9, 3, 5, 0), raw_image_path="raw/L1_HP12_20260902100000000_OK.jpg")
    rc = _run(["run", "--out", str(out), "--no-upload", "--images-dir", str(images),
               "--until", "2026-09-04T00:00:00+00:00", "--run-id", "run2"])
    assert rc == 0
    capsys.readouterr()
    idx2 = out / "runs/run2/raw/index/raw_images_run2.jsonl"
    recs = [json.loads(line) for line in idx2.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1 and recs[0]["captured_at"].startswith("2026-09-03T14:00")
    ai2 = list((out / "runs/run2/ai-analysis/inspections/2026/09").glob("*.jsonl"))
    assert len(ai2) == 1 and ai2[0].name == "inspections_20260903_run2.jsonl"

    rc = _run(["status", "--out", str(out)])
    assert rc == 0
    status = json.loads(capsys.readouterr().out)
    assert [(p["run_id"], p["dataset"]) for p in status["pending"]] == [
        ("run1", "raw"), ("run1", "processed"), ("run1", "ai-analysis"),
        ("run2", "raw"), ("run2", "processed"), ("run2", "ai-analysis"),
    ]
    assert status["last_until"]["ai-analysis"] == "2026-09-04T00:00:00+00:00"


def test_cli_run_dry_run_upload_cleans_and_retries_pending(db, tmp_path, monkeypatch, capsys):
    images = _images_dir(tmp_path)
    _seed(db)
    out = tmp_path / "portal"
    confs = {}
    for ds, name in (("raw", "jntp-raw.conf"), ("processed", "jntp-processed.conf"), ("ai", "jntp-ai-model.conf")):
        p = tmp_path / name
        p.write_text(f"JNTP_UPLOAD_CODE=CODE-{ds}\nJNTP_API_BASE=https://jntp-data.example/api\n", encoding="utf-8")
        confs[ds] = str(p)
    monkeypatch.delenv("JNTP_UPLOAD_CODE", raising=False)
    monkeypatch.delenv("JNTP_API_BASE", raising=False)

    # 이전 회차 대기분(실패분 가정) — 재전송 후 정리돼야 한다.
    old = out / "runs/oldrun/raw/inspection/2026/09/01"
    old.mkdir(parents=True)
    (old / "x.jpg").write_bytes(_jpeg_bytes())

    rc = _run(["run", "--out", str(out), "--images-dir", str(images), "--dry-run",
               "--conf-raw", confs["raw"], "--conf-processed", confs["processed"],
               "--conf-ai-model", confs["ai"], "--until", "2026-09-03T00:00:00+00:00", "--run-id", "run1"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pending"][0]["run_id"] == "oldrun" and report["pending"][0]["upload"]["ok"] is True
    assert not (out / "runs/oldrun").exists()          # 대기분 전송 성공 → 정리
    assert not (out / "runs/run1").exists()            # 이번 회차 전송 성공 → 정리
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["runs"][-1]["ok"] is True
    assert state["runs"][-1]["datasets"]["raw"]["uploaded"] == 3   # 이미지 2 + 인덱스 1
    assert state["runs"][-1]["datasets"]["ai-analysis"]["accepted"] >= 3

    # 환경변수로 설정 파일을 주는 경로 + --keep 보존
    monkeypatch.setenv("JNTP_CONF_RAW", confs["raw"])
    rc = _run(["run", "--out", str(out), "--dataset", "raw", "--images-dir", str(images), "--dry-run", "--keep",
               "--since", "2026-09-02T10:02:00+00:00", "--until", "2026-09-03T00:00:00+00:00", "--run-id", "run2"])
    assert rc == 0 and (out / "runs/run2/raw").is_dir()
    capsys.readouterr()

    # 설정 파일 없으면 실행하지 않는다(코드 유출/오전송 방지).
    monkeypatch.delenv("JNTP_CONF_RAW", raising=False)
    assert _run(["run", "--out", str(out), "--dataset", "raw", "--images-dir", str(images), "--dry-run"]) == 2


def test_cli_export_and_upload_commands(db, tmp_path, monkeypatch, capsys):
    images = _images_dir(tmp_path)
    _seed(db)
    out = tmp_path / "portal"
    rc = _run(["export", "--out", str(out), "--dataset", "ai-analysis", "--images-dir", str(images),
               "--until", "2026-09-03T00:00:00+00:00", "--run-id", "e1"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "e1" and payload["datasets"][0]["dataset"] == "ai-analysis"
    assert payload["datasets"][0]["records"] == 4

    conf = tmp_path / "jntp-ai-model.conf"
    conf.write_text("JNTP_UPLOAD_CODE=C\nJNTP_API_BASE=https://jntp-data.example/api\n", encoding="utf-8")
    monkeypatch.delenv("JNTP_UPLOAD_CODE", raising=False)
    monkeypatch.delenv("JNTP_API_BASE", raising=False)
    rc = _run(["upload", "--dataset", "ai-analysis", "--dir", str(out / "runs/e1/ai-analysis"),
               "--conf", str(conf), "--dry-run"])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["uploaded_files"] == res["accepted"] >= 4

    rc = _run(["schema"])
    assert rc == 0 and "ai-analysis" in json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# 데이터 정의서 규격 고정 (3-2 / 5-2 파일명, KST 표기)
#
# 아래 값들은 전남TP 에 제출한 정의서에 박혀 있는 규격이다. 코드가 바뀌어
# 제출본과 어긋나면 데이터셋 전체를 다시 만들어야 하므로 여기서 고정한다.
# ---------------------------------------------------------------------------

def test_dataset_raw_name_has_no_verdict():
    """원본 파일명에 판정을 넣지 않는다 (정의서 3-2).

    학습 입력이 될 원본 이름에 정답이 박혀 있으면, 파일명으로 정렬하거나 분할하는
    순간 라벨이 새어 들어가 정확도가 부풀려진다.
    """
    ts = datetime(2026, 9, 2, 1, 5, 0, 123000, tzinfo=timezone.utc)  # KST 10:05:00.123
    name = dataset_raw_name("LOT20260902", "HP12", "CUT_LENGTH", ts, 12345)

    assert name == "LOT20260902_HP12_CUT_20260902100500123_12345.jpg"
    assert "_OK" not in name and "_NG" not in name


def test_dataset_result_name_keeps_verdict():
    """판정 오버레이에는 OK/NG 를 붙인다 (정의서 5-2)."""
    ts = datetime(2026, 9, 2, 1, 5, 0, 123000, tzinfo=timezone.utc)
    assert (
        dataset_result_name("LOT20260902", "HP12", ts, 12345, "NG")
        == "LOT20260902_HP12_20260902100500123_12345_NG.jpg"
    )
    # 알 수 없는 판정은 NG 로 안전화(애매 = 불량).
    assert dataset_result_name("L", "I", ts, 1, None).endswith("_NG.jpg")


def test_dataset_names_use_kst_not_utc():
    """파일명 시각은 현장 기준시(KST). UTC 로 찍으면 9시간 어긋난다."""
    ts = datetime(2026, 9, 2, 15, 30, 0, tzinfo=timezone.utc)   # KST 익일 00:30
    name = dataset_raw_name("L1", "HP12", "CUT_LENGTH", ts, 7)
    assert name.startswith("L1_HP12_CUT_20260903003000")


def test_stage_token_mapping():
    """운영 파일명은 축약 토큰을 쓴다 — 전체 이름의 밑줄이 필드 경계를 무너뜨린다."""
    assert stage_token("CUT_LENGTH") == "CUT"
    assert stage_token("POST_WASH_SURFACE") == "WASH"
    assert stage_token(None) == "NA"       # 단계 구분이 없던 시절 수집분


def test_partition_uses_kst_date(db, tmp_path):
    """KST 자정 직후 검사분이 UTC 기준 전날 폴더로 새지 않는다."""
    images = _images_dir(tmp_path)
    # 2026-09-02 16:00 UTC = 2026-09-03 01:00 KST → 9/3 폴더여야 한다.
    _add(db, datetime(2026, 9, 2, 16, 0),
         raw_image_path="raw/L1_HP12_20260902100000000_OK.jpg",
         inspection_stage="POST_WASH_SURFACE")
    out = tmp_path / "out" / "raw"
    export_raw(db, out, ExportOptions(images_dir=str(images), run_id="r9"))

    assert list((out / "inspection/2026/09/03").glob("*.jpg"))
    assert not (out / "inspection/2026/09/02").exists()


def test_inspection_stage_flows_into_every_record(db, tmp_path):
    """검사 단계가 원시 인덱스와 AI분석 레코드 양쪽에 실린다 (정의서 3-3 / 5-3)."""
    images = _images_dir(tmp_path)
    _seed(db)
    raw_out = tmp_path / "out" / "raw"
    ai_out = tmp_path / "out" / "ai"
    export_raw(db, raw_out, ExportOptions(images_dir=str(images), until=UNTIL, run_id="r1"))
    export_ai_analysis(db, ai_out, ExportOptions(images_dir=str(images), until=UNTIL, run_id="r1"))

    idx = [
        json.loads(x)
        for x in (raw_out / "index/raw_images_r1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {r["inspection_stage"] for r in idx} == {"CUT_LENGTH"}

    ai_files = list((ai_out / "inspections").rglob("*.jsonl"))
    recs = [json.loads(x) for f in ai_files for x in f.read_text(encoding="utf-8").splitlines()]
    assert recs
    staged = {r["inspection_id"]: r.get("inspection_stage") for r in recs}
    assert staged[1] == staged[2] == staged[3] == "CUT_LENGTH"
    # 단계 구분이 없던 시절의 행(r4)은 비워 둔다. 어느 한쪽으로 단정하면
    # 단계별 정확도·분포가 조용히 오염된다.
    assert staged.get(4) is None
