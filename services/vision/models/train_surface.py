"""표면 3점수 모델 학습 → ONNX(surface3) + 사이드카 (§6.3 점진 고도화, 2026-10-24).

    PYTHONPATH=<torch 경로> python -m vision.models.train_surface \
        --train <data>/train --test <data>/test --out runs/surface_v1 [--real-dir <현장 사진>]

입력: 부록 A.4 형식(raw/<CLS>/*.jpg + 사이드카 json). 라벨 배열 → 목표 [oil, discolor, scratch].
경계 샘플(border=true)은 부드러운 목표 0.6 — 옅은 불량을 '확실한 불량' 과 같은 무게로 가르치지 않는다.

모델: 작은 CNN(약 0.1M 파라미터) — 입력 3×64×512(튜브 몸통을 그대로 늘림), 전역 max+avg 풀링(작은 흠도
놓치지 않게) → 3 로짓. 파이 CPU 에서 수 ms. 산출물:
  <out>/surface.onnx + surface.json(사이드카: kind=surface3, apply_sigmoid) + metrics.json
배포: AIVIS_SURFACE_ONNX=<out>/surface.onnx (또는 models/surface.onnx). **기준정보 임계를 모델에 맞게**
(metrics.json 의 권장 임계) 바꿔야 한다 — 고전 CV 점수와 척도가 다르다.

torch 는 학습 환경에만 필요하다(추론은 onnxruntime). **직접 만든 데이터로 학습한 모델은 인수 증빙이 아니다.**
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

KEYS = ("oil", "discolor", "scratch")
CODE2IDX = {"OIL": 0, "DIS": 1, "SCR": 2}
H, W = 64, 512
MEAN = (0.45, 0.45, 0.45)
STD = (0.25, 0.25, 0.25)


def load_split(root: Path) -> Tuple[List[str], np.ndarray, np.ndarray]:
    paths, ys, borders = [], [], []
    for js in sorted(root.glob("raw/*/*.json")):
        img = js.with_suffix(".jpg")
        if not img.exists():
            continue
        side = json.loads(js.read_text(encoding="utf-8"))
        y = np.zeros(3, np.float32)
        border = bool(side.get("border"))
        for code in side.get("labels", []):
            if code in CODE2IDX:
                y[CODE2IDX[code]] = 0.6 if border else 1.0
        paths.append(str(img))
        ys.append(y)
        borders.append(border)
    return paths, np.stack(ys), np.array(borders)


def prep(img_bgr: np.ndarray) -> np.ndarray:
    """OnnxSurfaceModel 전처리와 같게: BGR→RGB, (H,W) 리사이즈, /255, (x−mean)/std, NCHW."""
    x = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    x = cv2.resize(x, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - np.array(MEAN, np.float32)) / np.array(STD, np.float32)
    return x.transpose(2, 0, 1)


def augment(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if rng.random() < 0.5:
        img = img[:, ::-1]
    if rng.random() < 0.5:
        img = img[::-1, :]
    h, w = img.shape[:2]
    if rng.random() < 0.5 and w > 40:                  # 길이 방향 일부(80~100%)
        cw = int(w * rng.uniform(0.8, 1.0))
        x0 = int(rng.integers(0, w - cw + 1))
        img = img[:, x0:x0 + cw]
    x = img.astype(np.float32) * rng.uniform(0.85, 1.15) + rng.uniform(-12, 12)
    if rng.random() < 0.5:                             # 감마(조명·노출 차)
        x = 255.0 * (np.clip(x, 0, 255) / 255.0) ** rng.uniform(0.8, 1.25)
    if rng.random() < 0.3:                             # 흐림(초점)
        x = cv2.GaussianBlur(x, (0, 0), rng.uniform(0.3, 1.0))
    if rng.random() < 0.3:                             # 잡음
        x = x + rng.normal(0, rng.uniform(1.0, 4.0), x.shape)
    return np.clip(x, 0, 255).astype(np.uint8)


def build_model():
    import torch.nn as nn

    def block(cin, cout):
        return nn.Sequential(nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                             nn.Conv2d(cout, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True))

    class SurfaceNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.f = nn.Sequential(block(3, 16), nn.MaxPool2d(2), block(16, 24), nn.MaxPool2d(2),
                                   block(24, 32), nn.MaxPool2d(2), block(32, 48), nn.MaxPool2d(2), block(48, 64))
            self.head = nn.Linear(128, 3)

        def forward(self, x):
            import torch
            z = self.f(x)
            g = torch.cat([z.amax(dim=(2, 3)), z.mean(dim=(2, 3))], dim=1)
            return self.head(g)

    return SurfaceNet()


def auroc(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    pos, neg = s[y > 0.5], s[y <= 0.5]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def evaluate(scores: np.ndarray, ys: np.ndarray, borders: np.ndarray, th: Sequence[float]) -> dict:
    out = {}
    for k, key in enumerate(KEYS):
        y = ys[:, k] > 0.3
        pred = scores[:, k] >= th[k]
        clear = ~borders
        out[key] = {
            "auroc": auroc(y.astype(float), scores[:, k]),
            "accuracy": float((pred == y).mean()),
            "accuracy_clear": float((pred[clear] == y[clear]).mean()) if clear.any() else None,
            "recall": float(pred[y].mean()) if y.any() else None,
            "false_alarm": float(pred[~y].mean()) if (~y).any() else None,
            "threshold": float(th[k]),
        }
    ok = ~(ys > 0.3).any(axis=1)
    out["ok_false_alarm"] = float(((scores >= np.array(th)).any(axis=1) & ok).sum() / max(1, ok.sum()))
    return out


def pick_thresholds(scores: np.ndarray, ys: np.ndarray, target_fa: float = 0.02) -> List[float]:
    """항목마다 정상(그 항목 음성)에서 오경보 target_fa 가 되는 임계(검증셋 기준)."""
    th = []
    for k in range(3):
        neg = scores[ys[:, k] <= 0.3, k]
        th.append(float(np.clip(np.quantile(neg, 1 - target_fa), 0.2, 0.95)) if len(neg) else 0.5)
    return th


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--train", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=24)
    ap.add_argument("--batch", type=int, default=48)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 스레드 수(0 이면 기본)")
    ap.add_argument("--real-ok", default=None, help="현장 정상 튜브 몸통 사진 폴더(오경보율 확인용, 학습에 안 씀)")
    a = ap.parse_args(argv)

    import torch
    import torch.nn.functional as F

    if a.threads:
        torch.set_num_threads(a.threads)
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tr_p, tr_y, tr_b = load_split(Path(a.train))
    te_p, te_y, te_b = load_split(Path(a.test))
    tr_img = [cv2.imread(p) for p in tr_p]
    te_x = torch.from_numpy(np.stack([prep(cv2.imread(p)) for p in te_p]))
    print(f"train {len(tr_p)}  test {len(te_p)}")
    model = build_model()
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * int(np.ceil(len(tr_p) / a.batch))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.15)
    pos = tr_y.mean(axis=0)
    pos_weight = torch.tensor(np.clip((1 - pos) / np.maximum(pos, 1e-3), 1.0, 6.0), dtype=torch.float32)
    t0 = time.time()
    hist = []
    for ep in range(a.epochs):
        model.train()
        idx = rng.permutation(len(tr_p))
        tot = 0.0
        for s in range(0, len(idx), a.batch):
            b = idx[s:s + a.batch]
            x = torch.from_numpy(np.stack([prep(augment(tr_img[i], rng)) for i in b]))
            y = torch.from_numpy(tr_y[b])
            loss = F.binary_cross_entropy_with_logits(model(x), y, pos_weight=pos_weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(b)
        model.eval()
        with torch.no_grad():
            sc = torch.sigmoid(model(te_x)).numpy()
        ev = evaluate(sc, te_y, te_b, [0.5, 0.5, 0.5])
        hist.append({"epoch": ep + 1, "loss": tot / len(idx), **{k: ev[k]["auroc"] for k in KEYS}})
        print(f"ep {ep + 1:2d} loss {tot / len(idx):.4f} auroc oil {ev['oil']['auroc']:.3f} dis {ev['discolor']['auroc']:.3f} "
              f"scr {ev['scratch']['auroc']:.3f}  ({time.time() - t0:.0f}s)", flush=True)
    model.eval()
    with torch.no_grad():
        sc = torch.sigmoid(model(te_x)).numpy()
    th = pick_thresholds(sc, te_y)
    metrics = {"test_at_0.5": evaluate(sc, te_y, te_b, [0.5, 0.5, 0.5]),
               "test_at_recommended": evaluate(sc, te_y, te_b, th), "recommended_thresholds": dict(zip(KEYS, th)),
               "train_samples": len(tr_p), "test_samples": len(te_p), "history": hist,
               "note": "직접 만든 데이터로 학습·평가 — 인수 증빙 아님. 실물 정답셋으로 임계를 다시 잡는다."}
    if a.real_ok:
        rp = sorted(Path(a.real_ok).glob("*.jpg"))
        if rp:
            rx = torch.from_numpy(np.stack([prep(cv2.imread(str(p))) for p in rp]))
            with torch.no_grad():
                rs = torch.sigmoid(model(rx)).numpy()
            metrics["real_ok"] = {"samples": len(rp),
                                  "false_alarm_at_recommended": float((rs >= np.array(th)).any(axis=1).mean()),
                                  "false_alarm_at_0.5": float((rs >= 0.5).any(axis=1).mean()),
                                  "score_p95": dict(zip(KEYS, np.percentile(rs, 95, axis=0).round(3).tolist()))}
    torch.save(model.state_dict(), out / "surface.pt")
    dummy = torch.zeros(1, 3, H, W)
    torch.onnx.export(model, dummy, str(out / "surface.onnx"), input_names=["input"], output_names=["logits"],
                      opset_version=17, dynamic_axes={"input": {0: "n"}, "logits": {0: "n"}})
    side = {"kind": "surface3", "input_size": [H, W], "layout": "NCHW", "color": "RGB", "scale": 1.0 / 255.0,
            "mean": list(MEAN), "std": list(STD), "apply_sigmoid": True, "outputs": {"oil": 0, "discolor": 1, "scratch": 2},
            "recommended_thresholds": dict(zip(KEYS, [round(t, 3) for t in th])),
            "trained_on": "직접 만든 데이터(vision.synth) — 인수 증빙 아님"}
    (out / "surface.json").write_text(json.dumps(side, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: metrics[k] for k in metrics if k != "history"}, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
