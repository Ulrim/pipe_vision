"""길이 측정 오차 예산 — "이 광학 구성으로 그 공차를 잴 수 있는가" 를 숫자로 답한다.

MSA(`quality/msa.py`)는 **이미 찍힌 이미지**를 반복 측정해 파이프라인의 변동을
본다. 파이프라인이 결정적이라 같은 이미지를 30번 넣으면 σ=0 이 나온다 — 코드가
안정적이라는 뜻이지, **측정시스템이 공차를 감당한다는 뜻이 아니다.**

실제로 공차를 갉아먹는 것은 코드 밖에 있다: 화소 분해능, 렌즈 왜곡 잔차, 제품이
놓이는 높이의 흔들림, 온도, 기준자 정확도, 롤링셔터. 이 모듈은 그 항들을 하나씩
세워 **합성 불확도**를 내고 AIAG %GR&R 기준으로 합·부를 판정한다.

항을 두 종류로 나눈다 — 섞으면 판단이 틀어진다.

* **랜덤(반복성)** — 같은 제품을 다시 재면 다르게 나오는 양. 보정으로 못 없앤다.
  %GR&R 은 **이것만** 으로 계산한다.
* **계통(bias)** — 항상 같은 방향으로 틀리는 양. 품목별 보정계수
  (`item_master.px_to_mm_scale`)가 흡수한다. 단 **흡수 조건이 있다**: 보정을
  실제 제품과 같은 높이·같은 온도·같은 속도에서 했어야 한다. 조건이 깨지면
  bias 는 그대로 오차로 남는다. 그래서 따로 보고한다.

참고(2026-10-03 확인): 라즈베리파이 5 는 CSI 커넥터가 **2개**(CAM0/CAM1)다.
그래서 "양 끝단 2카메라" 구성이 파이에서도 가능하다 — 파이4(CSI 1개) 기준으로
`CLAUDE.md` §A.1.1 이 그 선택지를 지웠던 것은 파이5 에는 해당하지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

#: 알루미늄 선팽창계수 [1/K]. Clad AL Header Pipe 가 대상이므로 기본값으로 둔다.
CTE_ALUMINIUM = 23.1e-6
#: 강(기준자용) 선팽창계수 [1/K].
CTE_STEEL = 11.7e-6

#: AIAG 관례. %GR&R ≤ 10 양호, ≤ 30 조건부 수용, > 30 불가.
GRR_GOOD = 10.0
GRR_MARGINAL = 30.0


@dataclass(frozen=True)
class OpticalSetup:
    """길이 측정 한 스테이션의 물리 구성.

    기본값은 "잘 만든 라즈베리파이 셋업"을 가정한다. 현장 값이 나오면 바꿔서
    다시 돌리면 된다 — 그게 이 모듈의 용도다.
    """

    length_mm: float
    """제품 전장. 오차 항 대부분이 길이에 비례하므로 가장 중요한 입력이다."""

    fov_mm: float
    """길이 방향 시야(mm). 다발을 한 화면에 담아도 **길이 방향** 시야만 분해능을
    정한다. 제품 길이 + 여유(보통 10~20%) + 기준자 자리."""

    sensor_px: int
    """길이 방향 유효 화소 수. 파이 카메라 3 = 4608, HQ(IMX477) = 4056,
    글로벌셔터(IMX296) = 1456."""

    working_distance_mm: float
    """렌즈~제품 상면 거리. 깊이 민감도가 1/WD 이므로 멀수록 유리하다."""

    edge_sigma_px: float = 0.1
    """끝단 서브픽셀 검출의 1σ(px). 대비가 좋은 실루엣 엣지에서 0.05~0.1px 가
    현실적이다. 반사가 심하거나 끝단이 번지면 0.3px 까지 간다."""

    distortion_residual_px: float = 0.3
    """렌즈 보정 후 남는 재투영 잔차 1σ(px). `calibrate_lens` 의 RMS 를 넣는다.
    **보정을 안 했다면 이 값이 아니라 `uncorrected_distortion_mm` 를 쓴다** —
    미보정 왜곡은 랜덤이 아니라 수 mm 짜리 계통오차다(§A.1.1)."""

    height_sigma_mm: float = 0.05
    """제품 상면 높이의 반복성 1σ. V홈이면 OD 공차에 지배된다. 평판 위에 그냥
    굴러 놓으면 훨씬 커진다."""

    temp_sigma_k: float = 3.0
    """측정 시점 제품 온도의 변동 1σ. 절단 직후라면 톱 발열로 훨씬 크다."""

    cte_per_k: float = CTE_ALUMINIUM

    scale_rel_sigma: float = 2e-5
    """기준자(스케일 게이지) 길이의 상대 불확도 1σ. 금속 자 등급에 따른다."""

    # --- 계통(bias) 항 ---
    height_offset_mm: float = 0.0
    """보정 시점과 측정 시점의 제품 상면 높이 차. OD 가 다른 품목으로 바꾸면서
    보정을 다시 안 하면 여기에 OD 차이의 절반이 들어온다."""

    temp_offset_k: float = 0.0
    """보정 시점 대비 평균 온도차."""

    conveyor_speed_mm_s: float = 0.0
    """촬영 순간의 제품 이송속도. 0 이면 정지 촬영(권장)."""

    rolling_readout_s: float = 0.0
    """전체 프레임 롤링셔터 읽기 시간(s). 파이 카메라 3 풀해상도 ≈ 1/14s.
    글로벌셔터면 0."""

    uncorrected_distortion_mm: float = 0.0
    """렌즈 보정을 하지 않았을 때의 끝단 왜곡 오차(mm). 보정했으면 0."""

    @property
    def mm_per_px(self) -> float:
        return self.fov_mm / float(self.sensor_px)


@dataclass
class BudgetResult:
    setup: OpticalSetup
    tol_plus_mm: float
    tol_minus_mm: float
    random_terms: Dict[str, float] = field(default_factory=dict)   # 1σ mm
    bias_terms: Dict[str, float] = field(default_factory=dict)     # mm (부호 있음)

    @property
    def tolerance_mm(self) -> float:
        """공차 **폭**. ±0.1 이면 0.2 다 — %GR&R 분모는 폭이다."""
        return self.tol_plus_mm + self.tol_minus_mm

    @property
    def sigma_mm(self) -> float:
        return math.sqrt(sum(v * v for v in self.random_terms.values()))

    @property
    def bias_mm(self) -> float:
        """계통항 합. 같은 방향으로 더해질 수 있으므로 제곱합이 아니라 단순합."""
        return sum(self.bias_terms.values())

    @property
    def pct_grr(self) -> float:
        t = self.tolerance_mm
        return (6.0 * self.sigma_mm / t * 100.0) if t > 0 else float("inf")

    @property
    def verdict(self) -> str:
        p = self.pct_grr
        if p <= GRR_GOOD:
            return "GOOD"
        if p <= GRR_MARGINAL:
            return "MARGINAL"
        return "FAIL"

    @property
    def passed(self) -> bool:
        return self.pct_grr <= GRR_MARGINAL

    def dominant(self, n: int = 3) -> List[Tuple[str, float]]:
        """큰 항부터. 어디를 고쳐야 하는지는 이것만 보면 된다."""
        return sorted(self.random_terms.items(), key=lambda kv: -kv[1])[:n]

    def max_mm_per_px(self) -> float:
        """%GR&R 30% 를 끝단 검출 노이즈**만으로** 다 쓴다고 할 때의 상한 mm/px.

        다른 항이 0 이어도 이보다 거칠면 불가능하다는 뜻 — 센서/시야를 고를 때
        먼저 보는 수치다.
        """
        budget_sigma = self.tolerance_mm * GRR_MARGINAL / 100.0 / 6.0
        per_px = math.sqrt(2.0) * self.setup.edge_sigma_px
        return budget_sigma / per_px if per_px > 0 else float("inf")

    def as_dict(self) -> dict:
        return {
            "length_mm": self.setup.length_mm,
            "fov_mm": self.setup.fov_mm,
            "sensor_px": self.setup.sensor_px,
            "mm_per_px": round(self.setup.mm_per_px, 6),
            "tolerance_mm": round(self.tolerance_mm, 4),
            "random_terms_mm": {k: round(v, 6) for k, v in self.random_terms.items()},
            "sigma_mm": round(self.sigma_mm, 6),
            "bias_terms_mm": {k: round(v, 6) for k, v in self.bias_terms.items()},
            "bias_mm": round(self.bias_mm, 6),
            "pct_grr": round(self.pct_grr, 2),
            "verdict": self.verdict,
            "passed": self.passed,
            "max_mm_per_px_for_30pct": round(self.max_mm_per_px(), 6),
        }


def length_budget(
    setup: OpticalSetup, *, tol_plus_mm: float, tol_minus_mm: float
) -> BudgetResult:
    """오차 예산을 세운다.

    각 항의 유도는 아래 주석에 남긴다. 숫자만 믿지 말고 가정이 현장과 맞는지
    보라 — 틀린 가정으로 낸 합격은 합격이 아니다.
    """
    s = setup
    mmpx = s.mm_per_px
    L = s.length_mm
    wd = s.working_distance_mm

    random_terms: Dict[str, float] = {}
    bias_terms: Dict[str, float] = {}

    # 끝단 검출: 양 끝 두 번 재므로 √2 배.
    random_terms["끝단검출"] = math.sqrt(2.0) * s.edge_sigma_px * mmpx

    # 렌즈 왜곡 잔차: 끝단이 화면 가장자리에 있으므로 잔차가 그대로 먹힌다.
    if s.distortion_residual_px > 0:
        random_terms["왜곡잔차"] = math.sqrt(2.0) * s.distortion_residual_px * mmpx

    # 높이(깊이) 반복성 → 배율 변동. 닮은꼴에서 오차 = L × Δz / WD.
    # 기준자를 같은 평면에 함께 찍으면 *평균* 높이차(bias)는 지워지지만,
    # 매 측정마다 흔들리는 양(σ)은 지워지지 않는다. 여기 들어오는 건 후자다.
    if wd > 0:
        random_terms["높이반복성"] = L * s.height_sigma_mm / wd

    # 열팽창: 알루미늄은 1m·1K 에 23µm 다. 250mm·3K 면 17µm — 무시 못 한다.
    random_terms["열팽창"] = L * s.cte_per_k * s.temp_sigma_k

    # 기준자 길이 불확도는 길이에 비례해 그대로 옮겨온다.
    random_terms["기준자"] = L * s.scale_rel_sigma

    # --- 계통 ---
    if wd > 0 and s.height_offset_mm:
        # 품목이 바뀌어 OD 가 달라졌는데 보정을 안 했을 때가 전형적이다.
        bias_terms["높이오프셋"] = L * s.height_offset_mm / wd
    if s.temp_offset_k:
        bias_terms["온도오프셋"] = L * s.cte_per_k * s.temp_offset_k
    if s.conveyor_speed_mm_s and s.rolling_readout_s and s.fov_mm > 0:
        # 롤링셔터: 양 끝단은 t = readout × (L/FOV) 만큼 시차를 두고 읽힌다.
        # 그 사이 제품이 이동한 거리가 그대로 길이에 더해지거나 빠진다.
        dt = s.rolling_readout_s * (L / s.fov_mm)
        bias_terms["롤링셔터"] = s.conveyor_speed_mm_s * dt
    if s.uncorrected_distortion_mm:
        bias_terms["미보정왜곡"] = s.uncorrected_distortion_mm

    return BudgetResult(
        setup=s,
        tol_plus_mm=tol_plus_mm,
        tol_minus_mm=tol_minus_mm,
        random_terms=random_terms,
        bias_terms=bias_terms,
    )


def required_fov_mm(
    *,
    tol_plus_mm: float,
    tol_minus_mm: float,
    sensor_px: int,
    edge_sigma_px: float = 0.1,
    share: float = 0.5,
) -> float:
    """끝단검출 항이 공차 예산의 `share` 만 쓰도록 하는 최대 시야(mm).

    `share=0.5` 는 "분해능이 예산의 절반, 나머지 절반을 치구·온도·왜곡에" 라는
    배분이다. 센서를 고를 때 역산용.
    """
    tol = tol_plus_mm + tol_minus_mm
    sigma_budget = tol * GRR_MARGINAL / 100.0 / 6.0 * share
    mmpx = sigma_budget / (math.sqrt(2.0) * edge_sigma_px)
    return mmpx * sensor_px


def render_md(res: BudgetResult, *, title: str = "길이 측정 오차 예산") -> str:
    d = res.as_dict()
    lines = [
        f"# {title}",
        "",
        f"- 제품 전장 **{d['length_mm']:g} mm**, 공차 폭 **{d['tolerance_mm']:g} mm**"
        f" (+{res.tol_plus_mm:g}/-{res.tol_minus_mm:g})",
        f"- 시야 {d['fov_mm']:g} mm / 센서 {d['sensor_px']} px"
        f" → **{d['mm_per_px']:.4f} mm/px**",
        "",
        "## 랜덤 항 (반복성 — 보정으로 못 없앤다)",
        "",
        "| 항 | 1σ (mm) | 공차 폭 대비 |",
        "|---|---|---|",
    ]
    tol = res.tolerance_mm
    for k, v in sorted(res.random_terms.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v:.4f} | {(6*v/tol*100 if tol else 0):.0f}% |")
    lines += [
        f"| **합성 σ** | **{d['sigma_mm']:.4f}** | **{d['pct_grr']:.0f}%** |",
        "",
        f"**%GR&R = {d['pct_grr']:.1f}% → {d['verdict']}**"
        f" (AIAG: ≤{GRR_GOOD:g}% 양호, ≤{GRR_MARGINAL:g}% 조건부)",
        "",
    ]
    if res.bias_terms:
        lines += [
            "## 계통 항 (품목별 보정계수가 흡수 — 단, 보정 조건이 같을 때만)",
            "",
            "| 항 | mm |",
            "|---|---|",
        ]
        for k, v in sorted(res.bias_terms.items(), key=lambda kv: -abs(kv[1])):
            lines.append(f"| {k} | {v:+.4f} |")
        lines += [f"| **합** | **{d['bias_mm']:+.4f}** |", ""]
    lines += [
        "## 해석",
        "",
        f"- 끝단검출만으로 30% 를 다 쓴다고 해도 **{d['max_mm_per_px_for_30pct']:.4f} mm/px**"
        " 보다 거칠면 이 공차는 불가능하다.",
        "- 큰 항부터: "
        + ", ".join(f"{k}({v:.4f}mm)" for k, v in res.dominant()),
        "",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class FrameFit:
    """한 프레임이 다발 폭과 길이 창을 동시에 담을 때의 실제 분해능.

    다발 검사라서 **가로(다발 폭)와 세로(길이 방향)가 같은 센서를 나눠 쓴다.**
    공차를 결정하는 것은 길이 방향 분해능인데, 그 분해능은 길이가 아니라
    **다발 폭** 이 정하는 경우가 많다 — 폭을 담느라 배율을 낮추면 길이도 같이
    거칠어지기 때문이다. 이 사실이 "끝단만 좁게 보는 2카메라" 라는 해법을
    다발 검사에서 무력화시킨다.
    """

    mm_per_px: float
    length_window_mm: float
    """그 배율에서 길이 방향으로 실제 담기는 거리."""
    cross_fov_mm: float
    rotated: bool
    """True = 센서 긴 축을 다발 폭 방향으로 눕힌 배치."""
    binding: str
    """'width' = 다발 폭이 분해능을 제한, 'length' = 길이 창이 제한."""


def fit_frame(
    *,
    sensor_long_px: int,
    sensor_short_px: int,
    bundle_width_mm: float,
    min_length_window_mm: float,
) -> FrameFit:
    """두 방향을 모두 담는 배치 중 분해능이 가장 좋은 쪽을 고른다."""
    best: FrameFit | None = None
    for rotated in (False, True):
        cross_px = sensor_long_px if rotated else sensor_short_px
        len_px = sensor_short_px if rotated else sensor_long_px
        need_w = bundle_width_mm / cross_px
        need_l = min_length_window_mm / len_px
        mmpx = max(need_w, need_l)
        fit = FrameFit(
            mm_per_px=mmpx,
            length_window_mm=len_px * mmpx,
            cross_fov_mm=cross_px * mmpx,
            rotated=rotated,
            binding="width" if need_w >= need_l else "length",
        )
        if best is None or fit.mm_per_px < best.mm_per_px:
            best = fit
    assert best is not None
    return best
