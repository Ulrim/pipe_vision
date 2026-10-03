"""선별 한계를 정한다 — "±얼마로 거를 것인가" 에 숫자로 답한다.

`budget.py` 의 %GR&R 은 **측정시스템을 특성화할 수 있는가**를 묻는다(AIAG).
그런데 인라인 선별에서 실제로 중요한 것은 다른 질문이다 —
**멀쩡한 걸 몇 개나 버리고, 불량을 몇 개나 내보내는가.**

둘은 같지 않다. %GR&R 이 79% 로 '불합격' 이어도 공정 산포가 충분히 좁으면
오판율이 1% 아래일 수 있다. 반대로 %GR&R 이 좋아도 공정이 한계선에 걸쳐
있으면 오판이 쏟아진다. **공정 산포를 모르면 선별 한계를 정할 수 없다.**

모델:
    참 편차  d ~ N(0, σp)      공정 산포
    측정오차 e ~ N(0, σm)      측정시스템(budget.py 의 합성 σ)
    측정값   m = d + e
    양품 정의: |d| ≤ spec       선별 통과: |m| ≤ screen

    오검(false reject) = P(|d| ≤ spec  그리고 |m| > screen)   멀쩡한 걸 버림
    미검(false accept) = P(|d| > spec  그리고 |m| ≤ screen)   불량을 내보냄

`screen < spec` 으로 두는 것을 **가드밴드**라 한다. 미검을 줄이는 대신 오검을
늘린다 — 어느 쪽이 비싼지는 현장이 정한다(재작업 비용 vs 고객 클레임).

계산은 **결정적**이다(몬테카를로 아님). d 에 대한 1차원 적분이고, 조건부
확률은 정규분포 CDF 로 닫힌 형태다. 표준 라이브러리만 쓴다 — 파이에 scipy 를
깔지 않기 위해서다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import List

_N = NormalDist()

#: 적분 범위(공정 σ 의 배수)와 분할 수. 꼬리까지 충분히 덮되 빠르게.
_SPAN_SIGMA = 10.0
_STEPS = 4000           # 짝수여야 한다(심슨 법칙)


@dataclass(frozen=True)
class ScreenResult:
    spec_mm: float
    """양품 정의(도면·요구 공차). ±spec_mm."""
    screen_mm: float
    """실제로 거르는 한계. ±screen_mm. spec 보다 작으면 가드밴드."""
    process_sigma_mm: float
    meas_sigma_mm: float

    true_good: float
    """공정이 만들어내는 진짜 양품 비율(0~1). 측정과 무관한 공정 능력."""
    false_reject: float
    """양품인데 떨어뜨리는 비율(0~1). 수율 손실."""
    false_accept: float
    """불량인데 통과시키는 비율(0~1). 고객에게 가는 것."""

    @property
    def misjudge(self) -> float:
        """오검+미검. CLAUDE.md §1.1 '검사불량률' 의 정의와 같다."""
        return self.false_reject + self.false_accept

    @property
    def shipped_defect_ppm(self) -> float:
        """통과분 중 불량 비율(ppm). 고객이 실제로 겪는 품질."""
        passed = 1.0 - self.true_good - self.false_accept + self.false_accept
        passed = (self.true_good - self.false_reject) + self.false_accept
        return (self.false_accept / passed * 1e6) if passed > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "spec_mm": self.spec_mm,
            "screen_mm": round(self.screen_mm, 4),
            "process_sigma_mm": self.process_sigma_mm,
            "meas_sigma_mm": self.meas_sigma_mm,
            "true_good_pct": round(self.true_good * 100, 4),
            "false_reject_pct": round(self.false_reject * 100, 4),
            "false_accept_pct": round(self.false_accept * 100, 4),
            "misjudge_pct": round(self.misjudge * 100, 4),
            "shipped_defect_ppm": round(self.shipped_defect_ppm, 1),
        }


def _accept_prob(d: float, screen: float, sm: float) -> float:
    """참 편차가 d 일 때 측정값이 선별을 통과할 확률."""
    if sm <= 0:
        return 1.0 if abs(d) <= screen else 0.0
    return _N.cdf((screen - d) / sm) - _N.cdf((-screen - d) / sm)


def _simpson(f, lo: float, hi: float, steps: int = _STEPS) -> float:
    """심슨 법칙. 구간을 쪼개 적분한다(결정적·재현 가능)."""
    if hi <= lo:
        return 0.0
    if steps % 2:
        steps += 1
    h = (hi - lo) / steps
    total = f(lo) + f(hi)
    for i in range(1, steps):
        total += (4.0 if i % 2 else 2.0) * f(lo + i * h)
    return total * h / 3.0


def screen_rates(
    *,
    spec_mm: float,
    meas_sigma_mm: float,
    process_sigma_mm: float,
    screen_mm: float | None = None,
) -> ScreenResult:
    """선별 한계를 정했을 때의 오검·미검을 구한다.

    screen_mm 생략 시 spec_mm 과 같게 둔다(가드밴드 없음).
    """
    if spec_mm <= 0:
        raise ValueError("spec_mm 은 0 보다 커야 한다")
    if process_sigma_mm < 0 or meas_sigma_mm < 0:
        raise ValueError("σ 는 음수일 수 없다")
    screen = spec_mm if screen_mm is None else float(screen_mm)

    sp, sm = process_sigma_mm, meas_sigma_mm
    if sp <= 0:
        # 공정 산포가 0 이면 모든 제품이 정확히 공칭값 — 양품 100%,
        # 측정오차가 한계를 넘을 때만 오검.
        fr = 1.0 - _accept_prob(0.0, screen, sm)
        return ScreenResult(spec_mm, screen, sp, sm, 1.0, fr, 0.0)

    if sm <= 0:
        # 측정오차가 없으면 판정이 계단함수가 된다. 계단을 수치적분하면
        # 불연속점에서 오차가 남으므로(심슨은 매끄러운 함수를 가정한다)
        # 닫힌 형태로 푼다 — 더 정확하고 더 빠르다.
        def band(a: float, b: float) -> float:
            return _N.cdf(b / sp) - _N.cdf(a / sp)

        good = band(-spec_mm, spec_mm)
        lo, hi = min(screen, spec_mm), max(screen, spec_mm)
        gap = band(lo, hi) + band(-hi, -lo)      # screen 과 spec 사이 띠
        return ScreenResult(
            spec_mm=spec_mm, screen_mm=screen, process_sigma_mm=sp,
            meas_sigma_mm=sm, true_good=good,
            false_reject=gap if screen < spec_mm else 0.0,
            false_accept=gap if screen > spec_mm else 0.0,
        )

    pdf = lambda d: math.exp(-0.5 * (d / sp) ** 2) / (sp * math.sqrt(2 * math.pi))
    lim = _SPAN_SIGMA * sp

    true_good = _simpson(pdf, -spec_mm, spec_mm)
    false_reject = _simpson(
        lambda d: pdf(d) * (1.0 - _accept_prob(d, screen, sm)), -spec_mm, spec_mm
    )
    false_accept = (
        _simpson(lambda d: pdf(d) * _accept_prob(d, screen, sm), -lim, -spec_mm)
        + _simpson(lambda d: pdf(d) * _accept_prob(d, screen, sm), spec_mm, lim)
    )
    return ScreenResult(
        spec_mm=spec_mm, screen_mm=screen,
        process_sigma_mm=sp, meas_sigma_mm=sm,
        true_good=true_good, false_reject=false_reject, false_accept=false_accept,
    )


def guard_band_for(
    *,
    spec_mm: float,
    meas_sigma_mm: float,
    process_sigma_mm: float,
    max_false_accept: float = 0.001,
    steps: int = 60,
) -> ScreenResult | None:
    """미검을 목표 이하로 눌러주는 **가장 느슨한** 선별 한계를 찾는다.

    느슨한 쪽부터 조여 가며 처음 만족하는 값을 쓴다 — 더 조이면 오검만
    늘어나므로, 조건을 만족하는 선에서 가장 헐거운 것이 최선이다.
    만족하는 값이 없으면 None(측정으로는 그 수준을 보장할 수 없다는 뜻).
    """
    for i in range(steps + 1):
        screen = spec_mm * (1.0 - i / steps)
        if screen <= 0:
            break
        r = screen_rates(spec_mm=spec_mm, meas_sigma_mm=meas_sigma_mm,
                         process_sigma_mm=process_sigma_mm, screen_mm=screen)
        if r.false_accept <= max_false_accept:
            return r
    return None


def sweep(
    *, spec_mm: float, meas_sigma_mm: float, process_sigmas: List[float],
) -> List[ScreenResult]:
    """공정 산포를 모를 때 — 여러 값으로 한 번에 본다."""
    return [
        screen_rates(spec_mm=spec_mm, meas_sigma_mm=meas_sigma_mm,
                     process_sigma_mm=sp)
        for sp in process_sigmas
    ]
