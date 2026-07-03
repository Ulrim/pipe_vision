"""카메라 하드웨어 추상화 계층 (HAL) — CLAUDE.md §6.1, M1.

실물 카메라 없이 전 파이프라인을 개발/검증하기 위한 CameraAdapter 인터페이스.
- SimulatorCamera : 샘플 이미지 폴더를 트리거마다 순차 리플레이(개발/테스트 전용).
- GenICamCamera   : GigE/USB3 Vision 실카메라. 통합 단계(P7)에서 벤더 SDK 결선.
- PiCameraAdapter : Raspberry Pi Camera v3(Sony IMX708) + picamera2. 최종 배포 HW.

환경변수 AIVIS_CAMERA=sim|genicam|picam 으로 스위치(factory.py).
모든 테스트는 AIVIS_CAMERA=sim 으로 통과해야 한다(picamera2 미설치 CI 포함).
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


class PiCameraError(CameraError):
    """Raspberry Pi Camera(picamera2) 취득/설정 실패(런타임).

    GenICamCamera 와 달리 PiCameraAdapter 는 Raspberry Pi 상에서 **실제로
    동작**하는 어댑터다(스텁 아님). 따라서 취득 실패는 순수 취득 오류이지
    "미구현" 이 아니므로 NotImplementedError 를 상속하지 않는다.
    """


class PiCameraSDKError(PiCameraError):
    """picamera2/libcamera 미설치 또는 하드웨어 미가용 시 안내 예외.

    PiCameraError(=CameraError) 계열이라 grab_with_retry 재시도 경로와
    호환된다. GenICamSDKError 와 유사하나 NotImplementedError 는 아니다
    (라즈베리파이에서는 실제 동작하기 때문). 메시지에 설치 안내를 담는다.
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


# =====================================================================
# Raspberry Pi Camera v3 (Sony IMX708) + picamera2 어댑터 — 최종 배포 HW
# =====================================================================
#
# capture_recipe(벤더 중립, item_master.capture_recipe) → picamera2 컨트롤 매핑.
# picamera2 는 라즈베리파이 전용(apt: python3-picamera2)이라 requirements.txt 에
# 넣지 않는다. 아래 순수 함수는 libcamera/hardware 없이 동작하므로 CI 에서 단위
# 테스트 가능하다. 실제 enum 해석(AfMode 등)은 configure() 에서 libcamera 를
# 동적 import 한 뒤 문자열 토큰 → controls.*Enum 으로 변환한다.
#
#   recipe 키          → picamera2 컨트롤        (값/단위)
#   exposure_us        → ExposureTime            (마이크로초, int; 0=자동)
#   analogue_gain      → AnalogueGain            (배수, float; 0=자동)
#   gain_db            → AnalogueGain            (dB → 10**(db/20) 배수 변환)
#   af_mode            → AfMode                  ("Manual"/"Auto"/"Continuous")
#   lens_position      → LensPosition            (디옵터, float; 0=무한대)
#   af_speed           → AfSpeed                 ("Fast"/"Normal")
#   awb_enable         → AwbEnable               (bool)
#   brightness         → Brightness              (float, passthrough)
#   contrast           → Contrast                (float, passthrough)
#   saturation         → Saturation              (float, passthrough)
#   sharpness          → Sharpness               (float, passthrough)
#   "PiCam.<Node>"     → <Node>                  (raw passthrough)
#
# width/height 는 컨트롤이 아니라 still-config 의 size 이므로 여기서 제외한다.
#
# 길이 계측 반복성을 위해 레시피에서 af_mode="manual" + 고정 lens_position 을
# 권장한다(오토포커스 흔들림 → 스케일 변동 방지).

_RECIPE_TO_PICAM = {
    "exposure_us": "ExposureTime",
    "analogue_gain": "AnalogueGain",
    "awb_enable": "AwbEnable",
    "brightness": "Brightness",
    "contrast": "Contrast",
    "saturation": "Saturation",
    "sharpness": "Sharpness",
    "lens_position": "LensPosition",
}

# af_mode / af_speed 토큰 정규화(대소문자·별칭 흡수). 실제 enum 은 configure 에서.
_AF_MODE_TOKENS = {
    "manual": "Manual",
    "auto": "Auto",
    "continuous": "Continuous",
}
_AF_SPEED_TOKENS = {
    "fast": "Fast",
    "normal": "Normal",
}

