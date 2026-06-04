"""합성 더미 이미지 생성 유틸 (테스트 자립용) — CLAUDE.md 부록 A 참고.

실제 데이터셋이 없어도 SimulatorCamera 와 전 파이프라인을 검증할 수 있도록
파이프 형상을 가진 결정적 합성 이미지를 생성한다.

- SIDE 구도: 무광 배경 위에 수평 파이프(밝은 직사각형 막대). 길이는
  pipe_len_px 로 제어 → length 모듈이 끝단을 검출해 px 거리를 측정할 수 있다.
- 결함 합성:
    * LEN: pipe_len_px 를 기준보다 늘리거나 줄임.
    * SCR: 파이프 표면에 어두운/밝은 선형 흠집(사광 가정).
    * OIL: 파이프 표면 일부에 하이라이트 얼룩(반사 포화 패치).
    * DIS: 파이프 표면 일부 색조(주황/갈색) 변조.

파일명은 부록 A.4 규칙을 따른다:
  {품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{일련}.jpg

모든 생성은 시드 고정으로 결정적이다.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

# 합성 기준 캔버스(테스트가 의존하는 고정 지오메트리)
DEFAULT_W = 800
DEFAULT_H = 300
# 기준 파이프 길이(px). length 테스트가 px_to_mm_scale 로 mm 환산.
DEFAULT_PIPE_LEN_PX = 500
DEFAULT_PIPE_THICK_PX = 90
BG_GRAY = 30          # 무광 어두운 배경
PIPE_GRAY = 185       # 알루미늄 표면 밝기


def _base_canvas(w: int, h: int) -> np.ndarray:
    img = np.full((h, w, 3), BG_GRAY, dtype=np.uint8)
    # 약한 균일 노이즈(결정적 시드)로 완전 평탄 회피
    rng = np.random.default_rng(12345)
    noise = rng.integers(-4, 5, size=(h, w, 1), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def _pipe_bbox(
    w: int, h: int, pipe_len_px: int, thick_px: int
) -> Tuple[int, int, int, int]:
    """파이프 직사각형 (x0,y0,x1,y1). 캔버스 중앙 수평 배치."""
    x0 = (w - pipe_len_px) // 2
    x1 = x0 + pipe_len_px
    y0 = (h - thick_px) // 2
    y1 = y0 + thick_px
    return x0, y0, x1, y1


def make_image(
    cls: str = "OK",
    *,
    w: int = DEFAULT_W,
    h: int = DEFAULT_H,
    pipe_len_px: int = DEFAULT_PIPE_LEN_PX,
    thick_px: int = DEFAULT_PIPE_THICK_PX,
    seed: int = 0,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """클래스별 합성 이미지를 만든다. (이미지, 파이프 bbox) 반환.

    cls: OK / LEN_PLUS / LEN_MINUS / SCR / OIL / DIS / MULTI
    """
    cls = cls.upper()
    rng = np.random.default_rng(seed + 777)

    # LEN 결함은 길이 자체를 바꾼다.
    eff_len = pipe_len_px
    if cls in ("LEN", "LEN_PLUS"):
        eff_len = pipe_len_px + 80
    elif cls == "LEN_MINUS":
        eff_len = pipe_len_px - 80

    img = _base_canvas(w, h)
    x0, y0, x1, y1 = _pipe_bbox(w, h, eff_len, thick_px)

    # 파이프 본체: 곡면 음영(중앙 밝고 가장자리 어둡게) — 금속 곡면 모사
    yy = np.arange(y0, y1)
    shade = (np.cos((yy - (y0 + y1) / 2) / (thick_px / 2) * (np.pi / 2)) ** 0.5)
    shade = np.clip(shade, 0.45, 1.0)
    body = (PIPE_GRAY * shade).astype(np.uint8)
    for i, yv in enumerate(yy):
        img[yv, x0:x1, :] = body[i]

    # 끝단을 또렷하게(서브픽셀 에지 검출 대상)
    cv2.line(img, (x0, y0), (x0, y1 - 1), (210, 210, 210), 1)
    cv2.line(img, (x1 - 1, y0), (x1 - 1, y1 - 1), (210, 210, 210), 1)

    # --- 표면 결함 합성 ---
    if cls in ("SCR", "MULTI"):
        # 선형 스크래치: 표면을 가로지르는 밝은 선 + 어두운 그림자(사광)
        sy = (y0 + y1) // 2 - 10
        sx0 = x0 + 60
        sx1 = x0 + 60 + 160
        cv2.line(img, (sx0, sy), (sx1, sy + 6), (245, 245, 245), 2)
        cv2.line(img, (sx0, sy + 2), (sx1, sy + 8), (60, 60, 60), 1)

    if cls in ("OIL", "MULTI"):
        # 유분기: 반사 하이라이트 포화 패치(밝은 얼룩)
        cx, cy = x1 - 120, (y0 + y1) // 2 + 8
        cv2.circle(img, (cx, cy), 26, (252, 252, 252), -1)
        img[cy - 26 : cy + 26, cx - 26 : cx + 26] = cv2.GaussianBlur(
            img[cy - 26 : cy + 26, cx - 26 : cx + 26], (9, 9), 0
        )

    if cls in ("DIS", "MULTI"):
        # 변색: 표면 일부를 주황/갈색조로(LAB 이상영역)
        dx0, dx1 = x0 + 200, x0 + 200 + 120
        dy0, dy1 = y0 + 12, y1 - 12
        patch = img[dy0:dy1, dx0:dx1].astype(np.float32)
        # B 낮추고 R 높여 주황/갈색
        patch[:, :, 2] = np.clip(patch[:, :, 2] * 1.15 + 25, 0, 255)  # R(BGR[2])
        patch[:, :, 0] = np.clip(patch[:, :, 0] * 0.55, 0, 255)       # B
        patch[:, :, 1] = np.clip(patch[:, :, 1] * 0.80, 0, 255)       # G
        img[dy0:dy1, dx0:dx1] = patch.astype(np.uint8)

    return img, (x0, y0, x1, y1)


# 클래스명 → (파일명 클래스 토큰)
_FILE_CLASS = {
    "OK": "OK",
    "LEN": "LEN",
    "LEN_PLUS": "LEN",
    "LEN_MINUS": "LEN",
    "SCR": "SCR",
    "OIL": "OIL",
    "DIS": "DIS",
    "MULTI": "MULTI",
}


def write_dataset(
    out_dir: str | Path,
    *,
    classes: Optional[Sequence[str]] = None,
    per_class: int = 2,
    item_code: str = "HP12",
    view: str = "SIDE",
) -> List[Path]:
    """부록 A.4 폴더/파일명 규칙으로 합성 데이터셋을 생성한다.

    out_dir/<CLASS>/{item}_{view}_{class}_{ts}_{seq}.jpg
    결정적: 파일명 타임스탬프도 고정(테스트 재현성).
    """
    classes = list(classes or ["OK", "LEN", "OIL", "DIS", "SCR", "MULTI"])
    out = Path(out_dir)
    written: List[Path] = []
    for cls in classes:
        ftoken = _FILE_CLASS.get(cls.upper(), cls.upper())
        sub = out / ftoken
        sub.mkdir(parents=True, exist_ok=True)
        for i in range(per_class):
            img, _ = make_image(cls, seed=i)
            ts = f"20260101-0000{i:02d}"
            fname = f"{item_code}_{view}_{ftoken}_{ts}_{i:03d}.jpg"
            path = sub / fname
            cv2.imwrite(str(path), img)
            written.append(path)
    return written


def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="AIVIS 합성 더미 이미지 생성")
    ap.add_argument("out_dir", help="출력 폴더(dataset/raw 권장)")
    ap.add_argument("--per-class", type=int, default=2)
    ap.add_argument("--item", default="HP12")
    ap.add_argument("--view", default="SIDE")
    args = ap.parse_args(argv)
    files = write_dataset(
        args.out_dir, per_class=args.per_class, item_code=args.item, view=args.view
    )
    print(f"generated {len(files)} images under {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
