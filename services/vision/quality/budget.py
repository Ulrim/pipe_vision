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
from typing import Dict, List, Optional, Tuple

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

    # --- 소프트웨어로 좋아지는 항 (2026-10-03 추가) ---
    edge_average_rows: int = 1
    """끝단 직선 적합에 쓰인 행 수. 행마다 독립적인 서브픽셀 추정을 평균하므로
    끝단 노이즈가 1/√N 로 준다. 튜브 OD 가 화면에서 200px 이면 N≈200, 즉
    **14배** 좋아진다. 초판 예산은 이걸 빼먹어 끝단 항을 크게 과대평가했다.
    (`length/edges.py` 의 fit_edge_lines)"""

    per_frame_scale: bool = False
    """기준자로 **매 프레임** 배율을 다시 재는가(`calib/fiducial.py`).
    True 면 높이 변동이 기준자에도 똑같이 걸려 상쇄되므로, 남는 것은 기준자와
    제품의 **평면 차이** 변동(coplanarity_sigma_mm)뿐이다. 이게 단일 카메라로
    ±0.1mm 에 다가가는 가장 큰 한 수다."""

    coplanarity_sigma_mm: float = 0.02
    """기준자 평면과 제품 상면의 높이차 변동 1σ. per_frame_scale 일 때만 쓴다.
    기준자를 제품 상면 높이에 고정하면 OD 공차 정도로 떨어진다."""

    tilt_corrected: bool = True
    """끝단 직선 적합으로 기울기를 보정하는가. False 면 1/cosθ 계통오차가
    그대로 남는다 — 250mm·2° 에 +0.152mm 다."""

    typical_tilt_deg: float = 1.5
    """컨베이어에서 흔한 기울기. 보정 전 계통오차와 보정 후 잔차의 기준점."""

    tilt_sigma_deg: Optional[float] = None
    """기울기 각도의 불확도 1σ. None 이면 edge_span_px·edge_average_rows 로
    **기하에서 계산**한다(tilt_sigma_from_fit). 추측값을 넣는 것보다 낫다 —
    처음에 0.1° 로 어림잡았다가 실제론 0.007° 임을 알고 바꿨다."""

    edge_span_px: Optional[float] = None
    """끝단 직선이 걸쳐 있는 세로 길이(px) ≈ 화면에서의 튜브 OD.
    기울기 불확도가 이 값에 반비례한다. None 이면 edge_average_rows 로 본다."""

    gauge_interpolated: bool = False
    """기준자를 '자'로 읽는가(FiducialScale.span_mm). True 면 끝단 바로 옆
    마크로 그 자리의 배율을 쓰므로 왜곡 잔차가 거의 지워진다. 대신 **마크 중심
    검출 노이즈**가 새 바닥이 된다."""

    gauge_pitch_px: float = 20.0
    """기준자 마크 간격(px). 보간 구간이 짧을수록 왜곡이 잘 지워진다."""

    mark_sigma_px: float = 0.05
    """마크 중심 검출의 1σ(px). 고대비 원형 마크면 0.05px 가 현실적이다."""

    local_marks: int = 5
    """각 끝단 근처에서 국소 배율에 쓰는 마크 수. 많을수록 1/√N 로 좋아진다."""

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

    # 끝단 검출: 양 끝 두 번 재므로 √2 배. 행 N개를 평균하면 1/√N.
    rows = max(1, int(s.edge_average_rows))
    random_terms["끝단검출"] = (
        math.sqrt(2.0) * s.edge_sigma_px * mmpx / math.sqrt(rows)
    )

    # 렌즈 왜곡 잔차: 끝단이 화면 가장자리에 있으므로 잔차가 그대로 먹힌다.
    # 행 평균으로는 줄지 않는다 — 왜곡은 노이즈가 아니라 위치의 함수라서
    # 같은 열의 모든 행이 같은 방향으로 틀린다.
    if s.distortion_residual_px > 0:
        d = math.sqrt(2.0) * s.distortion_residual_px * mmpx
        if s.gauge_interpolated and s.sensor_px > 0:
            # 기준자를 자로 읽으면 끝단 옆 마크 사이에서만 보간하므로, 천천히
            # 변하는 잔차의 **선형 성분**이 지워진다. 남는 것은 보간 구간
            # 길이에 비례하는 몫 — 보수적으로 1차(pitch/frame)로 둔다.
            d *= s.gauge_pitch_px / s.sensor_px
        random_terms["왜곡잔차"] = d

    # 기준자를 자로 읽으면 마크 중심 검출 노이즈가 새 바닥이 된다.
    if s.gauge_interpolated:
        marks = max(1, int(s.local_marks))
        random_terms["마크검출"] = (
            2.0 * math.sqrt(2.0) * s.mark_sigma_px * mmpx / math.sqrt(marks)
        )

    # 높이(깊이) 반복성 → 배율 변동. 닮은꼴에서 오차 = L × Δz / WD.
    if wd > 0:
        if s.per_frame_scale:
            # 기준자가 제품과 같이 움직이므로 상쇄된다. 남는 것은 둘 사이의
            # 평면 차이 변동뿐 — 이게 상수 스케일과의 결정적 차이다.
            random_terms["평면차(기준자)"] = L * s.coplanarity_sigma_mm / wd
        else:
            random_terms["높이반복성"] = L * s.height_sigma_mm / wd

    # 열팽창: 알루미늄은 1m·1K 에 23µm 다. 250mm·3K 면 17µm — 무시 못 한다.
    # 온도를 재서 환산하면(fiducial.to_reference_temperature) temp_sigma_k 는
    # '온도계의 불확도'로 줄어든다.
    random_terms["열팽창"] = L * s.cte_per_k * s.temp_sigma_k

    # 기준자 길이 불확도는 길이에 비례해 그대로 옮겨온다.
    random_terms["기준자"] = L * s.scale_rel_sigma

    # 기울기. 보정하면 각도 불확도만 남는다: d/dθ(L/cosθ) = L·tanθ·secθ ≈ L·θ.
    tilt = math.radians(s.typical_tilt_deg)
    if s.tilt_corrected:
        sig_deg = (
            s.tilt_sigma_deg
            if s.tilt_sigma_deg is not None
            else tilt_sigma_from_fit(
                edge_sigma_px=s.edge_sigma_px,
                n_rows=s.edge_average_rows,
                span_px=s.edge_span_px if s.edge_span_px else s.edge_average_rows,
            )
        )
        random_terms["기울기잔차"] = L * abs(tilt) * math.radians(sig_deg)
    elif tilt:
        bias_terms["기울기미보정"] = L * (1.0 / math.cos(tilt) - 1.0)

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