# 컨트롤이 아닌 키(config size 로 처리) → 매핑 결과에서 제외.
_PICAM_NON_CONTROL_KEYS = ("width", "height")


def map_recipe_to_picamera(recipe: dict) -> dict:
    """capture_recipe(벤더 중립) → picamera2 컨트롤 dict.

    순수 함수(하드웨어/libcamera 불필요) — 단위테스트 대상.
    - AfMode/AfSpeed 는 이 단계에서 **정규 문자열**("Manual"/"Fast" 등)로만
      보관한다. 실제 controls.AfModeEnum/AfSpeedEnum 해석은 configure() 가
      libcamera 를 import 한 뒤 수행한다(순수성 유지).
    - gain_db 는 analogue_gain 이 없을 때만 10**(db/20) 배수로 환산.
    - "PiCam." 접두 키는 raw passthrough(`PiCam.NoiseReductionMode` → 노드명).
    - width/height 는 컨트롤이 아니라 config size → 제외. 알 수 없는 키 무시.
    """
    controls: dict = {}
    src = dict(recipe or {})
    for key, val in src.items():
        if key in _PICAM_NON_CONTROL_KEYS:
            continue
        if key == "exposure_us":
            controls["ExposureTime"] = int(val)
        elif key == "analogue_gain":
            controls["AnalogueGain"] = float(val)
        elif key == "af_mode":
            token = _AF_MODE_TOKENS.get(str(val).strip().lower())
            if token is not None:
                controls["AfMode"] = token
        elif key == "af_speed":
            token = _AF_SPEED_TOKENS.get(str(val).strip().lower())
            if token is not None:
                controls["AfSpeed"] = token
        elif key in _RECIPE_TO_PICAM:
            controls[_RECIPE_TO_PICAM[key]] = val
        elif key.startswith("PiCam."):
            controls[key.split(".", 1)[1]] = val
        # 그 외 키(gain_db 포함)는 아래에서 별도 처리하거나 무시.

    # analogue_gain 이 없고 gain_db 만 있으면 배수로 환산.
    if "AnalogueGain" not in controls and "gain_db" in src:
        controls["AnalogueGain"] = float(10.0 ** (float(src["gain_db"]) / 20.0))
    return controls


def _finalize_pi_frame(arr: np.ndarray, swap_rb: bool) -> np.ndarray:
    """picamera2 capture_array 결과 → 파이프라인 계약(BGR HxWx3 uint8).

    picamera2 의 "RGB888" 포맷은 메모리 순서가 [B,G,R] 이라 OpenCV BGR 과
    바로 호환된다. 따라서 기본은 그대로 반환한다. 다만 일부 센서/스택 조합에서
    채널이 뒤집혀 오는 경우를 대비해 swap_rb=True 면 RGB2BGR 로 교정한다.

    순수 헬퍼(하드웨어 불필요) — 가짜 배열로 단위테스트한다.
    """
    if arr is None:
        raise PiCameraError("PiCameraAdapter: capture_array 가 None 을 반환")
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise PiCameraError(
            f"PiCameraAdapter: 예상치 못한 프레임 형상 {getattr(arr, 'shape', None)} "
            "(HxWx3 uint8 필요)"
        )
    # RGBA 등 4채널이면 앞 3채널만 사용.
    if arr.shape[2] > 3:
        arr = arr[:, :, :3]
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    if swap_rb:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    # RGB888(=BGR 메모리) → 그대로 BGR. 연속 배열 보장.
    return np.ascontiguousarray(arr)


def _parse_size(text: str) -> tuple:
    """"2304x1296" → (2304, 1296). 파싱 실패 시 PiCameraError."""
    try:
        w_str, h_str = str(text).lower().split("x", 1)
        return (int(w_str), int(h_str))
    except (ValueError, AttributeError) as exc:
        raise PiCameraError(
            f"AIVIS_PICAM_SIZE 형식 오류: {text!r} (예: '2304x1296')"
        ) from exc


