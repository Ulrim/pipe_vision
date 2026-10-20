"""영상 트리거 (2026-10-16) — 근접센서 없이 카메라 영상으로 '찍을 때' 를 정한다.

지키는 것:
1. 움직이는 동안에는 찍지 않는다 — 멈추고 settle 만큼 조용해야 찍는다(롤링 셔터).
2. 한 다발에 한 번. 손이 잠깐 지나가도 다시 찍지 않는다.
3. 똑같이 생긴 다음 다발도 찍는다(같은 품목은 거의 같아 보인다 — 컨베이어가 움직였는지로 구분).
4. 빈 장면은 한 번 쏘고 배운 뒤로는 쏘지 않는다. 조명 밝기 변화는 움직임이 아니다.
5. 크레이트(반출): 채우는 동안은 보관만, 빠질 때 '빠지기 직전 마지막 사진' 을 한 번 낸다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import VisionTrigger, create_trigger  # noqa: E402
from vision.acquisition.camera import PiCameraAdapter  # noqa: E402

W, H = 160, 90


def empty(level: int = 20) -> np.ndarray:
    return np.full((H, W), level, np.uint8)


def bundle(shift: int = 0, level: int = 20, n: int = 6) -> np.ndarray:
    """가로로 누운 튜브 n개(밝은 띠). shift 만큼 오른쪽에 있다(들어오는 중)."""
    img = empty(level)
    for i in range(n):
        y = 8 + i * 13
        img[y:y + 9, 30:130] = min(255, level + 170)
    out = empty(level)
    if shift <= 0:
        return img
    out[:, shift:] = img[:, : W - shift]
    return out


class ScriptCam:
    """미리보기를 대본대로 내놓는 가짜 카메라. grab 은 지금 장면의 '고해상도' 사본."""

    def __init__(self, scenes):
        self.scenes = list(scenes)
        self.i = 0
        self.cur = None
        self.grabs = 0

    def preview(self, width=320):
        p = self.scenes[min(self.i, len(self.scenes) - 1)]
        self.i += 1
        self.cur = p
        return p

    def grab(self):
        self.grabs += 1
        return np.dstack([self.cur] * 3).copy()


def run(trig: VisionTrigger, cam: ScriptCam, steps: int, dt: float = 0.1):
    """dt 간격으로 미리보기를 보며 쏜 시점(장면 번호)을 모은다."""
    t = 0.0
    trig._clock = lambda: t
    fired = []
    for _ in range(steps):
        t += dt
        if trig.poll_once():
            fired.append(cam.i - 1)
    return fired


def arrival(cam, **kw) -> VisionTrigger:
    kw.setdefault("settle_ms", 300)
    return VisionTrigger(cam, mode="arrival", fps=10, preview_width=W, **kw)


def arrive_sequence():
    """빈 화면 5 → 들어옴 4 → 정지 10."""
    return ([empty()] * 5 + [bundle(s) for s in (120, 80, 40, 10)] + [bundle()] * 10)


def test_fires_once_after_bundle_stops_not_while_moving():
    cam = ScriptCam(arrive_sequence())
    trig = arrival(cam)
    fired = run(trig, cam, len(cam.scenes))
    # 첫 정지 장면(빈 화면)은 한 번 본다 — 학습 전이라.
    assert fired[0] < 5
    trig.learn_empty()
    bundle_fires = [i for i in fired if i >= 9]
    assert len(bundle_fires) == 1, fired
    # 멈추고(9번) settle 300ms(3장) 뒤에야 쏜다 — 움직이는 중(5~8번)에는 안 쏜다.
    assert bundle_fires[0] >= 9 + 3


def test_hand_passing_does_not_reshoot_same_bundle():
    hand = bundle()
    hand[0:30, 0:25] = 230  # 화면 구석으로 손이 잠깐(작은 영역)
    scenes = [bundle()] * 6 + [hand] * 2 + [bundle()] * 8
    cam = ScriptCam(scenes)
    trig = arrival(cam)
    fired = run(trig, cam, len(scenes))
    assert len(fired) == 1, f"손이 지나간 뒤 같은 다발을 또 찍었다: {fired}"


def test_identical_next_bundle_is_still_shot():
    """같은 품목 다발은 거의 똑같다 — 컨베이어가 움직였으면 다음 다발로 본다."""
    leave = [empty()] * 3
    scenes = ([bundle()] * 6 + [bundle(40), bundle(90)] + leave
              + [bundle(s) for s in (120, 60, 10)] + [bundle()] * 8)
    cam = ScriptCam(scenes)
    trig = arrival(cam)
    fired = run(trig, cam, len(scenes))
    assert len(fired) == 2, fired


def test_empty_scene_learned_and_ignored_afterwards():
    scenes = [empty()] * 6 + [bundle(60), bundle(10)] + [bundle()] * 6 + [bundle(60)] + [empty()] * 8
    cam = ScriptCam(scenes)
    trig = arrival(cam)
    t = 0.0
    trig._clock = lambda: t
    shots = []
    for _ in range(len(scenes)):
        t += 0.1
        if trig.poll_once():
            scene = cam.cur
            shots.append("empty" if scene.max() < 100 else "bundle")
            if scene.max() < 100:
                trig.learn_empty()  # 워커: 검출 0 → 빈 장면
    assert shots == ["empty", "bundle"], shots
    assert trig.empty_scenes == 1


def test_brightness_change_is_not_motion():
    """조명·자동노출로 화면 전체 밝기가 바뀌어도 장면 변화가 아니다(중앙값 정규화)."""
    scenes = [bundle(level=20)] * 6 + [bundle(level=45)] * 8
    cam = ScriptCam(scenes)
    trig = arrival(cam)
    fired = run(trig, cam, len(scenes))
    assert len(fired) == 1, fired  # 기동 직후 1회만


def test_motion_outside_roi_is_ignored():
    scenes = [bundle()] * 6
    for k in range(6):
        s = bundle()
        s[:, 140 + (k % 3) * 5: 150 + (k % 3) * 5] = 250  # 영역 밖(오른쪽 끝)에서 움직임
        scenes.append(s)
    scenes += [bundle()] * 6
    cam = ScriptCam(scenes)
    trig = arrival(cam, roi=(0.0, 0.0, 0.8, 1.0))
    fired = run(trig, cam, len(scenes))
    assert len(fired) == 1, fired


def test_wait_for_trigger_times_out_and_survives_camera_errors():
    class Flaky(ScriptCam):
        def preview(self, width=320):
            if self.i % 3 == 0:
                self.i += 1
                raise RuntimeError("frame drop")
            return super().preview(width)

    cam = Flaky([empty()] * 100)
    t = [0.0]
    trig = VisionTrigger(cam, mode="arrival", fps=10, preview_width=W, settle_ms=300,
                         clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert trig.wait_for_trigger(timeout=2.0) is True  # 첫 정지 장면(빈 화면)
    trig.learn_empty()
    assert trig.wait_for_trigger(timeout=2.0) is False  # 계속 비어 있다 → 시간 초과


# ---- 크레이트(반출) ----------------------------------------------------------------

def crate(fill: int) -> np.ndarray:
    """위에서 본 크레이트 — 단면(밝은 고리) fill 개."""
    img = empty(40)
    k = 0
    for r in range(4):
        for c in range(8):
            if k >= fill:
                return img
            cy, cx = 12 + r * 22, 12 + c * 19
            img[cy - 6:cy + 6, cx - 6:cx + 6] = 220
            img[cy - 3:cy + 3, cx - 3:cx + 3] = 30
            k += 1
    return img


def departure(cam, **kw) -> VisionTrigger:
    kw.setdefault("depart_settle_ms", 500)
    return VisionTrigger(cam, mode="departure", fps=10, preview_width=W, **kw)


def hands(img):
    out = img.copy()
    out[30:70, 40:120] = 200  # 손·팔이 크레이트 위를 덮는다
    return out


def test_crate_counted_once_with_last_state_before_removal():
    scenes = []
    for fill in (8, 16, 24, 32):  # 네 번에 나눠 채운다 — 사이사이 손이 들어온다
        scenes += [hands(crate(fill))] * 3 + [crate(fill)] * 8
    scenes += [hands(crate(32))] * 2 + [empty(40)] * 10  # 크레이트를 뺐다
    cam = ScriptCam(scenes)
    trig = departure(cam)
    t = 0.0
    trig._clock = lambda: t
    emitted = []
    for _ in range(len(scenes)):
        t += 0.1
        if trig.poll_once():
            frame, at = trig.take_frame()
            emitted.append(frame[:, :, 0])
    assert len(emitted) == 1, f"한 판이 {len(emitted)}번 세어졌다"
    assert np.array_equal(emitted[0], crate(32)), "빠지기 직전(가장 꽉 찬) 사진이어야 한다"
    assert trig.take_frame() is None


def test_crate_swap_for_new_crate_emits_previous():
    scenes = [crate(32)] * 8 + [hands(crate(32))] * 2 + [crate(4)] * 8
    cam = ScriptCam(scenes)
    trig = departure(cam)
    t = 0.0
    trig._clock = lambda: t
    out = []
    for _ in range(len(scenes)):
        t += 0.1
        if trig.poll_once():
            out.append(trig.take_frame()[0][:, :, 0])
    assert len(out) == 1 and np.array_equal(out[0], crate(32))


def test_mode_follows_station_stage():
    stage = {"m": "arrival"}
    cam = ScriptCam([empty()] * 3)
    trig = VisionTrigger(cam, mode_provider=lambda: stage["m"], preview_width=W)
    assert trig.mode() == "arrival"
    stage["m"] = "departure"
    assert trig.mode() == "departure"
    stage["m"] = "nonsense"
    assert trig.mode() == "arrival"


# ---- 팩토리·카메라 ----------------------------------------------------------------

def test_factory_vision_needs_camera(monkeypatch):
    monkeypatch.setenv("AIVIS_TRIGGER", "vision")
    for cam_mode in ("sim", "picam"):
        monkeypatch.setenv("AIVIS_CAMERA", cam_mode)
        with pytest.raises(ValueError):
            create_trigger(0.0)
        t = create_trigger(0.0, camera=ScriptCam([empty()]), mode_provider=lambda: "arrival")
        assert isinstance(t, VisionTrigger) and t.event_driven


def test_factory_default_unchanged(monkeypatch):
    monkeypatch.delenv("AIVIS_TRIGGER", raising=False)
    monkeypatch.setenv("AIVIS_CAMERA", "sim")
    assert not isinstance(create_trigger(0.0, camera=ScriptCam([empty()])), VisionTrigger)


def test_roi_env_parsing(monkeypatch):
    monkeypatch.setenv("AIVIS_VISION_ROI", "0.9,0.8,0.1,0.2")
    assert VisionTrigger(ScriptCam([empty()])).roi == (0.1, 0.2, 0.9, 0.8)
    monkeypatch.setenv("AIVIS_VISION_ROI", "junk")
    assert VisionTrigger(ScriptCam([empty()])).roi == (0.0, 0.0, 1.0, 1.0)


class _FakePicam2:
    """picamera2 흉내 — 받은 설정을 기록하고 lores 는 YUV420 배열을 준다."""

    reject_lores = False

    def __init__(self):
        self.configs = []

    def create_still_configuration(self, main, lores=None):
        if lores is not None and self.reject_lores:
            raise RuntimeError("lores unsupported")
        return {"main": main, "lores": lores}

    def configure(self, cfg):
        self.configs.append(cfg)

    def start(self):
        pass

    def set_controls(self, c):
        pass

    def capture_array(self, name="main"):
        if name == "lores":
            lw, lh = self.configs[-1]["lores"]["size"]
            y = np.full((lh, lw), 77, np.uint8)
            uv = np.full((lh // 2, lw), 128, np.uint8)
            return np.vstack([y, uv])  # YUV420: Y 다음에 UV
        w, h = self.configs[-1]["main"]["size"]
        return np.full((h, w, 3), 50, np.uint8)

    def stop(self):
        pass

    def close(self):
        pass


def _picam(monkeypatch, reject=False, **kw):
    _FakePicam2.reject_lores = reject
    cam = PiCameraAdapter(size="1280x720", warmup_frames=0, **kw)

    class _Ctl:
        pass

    monkeypatch.setattr(cam, "_require_picamera2", lambda: (_FakePicam2, _Ctl))
    return cam


def test_picam_lores_preview_uses_y_plane(monkeypatch):
    cam = _picam(monkeypatch, lores=True, lores_width=320)
    p = cam.preview(320)
    assert p.shape == (180, 320) and p.dtype == np.uint8 and int(p.mean()) == 77
    assert cam._picam2.configs[-1]["lores"] == {"size": (320, 180), "format": "YUV420"}
    assert cam.grab().shape == (720, 1280, 3), "고해상도 촬영은 그대로"


def test_picam_lores_rejected_falls_back_to_main(monkeypatch):
    cam = _picam(monkeypatch, reject=True, lores=True)
    p = cam.preview(320)
    assert p.shape == (180, 320) and int(p.mean()) == 50  # main 을 줄인 것
    assert cam._picam2.configs[-1]["lores"] is None


def test_picam_lores_default_on_for_trigger_and_live_view(monkeypatch):
    """lores 는 기본으로 켠다(2026-10-20) — 영상 트리거와 라이브 뷰가 쓴다. "0" 이면 끈다."""
    monkeypatch.delenv("AIVIS_PICAM_LORES", raising=False)
    monkeypatch.delenv("AIVIS_PICAM_LORES_WIDTH", raising=False)
    monkeypatch.setenv("AIVIS_TRIGGER", "timer")
    cam = PiCameraAdapter()
    assert cam.lores is True and cam.lores_width == 640
    monkeypatch.setenv("AIVIS_PICAM_LORES", "0")
    assert PiCameraAdapter().lores is False
    monkeypatch.setenv("AIVIS_PICAM_LORES_WIDTH", "480")
    assert PiCameraAdapter().lores_width == 480
