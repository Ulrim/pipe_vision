"""학습·평가 데이터 만들기 CLI (vision.synth) — 실물 정답셋 전까지 쓸 데이터.

    python -m vision.tools.build_training_data --out /data/synth            # 프로그램으로만
    python -m vision.tools.build_training_data --out /data/synth --real-dir ~/photos   # 현장 사진 질감 섞기

만드는 것:
  <out>/train/raw/<CLS>/...  표면 학습셋(부록 A.4 + 사이드카) — 기본 정상 600, SCR·OIL·DIS 각 300, MULTI 150, BORDER 240
  <out>/test/raw/<CLS>/...   같은 구성 1/4, 다른 시드·(현장 사진이 있으면) 다른 사진의 질감
  <out>/scenes/<kind>/...    다발 장면 + 정답(개수·튜브 모서리·길이 mm) — 개수·길이 평가용

결정적(시드 고정). **인수 증빙 아님** — 실제 정확도는 실물 정답셋(AIVIS_DATASET_DIR)으로 다시 잰다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from vision.synth.dataset import write_scene_set, write_surface_set

TRAIN_COUNTS = {"OK": 600, "SCR": 300, "OIL": 300, "DIS": 300, "MULTI": 150, "BORDER": 240}
SCENE_KINDS = {"production": 60, "tray": 60, "floor": 30, "hard": 30}


def _real_sources(real_dir: Optional[str]):
    """현장 사진 질감 — 사진 단위로 train/test 를 나눈다(같은 사진이 양쪽에 들어가지 않게)."""
    if not real_dir:
        return None, None, {}
    from vision.synth.real import load_bank, patch_from_real

    bank = load_bank(real_dir)
    sources = sorted({t.source for t in bank})
    if not bank or len(sources) < 2:
        return None, None, {"real_tubes": len(bank), "real_photos": len(sources)}
    test_src = set(sources[-max(1, len(sources) // 4):])
    tr = [t for t in bank if t.source not in test_src]
    te = [t for t in bank if t.source in test_src]

    def maker(pool):
        def make(rng, L, D):
            return patch_from_real(pool[int(rng.integers(0, len(pool)))], L, D, rng)
        return make
    info = {"real_tubes": len(bank), "real_photos": len(sources), "train_tubes": len(tr), "test_tubes": len(te),
            "test_photos": sorted(test_src)}
    return maker(tr), maker(te), info


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--real-dir", default=None, help="현장 사진 폴더(붙은 다발 사진에서 튜브 질감을 자른다)")
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--scale", type=float, default=1.0, help="표면 학습셋 수량 배율")
    ap.add_argument("--scenes", type=float, default=1.0, help="장면 수량 배율(0 이면 장면 안 만듦)")
    ap.add_argument("--scene-size", default="1600x1100")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    real_tr, real_te, info = _real_sources(a.real_dir)
    counts = {k: max(1, int(round(v * a.scale))) for k, v in TRAIN_COUNTS.items()}
    tcounts = {k: max(1, int(round(v * a.scale / 4))) for k, v in TRAIN_COUNTS.items()}
    wtr = write_surface_set(out / "train", counts, np.random.default_rng(a.seed), real_patch=real_tr)
    wte = write_surface_set(out / "test", tcounts, np.random.default_rng(a.seed + 1), real_patch=real_te)
    n_sc = 0
    if a.scenes > 0:
        w, h = (int(x) for x in a.scene_size.lower().split("x"))
        kinds = {k: max(1, int(round(v * a.scenes))) for k, v in SCENE_KINDS.items()}
        n_sc = write_scene_set(out, kinds, a.seed + 2, size=(w, h), patch_source=None)
    manifest = {"train": wtr, "test": wte, "scenes": n_sc, "real": info, "seed": a.seed,
                "note": "직접 만든 데이터 — 인수 증빙 아님(CLAUDE.md §1.2)"}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
