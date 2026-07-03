"""MES 연계 설정 (환경변수 기반, CLAUDE.md §7.3).

MES_MODE = table | rest 로 인터페이스 방식을 전환한다. §7.3 우선순위에 따라
기본값은 'table'(스테이징 테이블, 가장 안정). REST 모드는 외부 MES 엔드포인트로
직접 POST 전송하고 실패 시 지수 백오프 재시도 큐로 연계율 100% 를 보장한다.

backend 소유의 core.config.Settings.mes_mode 를 단일 진실원으로 재사용하고,
MES 전용 추가 파라미터(엔드포인트/주기/배치/백오프)는 여기서 읽는다.
core/config.py 는 변경하지 않는다(소유권 경계).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core.config import get_settings

# 유효 모드 화이트리스트.
_VALID_MODES = {"table", "rest"}


def _int(env: str, default: int) -> int:
    raw = os.getenv(env)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(env: str, default: float) -> float:
    raw = os.getenv(env)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class MesConfig:
    """MES 연계 런타임 설정 스냅샷."""

    mode: str                     # table | rest
    rest_url: str | None          # 외부 MES REST 엔드포인트 (rest 모드)
    rest_timeout_s: float         # REST 요청 타임아웃(초)
    idem_header: str              # 멱등키 전달 헤더명
    watchdog_interval_s: float    # 워치독 주기(초)
    watchdog_batch_size: int      # 1회 재시도 배치 크기
    max_retry: int                # 행별 최대 재시도 횟수(초과 시 실패로 표시)
    backoff_base_s: float         # 지수 백오프 기준(초)
    backoff_max_s: float          # 지수 백오프 상한(초)

    @property
    def is_rest(self) -> bool:
        return self.mode == "rest"

    @property
    def is_table(self) -> bool:
        return self.mode == "table"


def get_mes_config() -> MesConfig:
    """환경변수에서 MES 설정을 읽어 스냅샷 생성.

    캐시하지 않는다 → 테스트/워치독이 env 를 바꿔 재구성할 수 있게.
    """
    mode = (get_settings().mes_mode or "table").strip().lower()
    if mode not in _VALID_MODES:
        # 잘못된 값은 가장 안정한 table 로 폴백(§7.3 우선순위).
        mode = "table"

    return MesConfig(
        mode=mode,
        rest_url=os.getenv("MES_REST_URL") or None,
        rest_timeout_s=_float("MES_REST_TIMEOUT_S", 5.0),
        idem_header=os.getenv("MES_IDEM_HEADER", "X-Idempotency-Key"),
        watchdog_interval_s=_float("MES_WATCHDOG_INTERVAL_S", 10.0),
        watchdog_batch_size=_int("MES_WATCHDOG_BATCH", 100),
        max_retry=_int("MES_MAX_RETRY", 8),
        backoff_base_s=_float("MES_BACKOFF_BASE_S", 0.5),
        backoff_max_s=_float("MES_BACKOFF_MAX_S", 30.0),
    )


def backoff_delay(cfg: MesConfig, attempt: int) -> float:
    """지수 백오프 지연(초). attempt 는 0부터. 상한 적용."""
    if attempt < 0:
        attempt = 0
    delay = cfg.backoff_base_s * (2 ** attempt)
    return min(delay, cfg.backoff_max_s)
