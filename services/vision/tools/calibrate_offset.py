"""마스터 시료로 길이 치우침(length_offset_mm) 구하기 — 현장 설치·카메라 이동 뒤 한 번.

    python -m vision.tools.calibrate_offset --scale 0.177 --ref 250 \
        master1.jpg:250.03 master1b.jpg:250.03 master2.jpg:249.96 ...

이미지마다 캘리퍼로 잰 실제 길이(mm)를 `파일:길이` 로 준다. 사진에 튜브가 여러 개면 모두 같은 마스터 길이로 본다
(한 개씩 찍는 것을 권장). 배포 경로 그대로(배치 검사 axis=auto) 재고, 이미 들어 있는 오프셋은 빼고 잰다.

출력: 평균(캘리퍼 − 측정) = 넣을 `length_offset_mm`, 흩어짐(σ), 표본 수, 기준정보 촬영 레시피에 넣을 JSON.
표본이 3개(튜브) 미만이거나 σ 가 치우침보다 크면 경고 — 오프셋보다 셋업(초점·거리·조명)을 먼저 본다.
**docs/FIELD_SETUP.md §5 '길이 치우침 보정'.**
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from aivis_types import ItemMaster

from vision.multi.batch import inspect_batch


def measure(pairs: Sequence[Tuple[str, float]], item: ItemMaster) -> List[Tuple[str, float, float]]:
    """(파일, 캘리퍼, 측정) 목록 — 오프셋 0 으로 잰다."""
    base = item.model_copy(update={"capture_recipe": {**(item.capture_recipe or {}), "length_offset_mm": 0.0}})
    out = []
    for path, true_mm in pairs:
        img = cv2.imread(path)
        if img is None:
            raise SystemExit(f"이미지를 못 읽음: {path}")
        b = inspect_batch(img, base, axis="auto", stage="CUT_LENGTH")
        for t in b.tubes:
            if t.length_mm is not None:
                out.append((Path(path).name, float(true_mm), float(t.length_mm)))
    return out


def summarize(rows: Sequence[Tuple[str, float, float]]) -> dict:
    d = np.array([c - m for _f, c, m in rows], dtype=float)
    res = {"samples": int(d.size)}
    if d.size:
        res.update(length_offset_mm=round(float(d.mean()), 3), sd_mm=round(float(d.std(ddof=1)) if d.size > 1 else 0.0, 4))
    warn = []
    if d.size < 3:
        warn.append("표본이 3개 미만 — 마스터 시료를 더 찍는다(튜브 3개 × 5회 권장)")
    elif res["sd_mm"] > abs(res["length_offset_mm"]):
        warn.append("흩어짐이 치우침보다 크다 — 오프셋보다 셋업(초점 고정·거리·조명·정지 촬영)을 먼저 본다")
    res["warnings"] = warn
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("shots", nargs="+", help="파일:캘리퍼길이(mm)")
    ap.add_argument("--scale", type=float, required=True, help="px→mm 배율(기준정보 px_to_mm_scale)")
    ap.add_argument("--ref", type=float, required=True, help="기준 길이(mm) — 판정용, 오프셋 계산엔 쓰지 않음")
    ap.add_argument("--od", type=float, default=None, help="외경(mm, 있으면 덩어리 나누기 기준)")
    a = ap.parse_args(argv)
    pairs = []
    for s in a.shots:
        path, _, val = s.rpartition(":")
        if not path:
            raise SystemExit(f"'파일:길이' 형식이 아님: {s}")
        pairs.append((path, float(val)))
    item = ItemMaster(item_code="MASTER", item_name="master", ref_length_mm=a.ref, tol_plus_mm=1.0, tol_minus_mm=1.0,
                      px_to_mm_scale=a.scale, oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5,
                      outer_diameter_mm=a.od)
    rows = measure(pairs, item)
    for f, c, m in rows:
        print(f"{f}: 캘리퍼 {c:.3f}  측정 {m:.3f}  차 {c - m:+.3f}")
    res = summarize(rows)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if "length_offset_mm" in res:
        print("기준정보 촬영 레시피에 넣을 값:", json.dumps({"length_offset_mm": res["length_offset_mm"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
