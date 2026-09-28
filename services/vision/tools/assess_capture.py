"""촬영본 전수 판독 — 이 사진들로 검사가 되는가? (부록 A.3 촬영 조건 표준)

**왜 필요한가**: 현장에서 사진을 수백~수천 장 받았을 때, 눈으로 넘겨보는 것만으로는
"학습·검증에 쓸 수 있는 데이터인지"를 판단할 수 없다. 반사로 날아간 화소, 미세한
흔들림, 한 프레임에 여러 개가 들어간 구도는 몇 장 넘기다 보면 놓친다. 그런데 이걸
놓치고 수집을 계속하면, 나중에 정확도가 안 나올 때 원인이 모델인지 데이터인지
가릴 수 없게 된다.

그래서 **전수를 기계로 재고, 문제가 있는 것만 사람이 본다.**

측정 항목과 판단 근거:
- 해상도            : 너무 작으면 서브픽셀 끝단 검출이 무의미해진다.
- 흔들림(라플라시안 분산): 낮으면 끝단이 뭉개져 길이 오차가 커진다.
- 하이라이트 포화   : 금속 반사로 255 에 붙은 화소. 그 영역의 표면 정보는 이미
                      사라진 것이라 유분기·변색을 판정할 수 없다(A.3 "반사
                      하이라이트 포화 회피").
- 배경 균일도       : 배경이 지저분하면 ROI 분리가 제품이 아닌 것을 잡는다.
- 전경 덩어리 수    : 2개 이상이면 다발 촬영이다(A.3 "1프레임 1개", 다객체는
                      개별 판정·길이 측정 불가).
- ROI 검출          : 실제 전처리 모듈이 제품 영역을 찾아내는가.
- 끝단 검출         : 실제 길이 모듈이 양 끝단을 잡아내는가. **이게 길이 측정의
                      가부를 가른다.** 여기서 실패하면 그 사진으로는 길이를 못 잰다.
- 잘림              : 제품이 프레임 가장자리에 닿으면 전장이 안 들어온 것이다.

판정은 통과/경고/불가 3단계다. "불가"만 골라내면 재촬영 목록이 된다.

사용:
    python -m vision.tools.assess_capture --dir <폴더> [--out report.json] [--md report.md]

주의: 이 도구는 **촬영 품질**만 본다. 제품이 양품인지 불량인지는 판단하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import ItemMaster  # noqa: E402

from vision.length.measure import measure_length_ex  # noqa: E402
from vision.preprocess import preprocess  # noqa: E402

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# --- 임계값 --------------------------------------------------------------
# 현장 광학이 확정되기 전의 1차 선별 기준이다. 확정 후에는 실측에 맞춰 조정한다.
MIN_WIDTH_PX = 640          # 이보다 작으면 서브픽셀 측정이 의미 없다
WARN_WIDTH_PX = 1280
BLUR_FAIL = 40.0            # 라플라시안 분산. 낮을수록 흐리다
BLUR_WARN = 120.0
SATURATED_FAIL_PCT = 5.0    # 255 에 붙은 화소 비율
SATURATED_WARN_PCT = 1.0
DARK_FAIL_PCT = 40.0        # 0 에 붙은 화소 비율(노출 부족)
BG_STD_WARN = 45.0          # 배경 표준편차(지저분한 배경)
EDGE_TOUCH_PX = 3           # 프레임 가장자리 접촉 허용 여유


@dataclass
class ImageAssessment:
    """사진 1장 판독 결과."""

    path: str
    width: Optional[int] = None
    height: Optional[int] = None
    blur_var: Optional[float] = None
    saturated_pct: Optional[float] = None
    dark_pct: Optional[float] = None
    bg_std: Optional[float] = None
    blob_count: Optional[int] = None
    roi_found: bool = False
    ends_found: bool = False
    pipe_len_px: Optional[float] = None
    touches_edge: bool = False
    verdict: str = "불가"           # 통과 | 경고 | 불가
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _probe_item() -> ItemMaster:
    """끝단 검출 가부만 보기 위한 임시 기준정보.

    길이 **값**은 여기서 판단하지 않는다(캘리브레이션 전이라 mm 환산이 무의미).
    보는 것은 오직 "양 끝단이 잡히는가" 뿐이므로 scale 은 1.0 을 둔다.
    """
    return ItemMaster(
        item_code="PROBE",
        item_name="capture probe",
        ref_length_mm=0.0,
        tol_plus_mm=10_000.0,       # 공차 판정은 쓰지 않으므로 넓게
        tol_minus_mm=10_000.0,
        px_to_mm_scale=1.0,
    )


def _blob_count(gray: np.ndarray) -> int:
    """프레임 안의 제품 덩어리 수 — 다발 촬영(A.3 "1프레임 1개") 검출용.

    **전처리 마스크로 세면 안 된다.** preprocess 는 여러 개가 찍혀 있어도 그중
    하나만 골라 마스크를 만든다(측정 대상 선택이 그 모듈의 일이다). 그 마스크를
    세면 항상 1이 나와, 정작 잡아야 할 다발 사진이 통과해 버린다. 그래서 여기서는
    원본 그레이에서 독립적으로 전경을 잡는다.
    """
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _th, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 가는 틈으로 갈라진 한 덩어리가 여러 개로 세지지 않게 살짝 메운다.
    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    )
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return 0
    areas = sorted(stats[1:, cv2.CC_STAT_AREA], reverse=True)
    frame_area = gray.shape[0] * gray.shape[1]
    # "제품"으로 칠 최소 크기: 최대 덩어리의 20% 이상이면서 프레임의 0.5% 이상.
    # 두 조건을 함께 걸어 먼지·반사점과 배경 얼룩을 모두 제외한다.
    cutoff = max(areas[0] * 0.2, frame_area * 0.005)
    return sum(1 for a in areas if a >= cutoff)


def assess_image(path: str | Path) -> ImageAssessment:
    """사진 1장을 실제 파이프라인에 통과시켜 촬영 품질을 판독한다."""
    p = Path(path)
    out = ImageAssessment(path=str(p))
    # 판독 도중 발생한 오류. _judge 가 사유 목록을 새로 쓰므로 따로 모았다가 합친다
    # (예전에 여기서 예외 메시지가 조용히 지워져 원인을 못 찾은 적이 있다).
    errors: list[str] = []
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        out.verdict = "불가"
        out.reasons = ["이미지를 읽을 수 없음(손상 또는 미지원 형식)"]
        return out

    h, w = img.shape[:2]
    out.width, out.height = int(w), int(h)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    out.blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    out.saturated_pct = float((gray >= 250).sum() / gray.size * 100.0)
    out.dark_pct = float((gray <= 5).sum() / gray.size * 100.0)

    # 실제 전처리·길이 모듈을 그대로 태운다 — 별도 구현을 두면 판독과 운영이 어긋난다.
    try:
        pre = preprocess(img)
        out.roi_found = bool(pre.found)
        out.blob_count = _blob_count(gray)
        # 배경 = 마스크 밖. 여기가 고르지 않으면 ROI 가 엉뚱한 것을 잡는다.
        bg = pre.gray_corrected[pre.mask == 0]
        out.bg_std = float(bg.std()) if bg.size else None

        if pre.found and pre.length_roi is not None:
            r = pre.length_roi
            out.touches_edge = (
                r.x0 <= EDGE_TOUCH_PX
                or r.y0 <= EDGE_TOUCH_PX
                or r.x1 >= w - EDGE_TOUCH_PX
                or r.y1 >= h - EDGE_TOUCH_PX
            )
            # 운영 파이프라인과 같은 방식으로 자른다(pipeline.py 참조).
            _res, ends = measure_length_ex(r.crop(pre.gray_corrected), _probe_item())
            if ends is not None:
                out.ends_found = True
                out.pipe_len_px = float(abs(ends.right_x - ends.left_x))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"파이프라인 예외: {type(exc).__name__}: {exc}")

    _judge(out, errors)
    return out


def _judge(a: ImageAssessment, errors: list[str] | None = None) -> None:
    """측정값 → 통과/경고/불가. 사유를 사람이 읽을 문장으로 남긴다."""
    fail: list[str] = list(errors or [])
    warn: list[str] = []

    if a.width and a.width < MIN_WIDTH_PX:
        fail.append(f"해상도 부족({a.width}px) — 서브픽셀 끝단 측정 불가")
    elif a.width and a.width < WARN_WIDTH_PX:
        warn.append(f"해상도 낮음({a.width}px)")

    if a.blur_var is not None:
        if a.blur_var < BLUR_FAIL:
            fail.append(f"흔들림/초점 이탈(선명도 {a.blur_var:.0f}) — 끝단이 뭉개짐")
        elif a.blur_var < BLUR_WARN:
            warn.append(f"선명도 낮음({a.blur_var:.0f})")

    if a.saturated_pct is not None:
        if a.saturated_pct >= SATURATED_FAIL_PCT:
            fail.append(
                f"반사 포화 {a.saturated_pct:.1f}% — 그 영역은 표면 정보가 소실돼 "
                "유분기·변색 판정 불가"
            )
        elif a.saturated_pct >= SATURATED_WARN_PCT:
            warn.append(f"반사 포화 {a.saturated_pct:.1f}%")

    if a.dark_pct is not None and a.dark_pct >= DARK_FAIL_PCT:
        fail.append(f"노출 부족(암부 {a.dark_pct:.0f}%)")

    if not a.roi_found:
        fail.append("제품 영역(ROI) 미검출 — 배경/조명 대비 부족")
    if a.blob_count and a.blob_count >= 2:
        fail.append(f"한 프레임에 {a.blob_count}개 — 1프레임 1개 규격 위반(A.3)")
    if a.roi_found and not a.ends_found:
        fail.append("양 끝단 미검출 — 이 사진으로는 길이를 잴 수 없음")
    if a.touches_edge:
        warn.append("제품이 프레임 가장자리에 닿음 — 전장이 안 들어왔을 수 있음")
    if a.bg_std is not None and a.bg_std > BG_STD_WARN:
        warn.append(f"배경이 균일하지 않음(표준편차 {a.bg_std:.0f})")

    a.reasons = fail + warn
    a.verdict = "불가" if fail else ("경고" if warn else "통과")


def assess_dir(directory: str | Path) -> list[ImageAssessment]:
    """폴더 전체(하위 포함)를 판독한다. 파일명 순으로 결정적."""
    root = Path(directory)
    files = sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _IMG_EXTS
    )
    return [assess_image(p) for p in files]


def summarize(items: list[ImageAssessment]) -> dict[str, Any]:
    """집계 + 자주 걸린 사유 순위. 무엇부터 고쳐야 하는지 보이게 한다."""
    counts = {"통과": 0, "경고": 0, "불가": 0}
    reason_hits: dict[str, int] = {}
    for it in items:
        counts[it.verdict] = counts.get(it.verdict, 0) + 1
        for r in it.reasons:
            key = r.split("(")[0].split(" —")[0].strip()
            reason_hits[key] = reason_hits.get(key, 0) + 1
    usable = counts["통과"] + counts["경고"]
    return {
        "total": len(items),
        "verdicts": counts,
        "usable_pct": round(usable / len(items) * 100.0, 1) if items else 0.0,
        "length_measurable": sum(1 for i in items if i.ends_found),
        "top_reasons": sorted(reason_hits.items(), key=lambda kv: -kv[1])[:10],
    }


def to_markdown(items: list[ImageAssessment], summary: dict[str, Any]) -> str:
    """사람이 읽는 보고서. 문제 있는 것만 나열한다(통과분은 셈만)."""
    lines = [
        "# 촬영본 전수 판독 결과",
        "",
        f"- 총 {summary['total']}장 — "
        f"통과 {summary['verdicts']['통과']} / 경고 {summary['verdicts']['경고']} / "
        f"불가 {summary['verdicts']['불가']}",
        f"- 학습·검증에 쓸 수 있는 비율: **{summary['usable_pct']}%**",
        f"- 길이 측정이 가능한 장수: {summary['length_measurable']}",
        "",
        "> 촬영 품질만 본 결과입니다. 제품의 양품/불량 판정이 아닙니다.",
        "",
    ]
    if summary["top_reasons"]:
        lines += ["## 많이 걸린 사유", ""]
        lines += [f"- {k} — {v}장" for k, v in summary["top_reasons"]]
        lines.append("")

    bad = [i for i in items if i.verdict != "통과"]
    if bad:
        lines += ["## 조치가 필요한 사진", "", "| 파일 | 판정 | 사유 |", "|---|---|---|"]
        for i in bad:
            lines.append(
                f"| {Path(i.path).name} | {i.verdict} | {'; '.join(i.reasons)} |"
            )
    else:
        lines.append("모든 사진이 촬영 표준을 만족합니다.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="assess_capture", description="촬영본 전수 판독(부록 A.3)"
    )
    ap.add_argument("--dir", required=True, help="이미지 폴더(하위 포함)")
    ap.add_argument("--out", default=None, help="JSON 결과 경로")
    ap.add_argument("--md", default=None, help="마크다운 보고서 경로")
    args = ap.parse_args(argv)

    items = assess_dir(args.dir)
    if not items:
        print(f"이미지가 없습니다: {args.dir}", file=sys.stderr)
        return 2
    summary = summarize(items)

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {"summary": summary, "items": [i.as_dict() for i in items]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if args.md:
        Path(args.md).write_text(to_markdown(items, summary), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
