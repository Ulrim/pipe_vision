"""다발 맨 바깥 칸 확인 — 학습 모델(선택, 2026-11-14).

줄무늬 격자로 센 다발에서 남는 오류는 대부분 **다발 맨 바깥**이다: 트레이 벽·그늘을 한 칸 더 세거나(19 → 20),
맨 끝 튜브를 빠뜨린다(19 → 18). 맨 끝 튜브 칸 둘(양쪽)과 그 한 칸 밖(외삽) 둘, 모두 네 칸을 잘라 작은 CNN 이
'튜브인가' 를 본다. **셈과 모델이 다르면 개수를 고치지 않고 재확인으로 돌린다** — 휴대폰 트레이 사진 440장
교차검증(50장 블록 2겹)에서 모델로 개수를 직접 고치면 고친 만큼(14장) 다른 사진을 망가뜨렸고(12장), 불일치를
재확인 신호로만 쓰면 조용히 틀리는 사진이 10.2% → 2.5% 로 줄었다(재확인 11.4%). docs/TRAINING_DATA.md §9.

칸 잘라 내기(cell_crops) — 학습 도구(vision.tools.build_edge_cells)와 추론이 같은 함수를 쓴다:
- 칸마다 제 방향·길이·폭(원근으로 다발이 부채꼴이어도 칸이 튜브에 맞는다). 바깥 칸은 맨 끝 튜브를 이웃 간격만큼
  옮긴 것.
- 폭 2칸(앞뒤 반 칸씩 문맥) × 길이 1칸을 32×128 로. **바깥 방향이 항상 위** — 모델은 '위는 바깥, 아래는 다발'
  만 보면 된다.
- 밝기는 다발 안쪽 튜브(맨 끝 둘 제외) 화소의 평균·표준편차로 정규화(조명·노출 무관).

모델 파일은 저장소에 없다(현장·고객 사진으로 학습하므로 — 저장소가 공개다). `AIVIS_EDGE_MODEL=<edge.onnx>` 를
주면 켜지고, 없거나 onnxruntime 이 없거나 로드에 실패하면 **조용히 꺼진다**(종전 동작 그대로). 학습한 영역이
밝은 바닥(트레이) 사진이라 **밝은 배경에서만** 쓴다 — 현장 권장(무광 검정 바닥)에서는 학습 사진이 생기기 전까지
보지 않는다.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

CELL_H, CELL_W = 32, 128
#: 칸 순서(모델 입력 행 순서·학습 표본 이름)
KINDS = ("lo_edge", "lo_out", "hi_edge", "hi_out")
#: 이보다 튜브가 적으면 보지 않는다(안쪽 정규화 칸이 모자란다)
MIN_TUBES = 4


def _axis(q: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """사각형(왼위·오위·오아래·왼아래) → 중심선 양 끝 a, b 와 폭."""
    a, b = 0.5 * (q[0] + q[3]), 0.5 * (q[1] + q[2])
    wd = 0.5 * (float(np.linalg.norm(q[0] - q[3])) + float(np.linalg.norm(q[1] - q[2])))
    return a, b, wd


def _crop(gray: np.ndarray, c: np.ndarray, d: np.ndarray, length: float, width: float, up: int) -> np.ndarray:
    """중심 c, 길이 방향 d 인 칸을 폭 2칸 × 길이 1칸으로 잘라 CELL_H×CELL_W 로.

    up=+1 이면 칸 법선 n=(−d_y, d_x) 쪽이 바깥 — 출력의 위. 출력 (x, y) → 원본 c + (x − W/2)·sx·d + (y − H/2)·sy·v,
    v = −n·up(아래로 가면 다발 쪽).
    """
    n = np.array([-d[1], d[0]])
    v = -n * up
    sx, sy = length / CELL_W, 2.0 * width / CELL_H
    m = np.array([[d[0] * sx, v[0] * sy, c[0] - 0.5 * CELL_W * d[0] * sx - 0.5 * CELL_H * v[0] * sy],
                  [d[1] * sx, v[1] * sy, c[1] - 0.5 * CELL_W * d[1] * sx - 0.5 * CELL_H * v[1] * sy]])
    return cv2.warpAffine(gray, m, (CELL_W, CELL_H), flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def cell_crops(gray: np.ndarray, quads: Sequence[np.ndarray], *, inner_every: int = 0
               ) -> Optional[Tuple[np.ndarray, List[str]]]:
    """튜브 사각형들 → (칸 영상 (k, CELL_H, CELL_W) float32, 칸 이름들). 튜브가 MIN_TUBES 보다 적으면 None.

    기본은 KINDS 네 칸. 학습용으로 inner_every>0 이면 안쪽 칸(튜브, 양쪽 방향)과 한 칸 더 안쪽(lo_edge2/hi_edge2)·
    두 칸 밖(lo_out2/hi_out2)도 덧붙인다.
    """
    qs = [np.asarray(q, np.float64).reshape(4, 2) for q in quads if q is not None]
    if len(qs) < MIN_TUBES:
        return None
    g = gray.astype(np.float32)
    geo = [_axis(q) for q in qs]
    dirs = np.array([(b - a) / max(float(np.linalg.norm(b - a)), 1e-9) for a, b, _w in geo])
    # 방향 부호를 영상 기준으로 고정(+x 쪽, 세로면 +y 쪽) — 사각형 꼭짓점 방향·튜브 순서와 무관하게 lo/hi 가 같다
    ref = dirs[0] if abs(dirs[0][0]) >= abs(dirs[0][1]) and dirs[0][0] >= 0 or (
        abs(dirs[0][0]) < abs(dirs[0][1]) and dirs[0][1] >= 0) else -dirs[0]
    dirs = np.array([dd if dd @ ref >= 0 else -dd for dd in dirs])
    dv = np.median(dirs, axis=0)
    dv /= max(float(np.linalg.norm(dv)), 1e-9)
    nv = np.array([-dv[1], dv[0]])
    cen = np.array([0.5 * (a + b) for a, b, _w in geo])
    order = np.argsort(cen @ nv)
    qs = [qs[i] for i in order]
    geo = [geo[i] for i in order]
    dirs = dirs[order]
    cen = cen[order]
    n = len(qs)
    mask = np.zeros(g.shape, np.uint8)
    for q in qs[1:-1]:
        cv2.fillConvexPoly(mask, np.round(q).astype(np.int32), 1)
    vals = g[mask > 0]
    if vals.size < 16:
        return None
    mu, sd = float(vals.mean()), float(vals.std()) + 1e-3

    def one(k: int, up: int) -> np.ndarray:
        if 0 <= k < n:
            c, j = cen[k], k
        elif k < 0:
            c, j = cen[0] + (cen[0] - cen[1]) * (-k), 0
        else:
            c, j = cen[-1] + (cen[-1] - cen[-2]) * (k - n + 1), n - 1
        a, b, wd = geo[j]
        dd = dirs[j]
        # 법선 nv 의 + 쪽이 hi(정렬 순서 끝). 칸 자체의 법선이 nv 와 반대면 up 을 뒤집는다.
        nn_ = np.array([-dd[1], dd[0]])
        u = up if nn_ @ nv >= 0 else -up
        return (_crop(g, c, dd, float(np.linalg.norm(b - a)), wd, u) - mu) / sd

    cells, names = [], []
    # lo 쪽 바깥은 −nv, hi 쪽 바깥은 +nv
    for name, k, up in (("lo_edge", 0, -1), ("lo_out", -1, -1), ("hi_edge", n - 1, +1), ("hi_out", n, +1)):
        cells.append(one(k, up))
        names.append(name)
    if inner_every > 0:
        for name, k, up in (("lo_edge2", 1, -1), ("lo_out2", -2, -1), ("hi_edge2", n - 2, +1), ("hi_out2", n + 1, +1)):
            cells.append(one(k, up))
            names.append(name)
        for k in range(2, n - 2, inner_every):
            cells.append(one(k, -1))
            names.append("inner")
            cells.append(one(k, +1))
            names.append("inner")
    return np.stack(cells).astype(np.float32), names


@dataclass
class EdgeCheck:
    """네 칸의 '튜브일 확률'과 셈과의 불일치."""

    p_lo_edge: float
    p_lo_out: float
    p_hi_edge: float
    p_hi_out: float
    threshold: float = 0.5

    @property
    def disagree(self) -> bool:
        """맨 끝 칸이 튜브가 아니거나 한 칸 밖이 튜브로 보이면 — 셈과 모델이 다르다."""
        t = self.threshold
        return (self.p_lo_edge < t or self.p_hi_edge < t or self.p_lo_out > 1.0 - t or self.p_hi_out > 1.0 - t)

    def note(self) -> str:
        parts = []
        for side, pe, po in (("한쪽", self.p_lo_edge, self.p_lo_out), ("다른 쪽", self.p_hi_edge, self.p_hi_out)):
            if pe < self.threshold:
                parts.append(f"{side} 맨 끝 칸이 튜브로 보이지 않음({pe:.2f})")
            if po > 1.0 - self.threshold:
                parts.append(f"{side} 다발 바깥 한 칸에 튜브가 보임({po:.2f})")
        return "맨 바깥 칸 확인: " + ", ".join(parts) + " — 개수 재확인"


class EdgeChecker:
    """ONNX(입력 (N,1,CELL_H,CELL_W) float32 → 로짓 (N,)) — 좌우 뒤집은 칸과 평균(학습과 같은 TTA)."""

    def __init__(self, path: str):
        import onnxruntime as ort  # type: ignore

        self.path = path
        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.threshold = 0.5
        side = Path(path).with_suffix(".json")
        if side.exists():
            meta = json.loads(side.read_text(encoding="utf-8"))
            if meta.get("kind") not in (None, "edge_cell"):
                raise ValueError(f"사이드카 kind={meta.get('kind')!r} — edge_cell 모델이 아니다")
            self.threshold = float(meta.get("threshold", 0.5))

    def probs(self, cells: np.ndarray) -> np.ndarray:
        x = cells[:, None].astype(np.float32)
        lo = self.sess.run(None, {self.inp: x})[0].reshape(-1)
        lf = self.sess.run(None, {self.inp: np.ascontiguousarray(x[:, :, :, ::-1])})[0].reshape(-1)
        return 0.5 * (1.0 / (1.0 + np.exp(-lo)) + 1.0 / (1.0 + np.exp(-lf)))

    def check(self, gray: np.ndarray, quads: Sequence[np.ndarray]) -> Optional[EdgeCheck]:
        got = cell_crops(gray, quads)
        if got is None:
            return None
        p = self.probs(got[0])
        return EdgeCheck(float(p[0]), float(p[1]), float(p[2]), float(p[3]), threshold=self.threshold)


_LOCK = threading.Lock()
_CACHE: dict = {}


def default_checker() -> Optional[EdgeChecker]:
    """AIVIS_EDGE_MODEL 의 모델 — 없거나 실패하면 None(확인 끔). 경로마다 한 번만 연다."""
    path = os.environ.get("AIVIS_EDGE_MODEL", "").strip()
    if not path:
        return None
    with _LOCK:
        if path not in _CACHE:
            try:
                _CACHE[path] = EdgeChecker(path) if Path(path).is_file() else None
            except Exception:  # noqa: BLE001 — onnxruntime 미설치·손상 파일: 확인만 끈다
                _CACHE[path] = None
        return _CACHE[path]
