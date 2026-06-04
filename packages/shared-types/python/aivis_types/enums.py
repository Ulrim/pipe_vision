"""AIVIS 공용 열거형(Enum) 정의.

CLAUDE.md §7.2(불량유형 코드표), §7.1(권한 role), 판정 verdict 기준.
모든 서브에이전트(vision / data-mes / backend / frontend)가 공유하는 단일 진실원이다.
"""
from __future__ import annotations

from enum import Enum


class DefectCode(str, Enum):
    """불량유형 코드표 (CLAUDE.md §7.2).

    LEN  : 길이 부적합
    OIL  : 유분기
    DIS  : 변색
    SCR  : 스크래치
    MULTI: 2종 이상 복합 불량
    """

    LEN = "LEN"
    OIL = "OIL"
    DIS = "DIS"
    SCR = "SCR"
    MULTI = "MULTI"


class Verdict(str, Enum):
    """판정 결과. 길이/표면/종합 판정에 공통 사용."""

    OK = "OK"
    NG = "NG"


class Role(str, Enum):
    """사용자 권한 3역할 (CLAUDE.md §5 M14, §7.1 app_user.role).

    operator : 작업자 (검사화면 조회, 재확인 입력)
    quality  : 품질관리자 (기준정보/KPI 관리)
    admin    : 관리자 (전체 + 사용자/권한 관리)
    """

    OPERATOR = "operator"
    QUALITY = "quality"
    ADMIN = "admin"


class LogCategory(str, Enum):
    """시스템 로그 분류 (CLAUDE.md §7.1 sys_log.category, §5 M15)."""

    INSPECT = "inspect"
    DB = "db"
    MES = "mes"
    ERROR = "error"
    USER = "user"


class CameraView(str, Enum):
    """촬영 구도 (부록 A.1). 학습/검사 메타용."""

    END = "END"   # 단면(端面)
    SIDE = "SIDE"  # 측면(側面) — 길이 측정 필수 구도
