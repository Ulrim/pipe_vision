"""직접 만든 데이터 저장 — 부록 A.4 형식(표면 학습셋) + 장면 정답 json(개수·길이 평가).

표면 학습셋(튜브 단위):
  <out>/<split>/raw/<CLS>/HP12_POST_WASH_SURFACE_SIDE_<CLS>_<YYYYMMDD-HHmmss>_<seq>.jpg  + 같은 이름 .json
  CLS = OK | SCR | OIL | DIS | MULTI | BORDER   (BORDER 는 옅은 불량 — 사이드카 labels 에 실제 코드, border=true)
  → labeling.groundtruth 가 그대로 읽는다(AIVIS_DATASET_DIR).

장면(다발):
  <out>/scenes/<kind>/<kind>_<seq>.jpg + .json   (scene.make_scene 의 정답)

이미지는 파이프라인이 표면 판정에 넘기는 영역과 같게 자른다 — 튜브 몸통(끝에서 6% 안쪽),
둘레 방향은 전체(일반 분할) 또는 가운데 60%(다발 띠)를 섞는다. 배경 한두 화소가 섞이는 것까지 같다.
"""
from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from .defects import DEFECT_CODES, paint_defects
from .tube import TubePatch, body_crop, render_tube

SURFACE_CLASSES = ("OK", "SCR", "OIL", "DIS", "MULTI", "BORDER")
_T0 = datetime(2026, 10, 24, 9, 0, 0)
NOTE = "직접 만든 데이터 — 인수 증빙 아님"


def _camera(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """촬영 조건 흔들기: 밝기·감마·흐림·잡음·JPEG."""
    x = img.astype(np.float32) * float(rng.uniform(0.8, 1.2))
    x = 255.0 * (np.clip(x, 0, 255) / 255.0) ** float(rng.uniform(0.85, 1.2))
    s = float(rng.choice([0.0, 0.0, rng.uniform(0.4, 1.1)]))
    if s > 0:
        x = cv2.GaussianBlur(x, (0, 0), s)
    x = x + rng.normal(0, rng.uniform(0.8, 3.5), x.shape)
    x = np.clip(x, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", x, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(78, 96))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def surface_sample(cls: str, rng: np.random.Generator, *,
                   real_patch: Optional[Callable[[np.random.Generator, float, float], TubePatch]] = None,
                   p_real: float = 0.5):
    """튜브 몸통 사진 1장 + 라벨. 반환 (BGR uint8, labels, border, source)."""
    L = float(rng.uniform(500, 1400))
    D = L / float(rng.uniform(18, 32))
    use_real = real_patch is not None and rng.random() < p_real
    light = rng.random() < 0.4
    patch = real_patch(rng, L, D) if use_real else render_tube(L, D, rng, light_bg=light)
    labels: List[str] = []
    border = False
    if cls in ("SCR", "OIL", "DIS"):
        labels = paint_defects(patch, [cls], rng)
    elif cls == "MULTI":
        k = int(rng.integers(2, 4))
        labels = paint_defects(patch, list(rng.choice(DEFECT_CODES, size=k, replace=False)), rng)
    elif cls == "BORDER":
        labels = paint_defects(patch, [str(rng.choice(DEFECT_CODES))], rng, border=True)
        border = True
    elif cls != "OK":
        raise ValueError(f"모르는 클래스: {cls}")
    # 배경 위에 얹어 실루엣 가장자리까지 실제처럼
    bg = float(rng.uniform(180, 215)) if light else float(rng.uniform(18, 55))
    a = patch.alpha[..., None]
    patch.rgb = patch.rgb * a + bg * (1 - a)
    crop = body_crop(patch, frac_v=float(rng.choice([1.0, 1.0, 0.6])))
    return _camera(crop, rng), labels, border, ("real-texture" if use_real else "procedural")


def write_surface_set(out: Path, counts: Dict[str, int], rng: np.random.Generator, *,
                      real_patch=None, p_real: float = 0.5, item: str = "HP12") -> Dict[str, int]:
    written: Dict[str, int] = {}
    seq = 0
    for cls, n in counts.items():
        d = out / "raw" / cls
        d.mkdir(parents=True, exist_ok=True)
        for _ in range(n):
            img, labels, border, source = surface_sample(cls, rng, real_patch=real_patch, p_real=p_real)
            seq += 1
            ts = (_T0 + timedelta(seconds=seq)).strftime("%Y%m%d-%H%M%S")
            stem = f"{item}_POST_WASH_SURFACE_SIDE_{cls}_{ts}_{seq:05d}"
            cv2.imwrite(str(d / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            side = {"item_code": item, "inspection_stage": "POST_WASH_SURFACE", "view": "SIDE",
                    "labels": [str(x) for x in labels], "border": bool(border), "lighting": "diffuse",
                    "inspector": "synthetic", "captured_at": (_T0 + timedelta(seconds=seq)).isoformat() + "+09:00",
                    "source": source, "note": NOTE}
            (d / f"{stem}.json").write_text(json.dumps(side, ensure_ascii=False, indent=1), encoding="utf-8")
            written[cls] = written.get(cls, 0) + 1
    return written


def write_scene_set(out: Path, kinds: Dict[str, int], seed: int, *, size=(1600, 1100), patch_source=None) -> int:
    from .scene import make_scene, random_spec

    n = 0
    for kind, count in kinds.items():
        d = out / "scenes" / kind
        d.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            rng = np.random.default_rng((seed, zlib.crc32(kind.encode()) & 0xFFFF, i))
            spec = random_spec(rng, kind=kind, seed=int(rng.integers(0, 2**31 - 1)), size=size)
            img, gt = make_scene(spec, patch_source=patch_source)
            stem = f"{kind}_{i:04d}"
            cv2.imwrite(str(d / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            (d / f"{stem}.json").write_text(json.dumps(gt, ensure_ascii=False, default=_jsonable), encoding="utf-8")
            n += 1
    return n


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.str_):
        return str(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


__all__ = ["NOTE", "SURFACE_CLASSES", "surface_sample", "write_scene_set", "write_surface_set"]
