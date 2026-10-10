"""검사 1사이클의 처리시간 — 계획서 정의(이미지 취득 ~ 결과 저장)대로 잰다.

§1.2 지표3: "검사 처리속도 300ms/ea 이하 = 이미지 취득~결과 저장까지 평균 소요시간".

2026-10-10 개발 진척 점검: 종전 proc_time_ms 는 **판정(파이프라인) 구간만** 쟀다.
취득(카메라)과 이미지 저장(JPEG 인코딩·디스크/업로드)이 빠져, PC 합성 이미지로
12ms 가 나오는 동안 파이 실해상도에서는 판정만 191ms 였고 저장까지 더하면 그보다
길다. 측정 구간이 짧으면 시험을 해도 증빙이 안 된다. 그래서:

- proc_time_ms(행에 저장, KPI) = 취득 시작 → 이미지 저장 완료(=결과 전송 직전).
  결과 전송(POST → DB 커밋)은 그 행 자신에 담을 수 없어(보내기 전에 값이 정해져야
  한다) 직전 사이클의 전송 시간을 하트비트 `timings.post_ms` 로 따로 보낸다.
- 하트비트 `timings` 에 단계별 분해(grab/infer/save/post)를 실어 실시간 현황과
  로그에서 어디가 느린지 바로 보이게 한다.
- 다발(한 장에 N개): 행의 proc_time_ms 는 **1개당(ms/ea)** = 프레임 전체 ÷ N.
  지표 단위가 ms/ea 이기 때문이다. 프레임 전체는 timings.total_ms 로 함께 남긴다.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Optional


class CycleClock:
    """취득 시작 시점부터 단계 표시(mark)를 받아 ms 로 나눠 준다."""

    STAGES = ("grab", "infer", "save")

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self.t0 = clock()
        self._marks: Dict[str, float] = {}

    def mark(self, stage: str) -> None:
        self._marks[stage] = self._clock()

    def _ms(self, a: float, b: float) -> int:
        return max(0, int(round((b - a) * 1000.0)))

    def total_ms(self) -> int:
        """취득 시작 ~ 마지막 표시(보통 save). 표시가 없으면 지금까지."""
        end = max(self._marks.values()) if self._marks else self._clock()
        return self._ms(self.t0, end)

    def breakdown(self, *, post_ms: Optional[int] = None, n: int = 1) -> dict:
        """{grab_ms, infer_ms, save_ms, total_ms, per_ea_ms, n, post_ms}."""
        out: dict = {}
        prev = self.t0
        for st in self.STAGES:
            if st in self._marks:
                out[f"{st}_ms"] = self._ms(prev, self._marks[st])
                prev = self._marks[st]
        total = self.total_ms()
        out["total_ms"] = total
        n = max(1, int(n))
        out["n"] = n
        out["per_ea_ms"] = int(round(total / n))
        if post_ms is not None:
            out["post_ms"] = int(post_ms)
        return out


def per_ea_ms(total_ms: int, n: int) -> int:
    """다발 1개당 처리시간(ms/ea). n<=0 이면 전체."""
    return int(round(total_ms / n)) if n and n > 0 else int(total_ms)
