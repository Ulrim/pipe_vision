"""다객체(다중 튜브) 윗면 검사 모듈 (M2~M5 확장).

한 프레임에 축이 나란히(서로 붙어) 눕힌 알루미늄 튜브 최대 20개를 개별 분리해
튜브별 길이+표면을 판정한다. 기존 단일 튜브 모듈(length.measure_length,
surface.analyze_surface)을 스트립 단위로 재사용한다(고전 CV, AI 불필요).

- segment_tubes: 밝기 프로파일의 crown/seam 주기 패턴으로 스트립 분할.
- inspect_batch : 스트립별 단일 튜브 파이프라인 → TubeResult + BatchResult.

모든 연산은 결정적이며 proc_time_ms 를 계측한다(vision-ai 원칙).
BatchResult/TubeResult 는 내부 dataclass(shared-types 미변경 — 승인 전).
"""
from __future__ import annotations

from .batch import BatchResult, TubeResult, inspect_batch
from .segment import MAX_TUBES_HARD, TubeROI, segment_tubes

__all__ = [
    "segment_tubes",
    "TubeROI",
    "MAX_TUBES_HARD",
    "inspect_batch",
    "TubeResult",
    "BatchResult",
]
