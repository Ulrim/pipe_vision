"""검사 오케스트레이션 (CLAUDE.md §4 7단계 중 ①~④).

trigger → grab → preprocess → length + surface → verdict.
각 단계 proc_time 을 합산해 전체 proc_time_ms 를 계측한다(목표 <300ms).

ItemMaster 는 주입받는다(DB 직접접근 X — dict/스키마로 받음).
최종 산출물은 VerdictResult 이며, backend POST /inspection 본문
(InspectionResult)으로 변환하는 매핑 함수를 제공한다(HTTP 전송은 옵션/스텁).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import numpy as np
from aivis_types import (
    DefectCode,
    InspectionResult,
    ItemMaster,
    LengthResult,
    SurfaceResult,
    Verdict,
    VerdictResult,
)

from .length import LengthSpan, measure_length_ex
from .length.measure import length_window
from .calib.fiducial import (
    FiducialConfig,
    FiducialError,
    FiducialScale,
    measure_scale,
    measure_scale_along,
)
from .calib.fiducial import resolve_config as resolve_fiducial_config
from .calib.lens import LensCalibration, resolve_calibration
from .calib.temperature import (
    CTE_STEEL,
    TemperatureMonitor,
    compensation_factor,
    cte_from_env,
    monitor_from_env,
)
from .preprocess import InspectArea, area_rect, preprocess
from .surface.anomaly import resolve_surface_model
from .surface.model import SurfaceModel
from .verdict import combine_verdict
from .verdict.combine import codes_for_stage


def _to_item(item: Union[ItemMaster, Dict[str, Any]]) -> ItemMaster:
    if isinstance(item, ItemMaster):
        return item
    return ItemMaster(**item)


def _error_verdict(
    item: ItemMaster,
    *,
    proc_time_ms: int,
    defect_codes: Optional[List[DefectCode]] = None,
) -> VerdictResult:
    """예외/실패 시에도 미판정 없이 결정적 NG 를 만든다(자동검사율 100%).

    길이·표면을 '검출 실패' 상태로 채우고 review_flag=True(재확인 대상)로 둔다.
    defect_codes 가 없으면 길이·표면 모두 불명이므로 MULTI 로 표기한다.
    """
    ref = float(item.ref_length_mm)
    length = LengthResult(
        ref_length_mm=ref,
        meas_length_mm=None,
        deviation_mm=None,
        length_verdict=Verdict.NG,
        edge_detected=False,
        proc_time_ms=0,
    )
    surface = SurfaceResult(
        oil_score=None,
        discolor_score=None,
        scratch_score=None,
        surface_verdict=Verdict.NG,
        defect_codes=[],
        proc_time_ms=0,
    )
    codes = defect_codes if defect_codes else [DefectCode.MULTI]
    return VerdictResult(
        final_verdict=Verdict.NG,
        defect_codes=codes,
        confidence=0.0,
        review_flag=True,
        length=length,
        surface=surface,
        proc_time_ms=proc_time_ms,
    )


class _Skipped(Exception):
    """이 모드에서는 이 단계를 보지 않는다 — 오류가 아니라 의도된 건너뜀."""


def _length_not_evaluated(item: ItemMaster) -> LengthResult:
    """길이를 보지 않는 모드의 중립 결과. edge_detected=True 로 두어 '검출 실패'
    로 오인되지 않게 하고, 판정은 OK 로 둔다(모드 게이팅이 어차피 무시한다)."""
    return LengthResult(
        ref_length_mm=float(item.ref_length_mm),
        meas_length_mm=None,
        deviation_mm=None,
        length_verdict=Verdict.OK,
        edge_detected=True,
        proc_time_ms=0,
    )


def _surface_not_evaluated() -> SurfaceResult:
    return SurfaceResult(
        oil_score=None,
        discolor_score=None,
        scratch_score=None,
        surface_verdict=Verdict.OK,
        defect_codes=[],
        proc_time_ms=0,
    )


@dataclass(frozen=True)
class FramePrep:
    """다발 프레임 전체에 **한 번** 한 보정 — 튜브 crop 마다 다시 하면 안 되는 것들.

    렌즈 왜곡 보정은 광학 중심 기준이라 전체 프레임에만 맞다(crop 에 하면 crop 을 센서
    전체로 보고 엉뚱하게 휜다). 기준자도 프레임 좌표로 놓여 있어 전체 프레임에서 읽어야
    한다(crop 에서 읽으면 범위 밖 → '읽기 실패' → 튜브 전부 NG). 2026-10-17.
    """

    frame: np.ndarray
    """보정된 프레임(렌즈 보정이 없으면 원본 그대로). 분할·crop 은 이걸로 한다."""
    lens_applied: bool = False
    gauge: Optional[FiducialScale] = None
    gauge_read: bool = False
    """기준자 설정이 있어 전체 프레임에서 읽기를 시도했는가(성공·실패 무관)."""
    notes: tuple = ()
    """튜브마다 오류로 남길 사유(렌즈 보정 실패·기준자 읽기 실패) — 단일 경로와 같은 규칙."""


@dataclass(frozen=True)
class CropContext:
    """튜브 crop 1개를 잴 때 필요한 프레임 정보.

    origin/direction: crop 의 x 가 기준자 좌표(프레임 x, 세로 다발이면 프레임 y)로
    어디인가. 기준자 좌표 = origin + direction × crop_x.
    """

    prep: FramePrep
    origin: float = 0.0
    direction: int = 1


@dataclass
class StageTimings:
    """단계별 처리시간(ms) 분해 — 처리속도 KPI 회귀/병목 분석용."""

    preprocess_ms: int = 0
    length_ms: int = 0
    surface_ms: int = 0
    verdict_ms: int = 0
    total_ms: int = 0


@dataclass
class InspectionPipeline:
    """단일 프레임 검사 파이프라인.

    표면 모델은 품목별로 지연 선택한다(seam): 학습된 이상탐지 모델이 있으면
    AnomalySurfaceModel, 없으면 ClassicalSurfaceModel(현행)을 쓴다
    (resolve_surface_model, env AIVIS_SURFACE_ANOMALY=on|off|auto). surface_model
    을 명시로 주면 그것을 그대로 쓴다(테스트/고정 배포). 모델이 없을 때 현행 동작과
    100% 동일하다(회귀 없음).
    """

    surface_model: Optional[SurfaceModel] = None
    surface_inset_ratio: float = 0.06
    #: 렌즈 왜곡 보정. None 이면 환경변수(AIVIS_LENS_CALIB)에서 1회 해석한다.
    #: 설정이 없으면 보정 없이 돈다 — 시뮬레이터·합성에는 왜곡이 없고, 현장도
    #: 캘리브레이션 전에는 돌아야 하기 때문이다. 다만 그 상태의 길이값은
    #: 정확도를 보장하지 못한다(§6.2, calib/lens.py 참조).
    lens: Optional[LensCalibration] = None
    #: 프레임별 기준자. None 이면 환경변수(AIVIS_FIDUCIAL)에서 1회 해석한다.
    #: 설정하면 매 프레임 기준자를 읽어 **그 프레임의** 배율로 길이를 잰다 —
    #: 제품 높이·초점이 흔들려도 상쇄된다(calib/fiducial.py). 미설정이면
    #: item_master.px_to_mm_scale(저장된 상수)로 동작한다.
    fiducial: Optional[FiducialConfig] = None
    #: 기본 검사 단계(=모드). 호출마다 stage= 로 덮어쓸 수 있다. None 이면
    #: 종전처럼 길이·표면을 모두 판정한다(옛 호출자·테스트 호환).
    stage: Optional[str] = None
    #: 제품 온도 → 길이 20°C 환산. None 이면 환경변수(AIVIS_TEMP_SOURCE)에서 1회 해석한다.
    #: 미설정이면 환산하지 않는다 — 오차 예산에 열팽창 항이 그대로 남는다(calib/temperature.py).
    temperature: Optional[TemperatureMonitor] = None
    _model_cache: Dict[tuple, SurfaceModel] = field(
        default_factory=dict, repr=False, compare=False
    )
    _lens_resolved: bool = field(default=False, repr=False, compare=False)
    _fiducial_resolved: bool = field(default=False, repr=False, compare=False)
    _temperature_resolved: bool = field(default=False, repr=False, compare=False)

    def _fiducial_config(self) -> Optional[FiducialConfig]:
        """환경변수에서 한 번만 해석한다(매 프레임 파싱하지 않는다)."""
        if self.fiducial is not None:
            return self.fiducial
        if not self._fiducial_resolved:
            self._fiducial_resolved = True
            self.fiducial = resolve_fiducial_config()
        return self.fiducial

    def temperature_monitor(self) -> Optional[TemperatureMonitor]:
        """환경변수에서 한 번만 해석한다. 워커 하트비트도 같은 값을 쓴다."""
        if self.temperature is not None:
            return self.temperature
        if not self._temperature_resolved:
            self._temperature_resolved = True
            self.temperature = monitor_from_env()
        return self.temperature

    def _to_reference_temperature(
        self, length: LengthResult, master: ItemMaster, *, gauge_used: bool
    ) -> LengthResult:
        """측정 길이를 20°C 환산하고 편차·판정을 다시 낸다. 온도를 모르면 그대로(meas_temp_c=None).

        기준자로 잰 값은 자도 같이 늘어난 눈금이라 (1+α자·ΔT)/(1+α제품·ΔT), 저장된 배율로 잰 값은
        1/(1+α제품·ΔT) 이다(calib/temperature.compensation_factor).
        """
        mon = self.temperature_monitor()
        if mon is None or length.meas_length_mm is None:
            return length
        temp = mon.current()
        if temp is None:
            return length  # 센서 오류는 하트비트로 화면에 뜬다. 행에는 '환산 안 함'(NULL)으로 남는다.
        factor = compensation_factor(
            temp,
            part_cte=cte_from_env("AIVIS_PART_CTE", 23.1),
            ruler_cte=cte_from_env("AIVIS_RULER_CTE", CTE_STEEL) if gauge_used else None,
        )
        meas = round(float(length.meas_length_mm) * factor, 3)
        dev = round(meas - float(length.ref_length_mm), 3)
        ok = -float(master.tol_minus_mm) <= dev <= float(master.tol_plus_mm)
        return length.model_copy(update={
            "meas_length_mm": meas,
            "deviation_mm": dev,
            "length_verdict": Verdict.OK if ok else Verdict.NG,
            "meas_temp_c": round(float(temp), 2),
        })

    def _lens_calibration(self) -> Optional[LensCalibration]:
        """환경변수에서 한 번만 해석한다(매 프레임 파일을 읽지 않는다)."""
        if self.lens is not None:
            return self.lens
        if not self._lens_resolved:
            self._lens_resolved = True
            self.lens = resolve_calibration()
        return self.lens

    def _select_surface_model(self, master: ItemMaster) -> SurfaceModel:
        """품목·모드별 표면 모델 선택(캐시). 명시 주입 시 그대로 사용."""
        if self.surface_model is not None:
            return self.surface_model
        model_mode = os.environ.get("AIVIS_SURFACE_MODEL", "auto").lower()
        mode = os.environ.get("AIVIS_SURFACE_ANOMALY", "auto").lower()
        key = (getattr(master, "item_code", None), model_mode, mode)
        model = self._model_cache.get(key)
        if model is None:
            model = resolve_surface_model(
                master, mode=mode, model_mode=model_mode
            )
            self._model_cache[key] = model
        return model

    def prepare_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        axis: str = "horizontal",
        stage: Optional[str] = None,
    ) -> FramePrep:
        """다발 검사 전에 전체 프레임에서 렌즈 보정·기준자 판독을 한 번 한다.

        실패해도 raise 하지 않는다 — 사유를 notes 에 담아 튜브마다 오류로 남긴다(단일
        경로와 같이 NG + 재확인). 기준자는 이 모드가 길이를 볼 때만 읽는다.
        """
        stage = stage if stage is not None else self.stage
        notes: List[str] = []
        frame = frame_bgr
        lens_applied = False
        try:
            calib = self._lens_calibration()
            if calib is not None:
                frame = calib.undistort(frame_bgr)
                lens_applied = True
        except Exception as exc:  # noqa: BLE001
            lens_applied = True  # crop 에서 다시 시도하지 않는다(그게 더 틀린다)
            notes.append(f"렌즈 보정 실패: {type(exc).__name__}: {exc}")

        gauge: Optional[FiducialScale] = None
        gauge_read = False
        if DefectCode.LEN in codes_for_stage(stage):
            try:
                fid = self._fiducial_config()
            except Exception as exc:  # noqa: BLE001 - 설정 오타(resolve_config 가 raise)
                fid = None
                gauge_read = True
                notes.append(f"기준자 설정 오류: {exc}")
            if fid is not None:
                gauge_read = True
                try:
                    gauge = measure_scale_along(frame, fid, axis=axis)
                except FiducialError as exc:
                    notes.append(f"기준자 읽기 실패(저장된 스케일 사용): {exc}")
        return FramePrep(frame=frame, lens_applied=lens_applied, gauge=gauge,
                         gauge_read=gauge_read, notes=tuple(notes))

    def run(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
        *,
        stage: Optional[str] = None,
    ) -> VerdictResult:
        """프레임 1장 → VerdictResult. 결정적, 전체 proc_time_ms 계측.

        어떤 입력/예외에도 미판정 없이 VerdictResult 를 반환한다(자동검사율 100%).
        stage: 검사 단계(=모드). 그 모드의 항목만 판정한다. None 이면 self.stage.
        """
        result, _, _, _ = self._run_core(frame_bgr, item, stage=stage)
        return result

    def run_with_timings(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
        *,
        stage: Optional[str] = None,
    ) -> tuple[VerdictResult, StageTimings]:
        """run() 과 동일하나 단계별 타이밍 분해를 함께 반환."""
        result, timings, _, _ = self._run_core(frame_bgr, item, stage=stage)
        return result, timings

    def run_with_geometry(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
        *,
        stage: Optional[str] = None,
        area: Optional[InspectArea] = None,
        crop: Optional[CropContext] = None,
    ) -> tuple[VerdictResult, Optional[LengthSpan]]:
        """run() + 길이 측정 스팬(끝단 2점·세로 범위, 프레임 좌표). 결과 오버레이가
        측정 근거(끝단/측정선)를 그리도록 워커가 이 값을 save 로 넘긴다.

        span 좌표는 입력 frame_bgr 좌표계다(단일=전체 프레임, 배치=튜브 crop).
        끝단 미검출/ROI 미검출 시 None. shared-types(VerdictResult)는 바꾸지 않고
        별도 채널로 반환한다(오케스트레이터 승인 정책 준수).

        area: 검사 영역(preprocess/area.py) — 그 안에서 가장 큰 제품을 잰다. 렌즈 보정·
        기준자는 전체 프레임 기준 그대로다(그래서 자르지 않고 제품 찾기만 영역으로 한정).

        crop: 다발 튜브 crop 을 잴 때(multi/batch.py). 전체 프레임에서 이미 한 렌즈 보정·
        기준자 판독(prepare_frame)을 쓰고, crop 에서 다시 하지 않는다.
        """
        result, _, _, span = self._run_core(frame_bgr, item, stage=stage, area=area,
                                            crop=crop)
        return result, span

    def run_safe(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
        *,
        stage: Optional[str] = None,
    ) -> tuple[VerdictResult, Optional[str]]:
        """run() + 오류 사유. 결코 raise 하지 않는다.

        반환: (VerdictResult, error_reason). 정상 처리 시 error_reason=None.
        shared-types 스키마(VerdictResult)는 변경하지 않으므로, 사유는 별도
        채널로 반환한다(상위에서 sys_log/HMI 알람에 연결).
        """
        result, _, err, _ = self._run_core(frame_bgr, item, stage=stage)
        return result, err

    # --- 내부: 단계별 예외 격리 + 결정적 NG 폴백 ---
    def _run_core(
        self,
        frame_bgr: np.ndarray,
        item: Union[ItemMaster, Dict[str, Any]],
        *,
        stage: Optional[str] = None,
        area: Optional[InspectArea] = None,
        crop: Optional[CropContext] = None,
    ) -> tuple[VerdictResult, StageTimings, Optional[str], Optional[LengthSpan]]:
        t0 = time.perf_counter()
        # ItemMaster 변환 실패는 호출자 오류이므로 그대로 raise(검사 불가).
        master = _to_item(item)
        stage = stage if stage is not None else self.stage
        # 모드가 보지 않는 항목은 **계산도 하지 않는다.** 계산해 두고 판정에서만
        # 빼면 저장된 행에 길이 NG 가 남아 작업자가 또 헷갈린다. 점수는 None
        # 으로 남겨 "안 봤다" 가 그대로 드러나게 한다.
        allowed = codes_for_stage(stage)
        see_length = DefectCode.LEN in allowed
        see_surface = bool(allowed & {DefectCode.OIL, DefectCode.DIS, DefectCode.SCR})

        # ①' 렌즈 왜곡 보정 — 전처리보다 먼저. 광각 렌즈는 가장자리가 눌려
        # 보이는데 길이는 하필 양 끝단을 쓴다. 보정 없이는 왜곡만으로 공차를
        # 넘는다(calib/lens.py 의 측정값 참조).
        try:
            # 다발 crop 은 전체 프레임에서 이미 보정했다 — 다시 하면 crop 을 센서 전체로 본다.
            calib = None if crop is not None and crop.prep.lens_applied else self._lens_calibration()
            if calib is not None:
                frame_bgr = calib.undistort(frame_bgr)
        except Exception as exc:  # noqa: BLE001
            total = int(round((time.perf_counter() - t0) * 1000))
            reason = f"렌즈 보정 실패: {type(exc).__name__}: {exc}"
            result = _error_verdict(master, proc_time_ms=total)
            return result, StageTimings(total_ms=total), reason, None

        # ② 전처리 — 실패해도 검사 자체는 NG 로 종료(미판정 0).
        try:
            pre = preprocess(frame_bgr, self.surface_inset_ratio, area=area)
        except Exception as exc:  # noqa: BLE001
            total = int(round((time.perf_counter() - t0) * 1000))
            reason = f"preprocess 실패: {type(exc).__name__}: {exc}"
            result = _error_verdict(master, proc_time_ms=total)
            return result, StageTimings(total_ms=total), reason, None

        # 다발: 전체 프레임 보정 단계의 실패 사유도 튜브마다 오류로 남긴다.
        errors: List[str] = list(crop.prep.notes) if crop is not None else []

        # ③ 길이 측정 — 실패 시 끝단검출 실패(NG) 로 격리. 끝단 좌표(endpoints)는
        #    결과 오버레이 측정선 표기용으로 함께 받아 프레임 좌표 span 을 만든다.
        length_span: Optional[LengthSpan] = None
        try:
            if not see_length:
                raise _Skipped()
            if pre.length_roi is not None:
                r = pre.length_roi
                # 측정 창을 끝단 바깥 배경까지 넓힌다(END_MARGIN_PX). 제품 마스크 상자에서 딱
                # 끝나는 창은 밝기 변화가 창 밖이라 서브픽셀로 잴 것이 없다(창 폭 −1px 로
                # 고정됐었다). 검사 영역이 있으면 그 밖으로는 넓히지 않는다.
                rect = area_rect(area, frame_bgr)
                gray_roi, win_x0 = length_window(
                    pre.gray_corrected, r,
                    lo=rect.x0 if rect is not None else 0,
                    hi=rect.x1 if rect is not None else None,
                )
            else:
                win_x0 = 0
                gray_roi = pre.gray_corrected  # ROI 미검출 → 끝단검출 실패 유도.
            # 기준자가 설정돼 있으면 **이 프레임에서** 배율을 읽어 쓴다.
            # 못 읽으면 저장된 스케일로 떨어지되 반드시 기록에 남긴다 —
            # 조용히 떨어지면 정확도가 왜 나빠졌는지 알 수 없다.
            gauge = None
            origin, direction = 0.0, 1
            if crop is not None and crop.prep.gauge_read:
                # 다발: 전체 프레임에서 읽은 기준자(실패 사유는 이미 errors 에 있다).
                gauge = crop.prep.gauge
                origin, direction = crop.origin, crop.direction
            else:
                fid = self._fiducial_config()
                if fid is not None:
                    try:
                        gauge = measure_scale(
                            pre.gray_corrected,
                            pitch_mm=fid.pitch_mm,
                            roi=fid.roi,
                            dark_marks=fid.dark_marks,
                        )
                    except FiducialError as exc:
                        errors.append(f"기준자 읽기 실패(저장된 스케일 사용): {exc}")
            roi_x0 = win_x0
            length, endpoints = measure_length_ex(
                gray_roi, master, gauge=gauge,
                roi_x_offset=origin + direction * float(roi_x0), gauge_dir=direction,
            )
            length = self._to_reference_temperature(length, master, gauge_used=gauge is not None)
            if endpoints is not None and pre.length_roi is not None:
                r = pre.length_roi
                # 로컬(측정 창) x → 프레임 x(+창 시작). 세로는 length_roi 범위.
                length_span = LengthSpan(
                    left_x=win_x0 + endpoints.left_x,
                    right_x=win_x0 + endpoints.right_x,
                    y_top=r.y0,
                    y_bottom=r.y1,
                )
        except _Skipped:
            # 이 모드는 길이를 보지 않는다. 판정에 영향 없는 중립값.
            length = _length_not_evaluated(master)
            length_span = None
        except Exception as exc:  # noqa: BLE001
            errors.append(f"length 실패: {type(exc).__name__}: {exc}")
            length = LengthResult(
                ref_length_mm=float(master.ref_length_mm),
                meas_length_mm=None,
                deviation_mm=None,
                length_verdict=Verdict.NG,
                edge_detected=False,
                proc_time_ms=0,
            )
            length_span = None

        # ③ 표면 판정 — 추론 실패 시 NG(표면 불명) 로 격리.
        anomaly_review = False
        try:
            if not see_surface:
                raise _Skipped()
            surface_model = self._select_surface_model(master)
            if pre.surface_roi is not None:
                region = pre.surface_roi.crop(frame_bgr)
                region_mask = pre.surface_roi.crop(pre.mask)
            else:
                region = frame_bgr
                region_mask = pre.mask
            surface = surface_model.predict(region, master, mask=region_mask)
            # 이상탐지(비지도) 부가결과: 정상 분포 이탈이면 재확인 대상으로만 표시
            # (final NG 강제 X — 미학습 초기 오검 방지). 스키마 미변경 → 별도 채널.
            rep = getattr(surface_model, "last_report", None)
            if rep is not None and getattr(rep, "review_flag", False):
                anomaly_review = True
        except _Skipped:
            surface = _surface_not_evaluated()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"surface 실패: {type(exc).__name__}: {exc}")
            # 표면 불명: 특정 결함을 단정하지 않는다(MULTI 날조 금지).
            # 최종 override 로 NG+review 가 강제되므로 미판정은 없다.
            surface = SurfaceResult(
                oil_score=None,
                discolor_score=None,
                scratch_score=None,
                surface_verdict=Verdict.NG,
                defect_codes=[],
                proc_time_ms=0,
            )

        # ④ 종합 판정 — combine 실패도 NG 폴백으로 보장.
        total = int(round((time.perf_counter() - t0) * 1000))
        try:
            result = combine_verdict(
                length, surface, master, proc_time_ms=total, stage=stage
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"verdict 실패: {type(exc).__name__}: {exc}")
            result = _error_verdict(master, proc_time_ms=total)

        # 이상탐지가 정상 분포 이탈을 알렸다면 재확인 대상으로 표시(OR, 강등만).
        if anomaly_review and not result.review_flag:
            result = result.model_copy(update={"review_flag": True})

        # 단계 오류가 있었다면 신뢰할 수 없는 판정이므로 NG + 재확인 강제
        # (자동검사율 100% 유지하되 오검 방지 — 재확인 대상으로 보낸다).
        if errors:
            result = result.model_copy(
                update={
                    "final_verdict": Verdict.NG.value,
                    "review_flag": True,
                    "confidence": 0.0,
                }
            )

        timings = StageTimings(
            preprocess_ms=getattr(pre, "proc_time_ms", 0),
            length_ms=length.proc_time_ms,
            surface_ms=surface.proc_time_ms,
            verdict_ms=max(
                0,
                total
                - getattr(pre, "proc_time_ms", 0)
                - length.proc_time_ms
                - surface.proc_time_ms,
            ),
            total_ms=total,
        )
        reason = "; ".join(errors) if errors else None
        return result, timings, reason, length_span


def to_inspection_result(
    verdict: VerdictResult,
    *,
    lot: str,
    item_code: str,
    cam_id: str,
    inspection_stage: Optional[str] = None,
    inspected_at: Optional[datetime] = None,
    work_order: Optional[str] = None,
    shift: Optional[str] = None,
    operator: Optional[str] = None,
    raw_image_path: Optional[str] = None,
    result_image_path: Optional[str] = None,
) -> InspectionResult:
    """VerdictResult → InspectionResult (POST /inspection 본문).

    HTTP 전송은 backend 책임. 본 함수는 스키마 매핑만 수행한다.
    id/mes_synced 는 서버가 채운다.
    """
    length = verdict.length
    surface = verdict.surface
    # 모드가 보지 않은 항목은 **None 으로 저장**한다. 길이 모드에서 표면 점수가,
    # 표면 모드에서 길이 판정 'OK' 가 행에 남으면 이력 화면에서 "그때 봤나?"
    # 를 알 수 없고, 단계별 정확도 집계가 오염된다.
    allowed = codes_for_stage(inspection_stage)
    see_length = DefectCode.LEN in allowed
    see_surface = bool(allowed & {DefectCode.OIL, DefectCode.DIS, DefectCode.SCR})
    return InspectionResult(
        lot=lot,
        work_order=work_order,
        item_code=item_code,
        cam_id=cam_id,
        inspection_stage=inspection_stage,
        inspected_at=inspected_at or datetime.now(timezone.utc),
        shift=shift,
        operator=operator,
        ref_length_mm=length.ref_length_mm,
        meas_length_mm=length.meas_length_mm if see_length else None,
        deviation_mm=length.deviation_mm if see_length else None,
        meas_temp_c=length.meas_temp_c if see_length else None,
        length_verdict=length.length_verdict if see_length else None,
        oil_score=surface.oil_score if see_surface else None,
        discolor_score=surface.discolor_score if see_surface else None,
        scratch_score=surface.scratch_score if see_surface else None,
        final_verdict=verdict.final_verdict,
        defect_codes=list(verdict.defect_codes),
        confidence=verdict.confidence,
        raw_image_path=raw_image_path,
        result_image_path=result_image_path,
        proc_time_ms=verdict.proc_time_ms,
        review_flag=verdict.review_flag,
    )


__all__ = [
    "InspectionPipeline",
    "StageTimings",
    "to_inspection_result",
]


# combine_verdict 가 surface_verdict 를 직접 쓰지 않으므로(코드 합집합 기반),
# 표면 폴백에서 surface_verdict=NG/ defect_codes=[MULTI] 를 주면 NG 가 보장된다.
