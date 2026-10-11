"""수집용(학습 데이터) LOT 구분 — 2026-10-11.

표면결함 학습 데이터를 모을 때는 결함 시료를 **일부러** 섞어 찍는다. 그 결과가
그 달 공정불량률·검사불량률·통계에 들어가면 숫자가 망가지고, MES 로 넘어가면
고객 품질 DB 에 시험 데이터가 섞인다. 그래서 LOT 이름으로 구분한다.

규칙: LOT 이 `AIVIS_TRAINING_LOT_PREFIXES`(콤마, 기본 `TRAIN-`) 중 하나로
시작하면(대소문자 무시) 수집용이다.
  - KPI·월간 리포트·통계 화면·포털 KPI 에서 뺀다.
  - MES 스테이징·재전송 대상에서 뺀다(연계율 분모에서도 뺀다).
  - 저장·이미지·라벨링은 평소대로(그것이 목적이다).
  - 작업자 화면과 실시간 현황에 "데이터 수집 중 — KPI 제외" 를 크게 띄운다.
    수집이 끝나고 LOT 을 되돌리는 것을 잊으면 생산 실적이 통째로 빠지기 때문이다.
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import and_, func, or_, true

DEFAULT_PREFIXES = ("TRAIN-",)


def training_prefixes() -> tuple[str, ...]:
    raw = os.getenv("AIVIS_TRAINING_LOT_PREFIXES")
    if raw is None:
        return DEFAULT_PREFIXES
    items = tuple(p.strip() for p in raw.split(",") if p.strip())
    return items  # 빈 값이면 () — 수집용 구분을 끈다


def is_training_lot(lot: Optional[str]) -> bool:
    if not lot:
        return False
    up = str(lot).upper()
    return any(up.startswith(p.upper()) for p in training_prefixes())


def _like_prefix(p: str) -> str:
    # 접두어 안의 LIKE 와일드카드(%, _)는 글자 그대로 — escape 문자는 '\'.
    return p.upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def not_training(col):
    """SQL 조건: col(LOT) 이 수집용이 아니다. 접두어가 없으면 항상 참.

    LOT 이 NULL 인 행은 수집용이 아니다(NOT LIKE 가 NULL 이 되어 빠지지 않게).
    """
    prefixes = training_prefixes()
    if not prefixes:
        return true()
    return or_(
        col.is_(None),
        and_(*[~func.upper(col).like(_like_prefix(p), escape="\\") for p in prefixes]),
    )
