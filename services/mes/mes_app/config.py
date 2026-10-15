"""MES 설정(환경변수). 값은 프로세스 시작 시 한 번 읽는다(get_settings 캐시)."""
from __future__ import annotations

import os
from functools import lru_cache


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def normalize_db_url(url: str) -> str:
    """`postgres://`·`postgresql://`(Supabase 대시보드 형식) → psycopg(v3) 드라이버 주소."""
    u = (url or "").strip()
    for head in ("postgres://", "postgresql://"):
        if u.startswith(head):
            return "postgresql+psycopg://" + u[len(head):]
    return u


class Settings:
    def __init__(self) -> None:
        # MES 자체 DB. 독립형 파이는 sqlite 파일, 서버는 Postgres(별도 데이터베이스).
        # 클라우드(2026-10-15): AIVIS 와 같은 Supabase DB 하나를 쓴다(MES 표는 mes_ 접두어).
        # 그래서 MES_* 가 없으면 DATABASE_URL 을 둘 다로 쓴다 — Render 에 주소를 한 번만 넣게.
        shared = os.getenv("DATABASE_URL", "")
        self.database_url: str = normalize_db_url(
            os.getenv("MES_DATABASE_URL") or shared or "sqlite:///./mes.db")
        # AIVIS 연계 인터페이스 DB(mes_quality_if 가 있는 곳). 비우면 폴링을 끈다
        # — 그때는 REST 수신(POST /intake/quality)만 받는다.
        self.aivis_db_url: str = normalize_db_url(os.getenv("MES_AIVIS_DB_URL") or shared)
        self.poll_interval_s: float = _f("MES_POLL_INTERVAL_S", 10.0)
        self.poll_batch: int = int(_f("MES_POLL_BATCH", 500))
        # REST 수신 토큰. 설정하면 AIVIS 가 `Authorization: Bearer <토큰>` 을 실어야 한다
        # (AIVIS 쪽 MES_REST_TOKEN 과 같은 값).
        self.intake_token: str = os.getenv("MES_INTAKE_TOKEN", "").strip()
        # 로그인은 AIVIS 와 하나 — 같은 서명키로 AIVIS 가 발급한 토큰을 그대로 검증한다.
        self.jwt_secret: str = os.getenv("JWT_SECRET", "dev-insecure-change-me")
        self.jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
        raw = os.getenv("ALLOWED_ORIGINS", "")
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        self.allowed_origins: list[str] = origins or ["*"]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def table_intake_enabled(self) -> bool:
        return bool(self.aivis_db_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
