"""MSA 반복성/재현성 산출 CLI (현장·오프라인 실행형) — §5 M3 DoD, §12 인수 산출물.

동일 샘플 1장을 N회(기본 30) 반복 측정해 반복성(EV)/재현성(AV)/%GR&R 를 산출하고
리포트(JSON+MD)를 남긴다. 라즈베리파이 현장에서 커맨드 한 줄로 FAT/SAT 인수
자료(MSA 분석 결과서)를 뽑는 것이 목적이다.

원칙:
  - 완전 오프라인 가능: 이미지 경로 + 기준정보(--ref-length/--tol/--scale)만으로 동작.
  - 기준정보는 API(GET /master/items/{code})에서 읽거나 CLI 인자로 직접 줄 수 있다.
  - 결정적: 동일 입력 → 동일 GR&R(무작위 없음). 임계/보정계수 하드코딩 금지
    (item_master 또는 명시 인자에서만 읽는다).
  - 신규 의존성 금지(OpenCV/argparse 만). debug_length.py 와 동일한 실행 스타일.

실행 예:

    # 이미지 1장 + 명시 기준정보(오프라인, 파이 권장)
    python -m tools.run_msa --item HP12 --image raw.jpg --repeats 30 \
        --ref-length 125.0 --tol-plus 0.5 --tol-minus 0.5 --scale 0.1832 \
        --out /var/lib/aivis/msa

    # SimulatorCamera 로 1프레임 취득(실이미지 없이 검증)
    python -m tools.run_msa --item HP12 --camera sim --repeats 30 \
        --ref-length 125.0 --scale 0.25 --out ./msa_out

    # 기준정보를 API 에서 조회
    python -m tools.run_msa --item HP12 --image raw.jpg --api-url http://api:8000 \
        --out ./msa_out

출력: <out>/msa_<item>.json + msa_<item>.md, 콘솔에 한국어 요약.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

import cv2


def _ensure_vision_importable() -> None:
    """`vision.*` 절대 import 가능하도록 sys.path 를 보강(debug_length 와 동일 전략)."""
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

from aivis_types import ItemMaster  # noqa: E402

from vision.quality.msa import run_msa, write_msa_reports  # noqa: E402

# 데모 시드값(services/api _seed_demo_item 과 동일 — 오프라인 기본값).
DEMO_SCALE = 0.25
DEMO_REF_MM = 125.0


def _load_item_from_api(
    api_url: str, item_code: str, *, service_token: Optional[str], timeout_s: int
) -> Optional[ItemMaster]:
    """GET /master/items/{code} 로 기준정보 조회(인증 폴백 포함). 실패 시 None."""
    from vision.worker.client import ApiClient

    client = ApiClient(api_url, service_token=service_token)
    try:
        return client.fetch_item(item_code, timeout_s=timeout_s)
    finally:
        client.close()


def _build_item_from_args(args: argparse.Namespace) -> ItemMaster:
    """CLI 인자로 오프라인 ItemMaster 구성(파이 오프라인 대비)."""
    ref = DEMO_REF_MM if args.ref_length is None else float(args.ref_length)
    return ItemMaster(
        item_code=args.item,
        item_name=f"{args.item} (msa-cli)",
        ref_length_mm=ref,
        tol_plus_mm=float(args.tol_plus),
        tol_minus_mm=float(args.tol_minus),
        px_to_mm_scale=float(args.scale),
    )


def _resolve_item(args: argparse.Namespace) -> Optional[ItemMaster]:
    """기준정보 확보. 우선순위: 명시 인자(--ref-length) > API(--api-url).

    - --ref-length 가 주어지면 오프라인 인자로 구성(현장 권장 — API 불필요).
    - 아니면 --api-url 에서 조회. 둘 다 없으면 None(호출자 오류 처리).
    """
    if args.ref_length is not None:
        return _build_item_from_args(args)
    if args.api_url:
        item = _load_item_from_api(
            args.api_url,
            args.item,
            service_token=args.service_token,
            timeout_s=args.api_timeout,
        )
        if item is None:
            print(
                f"오류: API({args.api_url})에서 품목 {args.item} 기준정보를 "
                "확보하지 못했습니다. --ref-length/--scale 로 직접 주세요.",
                file=sys.stderr,
            )
        return item
    print(
        "오류: 기준정보가 없습니다. --ref-length(+--scale) 로 직접 주거나 "
        "--api-url 로 조회하세요.",
        file=sys.stderr,
    )
    return None


def _load_image(args: argparse.Namespace):
    """측정 대상 프레임 1장 확보. --image 우선, 없으면 --camera sim 로 1프레임."""
    if args.image:
        img = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
        if img is None:
            print(f"오류: 이미지를 읽을 수 없습니다: {args.image}", file=sys.stderr)
            return None
        return img
    if args.camera == "sim":
        # SimulatorCamera 로 데이터셋 1프레임(없으면 합성 데이터셋 자동 생성).
        os.environ["AIVIS_CAMERA"] = "sim"
        from vision.acquisition import AcquisitionService, create_camera
        from vision.worker.dataset import ensure_dataset

        ds = ensure_dataset(args.dataset_dir)
        cam = create_camera(dataset_dir=ds, view_filter=args.view)
        try:
            grab = AcquisitionService(camera=cam).grab_with_retry()
        finally:
            cam.close()
        if not grab.ok:
            print(f"오류: 시뮬레이터 프레임 취득 실패: {grab.error}", file=sys.stderr)
            return None
        return grab.frame
    print(
        "오류: 측정 대상이 없습니다. --image <경로> 또는 --camera sim 을 주세요.",
        file=sys.stderr,
    )
    return None


def _format_summary(item_code: str, res, paths: dict) -> str:
    """콘솔 한국어 요약(결정적)."""
    verdict = "합격(PASS)" if res.passed else "불합격(FAIL)"
    lines = [
        "=== AIVIS MSA 반복성/재현성 결과 ===",
        f"품목: {item_code}   반복: {res.repeats}회 × {res.appraisers}조건",
        f"반복성(EV) σ: {res.repeatability_std_mm:.6f} mm",
        f"재현성(AV) σ: {res.reproducibility_std_mm:.6f} mm",
        f"GR&R σ: {res.grr_std_mm:.6f} mm   공차: {res.tolerance_mm:.4f} mm",
        f"%GR&R(공차 대비): {res.pct_grr_tolerance:.4f}%  (임계 ≤30%)",
        f"측정 범위(max-min): {res.range_mm:.6f} mm",
        f"판정: {verdict}",
        f"리포트: {paths['json']}",
        f"        {paths['md']}",
    ]
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run_msa",
        description=(
            "AIVIS MSA 반복성/재현성 산출 CLI — 동일 샘플 N회 반복 측정으로 "
            "EV/AV/%GR&R 를 계산하고 JSON+MD 리포트를 남긴다(오프라인 가능)."
        ),
    )
    ap.add_argument("--item", default="HP12", help="품목 코드(리포트/조회 키)")
    ap.add_argument("--image", default=None, help="측정 대상 이미지 경로")
    ap.add_argument(
        "--camera", default=None, choices=["sim"],
        help="이미지 대신 카메라로 1프레임 취득(현재 sim 만)",
    )
    ap.add_argument("--repeats", type=int, default=30, help="반복 측정 횟수(기본 30)")
    ap.add_argument(
        "--out", default=".", help="리포트 출력 폴더(기본 현재 디렉터리)"
    )
    # 오프라인 기준정보(명시 시 API 조회를 건너뛴다).
    ap.add_argument("--ref-length", type=float, default=None, help="기준 길이(mm)")
    ap.add_argument("--tol-plus", type=float, default=0.5, help="허용 공차 +(mm)")
    ap.add_argument("--tol-minus", type=float, default=0.5, help="허용 공차 -(mm)")
    ap.add_argument(
        "--scale", type=float, default=DEMO_SCALE, help="px_to_mm_scale(보정계수)"
    )
    ap.add_argument(
        "--grr-pct-max", type=float, default=30.0, help="%GR&R 합격 상한(기본 30)"
    )
    # API 조회(선택).
    ap.add_argument("--api-url", default=None, help="기준정보 조회 API base URL")
    ap.add_argument("--service-token", default=None, help="API 서비스 토큰(선택)")
    ap.add_argument("--api-timeout", type=int, default=10, help="API 조회 타임아웃(s)")
    # 시뮬레이터 옵션.
    ap.add_argument("--dataset-dir", default=None, help="시뮬레이터 데이터셋 폴더")
    ap.add_argument("--view", default=None, help="시뮬레이터 view 필터(SIDE/END)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    item = _resolve_item(args)
    if item is None:
        return 2

    img = _load_image(args)
    if img is None:
        return 2

    repeats = max(1, int(args.repeats))
    res = run_msa(
        img,
        item,
        repeats=repeats,
        appraiser_insets=[0.05, 0.06, 0.07],
        grr_pct_max=float(args.grr_pct_max),
    )
    source = (
        f"image={args.image}" if args.image else f"camera={args.camera}"
    )
    paths = write_msa_reports(args.out, item.item_code, res, source=source)
    print(_format_summary(item.item_code, res, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
