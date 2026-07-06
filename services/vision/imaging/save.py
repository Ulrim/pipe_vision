"""검사 이미지 디스크 저장 + 결과 오버레이 렌더 (M7 일부, §6.4).

판정만 하고 끝나던 파이프라인을 보완해, 원본(raw)과 결과 오버레이(result)
이미지를 로컬 파일시스템에 저장하고 그 상대경로를 검사결과에 싣는다.
MinIO/S3 업로드는 범위 밖(backend 책임) — 여기서는 로컬 디스크만 다룬다.

계약(backend/devops 합의 — 절대 변경 금지):
- AIVIS_IMAGES_DIR (기본 /data/images). 하위 raw/ result/ review/ 자동 생성.
- 파일명(§6.4): {LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg (ms 3자리).
- 반환 경로는 AIVIS_IMAGES_DIR 기준 상대경로(절대경로 금지 — 서버가 base 와 join).
- review_flag=True 면 result 사본을 review/ 에도 추가 기록.

모든 렌더/파일명은 결정적이다(동일 입력 → 동일 출력). 라벨은 영문/코드만
사용한다(OpenCV HERSHEY 폰트가 한글을 렌더하지 못해 깨짐 방지).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

import cv2
import numpy as np

from .storage import (
    StorageBackend,
    StorageSettings,
    build_backend,
    encode_jpeg,
)

log = logging.getLogger("aivis.vision.imaging")

DEFAULT_IMAGES_DIR = "/data/images"

# 하위 버킷(§6.4).
_RAW = "raw"
_RESULT = "result"
_REVIEW = "review"

# 색약 고려: 색 + 텍스트 기호 이중표기. BGR.
_GREEN = (60, 180, 75)   # OK
_RED = (40, 40, 220)     # NG
_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)

_FONT = cv2.FONT_HERSHEY_SIMPLEX

# JPEG 품질 고정 → 결정적 바이트.
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 95]

# 파일명 안전화: 경로/구분 문자를 '_' 로. 영숫자/-/_ 만 허용.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_token(value: str, *, fallback: str = "NA") -> str:
    """LOT/Item 등 파일명 토큰 안전화. 경로 문자/공백 제거."""
    if value is None:
        return fallback
    token = _UNSAFE.sub("_", str(value).strip())
    token = token.strip("._-")
    return token or fallback


def _safe_verdict(verdict: str) -> str:
    """verdict 는 OK|NG 만 허용. 그 외엔 NG 로 안전화(애매=NG)."""
    v = str(verdict).strip().upper()
    return "OK" if v == "OK" else "NG"


def build_filename(
    lot: str,
    item: str,
    ts: datetime,
    verdict: str,
) -> str:
    """§6.4 규칙: {LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg (ms 3자리)."""
    stamp = f"{ts:%Y%m%d%H%M%S}{ts.microsecond // 1000:03d}"
    return (
        f"{_safe_token(lot)}_{_safe_token(item)}_{stamp}_{_safe_verdict(verdict)}.jpg"
    )


def _ensure_dirs(images_dir: str) -> Path:
    base = Path(images_dir)
    for sub in (_RAW, _RESULT, _REVIEW):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


def _imwrite(path: Path, image: np.ndarray) -> None:
    """cv2.imwrite 래퍼. 실패 시 OSError 로 승격(호출자가 graceful 처리)."""
    ok = cv2.imwrite(str(path), image, _JPEG_PARAMS)
    if not ok:
        raise OSError(f"cv2.imwrite 실패: {path}")


def save_raw(
    frame: np.ndarray,
    images_dir: str,
    lot: str,
    item: str,
    ts: datetime,
    verdict: str,
) -> str:
    """원본 프레임을 raw/ 에 저장하고 images_dir 기준 상대경로를 반환한다."""
    base = _ensure_dirs(images_dir)
    fname = build_filename(lot, item, ts, verdict)
    _imwrite(base / _RAW / fname, frame)
    return f"{_RAW}/{fname}"


# --- 오버레이 렌더 ---

def _fmt_num(value: Optional[float], suffix: str = "") -> str:
    if value is None:
        return "--"
    return f"{value:.3f}{suffix}"


def _score_line(label: str, score: Optional[float], threshold: Optional[float]) -> str:
    s = "--" if score is None else f"{score:.3f}"
    t = "" if threshold is None else f"/{threshold:.3f}"
    return f"{label}: {s}{t}"


def render_overlay(frame: np.ndarray, result, *, item=None) -> np.ndarray:
    """원본 위에 판정 결과를 시각화한 BGR 이미지를 반환한다(결정적).

    result: VerdictResult (length/surface/final_verdict/defect_codes/confidence).
    item:   ItemMaster(optional) — 임계값을 함께 표기해 현장 판독 보조.

    표기:
      - 최종 OK/NG: 색(초록/빨강) + 텍스트 기호([OK]/[NG], 색약 고려).
      - 길이: 측정/기준/편차(mm).
      - 표면 점수: OIL/DIS/SCR (임계값 동반 가능).
      - 불량코드: LEN/OIL/DIS/SCR/MULTI 배열.
      - review 대상이면 REVIEW 배지.
    그리기는 OpenCV 만 사용하며 한글을 쓰지 않는다(폰트 깨짐 방지).
    """
    if frame is None or getattr(frame, "ndim", 0) != 3:
        raise ValueError("render_overlay: BGR 3채널 이미지가 필요하다")

    canvas = frame.copy()
    h, w = canvas.shape[:2]

    final = str(getattr(result, "final_verdict", "NG")).upper()
    is_ok = final == "OK"
    color = _GREEN if is_ok else _RED
    symbol = "[OK]" if is_ok else "[NG]"

    # 1) 외곽 테두리(색약 고려: 두께 + 색으로 즉시 식별).
    cv2.rectangle(canvas, (1, 1), (w - 2, h - 2), color, thickness=max(3, h // 100))

    # 2) 상단 헤더 바 + 판정 텍스트.
    bar_h = max(34, h // 9)
    cv2.rectangle(canvas, (0, 0), (w, bar_h), color, thickness=-1)
    scale = max(0.6, bar_h / 48.0)
    cv2.putText(
        canvas,
        f"{symbol} VERDICT: {final}",
        (10, int(bar_h * 0.7)),
        _FONT,
        scale,
        _WHITE,
        thickness=2,
        lineType=cv2.LINE_AA,
    )

    conf = getattr(result, "confidence", None)
    if conf is not None:
        conf_txt = f"conf {conf:.2f}"
        (tw, _th), _ = cv2.getTextSize(conf_txt, _FONT, scale * 0.7, 2)
        cv2.putText(
            canvas,
            conf_txt,
            (max(10, w - tw - 12), int(bar_h * 0.7)),
            _FONT,
            scale * 0.7,
            _WHITE,
            thickness=2,
            lineType=cv2.LINE_AA,
        )

    # 3) 좌하단 상세 패널(반투명 배경에 텍스트 줄).
    length = getattr(result, "length", None)
    surface = getattr(result, "surface", None)
    codes: Sequence = getattr(result, "defect_codes", []) or []
    code_str = ",".join(str(c) for c in codes) if codes else "-"

    oil_t = getattr(item, "oil_threshold", None) if item is not None else None
    dis_t = getattr(item, "discolor_threshold", None) if item is not None else None
    scr_t = getattr(item, "scratch_threshold", None) if item is not None else None

    lines: List[str] = []
    if length is not None:
        meas = getattr(length, "meas_length_mm", None)
        ref = getattr(length, "ref_length_mm", None)
        dev = getattr(length, "deviation_mm", None)
        lv = str(getattr(length, "length_verdict", "")).upper()
        edge = getattr(length, "edge_detected", True)
        edge_txt = "" if edge else " (no-edge)"
        lines.append(
            f"LEN {lv}: meas {_fmt_num(meas)} ref {_fmt_num(ref)} "
            f"dev {_fmt_num(dev)}{edge_txt}"
        )
    if surface is not None:
        lines.append(_score_line("OIL", getattr(surface, "oil_score", None), oil_t))
        lines.append(
            _score_line("DIS", getattr(surface, "discolor_score", None), dis_t)
        )
        lines.append(
            _score_line("SCR", getattr(surface, "scratch_score", None), scr_t)
        )
    lines.append(f"DEFECTS: {code_str}")

    txt_scale = max(0.45, w / 1400.0)
    line_h = int(26 * txt_scale) + 6
    pad = 8
    panel_h = line_h * len(lines) + pad * 2
    panel_w = w
    y0 = h - panel_h
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, y0), (panel_w, h), _BLACK, thickness=-1)
    cv2.addWeighted(overlay, 0.55, canvas, 0.45, 0, canvas)

    y = y0 + pad + line_h - 6
    for line in lines:
        cv2.putText(
            canvas,
            line,
            (10, y),
            _FONT,
            txt_scale,
            _WHITE,
            thickness=1,
            lineType=cv2.LINE_AA,
        )
        y += line_h

    # 4) 재확인 배지(우상단, 헤더 아래).
    if getattr(result, "review_flag", False):
        badge = "REVIEW"
        (bw, bh), _ = cv2.getTextSize(badge, _FONT, txt_scale, 2)
        bx1 = w - 8
        bx0 = bx1 - bw - 12
        by0 = bar_h + 6
        by1 = by0 + bh + 10
        cv2.rectangle(canvas, (bx0, by0), (bx1, by1), (0, 200, 255), thickness=-1)
        cv2.putText(
            canvas,
            badge,
            (bx0 + 6, by1 - 6),
            _FONT,
            txt_scale,
            _BLACK,
            thickness=2,
            lineType=cv2.LINE_AA,
        )

    return canvas


def render_batch_overlay(frame: np.ndarray, batch_result) -> np.ndarray:
    """다중 튜브 배치 결과를 원본 위에 시각화한 BGR 이미지 반환(결정적).

    batch_result: multi.BatchResult (duck-typed) — .tubes[*].bbox/index/
    final_verdict/defect_codes/length_mm, .count_detected/.count_expected/
    .count_mismatch/.batch_verdict/.ng_count.

    표기(render_overlay 시각 규약과 일관 — 색약 고려 색+기호+두께):
      - 튜브별 bbox 사각형: OK=초록(얇게), NG=빨강(두껍게).
      - 각 튜브 근처: '#N [OK/NG]' + 불량코드(LEN/OIL/DIS/SCR) + 길이(mm).
      - 상단 헤더바: 배치 OK/NG + 검출개수 vs 기대개수, 불일치 시 MISMATCH 경고.
    OpenCV 그리기만 사용하며 한글을 쓰지 않는다(폰트 깨짐 방지).
    """
    if frame is None or getattr(frame, "ndim", 0) != 3:
        raise ValueError("render_batch_overlay: BGR 3채널 이미지가 필요하다")

    canvas = frame.copy()
    h, w = canvas.shape[:2]
    tubes = list(getattr(batch_result, "tubes", []) or [])
    txt_scale = max(0.4, w / 1600.0)

    # 1) 튜브별 박스 + 라벨.
    for t in tubes:
        bbox = getattr(t, "bbox", None)
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = (int(v) for v in bbox)
        tv = str(getattr(t, "final_verdict", "NG")).upper()
        is_ok = tv == "OK"
        color = _GREEN if is_ok else _RED
        # 색약 고려: NG 는 더 두꺼운 테두리로도 구분.
        thick = 2 if is_ok else 4
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), color, thickness=thick)

        idx = getattr(t, "index", 0)
        sym = "OK" if is_ok else "NG"
        codes = getattr(t, "defect_codes", []) or []
        code_str = ",".join(str(c) for c in codes) if codes else ""
        length_mm = getattr(t, "length_mm", None)
        len_str = "" if length_mm is None else f" {length_mm:.1f}mm"
        label = f"#{idx} [{sym}]{(' ' + code_str) if code_str else ''}{len_str}"

        (lw, lh), _ = cv2.getTextSize(label, _FONT, txt_scale, 1)
        # 라벨은 박스 상단 내부(공간 없으면 하단)로.
        ly = y0 + lh + 4
        if ly + 2 > y1:
            ly = max(lh + 2, y0 - 4)
        lx = min(x0 + 3, max(0, w - lw - 4))
        # 가독성 배경 박스.
        cv2.rectangle(
            canvas,
            (lx - 2, ly - lh - 3),
            (lx + lw + 2, ly + 3),
            _BLACK,
            thickness=-1,
        )
        cv2.putText(
            canvas, label, (lx, ly), _FONT, txt_scale, color,
            thickness=1, lineType=cv2.LINE_AA,
        )

    # 2) 상단 헤더바(배치 요약).
    batch_v = str(getattr(batch_result, "batch_verdict", "NG")).upper()
    is_ok = batch_v == "OK"
    hdr_color = _GREEN if is_ok else _RED
    sym = "[OK]" if is_ok else "[NG]"
    detected = getattr(batch_result, "count_detected", len(tubes))
    expected = getattr(batch_result, "count_expected", None)
    ng_count = getattr(batch_result, "ng_count", 0)
    mismatch = bool(getattr(batch_result, "count_mismatch", False))

    bar_h = max(30, h // 12)
    cv2.rectangle(canvas, (0, 0), (w, bar_h), hdr_color, thickness=-1)
    exp_txt = "?" if expected is None else str(expected)
    hdr = (
        f"{sym} BATCH: {batch_v}  tubes {detected}/{exp_txt}  NG {ng_count}"
    )
    hscale = max(0.5, bar_h / 44.0)
    cv2.putText(
        canvas, hdr, (10, int(bar_h * 0.72)), _FONT, hscale, _WHITE,
        thickness=2, lineType=cv2.LINE_AA,
    )
    if mismatch:
        warn = "! COUNT MISMATCH"
        (ww, _wh), _ = cv2.getTextSize(warn, _FONT, hscale, 2)
        cv2.putText(
            canvas, warn, (max(10, w - ww - 12), int(bar_h * 0.72)),
            _FONT, hscale, (0, 220, 255), thickness=2, lineType=cv2.LINE_AA,
        )

    return canvas


def save_result(
    overlay: np.ndarray,
    images_dir: str,
    lot: str,
    item: str,
    ts: datetime,
    verdict: str,
    *,
    review_flag: bool = False,
) -> str:
    """결과 오버레이를 result/ 에 저장(필요시 review/ 사본).

    반환: result/ 상대경로. review_flag=True 면 동일 파일명을 review/ 에도 복제
    기록한다(오검·미검 분리 — §6.4). review 사본 실패는 무시한다(주 경로 우선).
    """
    base = _ensure_dirs(images_dir)
    fname = build_filename(lot, item, ts, verdict)
    _imwrite(base / _RESULT / fname, overlay)
    if review_flag:
        try:
            _imwrite(base / _REVIEW / fname, overlay)
        except OSError:
            # review 사본은 보조 — 주 result 저장이 성공했으면 막지 않는다.
            pass
    return f"{_RESULT}/{fname}"


@dataclass(frozen=True)
class ImageSaveResult:
    """이미지 저장 결과 묶음. 경로는 images_dir 기준 상대경로(없으면 None).

    pending_images: 원격 업로드에 실패해 스풀(디스크 버퍼)로 우회한 키 목록.
    키는 결정적이므로 나중 업로드로 그대로 복구된다(경로는 payload 에 유지).
    """

    raw_image_path: Optional[str] = None
    result_image_path: Optional[str] = None
    error: Optional[str] = None
    pending_images: tuple[str, ...] = ()


def _put_or_spool(
    backend: StorageBackend,
    key: str,
    jpeg: bytes,
    pending_sink,
    pending: list,
) -> str:
    """backend.put 시도 → 실패 시 pending_sink(스풀)에 바이트 보존 후 키 유지.

    sink 마저 없으면(또는 sink 저장 실패) 예외를 그대로 올린다(기존 graceful
    경로 — 경로 None 으로 POST). sink 성공 시 키를 pending 목록에 추가한다.
    """
    try:
        return backend.put(key, jpeg)
    except Exception as exc:  # noqa: BLE001
        if pending_sink is None:
            raise
        saved = pending_sink(key, jpeg)
        if saved is None:
            raise
        log.warning("이미지 업로드 실패(%s) → 스풀 보존: %s", exc, key)
        pending.append(key)
        return key


def _save_via_backend(
    backend: StorageBackend,
    frame: np.ndarray,
    result,
    *,
    lot: str,
    item_code: str,
    ts: datetime,
    verdict: str,
    review: bool,
    item,
    pending_sink=None,
) -> ImageSaveResult:
    """JPEG 인코딩 후 스토리지 백엔드(supabase 등)에 업로드.

    반환하는 상대경로(키)는 로컬 모드와 동일하다: raw/<name>, result/<name>.
    review 면 review/<name> 키도 업로드(보조 — 실패해도 주 경로는 유지).
    pending_sink((key, jpeg) -> str|None)가 있으면 업로드 실패 시 바이트를
    스풀에 보존하고 키를 pending_images 로 보고한다(경로는 그대로 유지).
    """
    fname = build_filename(lot, item_code, ts, verdict)
    raw_key = f"{_RAW}/{fname}"
    result_key = f"{_RESULT}/{fname}"

    pending: list = []
    raw_path = _put_or_spool(
        backend, raw_key, encode_jpeg(frame), pending_sink, pending
    )
    overlay = render_overlay(frame, result, item=item)
    overlay_jpeg = encode_jpeg(overlay)
    result_path = _put_or_spool(
        backend, result_key, overlay_jpeg, pending_sink, pending
    )
    if review:
        try:
            backend.put(f"{_REVIEW}/{fname}", overlay_jpeg)
        except Exception as exc:  # noqa: BLE001
            # review 사본은 보조 — 주 result 업로드가 성공했으면 막지 않는다.
            log.warning("review 사본 업로드 실패(무시): %s", exc)
    return ImageSaveResult(
        raw_image_path=raw_path,
        result_image_path=result_path,
        pending_images=tuple(pending),
    )


def save_inspection_images(
    frame: np.ndarray,
    result,
    *,
    images_dir: Optional[str] = None,
    lot: str,
    item_code: str,
    inspected_at: Optional[datetime] = None,
    item=None,
    storage: Optional[StorageBackend] = None,
    pending_sink=None,
) -> ImageSaveResult:
    """raw + result 저장 일괄 처리(워커 통합 진입점).

    파일명 타임스탬프는 inspected_at(검사 시각)과 동일해 raw/result 가 짝을 이룬다.
    저장 백엔드는 AIVIS_STORAGE_BACKEND(local|supabase)로 전환한다:
      - local(기본): images_dir 하위 raw/ result/ review/ 디스크 저장(기존 동작).
      - supabase: JPEG 인코딩 후 Supabase Storage REST 업로드. 키(상대경로) 동일.
    storage 백엔드를 직접 주입하면 env 분기를 건너뛴다(테스트/통합용).
    pending_sink((key, jpeg) -> str|None)가 있으면 원격 업로드 실패 시 바이트를
    스풀에 보존하고 pending_images 로 보고한다(오프라인 대비 — 워커 스풀 연동).

    I/O 실패는 잡아서 error 에 담고 경로는 None 으로 둔다(검사결과 적재를 절대
    막지 않는다 — 워커는 경로 None 이라도 POST 한다 — graceful 정책 유지).
    """
    target_dir = images_dir or os.environ.get("AIVIS_IMAGES_DIR") or DEFAULT_IMAGES_DIR
    ts = inspected_at or datetime.now(timezone.utc)
    verdict = str(getattr(result, "final_verdict", "NG"))
    review = bool(getattr(result, "review_flag", False))

    backend = storage
    if backend is None:
        settings = StorageSettings.from_env(images_dir=target_dir)
        if settings.is_supabase:
            backend = build_backend(settings)

    raw_path: Optional[str] = None
    result_path: Optional[str] = None
    try:
        if backend is not None:
            return _save_via_backend(
                backend,
                frame,
                result,
                lot=lot,
                item_code=item_code,
                ts=ts,
                verdict=verdict,
                review=review,
                item=item,
                pending_sink=pending_sink,
            )
        # local 디스크 경로(기존 동작 그대로).
        raw_path = save_raw(frame, target_dir, lot, item_code, ts, verdict)
        overlay = render_overlay(frame, result, item=item)
        result_path = save_result(
            overlay,
            target_dir,
            lot,
            item_code,
            ts,
            verdict,
            review_flag=review,
        )
        return ImageSaveResult(raw_image_path=raw_path, result_image_path=result_path)
    except Exception as exc:  # noqa: BLE001
        return ImageSaveResult(
            raw_image_path=raw_path,
            result_image_path=result_path,
            error=f"{type(exc).__name__}: {exc}",
        )
