"""현장 입출력 — 근접센서 GPIO 트리거, 경광등·부저 (2026-10-10 점검 보완).

점검: "촬영 신호가 소프트웨어 타이머뿐 — 근접센서 GPIO 트리거", "경광등 출력 0".
하드웨어 없이 FakeGpioBackend 로 동작을 고정한다.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from vision.acquisition import GpioTrigger, TimerTrigger, TriggerSDKError  # noqa: E402
from vision.acquisition.factory import create_trigger  # noqa: E402
from vision.fieldio import FakeGpioBackend, NullTower, SignalTower, TowerPins  # noqa: E402
from vision.worker.runner import Worker  # noqa: E402

_spec = importlib.util.spec_from_file_location("_tw_fieldio", Path(__file__).with_name("test_worker.py"))
_tw = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_tw)


# ---- 근접센서 트리거 -----------------------------------------------------------

def test_gpio_trigger_fires_on_inactive_to_active_edge_only():
    # 상태 열: (대기→활성) 촬영, (아직 활성 → 다음 대기에서 비활성 기다림) ...
    fake = FakeGpioBackend({17: [True, False, True]})
    t = GpioTrigger(17, settle_ms=0, backend=fake)
    assert t.wait_for_trigger(timeout=0.01) is True         # 제품 도착 → 촬영
    assert fake.inputs_setup[17] == (True, 30), "NPN 센서 기본: active_low, 30ms 떨림 무시"
    # 같은 제품이 센서 앞에 있으면 빠질 때까지(False) 기다린 뒤 다음 도착(True)
    assert t.wait_for_trigger(timeout=0.01) is True
    assert t.fired == 2


def test_gpio_trigger_timeout_means_no_product():
    fake = FakeGpioBackend({17: [False]})
    t = GpioTrigger(17, settle_ms=0, backend=fake)
    assert t.wait_for_trigger(timeout=0.01) is False
    assert t.event_driven is True and TimerTrigger.event_driven is False


def test_gpio_trigger_waits_settle_time_before_capture():
    """롤링 셔터 — 멈춘 뒤 찍는다. 감지 후 settle_ms 만큼 쉰다."""
    t = GpioTrigger(5, settle_ms=60, backend=FakeGpioBackend({5: [True]}))
    t0 = time.monotonic()
    assert t.wait_for_trigger(timeout=0.01)
    assert time.monotonic() - t0 >= 0.055


def test_gpio_trigger_without_pin_explains_itself(monkeypatch):
    monkeypatch.delenv("AIVIS_TRIGGER_GPIO", raising=False)
    t = GpioTrigger(backend=FakeGpioBackend())
    with pytest.raises(TriggerSDKError, match="AIVIS_TRIGGER_GPIO"):
        t.wait_for_trigger(timeout=0.01)


def test_factory_selects_gpio_on_pi(monkeypatch):
    monkeypatch.setenv("AIVIS_CAMERA", "picam")
    monkeypatch.setenv("AIVIS_TRIGGER", "gpio")
    monkeypatch.setenv("AIVIS_TRIGGER_GPIO", "22")
    t = create_trigger(interval_s=1.0)
    assert isinstance(t, GpioTrigger) and t.pin == 22
    monkeypatch.setenv("AIVIS_TRIGGER", "timer")
    assert isinstance(create_trigger(interval_s=1.0), TimerTrigger)


# ---- 경광등·부저 ---------------------------------------------------------------

def _tower(**kw):
    fake = FakeGpioBackend()
    pins = TowerPins(red=23, green=24, yellow=25, buzzer=26)
    return SignalTower(pins, fake, **kw), fake


def test_ok_green_ng_red_with_buzz_pulse():
    tw, f = _tower(buzz_ms=30, consec_threshold=3)
    tw.signal(ng=False)
    assert f.out_state[24] is True and f.out_state[23] is False
    tw.signal(ng=True)
    assert f.out_state[23] is True and f.out_state[24] is False
    assert f.out_state[26] is True, "NG 순간 부저"
    time.sleep(0.08)
    assert f.out_state[26] is False, "짧게 울리고 꺼진다"
    tw.close()


def test_consecutive_ng_latches_buzzer_until_ok():
    tw, f = _tower(buzz_ms=20, consec_threshold=3)
    for _ in range(3):
        tw.signal(ng=True)
    time.sleep(0.06)
    assert f.out_state[26] is True, "연속 NG — 부저 계속"
    assert tw.consecutive_ng == 3
    tw.signal(ng=False)
    assert f.out_state[26] is False and f.out_state[24] is True and tw.consecutive_ng == 0
    tw.close()


def test_fault_is_yellow_and_clears_on_next_cycle():
    tw, f = _tower()
    tw.fault()
    assert f.out_state[25] is True
    tw.signal(ng=False)
    assert f.out_state[25] is False
    tw.close()
    assert f.closed and all(v is False for v in f.out_state.values()), "종료 시 전부 끈다"


def test_active_low_relay_and_no_pins_is_null(monkeypatch):
    for k in ("RED", "GREEN", "YELLOW", "BUZZER"):
        monkeypatch.delenv(f"AIVIS_TOWER_{k}", raising=False)
    assert isinstance(SignalTower.from_env(backend=FakeGpioBackend()), NullTower)
    monkeypatch.setenv("AIVIS_TOWER_RED", "23")
    monkeypatch.setenv("AIVIS_TOWER_ACTIVE_LOW", "true")
    f = FakeGpioBackend()
    tw = SignalTower.from_env(backend=f)
    assert isinstance(tw, SignalTower) and f.outputs_setup == {23: True}


def test_tower_output_failure_never_stops_inspection():
    class Broken(FakeGpioBackend):
        def write(self, pin, on):
            raise OSError("릴레이 단선")

    tw = SignalTower(TowerPins(red=1, green=2), Broken())
    tw.signal(ng=True)  # 예외가 새면 안 된다
    tw.signal(ng=False)


# ---- 워커 통합 -------------------------------------------------------------------

class _FakeEventTrigger:
    event_driven = True

    def __init__(self, fires):
        self._fires = list(fires)

    def wait_for_trigger(self, timeout=None):
        return self._fires.pop(0) if self._fires else False

    def close(self):
        pass


def test_worker_does_not_capture_without_product_and_sends_waiting(tmp_path, monkeypatch):
    import vision.worker.runner as runner_mod

    backend = _tw.FakeBackend()
    f = FakeGpioBackend()
    tower = SignalTower(TowerPins(red=23, green=24), f)
    monkeypatch.setattr(runner_mod, "create_trigger",
                        lambda **_kw: _FakeEventTrigger([False, False, True]))
    w = Worker(_tw._cfg(tmp_path, max_iterations=1, images_dir=str(tmp_path / "i"),
                        trigger_idle_s=0.01),
               client=_tw._client(backend), tower=tower)
    rc = w.run()
    assert rc == 0
    assert len(backend.posted) == 1, "제품이 온 한 번만 찍어 올렸다"
    waits = [s for s in backend.statuses if s.get("waiting")]
    assert len(waits) == 2 and all(s["detected"] == 0 for s in waits)
    assert any(pin in (23, 24) for pin, _on in f.writes), "판정 후 경광등이 움직였다"
