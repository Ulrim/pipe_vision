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
    COUNT: 개수 불일치 (검출 개수 ≠ expected_count) — 개수 확인 모드의 NG 사유
    MULTI: 2종 이상 복합 불량
    """

    LEN = "LEN"
    OIL = "OIL"
    DIS = "DIS"
    SCR = "SCR"
    COUNT = "COUNT"
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


class InspectionStage(str, Enum):
    """검사 단계 (데이터 정의서 3-3/4-3/5-3 필수 항목).

    공정상 두 지점에서 서로 다른 것을 본다. 촬영 조건이 정반대라 한 스테이션이
    겸할 수 없다 — 길이는 실루엣을 얻으려 뒤에서 빛을 쏘고(백라이트), 표면은
    앞에서 고르게 비추거나(확산광) 흠집 그림자를 세우려 낮게 비춘다(사광).
    같은 제품이라도 어느 단계에서 찍혔는지에 따라 판정 근거와 학습 분포가
    달라지므로, 이미지·라벨·판정 레코드 전부에 이 값을 싣는다.

    단일 스테이션 운영이면 워커 설정(AIVIS_INSPECTION_STAGE)으로 하나를 고정한다.
    테스트 벤치처럼 한 대로 번갈아 볼 때는 active_order.inspection_stage 로
    재시작 없이 바꾼다(HMI 오더 설정).

    **단계는 곧 검사 모드다(2026-10-05).** 종전에는 세 가지를 한꺼번에 돌리고
    판정도 합쳐 냈는데, 현장에서 "NG 가 왜 났는지 모르겠다" 는 말이 나왔다.
    그래서 단계마다 **그 단계가 보는 항목만** 판정에 넣는다:
      CUT_LENGTH         → 길이(LEN)만
      POST_WASH_SURFACE  → 표면(OIL/DIS/SCR)만
      CRATE_COUNT        → 개수(COUNT)만
    """

    CUT_LENGTH = "CUT_LENGTH"                # 절단 후 길이 검사
    POST_WASH_SURFACE = "POST_WASH_SURFACE"  # 세척 후 표면 검사
    CRATE_COUNT = "CRATE_COUNT"              # 크레이트 단면 개수 확인
