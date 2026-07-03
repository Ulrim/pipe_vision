"""검사 이미지 저장 모듈 (M7 일부: 결과 이미지 생성, §6.4 저장 규칙).

검사 파이프라인의 판정 결과를 받아 원본(raw)과 결과 오버레이(result) 이미지를
로컬 파일시스템에 저장한다. MinIO/S3 업로드는 범위 밖(backend 책임).

계약(backend/devops 합의):
- AIVIS_IMAGES_DIR 기준 하위 raw/ result/ review/ 자동 생성.
- 파일명(§6.4): {LOT}_{Item}_{YYYYMMDDHHmmssSSS}_{verdict}.jpg (ms 3자리).
- 반환 경로는 AIVIS_IMAGES_DIR 기준 상대경로(절대경로 금지 — 서버가 join).
- review_flag=True 면 result 사본을 review/ 에 추가 기록.

모든 함수는 결정적이며 디스크 I/O 실패는 호출자가 잡을 수 있도록 예외를 던지되,
워커 통합부에서 graceful 처리한다(검사결과 적재는 절대 막지 않는다).
"""
from __future__ import annotations

from .save import (
    DEFAULT_IMAGES_DIR,
    ImageSaveResult,
    build_filename,
    render_overlay,
    save_inspection_images,
    save_raw,
    save_result,
)

__all__ = [
    "DEFAULT_IMAGES_DIR",
    "ImageSaveResult",
    "build_filename",
    "render_overlay",
    "save_inspection_images",
    "save_raw",
    "save_result",
]
