"""맨 바깥 칸 확인 모델 학습 → ONNX(edge_cell) + 사이드카 + 교차검증 보고 (2026-11-14).

    PYTHONPATH=<torch 경로> python -m vision.models.train_edge_check --cells cells.npz --out runs/edge_v1 [--cv-block 50]

입력: vision.tools.build_edge_cells 의 npz. 학습은 배포 경로의 개수가 정답과 같은 사진의 칸만 쓴다 —
맨 끝 칸·한 칸 안쪽·안쪽 칸 = 튜브(1), 한·두 칸 밖 = 튜브 아님(0).

교차검증: 사진 이름 끝 번호를 `--cv-block` 장씩 묶어 블록 2겹(짝수 블록 / 홀수 블록) — 같은 장면을 연달아 찍은
사진이 한쪽에만 들어가게. 한 겹으로 학습해 다른 겹 사진을 예측하고, 사진 단위로 셋을 보고한다:
  - 재확인 신호로 쓸 때: 자동 확정 장수·그 정확도, 재확인 장수(그중 실제 오답), 조용히 틀린 장수
  - 개수를 직접 고칠 때(참고 — 배포에 쓰지 않는다)
산출물: <out>/edge.onnx + edge.json(kind=edge_cell, threshold) + metrics.json. 배포: AIVIS_EDGE_MODEL=<out>/edge.onnx.

모델: 작은 CNN(약 0.2M) — 입력 1×32×128(위 = 다발 바깥), 위 절반·아래 절반을 따로 평균해 이어 붙인 뒤 로짓 하나.
torch 는 학습 환경에만 필요하다(추론은 onnxruntime). **고객 사진으로 학습한 모델은 저장소에 넣지 않는다.**
"""
from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

KIND = {"lo_edge": "edge", "hi_edge": "edge", "lo_edge2": "edge2", "hi_edge2": "edge2", "inner": "inner",
        "lo_out": "out1", "hi_out": "out1", "lo_out2": "out2", "hi_out2": "out2"}
WEIGHT = {"edge": 3.0, "edge2": 1.0, "inner": 0.3, "out1": 3.0, "out2": 1.0}


def _fold(name: str, block: int) -> int:
    m = re.findall(r"\d+", Path(name).stem)
    if m:
        return ((int(m[-1]) - 1) // block) % 2
    return zlib.crc32(name.encode()) % 2


def build_net():
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()

            def blk(i, o):
                return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                     nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU())

            self.f = nn.Sequential(blk(1, 16), nn.MaxPool2d(2), blk(16, 32), nn.MaxPool2d(2), blk(32, 64),
                                   nn.MaxPool2d((2, 4)), blk(64, 64))
            self.h = nn.Sequential(nn.Dropout(0.3), nn.Linear(128, 1))

        def forward(self, x):
            z = self.f(x)                                   # (b, 64, 4, 8)
            top = z[:, :, :2].mean((2, 3))                  # 위 = 다발 바깥
            bot = z[:, :, 2:].mean((2, 3))                  # 아래 = 다발 쪽
            import torch

            return self.h(torch.cat([top, bot], 1)).squeeze(1)

    return Net()


