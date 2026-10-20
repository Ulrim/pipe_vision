"""제품 온도 → 길이 20°C 환산 배선 (2026-10-20, calib/temperature.py).

1. 온도 소스: 고정값·DS18B20(1-wire)·숫자 파일(hwmon 1000배 포함), 고장값 거부.
2. 모니터: 뒤에서 읽은 마지막 값, 오래되면 None + 사유. 검사는 기다리지 않는다.
3. 환산 계수: 저장된 배율이면 1/(1+α제품ΔT), 기준자면 (1+α자ΔT)/(1+α제품ΔT) — 같은 재질이면 1.
4. 파이프라인: 길이·편차·판정을 환산값으로 다시 내고 행에 온도를 남긴다. 열팽창만으로 NG 이던
   제품이 환산하면 OK. 온도를 모르면 그대로(NULL). 길이를 보지 않는 모드는 손대지 않는다.
5. 워커 하트비트에 제품 온도·오류가 실린다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from aivis_types import InspectionStage, ItemMaster, LengthResult, Verdict  # noqa: E402

from vision.calib.fiducial import CTE_ALUMINIUM  # noqa: E402
from vision.calib.temperature import (  # noqa: E402
    CTE_STEEL,
    TemperatureError,
    TemperatureMonitor,
    TemperatureSource,
    compensation_factor,
    cte_from_env,
    monitor_from_env,
    parse_ds18b20,
    parse_number_file,
    parse_source,
)
from vision.pipeline import InspectionPipeline, to_inspection_result  # noqa: E402

W1_OK = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t=23125\n"


# --- 1. 소스 ------------------------------------------------------------------
def test_parse_ds18b20():
    assert parse_ds18b20(W1_OK) == pytest.approx(23.125)
    with pytest.raises(TemperatureError, match="CRC"):
        parse_ds18b20(W1_OK.replace("YES", "NO"))
    with pytest.raises(TemperatureError, match="85"):
        parse_ds18b20(W1_OK.replace("t=23125", "t=85000"))


def test_parse_number_file_and_range():
    assert parse_number_file("23.5\n") == 23.5
    assert parse_number_file("45123") == pytest.approx(45.123), "hwmon 은 1000 배 정수"
    with pytest.raises(TemperatureError, match="범위"):
        parse_number_file("150")


def test_parse_source_variants(tmp_path):
    assert parse_source(None) is None and parse_source("none") is None
    fixed = parse_source("fixed:22.5", offset_c=-0.5)
    assert fixed is not None and fixed.read() == 22.0 and fixed.is_fixed
    dev = tmp_path / "28-0000abcd"
    dev.mkdir()
    (dev / "w1_slave").write_text(W1_OK)
    ds = parse_source("ds18b20", w1_glob=str(tmp_path / "28-*"))
    assert ds is not None and ds.read() == pytest.approx(23.125)
    named = parse_source("ds18b20:28-0000abcd", w1_glob=str(tmp_path / "28-*"))
    assert named is not None and named.path == str(dev / "w1_slave")
    f = tmp_path / "t"
    f.write_text("21.0")
    assert parse_source(f"file:{f}").read() == 21.0
    for bad in ("fixed:abc", "file:", "thermo:1"):
        with pytest.raises(ValueError):
            parse_source(bad)
    with pytest.raises(ValueError, match="DS18B20"):
        parse_source("ds18b20", w1_glob=str(tmp_path / "nothing-*"))


# --- 2. 모니터 ----------------------------------------------------------------
def test_monitor_keeps_last_value_then_goes_stale():
    t = [100.0]
    vals = iter(["22.0", "boom", "boom"])

    def reader(_p):
        v = next(vals)
        if v == "boom":
            raise OSError("센서 빠짐")
        return v

    mon = TemperatureMonitor(TemperatureSource("file", path="x", reader=reader),
                             max_age_s=60, clock=lambda: t[0], start_thread=False)
    assert mon.poll() == 22.0 and mon.current() == 22.0
    t[0] += 30
    assert mon.poll() is None and mon.current() == 22.0, "잠깐 실패는 마지막 값으로"
    t[0] += 40
    mon.poll()
    assert mon.current() is None
    assert "센서 빠짐" in (mon.stale_reason() or "")


def test_fixed_monitor_never_stale():
    t = [0.0]
    mon = TemperatureMonitor(TemperatureSource("fixed", value=25.0), clock=lambda: t[0])
    t[0] += 1e6
    assert mon.current() == 25.0 and mon.stale_reason() is None


def test_monitor_from_env(monkeypatch):
    monkeypatch.delenv("AIVIS_TEMP_SOURCE", raising=False)
    assert monitor_from_env() is None
    monkeypatch.setenv("AIVIS_TEMP_SOURCE", "fixed:24")
    monkeypatch.setenv("AIVIS_TEMP_OFFSET_C", "0.5")
    mon = monitor_from_env()
    assert mon is not None and mon.current() == 24.5
    monkeypatch.setenv("AIVIS_TEMP_SOURCE", "nonsense")
    assert monitor_from_env() is None, "설정 오류는 경고만 — 검사는 환산 없이 계속"


# --- 3. 환산 계수 --------------------------------------------------------------
def test_compensation_factor():
    assert compensation_factor(20.0) == 1.0
    f = compensation_factor(30.0)
    assert f == pytest.approx(1 / (1 + CTE_ALUMINIUM * 10))
    assert 250 * (1 - f) == pytest.approx(0.0578, abs=1e-3), "250mm·10K ≈ 58µm"
    assert compensation_factor(30.0, ruler_cte=CTE_ALUMINIUM) == pytest.approx(1.0), "같은 재질 자는 상쇄"
    steel = compensation_factor(30.0, ruler_cte=CTE_STEEL)
    assert f < steel < 1.0
    assert cte_from_env("NOPE_CTE", 23.1) == pytest.approx(23.1e-6)


# --- 4. 파이프라인 --------------------------------------------------------------
SCALE = 0.25


def _tube_frame(length_px: float, *, h: int = 240, w: int = 1400, up: int = 10) -> np.ndarray:
    big = np.full((h * up, w * up, 3), 30, np.uint8)
    xs, xe = int(round(150.0 * up)), int(round((150.0 + length_px) * up))
    big[(h // 2 - 30) * up:(h // 2 + 30) * up, xs:xe] = 200
    return cv2.resize(big, (w, h), interpolation=cv2.INTER_AREA)


def _item(ref: float) -> ItemMaster:
    return ItemMaster(item_code="HP12", item_name="t", ref_length_mm=ref, tol_plus_mm=0.1,
                      tol_minus_mm=0.1, px_to_mm_scale=SCALE)


def _fixed(temp: float) -> TemperatureMonitor:
    return TemperatureMonitor(TemperatureSource("fixed", value=temp))


def test_pipeline_compensates_length_and_rejudges(monkeypatch):
    monkeypatch.delenv("AIVIS_TEMP_SOURCE", raising=False)
    monkeypatch.delenv("AIVIS_FIDUCIAL", raising=False)
    frame = _tube_frame(1000.6)  # 약 250.15mm — 40°C 에서 잰 250mm 제품(+0.1155mm)
    base = InspectionPipeline(stage=InspectionStage.CUT_LENGTH.value)
    raw = base.run(frame, _item(250.0)).length
    assert raw.meas_temp_c is None
    hot = InspectionPipeline(stage=InspectionStage.CUT_LENGTH.value, temperature=_fixed(40.0))
    comp = hot.run(frame, _item(250.0)).length
    assert comp.meas_temp_c == 40.0
    assert comp.meas_length_mm == pytest.approx(raw.meas_length_mm / (1 + CTE_ALUMINIUM * 20), abs=1e-3)
    assert raw.length_verdict == Verdict.NG.value or raw.length_verdict == Verdict.NG
    assert comp.length_verdict in (Verdict.OK, Verdict.OK.value), (raw, comp)
    assert comp.deviation_mm == pytest.approx(comp.meas_length_mm - 250.0, abs=1e-3)


def test_inspection_row_carries_temperature_only_when_length_is_seen(monkeypatch):
    monkeypatch.delenv("AIVIS_FIDUCIAL", raising=False)
    frame = _tube_frame(1000.0)
    pipe = InspectionPipeline(temperature=_fixed(26.0))
    vr = pipe.run(frame, _item(250.0), stage=InspectionStage.CUT_LENGTH.value)
    row = to_inspection_result(vr, lot="L", item_code="HP12", cam_id="C",
                               inspection_stage=InspectionStage.CUT_LENGTH.value)
    assert row.meas_temp_c == 26.0
    vr2 = pipe.run(frame, _item(250.0), stage=InspectionStage.POST_WASH_SURFACE.value)
    row2 = to_inspection_result(vr2, lot="L", item_code="HP12", cam_id="C",
                                inspection_stage=InspectionStage.POST_WASH_SURFACE.value)
    assert row2.meas_temp_c is None and row2.meas_length_mm is None


def test_unknown_temperature_leaves_length_untouched():
    t = [0.0]
    mon = TemperatureMonitor(TemperatureSource("file", path="x", reader=lambda _p: "x"),
                             clock=lambda: t[0], start_thread=False)
    pipe = InspectionPipeline(temperature=mon)
    lr = LengthResult(ref_length_mm=250, meas_length_mm=250.05, deviation_mm=0.05,
                      length_verdict=Verdict.OK)
    out = pipe._to_reference_temperature(lr, _item(250.0), gauge_used=False)
    assert out == lr and out.meas_temp_c is None


def test_ruler_cte_used_only_with_gauge(monkeypatch):
    monkeypatch.setenv("AIVIS_RULER_CTE", str(CTE_ALUMINIUM * 1e6))
    pipe = InspectionPipeline(temperature=_fixed(35.0))
    lr = LengthResult(ref_length_mm=250, meas_length_mm=250.0, deviation_mm=0.0,
                      length_verdict=Verdict.OK)
    assert pipe._to_reference_temperature(lr, _item(250.0), gauge_used=True).meas_length_mm == 250.0
    assert pipe._to_reference_temperature(lr, _item(250.0), gauge_used=False).meas_length_mm < 250.0


# --- 5. 하트비트 ----------------------------------------------------------------
def test_worker_heartbeat_reports_product_temperature(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("_tw_temp", Path(__file__).with_name("test_worker.py"))
    tw = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(tw)
    from vision.worker.runner import Worker

    backend = tw.FakeBackend(master_requires_auth=True)
    w = Worker(tw._cfg(tmp_path, max_iterations=1), client=tw._client(backend),
               pipeline=InspectionPipeline(temperature=_fixed(23.4)))
    assert w.startup() is True
    w._send_idle_status()
    host = backend.statuses[-1]["host"]
    assert host["product_temp_c"] == 23.4 and host["temp_source"] == "fixed:23.4"
    assert host["temp_error"] is None
    w.shutdown()
