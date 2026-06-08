"""런타임 설정 (환경변수 기반). .env.example 의 키와 정합 (CLAUDE.md §3)."""
from __future__ import annotations

import os
from functools import lru_cache


def _bool(env: str, default: bool) -> bool:
    val = os.getenv(env)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """앱 전역 설정. 환경변수 미설정 시 개발/테스트용 기본값(sqlite)."""

    def __init__(self) -> None:
        # DB: postgres 우선, 미설정 시 로컬 sqlite (개발/테스트).
        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite:///./aivis_dev.db"
        )

        # JWT / RBAC
        self.jwt_secret: str = os.getenv("JWT_SECRET", "dev-insecure-change-me")
        self.jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

        # 검사결과 저장 실패 시 로컬 큐 백업 디렉터리 (M7 DoD).
        self.local_queue_dir: str = os.getenv(
            "AIVIS_LOCAL_QUEUE_DIR",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "local_queue"),
        )

        # MES 연계 모드: table(스테이징 테이블) | rest (§7.3).
        self.mes_mode: str = os.getenv("MES_MODE", "table")

        # 초기 admin 시드 계정 (개발/부트스트랩용).
        self.seed_admin_user: str = os.getenv("AIVIS_SEED_ADMIN_USER", "admin")
        self.seed_admin_password: str = os.getenv(
            "AIVIS_SEED_ADMIN_PASSWORD", "admin1234"
        )
        self.seed_on_startup: bool = _bool("AIVIS_SEED_ON_STARTUP", True)

        # 연속 NG 알람 임계 (M6). cam_id 단위 연속 NG 가 이 값 이상이면
        # /ws/live 로 consecutive_ng 알람 브로드캐스트.
        self.consec_ng_threshold: int = max(
            1, int(os.getenv("AIVIS_CONSEC_NG_THRESHOLD", "3"))
        )

        # 검사워커(POST /inspection) 내부 호출용 서비스 토큰(M14).
        # 설정 시 X-Service-Token 헤더(또는 Bearer)로 내부 호출을 인증한다.
        # 미설정(기본)이면 내부 POST /inspection 은 화이트리스트(무인증) 허용.
        self.service_token: str | None = os.getenv("AIVIS_SERVICE_TOKEN") or None

        # CORS 교차 출처 허용 목록(클라우드 데모: 프론트=Vercel, 백엔드=Render 등).
        # ALLOWED_ORIGINS 콤마 구분 목록. 미설정 시 개발 편의로 ["*"] 허용.
        self.allowed_origins: list[str] = self._parse_origins(
            os.getenv("ALLOWED_ORIGINS")
        )

        # 데모 품목 시드(item_master FK 충족). 데모 배포에서 True 로 켠다.
        self.seed_demo_item: bool = _bool("AIVIS_SEED_DEMO_ITEM", False)
        self.demo_item_code: str = os.getenv("AIVIS_DEMO_ITEM_CODE", "HP12")

    @staticmethod
    def _parse_origins(raw: str | None) -> list[str]:
        """ALLOWED_ORIGINS 파싱: 콤마 분리, 공백 트림, 빈값 제거.

        미설정/전체 공백이면 ["*"](모든 출처 허용, credentials 불가).
        """
        if raw is None:
            return ["*"]
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        return origins or ["*"]

    @property
    def cors_allow_credentials(self) -> bool:
        """명시 출처 목록이면 credentials 허용. "*" 이면 불가(스펙 충돌 회피)."""
        return self.allowed_origins != ["*"]

    @property
    def consec_ng_threshold_value(self) -> int:
        return self.consec_ng_threshold

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
