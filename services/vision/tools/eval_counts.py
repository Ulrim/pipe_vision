"""실물 사진 개수 평가 — 사진 폴더 + 정답 개수표(csv)로 배포 경로(axis=auto) 개수 정확도를 잰다.

    python -m vision.tools.eval_counts --images <사진 폴더> --truth counts.csv --out <결과 폴더>
    python -m vision.tools.eval_counts --images <사진 폴더> --out <결과 폴더>      # 정답표 없이 → 빈칸 정답표를 만든다

정답표(csv, 엑셀에서 저장 가능): `파일,실제개수[,비고]` — 첫 줄은 머리글. 실제개수가 빈 칸이면 평가에서 뺀다
(가까이 찍어 일부만 보이는 사진 등). 정답표가 없으면 검출 개수를 채운 표(`counts_template.csv`)를 만들어 준다 —
사람이 '실제개수' 칸만 고쳐 다시 넣으면 된다.

결과: 사진마다 결과 사진(튜브 테두리·번호), report.md/json — 정확 / 틀림(불확실 표시) / 틀림(표시 없음).
기준정보에 개수(expected_count)를 넣고 쓰면 틀린 개수는 모두 '개수 불일치' 로 배치 NG 가 된다 — '표시 없음' 은
개수를 모르고 쓸 때 조용히 넘어갈 수 있는 것이다(개수 세기 자체의 실력).
현장 사진은 공개 저장소에 넣지 않는다 — 결과 폴더도 저장소 밖에 둔다.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import cv2
from aivis_types import ItemMaster

from vision.imaging.save import render_batch_overlay
from vision.multi.batch import inspect_batch

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def read_truth(path: Path) -> Dict[str, Optional[int]]:
    """파일 이름(확장자 포함·제외 모두) → 실제 개수(빈 칸이면 None)."""
    out: Dict[str, Optional[int]] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name = row[0].strip()
        val = row[1].strip() if len(row) > 1 else ""
        n = int(float(val)) if val else None
        out[name] = n
        out[Path(name).stem] = n
    return out


def evaluate(images: Path, truth: Optional[Dict[str, Optional[int]]], out: Path, *,
             od_mm: Optional[float] = None, scale: float = 0.2) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "overlay").mkdir(exist_ok=True)
    rows: List[dict] = []
    for p in sorted(x for x in images.iterdir() if x.suffix.lower() in EXTS):
        img = cv2.imread(str(p))
        if img is None:
            continue
        n_true = None if truth is None else truth.get(p.name, truth.get(p.stem))
        item = ItemMaster(item_code="EVAL", item_name="eval", ref_length_mm=250.0, tol_plus_mm=1.0, tol_minus_mm=1.0,
                          px_to_mm_scale=scale, oil_threshold=0.5, discolor_threshold=0.5, scratch_threshold=0.5,
                          expected_count=n_true or 1, outer_diameter_mm=od_mm)
        b = inspect_batch(img, item, axis="auto", expected_count=n_true, stage="CUT_LENGTH")
        ov = render_batch_overlay(b.view if b.view is not None else img, b)
        cv2.imwrite(str(out / "overlay" / f"{p.stem}.jpg"), ov, [cv2.IMWRITE_JPEG_QUALITY, 85])
        row = {"file": p.name, "detected": b.count_detected, "uncertain": bool(b.count_uncertain), "truth": n_true,
               "notes": list(b.notes)[:3]}
        if n_true is not None:
            row["result"] = "정확" if b.count_detected == n_true else ("틀림(불확실 표시)" if b.count_uncertain else "틀림(표시 없음)")
        rows.append(row)
    scored = [r for r in rows if r.get("truth") is not None]
    summary = {
        "photos": len(rows),
        "scored": len(scored),
        "exact": sum(r["result"] == "정확" for r in scored),
        "wrong_flagged": sum(r["result"] == "틀림(불확실 표시)" for r in scored),
        "silent_wrong": sum(r["result"] == "틀림(표시 없음)" for r in scored),
    }
    res = {"summary": summary, "rows": rows}
    (out / "report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 실물 사진 개수 평가", ""]
    if scored:
        s = summary
        lines += [f"정답이 있는 사진 {s['scored']}장: 정확 **{s['exact']}** · 틀림(불확실 표시) {s['wrong_flagged']} · "
                  f"틀림(표시 없음) {s['silent_wrong']}", "",
                  "기준정보에 개수를 넣고 쓰면 틀린 개수는 모두 '개수 불일치' 로 배치 NG(작업자 확인)가 된다.", ""]
    lines += ["| 파일 | 검출 | 실제 | 결과 | 비고 |", "|---|---|---|---|---|"]
    for r in rows:
        flag = " (불확실)" if r["uncertain"] else ""
        lines.append(f"| {r['file']} | {r['detected']}{flag} | {'' if r['truth'] is None else r['truth']} | "
                     f"{r.get('result', '')} | {'; '.join(r['notes'])[:80]} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if truth is None:
        with open(out / "counts_template.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["파일", "실제개수", "검출개수(참고)", "비고"])
            for r in rows:
                w.writerow([r["file"], "", r["detected"], "불확실" if r["uncertain"] else ""])
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", required=True)
    ap.add_argument("--truth", default=None, help="정답 개수표 csv(파일,실제개수[,비고]) — 없으면 빈칸 표를 만든다")
    ap.add_argument("--out", required=True)
    ap.add_argument("--od", type=float, default=None, help="외경(mm) — 기준정보에 넣을 값과 같게")
    ap.add_argument("--scale", type=float, default=0.2, help="px→mm 배율(개수만 볼 때는 아무 값)")
    a = ap.parse_args(argv)
    truth = read_truth(Path(a.truth)) if a.truth else None
    res = evaluate(Path(a.images), truth, Path(a.out), od_mm=a.od, scale=a.scale)
    print((Path(a.out) / "report.md").read_text(encoding="utf-8")[:4000])
    return 0 if res else 1


if __name__ == "__main__":
    raise SystemExit(main())
