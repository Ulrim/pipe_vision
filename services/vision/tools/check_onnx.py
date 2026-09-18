"""GitHub 공개 ONNX 모델 현장 검증 CLI (§6.3, docs/OPEN_MODELS.md).

받아온 모델(.onnx + 사이드카 <모델>.json)이 라즈베리파이(ARM CPU)에서
쓸 만한지 배포 전에 확인하는 게이트다. 사이드카 계약 요약, 세션 로드,
1장 추론 점수, 반복 지연(p50/p95)과 300ms KPI 합격 여부를 한국어로 출력한다.

실행 예(services/vision 디렉터리에서):

    # 실이미지 1장으로 검증
    python -m tools.check_onnx --model models/surface.onnx --image sample.jpg

    # 이미지 없이 합성 이미지로 지연만 확인, 기준정보는 API 에서
    python -m tools.check_onnx --model m.onnx --repeats 50 \
        --item HP12 --api-url http://api:8000

종료코드: 0=정상, 1=로드 실패(사이드카/세션), 2=p95 가 예산(기본 300ms) 초과.
임계값은 item_master(API) 또는 사이드카에서 읽는다(하드코딩 금지 — item
미지정 시 기본 임계 0.5 로 '판정 표시만' 한다).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Optional, Sequence


def _ensure_vision_importable() -> None:
    """`vision.*` 절대 import 가능하도록 sys.path 를 보강(run_msa 와 동일 전략)."""
    import importlib.util

    here = Path(__file__).resolve()
    vision_root = here.parents[1]      # services/vision
    services_root = here.parents[2]    # services
    if str(services_root) not in sys.path:
        sys.path.insert(0, str(services_root))
    if importlib.util.find_spec("vision") is None:
        import importlib.machinery

        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec("vision", loader=None, is_package=True)
        )
        pkg.__path__ = [str(vision_root)]  # type: ignore[attr-defined]
        sys.modules.setdefault("vision", pkg)


_ensure_vision_importable()

import cv2  # noqa: E402
from aivis_types import ItemMaster  # noqa: E402

from vision.surface.model import OnnxSurfaceModel  # noqa: E402
from vision.surface.onnx_meta import sidecar_path  # noqa: E402


def _load_item(args: argparse.Namespace) -> ItemMaster:
    """기준정보 확보: --api-url 이 있으면 조회, 아니면 기본 임계(0.5) 표시용."""
    if args.item and args.api_url:
        from vision.worker.client import ApiClient

        client = ApiClient(args.api_url, service_token=args.service_token)
        try:
            item = client.fetch_item(args.item, timeout_s=args.api_timeout)
        finally:
            client.close()
        if item is not None:
            return item
        print(
            f"경고: API({args.api_url})에서 품목 {args.item} 기준정보를 "
            "확보하지 못했습니다 — 기본 임계 0.5 로 판정 표시만 합니다.",
            file=sys.stderr,
        )
    code = args.item or "CHECK"
    # 임계 None → 판정 시 보수적 기본 0.5(표시용). 길이 필드는 미사용 더미.
    return ItemMaster(
        item_code=code,
        item_name=f"{code} (check-onnx)",
        ref_length_mm=100.0,
        tol_plus_mm=1.0,
        tol_minus_mm=1.0,
        px_to_mm_scale=1.0,
    )


def _load_image(args: argparse.Namespace):
    """검증 이미지 1장. --image 우선, 없으면 결정적 합성 이미지."""
    if args.image:
        img = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if img is None:
            print(f"오류: 이미지를 읽을 수 없습니다: {args.image}", file=sys.stderr)
            return None
        return img
    from vision.tools.gen_synthetic import make_image

    img, _ = make_image("OK")
    print("이미지 미지정 → 합성 이미지(OK)로 검증합니다.")
    return img


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="ONNX 표면 모델 현장 검증(로드/점수/지연 p50·p95/300ms 게이트)"
    )
    ap.add_argument("--model", required=True, help="ONNX 모델 경로(.onnx)")
    ap.add_argument("--image", help="검증 이미지(미지정 시 합성 이미지)")
    ap.add_argument("--repeats", type=int, default=50, help="반복 횟수(기본 50)")
    ap.add_argument("--item", help="품목 코드(임계 조회용)")
    ap.add_argument("--api-url", help="기준정보 API URL(GET /master/items)")
    ap.add_argument("--service-token", help="API 서비스 토큰(선택)")
    ap.add_argument("--api-timeout", type=int, default=5)
    ap.add_argument(
        "--budget-ms", type=float, default=300.0,
        help="처리속도 예산 ms(기본 300 — §1.2 KPI)",
    )
    args = ap.parse_args(argv)

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"오류: 모델 파일이 없습니다: {model_path}", file=sys.stderr)
        return 1

    print(f"[1/4] 모델 로드: {model_path}")
    sc = sidecar_path(model_path)
    print(f"      사이드카: {sc} ({'있음' if sc.exists() else '없음 — 자동 인식 시도'})")
    model = OnnxSurfaceModel(str(model_path))
    if not model.loaded:
        print(f"오류: 모델 로드 실패 — {model._load_error}", file=sys.stderr)
        return 1
    meta = model.meta
    assert meta is not None
    print(
        f"      계약 요약: kind={meta.kind}, input={meta.input_size} "
        f"{meta.layout}/{meta.color}, scale={meta.scale:.6g}, "
        f"mean={list(meta.mean)}, std={list(meta.std)}, "
        f"sigmoid={meta.apply_sigmoid}"
    )
    if meta.kind == "anomaly":
        print(
            f"      anomaly: threshold={meta.anomaly_threshold}, "
            f"force_ng={meta.anomaly_force_ng}"
        )

    img = _load_image(args)
    if img is None:
        return 1
    item = _load_item(args)

    print("[2/4] 1장 추론:")
    res = model.predict(img, item)
    print(
        f"      oil={res.oil_score} discolor={res.discolor_score} "
        f"scratch={res.scratch_score} verdict={res.surface_verdict} "
        f"codes={list(res.defect_codes)}"
    )
    rep = model.last_report
    if rep is not None:
        print(
            f"      anomaly score={rep.score} (raw={rep.distance}, "
            f"threshold={rep.threshold}) review={rep.review_flag}"
        )

    repeats = max(1, int(args.repeats))
    print(f"[3/4] 지연 측정({repeats}회 반복):")
    lat_ms = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        model.predict(img, item)
        lat_ms.append((time.perf_counter() - t0) * 1000.0)
    lat_sorted = sorted(lat_ms)
    p50 = statistics.median(lat_sorted)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(round(0.95 * len(lat_sorted))) - 1)]
    print(f"      p50={p50:.1f}ms  p95={p95:.1f}ms  max={lat_sorted[-1]:.1f}ms")

    budget = float(args.budget_ms)
    print(f"[4/4] 판정(예산 {budget:.0f}ms):")
    if p95 > budget:
        print(
            f"      불합격 — p95 {p95:.1f}ms 가 예산 {budget:.0f}ms 를 "
            "초과합니다. 더 작은 모델/입력 크기 축소/INT8 양자화를 검토하세요."
        )
        return 2
    print(f"      합격 — p95 {p95:.1f}ms ≤ {budget:.0f}ms. 배포 가능합니다.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
