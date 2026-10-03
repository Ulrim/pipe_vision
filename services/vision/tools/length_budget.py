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

from vision.quality.budget import (
    OpticalSetup,
    fit_frame,
    length_budget,
    render_md,
    required_fov_mm,
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
    ap.add_argument("--height-sigma", type=float, default=0.05, help="제품 상면 높이 1σ mm")
    ap.add_argument("--temp-sigma", type=float, default=3.0, help="제품 온도 1σ K")
    ap.add_argument("--scale-sigma", type=float, default=2e-5, help="기준자 상대 불확도 1σ")
    ap.add_argument("--height-offset", type=float, default=0.0, help="보정 대비 높이차 mm(계통)")
    ap.add_argument("--temp-offset", type=float, default=0.0, help="보정 대비 온도차 K(계통)")
    ap.add_argument("--speed", type=float, default=0.0, help="촬영 시 이송속도 mm/s")
    ap.add_argument("--readout", type=float, default=0.0, help="롤링셔터 프레임 읽기 s")
    ap.add_argument("--sweep", action="store_true", help="구성 비교표만 출력")
    ap.add_argument("--bundle-width", type=float, default=None,
                    help="다발 폭 mm. 주면 폭/길이가 센서를 나눠 쓰는 효과를 계산")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    a = ap.parse_args(argv)

    if a.bundle_width:
        return _bundle(a)
    if a.sweep:
        return _sweep(a)

    res = length_budget(_setup(a), tol_plus_mm=a.tol, tol_minus_mm=a.tol)
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
