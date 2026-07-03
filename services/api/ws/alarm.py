"""연속 NG 알람 추적기 (CLAUDE.md §5 M6).

cam_id(라인) 단위로 연속 NG 카운터를 유지한다. 임계(기본 3,
AIVIS_CONSEC_NG_THRESHOLD)를 넘으면 /ws/live 로 alarm 이벤트
(type=alarm, kind=consecutive_ng, count, cam_id, threshold)를 발행한다.
OK 수신 시 해당 cam 의 카운터를 리셋한다.

인메모리 상태(단일 호스트 토폴로지, §4). 프로세스 재시작 시 카운터는 0.
"""
from __future__ import annotations

import threading
from typing import Optional


class ConsecutiveNgTracker:
    """cam_id 별 연속 NG 카운터."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, cam_id: str, verdict: str) -> int:
        """판정 1건 반영 후 해당 cam 의 현재 연속 NG 수를 반환.

        verdict == 'NG' 면 +1, 그 외(OK 등)는 0 으로 리셋.
        """
        with self._lock:
            if verdict == "NG":
                self._counts[cam_id] = self._counts.get(cam_id, 0) + 1
            else:
                self._counts[cam_id] = 0
            return self._counts[cam_id]

    def current(self, cam_id: str) -> int:
        with self._lock:
            return self._counts.get(cam_id, 0)

    def reset(self, cam_id: Optional[str] = None) -> None:
        """특정 cam 또는 전체 리셋(테스트/운영용)."""
        with self._lock:
            if cam_id is None:
                self._counts.clear()
            else:
                self._counts.pop(cam_id, None)


# 앱 전역 단일 트래커.
tracker = ConsecutiveNgTracker()