@dataclass(frozen=True)
class CameraOptics:
    """카메라 1대의 광학 제원. 작업거리를 '권장' 이 아니라 **계산**하기 위한 것."""

    name: str
    sensor_w_mm: float
    """측정축(긴 변) 방향 이미지 영역 크기."""
    sensor_h_mm: float
    px_w: int
    px_h: int
    focal_mm: Optional[float] = None
    """고정렌즈의 초점거리. None = C/CS 마운트(렌즈를 고를 수 있다)."""
    min_focus_mm: float = 100.0


#: 2026-10-03 제원 확인. CM3 는 **렌즈가 고정**이라 작업거리를 고를 수 없다 —
#: 이게 공차 ±0.1mm 에서 결정적인 제약이 된다(아래 working_distance_mm 참조).
PI_CAMERAS = {
    "cam3": CameraOptics("Camera Module 3 (IMX708)", 6.45, 3.63, 4608, 2592,
                         focal_mm=4.74, min_focus_mm=100.0),
    "hq": CameraOptics("HQ Camera (IMX477) + C/CS 렌즈", 6.287, 4.712,
                       4056, 3040, focal_mm=None),
    "gs": CameraOptics("Global Shutter (IMX296) + C/CS 렌즈", 6.3, 4.9,
                       1456, 1088, focal_mm=None),
}


def working_distance_mm(*, fov_mm: float, sensor_mm: float, focal_mm: float
                        ) -> float:
    """시야를 그만큼 담으려면 렌즈가 피사체에서 얼마나 떨어져야 하는가.

    배율 m = f/(d−f), 시야 = 센서/m 이므로  d = f·(시야/센서 + 1).
    (d 는 렌즈 주점~피사체. 실무에선 렌즈 앞면 기준으로 몇 mm 차이가 나지만
    수백 mm 규모에서는 무시할 수 있다.)
    """
    if focal_mm <= 0 or sensor_mm <= 0:
        raise ValueError("초점거리·센서 크기는 양수")
    return focal_mm * (fov_mm / sensor_mm + 1.0)


def focal_for(*, fov_mm: float, sensor_mm: float, working_distance_mm_: float
              ) -> float:
    """거꾸로 — 그 거리에 두고 싶으면 어떤 렌즈를 사야 하는가."""
    return working_distance_mm_ / (fov_mm / sensor_mm + 1.0)


def tilt_sigma_from_fit(
    *, edge_sigma_px: float, n_rows: int, span_px: float
) -> float:
    """직선 적합이 주는 기울기 각도의 1σ(도) — 추측이 아니라 기하.

    기울기 b 의 최소제곱 분산은 σ²/Σ(y−ȳ)² 다. 행이 세로로 균일하게 span_px 에
    걸쳐 N개 있으면 Σ(y−ȳ)² = N·span²/12 이므로

        σ_b = σ_point / (span/√12 · √N)

    OD 200px 에 200행이면 0.1px 짜리 점 노이즈가 0.007° 로 떨어진다. 처음에
    0.1° 로 어림잡았던 것은 **14배 과대평가**였다.
    """
    n = max(1, int(n_rows))
    if span_px <= 0 or edge_sigma_px <= 0:
        return 0.0
    sigma_b = edge_sigma_px / (span_px / math.sqrt(12.0) * math.sqrt(n))
    return math.degrees(math.atan(sigma_b))


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
