"""제품 온도 입력 → 길이 20°C 환산 (2026-10-20, docs/LENGTH_TOLERANCE.md §3-2·§5).

±0.1mm 공차에서 %GR&R 29% 는 **온도를 ±1K 안에서 안다**는 전제다. 250mm 알루미늄은 1K 에
5.8µm 움직이므로, 여름·겨울 10K 차이면 그것만으로 공차의 29% 가 계통오차로 들어온다.
환산식(`fiducial.to_reference_temperature`)은 있었지만 온도 값을 검사까지 끌고 오는 배선이
없었다 — 이 모듈이 그 배선이다.

온도 소스(`AIVIS_TEMP_SOURCE`):
  none(기본)            환산하지 않는다. 오차 예산에 열팽창 항이 그대로 남는다(정직한 기본값).
  fixed:<°C>            고정값. 냉난방으로 실내 온도가 일정한 곳, 또는 시험용.
  ds18b20[:<센서ID>]     파이 1-wire 온도 센서(약 3천 원, 정확도 ±0.5°C). /sys/bus/w1/devices/28-*/
  file:<경로>            숫자 하나가 든 파일(°C, 또는 1000 배 정수 — 리눅스 hwmon temp*_input).
                        다른 프로그램이 쓰는 값을 그대로 읽을 때.

**길이 스테이션을 세척 이후에 두면 공기 온도계 하나로 충분하다**(제품이 주위와 평형). 절단
직후면 톱 발열로 제품마다 온도가 달라 공기 온도로는 안 된다(§A.1.1 현장 조건 3).

1-wire 읽기는 한 번에 ~750ms 걸린다 — 검사(300ms 예산) 안에서 읽으면 안 된다. 그래서
`TemperatureMonitor` 가 뒤에서 주기적으로 읽어 두고, 검사는 마지막 값만 가져간다. 값이 오래되면
(센서 빠짐) None 을 돌려 환산하지 않고, 사유를 남긴다.

자(기준자)도 늘어난다. 매 프레임 기준자로 배율을 읽으면 측정값은 '그 온도의 자 눈금' 이므로
환산은 (1+α자·ΔT)/(1+α제품·ΔT) 다. 자가 제품과 같은 알루미늄이면 둘이 상쇄된다(온도계 없이도
된다는 뜻 — 단 자와 제품 온도가 같아야 한다). 기본 자 재질은 강철(11.5µm/m·K, 산업용 강척).
"""
from __future__ import annotations

import glob
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .fiducial import CTE_ALUMINIUM, REFERENCE_TEMP_C

log = logging.getLogger("aivis.vision.calib.temperature")

#: 강철 선팽창계수 [1/K] — 산업용 강척·게이지 블록.
CTE_STEEL = 11.5e-6
#: 이 범위 밖의 값은 센서 고장으로 본다(실내 공정).
TEMP_RANGE_C = (-20.0, 80.0)
W1_GLOB = "/sys/bus/w1/devices/28-*"


class TemperatureError(RuntimeError):
    pass


def _check(v: float) -> float:
    if not (TEMP_RANGE_C[0] <= v <= TEMP_RANGE_C[1]):
        raise TemperatureError(f"온도 {v:.2f}°C 가 정상 범위 {TEMP_RANGE_C} 밖 — 센서 확인")
    return v


def parse_ds18b20(text: str) -> float:
    """1-wire `w1_slave` 내용 → °C. 첫 줄 끝 'YES'(CRC 통과)와 둘째 줄 't=23125' 를 본다."""
    lines = text.strip().splitlines()
    if len(lines) < 2 or not lines[0].strip().endswith("YES"):
        raise TemperatureError("DS18B20 CRC 실패(배선·풀업 저항 확인)")
    _, sep, raw = lines[1].partition("t=")
    if not sep:
        raise TemperatureError("DS18B20 응답에 t= 가 없다")
    v = int(raw.strip()) / 1000.0
    if v == 85.0:  # 전원 직후 초기값 — 변환이 안 된 것
        raise TemperatureError("DS18B20 초기값 85°C(변환 실패)")
    return _check(v)


def parse_number_file(text: str) -> float:
    """숫자 하나 → °C. 절댓값이 200 을 넘으면 1000 배 정수(hwmon)로 본다."""
    v = float(text.strip().split()[0])
    if abs(v) > 200:
        v /= 1000.0
    return _check(v)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="ascii", errors="replace") as fh:
        return fh.read()


@dataclass
class TemperatureSource:
    """한 번 읽는다(느릴 수 있다). kind = fixed | ds18b20 | file."""

    kind: str
    value: Optional[float] = None   # fixed
    path: Optional[str] = None      # ds18b20 / file
    offset_c: float = 0.0           # 교정 오프셋(기준 온도계와 비교해 맞춘다)
    reader: Callable[[str], str] = _read_text

    @property
    def label(self) -> str:
        if self.kind == "fixed":
            return f"fixed:{self.value}"
        return f"{self.kind}:{self.path}"

    @property
    def is_fixed(self) -> bool:
        return self.kind == "fixed"

    def read(self) -> float:
        if self.kind == "fixed":
            assert self.value is not None
            return _check(self.value + self.offset_c)
        assert self.path is not None
        text = self.reader(self.path)
        v = parse_ds18b20(text) if self.kind == "ds18b20" else parse_number_file(text)
        return _check(v + self.offset_c)


