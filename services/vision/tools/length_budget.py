"""길이 공차 타당성 계산 CLI — "±0.1mm, 가능한가?" 에 숫자로 답한다.

    python -m vision.tools.length_budget --length 250 --tol 0.1
    python -m vision.tools.length_budget --length 250 --tol 0.1 --sensor hq \
        --fov 280 --wd 600 --height-sigma 0.02 --temp-sigma 1
    python -m vision.tools.length_budget --length 250 --tol 0.1 --sweep

`--sweep` 는 현실적인 구성 몇 가지를 한 번에 돌려 어디서 갈리는지 보여준다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vision.quality.screening import guard_band_for, screen_rates
from vision.quality.budget import (
    PI_CAMERAS,
    OpticalSetup,
    fit_frame,
    focal_for,
    length_budget,
    render_md,
    required_fov_mm,
    working_distance_mm,
)

#: 길이 방향 유효 화소. 파이에서 실제로 살 수 있는 것만 둔다.
SENSORS = {
    "cam3": 4608,      # Camera Module 3 (IMX708) 4608x2592, 롤링셔터, AF
    "hq": 4056,        # HQ Camera (IMX477) 4056x3040, 롤링셔터, C/CS 마운트
    "v2": 3280,        # Camera Module 2 (IMX219) 3280x2464
    "gs": 1456,        # Global Shutter (IMX296) 1456x1088
}

#: (긴 축, 짧은 축). 다발 폭과 길이 창이 센서를 나눠 쓰므로 양쪽이 다 필요하다.
SENSOR_WH = {
    "cam3": (4608, 2592),
    "hq": (4056, 3040),
    "v2": (3280, 2464),
    "gs": (1456, 1088),
}


def _screen(a: argparse.Namespace) -> int:
    """선별 한계를 정한다 — %GR&R 이 아니라 **오검·미검**으로 본다.

    %GR&R 은 "측정시스템을 특성화할 수 있는가"를 묻는 지표다. 인라인 선별에서
    정작 중요한 것은 "멀쩡한 걸 몇 개 버리고 불량을 몇 개 내보내는가" 이고,
    그 답은 **공정 산포에 달려 있다.** %GR&R 이 불합격이어도 공정이 좁으면
    쓸 만할 수 있다.
    """
    sm = a.meas_sigma
    if sm is None:
        print("--meas-sigma (측정 1σ, mm) 가 필요합니다. "
              "--optics 로 구한 합성 σ 를 넣으세요.", file=sys.stderr)
        return 2
    spec = a.tol
    print(f"선별 ±{spec:g}mm, 측정 1σ = {sm*1000:.1f}µm\n")
    print(f"{'공정산포1σ':>10s} {'진짜양품':>9s} {'오검':>7s} {'미검':>7s} "
          f"{'검사불량률':>10s} {'출하불량':>10s}")
    print("-" * 62)
    for sp in a.process_sigmas:
        r = screen_rates(spec_mm=spec, meas_sigma_mm=sm, process_sigma_mm=sp)
        d = r.as_dict()
        print(f"{sp*1000:7.0f}µm {d['true_good_pct']:8.2f}% "
              f"{d['false_reject_pct']:6.2f}% {d['false_accept_pct']:6.2f}% "
              f"{d['misjudge_pct']:9.2f}% {d['shipped_defect_ppm']:7.0f}ppm")
    print("\n검사불량률 = 오검+미검 (CLAUDE.md §1.1, 목표 30% 이하)")
    print("출하불량   = 통과분 중 불량 ppm (공정불량률 목표 600ppm 과 비교)\n")

    print(f"미검을 {a.max_false_accept*100:g}% 이하로 누르는 선별 한계(가드밴드):")
    for sp in a.process_sigmas:
        g = guard_band_for(spec_mm=spec, meas_sigma_mm=sm, process_sigma_mm=sp,
                           max_false_accept=a.max_false_accept)
        if g is None:
            print(f"  공정 1σ={sp*1000:3.0f}µm → 어떤 한계로도 보장 불가")
        else:
            d = g.as_dict()
            print(f"  공정 1σ={sp*1000:3.0f}µm → ±{g.screen_mm:.3f}mm "
                  f"(미검 {d['false_accept_pct']:.2f}%, 오검 {d['false_reject_pct']:.1f}%)")
    return 0


def _optics(a: argparse.Namespace) -> int:
    """카메라를 제품에서 몇 cm 떨어뜨려야 하는가.

    핵심: **시야가 정해지면 mm/px 은 작업거리와 무관하다.** 거리는 깊이
    민감도(길이×Δz/거리)만 바꾸므로 멀수록 유리하고 트레이드오프가 없다.
    따라서 "적당한 거리"가 아니라 "공차가 허락하는 최소 거리"를 찾는 문제다.
    """
    a = _resolve(a, "optics")
    tol, L = a.tol, a.length
    fov = a.fov if a.fov else L * 1.15

    def budget(wd: float, px: int):
        s = OpticalSetup(
            length_mm=L, fov_mm=fov, sensor_px=px, working_distance_mm=wd,
            edge_sigma_px=a.edge_sigma, distortion_residual_px=a.distortion_px,
            edge_average_rows=a.edge_rows, edge_span_px=float(a.edge_rows),
            per_frame_scale=True, coplanarity_sigma_mm=a.height_sigma,
            gauge_interpolated=True, scale_rel_sigma=0.0,
            temp_sigma_k=a.temp_sigma, tilt_corrected=True,
        )
        return length_budget(s, tol_plus_mm=tol, tol_minus_mm=tol)

    print(f"제품 {L:g}mm, 시야 {fov:g}mm, 공차 ±{tol:g}mm (폭 {2*tol:g}mm)")
    print("전제 — 이 값이 안 맞으면 아래 거리도 안 맞는다:")
    print("  기준자 프레임별 측정 + 자 보간 적용, 기울기 보정 적용")
    print(f"  기준자-제품 평면차 1σ   {a.height_sigma:g} mm")
    print(f"  제품 온도 1σ            {a.temp_sigma:g} K")
    print(f"  끝단 적합 행 수          {a.edge_rows} (≈ 화면에서의 튜브 OD px)")
    print(f"  렌즈보정 후 잔차         {a.distortion_px:g} px\n")

    print("작업거리별:")
    print(f"  {'거리':>8s} {'평면차항':>9s} {'합성σ':>8s} {'%GR&R':>7s}  판정")
    need = None
    for wd in (150, 200, 300, 400, 500, 600, 800, 1000, 1500, 2000):
        r = budget(float(wd), PI_CAMERAS["hq"].px_w)
        if need is None and r.passed:
            need = wd
        print(f"  {wd:6d}mm {r.random_terms.get('평면차(기준자)', 0):9.4f} "
              f"{r.sigma_mm:8.4f} {r.pct_grr:7.1f}  {r.verdict}")
    if need is None:
        print("\n  2m 까지 가도 통과하지 못한다 — 거리 말고 다른 항이 범인이다.")
    else:
        print(f"\n  → 통과에 필요한 최소 작업거리 약 **{need}mm ({need/10:.0f}cm)**")

    print("\n카메라·렌즈별 실현 가능한 거리:")
    for key, cam in PI_CAMERAS.items():
        cross = cam.sensor_h_mm / cam.sensor_w_mm * fov
        if cam.focal_mm:
            d = working_distance_mm(fov_mm=fov, sensor_mm=cam.sensor_w_mm,
                                    focal_mm=cam.focal_mm)
            r = budget(d, cam.px_w)
            print(f"  {cam.name}")
            print(f"    렌즈 고정 f={cam.focal_mm}mm → 거리가 "
                  f"**{d:.0f}mm 로 강제됨** (고를 수 없다)")
            print(f"    %GR&R {r.pct_grr:.0f}% {r.verdict}, "
                  f"다발폭 {cross:.0f}mm, 최단초점 {cam.min_focus_mm:.0f}mm")
            if d < cam.min_focus_mm:
                print("    ⚠ 최단 초점거리보다 가깝다 — 초점이 안 맞는다")
        else:
            print(f"  {cam.name}  (다발폭 {cross:.0f}mm)")
            for f in (6, 8, 12, 16, 25, 35, 50):
                d = working_distance_mm(fov_mm=fov, sensor_mm=cam.sensor_w_mm,
                                        focal_mm=float(f))
                r = budget(d, cam.px_w)
                mark = " ←권장" if r.pct_grr <= 26.0 else ""
                print(f"    f={f:2d}mm → {d:6.0f}mm ({d/10:5.1f}cm)  "
                      f"%GR&R {r.pct_grr:5.1f}  {r.verdict}{mark}")
    if need:
        f_need = focal_for(fov_mm=fov, sensor_mm=PI_CAMERAS["hq"].sensor_w_mm,
                           working_distance_mm_=float(need))
        print(f"\n  거리 {need}mm 를 만들려면 HQ 기준 f≈{f_need:.0f}mm 렌즈.")
    return 0


def _bundle(a: argparse.Namespace) -> int:
    """다발 폭을 함께 담아야 할 때 길이 분해능이 어디까지 떨어지는지 본다."""
    tol = a.tol
    print(f"제품 {a.length:g}mm, 공차 ±{tol:g}mm (폭 {2*tol:g}mm), "
          f"다발 폭 {a.bundle_width:g}mm")
    print(f"{'센서':6s} {'배치':10s} {'제한':7s} {'mm/px':>8s} {'길이창':>8s} "
          f"{'σ(mm)':>8s} {'%GR&R':>7s}  판정")
    print("-" * 78)
    worst_ok = False
    for name in ("cam3", "hq"):
        lw, sw = SENSOR_WH[name]
        for window, label in ((a.length * 1.15, "전장"), (40.0, "끝단창")):
            f = fit_frame(
                sensor_long_px=lw, sensor_short_px=sw,
                bundle_width_mm=a.bundle_width,
                min_length_window_mm=window,
            )
            s_ = OpticalSetup(
                length_mm=a.length,
                fov_mm=f.length_window_mm,
                sensor_px=int(round(f.length_window_mm / f.mm_per_px)),
                working_distance_mm=a.wd,
                edge_sigma_px=a.edge_sigma,
                distortion_residual_px=a.distortion_px,
                height_sigma_mm=a.height_sigma,
                temp_sigma_k=a.temp_sigma,
                scale_rel_sigma=a.scale_sigma,
            )
            r = length_budget(s_, tol_plus_mm=tol, tol_minus_mm=tol)
            worst_ok = worst_ok or r.passed
            rot = "가로눕힘" if f.rotated else "정방향"
            print(f"{name:6s} {label+'/'+rot:10s} {f.binding:7s} {f.mm_per_px:8.4f} "
                  f"{f.length_window_mm:8.1f} {r.sigma_mm:8.4f} {r.pct_grr:7.0f}  "
                  f"{r.verdict}")
    print()
    print("※ '제한'=width 면 길이 분해능을 다발 폭이 정하고 있다는 뜻 —")
    print("  이때는 끝단만 좁게 보는 2카메라 구성도 소용이 없다(폭이 그대로 남으므로).")
    return 0 if worst_ok else 1


#: 모드별 전제. --optics 는 "보정을 다 했다는 전제에서 거리가 얼마나
#: 필요한가" 를 묻는 모드라, 보정 전 값을 쓰면 답이 통째로 달라진다.
DEFAULTS = {
    "plain":  {"height_sigma": 0.05, "temp_sigma": 3.0},
    "optics": {"height_sigma": 0.02, "temp_sigma": 1.0},
}


def _resolve(a: argparse.Namespace, mode: str) -> argparse.Namespace:
    d = DEFAULTS[mode]
    if a.height_sigma is None:
        a.height_sigma = d["height_sigma"]
    if a.temp_sigma is None:
        a.temp_sigma = d["temp_sigma"]
    return a


def _setup(a: argparse.Namespace) -> OpticalSetup:
    fov = a.fov if a.fov else a.length * 1.15
    return OpticalSetup(
        length_mm=a.length,
        fov_mm=fov,
        sensor_px=SENSORS[a.sensor],
        working_distance_mm=a.wd,
        edge_sigma_px=a.edge_sigma,
        distortion_residual_px=a.distortion_px,
        height_sigma_mm=a.height_sigma,
        temp_sigma_k=a.temp_sigma,
        scale_rel_sigma=a.scale_sigma,
        height_offset_mm=a.height_offset,
        temp_offset_k=a.temp_offset,
        conveyor_speed_mm_s=a.speed,
        rolling_readout_s=a.readout,
    )


def _sweep(a: argparse.Namespace) -> int:
    """같은 공차를 두고 구성을 바꿔가며 어디서 합격선을 넘는지 본다."""
    tol = a.tol
    rows = []
    cases = [
        ("전장 1카메라 / cam3 / 평판", "cam3", a.length * 1.15, 500.0, 0.20, 5.0),
        ("전장 1카메라 / cam3 / V홈", "cam3", a.length * 1.15, 500.0, 0.05, 3.0),
        ("전장 1카메라 / hq / V홈+항온", "hq", a.length * 1.15, 800.0, 0.02, 1.0),
        ("끝단 2카메라 / cam3 / 창 40mm", "cam3", 40.0, 300.0, 0.05, 3.0),
        ("끝단 2카메라 / hq / 창 40mm+항온", "hq", 40.0, 300.0, 0.02, 1.0),
    ]
    for name, sensor, fov, wd, hz, dt in cases:
        two_cam = "2카메라" in name
        s = OpticalSetup(
            # 2카메라는 각 카메라가 '창' 만 보므로 배율 오차도 창 기준이다.
            length_mm=(fov if two_cam else a.length),
            fov_mm=fov,
            sensor_px=SENSORS[sensor],
            working_distance_mm=wd,
            edge_sigma_px=a.edge_sigma,
            distortion_residual_px=a.distortion_px,
            height_sigma_mm=hz,
            temp_sigma_k=dt,
            scale_rel_sigma=a.scale_sigma,
        )
        r = length_budget(s, tol_plus_mm=tol, tol_minus_mm=tol)
        sigma = r.sigma_mm
        note = ""
        if two_cam:
            # 두 카메라 사이 기준거리(베이스라인)의 안정성이 새 지배항이 된다.
            # 강재 프레임 1σ 0.5K 가정: 300mm × 11.7e-6 × 0.5 = 0.0018mm.
            base = a.length * 11.7e-6 * 0.5
            sigma = (sigma**2 + base**2) ** 0.5
            note = f" (+베이스라인 {base:.4f})"
        pct = 6 * sigma / (2 * tol) * 100
        verdict = "GOOD" if pct <= 10 else ("MARGINAL" if pct <= 30 else "FAIL")
        rows.append((name, s.mm_per_px, sigma, pct, verdict, note))

    print(f"제품 {a.length:g}mm, 공차 ±{tol:g}mm (폭 {2*tol:g}mm)")
    print(f"{'구성':34s} {'mm/px':>8s} {'σ(mm)':>8s} {'%GR&R':>7s}  판정")
    print("-" * 74)
    for name, mmpx, sigma, pct, verdict, note in rows:
        print(f"{name:34s} {mmpx:8.4f} {sigma:8.4f} {pct:7.0f}  {verdict}{note}")
    print()
    for sensor in ("cam3", "hq"):
        fov = required_fov_mm(
            tol_plus_mm=tol, tol_minus_mm=tol,
            sensor_px=SENSORS[sensor], edge_sigma_px=a.edge_sigma,
        )
        print(
            f"{sensor}: 끝단검출이 예산의 절반만 쓰려면 길이 방향 시야 ≤ {fov:.0f}mm"
        )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--length", type=float, required=True, help="제품 전장 mm")
    ap.add_argument("--tol", type=float, required=True, help="허용 공차 ±mm")
    ap.add_argument("--sensor", choices=sorted(SENSORS), default="cam3")
    ap.add_argument("--fov", type=float, default=None, help="길이 방향 시야 mm (기본 전장×1.15)")
    ap.add_argument("--wd", type=float, default=500.0, help="작업거리 mm")
    ap.add_argument("--edge-sigma", type=float, default=0.1, help="끝단 검출 1σ px")
    ap.add_argument("--distortion-px", type=float, default=0.3, help="렌즈보정 후 잔차 1σ px")
    ap.add_argument("--height-sigma", type=float, default=None,
                    help="제품 상면 높이 1σ mm (기본: 일반 0.05 / --optics 0.02)")
    ap.add_argument("--temp-sigma", type=float, default=None,
                    help="제품 온도 1σ K (기본: 일반 3.0 / --optics 1.0)")
    ap.add_argument("--scale-sigma", type=float, default=2e-5, help="기준자 상대 불확도 1σ")
    ap.add_argument("--height-offset", type=float, default=0.0, help="보정 대비 높이차 mm(계통)")
    ap.add_argument("--temp-offset", type=float, default=0.0, help="보정 대비 온도차 K(계통)")
    ap.add_argument("--speed", type=float, default=0.0, help="촬영 시 이송속도 mm/s")
    ap.add_argument("--readout", type=float, default=0.0, help="롤링셔터 프레임 읽기 s")
    ap.add_argument("--sweep", action="store_true", help="구성 비교표만 출력")
    ap.add_argument("--screen", action="store_true",
                    help="선별 한계·오검/미검 (공정 산포가 필요)")
    ap.add_argument("--meas-sigma", type=float, default=None,
                    help="측정 1σ(mm). --optics 가 내주는 합성 σ 를 넣는다")
    ap.add_argument("--process-sigmas", type=float, nargs="+",
                    default=[0.02, 0.03, 0.05, 0.08],
                    help="공정 산포 1σ(mm) 후보들")
    ap.add_argument("--max-false-accept", type=float, default=0.001,
                    help="가드밴드가 지켜야 할 미검 상한(비율)")
    ap.add_argument("--optics", action="store_true",
                    help="작업거리·렌즈 선택(카메라를 몇 cm 떨어뜨릴 것인가)")
    ap.add_argument("--edge-rows", type=int, default=200,
                    help="끝단 직선 적합에 쓰일 행 수 ≈ 화면에서의 튜브 OD(px)")
    ap.add_argument("--bundle-width", type=float, default=None,
                    help="다발 폭 mm. 주면 폭/길이가 센서를 나눠 쓰는 효과를 계산")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    a = ap.parse_args(argv)

    if a.screen:
        return _screen(a)
    if a.optics:
        return _optics(a)
    if a.bundle_width:
        return _bundle(_resolve(a, "plain"))
    if a.sweep:
        return _sweep(_resolve(a, "plain"))

    res = length_budget(_setup(_resolve(a, "plain")), tol_plus_mm=a.tol,
                        tol_minus_mm=a.tol)
    md = render_md(res)
    print(md)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    if a.md:
        a.md.parent.mkdir(parents=True, exist_ok=True)
        a.md.write_text(md)
    # 불가 구성을 조용히 넘기지 않는다 — 스크립트에서 걸러낼 수 있게 종료코드로.
    return 0 if res.passed else 1


if __name__ == "__main__":
    sys.exit(main())
