"""카메라 하드웨어 추상화 계층 (HAL) — CLAUDE.md §6.1, M1.

실물 카메라 없이 전 파이프라인을 개발/검증하기 위한 CameraAdapter 인터페이스.
- SimulatorCamera : 샘플 이미지 폴더를 트리거마다 순차 리플레이(개발/테스트 전용).
- GenICamCamera   : GigE/USB3 Vision 실카메라. 통합 단계(P7)에서 벤더 SDK 결선.

환경변수 AIVIS_CAMERA=sim|genicam 으로 스위치(factory.py).
모든 테스트는 AIVIS_CAMERA=sim 으로 통과해야 한다.
"""
from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np


class CameraError(RuntimeError):
    """카메라 취득 실패(설정/grab/close)."""


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


class GenICamCamera(CameraAdapter):
    """GigE Vision / USB3 Vision 실카메라 어댑터 (P7 통합 단계 스텁).

    통합 시 Basler pylon / HIKROBOT MVS 등 벤더 SDK 를 이 어댑터 내부에서만
    결선한다. 상위 인터페이스(configure/grab/close)는 SimulatorCamera 와 동일하게
    유지하므로 파이프라인 코드 변경 없이 교체 가능(HAL).
    """

    def __init__(self, device_id: Optional[str] = None) -> None:
        self.device_id = device_id

    def configure(self, recipe: dict) -> None:  # pragma: no cover - 통합 단계
        raise NotImplementedError(
            "GenICamCamera 는 P7 실카메라 통합 단계에서 벤더 SDK 로 결선한다. "
            "개발/테스트는 AIVIS_CAMERA=sim 을 사용하라."
        )

    def grab(self) -> np.ndarray:  # pragma: no cover - 통합 단계
        raise NotImplementedError("GenICamCamera.grab: 벤더 SDK 결선 필요(P7)")

    def close(self) -> None:  # pragma: no cover - 통합 단계
        pass
