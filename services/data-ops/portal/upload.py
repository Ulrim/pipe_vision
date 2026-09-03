"""데이터포털 업로드 클라이언트 (B안: API 직접 연계) — 업로드 매뉴얼 §4·§5·§6.

- `POST {API_BASE}/dataset-uploads`, 헤더 `X-Dataset-Code`(업로드 유형별 코드),
  `X-Upload-Run`(회차 식별), multipart/form-data `files` 반복(filename=상대경로).
- 응답 201: {"data": {"datasetId", "versionId", "version", "status",
  "acceptedCount", "rejected": [{"fileName", "reason"}]}}
- 규칙(§5): 파일당 500 MiB, 요청당 5 GiB, 상대경로('/' 구분), 빈 파일 제외,
  같은 경로 재전송 = 최신 내용으로 갱신(중복 등록 없음 → 재시도 안전).
- 오류(§6): 400 files 없음 / 401 코드 오류 / 503 스토리지 / 500.

전남TP 제공 `upload.sh`(A안) 와 같은 규칙(정렬·배치 300·빈 파일/특수문자 파일명 제외)
로 동작해 두 방식이 같은 데이터셋에 누적되도록 한다. 업로드 코드는 비밀값이며
로그/예외 메시지에 싣지 않는다.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from portal.layout import ALLOWED_EXTENSIONS

UPLOAD_PATH = "/dataset-uploads"
MAX_FILE_BYTES = 500 * 1024 * 1024          # 파일당 500 MiB
MAX_REQUEST_BYTES = 5 * 1024 * 1024 * 1024  # 요청당 5 GiB
DEFAULT_BATCH_FILES = 300                   # upload.sh JNTP_BATCH_SIZE 기본과 동일
DEFAULT_TIMEOUT_S = 600.0                   # upload.sh JNTP_MAX_TIME 기본과 동일
DEFAULT_RETRY = 3
DEFAULT_RETRY_DELAY_S = 30.0
_UNSAFE_CHARS = ('"', "\\", ";")            # upload.sh unsafe_name 과 동일


@dataclass
class PlannedFile:
    rel: str            # 포털 등록 경로(루트 기준 상대, '/' 구분)
    path: Path          # 로컬 절대경로
    size: int


@dataclass
class UploadResult:
    dataset_dir: str
    run_id: str
    total_files: int = 0
    total_bytes: int = 0
    batches: int = 0
    uploaded_files: int = 0
    accepted: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    ok: bool = False              # 모든 묶음이 HTTP 201 로 전송됨(거절/제외는 warning 으로 별도 보고)
    error: str | None = None
    warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_dir": self.dataset_dir,
            "run_id": self.run_id,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "batches": self.batches,
            "uploaded_files": self.uploaded_files,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "version": self.versions[-1] if self.versions else None,
            "ok": self.ok,
            "error": self.error,
            "warning": self.warning,
        }


class PortalTransport(Protocol):
    """전송 계층. (status_code, body) 반환. 연결 오류는 예외."""

    def post(self, url: str, headers: dict[str, str], files: list[PlannedFile]) -> tuple[int, Any]: ...


class HttpxPortalTransport:
    """httpx 기반 실제 전송(HTTP/1.1, multipart 스트리밍)."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s

    def post(self, url: str, headers: dict[str, str], files: list[PlannedFile]) -> tuple[int, Any]:
        import httpx  # 지연 import: 테스트/오프라인에서 필수 의존 아님

        with ExitStack() as stack:
            parts = []
            for f in files:
                fh = stack.enter_context(open(f.path, "rb"))
                ctype = mimetypes.guess_type(f.rel)[0] or "application/octet-stream"
                parts.append(("files", (f.rel, fh, ctype)))
            with httpx.Client(http1=True, http2=False, timeout=self.timeout_s) as client:
                resp = client.post(url, headers=headers, files=parts)
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


