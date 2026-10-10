"""파이 단독(독립형) sqlite DB 온라인 백업 — 2026-10-10 점검 보완.

점검: "파이 단독 SQLite 용 백업 없음". scripts/backup.sh 는 Docker Postgres 전용이다.
독립형은 `$AIVIS_HOME/db/aivis.db` 한 파일에 검사 이력·기준정보·감사 로그가 다 있다.
SD 카드가 죽으면 전부 잃는다.

- **온라인 백업**: sqlite3 백업 API — 검사가 돌고 있어도 일관된 사본(파일 복사는
  쓰는 중이면 깨질 수 있다).
- gzip 압축, 이름 `aivis_YYYYmmdd_HHMMSS.db.gz`(KST), 최근 N개만 보관(기본 14).
- 복원 확인: 사본을 다시 열어 `PRAGMA integrity_check` 가 ok 여야 성공.
- 표준 라이브러리만(파이에 추가 설치 없음).

사용:
  python3 services/api/tools/backup_sqlite.py --db /var/lib/aivis/db/aivis.db \\
      --out /var/lib/aivis/backups --keep 14
  (bash scripts/aivis.sh backup 이 같은 일을 한다)

복원: 서비스 중지 → gunzip -c aivis_….db.gz > /var/lib/aivis/db/aivis.db → 시작.
SD 카드 고장에 대비하려면 --out 을 **USB 메모리나 NAS** 로 둔다.
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

KST = timezone(timedelta(hours=9))
PREFIX = "aivis_"
SUFFIX = ".db.gz"


def backup(db: Path, out_dir: Path, *, keep: int = 14, now: Optional[datetime] = None) -> Path:
    """db 를 out_dir 에 압축 백업하고 경로를 돌려준다. 실패하면 예외."""
    db = Path(db)
    if not db.is_file():
        raise FileNotFoundError(f"DB 파일이 없습니다: {db}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(KST)).astimezone(KST).strftime("%Y%m%d_%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}{SUFFIX}"

    with tempfile.TemporaryDirectory(dir=out_dir) as td:
        raw = Path(td) / "snapshot.db"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(raw)
            try:
                src.backup(dst)  # 쓰는 중이어도 일관된 사본
            finally:
                dst.close()
        finally:
            src.close()
        chk = sqlite3.connect(raw)
        try:
            res = chk.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            chk.close()
        if res != "ok":
            raise RuntimeError(f"백업 사본 무결성 검사 실패: {res}")
        part = target.with_suffix(target.suffix + ".part")
        with open(raw, "rb") as fi, gzip.open(part, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        part.replace(target)  # 반쯤 쓴 파일이 '최신 백업' 으로 보이지 않게
    prune(out_dir, keep=keep)
    return target


def list_backups(out_dir: Path) -> List[Path]:
    return sorted(Path(out_dir).glob(f"{PREFIX}*{SUFFIX}"))


def prune(out_dir: Path, *, keep: int) -> List[Path]:
    """오래된 것부터 지워 keep 개만 남긴다. 지운 목록 반환."""
    items = list_backups(out_dir)
    if keep <= 0 or len(items) <= keep:
        return []
    gone = items[: len(items) - keep]
    for p in gone:
        p.unlink(missing_ok=True)
    return gone


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AIVIS 독립형 sqlite DB 온라인 백업")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", type=int, default=14)
    a = ap.parse_args(argv)
    try:
        p = backup(Path(a.db), Path(a.out), keep=a.keep)
    except Exception as exc:  # noqa: BLE001
        print(f"[backup] 실패: {exc}", file=sys.stderr)
        return 1
    size_mb = p.stat().st_size / 1024 / 1024
    print(f"[backup] 완료: {p} ({size_mb:.1f} MB), 보관 {len(list_backups(p.parent))}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
