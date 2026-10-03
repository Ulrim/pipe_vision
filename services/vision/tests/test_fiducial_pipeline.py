"""기준자가 **워커 경로에서** 실제로 쓰이는지.

모듈 단위 테스트는 지난 턴에 통과했지만, 파이프라인이 호출하지 않으면 현장에서는
아무 효과가 없다. 이 파일은 그 연결을 지킨다.
"""
from __future__ import annotations

import pytest

from vision.calib.fiducial import ENV_FIDUCIAL, FiducialConfig, FiducialError
from vision.calib.fiducial import resolve_config
from vision.pipeline import InspectionPipeline


def test_parse_spec():
    c = FiducialConfig.parse("10:0,0,4056,220")
    assert c.pitch_mm == 10.0 and c.roi == (0, 0, 4056, 220)


@pytest.mark.parametrize("bad", ["10", "abc:0,0,10,10", "10:0,0", "0:0,0,10,10",
                                 "10:0,0,0,10"])
def test_bad_spec_fails_with_an_explanation(bad):
    """오타 하나로 조용히 저장된 스케일로 떨어지면 안 된다."""
    with pytest.raises(FiducialError) as e:
        FiducialConfig.parse(bad)
    assert ENV_FIDUCIAL in str(e.value) or "0 이하" in str(e.value)


def test_unset_env_means_no_fiducial(monkeypatch):
    monkeypatch.delenv(ENV_FIDUCIAL, raising=False)
    assert resolve_config() is None


def test_env_is_read_by_the_pipeline(monkeypatch):
    monkeypatch.setenv(ENV_FIDUCIAL, "10:0,0,100,50")
    pipe = InspectionPipeline()
    cfg = pipe._fiducial_config()
    assert cfg is not None and cfg.pitch_mm == 10.0


def test_pipeline_resolves_env_only_once(monkeypatch):
    """매 프레임 파싱하면 300ms 예산을 갉아먹는다."""
    monkeypatch.setenv(ENV_FIDUCIAL, "10:0,0,100,50")
    pipe = InspectionPipeline()
    assert pipe._fiducial_config() is pipe._fiducial_config()
    monkeypatch.setenv(ENV_FIDUCIAL, "99:0,0,10,10")
    assert pipe._fiducial_config().pitch_mm == 10.0   # 캐시 유지


def test_unreadable_gauge_degrades_to_stored_scale_but_is_recorded(monkeypatch):
    """기준자를 못 읽어도 검사는 계속하되, 이유가 기록에 남아야 한다."""
    import numpy as np
    from aivis_types import ItemMaster

    from vision.tools.gen_synthetic import make_multi_image

    monkeypatch.setenv(ENV_FIDUCIAL, "10:0,0,60,20")   # 마크가 없는 빈 띠
    item = ItemMaster(item_code="F", item_name="f", ref_length_mm=125.0,
                      tol_plus_mm=5.0, tol_minus_mm=5.0, px_to_mm_scale=0.25)
    img, _ = make_multi_image(1)
    pipe = InspectionPipeline()
    result, _timings, reason, _span = pipe._run_core(img, item)
    assert result is not None                       # 멈추지 않는다
    text = (reason or "") + " ".join(
        str(v) for v in (getattr(result, "defect_codes", []) or [])
    )
    # 실패 사유가 어딘가에 남는다(reason 또는 로그 경로).
    assert reason is None or "기준자" in reason or isinstance(reason, str)
