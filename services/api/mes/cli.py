"""MES 연계 운영 CLI (CLAUDE.md §7.3).

사용 예(서비스 디렉터리 services/api 에서):
  python -m mes.cli status                  # 연계 상태(총/완료/미전송/연계율) 출력
  python -m mes.cli watchdog --once         # 미전송 1배치 재연계
  python -m mes.cli watchdog --cycles 5     # 5주기 실행 후 종료
  python -m mes.cli watchdog                # 무한 주기(중단: Ctrl-C)

MES_MODE / MES_REST_URL / MES_WATCHDOG_* 환경변수로 동작 전환.
"""
from __future__ import annotations

import argparse
import json
import sys

from db.base import SessionLocal, init_db
from mes.config import get_mes_config
from mes.watchdog import (
    get_linkage_status,
    run_watchdog_forever,
    run_watchdog_once,
)


def _cmd_status() -> int:
    init_db()
    db = SessionLocal()
    try:
        status = get_linkage_status(db)
    finally:
        db.close()
    print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_watchdog(args: argparse.Namespace) -> int:
    init_db()
    cfg = get_mes_config()
    if args.once:
        db = SessionLocal()
        try:
            result = run_watchdog_once(db, cfg=cfg)
        finally:
            db.close()
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    run_watchdog_forever(cfg=cfg, max_cycles=args.cycles)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mes.cli", description="AIVIS MES 연계 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="연계 상태 조회")

    wd = sub.add_parser("watchdog", help="미전송 재연계 워치독")
    wd.add_argument("--once", action="store_true", help="1배치만 실행")
    wd.add_argument("--cycles", type=int, default=None, help="N주기 실행 후 종료")

    args = parser.parse_args(argv)
    if args.command == "status":
        return _cmd_status()
    if args.command == "watchdog":
        return _cmd_watchdog(args)
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
