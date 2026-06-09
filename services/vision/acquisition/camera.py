"""카메라 하드웨어 추상화 계층 (HAL) — CLAUDE.md §6.1, M1.

실물 카메라 없이 전 파이프라인을 개발/검증하기 위한 CameraAdapter 인터페이스.
- SimulatorCamera : 샘플 이미지 폴더를 트리거마다 순차 리플레이(개발/테스트 전용).
- GenICamCamera   : GigE/USB3 Vision 실카메라. 통합 단계(P7)에서 벤더 SDK 결선.

환경변수 AIVIS_CAMERA=sim|genicam 으로 스위치(factory.py).
모든 테스트는 AIVIS_CAMERA=sim 으로 통과해야 한다.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np


class CameraError(RuntimeError):
    """카메라 취득 실패(설정/grab/close)."""


class GenICamSDKError(CameraError, NotImplementedError):
    """GenICam 실카메라 SDK/GenTL 환경 미구성 시 발생.

    CameraError(취득 실패 계열) 이면서 NotImplementedError(미결선 스텁) 이다.
    → AcquisitionService.grab_with_retry 의 CameraError 핸들링 경로와
      "스텁 미구현" 의미를 동시에 만족한다. 메시지에 필요한 SDK/환경변수를 담는다.
    """


class CameraAdapter(ABC):
    """카메라 어댑터 인터페이스 (CLAUDE.md §6.1).

    실물/시뮬레이터 모두 동일 인터페이스를 구현한다. 상위 파이프라인은
    구현체를 몰라야 한다(HAL 경계).
    """

    @abstractmethod
    def configure(self, recipe: dict) -> None:
        """촬영 레시피(노출/게인/조명) 적용. recipe 는 item_master.capture_recipe."""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """1프레임 취득(BGR np.ndarray, HxWx3 uint8). 실패 시 CameraError."""

    @abstractmethod
    def close(self) -> None:
        """리소스 해제."""

    # --- 공통 컨텍스트 매니저 지원 ---
    def __enter__(self) -> "CameraAdapter":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


_IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


class SimulatorCamera(CameraAdapter):
    """샘플 이미지 폴더를 트리거마다 순차 리플레이. 개발/테스트 전용.

    - dataset_dir 하위(기본 $AIVIS_DATASET_DIR or ./dataset/raw)를 재귀 스캔.
    - view_filter(SIDE/END)가 주어지면 파일명에 _SIDE_/_END_ 포함분만 선택(부록 A.4).
    - 끝에 도달하면 순환(loop).
    - grab() 은 정렬된 순서를 보장 → 결정적.
    """

    def __init__(
        self,
        dataset_dir: Optional[str] = None,
        view_filter: Optional[str] = None,
        loop: bool = True,
    ) -> None:
        base = dataset_dir or os.environ.get("AIVIS_DATASET_DIR") or "dataset/raw"
        self.dataset_dir = Path(base)
        self.view_filter = view_filter.upper() if view_filter else None
        self.loop = loop
        self._recipe: dict = {}
        self._index = 0
        self._files: List[Path] = self._scan()

    def _scan(self) -> List[Path]:
        if not self.dataset_dir.exists():
            return []
        files = [
            p
            for p in sorted(self.dataset_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in _IMG_EXTS
        ]
        if self.view_filter:
            token = f"_{self.view_filter}_"
            files = [p for p in files if token in p.name.upper()]
        return files

    @property
    def files(self) -> List[Path]:
        return list(self._files)

    def configure(self, recipe: dict) -> None:
        # 시뮬레이터는 레시피를 저장만 한다(실카메라는 노출/게인 적용).
        self._recipe = dict(recipe or {})

    def grab(self) -> np.ndarray:
        if not self._files:
            raise CameraError(
                f"SimulatorCamera: no images under {self.dataset_dir} "
                f"(view_filter={self.view_filter})"
            )
        if self._index >= len(self._files):
            if not self.loop:
                raise CameraError("SimulatorCamera: end of dataset (loop=False)")
            self._index = 0
        path = self._files[self._index]
        self._index += 1
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise CameraError(f"SimulatorCamera: failed to decode {path}")
        return img

    @property
    def last_index(self) -> int:
        """직전 grab() 이 반환한 파일의 인덱스(0-base)."""
        return (self._index - 1) % max(len(self._files), 1)

    def current_path(self) -> Optional[Path]:
        if not self._files:
            return None
        return self._files[self.last_index]

    def reset(self) -> None:
        self._index = 0

    def close(self) -> None:
        self._files = []
        self._index = 0


# --- capture_recipe(JSONB) → GenICam(SFNC) 노드맵 키 매핑 ---
#
# ItemMaster.capture_recipe 는 벤더 중립 키를 쓴다(품목별 촬영 레시피, M1/M13).
# 실 SDK 결선 시 아래 표준 GenICam SFNC(Standard Features Naming Convention)
# 노드명으로 변환한다. 벤더가 비표준 노드명을 쓰면 GENICAM_NODE_ALIASES 로 보정.
#
#   recipe 키          → SFNC 노드명          (값/단위)
#   exposure_us        → ExposureTime         (마이크로초, float)
#   exposure_auto      → ExposureAuto         (Off/Once/Continuous)
#   gain_db            → Gain                 (dB, float)
#   gain_auto          → GainAuto             (Off/Once/Continuous)
#   pixel_format       → PixelFormat          (예: BGR8/RGB8/BayerRG8/Mono8)
#   trigger_mode       → TriggerMode          (On/Off)
#   trigger_source     → TriggerSource        (Line0/Software/...)
#   width / height     → Width / Height       (ROI, px)
#   offset_x / offset_y→ OffsetX / OffsetY    (ROI 원점, px)
#   black_level        → BlackLevel           (float)
#   gamma              → Gamma                (float)
#
# 조명(lighting/illumination)은 카메라 노드가 아니라 별도 컨트롤러(스트로브/IO)
# 소관이다. 여기서는 strobe(LineSelector/LineSource) 노드로 전달할 값만 추려
# self._strobe 에 보관하고, 실제 조명 제어 결선은 통합 단계에서 추가한다.
_RECIPE_TO_SFNC = {
    "exposure_us": "ExposureTime",
    "exposure_auto": "ExposureAuto",
    "gain_db": "Gain",
    "gain_auto": "GainAuto",
    "pixel_format": "PixelFormat",
    "trigger_mode": "TriggerMode",
    "trigger_source": "TriggerSource",
    "width": "Width",
    "height": "Height",
    "offset_x": "OffsetX",
    "offset_y": "OffsetY",
    "black_level": "BlackLevel",
    "gamma": "Gamma",
}

# 카메라 노드가 아닌 키(조명/스트로브)는 별도 보관.
_STROBE_KEYS = ("lighting", "illumination", "strobe", "light_intensity")


def map_recipe_to_genicam(recipe: dict) -> dict:
    """capture_recipe(벤더 중립) → GenICam SFNC 노드맵 {노드명: 값}.

    - 알 수 없는 키는 무시(로그 대상)하되, 'GenICam.' 접두 키는 raw passthrough
      로 그대로 전달(`GenICam.SomeVendorNode` → `SomeVendorNode`).
    - 조명/스트로브 키(_STROBE_KEYS)는 결과에 포함하지 않는다(별도 처리).
    - 임계/보정계수가 아니라 촬영 파라미터이므로 하드코딩 금지 원칙과 무관하나,
      값은 전적으로 recipe(ItemMaster)에서 온다.
    """
    nodes: dict = {}
    for key, val in (recipe or {}).items():
        if key in _STROBE_KEYS:
            continue
        if key in _RECIPE_TO_SFNC:
            nodes[_RECIPE_TO_SFNC[key]] = val
        elif key.startswith("GenICam."):
            nodes[key.split(".", 1)[1]] = val
        # 그 외 키는 무시(매핑표/별칭으로 확장).
    return nodes


def extract_strobe_config(recipe: dict) -> dict:
    """capture_recipe 에서 조명/스트로브 관련 값만 추출."""
    return {k: v for k, v in (recipe or {}).items() if k in _STROBE_KEYS}


class GenICamCamera(CameraAdapter):
    """GigE Vision / USB3 Vision 실카메라 어댑터 (P7 실카메라 통합).

    설계 원칙(CLAUDE.md §2.2, §6.1, §11):
    - 벤더 SDK **본체는 범위 외**다. 본 어댑터는 GenTL/GenICam 표준 경유 **연동
      어댑터**만 제공한다. SDK 는 동적 import 로 감싸 **미설치 환경에서도
      import 시 죽지 않는다**(__init__ 성공). 실제 디바이스 접근이 필요한
      configure/grab 시점에 SDK/환경 미구성이면 안내 예외(GenICamSDKError)를
      던진다.
    - 상위 인터페이스(configure/grab/close)는 SimulatorCamera 와 동일하므로
      파이프라인 코드 변경 없이 sim↔genicam 교체 가능(HAL 경계).

    환경변수:
    - AIVIS_GENICAM_BACKEND : 'harvesters'(기본) | 'pypylon'. GenTL 표준 경로는
      harvesters(+CTI producer), Basler 전용은 pypylon.
    - AIVIS_GENICAM_CTI     : GenTL producer(.cti) 파일 경로(harvesters 필수).
      예) /opt/pylon/lib/gentlproducer/gtl/ProducerGEV.cti
    - AIVIS_GENICAM_DEVICE  : 디바이스 선택(serial/user-id/index). 미지정 시 0번.
    - AIVIS_GENICAM_TIMEOUT_MS : grab 타임아웃(ms, 기본 1000).

    통합 단계 작업 목록(TODO, SDK 결선 지점):
    1. _open_backend(): harvesters Harvester() 생성 + add_file(cti) +
       update() + create_image_acquirer(선택자) / 또는 pypylon
       TlFactory.GetInstance().CreateFirstDevice().
    2. configure(): map_recipe_to_genicam() 결과를 node_map 에 set.
       ia.remote_device.node_map.<Node>.value = <val>.
    3. grab(): ia.start_acquisition() 1회 + fetch_buffer(timeout) →
       component → numpy → _to_bgr(pixel_format) 변환.
    4. close(): ia.stop_acquisition()/destroy(), Harvester.reset().
    5. 재연결: grab 실패(타임아웃/링크다운) 시 _reconnect() 후 1회 재시도.
    """

    _DEFAULT_TIMEOUT_MS = 1000

    def __init__(
        self,
        device_id: Optional[str] = None,
        *,
        backend: Optional[str] = None,
        cti_path: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:
        # 생성은 항상 성공한다(SDK 없어도). 디바이스 접근은 lazy.
        self.device_id = device_id or os.environ.get("AIVIS_GENICAM_DEVICE")
        self.backend = (
            backend or os.environ.get("AIVIS_GENICAM_BACKEND", "harvesters")
        ).strip().lower()
        self.cti_path = cti_path or os.environ.get("AIVIS_GENICAM_CTI")
        self.timeout_ms = int(
            timeout_ms
            if timeout_ms is not None
            else os.environ.get("AIVIS_GENICAM_TIMEOUT_MS", self._DEFAULT_TIMEOUT_MS)
        )
        self._recipe: dict = {}
        self._node_values: dict = {}   # 마지막으로 적용한 SFNC 노드 값(검증/디버깅용).
        self._strobe: dict = {}
        self._pixel_format: str = "BGR8"
        self._connected = False
        # SDK 핸들(통합 단계에서 채움). harvesters: (Harvester, ImageAcquirer).
        self._harvester = None
        self._acquirer = None

    # --- SDK 가용성/연결 ---
    def _require_sdk(self):
        """동적 import 로 백엔드 SDK 를 로드. 미설치면 안내 예외.

        반환: import 된 모듈(harvesters 또는 pypylon.pylon).
        """
        if self.backend == "harvesters":
            try:
                import harvesters.core as hv  # type: ignore
            except ImportError as exc:
                raise GenICamSDKError(
                    "GenICam(harvesters) SDK 미설치. 실카메라 통합 시 "
                    "`pip install harvesters` 후 GenTL producer(.cti) 경로를 "
                    "환경변수 AIVIS_GENICAM_CTI 로 지정하라(예: Basler pylon / "
                    "HIKROBOT MVS 의 *.cti). 개발/테스트는 AIVIS_CAMERA=sim 사용."
                ) from exc
            if not self.cti_path:
                raise GenICamSDKError(
                    "AIVIS_GENICAM_CTI 미설정. GenTL producer(.cti) 파일 경로가 "
                    "필요하다(harvesters). 예) /opt/mvIMPACT/lib/.../mvGenTLProducer.cti"
                )
            if not Path(self.cti_path).exists():
                raise GenICamSDKError(
                    f"GenTL producer(.cti) 경로가 존재하지 않음: {self.cti_path}"
                )
            return hv
        if self.backend == "pypylon":
            try:
                from pypylon import pylon  # type: ignore
            except ImportError as exc:
                raise GenICamSDKError(
                    "Basler pypylon SDK 미설치. 실카메라 통합 시 "
                    "`pip install pypylon` 하라. 개발/테스트는 AIVIS_CAMERA=sim."
                ) from exc
            return pylon
        raise GenICamSDKError(
            f"AIVIS_GENICAM_BACKEND={self.backend!r} 미지원. "
            "'harvesters' 또는 'pypylon' 을 사용하라."
        )

    def _open_backend(self) -> None:
        """SDK 핸들 생성 + 디바이스 오픈. (통합 단계 결선 지점)

        SDK 미설치/미구성이면 _require_sdk() 가 GenICamSDKError 를 던진다.
        """
        self._require_sdk()
        # TODO(P7): 실제 디바이스 오픈 결선.
        #   harvesters:
        #     h = sdk.Harvester(); h.add_file(self.cti_path); h.update()
        #     self._acquirer = h.create(<selector by self.device_id or 0>)
        #     self._harvester = h
        #   pypylon:
        #     tl = sdk.TlFactory.GetInstance()
        #     self._acquirer = sdk.InstantCamera(tl.CreateFirstDevice()); .Open()
        raise GenICamSDKError(
            "GenICamCamera 디바이스 오픈은 P7 통합 단계에서 결선한다 "
            f"(backend={self.backend}, cti={self.cti_path}, device={self.device_id}). "
            "인터페이스/레시피 매핑은 준비됨; 실 SDK 호출만 남았다."
        )

    def configure(self, recipe: dict) -> None:
        """촬영 레시피 적용. capture_recipe → GenICam SFNC 노드맵 매핑 후 set.

        매핑(map_recipe_to_genicam)은 SDK 없이도 수행/검증 가능(단위테스트 대상).
        실제 node set 은 디바이스 연결 후이므로 SDK 미구성이면 GenICamSDKError.
        """
        self._recipe = dict(recipe or {})
        self._node_values = map_recipe_to_genicam(self._recipe)
        self._strobe = extract_strobe_config(self._recipe)
        pf = self._node_values.get("PixelFormat")
        if isinstance(pf, str):
            self._pixel_format = pf
        if not self._connected:
            self._open_backend()  # SDK 미구성이면 여기서 안내 예외.
        # TODO(P7): node_map 에 self._node_values 를 순서대로 set.
        #   for node, value in self._node_values.items():
        #       getattr(self._acquirer.remote_device.node_map, node).value = value
        #   스트로브/조명(self._strobe)은 LineSelector/LineSource 로 결선.

    @property
    def node_values(self) -> dict:
        """직전 configure 가 산출한 SFNC 노드 값(검증/디버깅)."""
        return dict(self._node_values)

    def _to_bgr(self, raw: np.ndarray, pixel_format: str) -> np.ndarray:
        """벤더 픽셀포맷 → OpenCV BGR(HxWx3 uint8) 변환 골격.

        파이프라인은 BGR 을 가정한다(SimulatorCamera 와 동일 계약).
        """
        pf = (pixel_format or "").lower()
        if raw.ndim == 2:
            if "bayer" in pf:
                code = {
                    "bayerrg8": cv2.COLOR_BAYER_RG2BGR,
                    "bayergr8": cv2.COLOR_BAYER_GR2BGR,
                    "bayergb8": cv2.COLOR_BAYER_GB2BGR,
                    "bayerbg8": cv2.COLOR_BAYER_BG2BGR,
                }.get(pf, cv2.COLOR_BAYER_RG2BGR)
                return cv2.cvtColor(raw, code)
            # Mono → 3ch BGR.
            return cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        if raw.ndim == 3 and raw.shape[2] == 3:
            if "rgb" in pf:
                return cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
            return raw  # 이미 BGR.
        raise CameraError(
            f"GenICamCamera: 지원하지 않는 프레임 형상 {raw.shape}/{pixel_format}"
        )

    def grab(self) -> np.ndarray:
        """1프레임 취득(BGR). 미연결이면 연결 시도(=SDK 필요).

        통합 단계 결선:
            self._acquirer.start_acquisition(run_in_background=False)
            with self._acquirer.fetch(timeout=self.timeout_ms/1000) as buf:
                comp = buf.payload.components[0]
                arr = comp.data.reshape(comp.height, comp.width, -1)
                return self._to_bgr(arr, self._pixel_format)
        """
        if not self._connected:
            self._open_backend()  # SDK 미구성이면 안내 예외(여기서 종료).
        # TODO(P7): 실제 fetch_buffer + _to_bgr. 타임아웃/링크다운 시 _reconnect().
        raise GenICamSDKError(
            "GenICamCamera.grab: 실 SDK fetch 결선 필요(P7). "
            "레시피 매핑/픽셀포맷 변환/타임아웃 골격은 준비됨."
        )

    def _reconnect(self) -> None:  # pragma: no cover - 통합 단계
        """링크다운/타임아웃 후 재연결(통합 단계 결선)."""
        self.close()
        self._open_backend()
        if self._recipe:
            self.configure(self._recipe)

    def close(self) -> None:
        """리소스 해제. SDK 핸들이 없으면 무해(생성만 하고 안 쓴 경우)."""
        # TODO(P7): acquirer.stop_acquisition()/destroy(); harvester.reset().
        self._acquirer = None
        self._harvester = None
        self._connected = False
