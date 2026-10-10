"""경광등·부저 출력 — NG 를 현장에서 눈과 귀로 알린다(M6).

판정마다:
  OK        → 녹색 켬, 적색 끔
  NG        → 적색 켬 + 부저 짧게(AIVIS_TOWER_BUZZ_MS, 기본 400ms)
  연속 NG    → 적색 + 부저 **계속**(AIVIS_TOWER_CONSEC_NG 회, 기본 3) — 다음 OK 까지
  장치 이상  → 황색 켬(카메라 취득 실패 등). 다음 정상 사이클에 끔.

워커가 파이에서 **직접** 울린다 — 서버·네트워크가 끊겨도 현장 경보는 산다.
(대시보드/HMI 의 연속 NG 알람은 서버 쪽에서 따로 돈다.)

핀(BCM)·극성은 환경변수로:
  AIVIS_TOWER_RED / _GREEN / _YELLOW / _BUZZER   (없으면 그 출력은 안 쓴다)
  AIVIS_TOWER_ACTIVE_LOW=true                     (릴레이 모듈이 LOW 에서 켜지면)
핀이 하나도 없으면 NullTower(아무것도 안 함).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

from .gpio import GpioBackend, GpioUnavailable, build_gpio_backend

log = logging.getLogger("aivis.vision.tower")


def _env_pin(name: str) -> Optional[int]:
    v = (os.environ.get(name) or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        log.warning("%s=%r 는 BCM 핀 번호가 아니다 — 무시", name, v)
        return None


@dataclass(frozen=True)
class TowerPins:
    red: Optional[int] = None
    green: Optional[int] = None
    yellow: Optional[int] = None
    buzzer: Optional[int] = None
    active_low: bool = False

    @classmethod
    def from_env(cls) -> "TowerPins":
        return cls(
            red=_env_pin("AIVIS_TOWER_RED"),
            green=_env_pin("AIVIS_TOWER_GREEN"),
            yellow=_env_pin("AIVIS_TOWER_YELLOW"),
            buzzer=_env_pin("AIVIS_TOWER_BUZZER"),
            active_low=(os.environ.get("AIVIS_TOWER_ACTIVE_LOW", "false").lower()
                        in ("1", "true", "yes")),
        )

    @property
    def any(self) -> bool:
        return any(p is not None for p in (self.red, self.green, self.yellow, self.buzzer))


class NullTower:
    """출력 핀이 없을 때 — 아무것도 하지 않는다(시뮬레이터·PC)."""

    consecutive_ng = 0

    def signal(self, *, ng: bool) -> None:
        pass

    def fault(self) -> None:
        pass

    def close(self) -> None:
        pass


class SignalTower(NullTower):
    def __init__(
        self,
        pins: TowerPins,
        backend: GpioBackend,
        *,
        buzz_ms: int = 400,
        consec_threshold: int = 3,
    ) -> None:
        self.pins = pins
        self._b = backend
        self.buzz_ms = max(0, int(buzz_ms))
        self.consec_threshold = max(1, int(consec_threshold))
        self.consecutive_ng = 0
        self._buzz_timer: Optional[threading.Timer] = None
        self._latched = False
        for p in (pins.red, pins.green, pins.yellow, pins.buzzer):
            if p is not None:
                backend.setup_output(p, active_low=pins.active_low)

    @classmethod
    def from_env(cls, backend: Optional[GpioBackend] = None):
        """env 로 만든다. 핀이 없거나 GPIO 를 못 열면 NullTower(검사는 계속)."""
        pins = TowerPins.from_env()
        if not pins.any:
            return NullTower()
        try:
            b = backend or build_gpio_backend()
        except GpioUnavailable as exc:
            log.warning("경광등 핀이 설정됐지만 GPIO 를 열 수 없다(경광등 없이 계속): %s", exc)
            return NullTower()
        return cls(
            pins, b,
            buzz_ms=int(os.environ.get("AIVIS_TOWER_BUZZ_MS", "400") or 400),
            consec_threshold=int(os.environ.get("AIVIS_TOWER_CONSEC_NG", "3") or 3),
        )

    # --- 내부 ---
    def _set(self, pin: Optional[int], on: bool) -> None:
        if pin is None:
            return
        try:
            self._b.write(pin, on)
        except Exception as exc:  # noqa: BLE001 — 경광등 고장이 검사를 멈추면 안 된다
            log.warning("경광등 출력 실패 pin=%s: %s", pin, exc)

    def _cancel_buzz(self) -> None:
        if self._buzz_timer is not None:
            self._buzz_timer.cancel()
            self._buzz_timer = None

    def _buzz_pulse(self) -> None:
        if self.pins.buzzer is None or self.buzz_ms <= 0:
            return
        self._cancel_buzz()
        self._set(self.pins.buzzer, True)
        t = threading.Timer(self.buzz_ms / 1000.0, self._set, args=(self.pins.buzzer, False))
        t.daemon = True
        self._buzz_timer = t
        t.start()

    # --- 공개 ---
    def signal(self, *, ng: bool) -> None:
        """한 사이클 판정 결과(다발이면 1개라도 NG 면 ng=True)."""
        self._set(self.pins.yellow, False)  # 정상 사이클 → 장치 이상 표시 해제
        if ng:
            self.consecutive_ng += 1
            self._set(self.pins.green, False)
            self._set(self.pins.red, True)
            if self.consecutive_ng >= self.consec_threshold:
                # 연속 NG — 부저를 계속 울린다(다음 OK 까지). 공정 이상 신호.
                self._cancel_buzz()
                self._latched = True
                self._set(self.pins.buzzer, True)
            elif not self._latched:
                self._buzz_pulse()
        else:
            self.consecutive_ng = 0
            self._latched = False
            self._cancel_buzz()
            self._set(self.pins.buzzer, False)
            self._set(self.pins.red, False)
            self._set(self.pins.green, True)

    def fault(self) -> None:
        """취득 실패 등 장치 이상 — 황색. 판정 표시는 건드리지 않는다."""
        self._set(self.pins.yellow, True)

    def close(self) -> None:
        self._cancel_buzz()
        for p in (self.pins.red, self.pins.green, self.pins.yellow, self.pins.buzzer):
            self._set(p, False)
        try:
            self._b.close()
        except Exception:  # noqa: BLE001
            pass
