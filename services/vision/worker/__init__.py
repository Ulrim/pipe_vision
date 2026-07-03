"""AIVIS 검사 워커 패키지 (services/vision/worker).

`python -m worker` 로 기동하는 검사 런타임 루프(CLAUDE.md §4). 컨테이너(flat /app)
와 개발/테스트(vision.* 패키지) 양쪽 import 레이아웃을 _bootstrap 으로 흡수한다.
"""
from __future__ import annotations

from .config import WorkerConfig
from .runner import Worker, main

__all__ = ["Worker", "WorkerConfig", "main"]
