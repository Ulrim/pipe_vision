"""영상 트리거 현장 조정 도구 (2026-10-16, docs/OPERATIONS_PI.md §11-1).

설치 때 한 번 돌려 **검사 영역(AIVIS_ROI)** 과 민감도를 맞춘다. 검사 영역은 영상 트리거가
보는 곳이자 검사하는 곳이고, 결과 사진(작업자 화면)에 노란 점선 사각형으로 나온다 — 여기서
본 사각형이 그대로 화면에 나온다. 카메라는 한 번에 한 프로그램만 열 수 있으므로 워커를
멈추고 돌린다:

    sudo systemctl stop aivis-vision          # (독립형이면 aivis-standalone)
    cd /opt/aivis/services/vision
    AIVIS_CAMERA=picam AIVIS_TRIGGER=vision \\
    AIVIS_ROI=0.05,0.20,0.95,0.80 \\
    .venv/bin/python -m vision.tools.vision_trigger_preview --seconds 60 --expected 20
    sudo systemctl start aivis-vision

하는 일:
  1. 시작 사진에 검사 영역(노란 점선, 밖은 어둡게)과 10% 눈금을 그려 `roi.jpg` 로 저장 —
     영역 숫자를 정하는 데 쓴다(비율이라 해상도와 무관). 제품 전체(길이 스테이션은 양
     끝단까지)가 사각형 안에 들어오고 작업자 통로·옆 크레이트는 빠져야 한다.
  2. 지정한 시간 동안 영상 트리거를 실제와 똑같이 돌리며 0.5초마다 움직임·변화 수치를
     찍고, 쏠 때마다 그 사진을 `fire_01.jpg` … 로 저장한다(무엇을 찍었는지 눈으로 확인).
  3. --expected 를 주면 워커처럼 **검사 영역 안에서** 제품을 세어 빈 장면을 판정·학습한다
     (빈 장면 사진은 `empty_..` 로).
  4. 끝에 '가만히 있을 때' 의 움직임 수치(잡음)를 보고 AIVIS_VISION_MOTION 권장값을 낸다.

제품을 몇 번 통과시키며(또는 크레이트를 채우고 빼며) 다발마다 정확히 한 번씩 찍히는지,
손만 지나갈 때는 안 찍히는지 본다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _draw_roi(frame, area, text: str = ""):
    import cv2

    from vision.imaging.draw import draw_inspect_area
    from vision.preprocess.area import area_rect

    img = frame.copy()
    h, w = img.shape[:2]
    draw_inspect_area(img, area_rect(area, img), top_reserved=24)  # 결과 사진과 같은 그림
    for i in range(1, 10):  # 10% 눈금 — 영역 숫자를 읽기 쉽게
        x, y = int(w * i / 10), int(h * i / 10)
        cv2.line(img, (x, 0), (x, h), (90, 90, 90), 1)
        cv2.line(img, (0, y), (w, y), (90, 90, 90), 1)
        cv2.putText(img, f"{i / 10:.1f}", (x + 3, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(img, f"{i / 10:.1f}", (3, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 200), 1, cv2.LINE_AA)
    if text:
        cv2.putText(img, text, (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 0), 2,
                    cv2.LINE_AA)
    return img


def _count(frame, mode: str, axis: str, area=None) -> int:
    """워커와 같은 기준(검사 영역 안)으로 제품 수를 센다(빈 장면 판정용)."""
    if mode == "departure":
        from vision.multi.bundle import count_bundle

        return int(count_bundle(frame, area=area).count)
    from vision.multi.segment import segment_tubes

    return len(segment_tubes(frame, axis=axis, expected_count=None, area=area))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="영상 트리거 현장 조정(검사 영역·민감도)")
    ap.add_argument("--seconds", type=float, default=60.0, help="관찰 시간(초)")
    ap.add_argument("--out", default="vision_trigger_check", help="사진 저장 폴더")
    ap.add_argument("--mode", choices=["arrival", "departure"], default=None,
                    help="arrival=길이·표면(도착 후 정지), departure=크레이트(반출 직전). "
                         "비우면 AIVIS_VISION_MODE 또는 arrival")
    ap.add_argument("--expected", type=int, default=0,
                    help="기준 개수. 주면 워커처럼 빈 장면을 판정·학습한다")
    ap.add_argument("--axis", choices=["horizontal", "vertical"], default="horizontal")
    ap.add_argument("--max-saves", type=int, default=10)
    args = ap.parse_args(argv)

    import cv2
    import numpy as np

    from vision.acquisition import VisionTrigger, create_camera
    from vision.preprocess.area import InspectArea

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cam = create_camera()
    cam.configure({})
    area = InspectArea.from_env()
    trig = VisionTrigger(cam, mode=args.mode, roi=area.fractions)
    mode = trig.mode()

    first = cam.grab()
    roi_path = out / "roi.jpg"
    cv2.imwrite(str(roi_path), _draw_roi(first, area, f"AIVIS_ROI={area.text()}  mode={mode}"))
    print(f"[조정] 검사 영역 AIVIS_ROI={area.text()}"
          + (" (화면 전체)" if area.full else "") + f" — 확인: {roi_path}")
    print(f"[조정] 모드 {mode}, 확인 빈도 {trig.fps:g}/s, 정지 판정 "
          f"{(trig.settle_s if mode == 'arrival' else trig.depart_settle_s) * 1000:.0f}ms, "
          f"움직임 기준 {trig.motion_thr}, 같은 장면 기준 {trig.change_thr}")

    idle_motion: list[float] = []
    saves = fires = empties = 0
    period = 1.0 / trig.fps if trig.fps > 0 else 0.1
    end = time.monotonic() + args.seconds
    next_print = 0.0
    try:
        while time.monotonic() < end:
            t0 = time.monotonic()
            try:
                shot = trig.poll_once()
            except Exception as exc:  # noqa: BLE001
                print(f"[조정] 미리보기 실패: {exc}")
                time.sleep(0.5)
                continue
            m = trig.last_metrics
            if m.get("motion") is not None and m["motion"] <= trig.motion_thr:
                idle_motion.append(float(m["motion"]))
            if t0 >= next_print:
                print(f"  움직임 {m.get('motion')}  변화 {m.get('change')}  "
                      f"(촬영 {fires}, 빈 장면 {empties})")
                next_print = t0 + 0.5
            if shot:
                held = trig.take_frame()
                frame = held[0] if held else cam.grab()
                n = _count(frame, mode, args.axis, area) if args.expected else None
                is_empty = (n is not None and (n == 0 or (
                    args.expected >= 5 and n <= max(1, args.expected // 10))))
                if is_empty:
                    trig.learn_empty()
                    empties += 1
                    tag = f"empty_{empties:02d}"
                    print(f"[조정] 빈 장면(검출 {n}) — 학습")
                else:
                    fires += 1
                    tag = f"fire_{fires:02d}"
                    print(f"[조정] ▶ 촬영 {fires}" + (f" — 검출 {n}개" if n is not None else ""))
                if saves < args.max_saves:
                    cv2.imwrite(str(out / f"{tag}.jpg"), _draw_roi(frame, area, tag))
                    saves += 1
            wait = period - (time.monotonic() - t0)
            if wait > 0:
                time.sleep(wait)
    except KeyboardInterrupt:
        pass
    finally:
        cam.close()

    print(f"[조정] 끝 — 촬영 {fires}회, 빈 장면 학습 {empties}회, 사진: {out}/")
    if idle_motion:
        p95 = float(np.percentile(idle_motion, 95))
        rec = max(0.004, round(p95 * 3, 4))
        print(f"[조정] 가만히 있을 때 움직임 잡음 p95={p95:.4f} → "
              f"권장 AIVIS_VISION_MOTION={rec} (지금 {trig.motion_thr})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
