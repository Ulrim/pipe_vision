"""vision 서브모듈 import 부트스트랩 — flat(컨테이너) / 패키지(dev·테스트) 양립.

컨테이너(services/vision/Dockerfile)는 `COPY services/vision/ .` 로 소스를 /app 에
flat 배치하므로 `python -m worker` 가 `pipeline`, `acquisition` 을 최상위 모듈로
import 한다. 반면 개발/테스트(conftest)는 services/ 를 sys.path 에 올려 `vision.*`
패키지로 import 한다.

본 모듈은 두 경우 모두에서 동일한 심볼을 제공한다:
    - InspectionPipeline, to_inspection_result  (pipeline)
    - create_camera, create_trigger, AcquisitionService, get_camera_mode  (acquisition)
    - write_dataset, DEFAULT_PIPE_LEN_PX        (tools.gen_synthetic)

flat 모드일 때 /app(현재 작업 디렉터리 또는 worker 패키지의 부모)이 sys.path 에
없으면 추가해 최상위 모듈 import 가 가능하도록 보강한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_paths() -> None:
    """vision 소스 루트를 sys.path 에 보강(flat / 패키지 양쪽).

    - worker 패키지의 부모(= services/vision 또는 컨테이너 /app)를 path 에 올려
      `import pipeline`/`import acquisition` 이 가능하게 한다(flat 모드).
    - services/ (vision 의 부모의 부모)도 올려 `import vision.*` 가능하게 한다(dev).
    """
    import importlib
    import importlib.machinery
    import importlib.util

    here = Path(__file__).resolve()
    vision_root = here.parents[1]      # services/vision  또는  /app
    services_root = here.parents[2]    # services        또는  /

    # 1) dev/테스트: services/ 가 path 에 있으면 `import vision.*` 가 그냥 된다.
    if str(services_root) and str(services_root) not in sys.path:
        sys.path.append(str(services_root))

    # 2) flat(컨테이너 /app): `vision` 패키지가 import 불가하면, 현재 디렉터리를
    #    `vision` 패키지로 합성 등록한다. vision 내부 모듈들이 상대 import
    #    (`from .length import ...`)를 쓰므로 반드시 패키지 컨텍스트가 필요하다.
    if importlib.util.find_spec("vision") is None:
        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(
                "vision",
                loader=None,
                is_package=True,
            )
        )
        pkg.__path__ = [str(vision_root)]  # type: ignore[attr-defined]
        sys.modules.setdefault("vision", pkg)


_ensure_paths()


def _import_symbols():
    """항상 `vision.*` 로 import. flat 레이아웃은 _ensure_paths 가 vision 패키지를
    합성 등록해 상대 import(`from .length import ...`)가 동작한다."""
    from vision.pipeline import InspectionPipeline, to_inspection_result
    from vision.acquisition import (
        AcquisitionService,
        create_camera,
        create_trigger,
        get_camera_mode,
    )
    from vision.tools.gen_synthetic import (
        DEFAULT_PIPE_LEN_PX,
        write_dataset,
    )
    return {
        "InspectionPipeline": InspectionPipeline,
        "to_inspection_result": to_inspection_result,
        "AcquisitionService": AcquisitionService,
        "create_camera": create_camera,
        "create_trigger": create_trigger,
        "get_camera_mode": get_camera_mode,
        "DEFAULT_PIPE_LEN_PX": DEFAULT_PIPE_LEN_PX,
        "write_dataset": write_dataset,
    }


_SYMS = _import_symbols()

InspectionPipeline = _SYMS["InspectionPipeline"]
to_inspection_result = _SYMS["to_inspection_result"]
AcquisitionService = _SYMS["AcquisitionService"]
create_camera = _SYMS["create_camera"]
create_trigger = _SYMS["create_trigger"]
get_camera_mode = _SYMS["get_camera_mode"]
DEFAULT_PIPE_LEN_PX = _SYMS["DEFAULT_PIPE_LEN_PX"]
write_dataset = _SYMS["write_dataset"]

__all__ = list(_SYMS.keys())
