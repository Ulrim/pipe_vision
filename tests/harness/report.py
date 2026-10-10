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


def is_synthetic(p: Dict[str, Any]) -> bool:
    """합성 데이터로 낸 결과인가 — 그러면 인수 증빙이 아니다(2026-10-10 점검)."""
    return "synthetic" in str(p.get("dataset_source", "synthetic")).lower()


def write_reports(report_dir: str | Path, name: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """report_dir/<name>.json + <name>.md 를 쓴다. 경로 dict 반환."""
    rd = Path(report_dir)
    rd.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    # 증빙 등급: 합성 데이터 결과는 "참고" — 결과서에 그대로 쓰면 안 된다.
    payload["evidence"] = "synthetic-reference" if is_synthetic(payload) else "real"

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
    synthetic = is_synthetic(p)
    if overall is not None:
        if synthetic:
            lines.append(
                f"- **종합 판정: {_verdict(bool(overall))} — 합성 데이터 기준 참고값(인수 증빙 아님)**"
            )
        else:
            lines.append(f"- **종합 판정: {_verdict(bool(overall))}**")
    lines.append("")
    if synthetic:
        lines.append(
            "> ⚠ **이 결과는 합성 이미지로 낸 것이다 — FAT/SAT 결과서의 증빙으로 쓰지 않는다.** "
            "코드가 끝까지 돈다는 것(회귀 확인)만 보여준다. 결과서에는 실촬영 정답셋으로 "
            "`AIVIS_DATASET_DIR=<정답셋 폴더> pytest tests/fat tests/sat` 를 다시 돌린 결과만 쓴다."
        )
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
        scope = lat.get("scope", "acquire_to_save")
        lines.append(
            "- 측정 구간: **이미지 취득(읽기) → 판정 → 원본·결과 이미지 저장** (§1.2 정의)"
            if scope == "acquire_to_save" else f"- 측정 구간: {scope}"
        )
        bd = lat.get("breakdown_mean_ms") or {}
        if bd:
            lines.append(
                f"- 단계별 평균(ms): 취득 {bd.get('read_ms')} · 판정 {bd.get('infer_ms')} · "
                f"저장 {bd.get('save_ms')}"
            )
        io = lat.get("infer_only") or {}
        if io:
            lines.append(f"- 참고 — 판정만(종전 측정 구간): p95 {io.get('p95_ms')}ms (합격 판정에 쓰지 않음)")
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
        if synthetic and int(p.get("sample_count") or 0) <= 1:
            lines.append("## 결정성 확인 — 같은 합성 이미지 반복 (MSA 아님)")
            lines.append("")
            lines.append(
                "> 같은 이미지를 반복해 넣으면 결정적인 알고리즘은 늘 같은 값을 낸다 — "
                "%GR&R 0% 는 당연한 결과이고 **측정시스템 능력을 보여주지 않는다**. "
                "실물 MSA 는 실제 부품 10개 × 3회 × 측정자(재거치) 3명을 실제 카메라로 찍어 "
                "`python -m vision.tools.run_msa` 로 낸다(docs/LENGTH_TOLERANCE.md)."
            )
            lines.append("")
        else:
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