def parse_source(
    spec: Optional[str],
    *,
    offset_c: float = 0.0,
    w1_glob: str = W1_GLOB,
    reader: Callable[[str], str] = _read_text,
) -> Optional[TemperatureSource]:
    """`AIVIS_TEMP_SOURCE` 값 → 소스. none/빈 값이면 None. 잘못된 값은 ValueError."""
    s = (spec or "").strip()
    if not s or s.lower() in ("none", "off", "0", "false"):
        return None
    kind, _, arg = s.partition(":")
    kind = kind.strip().lower()
    arg = arg.strip()
    if kind == "fixed":
        try:
            return TemperatureSource("fixed", value=float(arg), offset_c=offset_c)
        except ValueError as exc:
            raise ValueError(f"AIVIS_TEMP_SOURCE=fixed:<°C> 형식이 아니다: {spec!r}") from exc
    if kind == "ds18b20":
        if arg:
            base = arg if arg.startswith("/") else os.path.join(os.path.dirname(w1_glob), arg)
        else:
            found = sorted(glob.glob(w1_glob))
            if not found:
                raise ValueError("DS18B20 를 찾지 못했다(/boot/firmware/config.txt 에 dtoverlay=w1-gpio, 배선 확인)")
            base = found[0]
        return TemperatureSource("ds18b20", path=os.path.join(base, "w1_slave"), offset_c=offset_c,
                                 reader=reader)
    if kind == "file":
        if not arg:
            raise ValueError("AIVIS_TEMP_SOURCE=file:<경로> 에 경로가 없다")
        return TemperatureSource("file", path=arg, offset_c=offset_c, reader=reader)
    raise ValueError(f"알 수 없는 AIVIS_TEMP_SOURCE: {spec!r} (none|fixed:<°C>|ds18b20[:<ID>]|file:<경로>)")


class TemperatureMonitor:
    """온도를 뒤에서 주기적으로 읽어 두고, 검사는 마지막 값만 가져간다.

    current() 는 기다리지 않는다. 마지막 성공이 max_age_s 보다 오래되면 None(환산 안 함).
    """

    def __init__(
        self,
        source: TemperatureSource,
        *,
        interval_s: float = 10.0,
        max_age_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ) -> None:
        self.source = source
        self.interval_s = max(1.0, float(interval_s))
        self.max_age_s = float(max_age_s)
        self._clock = clock
        self._value: Optional[float] = None
        self._at: Optional[float] = None
        self.last_error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if source.is_fixed:
            self.poll()
        elif start_thread:
            self.poll()  # 첫 값은 시작할 때 받아 둔다(첫 검사부터 환산)
            self._thread = threading.Thread(target=self._loop, name="aivis-temp", daemon=True)
            self._thread.start()

    def poll(self) -> Optional[float]:
        try:
            v = self.source.read()
        except Exception as exc:  # noqa: BLE001 - 센서 고장은 검사를 막지 않는다
            msg = f"{type(exc).__name__}: {exc}"
            if msg != self.last_error:
                log.warning("온도 읽기 실패(%s): %s", self.source.label, msg)
            self.last_error = msg
            return None
        self._value, self._at, self.last_error = v, self._clock(), None
        return v

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def current(self) -> Optional[float]:
        if self._value is None or self._at is None:
            return None
        if not self.source.is_fixed and self._clock() - self._at > self.max_age_s:
            return None
        return self._value

    def stale_reason(self) -> Optional[str]:
        """current() 가 None 인 이유(검사 기록용). 값이 있으면 None."""
        if self.current() is not None:
            return None
        if self.last_error:
            return f"온도 없음({self.source.label}): {self.last_error}"
        return f"온도 없음({self.source.label}): 마지막 값이 {self.max_age_s:.0f}초보다 오래됨"

    def stop(self) -> None:
        self._stop.set()


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def monitor_from_env(*, start_thread: bool = True) -> Optional[TemperatureMonitor]:
    """환경변수로 만든다. 설정이 틀리면 경고만 남기고 None(검사는 환산 없이 계속)."""
    try:
        src = parse_source(os.environ.get("AIVIS_TEMP_SOURCE"),
                           offset_c=_envf("AIVIS_TEMP_OFFSET_C", 0.0))
    except ValueError as exc:
        log.warning("온도 소스 설정 오류 — 환산하지 않는다: %s", exc)
        return None
    if src is None:
        return None
    mon = TemperatureMonitor(
        src,
        interval_s=_envf("AIVIS_TEMP_INTERVAL_S", 10.0),
        max_age_s=_envf("AIVIS_TEMP_MAX_AGE_S", 120.0),
        start_thread=start_thread,
    )
    log.info("온도 환산 사용: %s (지금 %s°C)", src.label, mon.current())
    return mon


def compensation_factor(
    temp_c: float,
    *,
    part_cte: float = CTE_ALUMINIUM,
    ruler_cte: Optional[float] = None,
    reference_c: float = REFERENCE_TEMP_C,
) -> float:
    """측정값에 곱하면 20°C 길이가 되는 계수.

    ruler_cte=None: 저장된 px→mm 배율(상수)로 잰 값 → 1/(1+α제품·ΔT).
    ruler_cte 지정: 이 프레임의 기준자로 잰 값 → (1+α자·ΔT)/(1+α제품·ΔT).
    """
    dt = float(temp_c) - reference_c
    denom = 1.0 + part_cte * dt
    if denom <= 0:
        raise ValueError("비현실적인 온도")
    num = 1.0 + (ruler_cte * dt if ruler_cte is not None else 0.0)
    return num / denom


def cte_from_env(name: str, default: float) -> float:
    """선팽창계수 env — µm/m·K(예: 23.1) 또는 1/K(예: 2.31e-5) 둘 다 받는다."""
    v = _envf(name, default)
    return v * 1e-6 if v > 1e-3 else v
