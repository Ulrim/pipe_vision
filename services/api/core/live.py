"""라이브 뷰 중계 — 카메라 영상을 화면에 실시간으로 (2026-10-20).

설치할 때 카메라 위치·초점·노출·검사 영역을 맞추려면 지금 카메라가 보는 장면을 계속 봐야
한다. 화면(HMI·대시보드)은 클라우드(Vercel)에 있을 수 있어 파이에 직접 붙지 못하므로 API 가
중계한다.

흐름:
1. 화면이 `POST /system/live/{cam}/start` → 이 모듈이 '보고 싶다' 를 기한과 함께 기록.
2. 워커는 하트비트(`POST /inspection/status`) 응답의 `live: true` 를 보고 저해상도 흑백
   JPEG 을 초당 몇 장 `PUT /system/live/{cam}/frame` 으로 올린다(응답이 계속 `live` 인지 알려 준다).
3. 화면은 `GET /system/live/{cam}`(지표·기한 연장)와 `GET .../frame.jpg` 를 짧게 반복해 읽는다.
   화면을 닫으면 읽기가 끊겨 기한이 지나고, 워커는 다음 응답에서 `live: false` 를 받아 멈춘다
   — 아무도 안 볼 때는 영상이 회선을 쓰지 않는다(현장 무선 에그 용량).

같은 통로로 **1장 촬영**도 보낸다: 화면이 `POST .../capture` → 다음 하트비트·프레임 응답의
`capture: true`(한 번만) → 워커가 그 자리에서 1장 찍어 평소처럼 검사·저장한다.

메모리 전용이다(heartbeat 와 같다). 영상은 검사 기록이 아니라 DB·사진 저장소에 남기지 않고,
API 가 재시작되면 사라진다. 단일 프로세스 가정 — 여러 인스턴스로 띄우면 화면과 워커가 다른
인스턴스에 붙을 수 있다(Render 무료·현장 단독은 한 개).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

#: 화면이 상태를 읽을 때마다 '보고 있다' 를 이만큼 연장한다. 화면을 닫으면 이 시간 뒤 멈춘다.
VIEW_TTL_S = 15.0
#: start 직후에는 워커가 하트비트(대기 중 5초 간격)로 알아차릴 때까지 넉넉히 둔다.
START_TTL_S = 30.0
#: 한 번 켜면 이 시간을 넘겨 계속 보내지 않는다(화면을 켜 둔 채 잊어도 회선이 계속 새지 않게).
MAX_SESSION_S = 600.0
#: 프레임 한 장 상한(저해상도 흑백 JPEG 은 수십 KB).
MAX_FRAME_BYTES = 1_500_000
#: 이보다 오래된 프레임이면 화면에 '끊김' 으로 보인다.
STALE_S = 5.0

_clock = time.time


@dataclass
class _Cam:
    requested_until: float = 0.0
    session_started: float = 0.0
    started_by: Optional[str] = None
    capture: bool = False
    capture_by: Optional[str] = None
    jpeg: Optional[bytes] = None
    frame_at: Optional[float] = None
    seq: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)
    focus_peak: Optional[float] = None


_lock = threading.Lock()
_cams: Dict[str, _Cam] = {}


def _cam(cam_id: str) -> _Cam:
    c = _cams.get(cam_id)
    if c is None:
        c = _cams[cam_id] = _Cam()
    return c


def _iso(ts: Optional[float]) -> Optional[str]:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _requested(c: _Cam, now: float) -> bool:
    return c.requested_until > now and (now - c.session_started) <= MAX_SESSION_S


def start(cam_id: str, user: Optional[str] = None) -> Dict[str, Any]:
    """라이브 뷰를 켠다. 초점 최고값은 새로 센다(렌즈를 다시 돌리기 시작하는 순간이다)."""
    now = _clock()
    with _lock:
        c = _cam(cam_id)
        if not _requested(c, now):
            # 새 세션 — 지난 세션의 마지막 장면·지표를 지운다(다시 열었는데 옛 장면이 '끊김' 으로
            # 보이지 않게). seq 는 이어서 센다(화면은 번호가 바뀌어야 새 장면을 받는다).
            c.session_started = now
            c.focus_peak = None
            c.jpeg = None
            c.frame_at = None
            c.meta = {}
        c.requested_until = max(c.requested_until, now + START_TTL_S)
        c.started_by = user
        return _state(cam_id, c, now)


def stop(cam_id: str) -> Dict[str, Any]:
    now = _clock()
    with _lock:
        c = _cam(cam_id)
        c.requested_until = 0.0
        c.jpeg = None  # 메모리를 바로 돌려준다
        return _state(cam_id, c, now)


def request_capture(cam_id: str, user: Optional[str] = None) -> None:
    """다음 워커 응답에 1장 촬영을 실어 보낸다(한 번만). 라이브 뷰가 꺼져 있어도 된다."""
    with _lock:
        c = _cam(cam_id)
        c.capture = True
        c.capture_by = user


def wants(cam_id: str) -> Tuple[bool, bool]:
    """워커에게 줄 답: (영상을 보내야 하나, 지금 1장 찍어야 하나). capture 는 꺼내면 지워진다."""
    now = _clock()
    with _lock:
        c = _cams.get(cam_id)
        if c is None:
            return False, False
        cap, c.capture = c.capture, False
        return _requested(c, now), cap


def put_frame(cam_id: str, jpeg: bytes, meta: Optional[Dict[str, Any]] = None) -> Tuple[bool, bool]:
    """워커가 올린 프레임을 최신으로 둔다. 반환은 wants() 와 같다."""
    if len(jpeg) > MAX_FRAME_BYTES:
        raise ValueError(f"프레임이 너무 크다({len(jpeg)} bytes > {MAX_FRAME_BYTES})")
    now = _clock()
    with _lock:
        c = _cam(cam_id)
        if not _requested(c, now):
            # 아무도 안 본다 — 화면을 닫는 순간 날아오던 장면은 버린다(다음에 열 때 옛 장면이 남지 않게).
            cap, c.capture = c.capture, False
            return False, cap
        c.jpeg = jpeg
        c.frame_at = now
        c.seq += 1
        c.meta = dict(meta or {})
        focus = c.meta.get("focus")
        if isinstance(focus, (int, float)):
            c.focus_peak = float(focus) if c.focus_peak is None else max(c.focus_peak, float(focus))
    return wants(cam_id)


def touch(cam_id: str) -> None:
    """화면이 보고 있다 — 켜져 있는 동안만 기한을 연장한다(꺼진 것을 다시 켜지는 않는다)."""
    now = _clock()
    with _lock:
        c = _cams.get(cam_id)
        if c is not None and _requested(c, now):
            c.requested_until = max(c.requested_until, now + VIEW_TTL_S)


def _state(cam_id: str, c: _Cam, now: float) -> Dict[str, Any]:
    age = None if c.frame_at is None else round(now - c.frame_at, 2)
    requested = _requested(c, now)
    return {
        "cam_id": cam_id,
        "requested": requested,
        "streaming": bool(requested and age is not None and age <= STALE_S and c.jpeg is not None),
        "requested_until": _iso(c.requested_until) if requested else None,
        "session_left_s": round(max(0.0, MAX_SESSION_S - (now - c.session_started)), 1) if requested else None,
        "frame_at": _iso(c.frame_at),
        "age_s": age,
        "seq": c.seq,
        "capture_pending": c.capture,
        "focus_peak": None if c.focus_peak is None else round(c.focus_peak, 2),
        **{k: c.meta.get(k) for k in ("width", "height", "focus", "brightness",
                                      "saturated_pct", "dark_pct", "roi", "trigger")},
    }


def state(cam_id: str) -> Dict[str, Any]:
    now = _clock()
    with _lock:
        return _state(cam_id, _cams.get(cam_id) or _Cam(), now)


def frame(cam_id: str) -> Optional[Tuple[bytes, int]]:
    with _lock:
        c = _cams.get(cam_id)
        if c is None or c.jpeg is None:
            return None
        return c.jpeg, c.seq


def reset() -> None:
    """시험용."""
    with _lock:
        _cams.clear()
