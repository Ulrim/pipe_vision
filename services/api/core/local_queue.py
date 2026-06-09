"""검사결과 저장 실패 시 로컬 큐 백업·재시도 (CLAUDE.md §5 M7 DoD).

DB 트랜잭션 저장이 실패하면 검사결과 JSON 을 파일로 백업해 유실을 막고,
복구 시 큐를 재처리해 저장 성공률 100% 를 보장한다(데이터 저장 KPI).
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Callable

from aivis_types import InspectionResult

from core.config import get_settings


def _queue_dir() -> str:
    d = get_settings().local_queue_dir
    os.makedirs(d, exist_ok=True)
    return d


def backup(result: InspectionResult) -> str:
    """검사결과를 로컬 큐에 백업하고 파일 경로를 반환한다."""
    d = _queue_dir()
    ts = time.strftime("%Y%m%d-%H%M%S")
    fname = f"insp_{ts}_{uuid.uuid4().hex[:8]}.json"
    path = os.path.join(d, fname)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(result.model_dump_json())
    os.replace(tmp, path)  # 원자적 쓰기
    return path


def pending_files() -> list[str]:
    d = _queue_dir()
    return sorted(
        os.path.join(d, f)
        for f in os.listdir(d)
        if f.startswith("insp_") and f.endswith(".json")
    )


def load(path: str) -> InspectionResult:
    with open(path, encoding="utf-8") as f:
        return InspectionResult.model_validate_json(f.read())


def drain(saver: Callable[[InspectionResult], None]) -> int:
    """백업된 검사결과를 saver 로 재저장 시도. 성공한 항목 수 반환.

    saver 는 DB 저장 함수(예외 발생 시 해당 파일은 큐에 유지).
    """
    saved = 0
    for path in pending_files():
        try:
            result = load(path)
            saver(result)
        except Exception:
            # 재저장 실패: 파일 유지(다음 워치독/재시도에서 재처리).
            continue
        else:
            os.remove(path)
            saved += 1
    return saved


def pending_count() -> int:
    return len(pending_files())
