"""검사 이미지 스토리지 백엔드 (M7 일부, §4 ⑤ — 클라우드 배포 보완).

로컬 디스크에만 저장하면 클라우드(Render) 분리 배포에서 api 컨테이너가
워커가 쓴 이미지를 못 읽는다. 그래서 동일한 상대경로(키)를 유지한 채
업로드 대상을 **로컬 디스크 / Supabase Storage** 로 전환할 수 있게 한다.

계약(backend/devops 합의 — 절대 변경 금지):
- AIVIS_STORAGE_BACKEND = local | supabase (기본 local)
- SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
  SUPABASE_STORAGE_BUCKET(기본 "inspection-images")
- 오브젝트 키 = DB 에 싣는 상대경로 그대로:
  raw/<name>.jpg, result/<name>.jpg, review/<name>.jpg (§6.4 파일명 유지)
- 업로드: POST {URL}/storage/v1/object/{bucket}/{key}
  headers: Authorization: Bearer {KEY}, apikey: {KEY},
           Content-Type: image/jpeg, x-upsert: true
  body = jpeg bytes

두 백엔드 모두 **반환하는 상대경로(키)는 동일**하다. 업로드 실패는 호출자가
graceful 처리할 수 있도록 예외를 던진다(검사결과 적재는 절대 막지 않는다).
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("aivis.vision.storage")

LOCAL = "local"
SUPABASE = "supabase"
DEFAULT_BACKEND = LOCAL
DEFAULT_BUCKET = "inspection-images"

# Supabase 업로드 타임아웃(초). 워커 동기 루프 — 무한 대기 금지.
_HTTP_TIMEOUT_S = 10.0

# JPEG 품질 고정 → 결정적 바이트(save.py 와 동일 값 유지).
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 95]


def encode_jpeg(image: np.ndarray) -> bytes:
    """BGR 이미지를 JPEG 바이트로 인코딩(결정적). 실패 시 OSError."""
    ok, buf = cv2.imencode(".jpg", image, _JPEG_PARAMS)
    if not ok:
        raise OSError("cv2.imencode 실패(.jpg)")
    return buf.tobytes()


class StorageBackend(ABC):
    """이미지 저장 백엔드. put(key, jpeg) → 키 반환(상대경로 그대로)."""

    @abstractmethod
    def put(self, key: str, jpeg: bytes) -> str:
        """key(예: raw/<name>.jpg)로 jpeg 바이트를 저장하고 key 를 반환한다."""
        raise NotImplementedError


class LocalStorage(StorageBackend):
    """images_dir 하위에 키 경로 그대로 파일로 쓴다(기존 디스크 동작 유지)."""

    def __init__(self, images_dir: str) -> None:
        self.base = Path(images_dir)

    def put(self, key: str, jpeg: bytes) -> str:
        dst = self.base / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(jpeg)
        return key


class SupabaseStorage(StorageBackend):
    """Supabase Storage REST 업로드(x-upsert). 키 = 상대경로 그대로.

    httpx 동기 클라이언트(타임아웃 설정)를 사용한다(워커는 동기 루프).
    호출자가 client 를 주입할 수 있어 테스트에서 MockTransport 로 검증 가능.
    """

    def __init__(
        self,
        url: str,
        service_role_key: str,
        bucket: str = DEFAULT_BUCKET,
        *,
        client=None,
        timeout_s: float = _HTTP_TIMEOUT_S,
    ) -> None:
        import httpx  # 지연 import — local 모드는 httpx 불필요.

        self.url = url.rstrip("/")
        self.key = service_role_key
        self.bucket = bucket
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_s)

    def _object_url(self, key: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{key}"

    def put(self, key: str, jpeg: bytes) -> str:
        resp = self._client.post(
            self._object_url(key),
            content=jpeg,
            headers={
                "Authorization": f"Bearer {self.key}",
                "apikey": self.key,
                "Content-Type": "image/jpeg",
                "x-upsert": "true",
            },
        )
        # 2xx 가 아니면 graceful 처리 대상으로 승격.
        if resp.status_code >= 400:
            raise OSError(
                f"Supabase 업로드 실패 {resp.status_code}: {key} {resp.text[:200]}"
            )
        return key

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


@dataclass(frozen=True)
class StorageSettings:
    """스토리지 백엔드 설정 스냅샷(환경변수 1회 로드)."""

    backend: str = DEFAULT_BACKEND
    images_dir: str = "/data/images"
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    supabase_bucket: str = DEFAULT_BUCKET

    @property
    def is_supabase(self) -> bool:
        return self.backend == SUPABASE

    @classmethod
    def from_env(cls, *, images_dir: Optional[str] = None) -> "StorageSettings":
        backend = (os.environ.get("AIVIS_STORAGE_BACKEND") or DEFAULT_BACKEND).strip().lower()
        if backend not in (LOCAL, SUPABASE):
            log.warning(
                "알 수 없는 AIVIS_STORAGE_BACKEND=%r → local 로 폴백", backend
            )
            backend = LOCAL
        url = (os.environ.get("SUPABASE_URL") or "").strip() or None
        key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip() or None
        bucket = (
            os.environ.get("SUPABASE_STORAGE_BUCKET") or DEFAULT_BUCKET
        ).strip() or DEFAULT_BUCKET
        idir = images_dir or os.environ.get("AIVIS_IMAGES_DIR") or "/data/images"
        return cls(
            backend=backend,
            images_dir=idir,
            supabase_url=url,
            supabase_key=key,
            supabase_bucket=bucket,
        )


def build_backend(settings: StorageSettings, *, client=None) -> StorageBackend:
    """설정으로 백엔드를 만든다. supabase 설정 누락이면 경고 후 local 폴백."""
    if settings.is_supabase:
        if not settings.supabase_url or not settings.supabase_key:
            log.warning(
                "AIVIS_STORAGE_BACKEND=supabase 이지만 SUPABASE_URL/"
                "SUPABASE_SERVICE_ROLE_KEY 미설정 → local 디스크로 폴백"
            )
            return LocalStorage(settings.images_dir)
        return SupabaseStorage(
            settings.supabase_url,
            settings.supabase_key,
            settings.supabase_bucket,
            client=client,
        )
    return LocalStorage(settings.images_dir)
