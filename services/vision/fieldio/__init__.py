"""현장 입출력(라즈베리파이 GPIO) — 근접센서 트리거 입력, 경광등·부저 출력.

CLAUDE.md §2.1·M1·M6: 센서·경광등 **결선**은 도입기업 몫이지만, 그것을 읽고
울리는 **신호 모듈**은 소프트웨어 범위다. 2026-10-10 개발 진척 점검이 "촬영
신호가 소프트웨어 타이머뿐", "경광등 출력 0" 을 지적해 추가했다.

하드웨어 없이도 전부 테스트되도록 GPIO 는 백엔드 뒤에 둔다(FakeGpioBackend).
실물은 gpiozero(라즈베리파이 OS 기본 포함)를 쓴다.
"""
from .gpio import FakeGpioBackend, GpioBackend, GpioUnavailable, build_gpio_backend
from .tower import NullTower, SignalTower, TowerPins

__all__ = [
    "FakeGpioBackend",
    "GpioBackend",
    "GpioUnavailable",
    "build_gpio_backend",
    "NullTower",
    "SignalTower",
    "TowerPins",
]
