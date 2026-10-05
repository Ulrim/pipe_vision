"""길이+표면 종합 판정 (M5).

결정적 통합 룰. 임계는 ItemMaster 에서 읽어 review(경계) 판정에 사용한다.
"""
from __future__ import annotations

from typing import List, Optional

from aivis_types import (
    DefectCode,
    InspectionStage,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)

#: 단계(=검사 모드)마다 판정에 넣는 결함 코드. 여기 없는 코드는 그 모드에서
#: 계산이 되더라도 **NG 사유로 쓰지 않는다.** 현장에서 세 가지가 섞여 나오니
#: "NG 가 왜 났는지 모르겠다" 는 말이 나왔다(2026-10-05). 한 모드는 한 질문에만
#: 답해야 작업자가 대응할 수 있다.
STAGE_CODES = {
    InspectionStage.CUT_LENGTH.value: frozenset({DefectCode.LEN}),
    InspectionStage.POST_WASH_SURFACE.value: frozenset(
        {DefectCode.OIL, DefectCode.DIS, DefectCode.SCR}
    ),
    InspectionStage.CRATE_COUNT.value: frozenset({DefectCode.COUNT}),
}


def codes_for_stage(stage) -> frozenset:
    """단계가 None/미지(옛 호출자)면 **전부** — 종전 동작과 같다."""
    if stage is None:
        return frozenset(
            {DefectCode.LEN, DefectCode.OIL, DefectCode.DIS, DefectCode.SCR}
        )
    key = stage.value if isinstance(stage, InspectionStage) else str(stage)
    return STAGE_CODES.get(key, codes_for_stage(None))


def _is_ng(v) -> bool:
    # use_enum_values=True 라 문자열일 수 있음 → 둘 다 안전 비교.
    return v == Verdict.NG or v == Verdict.NG.value


def _confidence(
    length: LengthResult,
    surface: SurfaceResult,
    item: ItemMaster,
    *,
    see_length: bool = True,
    see_surface: bool = True,
) -> float:
    """종합 신뢰도(0~1). 결정적.

    - 길이: 공차 대비 편차 여유(margin)를 신뢰도로. 끝단검출 실패 시 낮춤.
    - 표면: 각 점수의 임계 대비 분리도(거리)를 신뢰도로.
    최종은 길이/표면 신뢰도의 최솟값(가장 불확실한 판단이 전체 신뢰도 결정).
    모드에 속하지 않는 항목은 신뢰도 계산에서 뺀다 — 표면 모드에서 길이를
    안 쟀다고 신뢰도가 0 이 되어 전부 재확인으로 가면 안 된다.
    """
    # --- 길이 신뢰도 ---
    if not see_length:
        len_conf = 1.0
    elif not length.edge_detected or length.deviation_mm is None:
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
    if not see_surface:
        surf_conf = 1.0

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
    stage=None,
) -> VerdictResult:
    """종합 판정. length+surface → VerdictResult.

    review_rel_band: 임계 대비 상대 밴드(±15% 기본). 이 밴드 안의 점수/편차는
    경계 사례로 보고 review_flag=True (오검/미검 후보 자동분류).
    stage: 검사 단계(=모드). 주면 **그 모드의 항목만** NG 사유와 재확인 판단에
      쓴다(STAGE_CODES). None 이면 종전처럼 전부 — 옛 호출자·테스트 호환.
    """
    allowed = codes_for_stage(stage)
    see_length = DefectCode.LEN in allowed
    see_surface = bool(allowed & {DefectCode.OIL, DefectCode.DIS, DefectCode.SCR})

    # --- 불량 코드 합집합(모드에 속한 것만) ---
    codes: List[DefectCode] = []
    if see_length and _is_ng(length.length_verdict):
        codes.append(DefectCode.LEN)
    if see_surface:
        for c in surface.defect_codes:
            # surface.defect_codes 는 use_enum_values 로 문자열일 수 있음.
            code = c if isinstance(c, DefectCode) else DefectCode(c)
            if code in allowed and code not in codes:
                codes.append(code)

    # 2종 이상이면 MULTI 추가(§7.2).
    if len(codes) >= 2 and DefectCode.MULTI not in codes:
        codes.append(DefectCode.MULTI)

    final = Verdict.NG if codes else Verdict.OK
    confidence = _confidence(
        length, surface, item, see_length=see_length, see_surface=see_surface
    )

    # --- review_flag: 경계값 자동분류 (모드에 속한 항목만) ---
    review = False
    if see_length:
        # 길이 편차가 공차 경계에 근접?
        if length.edge_detected and length.deviation_mm is not None:
            dev = float(length.deviation_mm)
            tol = float(item.tol_plus_mm) if dev >= 0 else float(item.tol_minus_mm)
            if tol > 0 and abs(abs(dev) - tol) <= tol * review_rel_band:
                review = True
        else:
            # 끝단 검출 실패 자체가 재확인 대상.
            review = True
    if see_surface:
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
