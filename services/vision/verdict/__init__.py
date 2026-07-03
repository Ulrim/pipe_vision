"""종합 판정 모듈 (M5) — CLAUDE.md §4④, §7.2 불량유형 코드표.

길이(LengthResult) + 표면(SurfaceResult) 통합:
- final_verdict = 모두 OK 면 OK, 아니면 NG.
- defect_codes = 길이(LEN) ∪ 표면({OIL,DIS,SCR}); 2종 이상이면 MULTI 추가.
- confidence  = 스코어 기반 결정적 산출.
- review_flag = 임계 근처 경계값 자동분류(오검/미검 후보, M5/M16).
결정성: 동일 입력 → 동일 출력.
"""
from __future__ import annotations

from .combine import combine_verdict

__all__ = ["combine_verdict"]