def _aug(xb: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    xb = xb.copy()
    flip = rng.random(len(xb)) < 0.5
    xb[flip] = xb[flip][:, :, ::-1]
    xb = xb * rng.uniform(0.8, 1.25, (len(xb), 1, 1)) + rng.normal(0, 0.25, (len(xb), 1, 1))
    xb = xb + rng.normal(0, 0.08, xb.shape)
    sh = rng.integers(-1, 2, len(xb))
    for i in np.nonzero(sh)[0]:
        xb[i] = np.roll(xb[i], sh[i], axis=0)
    return xb.astype(np.float32)


def train(X: np.ndarray, y: np.ndarray, w: np.ndarray, idx: np.ndarray, *, seed: int, epochs: int):
    import torch
    import torch.nn.functional as F

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    net = build_net()
    steps = len(idx) // 64 + 1
    opt = torch.optim.AdamW(net.parameters(), 2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 3e-3, total_steps=epochs * steps)
    p = w[idx] / w[idx].sum()
    for _ in range(epochs):
        net.train()
        for _ in range(steps):
            b = rng.choice(idx, 64, p=p)
            xb = torch.from_numpy(_aug(X[b], rng))[:, None]
            loss = F.binary_cross_entropy_with_logits(net(xb), torch.from_numpy(y[b].astype(np.float32)))
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    net.eval()
    return net


def predict(nets, X: np.ndarray, idx: np.ndarray) -> np.ndarray:
    import torch

    out = []
    with torch.no_grad():
        for s in range(0, len(idx), 512):
            x = torch.from_numpy(X[idx[s:s + 512]])[:, None]
            pr = [0.5 * (torch.sigmoid(n(x)) + torch.sigmoid(n(torch.flip(x, [3])))) for n in nets]
            out.append(torch.stack(pr).mean(0).numpy())
    return np.concatenate(out) if out else np.zeros(0)


def photo_report(meta: List[dict], prob: np.ndarray, threshold: float = 0.5) -> Dict[str, float]:
    """사진 단위 — 재확인 신호로 쓸 때와 개수를 직접 고칠 때."""
    ph: Dict[str, dict] = {}
    for m, p in zip(meta, prob):
        d = ph.setdefault(m["file"], {"truth": m["truth"], "count": m["count"]})
        if m["kind"] in ("lo_edge", "lo_out", "hi_edge", "hi_out"):
            d[m["kind"]] = float(p)
    n = len(ph)
    conf_ok = conf = review = review_wrong = fixed_ok = 0
    for d in ph.values():
        t = threshold
        dis = d["lo_edge"] < t or d["hi_edge"] < t or d["lo_out"] > 1 - t or d["hi_out"] > 1 - t
        right = d["count"] == d["truth"]
        if dis:
            review += 1
            review_wrong += not right
        else:
            conf += 1
            conf_ok += right
        c = d["count"]
        for s in ("lo", "hi"):
            if d[f"{s}_edge"] < t:
                c -= 1
            elif d[f"{s}_out"] > 1 - t:
                c += 1
        fixed_ok += c == d["truth"]
    base = sum(d["count"] == d["truth"] for d in ph.values())
    return {"photos": n, "count_correct": base, "confident": conf, "confident_correct": conf_ok,
            "confident_accuracy": round(conf_ok / max(1, conf), 4), "review": review,
            "review_rate": round(review / max(1, n), 4), "review_wrong": review_wrong,
            "silent_errors": conf - conf_ok, "silent_error_rate": round((conf - conf_ok) / max(1, n), 4),
            "baseline_silent_error_rate": round((n - base) / max(1, n), 4),
            "autocorrect_correct": fixed_ok}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cells", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--cv-block", type=int, default=50)
    ap.add_argument("--no-cv", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    import torch

    torch.set_num_threads(a.threads)
    d = np.load(a.cells)
    X = d["X"].astype(np.float32)
    meta = json.loads(str(d["meta"]))
    kind = [KIND[m["kind"]] for m in meta]
    y = np.array([0 if k in ("out1", "out2") else 1 for k in kind])
    w = np.array([WEIGHT[k] for k in kind])
    ok = np.array([bool(m["ok"]) for m in meta])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics: Dict[str, object] = {"cells": int(len(meta)), "photos": len({m["file"] for m in meta}),
                                  "train_photos": len({m["file"] for m in meta if m["ok"]})}
    if not a.no_cv:
        fold = np.array([_fold(m["file"], a.cv_block) for m in meta])
        prob = np.full(len(meta), np.nan)
        for f in (0, 1):
            tr = np.nonzero((fold == f) & ok)[0]
            te = np.nonzero(fold != f)[0]
            nets = [train(X, y, w, tr, seed=s, epochs=a.epochs) for s in range(a.seeds)]
            prob[te] = predict(nets, X, te)
            print(f"겹 {f}: 학습 칸 {len(tr)} → 다른 겹 칸 {len(te)} 예측", flush=True)
        cell = {}
        for k in ("edge", "edge2", "inner", "out1", "out2"):
            sel = [i for i in range(len(meta)) if kind[i] == k and ok[i]]
            cell[k] = round(float(np.mean((prob[sel] > 0.5) == y[sel])), 4) if sel else None
        rep = photo_report(meta, prob)
        metrics["cv"] = {"block": a.cv_block, "cell_accuracy": cell, **rep}
        print(json.dumps(metrics["cv"], ensure_ascii=False, indent=1))
    tr = np.nonzero(ok)[0]
    nets = [train(X, y, w, tr, seed=s, epochs=a.epochs) for s in range(a.seeds)]

    class Ens(torch.nn.Module):
        def __init__(self, ms):
            super().__init__()
            self.ms = torch.nn.ModuleList(ms)

        def forward(self, x):
            return torch.stack([m(x) for m in self.ms]).mean(0)

    ens = Ens(nets).eval()
    torch.onnx.export(ens, torch.zeros(2, 1, 32, 128), str(out / "edge.onnx"), input_names=["cells"],
                      output_names=["logit"], dynamic_axes={"cells": {0: "n"}, "logit": {0: "n"}}, opset_version=17,
                      dynamo=False)
    (out / "edge.json").write_text(json.dumps({"kind": "edge_cell", "threshold": 0.5, "input": [1, 32, 128],
                                               "train_photos": metrics["train_photos"]}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"모델: {out / 'edge.onnx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
