"""검사워커 라이브니스 하트비트 저장소 (CLAUDE.md §5 M6,M15 — 현장 모니터링).

워커는 매 검사 사이클(기본 1.5s, AIVIS_WORKER_INTERVAL_MS)마다
`POST /inspection/status` 로 취득/검출 상태를 보낸다. 이 모듈은 그 하트비트의
**마지막 수신 시각**을 프로세스 메모리에 기록해 `GET /system/status` 가
"워커가 지금 살아있는가"를 판정할 수 있게 한다.

**카메라(스테이션)별로 기록한다(2026-10-08).** 파이+카메라가 2대 이상이면 한
슬롯으로는 마지막에 말한 워커만 보이고 다른 쪽이 죽어도 모른다. 그래서
cam_id → (시각, 모드) 사전으로 둔다. `last_seen()`/`last_cam_id()` 는 종전
호출자를 위해 **가장 최근 하트비트** 를 그대로 돌려준다.

설계 메모:
- DB 에 남기지 않는다. 하트비트는 1.5s 마다 오는 고빈도 신호라 sys_log 에
  적재하면 로그 테이블이 순식간에 오염된다(하트비트는 검사결과가 아니다).
- 단일 프로세스 가정 — 락 없이 사전 대입만 한다(GIL 하에서 원자적).
- 프로세스 재시작 시 값이 사라진다 = "기동 후 하트비트 없음"(worker=down)
  으로 보이는 것이 의도된 동작이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Beat:
    cam_id: str
    seen: datetime
    stage: Optional[str] = None
    #: 마지막 사이클 요약(item_code/expected/detected/ng/mismatch/proc_time_ms/error).
    #: 파이 여러 대를 한 화면에서 볼 때(2026-10-09) "지금 그 라인이 무엇을 몇 개
    #: 보고 있나" 를 DB 를 거치지 않고 바로 보여주려고 함께 둔다.
    cycle: Dict[str, Any] = field(default_factory=dict)
    #: 그 파이 자신의 상태(온도·CPU·메모리·디스크·전원). 워커가 안 보내면 None.
    host: Optional[Dict[str, Any]] = None


_beats: Dict[str, Beat] = {}


def record(
    cam_id: str,
    ts: Optional[datetime] = None,
    *,
    stage: Optional[str] = None,
    cycle: Optional[Dict[str, Any]] = None,
    host: Optional[Dict[str, Any]] = None,
) -> None:
    """하트비트 수신을 기록한다.

    ts 미지정 시 현재 UTC 시각. ts 를 명시할 수 있게 둔 이유는 테스트에서
    "N초 전 하트비트" 상태를 결정적으로 재현하기 위해서다.
    """
    when = ts or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    _beats[cam_id] = Beat(cam_id=cam_id, seen=when, stage=stage,
                          cycle=dict(cycle or {}), host=dict(host) if host else None)


def get(cam_id: str) -> Optional[Beat]:
    """한 스테이션의 마지막 하트비트. 없으면 None."""
    return _beats.get(cam_id)


def _latest() -> Optional[Beat]:
    if not _beats:
        return None
    return max(_beats.values(), key=lambda b: b.seen)


def last_seen() -> Optional[datetime]:
    """가장 최근 하트비트 시각(UTC, tz-aware). 기동 후 수신 없으면 None."""
    b = _latest()
    return b.seen if b else None


def last_cam_id() -> Optional[str]:
    """가장 최근 하트비트를 보낸 카메라 ID. 수신 없으면 None."""
    b = _latest()
    return b.cam_id if b else None


def all_beats() -> list[Beat]:
    """스테이션별 마지막 하트비트. cam_id 순으로 정렬(화면 표시가 흔들리지 않게)."""
    return sorted(_beats.values(), key=lambda b: b.cam_id)


def reset() -> None:
    """기록 초기화(테스트/재기동 시뮬레이션 전용)."""
    _beats.clear()
