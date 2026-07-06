"""AIVIS 공용 스키마 단일 진실원 (CLAUDE.md §7).

vision / data-mes / backend / frontend(via TS mirror) 가 공유한다.
필드명/타입은 packages/shared-types/ts/src/index.ts 와 1:1 일치해야 한다.
"""
from __future__ import annotations

from .enums import (
    CameraView,
    DefectCode,
    LogCategory,
    Role,
    Verdict,
)
from .inspection import (
    CalibrationRequest,
    InspectionImages,
    InspectionResult,
    ItemMaster,
    ItemMasterCreate,
    ItemMasterUpdate,
    ReviewUpdate,
)
from .vision import (
    LengthResult,
    SurfaceResult,
    VerdictResult,
)
from .kpi import (
    KpiManual,
    KpiSummary,
)
from .auth import (
    LoginRequest,
    SysLog,
    TokenResponse,
    UserCreate,
    UserPublic,
)

__all__ = [
    # enums
    "CameraView",
    "DefectCode",
    "LogCategory",
    "Role",
    "Verdict",
    # inspection / master
    "CalibrationRequest",
    "InspectionImages",
    "InspectionResult",
    "ItemMaster",
    "ItemMasterCreate",
    "ItemMasterUpdate",
    "ReviewUpdate",
    # vision pipeline
    "LengthResult",
    "SurfaceResult",
    "VerdictResult",
    # kpi
    "KpiManual",
    "KpiSummary",
    # auth / logs
    "LoginRequest",
    "SysLog",
    "TokenResponse",
    "UserCreate",
    "UserPublic",
]

__version__ = "0.1.0"
