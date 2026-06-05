"""QA 하니스 부트스트랩 (tests/* 소유, P6 통합·검증).

CLAUDE.md §1.2 인수 합격기준 4지표를 자립 실행으로 검증하기 위한 공통 설정.
라이브 카메라/Postgres 없이 동작하도록:
  - services/ 를 sys.path 에 올려 `import vision.*` 가능 (services/vision/conftest.py 규칙).
  - services/api 를 sys.path 에 올려 `import main`, `import core.*` 가능.
  - DB 는 임시 sqlite 파일, MES 모드는 table, 시드 비활성 (api 테스트 conftest 규칙과 정합).

이 conftest 는 import 시점(수집 전)에 환경변수를 세팅해야 backend 설정 캐시가
sqlite 로 굳는다. services/* 코드는 읽기 전용으로만 사용한다(소유: tests/* 한정).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 1) 경로: services/ (vision) + services/api (backend) 를 import 가능하게.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[1]
_SERVICES = _ROOT / "services"
_API = _SERVICES / "api"
# data-ops 정답셋 빌더(labeling.groundtruth) import 용.
_DATA_OPS = _SERVICES / "data-ops"

# 루트(=`tests` 패키지 부모)도 올려 `import tests.harness.*` 가능하게.
for _p in (str(_ROOT), str(_SERVICES), str(_API), str(_DATA_OPS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# 2) 백엔드 테스트 환경: 임시 sqlite + table MES + 시드 OFF (라이브 인프라 불필요).
#    backend core.config.Settings 는 lru_cache 이므로 import 전에 세팅한다.
# ---------------------------------------------------------------------------
_TMPDIR = tempfile.mkdtemp(prefix="aivis_qa_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(_TMPDIR, 'qa.db')}")
os.environ.setdefault("AIVIS_LOCAL_QUEUE_DIR", os.path.join(_TMPDIR, "queue"))
os.environ.setdefault("JWT_SECRET", "qa-secret")
os.environ.setdefault("AIVIS_SEED_ON_STARTUP", "false")
os.environ.setdefault("MES_MODE", "table")
# 검사워커 내부 토큰: 설정해 POST /inspection 을 토큰 인증 경로로 검증.
os.environ.setdefault("AIVIS_SERVICE_TOKEN", "qa-internal-token")
# 시뮬레이터 카메라 강제 (실카메라 미연결).
os.environ.setdefault("AIVIS_CAMERA", "sim")


def pytest_report_header(config):  # pragma: no cover - 진단 출력
    return (
        "AIVIS QA harness | "
        f"db={os.environ['DATABASE_URL']} mes={os.environ['MES_MODE']} "
        f"camera={os.environ['AIVIS_CAMERA']}"
    )