class FakePortalTransport:
    """테스트/드라이런용 가짜 전송. 호출 기록 + 설정된 응답 반환."""

    def __init__(
        self,
        *,
        status: int = 201,
        fail_statuses: list[int] | None = None,
        reject: Callable[[str], str | None] | None = None,
        raise_times: int = 0,
        version: str = "1.0",
    ) -> None:
        self.status = status
        self.fail_statuses = list(fail_statuses or [])
        self.reject = reject
        self.raise_times = raise_times
        self.version = version
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], files: list[PlannedFile]) -> tuple[int, Any]:
        self.calls.append({"url": url, "headers": dict(headers), "files": [f.rel for f in files]})
        if self.raise_times > 0:
            self.raise_times -= 1
            raise ConnectionError("simulated network failure")
        if self.fail_statuses:
            st = self.fail_statuses.pop(0)
            return st, {"message": "simulated", "code": "Error"}
        if self.status != 201:
            return self.status, {"message": "simulated", "code": "Error"}
        rejected = []
        for f in files:
            reason = self.reject(f.rel) if self.reject else None
            if reason:
                rejected.append({"fileName": f.rel, "reason": reason})
        return 201, {"data": {
            "datasetId": "DS-fake", "versionId": "DSV-fake", "version": self.version,
            "status": "approved", "acceptedCount": len(files) - len(rejected),
            "rejected": rejected,
        }}


