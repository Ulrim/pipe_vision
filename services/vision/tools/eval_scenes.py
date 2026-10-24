"""장면 정답셋으로 개수·길이 평가 (vision.synth 장면 또는 같은 형식의 실물 정답).

    python -m vision.tools.eval_scenes --scenes <out>/scenes --report <폴더>

장면 json(정답)과 jpg 를 짝지어 inspect_batch(axis="auto") 를 돌리고 잰다.
- 개수: 맞음 / 틀림 / '불확실' 표시(틀렸지만 스스로 재확인으로 보낸 것 포함), 기대 개수와 다를 때 불일치 검출.
- 길이: 원근 없는 장면만(원근이 있으면 영상 px 길이가 위치마다 달라 mm 정답과 비교할 수 없다).
  개수가 맞은 장면에서 튜브를 축 방향 순서로 짝지어 mm 오차, 끝 못 찾음 비율, LEN 판정 일치.
- 처리시간(판정만, 저장 제외).
결과: report.json + report.md + 실패 장면 목록(재현용). **직접 만든 데이터 — 인수 증빙 아님.**
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np
from aivis_types import ItemMaster

from vision.multi.batch import inspect_batch


def _item(gt: dict) -> ItemMaster:
    sp = gt["spec"]
    return ItemMaster(item_code="HP12", item_name="synth", ref_length_mm=float(sp["length_mm"]),
                      tol_plus_mm=float(sp["tol_mm"]), tol_minus_mm=float(sp["tol_mm"]),
                      px_to_mm_scale=float(gt["mm_per_px"]), oil_threshold=0.3, discolor_threshold=0.2,
                      scratch_threshold=0.15, expected_count=int(gt["n_tubes"]))


def _match_lengths(gt_tubes: List[dict], tubes) -> Optional[List[tuple]]:
    """개수가 같을 때 축 방향 순서로 짝짓기(회전으로 순서가 뒤집혔을 수 있어 둘 다 보고 오차 작은 쪽)."""
    if len(gt_tubes) != len(tubes):
        return None
    g = [t["length_mm"] for t in gt_tubes]
    cand = []
    for order in (list(tubes), list(reversed(tubes))):
        p = [t.length_mm for t in order]
        errs = [abs(a - b) for a, b in zip(g, p) if b is not None]
        cand.append((float(np.median(errs)) if errs else 1e9, p))
    best = min(cand, key=lambda c: c[0])[1]
    return list(zip(gt_tubes, best))


def evaluate(scene_dir: Path, *, mismatch_every: int = 4, limit: Optional[int] = None) -> dict:
    files = sorted(scene_dir.rglob("*.json"))
    if limit:
        files = files[:limit]
    rows = []
    for k, jf in enumerate(files):
        gt = json.loads(jf.read_text(encoding="utf-8"))
        img = cv2.imread(str(jf.with_suffix(".jpg")))
        if img is None:
            continue
        n = int(gt["n_tubes"])
        expected = n + 1 if (mismatch_every and k % mismatch_every == mismatch_every - 1) else n
        item = _item(gt)
        t0 = time.perf_counter()
        b = inspect_batch(img, item, axis="auto", expected_count=expected, stage="CUT_LENGTH")
        ms = (time.perf_counter() - t0) * 1000
        sp = gt["spec"]
        row = {"scene": str(jf.relative_to(scene_dir)), "kind": jf.parent.name, "n": n, "expected": expected,
               "detected": b.count_detected, "uncertain": bool(getattr(b, "count_uncertain", False)),
               "dense": bool(getattr(b, "dense", False)), "mismatch_flag": bool(b.count_mismatch),
               "perspective": bool(sp.get("keystone", 0) > 1e-6), "background": sp["background"],
               "arrangement": sp["arrangement"], "angle": sp["angle_deg"], "ms": round(ms, 1)}
        row["count_ok"] = row["detected"] == n
        if not row["perspective"] and row["count_ok"]:
            pairs = _match_lengths(gt["tubes"], b.tubes)
            errs, missing, len_ok, len_total = [], 0, 0, 0
            for gt_t, length in pairs:
                if length is None:
                    missing += 1
                    continue
                errs.append(length - gt_t["length_mm"])
            # LEN 판정 일치(튜브 순서 짝 기준)
            for gt_t, length in pairs:
                if length is None:
                    continue
                gt_len = "LEN" in gt_t["labels"]
                pred_len = abs(length - float(sp["length_mm"])) > float(sp["tol_mm"])
                len_total += 1
                len_ok += int(gt_len == pred_len)
            row.update(len_errors=[round(e, 3) for e in errs], ends_missing=missing, len_ok=len_ok, len_total=len_total)
        rows.append(row)
    return summarize(rows)


def summarize(rows: List[dict]) -> dict:
    def stats(rs: List[dict]) -> dict:
        if not rs:
            return {}
        n = len(rs)
        ok = sum(r["count_ok"] for r in rs)
        wrong = [r for r in rs if not r["count_ok"]]
        wrong_flagged = sum(r["uncertain"] for r in wrong)
        errs = np.array([e for r in rs for e in r.get("len_errors", [])], float)
        tubes_eval = sum(len(r.get("len_errors", [])) + r.get("ends_missing", 0) for r in rs)
        miss = sum(r.get("ends_missing", 0) for r in rs)
        lt = sum(r.get("len_total", 0) for r in rs)
        lo = sum(r.get("len_ok", 0) for r in rs)
        mm = [r for r in rs if r["expected"] != r["n"]]
        return {
            "scenes": n,
            "count_accuracy": round(ok / n, 4),
            "count_wrong": len(wrong),
            "wrong_but_flagged_uncertain": wrong_flagged,
            "silent_wrong": len(wrong) - wrong_flagged,
            "uncertain_rate": round(sum(r["uncertain"] for r in rs) / n, 4),
            "mismatch_scenes": len(mm),
            "mismatch_detected": sum(r["mismatch_flag"] for r in mm),
            "length_tubes": int(tubes_eval),
            "ends_missing_rate": round(miss / tubes_eval, 4) if tubes_eval else None,
            "length_mae_mm": round(float(np.mean(np.abs(errs))), 4) if errs.size else None,
            "length_p95_abs_mm": round(float(np.percentile(np.abs(errs), 95)), 4) if errs.size else None,
            "length_bias_mm": round(float(np.mean(errs)), 4) if errs.size else None,
            "length_sd_mm": round(float(np.std(errs)), 4) if errs.size else None,
            "len_verdict_agreement": round(lo / lt, 4) if lt else None,
            "ms_mean": round(float(np.mean([r["ms"] for r in rs])), 1),
        }
    kinds = sorted({r["kind"] for r in rows})
    return {"overall": stats(rows), "by_kind": {k: stats([r for r in rows if r["kind"] == k]) for k in kinds},
            "failures": [r for r in rows if not r["count_ok"]], "rows": rows,
            "note": "직접 만든 데이터 — 인수 증빙 아님"}


def f(v, pct: bool = False) -> str:
    return "-" if v is None else (f"{v * 100:.1f}%" if pct else f"{v}")


def write_report(res: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 장면 평가 — 개수·길이 (직접 만든 데이터, 인수 증빙 아님)", "",
             "| 종류 | 장면 | 개수 정확도 | 틀림(불확실 표시/조용히) | 불일치 검출 | 끝 못 찾음 | 길이 MAE | 길이 p95 | 길이 치우침 | 길이 σ | LEN 판정 일치 | 판정 ms |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in list(res["by_kind"].items()) + [("전체", res["overall"])]:
        if not s:
            continue
        lines.append(f"| {k} | {s['scenes']} | {f(s['count_accuracy'], True)} | {s['count_wrong']} "
                     f"({s['wrong_but_flagged_uncertain']}/{s['silent_wrong']}) | {s['mismatch_detected']}/{s['mismatch_scenes']} | "
                     f"{f(s['ends_missing_rate'], True)} | {f(s['length_mae_mm'])} | {f(s['length_p95_abs_mm'])} | "
                     f"{f(s['length_bias_mm'])} | {f(s['length_sd_mm'])} | "
                     f"{f(s['len_verdict_agreement'], True)} | {s['ms_mean']} |")
    lines += ["", "길이는 원근 없는 장면에서 개수가 맞은 경우만(mm, 장면의 mm/px 기준).", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--mismatch-every", type=int, default=4)
    a = ap.parse_args(argv)
    res = evaluate(Path(a.scenes), mismatch_every=a.mismatch_every, limit=a.limit)
    write_report(res, Path(a.report))
    print((Path(a.report) / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
