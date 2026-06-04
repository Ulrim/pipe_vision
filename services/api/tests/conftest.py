"""pytest 픽스처: sqlite 인메모리 DB로 API 통합 테스트(라이브 postgres 불필요)."""
from __future__ import annotations

import os
import tempfile

import pytest

# 테스트 전용 환경: 임시 sqlite 파일 + 시드 비활성(테스트가 직접 사용자 생성).
_TMPDIR = tempfile.mkdtemp(prefix="aivis_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMPDIR, 'test.db')}"
os.environ["AIVIS_LOCAL_QUEUE_DIR"] = os.path.join(_TMPDIR, "queue")
os.environ["JWT_SECRET"] = "test-secret"
os.environ["AIVIS_SEED_ON_STARTUP"] = "false"
os.environ["MES_MODE"] = "table"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from core.security import hash_password  # noqa: E402
from db.base import SessionLocal, init_db  # noqa: E402
from db.models import AppUser  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    init_db()
    db = SessionLocal()
    try:
        # 역할별 테스트 계정.
        for username, role in [
            ("op1", "operator"),
            ("qa1", "quality"),
            ("admin1", "admin"),
        ]:
            if not db.get(AppUser, username):
                db.add(
                    AppUser(
                        username=username,
                        pw_hash=hash_password("pw12345"),
                        role=role,
                        active=True,
                    )
                )
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _token(client, username: str) -> str:
    r = client.post("/auth/login", json={"username": username, "password": "pw12345"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def auth(client):
    """역할명 -> Authorization 헤더 dict."""
    def _make(username: str) -> dict:
        return {"Authorization": f"Bearer {_token(client, username)}"}
    return _make