def load_conf(path: str | os.PathLike[str]) -> dict[str, str]:
    """전남TP 설정 파일(KEY=VALUE, `#` 주석) 파싱. 값의 따옴표는 제거."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if k.startswith("export "):
            k = k[len("export "):].strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k] = v
    return out


def credentials_from_conf(path: str | os.PathLike[str]) -> tuple[str, str]:
    """(api_base, upload_code). 환경변수 JNTP_API_BASE/JNTP_UPLOAD_CODE 가 있으면 우선(upload.sh 와 동일)."""
    conf = load_conf(path)
    api_base = os.getenv("JNTP_API_BASE") or conf.get("JNTP_API_BASE") or ""
    code = os.getenv("JNTP_UPLOAD_CODE") or conf.get("JNTP_UPLOAD_CODE") or ""
    if not api_base or not code:
        raise ValueError(f"설정 파일에 JNTP_API_BASE/JNTP_UPLOAD_CODE 가 없습니다: {path}")
    return api_base.rstrip("/"), code


def _is_unsafe(rel: str) -> bool:
    return any(c in rel for c in _UNSAFE_CHARS)


def plan_files(root: str | os.PathLike[str]) -> tuple[list[PlannedFile], list[dict[str, str]]]:
    """루트 하위 파일을 정렬해 계획한다(upload.sh 와 같은 제외 규칙 + 포털 §5 사전 검사)."""
    root_p = Path(root).resolve()
    planned: list[PlannedFile] = []
    skipped: list[dict[str, str]] = []
    if not root_p.is_dir():
        return planned, [{"path": str(root_p), "reason": "폴더 없음"}]
    for p in sorted(root_p.rglob("*"), key=lambda x: x.as_posix().encode("utf-8")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root_p).parts
        if any(part.startswith(".") for part in rel_parts) or "__MACOSX" in rel_parts:
            continue
        rel = "/".join(rel_parts)
        size = p.stat().st_size
        if size == 0:
            skipped.append({"path": rel, "reason": "빈 파일"})
            continue
        if _is_unsafe(rel):
            skipped.append({"path": rel, "reason": "파일명에 허용되지 않는 문자(\" \\ ;)"})
            continue
        if size > MAX_FILE_BYTES:
            skipped.append({"path": rel, "reason": "파일당 500 MiB 초과"})
            continue
        ext = p.suffix.lower().lstrip(".")
        if ext not in ALLOWED_EXTENSIONS:
            skipped.append({"path": rel, "reason": f"미지원 확장자 .{ext}"})
            continue
        planned.append(PlannedFile(rel=rel, path=p, size=size))
    return planned, skipped


def make_batches(files: list[PlannedFile], batch_files: int, max_bytes: int) -> list[list[PlannedFile]]:
    """개수(batch_files)와 요청 총량(max_bytes) 두 상한을 모두 지키는 배치 분할."""
    batches: list[list[PlannedFile]] = []
    cur: list[PlannedFile] = []
    cur_bytes = 0
    for f in files:
        if cur and (len(cur) >= batch_files or cur_bytes + f.size > max_bytes):
            batches.append(cur)
            cur, cur_bytes = [], 0
        cur.append(f)
        cur_bytes += f.size
    if cur:
        batches.append(cur)
    return batches


def make_upload_run_id(files: list[PlannedFile], now: datetime | None = None) -> str:
    """upload.sh 와 같은 형식: YYYYmmddTHHMMSS-<파일목록 sha256 앞 8자>."""
    now = now or datetime.now(timezone.utc)
    digest = hashlib.sha256("\n".join(f.rel for f in files).encode("utf-8") + b"\n").hexdigest()[:8]
    return f"{now:%Y%m%dT%H%M%S}-{digest}"


class PortalUploader:
    """폴더 단위 업로드(배치·재시도·거절 집계)."""

    def __init__(
        self,
        api_base: str,
        code: str,
        transport: PortalTransport | None = None,
        *,
        batch_files: int = DEFAULT_BATCH_FILES,
        max_request_bytes: int = MAX_REQUEST_BYTES,
        retry: int = DEFAULT_RETRY,
        retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_base or not code:
            raise ValueError("api_base 와 업로드 코드가 필요합니다")
        self.url = api_base.rstrip("/") + UPLOAD_PATH
        self._code = code
        self.transport = transport or HttpxPortalTransport()
        self.batch_files = max(1, batch_files)
        self.max_request_bytes = max_request_bytes
        self.retry = max(0, retry)
        self.retry_delay_s = retry_delay_s
        self._sleep = sleep

    def _headers(self, run_id: str) -> dict[str, str]:
        return {"X-Dataset-Code": self._code, "X-Upload-Run": run_id}

    def _post_with_retry(self, headers: dict[str, str], batch: list[PlannedFile]) -> tuple[int, Any]:
        attempt = 0
        while True:
            try:
                status, body = self.transport.post(self.url, headers, batch)
            except Exception as exc:  # noqa: BLE001 — 연결/타임아웃 계열은 재시도
                status, body = 0, {"message": f"transport error: {exc.__class__.__name__}"}
            if status == 201:
                return status, body
            retriable = status == 0 or status >= 500
            if not retriable or attempt >= self.retry:
                return status, body
            attempt += 1
            self._sleep(self.retry_delay_s)

    def upload_dir(self, root: str | os.PathLike[str], *, run_id: str | None = None) -> UploadResult:
        files, skipped = plan_files(root)
        result = UploadResult(dataset_dir=str(root), run_id=run_id or "", skipped=skipped)
        result.total_files = len(files)
        result.total_bytes = sum(f.size for f in files)
        if not files:
            result.error = "올릴 파일이 없습니다"
            return result
        result.run_id = run_id or make_upload_run_id(files)
        headers = self._headers(result.run_id)
        batches = make_batches(files, self.batch_files, self.max_request_bytes)
        result.batches = len(batches)
        for i, batch in enumerate(batches, 1):
            status, body = self._post_with_retry(headers, batch)
            if status != 201:
                msg = body.get("message") if isinstance(body, dict) else str(body)[:200]
                result.error = f"{i}/{len(batches)} 묶음 전송 실패 (HTTP {status}): {msg}"
                return result
            data = body.get("data", {}) if isinstance(body, dict) else {}
            result.uploaded_files += len(batch)
            result.accepted += int(data.get("acceptedCount") or 0)
            for r in data.get("rejected") or []:
                if isinstance(r, dict):
                    result.rejected.append({"fileName": str(r.get("fileName", "")), "reason": str(r.get("reason", ""))})
            if data.get("version"):
                result.versions.append(str(data["version"]))
        result.ok = True
        excluded = len(result.rejected) + len(result.skipped)
        if excluded:
            # 서버 거절/사전 제외는 재전송해도 같으므로 실패로 보지 않고 경고로 보고한다.
            result.warning = f"완료 — 등록 {result.accepted}건, 제외 {excluded}건"
        return result
