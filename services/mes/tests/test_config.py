"""MES 설정 — 클라우드에서는 AIVIS 와 같은 Supabase DB 하나(2026-10-15)."""
from __future__ import annotations

from mes_app.config import Settings, normalize_db_url


def test_shared_database_url_fallback(monkeypatch):
    for k in ("MES_DATABASE_URL", "MES_AIVIS_DB_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@pooler.supabase.com:5432/postgres")
    s = Settings()
    assert s.database_url == "postgresql+psycopg://u:p@pooler.supabase.com:5432/postgres"
    assert s.aivis_db_url == s.database_url, "MES 표와 연계 표를 같은 DB 에서"
    assert s.table_intake_enabled


def test_explicit_mes_urls_win(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared/db")
    monkeypatch.setenv("MES_DATABASE_URL", "sqlite:///./m.db")
    monkeypatch.setenv("MES_AIVIS_DB_URL", "sqlite:///./a.db")
    s = Settings()
    assert (s.database_url, s.aivis_db_url) == ("sqlite:///./m.db", "sqlite:///./a.db")


def test_no_urls_means_local_sqlite_and_rest_only(monkeypatch):
    for k in ("MES_DATABASE_URL", "MES_AIVIS_DB_URL", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.database_url.startswith("sqlite") and not s.table_intake_enabled


def test_normalize():
    assert normalize_db_url("postgres://x/y") == "postgresql+psycopg://x/y"
    assert normalize_db_url("") == ""
