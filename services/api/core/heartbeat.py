"""검사워커 라이브니스 하트비트 저장소 (CLAUDE.md §5 M6,M15 — 현장 모니터링).

워커는 매 검사 사이클(기본 1.5s, AIVIS_WORKER_INTERVAL_MS)마다
`POST /inspection/status` 로 취득/검출 상태를 보낸다. 이 모듈은 그 하트비트의
**마지막 수신 시각**만 프로세스 메모리에 기록해 `GET /system/status` 가
"워커가 지금 살아있는가"를 판정할 수 있게 한다.

설계 메모:
- DB 에 남기지 않는다. 하트비트는 1.5s 마다 오는 고빈도 신호라 sys_log 에
  적재하면 로그 테이블이 순식간에 오염된다(하트비트는 검사결과가 아니다).
- 단일 프로세스(단일 호스트 §4) 가정 — 락 없이 단순 대입만 한다.
  (GIL 하에서 참조 대입은 원자적이라 스레드 안전 장치가 필요 없다.)
- 프로세스 재시작 시 값이 사라진다 = "기동 후 하트비트 없음"(worker=down)
  으로 보이는 것이 의도된 동작이다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_last_seen: Optional[datetime] = None
_last_cam_id: Optional[str] = None


def record(cam_id: str, ts: Optional[datetime] = None) -> None:
    """하트비트 수신을 기록한다.

    ts 미지정 시 현재 UTC 시각. ts 를 명시할 수 있게 둔 이유는 테스트에서
    "N초 전 하트비트" 상태를 결정적으로 재현하기 위해서다(모듈 전역을 직접
    건드리지 않게 한다).
    """
    global _last_seen, _last_cam_id
    when = ts or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    _last_seen = when
    _last_cam_id = cam_id


def last_seen() -> Optional[datetime]:
    """마지막 하트비트 시각(UTC, tz-aware). 기동 후 수신 없으면 None."""
    return _last_seen


def last_cam_id() -> Optional[str]:
    """마지막 하트비트를 보낸 카메라 ID. 수신 없으면 None."""
    return _last_cam_id


def reset() -> None:
    """기록 초기화(테스트/재기동 시뮬레이션 전용)."""
    global _last_seen, _last_cam_id
    _last_seen = None
    _last_cam_id = None
