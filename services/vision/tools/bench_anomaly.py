"""이상탐지 처리속도 실측 — **검사 장비에서 직접 돌린다** (§1.2 300ms/ea).

**왜 이 도구가 필요한가**: 패치 격자를 키우면 작은 결함(스크래치) 탐지력이 크게
오르지만 연산도 함께 는다. 어느 격자까지 쓸 수 있는지는 **그 장비에서 재봐야**
안다. 개발 PC 수치에 배수를 곱해 추정하면 틀린다 — 파이4는 코어 수·메모리
대역폭·부동소수 성능이 전부 달라서 배수가 연산 종류마다 다르다.

그래서 이 도구는 라즈베리파이에서 직접 실행하도록 만들었다. 예산 안에 들어가는
가장 큰 격자를 골라 준다.

사용(파이에서):
    python -m vision.tools.bench_anomaly --image /var/lib/aivis/images/raw/<한장>.jpg
    python -m vision.tools.bench_anomaly --size 1600x400      # 이미지 없이 합성
    python -m vision.tools.bench_anomaly --budget-ms 200      # 여유를 두고 싶을 때

예산은 이상탐지 **단독** 기준이다. 길이 측정·고전 CV·저장이 같은 300ms 안에서
함께 돌아가므로, 전체 예산을 그대로 넣지 말고 이상탐지 몫만 넣는다(기본 120ms).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.surface.anomaly import patch_descriptors  # noqa: E402

DEFAULT_GRIDS = (1, 2, 4, 6, 8)
#: 이상탐지에 할당할 기본 예산(ms). 300ms 전체 중 나머지 단계 몫을 남긴 값.
DEFAULT_BUDGET_MS = 120.0


def _synth(w: int, h: int) -> np.ndarray:
    """합성 표면(결정적). 실제 촬영본이 없을 때 크기별 비용만 보기 위함."""
    rng = np.random.default_rng(12345)
    g = rng.integers(100, 150, size=(h, w), dtype=np.int16).astype(np.uint8)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def bench(
    image: np.ndarray,
    *,
    grids: Sequence[int] = DEFAULT_GRIDS,
    repeat: int = 5,
) -> list[dict]:
    """격자별 1회 소요시간(ms) 측정. 중앙값을 쓴다(첫 회 캐시 효과 배제)."""
    rows: list[dict] = []
    for g in grids:
        patch_descriptors(image, None, grid=g)  # 워밍업
        times: list[float] = []
        for _ in range(max(1, repeat)):
            t0 = time.perf_counter()
            out = patch_descriptors(image, None, grid=g)
            times.append((time.perf_counter() - t0) * 1000.0)
        times.sort()
        rows.append(
            {
                "grid": int(g),
                "patches": int(out.shape[0]),
                "ms_median": round(times[len(times) // 2], 2),
                "ms_min": round(times[0], 2),
                "ms_max": round(times[-1], 2),
            }
        )
    return rows


def recommend(rows: Sequence[dict], budget_ms: float) -> Optional[int]:
    """예산 안에 들어가는 **가장 큰** 격자. 전부 초과면 None."""
    ok = [r for r in rows if r["ms_median"] <= budget_ms]
    return max((r["grid"] for r in ok), default=None)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench_anomaly", description="이상탐지 처리속도 실측(장비에서 실행)"
    )
    ap.add_argument("--image", default=None, help="실제 촬영본 경로(권장)")
    ap.add_argument("--size", default="1600x400", help="이미지 없을 때 합성 크기 WxH")
    ap.add_argument("--budget-ms", type=float, default=DEFAULT_BUDGET_MS)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", default=None, help="결과 JSON 저장 경로")
    args = ap.parse_args(argv)

    if args.image:
        img = cv2.imread(args.image, cv2.IMREAD_COLOR)
        if img is None:
            print(f"이미지를 읽지 못했습니다: {args.image}", file=sys.stderr)
            return 2
        src = f"{args.image} ({img.shape[1]}x{img.shape[0]})"
    else:
        try:
            w, h = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            print(f"--size 형식은 WxH 입니다: {args.size}", file=sys.stderr)
            return 2
        img = _synth(w, h)
        src = f"합성 {w}x{h}"

    rows = bench(img, repeat=args.repeat)
    best = recommend(rows, args.budget_ms)

    print(f"대상: {src}")
    print(f"이상탐지 예산: {args.budget_ms:.0f}ms (전체 300ms 중 이상탐지 몫)")
    print()
    print(f"{'격자':>6} {'패치':>5} {'중앙값(ms)':>11} {'판정':>6}")
    for r in rows:
        mark = "OK" if r["ms_median"] <= args.budget_ms else "초과"
        print(
            f"{r['grid']:>3}x{r['grid']:<2} {r['patches']:>5} "
            f"{r['ms_median']:>11.1f} {mark:>6}"
        )
    print()
    if best is None:
        print("권장: 이 크기에서는 어떤 격자도 예산에 못 들어갑니다.")
        print("      표면 ROI 를 줄이거나(촬영 구도·크롭) 예산을 재협의하세요.")
    else:
        print(f"권장 격자: {best}x{best}")
        print(f"  설정: 학습 시 --grid {best} (모델 파일에 기록되어 추론이 따라갑니다)")
        if best == 1:
            print("  주의: 격자 1은 표면 전체를 한 벡터로 봅니다. 작은 스크래치는")
            print("        통계에 묻혀 놓칠 수 있습니다(DAGM 실측: AUROC 0.76 vs 1.00).")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "source": src,
                    "budget_ms": args.budget_ms,
                    "rows": rows,
                    "recommended_grid": best,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
