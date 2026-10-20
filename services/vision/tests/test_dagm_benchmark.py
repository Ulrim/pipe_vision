"""DAGM 벤치마크 도구 — 폴더 파싱·두 평가(공식 분할/교차검증)·비교표·속도 (§6.3).

실제 DAGM 은 저장소에 없다(라이선스·용량). DAGM 과 같은 폴더 구조의 작은
합성 세트로 도구가 결정적으로 돌고 보고서가 계약대로 나오는지만 본다.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from vision.tools.dagm_benchmark import (
    COMPARE_CONFIGS,
    BenchConfig,
    parse_labels,
    run_benchmark,
    run_compare,
    time_config,
    to_markdown,
)


def _tex(seed: int, size: int = 128) -> np.ndarray:
    rng = np.random.default_rng(seed)
    contrast = 0.8 + 0.4 * rng.random()
    g = np.clip(120 + rng.normal(0, 22 * contrast, (size, size)), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(g, (0, 0), 1.2)


def _smudge(g: np.ndarray) -> np.ndarray:
    out = g.copy()
    blur = cv2.GaussianBlur(g, (0, 0), 3.0)
    m = np.zeros(g.shape, np.uint8)
    cv2.circle(m, (40, 40), 14, 255, -1)
    out[m > 0] = blur[m > 0]
    return out


def _make_split(root: Path, name: str, normals: list[int], defects: list[int]) -> None:
    split = root / name
    (split / "Label").mkdir(parents=True)
    lines = ["1"]
    n = 0
    for s in normals:
        n += 1
        fn = f"{n:04d}.PNG"
        cv2.imwrite(str(split / fn), _tex(s))
        lines.append(f"{n}\t0\t{fn}\t0\t0\t")
    for s in defects:
        n += 1
        fn = f"{n:04d}.PNG"
        cv2.imwrite(str(split / fn), _smudge(_tex(s)))
        lines.append(f"{n}\t1\t{fn}\t0\t{n:04d}_label.PNG\t")
    (split / "Label" / "Labels.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "Class1"
    _make_split(root, "Train", list(range(18)), [])
    _make_split(root, "Test", list(range(100, 106)), list(range(200, 206)))
    return root


def test_parse_labels_reads_dagm_layout(tmp_path: Path) -> None:
    root = _dataset(tmp_path)
    tr = parse_labels(root / "Train")
    te = parse_labels(root / "Test")
    assert len(tr) == 18 and not any(i.is_defect for i in tr)
    assert sum(i.is_defect for i in te) == 6 and len(te) == 12


def test_run_benchmark_two_protocols_deterministic(tmp_path: Path) -> None:
    root = _dataset(tmp_path)
    a, model = run_benchmark(root, grid=4, cv_folds=3)
    b, _ = run_benchmark(root, grid=4, cv_folds=3)
    # 결정적: 판정·개수는 같고, 실수는 BLAS 합산 순서 수준(1e-9)까지만 흔들린다.
    da, db = a.as_dict(), b.as_dict()
    for k, va in da.items():
        if isinstance(va, float):
            assert np.isclose(va, db[k], rtol=1e-9, atol=0.0), k
        else:
            assert va == db[k], k
    assert (a.n_train_normal, a.n_test_normal, a.n_test_defect) == (18, 6, 6)
    assert a.feature_set == "v2" and a.threshold_level == "image"
    assert a.auroc is not None and a.auroc >= 0.9
    assert a.cv_folds == 3 and a.cv_auroc_mean is not None and a.cv_auroc_mean >= 0.9
    assert a.tp + a.fn == 6 and a.fp + a.tn == 6
    assert model["feature_set"] == "v2" and model["score_topk"] == 2


def test_v1_config_ignores_overlap_and_topk(tmp_path: Path) -> None:
    """v1 은 구 모델과 같은 채점(겹침 없음·최악 패치)으로만 돈다."""
    root = _dataset(tmp_path)
    r, _ = run_benchmark(root, feature_set="v1", grid=2, overlap=True, topk=3,
                         calib="patch", cv_folds=0)
    assert (r.patch_overlap, r.score_topk, r.threshold_level) == (False, 1, "patch")
    assert r.cv_auroc_mean is None


def test_compare_report_keeps_caveat_and_marks_default(tmp_path: Path) -> None:
    root = _dataset(tmp_path)
    cfgs = (
        BenchConfig("구 기본값", "v1", 1, False, 1, "patch"),
        BenchConfig("새 기본값", "v2", 4, True, 2, "image"),
    )
    res = run_compare(root, cv_folds=3, timing=False, configs=cfgs)
    assert [r.percentile for r in res] == [99.0, 99.0, 95.0]  # 95% 는 이미지 임계 후보만
    md = to_markdown(res)
    assert "에이엠피 제품에 쓸 수 없습니다" in md
    assert "| 구 기본값 |" in md and "| 새 기본값 |" in md
    # 비교표의 새 기본값 행은 실제 기본 설정과 같아야 한다.
    marked = [c for c in COMPARE_CONFIGS if "새 기본값" in c.name]
    assert len(marked) == 1
    d = marked[0]
    assert (d.feature_set, d.grid, d.overlap, d.topk) == ("v2", 8, True, 2)


def test_time_config_reports_each_size() -> None:
    out = time_config(BenchConfig("t", "v2", 8, True, 2), sizes=((128, 128), (300, 40)),
                      repeat=1)
    assert set(out) == {"128x128", "300x40"}
    assert all(v > 0 for v in out.values())
