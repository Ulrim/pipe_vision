"""데이터포털 제출 CLI (내보내기 + 업로드 + 회차/워터마크 관리).

사용 예(services/data-ops 에서, PYTHONPATH=../api):
  # 명세 확인(정의서 대조)
  python -m portal.cli schema

  # 내보내기만(A안: 이후 전남TP upload.sh 로 폴더 전송)
  python -m portal.cli export --dataset all --out /data/portal_export

  # 회차 실행: 대기분 재전송 → 증분 내보내기 → 업로드(B안 API 직접) → 성공분 정리
  python -m portal.cli run --out /data/portal_export \
      --conf-raw ~/jntp/jntp-raw.conf --conf-processed ~/jntp/jntp-processed.conf \
      --conf-ai-model ~/jntp/jntp-ai-model.conf

  # 특정 폴더 업로드(수동)
  python -m portal.cli upload --dataset raw --dir /data/portal_export/runs/<run>/raw \
      --conf ~/jntp/jntp-raw.conf

환경변수: AIVIS_PORTAL_EXPORT_DIR, AIVIS_IMAGES_DIR, AIVIS_DATASET_DIR, AIVIS_REPORTS_DIR,
JNTP_CONF_RAW / JNTP_CONF_PROCESSED / JNTP_CONF_AI_MODEL, JNTP_API_BASE, JNTP_UPLOAD_CODE.
DB 는 backend 의 DATABASE_URL 을 그대로 사용한다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from portal.export import (
    ExportOptions,
    export_dataset,
    make_run_id,
    utc_now,
)
from portal.layout import (
    CONF_NAME_BY_DATASET,
    DATASET_AI,
    DATASET_PROCESSED,
    DATASET_RAW,
    DATASETS,
    describe_schema,
)
from portal.upload import (
    DEFAULT_BATCH_FILES,
    DEFAULT_RETRY,
    DEFAULT_RETRY_DELAY_S,
    FakePortalTransport,
    PortalUploader,
    credentials_from_conf,
)

RUNS_DIR = "runs"
STATE_NAME = "state.json"
_CONF_ENV = {
    DATASET_RAW: "JNTP_CONF_RAW",
    DATASET_PROCESSED: "JNTP_CONF_PROCESSED",
    DATASET_AI: "JNTP_CONF_AI_MODEL",
}


# ---------------------------------------------------------------------------
# 상태(워터마크) 파일
# ---------------------------------------------------------------------------

def load_state(out_root: Path) -> dict[str, Any]:
    p = out_root / STATE_NAME
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"schema_version": "1.0", "last_until": {}, "runs": []}


def save_state(out_root: Path, state: dict[str, Any]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    state["runs"] = state.get("runs", [])[-50:]
    tmp = out_root / (STATE_NAME + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out_root / STATE_NAME)


def list_pending(out_root: Path) -> list[tuple[str, str, Path]]:
    """전송 대기 중인 (run_id, dataset, dir) 목록(오래된 회차부터)."""
    runs = out_root / RUNS_DIR
    if not runs.is_dir():
        return []
    out = []
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        for ds in DATASETS:
            d = run_dir / ds
            if d.is_dir():
                out.append((run_dir.name, ds, d))
    return out


# ---------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------

def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _datasets(arg: str) -> list[str]:
    if arg == "all":
        return list(DATASETS)
    if arg not in DATASETS:
        raise SystemExit(f"--dataset 은 all 또는 {', '.join(DATASETS)} 중 하나")
    return [arg]


def _options(args: argparse.Namespace, since: datetime | None, until: datetime, run_id: str) -> ExportOptions:
    return ExportOptions(
        images_dir=args.images_dir or os.getenv("AIVIS_IMAGES_DIR", "/data/images"),
        dataset_dir=args.dataset_dir or os.getenv("AIVIS_DATASET_DIR") or None,
        reports_dir=args.reports_dir or os.getenv("AIVIS_REPORTS_DIR") or None,
        since=since, until=until,
        include_capture=bool(args.include_capture),
        include_calib=bool(args.include_calib),
        view=args.view,
        run_id=run_id,
    )


def _db_session():
    from db.base import SessionLocal, init_db

    init_db()
    return SessionLocal()


def _uploader(conf: str, args: argparse.Namespace) -> PortalUploader:
    api_base, code = credentials_from_conf(conf)
    if getattr(args, "api_base", None):
        api_base = args.api_base
    transport = FakePortalTransport() if getattr(args, "dry_run", False) else None
    return PortalUploader(
        api_base, code, transport,
        batch_files=args.batch_size, retry=args.retry, retry_delay_s=args.retry_delay,
    )


def _conf_for(ds: str, args: argparse.Namespace) -> str | None:
    explicit = {
        DATASET_RAW: getattr(args, "conf_raw", None),
        DATASET_PROCESSED: getattr(args, "conf_processed", None),
        DATASET_AI: getattr(args, "conf_ai_model", None),
    }[ds]
    return explicit or os.getenv(_CONF_ENV[ds]) or None


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 서브커맨드
# ---------------------------------------------------------------------------

def _cmd_schema(_args: argparse.Namespace) -> int:
    _print(describe_schema())
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    out_root = Path(args.out)
    until = _parse_dt(args.until) or utc_now()
    run_id = args.run_id or make_run_id(until)
    db = _db_session()
    try:
        summaries = []
        for ds in _datasets(args.dataset):
            target = out_root / RUNS_DIR / run_id / ds
            s = export_dataset(ds, db, target, _options(args, _parse_dt(args.since), until, run_id))
            summaries.append(s.as_dict())
    finally:
        db.close()
    _print({"run_id": run_id, "out": str(out_root / RUNS_DIR / run_id), "datasets": summaries})
    return 0


def _cmd_upload(args: argparse.Namespace) -> int:
    conf = args.conf or os.getenv(_CONF_ENV[args.dataset])
    if not conf:
        print(f"설정 파일이 필요합니다(--conf 또는 {_CONF_ENV[args.dataset]}, 예 {CONF_NAME_BY_DATASET[args.dataset]})", file=sys.stderr)
        return 2
    res = _uploader(conf, args).upload_dir(args.dir)
    _print(res.as_dict())
    return 0 if res.ok else 1


def _cmd_run(args: argparse.Namespace) -> int:
    out_root = Path(args.out)
    state = load_state(out_root)
    datasets = _datasets(args.dataset)

    uploaders: dict[str, PortalUploader] = {}
    if not args.no_upload:
        for ds in datasets:
            conf = _conf_for(ds, args)
            if not conf:
                print(f"{ds}: 설정 파일 미지정(--conf-* 또는 {_CONF_ENV[ds]})", file=sys.stderr)
                return 2
            uploaders[ds] = _uploader(conf, args)

    report: dict[str, Any] = {"started_at": utc_now().isoformat(), "pending": [], "datasets": {}}
    all_ok = True

    # 1) 이전 회차 대기분(실패/미전송) 재전송 — 같은 경로 재전송은 갱신이라 안전
    if uploaders:
        for run_id, ds, d in list_pending(out_root):
            if ds not in uploaders:
                continue
            res = uploaders[ds].upload_dir(d)
            report["pending"].append({"run_id": run_id, "dataset": ds, "upload": res.as_dict()})
            if res.ok:
                if not args.keep:
                    shutil.rmtree(d, ignore_errors=True)
            else:
                all_ok = False
            _cleanup_run_dir(d.parent)

    # 2) 증분 내보내기 (since = 직전 워터마크) → 업로드 → 정리
    until = _parse_dt(args.until) or utc_now()
    run_id = args.run_id or make_run_id(until)
    db = _db_session()
    try:
        for ds in datasets:
            since = _parse_dt(args.since) if args.since else _parse_dt(state["last_until"].get(ds))
            target = out_root / RUNS_DIR / run_id / ds
            s = export_dataset(ds, db, target, _options(args, since, until, run_id))
            entry: dict[str, Any] = {"export": s.as_dict()}
            state["last_until"][ds] = until.isoformat()   # 내보낸 시점까지 워터마크 전진(파일은 runs/ 에 보존)
            if ds in uploaders:
                res = uploaders[ds].upload_dir(target)
                entry["upload"] = res.as_dict()
                if res.ok and not args.keep:
                    shutil.rmtree(target, ignore_errors=True)
                elif not res.ok:
                    all_ok = False
            report["datasets"][ds] = entry
    finally:
        db.close()
    _cleanup_run_dir(out_root / RUNS_DIR / run_id)

    report["run_id"] = run_id
    report["ok"] = all_ok
    state["runs"].append({
        "run_id": run_id, "started_at": report["started_at"], "ok": all_ok,
        "datasets": {ds: {
            "files": e["export"]["files"], "records": e["export"]["records"],
            "uploaded": e.get("upload", {}).get("uploaded_files"),
            "accepted": e.get("upload", {}).get("accepted"),
        } for ds, e in report["datasets"].items()},
    })
    save_state(out_root, state)
    _print(report)
    return 0 if all_ok else 1


def _cleanup_run_dir(run_dir: Path) -> None:
    """데이터셋 폴더가 모두 정리된 회차 폴더 삭제."""
    if run_dir.is_dir() and not any(run_dir.iterdir()):
        run_dir.rmdir()


def _cmd_status(args: argparse.Namespace) -> int:
    out_root = Path(args.out)
    state = load_state(out_root)
    pending = [{"run_id": r, "dataset": ds, "dir": str(d)} for r, ds, d in list_pending(out_root)]
    _print({"out": str(out_root), "last_until": state.get("last_until", {}),
            "pending": pending, "recent_runs": state.get("runs", [])[-10:]})
    return 0


# ---------------------------------------------------------------------------
# 파서
# ---------------------------------------------------------------------------

def _add_export_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=os.getenv("AIVIS_PORTAL_EXPORT_DIR", "/data/portal_export"),
                   help="내보내기 루트(runs/<run_id>/<dataset>/ 생성)")
    p.add_argument("--dataset", default="all", help="all | raw | processed | ai-analysis")
    p.add_argument("--since", default=None, help="검사시각 하한(ISO, 초과). run 은 기본 직전 워터마크")
    p.add_argument("--until", default=None, help="검사시각 상한(ISO, 이하). 기본 지금")
    p.add_argument("--images-dir", default=None, help="AIVIS_IMAGES_DIR")
    p.add_argument("--dataset-dir", default=None, help="AIVIS_DATASET_DIR(부록 A.4 학습 촬영본)")
    p.add_argument("--reports-dir", default=None, help="FAT/SAT/MSA 리포트 폴더")
    p.add_argument("--include-capture", action="store_true", help="학습 촬영 원본(대용량) 포함")
    p.add_argument("--include-calib", action="store_true", help="캘리브레이션 촬영 포함")
    p.add_argument("--view", default="SIDE", help="운영 검사 촬영 구도(END|SIDE)")
    p.add_argument("--run-id", default=None, help="회차 ID 고정(기본 UTC 시각)")


def _add_upload_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--api-base", default=None, help="포털 API 주소(기본 설정 파일/JNTP_API_BASE)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_FILES, help="요청당 파일 수(기본 300)")
    p.add_argument("--retry", type=int, default=DEFAULT_RETRY, help="재시도 횟수(기본 3)")
    p.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY_S, help="재시도 간격 초(기본 30)")
    p.add_argument("--dry-run", action="store_true", help="실제 전송 없이 배치 계획만(가짜 전송)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="portal.cli", description="AIVIS → 전남 AX 데이터포털 제출")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="데이터셋 폴더/레코드 명세 출력")

    e = sub.add_parser("export", help="포털 규격으로 내보내기(runs/<run_id>/<dataset>)")
    _add_export_args(e)

    u = sub.add_parser("upload", help="폴더 1개를 포털에 업로드(B안 API 직접)")
    u.add_argument("--dataset", required=True, choices=list(DATASETS))
    u.add_argument("--dir", required=True, help="업로드할 폴더(데이터셋 루트)")
    u.add_argument("--conf", default=None, help="전남TP 설정 파일(jntp-*.conf)")
    _add_upload_args(u)

    r = sub.add_parser("run", help="대기분 재전송 → 증분 내보내기 → 업로드 → 정리(정기 실행용)")
    _add_export_args(r)
    r.add_argument("--conf-raw", default=None, help="원시 데이터 설정 파일(jntp-raw.conf)")
    r.add_argument("--conf-processed", default=None, help="가공 데이터 설정 파일(jntp-processed.conf)")
    r.add_argument("--conf-ai-model", default=None, help="AI 모델(분석) 설정 파일(jntp-ai-model.conf)")
    r.add_argument("--no-upload", action="store_true", help="내보내기만(A안: upload.sh 로 전송)")
    r.add_argument("--keep", action="store_true", help="업로드 성공 후에도 회차 폴더 보존")
    _add_upload_args(r)

    s = sub.add_parser("status", help="워터마크/대기 회차/최근 실행 현황")
    s.add_argument("--out", default=os.getenv("AIVIS_PORTAL_EXPORT_DIR", "/data/portal_export"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "schema": _cmd_schema, "export": _cmd_export, "upload": _cmd_upload,
        "run": _cmd_run, "status": _cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
