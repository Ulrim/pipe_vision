"""길이+표면 종합 판정 (M5).

결정적 통합 룰. 임계는 ItemMaster 에서 읽어 review(경계) 판정에 사용한다.
"""
from __future__ import annotations

from typing import List, Optional

from aivis_types import (
    DefectCode,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)


def _is_ng(v) -> bool:
    # use_enum_values=True 라 문자열일 수 있음 → 둘 다 안전 비교.
    return v == Verdict.NG or v == Verdict.NG.value


def _confidence(
    length: LengthResult, surface: SurfaceResult, item: ItemMaster
) -> float:
    """종합 신뢰도(0~1). 결정적.

    - 길이: 공차 대비 편차 여유(margin)를 신뢰도로. 끝단검출 실패 시 낮춤.
    - 표면: 각 점수의 임계 대비 분리도(거리)를 신뢰도로.
    최종은 길이/표면 신뢰도의 최솟값(가장 불확실한 판단이 전체 신뢰도 결정).
    """
    # --- 길이 신뢰도 ---
    if not length.edge_detected or length.deviation_mm is None:
        len_conf = 0.0
    else:
        tol_plus = float(item.tol_plus_mm)
        tol_minus = float(item.tol_minus_mm)
        dev = float(length.deviation_mm)
        if dev >= 0:
            margin = (tol_plus - dev) / tol_plus if tol_plus > 0 else 0.0
        else:
            margin = (tol_minus - abs(dev)) / tol_minus if tol_minus > 0 else 0.0
        # OK 면 여유가 클수록, NG 면 초과가 클수록 신뢰.
        if _is_ng(length.length_verdict):
            # NG: 공차를 얼마나 넘었나(초과분/공차)로 신뢰.
            over = abs(dev) - (tol_plus if dev >= 0 else tol_minus)
            base = tol_plus if dev >= 0 else tol_minus
            len_conf = min(1.0, 0.6 + (over / base if base > 0 else 0.0))
        else:
            len_conf = max(0.0, min(1.0, 0.6 + 0.4 * margin))

    # --- 표면 신뢰도 ---
    scores = [
        (surface.oil_score, item.oil_threshold),
        (surface.discolor_score, item.discolor_threshold),
        (surface.scratch_score, item.scratch_threshold),
    ]
    seps: List[float] = []
    for score, th in scores:
        if score is None:
            continue
        t = float(th) if th is not None else 0.5
        seps.append(min(1.0, abs(float(score) - t) / max(t, 1e-3)))
    surf_conf = min(seps) if seps else 0.5
    surf_conf = max(0.0, min(1.0, 0.6 + 0.4 * surf_conf))

    return round(min(len_conf, surf_conf), 4)


def _near_threshold(
    score: Optional[float], th: Optional[float], rel: float
) -> bool:
    if score is None:
        return False
    t = float(th) if th is not None else 0.5
    band = max(t * rel, 0.03)
    return abs(float(score) - t) <= band


def combine_verdict(
    length: LengthResult,
    surface: SurfaceResult,
    item: ItemMaster,
    *,
    review_rel_band: float = 0.15,
    proc_time_ms: int = 0,
) -> VerdictResult:
    """종합 판정. length+surface → VerdictResult.

    review_rel_band: 임계 대비 상대 밴드(±15% 기본). 이 밴드 안의 점수/편차는
    경계 사례로 보고 review_flag=True (오검/미검 후보 자동분류).
    """
    # --- 불량 코드 합집합 ---
    codes: List[DefectCode] = []
    if _is_ng(length.length_verdict):
        codes.append(DefectCode.LEN)
    for c in surface.defect_codes:
        # surface.defect_codes 는 use_enum_values 로 문자열일 수 있음.
        code = c if isinstance(c, DefectCode) else DefectCode(c)
        if code not in codes:
            codes.append(code)

    # 2종 이상이면 MULTI 추가(§7.2).
    if len(codes) >= 2 and DefectCode.MULTI not in codes:
        codes.append(DefectCode.MULTI)

    final = Verdict.NG if codes else Verdict.OK
    confidence = _confidence(length, surface, item)

    # --- review_flag: 경계값 자동분류 ---
    review = False
    # 길이 편차가 공차 경계에 근접?
    if length.edge_detected and length.deviation_mm is not None:
        dev = float(length.deviation_mm)
        tol = float(item.tol_plus_mm) if dev >= 0 else float(item.tol_minus_mm)
        if tol > 0 and abs(abs(dev) - tol) <= tol * review_rel_band:
            review = True
    else:
        # 끝단 검출 실패 자체가 재확인 대상.
        review = True
    # 표면 점수가 임계 근처?
    if _near_threshold(surface.oil_score, item.oil_threshold, review_rel_band):
        review = True
    if _near_threshold(
        surface.discolor_score, item.discolor_threshold, review_rel_band
    ):
        review = True
    if _near_threshold(
        surface.scratch_score, item.scratch_threshold, review_rel_band
    ):
        review = True
    # 종합 신뢰도가 낮아도 재확인.
    if confidence < 0.65:
        review = True

    return VerdictResult(
        final_verdict=final,
        defect_codes=codes,
        confidence=confidence,
        review_flag=review,
        length=length,
        surface=surface,
        proc_time_ms=proc_time_ms,
    )
