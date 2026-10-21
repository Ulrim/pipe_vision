"""사진 NAS 보관 — 현장 Synology 에 모든 사진을 함께 남긴다 (2026-10-21).

클라우드(Supabase)에는 용량 때문에 결과 사진과 NG 원본만 올리고 보관기한이 지나면 지운다
(AIVIS_REMOTE_RAW=ng, AIVIS_REMOTE_RETENTION_DAYS). 그러면 **정상품 원본**이 어디에도 남지 않는다
— 표면 모델 재학습에 가장 많이 필요한 사진이다. 그래서 파이가 같은 공장 내부망의 NAS 에
**원본·결과 사진 전부**를 영구 보관용으로 한 벌 더 쓴다. 인터넷을 쓰지 않는다.

    AIVIS_ARCHIVE_DIR=/mnt/nas/aivis     # NAS 공유 폴더를 마운트한 경로(비우면 끔)

폴더: <AIVIS_ARCHIVE_DIR>/YYYY/MM/DD/{raw,result,review}/<운영 파일명>  (날짜는 KST)
파일명은 운영 규칙 그대로 `{LOT}_{품목}_{CamID}_{시각}_{판정}.jpg` 라 NAS 파일 검색으로 LOT 을 찾는다.

안전장치
- **NAS 표식 파일** `<AIVIS_ARCHIVE_DIR>/.aivis-nas` 이 보여야만 쓴다. NAS 가 안 붙은 채로
  마운트 지점에 쓰면 사진이 파이 SD 카드에 조용히 쌓여 카드를 채운다 — 표식이 없으면 'NAS 끊김'
  으로 보고 파이 스풀에 모았다가 NAS 가 돌아오면 오래된 것부터 옮긴다.
- 인코딩·쓰기는 **뒤에서**(스레드 1개) 한다. 검사 처리시간(취득~저장, 300ms/ea)에 들어가지 않는다.
- 파일은 임시 이름으로 쓴 뒤 이름을 바꾼다 — NAS 에 반쯤 쓰인 사진이 남지 않는다.
- 스풀 상한(AIVIS_ARCHIVE_SPOOL_MAX_GB, 기본 5)을 넘으면 새 사진은 버리고 센다(dropped) — SD 카드를
  지키는 쪽을 택한다. 하트비트로 화면에 '⚠ NAS 끊김(대기 n장)' 이 뜬다.
- 무엇이 실패해도 검사·저장·전송은 막지 않는다.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import cv2
import numpy as np

log = logging.getLogger("aivis.vision.archive")

MARKER = ".aivis-nas"
KST = timezone(timedelta(hours=9))
_KINDS = ("raw", "result", "review")
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 95]  # 로컬 저장과 같은 품질(원본 보존)
_STOP = object()


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def archive_relpath(key: str, ts: datetime) -> str:
    """'raw/<name>.jpg' + 검사 시각 → 'YYYY/MM/DD/raw/<name>.jpg'(KST 날짜)."""
    kind, _, name = key.partition("/")
    if kind not in _KINDS or not name or "/" in name or name.startswith("."):
        raise ValueError(f"보관 키 형식이 아니다: {key!r}")
    t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    d = t.astimezone(KST)
    return f"{d:%Y}/{d:%m}/{d:%d}/{kind}/{name}"


@dataclass
class _Task:
    key: str
    ts: datetime
    image: Optional[np.ndarray] = None
    jpeg: Optional[bytes] = None


class NasArchive:
    """원본·결과 사진을 NAS 에 한 벌 더 쓴다(뒤에서). submit 은 기다리지 않는다."""

    def __init__(
        self,
        root: Union[str, Path],
        spool_dir: Union[str, Path],
        *,
        spool_max_bytes: int = 5 * 1024**3,
        retry_s: float = 30.0,
        queue_size: int = 16,
        clock: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ) -> None:
        self.root = Path(root)
        self.spool = Path(spool_dir)
        self.spool_max_bytes = int(spool_max_bytes)
        self.retry_s = float(retry_s)
        self._clock = clock
        self._q: "queue.Queue[Any]" = queue.Queue(maxsize=max(1, int(queue_size)))
        self._lock = threading.Lock()
        self.written = 0
        self.dropped = 0
        self.last_error: Optional[str] = None
        self._ok: Optional[bool] = None
        self._last_retry = -1e18
        self._spool_bytes, self._pending = self._scan_spool()
        self._thread: Optional[threading.Thread] = None
        if start_thread:
            self._thread = threading.Thread(target=self._loop, name="aivis-nas", daemon=True)
            self._thread.start()

    # --- 만들기 ---------------------------------------------------------------
    @classmethod
    def from_env(cls, *, spool_dir: Union[str, Path]) -> Optional["NasArchive"]:
        root = (os.environ.get("AIVIS_ARCHIVE_DIR") or "").strip()
        if not root:
            return None
        arch = cls(
            root,
            Path(spool_dir) / "archive",
            spool_max_bytes=int(_envf("AIVIS_ARCHIVE_SPOOL_MAX_GB", 5.0) * 1024**3),
            retry_s=_envf("AIVIS_ARCHIVE_RETRY_S", 30.0),
        )
        log.info("NAS 보관 사용: %s", root)
        return arch

    # --- 넣기 -----------------------------------------------------------------
    def submit(self, key: str, ts: datetime, *, image: Optional[np.ndarray] = None,
               jpeg: Optional[bytes] = None) -> None:
        """사진 하나를 맡긴다. 큐가 차면(NAS 가 느리면) 그 자리에서 스풀에 쓴다. 예외 없음."""
        try:
            task = _Task(key=key, ts=ts, image=image, jpeg=jpeg)
            archive_relpath(key, ts)  # 키가 틀리면 지금 알린다
            try:
                self._q.put_nowait(task)
            except queue.Full:
                self._to_spool(archive_relpath(key, ts), self._encode(task))
        except Exception as exc:  # noqa: BLE001 - 보관은 검사를 막지 않는다
            self._fail(f"맡기기 실패: {type(exc).__name__}: {exc}")

    # --- 상태 -----------------------------------------------------------------
    def nas_available(self) -> bool:
        """표식 파일이 보이나. NAS 가 꺼져 있으면 자동 마운트가 몇 초 걸릴 수 있다 — 뒤 스레드에서만 부른다."""
        try:
            return (self.root / MARKER).is_file()
        except OSError:
            return False

    def pending(self) -> int:
        return self._pending

    def status(self) -> Dict[str, Any]:
        """하트비트용 — 화면의 'NAS' 표시. 마지막으로 확인한 상태를 돌려준다(기다리지 않는다)."""
        if self._ok is None and self._thread is None:
            self._set_ok(self.nas_available(), None)  # 스레드 없는 시험용
        ok = bool(self._ok)
        err = None if ok else (self.last_error or "NAS 연결 확인 중")
        return {"nas_ok": ok, "nas_pending": self._pending + self._q.qsize(),
                "nas_dropped": self.dropped, "nas_error": err}

    # --- 뒤에서 -----------------------------------------------------------------
    def _loop(self) -> None:
        ok = self.nas_available()
        self._set_ok(ok, None if ok else f"NAS 표식 없음({self.root / MARKER}) — 마운트 확인")
        while True:
            try:
                task = self._q.get(timeout=1.0)
            except queue.Empty:
                task = None
            if task is _STOP:
                break
            if task is not None:
                self._handle(task)
            if self._clock() - self._last_retry >= self.retry_s:
                self._last_retry = self._clock()
                if self.retry_spool() == 0 and self._pending == 0:
                    ok = self.nas_available()  # 사진이 없어도 화면 표시가 실제와 맞게
                    self._set_ok(ok, None if ok else f"NAS 표식 없음({self.root / MARKER}) — 마운트 확인")

    def _handle(self, task: _Task) -> None:
        try:
            rel = archive_relpath(task.key, task.ts)
            data = self._encode(task)
        except Exception as exc:  # noqa: BLE001
            self._fail(f"인코딩 실패 {task.key}: {type(exc).__name__}: {exc}")
            return
        if self._write_nas(rel, data):
            return
        self._to_spool(rel, data)

    @staticmethod
    def _encode(task: _Task) -> bytes:
        if task.jpeg is not None:
            return task.jpeg
        if task.image is None:
            raise ValueError("이미지가 없다")
        ok, buf = cv2.imencode(".jpg", task.image, _JPEG_PARAMS)
        if not ok:
            raise OSError("JPEG 인코딩 실패")
        return buf.tobytes()

    def _write_nas(self, rel: str, data: bytes) -> bool:
        if not self.nas_available():
            self._set_ok(False, f"NAS 표식 없음({self.root / MARKER}) — 마운트 확인")
            return False
        try:
            _atomic_write(self.root / rel, data)
        except OSError as exc:
            self._set_ok(False, f"NAS 쓰기 실패: {exc}")
            return False
        self.written += 1
        self._set_ok(True, None)
        return True

    def _to_spool(self, rel: str, data: bytes) -> None:
        with self._lock:
            if self._spool_bytes + len(data) > self.spool_max_bytes:
                self.dropped += 1
                if self.dropped == 1 or self.dropped % 100 == 0:
                    log.warning("NAS 보관 스풀이 상한(%.1fGB)을 넘어 사진을 버림(누적 %d장) — NAS 연결 확인",
                                self.spool_max_bytes / 1024**3, self.dropped)
                return
            try:
                _atomic_write(self.spool / rel, data)
                self._spool_bytes += len(data)
                self._pending += 1
            except OSError as exc:
                self.dropped += 1
                self._fail(f"스풀 쓰기 실패: {exc}")

    def retry_spool(self, limit: int = 500) -> int:
        """스풀에 모인 사진을 NAS 로 옮긴다(오래된 것부터). 옮긴 장수."""
        if self._pending <= 0 or not self.nas_available():
            return 0
        try:
            files = sorted((p for p in self.spool.rglob("*.jpg") if p.is_file()),
                           key=lambda p: p.stat().st_mtime)[:limit]
        except OSError:
            return 0
        moved = 0
        for p in files:
            rel = p.relative_to(self.spool).as_posix()
            try:
                data = p.read_bytes()
                if not self._write_nas(rel, data):
                    break
                p.unlink()
                with self._lock:
                    self._spool_bytes = max(0, self._spool_bytes - len(data))
                    self._pending = max(0, self._pending - 1)
                moved += 1
            except OSError as exc:
                self._fail(f"스풀 → NAS 실패 {rel}: {exc}")
                break
        if moved:
            log.info("NAS 보관: 스풀 %d장 옮김(남은 %d장)", moved, self.pending())
        return moved

    def close(self, timeout: float = 5.0) -> None:
        """맡긴 사진을 마저 처리한다(시간 안에서)."""
        if self._thread is None:
            while True:
                try:
                    task = self._q.get_nowait()
                except queue.Empty:
                    break
                if task is not _STOP:
                    self._handle(task)
            return
        try:
            self._q.put(_STOP, timeout=timeout)
        except queue.Full:
            return
        self._thread.join(timeout=timeout)

    # --- 기록 -----------------------------------------------------------------
    def _set_ok(self, ok: bool, err: Optional[str]) -> None:
        if ok != self._ok:
            if ok:
                log.info("NAS 보관 연결됨: %s", self.root)
            else:
                log.warning("NAS 보관 끊김 — 파이 스풀에 모은다: %s", err)
        self._ok = ok
        self.last_error = err

    def _fail(self, msg: str) -> None:
        if msg != self.last_error:
            log.warning("NAS 보관: %s", msg)
        self.last_error = msg

    def _scan_spool(self) -> tuple[int, int]:
        """시작할 때 한 번 — 지난번에 못 옮긴 스풀(바이트, 장수)."""
        try:
            files = [p for p in self.spool.rglob("*.jpg") if p.is_file()]
            return sum(p.stat().st_size for p in files), len(files)
        except OSError:
            return 0, 0


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def archive_put(archive: Optional[NasArchive], key: str, ts: datetime, *,
                image: Optional[np.ndarray] = None, jpeg: Optional[bytes] = None) -> None:
    """저장 헬퍼용 — 보관이 꺼져 있으면 아무것도 안 한다. 예외 없음."""
    if archive is None:
        return
    try:
        archive.submit(key, ts, image=image, jpeg=jpeg)
    except Exception:  # noqa: BLE001
        pass
