"""리포트 산출 (JSON + MD) — §9 qa 원칙: tests/fat·tests/sat 리포트로 남긴다.

각 하니스가 산출한 4지표 결과 dict 를 받아 report/<name>.json + report/<name>.md
를 쓴다. 지표 미달은 호출자(pytest)가 assert 로 차단한다(여기선 기록만).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _verdict(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def write_reports(report_dir: str | Path, name: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """report_dir/<name>.json + <name>.md 를 쓴다. 경로 dict 반환."""
    rd = Path(report_dir)
    rd.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

    json_path = rd / f"{name}.json"
    md_path = rd / f"{name}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_render_md(name, payload))

    return {"json": str(json_path), "md": str(md_path)}


def _render_md(name: str, p: Dict[str, Any]) -> str:
    lines: list[str] = []
    title = p.get("title", name.upper())
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- 생성시각(UTC): {p.get('generated_at')}")
    lines.append(f"- 데이터 출처: {p.get('dataset_source', 'synthetic')}")
    lines.append(f"- 표본 수: {p.get('sample_count', 'n/a')}")
    overall = p.get("overall_passed")
    if overall is not None:
        lines.append(f"- **종합 판정: {_verdict(bool(overall))}**")
    lines.append("")

    lines.append("## §1.2 인수 합격기준 4지표")
    lines.append("")
    lines.append("| No | 지표 | 측정값 | 목표 | 판정 |")
    lines.append("|---|---|---|---|---|")
    for row in p.get("kpi_table", []):
        lines.append(
            f"| {row['no']} | {row['name']} | {row['measured']} | {row['target']} | "
            f"{_verdict(row['passed'])} |"
        )
    lines.append("")

    # 지표2 항목별 혼동행렬.
    acc = p.get("metric2_item_accuracy")
    if acc:
        lines.append("## 지표2 — 항목별 정확도 & 혼동행렬")
        lines.append("")
        lines.append(f"- 임계: ≥{acc['threshold_pct']}% / 최저 정확도: "
                     f"{acc['min_accuracy_pct']}% → {_verdict(acc['passed'])}")
        lines.append("")
        lines.append("| 항목 | 정확도(%) | TP | FP | FN | TN | precision | recall |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for it, cm in acc["per_item"].items():
            lines.append(
                f"| {it} | {cm['accuracy_pct']} | {cm['tp']} | {cm['fp']} | "
                f"{cm['fn']} | {cm['tn']} | {cm['precision']} | {cm['recall']} |"
            )
        lines.append("")

    # 지표3 처리속도 백분위.
    lat = p.get("metric3_latency")
    if lat:
        lines.append("## 지표3 — 처리속도 백분위 (1,000장 배치)")
        lines.append("")
        lines.append("| 표본 | p50 | p95 | p99 | max | mean | >300ms |")
        lines.append("|---|---|---|---|---|---|---|")
        lines.append(
            f"| {lat['count']} | {lat['p50_ms']} | {lat['p95_ms']} | {lat['p99_ms']} | "
            f"{lat['max_ms']} | {lat['mean_ms']} | {lat['over_300_count']} |"
        )
        lines.append("")

    # 지표4 저장·연계.
    st = p.get("metric4_storage_mes")
    if st:
        lines.append("## 지표4 — 데이터 저장 & MES 연계율")
        lines.append("")
        lines.append(f"- 주입: {st['injected']}건 / 저장: {st['stored']}건 / "
                     f"MES 연계: {st['mes_synced']}건")
        lines.append(f"- 저장율: {st['storage_rate_pct']}% / "
                     f"연계율: {st['mes_rate_pct']}% → {_verdict(st['passed'])}")
        lines.append("")

    # MSA(선택).
    msa = p.get("msa")
    if msa:
        lines.append("## MSA — 길이 반복성/재현성 (§5 M3)")
        lines.append("")
        lines.append(f"- 반복 측정: {msa['repeats']}회 × {msa['samples']}샘플")
        lines.append(f"- 반복성(EV) σ: {msa['repeatability_std_mm']} mm")
        lines.append(f"- 재현성(AV) σ: {msa['reproducibility_std_mm']} mm")
        lines.append(f"- GR&R σ: {msa['grr_std_mm']} mm / %GR&R(공차대비): "
                     f"{msa['pct_grr_tolerance']}%")
        lines.append("")

    notes = p.get("notes")
    if notes:
        lines.append("## 비고")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)
