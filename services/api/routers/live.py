"""라이브 뷰·1장 촬영 API (2026-10-20, core/live.py 참조).

화면(작업자 이상)이 켜고·끄고·1장 촬영을 요청하고, 워커(서비스 토큰)가 프레임을 올린다.
영상은 메모리에만 둔다 — 검사 기록이 아니다.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from aivis_types import LogCategory, Role
from core import live
from core.logging import write_log
from core.security import CurrentUser, require_internal, require_min_role
from db.base import get_db

router = APIRouter(prefix="/system/live", tags=["live"])


class LiveState(BaseModel):
    cam_id: str
    requested: bool
    streaming: bool
    requested_until: Optional[str] = None
    session_left_s: Optional[float] = None
    frame_at: Optional[str] = None
    age_s: Optional[float] = None
    seq: int = 0
    capture_pending: bool = False
    focus: Optional[float] = None
    focus_peak: Optional[float] = None
    brightness: Optional[float] = None
    saturated_pct: Optional[float] = None
    dark_pct: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    roi: Optional[List[float]] = None
    trigger: Optional[Dict[str, Any]] = None


class LiveFrameIn(BaseModel):
    """워커 → API. 저해상도 JPEG(base64) + 그 장면의 지표."""

    jpeg_b64: str = Field(..., description="JPEG 바이트의 base64")
    width: Optional[int] = None
    height: Optional[int] = None
    focus: Optional[float] = Field(None, description="초점 지수(검사 영역 라플라시안 분산)")
    brightness: Optional[float] = Field(None, description="검사 영역 평균 밝기 0~255")
    saturated_pct: Optional[float] = Field(None, description="포화(≥250) 화소 비율 %")
    dark_pct: Optional[float] = Field(None, description="암부(≤5) 화소 비율 %")
    roi: Optional[List[float]] = Field(None, description="검사 영역 화면 비율 [x0,y0,x1,y1]")
    trigger: Optional[Dict[str, Any]] = Field(None, description="영상 트리거 지표(움직임·변화)")


class LiveCommand(BaseModel):
    live: bool
    capture: bool


@router.post("/{cam_id}/start", response_model=LiveState)
def start_live(cam_id: str, user: CurrentUser = Depends(require_min_role(Role.OPERATOR))):
    """라이브 뷰를 켠다. 워커는 다음 하트비트(대기 중 최대 5초)에서 알아차린다."""
    return live.start(cam_id, user.username)


@router.post("/{cam_id}/stop", response_model=LiveState)
def stop_live(cam_id: str, _user: CurrentUser = Depends(require_min_role(Role.OPERATOR))):
    return live.stop(cam_id)


@router.post("/{cam_id}/capture", status_code=status.HTTP_202_ACCEPTED)
def capture_once(
    cam_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_min_role(Role.OPERATOR)),
):
    """지금 1장 찍어 평소처럼 검사·저장하게 한다(설치 확인·학습 데이터 수집용)."""
    live.request_capture(cam_id, user.username)
    write_log(db, category=LogCategory.USER,
              message=f"live.capture [station={cam_id}] by={user.username}",
              payload={"cam_id": cam_id})
    return {"status": "requested", "cam_id": cam_id}


@router.put("/{cam_id}/frame", response_model=LiveCommand)
def put_frame(cam_id: str, body: LiveFrameIn, _internal: None = Depends(require_internal)):
    """워커가 올리는 최신 프레임. 응답으로 계속 보낼지·1장 찍을지 알려 준다."""
    try:
        jpeg = base64.b64decode(body.jpeg_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="jpeg_b64 가 base64 가 아니다") from exc
    if not jpeg.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=422, detail="JPEG 이 아니다")
    meta = body.model_dump(exclude={"jpeg_b64"})
    try:
        want_live, want_capture = live.put_frame(cam_id, jpeg, meta)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return LiveCommand(live=want_live, capture=want_capture)


@router.get("/{cam_id}", response_model=LiveState)
def get_live(cam_id: str, _user: CurrentUser = Depends(require_min_role(Role.OPERATOR))):
    """현재 상태·지표. 화면이 읽는 동안은 '보고 있다' 로 기한이 연장된다."""
    live.touch(cam_id)
    return live.state(cam_id)


@router.get("/{cam_id}/frame.jpg")
def get_frame(cam_id: str, _user: CurrentUser = Depends(require_min_role(Role.OPERATOR))):
    got = live.frame(cam_id)
    if got is None:
        raise HTTPException(status_code=404, detail="아직 받은 영상이 없다")
    jpeg, seq = got
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Frame-Seq": str(seq)})
