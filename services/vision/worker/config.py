"""검사 워커 환경설정 (CLAUDE.md §4 런타임 토폴로지, §6.1 HAL).

모든 운영 파라미터는 환경변수에서 읽는다(하드코딩 금지 원칙). 임계값/보정계수는
워커가 직접 보유하지 않고 backend item_master(GET /master/items)에서 가져온다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None:
        return default
    val = val.strip()
    return val if val else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass
class WorkerConfig:
    """워커 런타임 설정 스냅샷(환경변수 1회 로드)."""

    camera_mode: str = "sim"
    dataset_dir: str | None = None
    api_url: str = "http://api:8000"
    service_token: str | None = None
    item_code: str = "HP12"
    cam_id: str = "CAM1"
    lot: str = ""
    shift: str | None = None
    operator: str | None = None
    interval_ms: int = 1500
    # API readiness 폴링 한계(견고성: 무한 대기 금지).
    api_wait_timeout_s: int = 120
    # item_master 조회 재시도 한계.
    item_wait_timeout_s: int = 120
    # POST 타임아웃.
    http_timeout_s: float = 5.0
    # GET /master 인증 폴백용 시드 계정.
    seed_admin_user: str = "admin"
    seed_admin_password: str = "admin1234"
    # 진행 로그 주기(루프 수).
    log_every: int = 10
    ready_file: str = "/tmp/vision_ready"
    # 최대 루프 수(0=무한). 테스트/데모 스모크에서 유한 종료에 사용.
    max_iterations: int = 0
    # 검사 이미지 저장 루트(§6.4). 하위 raw/ result/ review/ 자동 생성.
    # 로컬 파일시스템만(MinIO/S3 업로드는 backend 책임).
    images_dir: str = "/data/images"

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        lot = _env("AIVIS_LOT") or f"LOT{date.today().strftime('%Y%m%d')}"
        return cls(
            camera_mode=(_env("AIVIS_CAMERA", "sim") or "sim").lower(),
            dataset_dir=_env("AIVIS_DATASET_DIR"),
            api_url=(_env("AIVIS_API_URL", "http://api:8000") or "").rstrip("/"),
            service_token=_env("AIVIS_SERVICE_TOKEN"),
            item_code=_env("AIVIS_ITEM_CODE", "HP12") or "HP12",
            cam_id=_env("AIVIS_CAM_ID", "CAM1") or "CAM1",
            lot=lot,
            shift=_env("AIVIS_SHIFT"),
            operator=_env("AIVIS_OPERATOR"),
            interval_ms=_env_int("AIVIS_WORKER_INTERVAL_MS", 1500),
            api_wait_timeout_s=_env_int("AIVIS_API_WAIT_TIMEOUT_S", 120),
            item_wait_timeout_s=_env_int("AIVIS_ITEM_WAIT_TIMEOUT_S", 120),
            http_timeout_s=float(_env_int("AIVIS_HTTP_TIMEOUT_MS", 5000)) / 1000.0,
            seed_admin_user=_env("AIVIS_SEED_ADMIN_USER", "admin") or "admin",
            seed_admin_password=_env("AIVIS_SEED_ADMIN_PASSWORD", "admin1234")
            or "admin1234",
            log_every=_env_int("AIVIS_WORKER_LOG_EVERY", 10),
            ready_file=_env("AIVIS_READY_FILE", "/tmp/vision_ready") or "/tmp/vision_ready",
            max_iterations=_env_int("AIVIS_WORKER_MAX_ITER", 0),
            images_dir=_env("AIVIS_IMAGES_DIR", "/data/images") or "/data/images",
        )

    @property
    def interval_s(self) -> float:
        return max(0.0, self.interval_ms / 1000.0)