class PiCameraAdapter(CameraAdapter):
    """Raspberry Pi Camera v3(Sony IMX708) 어댑터 — 최종 배포 HW.

    설계 원칙(CLAUDE.md §6.1):
    - picamera2/libcamera 는 라즈베리파이 전용이라 **동적 import** 로 감싼다.
      미설치(CI/dev) 환경에서도 __init__/close 는 성공하고, 실제 디바이스가
      필요한 configure/grab 시점에 안내 예외(PiCameraSDKError)를 던진다.
    - 상위 인터페이스(configure/grab/close)는 SimulatorCamera/GenICamCamera 와
      동일 → 파이프라인 변경 없이 sim↔picam 교체 가능(HAL 경계).
    - 프레임 계약: BGR HxWx3 uint8. picamera2 "RGB888" 은 메모리 [B,G,R] 이라
      그대로 BGR. (swap_rb=True 면 RGB2BGR 로 교정.)

    환경변수:
    - AIVIS_PICAM_SIZE          : still 해상도 "WxH"(기본 "2304x1296").
      recipe 의 width/height 가 있으면 그쪽이 우선한다.
    - AIVIS_PICAM_SWAP_RB       : "true"/"1" 이면 취득 후 RGB↔BGR 스왑(기본 false).
    - AIVIS_PICAM_WARMUP_FRAMES : start 후 버릴 워밍업 프레임 수(기본 2). AE/AWB
      수렴 및 초기 흐린 프레임 제거용.

    설치(라즈베리파이 OS 64bit):
        sudo apt install -y python3-picamera2
        # venv 사용 시: python -m venv --system-site-packages .venv
    """

    _DEFAULT_SIZE = "2304x1296"
    _DEFAULT_WARMUP = 2

    def __init__(
        self,
        *,
        size: Optional[str] = None,
        swap_rb: Optional[bool] = None,
        warmup_frames: Optional[int] = None,
    ) -> None:
        # 생성은 항상 성공한다(picamera2 미설치여도). 디바이스는 lazy open.
        size_str = size or os.environ.get("AIVIS_PICAM_SIZE", self._DEFAULT_SIZE)
        self._size = _parse_size(size_str)
        if swap_rb is None:
            raw = os.environ.get("AIVIS_PICAM_SWAP_RB", "false").strip().lower()
            swap_rb = raw in ("1", "true", "yes", "on")
        self.swap_rb = bool(swap_rb)
        self.warmup_frames = int(
            warmup_frames
            if warmup_frames is not None
            else os.environ.get("AIVIS_PICAM_WARMUP_FRAMES", self._DEFAULT_WARMUP)
        )
        self._recipe: dict = {}
        self._controls: dict = {}   # 마지막으로 산출한 picamera2 컨트롤(검증/디버깅).
        self._picam2 = None
        self._started = False

    # --- SDK 가용성 ---
    def _require_picamera2(self):
        """picamera2 + libcamera.controls 동적 import. 미설치면 안내 예외.

        반환: (Picamera2 클래스, libcamera.controls 모듈).
        """
        try:
            from picamera2 import Picamera2  # type: ignore
            from libcamera import controls  # type: ignore
        except ImportError as exc:
            raise PiCameraSDKError(
                "picamera2/libcamera 미설치 또는 미가용. 라즈베리파이 OS(64bit)에서 "
                "`sudo apt install -y python3-picamera2` 로 설치하라. venv 사용 시 "
                "시스템 패키지가 보이도록 `python -m venv --system-site-packages` 로 "
                "생성해야 한다(pip 로는 설치 불가·Pi 전용). 개발/테스트는 "
                "AIVIS_CAMERA=sim 을 사용하라."
            ) from exc
        return Picamera2, controls

    def _resolve_af(self, ctrl_controls, resolved: dict) -> None:
        """self._controls 의 AfMode/AfSpeed 문자열 토큰 → libcamera enum 해석.

        resolved(set_controls 로 넘길 dict)를 in-place 로 채운다.
        """
        af_mode = self._controls.get("AfMode")
        if isinstance(af_mode, str):
            enum = {
                "Manual": ctrl_controls.AfModeEnum.Manual,
                "Auto": ctrl_controls.AfModeEnum.Auto,
                "Continuous": ctrl_controls.AfModeEnum.Continuous,
            }.get(af_mode)
            if enum is not None:
                resolved["AfMode"] = enum
        af_speed = self._controls.get("AfSpeed")
        if isinstance(af_speed, str):
            enum = {
                "Fast": ctrl_controls.AfSpeedEnum.Fast,
                "Normal": ctrl_controls.AfSpeedEnum.Normal,
            }.get(af_speed)
            if enum is not None:
                resolved["AfSpeed"] = enum

    def _build_controls(self, ctrl_controls) -> dict:
        """self._controls(문자열 토큰 포함) → set_controls 용 최종 dict.

        AfMode/AfSpeed 는 enum 으로 치환, 나머지는 그대로.
        """
        resolved = {
            k: v
            for k, v in self._controls.items()
            if k not in ("AfMode", "AfSpeed")
        }
        self._resolve_af(ctrl_controls, resolved)
        return resolved

    def _open(self) -> None:
        """디바이스 open + still config + start + 컨트롤 적용 + 워밍업.

        picamera2 미설치면 _require_picamera2() 가 PiCameraSDKError 를 던진다.
        recipe 의 width/height 가 있으면 size 로 사용한다.
        """
        Picamera2, ctrl_controls = self._require_picamera2()
        w, h = self._size
        try:
            rw = self._recipe.get("width")
            rh = self._recipe.get("height")
            if rw and rh:
                w, h = int(rw), int(rh)
            picam2 = Picamera2()
            cfg = picam2.create_still_configuration(
                main={"size": (w, h), "format": "RGB888"}
            )
            picam2.configure(cfg)
            picam2.start()
            resolved = self._build_controls(ctrl_controls)
            if resolved:
                picam2.set_controls(resolved)
            # 워밍업 프레임 버림(AE/AWB 수렴, 초기 흐림 제거).
            for _ in range(max(self.warmup_frames, 0)):
                picam2.capture_array("main")
        except PiCameraError:
            raise
        except Exception as exc:  # noqa: BLE001 - 벤더 예외를 계약 예외로 변환.
            raise PiCameraError(
                f"PiCameraAdapter: 디바이스 open 실패 ({exc})"
            ) from exc
        self._picam2 = picam2
        self._started = True

    def configure(self, recipe: dict) -> None:
        """촬영 레시피 적용. capture_recipe → picamera2 컨트롤 매핑 후 적용.

        매핑(map_recipe_to_picamera)은 하드웨어 없이 수행/검증 가능(단위테스트).
        실제 set_controls 는 디바이스 open 후이므로 picamera2 미설치면
        PiCameraSDKError.
        """
        self._recipe = dict(recipe or {})
        self._controls = map_recipe_to_picamera(self._recipe)
        if not self._started:
            self._open()  # picamera2 미설치면 여기서 안내 예외.
        else:
            # 이미 start 된 경우 컨트롤만 재적용(size 변경은 재start 필요 — 생략).
            _, ctrl_controls = self._require_picamera2()
            resolved = self._build_controls(ctrl_controls)
            if resolved:
                self._picam2.set_controls(resolved)

    @property
    def controls(self) -> dict:
        """직전 configure 가 산출한 picamera2 컨트롤(문자열 토큰 포함, 검증용)."""
        return dict(self._controls)

    def grab(self) -> np.ndarray:
        """1프레임 취득(BGR HxWx3 uint8). 미시작이면 open(=picamera2 필요).

        RGB888(=BGR 메모리)이므로 그대로 반환. swap_rb 면 RGB2BGR 교정.
        """
        if not self._started:
            self._open()  # picamera2 미설치면 안내 예외(여기서 종료).
        try:
            arr = self._picam2.capture_array("main")
        except Exception as exc:  # noqa: BLE001 - 벤더 예외 → 계약 예외.
            raise PiCameraError(
                f"PiCameraAdapter.grab: capture_array 실패 ({exc})"
            ) from exc
        return _finalize_pi_frame(arr, self.swap_rb)

    def close(self) -> None:
        """리소스 해제. 미open 이면 무해."""
        cam = self._picam2
        self._picam2 = None
        self._started = False
        if cam is None:
            return
        for meth in ("stop", "close"):
            try:
                getattr(cam, meth)()
            except Exception:  # noqa: BLE001 - close 는 항상 무해해야 한다.
                pass
