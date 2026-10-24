"""표면 판정 평가 — 부록 A.4 형식 정답셋(튜브 몸통 사진 + 사이드카)에서 고전 CV 와 ONNX 모델을 비교.

    python -m vision.tools.eval_surface --data <정답셋>/test --report <폴더> [--onnx <모델>.onnx]

배포 경로 그대로 잰다 — 고전 CV 는 analyze_surface, ONNX 는 OnnxSurfaceModel.predict(사이드카 계약),
판정 임계는 기준정보(ItemMaster)로 넘긴다. ONNX 임계는 사이드카의 recommended_thresholds(학습 때 검증셋에서
정상 오경보 2% 로 잡은 값), 고전 CV 임계는 --classical-th(기본: 데모 기준정보 0.30/0.20/0.15, "auto" 면
같은 규칙 — 이 정답셋에서 항목별 오경보 2% — 로 맞춘 값).

항목마다 정확도·검출률·오경보율과 '정상 튜브를 하나라도 불량으로 본 비율', 장당 처리시간.
경계 샘플(border=true)은 정확도에서 따로 본다(옅은 불량은 사람도 갈린다).
**직접 만든 데이터로 잰 값은 인수 증빙이 아니다** — 실물 정답셋(AIVIS_DATASET_DIR)으로 다시 잰다.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from aivis_types import ItemMaster

from vision.surface.classical import analyze_surface

KEYS = ("oil", "discolor", "scratch")
CODES = {"oil": "OIL", "discolor": "DIS", "scratch": "SCR"}


def _item(th: Sequence[float]) -> ItemMaster:
    return ItemMaster(item_code="HP12", item_name="eval", ref_length_mm=250.0, tol_plus_mm=0.5, tol_minus_mm=0.5,
                      px_to_mm_scale=0.177, oil_threshold=float(th[0]), discolor_threshold=float(th[1]),
                      scratch_threshold=float(th[2]))


def load(root: Path) -> List[Tuple[Path, set, bool]]:
    out = []
    for js in sorted(root.glob("raw/*/*.json")):
        img = js.with_suffix(".jpg")
        if img.exists():
            side = json.loads(js.read_text(encoding="utf-8"))
            out.append((img, set(side.get("labels", [])), bool(side.get("border"))))
    return out


def run(samples, predict) -> dict:
    rows = []
    t_all = 0.0
    for path, labels, border in samples:
        img = cv2.imread(str(path))
        t0 = time.perf_counter()
        res = predict(img)
        t_all += time.perf_counter() - t0
        codes = {str(getattr(c, "value", c)) for c in res.defect_codes}
        rows.append((labels, border, codes))
    out: Dict[str, dict] = {}
    for k in KEYS:
        c = CODES[k]
        y = np.array([c in lab for lab, _b, _p in rows])
        p = np.array([c in pc for _l, _b, pc in rows])
        clear = np.array([not b for _l, b, _p in rows])
        out[k] = {
            "accuracy": round(float((y == p).mean()), 4),
            "accuracy_clear": round(float((y[clear] == p[clear]).mean()), 4) if clear.any() else None,
            "recall": round(float(p[y].mean()), 4) if y.any() else None,
            "recall_clear": round(float(p[y & clear].mean()), 4) if (y & clear).any() else None,
            "false_alarm": round(float(p[~y].mean()), 4) if (~y).any() else None,
        }
    ok = [pc for lab, _b, pc in rows if not (lab & set(CODES.values()))]
    out["ok_false_alarm"] = round(sum(bool(pc) for pc in ok) / max(1, len(ok)), 4)
    out["samples"] = len(rows)
    out["ms_per_crop"] = round(1000 * t_all / max(1, len(rows)), 2)
    return out


def _auto_thresholds(samples, target_fa: float = 0.02) -> List[float]:
    """고전 CV 점수로 항목마다 '그 항목이 없는 사진' 의 오경보가 target_fa 가 되는 임계(ONNX 와 같은 규칙)."""
    big = _item([1.0, 1.0, 1.0])
    sc = []
    for path, labels, _b in samples:
        r = analyze_surface(cv2.imread(str(path)), big)
        sc.append(([r.oil_score or 0.0, r.discolor_score or 0.0, r.scratch_score or 0.0], labels))
    th = []
    for i, k in enumerate(KEYS):
        neg = [v[i] for v, lab in sc if CODES[k] not in lab]
        # 점수가 0·1 로 몰리면 분위수가 0 이 된다 — 그 값 '초과' 만 불량이 되게 살짝 올린다
        th.append(min(1.0, round(float(np.quantile(neg, 1 - target_fa)) + 1e-4, 4)) if neg else 0.5)
    return th


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--onnx", default=None)
    ap.add_argument("--classical-th", default="0.30,0.20,0.15")
    a = ap.parse_args(argv)
    samples = load(Path(a.data))
    res = {"data": a.data, "note": "직접 만든 데이터 — 인수 증빙 아님"}
    if a.classical_th == "auto":
        cth = _auto_thresholds(samples)
    else:
        cth = [float(x) for x in a.classical_th.split(",")]
    citem = _item(cth)
    res["classical"] = {"thresholds": dict(zip(KEYS, cth)), **run(samples, lambda im: analyze_surface(im, citem))}
    if a.onnx:
        from vision.surface.model import OnnxSurfaceModel

        m = OnnxSurfaceModel(a.onnx)
        if not m.loaded:
            raise SystemExit(f"ONNX 로드 실패: {m._load_error}")
        side = json.loads(Path(a.onnx).with_suffix(".json").read_text(encoding="utf-8"))
        rth = side.get("recommended_thresholds") or {}
        oth = [float(rth.get(k, 0.5)) for k in KEYS]
        oitem = _item(oth)
        res["onnx"] = {"model": a.onnx, "thresholds": dict(zip(KEYS, oth)), **run(samples, lambda im: m.predict(im, oitem))}
    out = Path(a.report)
    out.mkdir(parents=True, exist_ok=True)
    (out / "surface_eval.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 표면 판정 평가 (직접 만든 데이터, 인수 증빙 아님)", "", f"정답셋: `{a.data}` · {res['classical']['samples']}장", "",
             "| 방식 | 항목 | 정확도 | 정확도(경계 제외) | 검출률 | 오경보율 | 임계 |", "|---|---|---|---|---|---|---|"]
    for name in ("classical", "onnx"):
        if name not in res:
            continue
        r = res[name]
        for k in KEYS:
            lines.append(f"| {name} | {k} | {r[k]['accuracy']:.1%} | {r[k]['accuracy_clear']:.1%} | "
                         f"{r[k]['recall']:.1%} | {r[k]['false_alarm']:.1%} | {r['thresholds'][k]:.2f} |")
        lines.append(f"| {name} | 정상 오경보 | {r['ok_false_alarm']:.1%} | | | | {r['ms_per_crop']} ms/장 |")
    (out / "surface_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
