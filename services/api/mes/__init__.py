"""MES 연계 어댑터 패키지 (CLAUDE.md §5 M9, §7.3).

services/api/mes/* — data-mes 에이전트 소유.

구성:
- config.py    : MES_MODE(table|rest) + 워치독/재시도 설정 (환경변수).
- transport.py : REST 전송 추상화(HttpxMesTransport / FakeMesTransport) + 멱등.
- adapter.py   : table/rest 모드 어댑터. 단일 inspection 행 연계.
- watchdog.py  : mes_synced=false 미전송 재시도(연계율 100% 보장), 상태 조회.

backend 소유(변경 금지) 자산 재사용:
- db.base.SessionLocal / db.models (Inspection, MesQualityIf, SysLog)
- core.inspection_service.make_idem_key (멱등키 = lot|item_code|inspected_at|cam_id)
- core.logging.write_log (sys_log category=mes)
"""
from __future__ import annotations

from mes.adapter import MesAdapter, build_adapter
from mes.config import MesConfig, get_mes_config
from mes.transport import (
    FakeMesTransport,
    HttpxMesTransport,
    MesTransport,
    MesTransportError,
)
from mes.watchdog import (
    LinkageStatus,
    get_linkage_status,
    run_watchdog_forever,
    run_watchdog_once,
)

__all__ = [
    "MesAdapter",
    "build_adapter",
    "MesConfig",
    "get_mes_config",
    "MesTransport",
    "MesTransportError",
    "HttpxMesTransport",
    "FakeMesTransport",
    "LinkageStatus",
    "get_linkage_status",
    "run_watchdog_once",
    "run_watchdog_forever",
]
