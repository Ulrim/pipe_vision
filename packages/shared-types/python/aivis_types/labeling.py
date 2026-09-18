"""정답 라벨링 스키마 (부록 A.5, §5 M16, §1.2 항목별 정확도).

표면 결함 모델을 학습·검증하려면 "이 사진이 유분기인지 변색인지"를 사람이 적어
둔 정답셋이 필요하다. 현장 검사 이미지는 수십 GB 쌓였지만 그 기록이 없어 04 가
막혀 있었다.

라벨은 **배열**이다(부록 A.5). 복합불량은 ["OIL","DIS"] 로 기록되고 §7.2
defect_codes 에 그대로 매핑된다. 빈 배열은 정상(OK)을 뜻한다 — 별도의 "OK" 코드를
두지 않는 이유는, OK 를 코드로 넣으면 ["OK","SCR"] 같은 모순된 라벨이 만들어질 수
있기 때문이다.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .enums import DefectCode

_VALID = {c.value for c in DefectCode}


class LabelIn(BaseModel):
    """라벨 입력 (PUT /labels/{inspection_id})."""

    labels: List[str] = Field(
        default_factory=list,
        description="불량유형 코드 배열(§7.2). 빈 배열 = 정상(OK)",
    )
    border: bool = Field(
        False,
        description=(
            "경계 샘플 — 작업자도 OK/NG 가 갈리는 것(부록 A.2). "
            "버리지 말고 표시해 모아야 정확도 95% 를 넘길 수 있다."
        ),
    )
    length_mm_gt: Optional[float] = Field(
        None, description="길이 정답(mm). 길이 라벨링 시에만"
    )
    note: Optional[str] = Field(None, max_length=500, description="비고")

    @field_validator("labels")
    @classmethod
    def _check_codes(cls, v: List[str]) -> List[str]:
        """코드 화이트리스트 + 중복 제거 + 정렬(결정적 저장)."""
        out = []
        for code in v:
            c = str(code).strip().upper()
            if c not in _VALID:
                raise ValueError(f"알 수 없는 불량유형 코드: {code}")
            if c not in out:
                out.append(c)
        return sorted(out)


class LabelOut(LabelIn):
    """라벨 조회 응답."""

    inspection_id: int
    labeled_by: Optional[str] = None
    labeled_at: Optional[datetime] = None


class LabelQueueItem(BaseModel):
    """라벨링 대기 1건 (GET /labels/queue).

    화면이 이미지를 띄우고 판정을 비교할 수 있도록 시스템 판정을 함께 준다.
    사람이 시스템 판정에 끌려가지 않도록 화면에서는 접어 두는 것을 권장한다.
    """

    inspection_id: int
    lot: str
    item_code: Optional[str] = None
    inspected_at: datetime
    final_verdict: Optional[str] = None
    defect_codes: List[str] = Field(default_factory=list)
    review_flag: bool = False
    meas_length_mm: Optional[float] = None
    has_result_image: bool = False
    has_raw_image: bool = False


class LabelProgress(BaseModel):
    """클래스별 라벨링 진척 (GET /labels/progress).

    목표 수량은 부록 A.2(1차 모델 기준 최소 수량)에서 온다. 화면이 "무엇이
    부족한지"를 보여줘야 검수자가 필요한 것부터 모을 수 있다.
    """

    labeled_total: int = Field(..., ge=0, description="라벨링 완료 건수")
    unlabeled_total: int = Field(..., ge=0, description="남은 건수")
    border_count: int = Field(..., ge=0, description="경계 샘플 수")
    #: 코드 -> {count, target}. 코드 "OK" 는 정상(빈 라벨) 건수.
    by_class: dict = Field(default_factory=dict)
