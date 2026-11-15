"""튜브 중심선 모델 학습 → ONNX(tube_ridge) + 사이드카 (2026-11-15).

    PYTHONPATH=<torch 경로> python -m vision.models.train_ridge --labels ridge_labels.json --out runs/ridge_v1 \\
        [--epochs 60] [--hold-fold 0 --cv-block 50]

입력: vision.tools.build_ridge_labels 의 JSON(개수가 정답과 같은 사진의 튜브 사각형, 원본 사진 좌표).
출력: <out>/ridge.onnx(입력 (1,3,H,W) RGB 0..1 → 출력 (1,2,H/2,W/2) 로짓[몸통, 중심선]) + ridge.json(kind=tube_ridge,
max_side). 배포: AIVIS_RIDGE_MODEL=<out>/ridge.onnx (multi/ridge_check.py).

- 모델: ResNet18 인코더(ImageNet 사전학습, timm `resnet18.a1_in1k` — 처음 한 번 내려받는다) + U-Net 디코더, 약 14M.
- 입력: 사진 긴 변 640px(추론과 같게). 목표: 사각형 몸통(채움)과 중심선(가로 가우시안, σ = 0.14폭).
- 증강: 0~360° 회전·0.7~1.35배·좌우 뒤집기·384px 자르기·밝기/대비/감마·흐림·JPEG. 그리고 **복사-붙이기** —
  사진 속 튜브를 띠로 잘라 빈 바닥에 1~4개(붙거나 떨어지게, 일부는 짧게) 붙인다. 다발 옆 낱개·짧은 튜브를
  라벨과 함께 보여 주기 위해서다(셈이 틀린 사진은 라벨이 없어 학습에 못 쓴다).
- 교차검증용: `--hold-fold k` 면 사진 이름 끝 번호를 `--cv-block` 장씩 묶은 블록 2겹 중 k 겹을 학습에서 뺀다.

torch·timm 은 학습 환경에만 필요하다(추론은 onnxruntime). **고객 사진으로 학습한 모델은 저장소에 넣지 않는다.**
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

MAX_SIDE = 640
CROP = 384
Seg = Tuple[np.ndarray, np.ndarray, float]


def fold_of(name: str, block: int) -> int:
    m = re.findall(r"\d+", Path(name).stem)
    return ((int(m[-1]) - 1) // block) % 2 if m else 0


def load(path: str) -> Tuple[np.ndarray, float]:
    im = cv2.imread(path)
    s = MAX_SIDE / max(im.shape[:2])
    if s != 1:
        im = cv2.resize(im, (int(round(im.shape[1] * s)), int(round(im.shape[0] * s))), interpolation=cv2.INTER_AREA)
    return im, s


def render(shape, segs: List[Seg], stride: int = 2) -> Tuple[np.ndarray, np.ndarray]:
    """세그먼트(입력 좌표) → (몸통, 중심선) 목표, 입력/stride 해상도."""
    hh, ww = shape[0] // stride, shape[1] // stride
    body = np.zeros((hh, ww), np.float32)
    ridge = np.zeros((hh, ww), np.float32)
    for a, b, w in segs:
        a, b, w = a / stride, b / stride, w / stride
        d = b - a
        ln = float(np.linalg.norm(d))
        if ln < 2 or w < 0.8:
            continue
        u = d / ln
        n = np.array([-u[1], u[0]])
        pts = np.array([a + n * w / 2, b + n * w / 2, b - n * w / 2, a - n * w / 2])
        cv2.fillPoly(body, [np.round(pts * 4).astype(np.int32)], 1.0, lineType=cv2.LINE_AA, shift=2)
        sig = max(0.7, 0.14 * w)
        r = 3 * sig + 1
        x0, x1 = int(max(0, math.floor(min(a[0], b[0]) - r))), int(min(ww - 1, math.ceil(max(a[0], b[0]) + r)))
        y0, y1 = int(max(0, math.floor(min(a[1], b[1]) - r))), int(min(hh - 1, math.ceil(max(a[1], b[1]) + r)))
        if x1 < x0 or y1 < y0:
            continue
        yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1].astype(np.float32)
        px, py = xx - a[0], yy - a[1]
        t = px * u[0] + py * u[1]
        perp = np.abs(px * n[0] + py * n[1])
        g = np.exp(-perp ** 2 / (2 * sig ** 2)) * ((t >= 0) & (t <= ln))
        sub = ridge[y0:y1 + 1, x0:x1 + 1]
        np.maximum(sub, g, out=sub)
    np.clip(body, 0, 1, out=body)
    return body, ridge


def _ribbon(img, a, b, w):
    d = b - a
    ln = float(np.linalg.norm(d))
    u = d / max(ln, 1e-6)
    n = np.array([-u[1], u[0]])
    ww, hh = int(round(ln)), int(round(w * 1.15))
    if ww < 10 or hh < 3:
        return None
    c = 0.5 * (a + b)
    m = np.array([[u[0], n[0], c[0] - 0.5 * ww * u[0] - 0.5 * hh * n[0]],
                  [u[1], n[1], c[1] - 0.5 * ww * u[1] - 0.5 * hh * n[1]]])
    return cv2.warpAffine(img, m, (ww, hh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def _paste(img, body, rib, c, ang, w) -> Optional[Seg]:
    hh, ww = rib.shape[:2]
    u = np.array([math.cos(ang), math.sin(ang)])
    n = np.array([-u[1], u[0]])
    m = np.array([[u[0], n[0], c[0] - 0.5 * ww * u[0] - 0.5 * hh * n[0]],
                  [u[1], n[1], c[1] - 0.5 * ww * u[1] - 0.5 * hh * n[1]]], np.float32)
    ih, iw = img.shape[:2]
    a, b = c - 0.5 * ww * u, c + 0.5 * ww * u
    for p in (a + n * 0.5 * hh, a - n * 0.5 * hh, b + n * 0.5 * hh, b - n * 0.5 * hh):
        if not (2 <= p[0] < iw - 2 and 2 <= p[1] < ih - 2):
            return None
    mk = np.zeros((hh, ww), np.float32)
    e = max(1, int(0.12 * hh))
    mk[e:hh - e, :] = 1
    mk = cv2.GaussianBlur(mk, (0, 0), max(0.6, 0.06 * hh))
    wm = cv2.warpAffine(mk, m, (iw, ih), flags=cv2.INTER_LINEAR)
    sel = wm > 0.3
    if not sel.any() or (body[sel] > 0.5).mean() > 0.03:
        return None
    wr = cv2.warpAffine(rib, m, (iw, ih), flags=cv2.INTER_LINEAR)
    img[:] = (img * (1 - wm[..., None]) + wr * wm[..., None]).astype(np.uint8)
    body[sel] = 1
    return a, b, w


def copy_paste(img, segs: List[Seg], rng) -> List[Seg]:
    if len(segs) < 2:
        return segs
    body = np.zeros(img.shape[:2], np.float32)
    for a, b, w in segs:
        d = b - a
        u = d / max(float(np.linalg.norm(d)), 1e-6)
        n = np.array([-u[1], u[0]])
        cv2.fillPoly(body, [np.array([a + n * w * 0.6, b + n * w * 0.6, b - n * w * 0.6, a - n * w * 0.6]).astype(np.int32)], 1.0)
    base = float(np.median(np.unwrap([math.atan2((b - a)[1], (b - a)[0]) for a, b, _ in segs])))
    out = list(segs)
    for _ in range(rng.integers(1, 3)):
        k = int(rng.integers(1, 5))
        src = [segs[i] for i in rng.choice(len(segs), size=k, replace=True)]
        frac = 1.0 if rng.random() < 0.6 else rng.uniform(0.35, 1.0)
        ang = base + rng.normal(0, 0.08) if rng.random() < 0.75 else rng.uniform(0, math.pi)
        if rng.random() < 0.5:
            ang += math.pi
        w0 = float(np.median([s[2] for s in src]))
        gap = 1.0 if rng.random() < 0.6 else rng.uniform(1.3, 3.0)
        c0 = np.array([rng.uniform(0.1, 0.9) * img.shape[1], rng.uniform(0.1, 0.9) * img.shape[0]])
        n = np.array([-math.sin(ang), math.cos(ang)])
        for j, (a, b, w) in enumerate(src):
            if frac < 1:
                b = a + (b - a) * frac
            rib = _ribbon(img, a, b, w)
            if rib is None:
                break
            r = _paste(img, body, rib, c0 + n * j * w0 * gap, ang, w)
            if r is None:
                break
            out.append(r)
    return out


def augment(img, segs: List[Seg], rng):
    h, w = img.shape[:2]
    if rng.random() < 0.5:
        segs = copy_paste(img, segs, rng)
    ang = rng.uniform(0, 360)
    sc = math.exp(rng.uniform(-0.35, 0.3))
    side = int(math.ceil(math.hypot(h, w) * sc)) + 4
    m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, sc)
    m[:, 2] += (side / 2 - w / 2, side / 2 - h / 2)
    im = cv2.warpAffine(img, m, (side, side), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
    flip = rng.random() < 0.5
    if flip:
        im = im[:, ::-1].copy()
    ns = []
    for a, b, wd in segs:
        aa, bb = m[:, :2] @ a + m[:, 2], m[:, :2] @ b + m[:, 2]
        if flip:
            aa, bb = np.array([side - 1 - aa[0], aa[1]]), np.array([side - 1 - bb[0], bb[1]])
        ns.append((aa, bb, wd * sc))
    cx = side / 2 + rng.uniform(-0.25, 0.25) * min(h, w) * sc
    cy = side / 2 + rng.uniform(-0.25, 0.25) * min(h, w) * sc
    x0 = int(np.clip(cx - CROP / 2, 0, max(0, side - CROP)))
    y0 = int(np.clip(cy - CROP / 2, 0, max(0, side - CROP)))
    im = im[y0:y0 + CROP, x0:x0 + CROP]
    if im.shape[0] < CROP or im.shape[1] < CROP:
        im = cv2.copyMakeBorder(im, 0, CROP - im.shape[0], 0, CROP - im.shape[1], cv2.BORDER_REFLECT_101)
    off = np.array([x0, y0], np.float64)
    ns = [(a - off, b - off, wd) for a, b, wd in ns]
    f = im.astype(np.float32) * rng.uniform(0.7, 1.3) + rng.uniform(-25, 25)
    f = f * (1 + rng.normal(0, 0.05, 3))[None, None]
    if rng.random() < 0.3:
        f = 255 * (np.clip(f, 0, 255) / 255) ** rng.uniform(0.7, 1.4)
    im = np.clip(f, 0, 255).astype(np.uint8)
    if rng.random() < 0.3:
        im = cv2.GaussianBlur(im, (0, 0), rng.uniform(0.5, 1.5))
    if rng.random() < 0.3:
        im = cv2.imdecode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(40, 90))])[1], 1)
    return im, ns


def build_net(pretrained: bool = True):
    import timm
    import torch
    import torch.nn as nn

    class Up(nn.Module):
        def __init__(self, cin, cskip, cout):
            super().__init__()
            self.c = nn.Sequential(nn.Conv2d(cin + cskip, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout),
                                   nn.ReLU(inplace=True), nn.Conv2d(cout, cout, 3, padding=1, bias=False),
                                   nn.BatchNorm2d(cout), nn.ReLU(inplace=True))

        def forward(self, x, s):
            x = nn.functional.interpolate(x, size=s.shape[-2:], mode="bilinear", align_corners=False)
            return self.c(torch.cat([x, s], 1))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc = timm.create_model("resnet18.a1_in1k", pretrained=pretrained, features_only=True)
            ch = self.enc.feature_info.channels()
            self.u4, self.u3 = Up(ch[4], ch[3], 192), Up(192, ch[2], 96)
            self.u2, self.u1 = Up(96, ch[1], 64), Up(64, ch[0], 48)
            self.head = nn.Conv2d(48, 2, 1)
            self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

        def forward(self, x):
            f = self.enc((x - self.mean) / self.std)
            y = self.u1(self.u2(self.u3(self.u4(f[4], f[3]), f[2]), f[1]), f[0])
            return self.head(y)

    return Net()


def seg_of(q) -> Seg:
    from vision.multi.ridge_check import quad_seg

    return quad_seg(np.asarray(q, np.float64))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--hold-fold", type=int, default=-1, help="교차검증: 이 겹(0/1)을 학습에서 뺀다")
    ap.add_argument("--cv-block", type=int, default=50)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    import torch

    torch.set_num_threads(a.threads)
    rows = [r for r in json.loads(Path(a.labels).read_text(encoding="utf-8")) if r.get("ok")]
    if a.hold_fold >= 0:
        rows = [r for r in rows if fold_of(r["file"], a.cv_block) != a.hold_fold]
    data = []
    for r in rows:
        im, s = load(r["file"])
        data.append((im, [tuple(x * s for x in seg_of(q)) for q in r["quads"]]))
    print(f"학습 사진 {len(data)}", flush=True)
    rng = np.random.default_rng(1000 + a.seed + max(a.hold_fold, 0))
    torch.manual_seed(a.seed + max(a.hold_fold, 0))
    net = build_net(True)
    enc_ids = {id(p) for p in net.enc.parameters()}
    dec = [p for p in net.parameters() if id(p) not in enc_ids]
    opt = torch.optim.AdamW([{"params": list(net.enc.parameters()), "lr": 3e-4}, {"params": dec, "lr": 1.5e-3}],
                            weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(data) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[3e-4, 1.5e-3], total_steps=steps, pct_start=0.1)
    bce = torch.nn.BCEWithLogitsLoss(reduction="none")
    t0 = time.time()
    for ep in range(a.epochs):
        net.train()
        tot = 0.0
        order = rng.permutation(len(data))
        for i in range(0, len(order), a.batch):
            xs, ys = [], []
            for j in order[i:i + a.batch]:
                im, segs = data[j]
                aim, asegs = augment(im.copy(), list(segs), rng)
                body, ridge = render(aim.shape, asegs)
                xs.append(aim[..., ::-1].transpose(2, 0, 1) / 255.0)
                ys.append(np.stack([body, ridge]))
            x = torch.tensor(np.stack(xs), dtype=torch.float32)
            y = torch.tensor(np.stack(ys), dtype=torch.float32)
            o = net(x)
            loss = bce(o[:, 0], y[:, 0]).mean() + 2 * (bce(o[:, 1], y[:, 1]) * (1 + 8 * y[:, 1])).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.item())
        print(f"ep {ep} loss {tot / math.ceil(len(order) / a.batch):.4f} {time.time() - t0:.0f}s", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    net.eval()
    torch.onnx.export(net, torch.rand(1, 3, 480, 640), str(out / "ridge.onnx"), input_names=["image"],
                      output_names=["maps"], opset_version=17,
                      dynamic_axes={"image": {2: "h", 3: "w"}, "maps": {2: "h2", 3: "w2"}}, dynamo=False)
    (out / "ridge.json").write_text(json.dumps({"kind": "tube_ridge", "max_side": MAX_SIDE, "stride": 2,
                                                "outputs": ["body", "ridge"], "train_photos": len(data),
                                                "hold_fold": a.hold_fold, "cv_block": a.cv_block},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"모델: {out / 'ridge.onnx'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
