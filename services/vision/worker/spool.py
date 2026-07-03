"""오프라인 스풀(디스크 버퍼) + 자동 재전송 (모드 A: Pi 엣지 워커 → 클라우드).

현장 인터넷 단절 시 POST /inspection 실패(연결 오류/타임아웃/5xx)와 Supabase
이미지 업로드 실패로 검사결과가 유실되지 않도록, 결과 payload(JSON)와 JPEG
바이트를 디스크에 스풀하고 네트워크 복구 후 자동 재전송한다.

서버(POST /inspection)는 자연키(cam_id+inspected_at+item_code) 멱등이므로
재전송 중복은 서버가 걸러준다 — 워커는 안심하고 재시도한다.

디렉터리 레이아웃({AIVIS_SPOOL_DIR} 기준, Pi 운영은 /var/lib/aivis/spool 권장):
    pending/{inspected_at_ms}_{cam_id}.json   # 재전송 대기 payload
    images/{raw|result}/<파일명>.jpg          # 업로드 대기 JPEG(키 경로 그대로)
    dead/…                                    # 4xx 영구 오류 항목(무한루프 방지)
    tmp/…                                     # 원자적 쓰기(tmp→rename) 작업 파일

계약:
  - payload JSON 은 InspectionResult.model_dump(mode="json") 그대로이며,
    업로드 미완 이미지 키 목록만 내부 메타 `_pending_images` 로 추가된다.
    서버 전송 전 반드시 제거한다(shared-types 스키마 불변).
  - 4xx(401/403/422 등)는 영구 오류 → 스풀하지 않는다(재시도 무의미).
  - 스풀 총 크기 > AIVIS_SPOOL_MAX_MB 면 가장 오래된 항목부터 삭제(SD 카드 보호).
  - flush 는 oldest-first, 한 번에 AIVIS_SPOOL_FLUSH_BATCH 개까지만 처리해
    라이브 검사 루프를 굶기지 않는다. 연결 오류 감지 시 즉시 중단.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence

import httpx

log = logging.getLogger("aivis.vision.worker.spool")

# 하위 디렉터리 이름(계약 — README/문서와 일치).
PENDING_DIR = "pending"
IMAGES_DIR = "images"
DEAD_DIR = "dead"
TMP_DIR = "tmp"

# payload 내부 메타 키(서버 전송 전 제거 — shared-types 스키마 불변).
PENDING_IMAGES_KEY = "_pending_images"

# 파일명 토큰 안전화(경로 문자 금지).
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# post_fn 계약: (payload dict) -> (status_code, detail). status 0 = 네트워크 오류.
PostFn = Callable[[dict], tuple[int, str]]
# upload_fn 계약: (key, jpeg bytes) -> None. 실패 시 예외(TransportError=연결 오류).
UploadFn = Callable[[str, bytes], None]


def is_retryable_status(status: int) -> bool:
    """스풀(재시도) 대상인가? 0(연결 오류/타임아웃) 또는 5xx."""
    return status == 0 or status >= 500


def is_permanent_status(status: int) -> bool:
    """영구 오류(4xx — 401/403/422 등)인가? 재시도 무의미 → 스풀 금지/dead."""
    return 400 <= status < 500


def _safe_token(value: str, *, fallback: str = "NA") -> str:
    token = _UNSAFE.sub("_", str(value or "").strip()).strip("._-")
    return token or fallback


def _epoch_ms(value) -> int:
    """inspected_at(datetime|ISO 문자열) → epoch 밀리초."""
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, str) and value:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    return 0


def _oldest_key(path: Path) -> tuple[int, str]:
    """pending 파일 정렬 키: 파일명 ms 접두(숫자) → oldest-first."""
    prefix = path.name.split("_", 1)[0]
    try:
        return (int(prefix), path.name)
    except ValueError:
        return (1 << 62, path.name)


@dataclass
class FlushReport:
    """flush 1회 결과(통계 로그/테스트용)."""

    sent: int = 0        # POST 2xx → 삭제
    held: int = 0        # 보류(이미지 업로드 실패/5xx 등) — 다음 기회
    dead: int = 0        # 4xx → dead/ 이동
    aborted: bool = False  # 연결 오류로 즉시 중단


class SpoolQueue:
    """디스크 스풀 큐. 원자적 저장(tmp→rename) + 용량 상한 + oldest-first flush."""

    def __init__(
        self,
        spool_dir: str | Path,
        *,
        max_mb: int = 512,
        flush_batch: int = 20,
        max_bytes: Optional[int] = None,
    ) -> None:
        self.base = Path(spool_dir)
        self.max_bytes = int(max_bytes) if max_bytes is not None else max_mb * 1024 * 1024
        self.flush_batch = max(1, int(flush_batch))
        # 누적 통계(주기 로그용).
        self.enqueued = 0
        self.sent = 0
        self.dropped = 0
        self.dead = 0

    # --- 경로 헬퍼 ---
    @property
    def pending_path(self) -> Path:
        return self.base / PENDING_DIR

    @property
    def images_path(self) -> Path:
        return self.base / IMAGES_DIR

    @property
    def dead_path(self) -> Path:
        return self.base / DEAD_DIR

    @property
    def tmp_path(self) -> Path:
        return self.base / TMP_DIR

    def stats(self) -> dict:
        """주기 로그에 싣는 스냅샷(적재/재전송 성공/드롭/영구오류/대기 수)."""
        return {
            "enqueued": self.enqueued,
            "sent": self.sent,
            "dropped": self.dropped,
            "dead": self.dead,
            "pending": self.pending_count(),
        }

    def pending_count(self) -> int:
        try:
            return sum(1 for _ in self.pending_path.glob("*.json"))
        except OSError:
            return 0

    def _pending_files(self) -> List[Path]:
        """pending 항목 oldest-first(파일명 ms 접두 기준)."""
        try:
            return sorted(self.pending_path.glob("*.json"), key=_oldest_key)
        except OSError:
            return []

    def _atomic_write(self, dst: Path, data: bytes) -> None:
        """tmp 에 쓴 뒤 os.replace 로 원자적 배치(전원 단절에도 반쪽 파일 금지)."""
        self.tmp_path.mkdir(parents=True, exist_ok=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.tmp_path / f"{dst.name}.tmp{os.getpid()}"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
        os.replace(tmp, dst)

    # --- 적재 ---
    def enqueue(
        self,
        result,
        *,
        pending_images: Optional[Sequence[str]] = None,
    ) -> Optional[Path]:
        """InspectionResult(또는 payload dict)를 pending/ 에 원자적 저장.

        파일명: {inspected_at_ms}_{cam_id}.json (충돌 시 -N 접미).
        pending_images 가 있으면 payload 에 `_pending_images` 메타를 추가한다
        (flush 가 이미지 선업로드 후 제거하고 전송). 실패해도 raise 하지 않는다.
        """
        try:
            if hasattr(result, "model_dump"):
                payload = result.model_dump(mode="json")
                ms = _epoch_ms(getattr(result, "inspected_at", None))
            else:
                payload = dict(result)
                ms = _epoch_ms(payload.get("inspected_at"))
            if pending_images:
                payload[PENDING_IMAGES_KEY] = [str(k) for k in pending_images]
            cam = _safe_token(payload.get("cam_id", "CAM"))
            dst = self.pending_path / f"{ms}_{cam}.json"
            n = 1
            while dst.exists():
                dst = self.pending_path / f"{ms}_{cam}-{n}.json"
                n += 1
            self._atomic_write(
                dst, json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            self.enqueued += 1
            log.info(
                "스풀 적재: %s (pending_images=%d, 대기=%d)",
                dst.name,
                len(pending_images or ()),
                self.pending_count(),
            )
            self._enforce_cap()
            return dst
        except Exception as exc:  # noqa: BLE001 — 스풀 실패가 루프를 죽이면 안 된다.
            log.error("스풀 적재 실패(결과 유실 위험): %s", exc)
            return None

    def save_image(self, key: str, jpeg: bytes) -> Optional[str]:
        """업로드 실패한 JPEG 를 images/{키경로} 로 저장(키 = 상대경로 그대로).

        키는 결정적(raw/<name>.jpg 등)이므로 나중 업로드로 그대로 복구된다.
        """
        try:
            rel = Path(str(key).lstrip("/"))
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"허용되지 않는 이미지 키: {key!r}")
            self._atomic_write(self.images_path / rel, jpeg)
            return str(rel)
        except Exception as exc:  # noqa: BLE001
            log.error("스풀 이미지 저장 실패(%s): %s", key, exc)
            return None

    # --- 용량 상한(SD 카드 보호) ---
    def _total_bytes(self) -> int:
        total = 0
        try:
            for p in self.base.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    continue
        except OSError:
            pass
        return total

    def _item_image_files(self, json_path: Path) -> List[Path]:
        """항목이 참조하는 스풀 이미지 파일 목록(존재하는 것만)."""
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
        files = []
        for key in payload.get(PENDING_IMAGES_KEY, []) or []:
            p = self.images_path / str(key).lstrip("/")
            if p.is_file():
                files.append(p)
        return files

    def _drop_files(self, files: Iterable[Path]) -> int:
        freed = 0
        for f in files:
            try:
                freed += f.stat().st_size
                f.unlink()
            except OSError:
                continue
        return freed

    def _enforce_cap(self) -> int:
        """총 크기 > 상한이면 가장 오래된 pending 항목(+이미지)부터 삭제.

        반환: 드롭한 항목 수. 여전히 초과면 dead/ 오래된 파일도 정리한다.
        """
        total = self._total_bytes()
        if total <= self.max_bytes:
            return 0
        dropped = 0
        for path in self._pending_files():
            if total <= self.max_bytes:
                break
            total -= self._drop_files(self._item_image_files(path) + [path])
            dropped += 1
            self.dropped += 1
        if dropped:
            log.warning(
                "스풀 용량 상한(%.1fMB) 초과 — 오래된 항목 %d개 삭제(누적 드롭=%d)",
                self.max_bytes / (1024 * 1024),
                dropped,
                self.dropped,
            )
        if total > self.max_bytes:
            try:
                dead_files = sorted(
                    (p for p in self.dead_path.glob("*.json")), key=_oldest_key
                )
            except OSError:
                dead_files = []
            for f in dead_files:
                if total <= self.max_bytes:
                    break
                total -= self._drop_files([f])
        return dropped

    # --- 재전송 ---
    def _move_dead(self, path: Path) -> None:
        try:
            self.dead_path.mkdir(parents=True, exist_ok=True)
            os.replace(path, self.dead_path / path.name)
        except OSError as exc:
            log.warning("dead 이동 실패(%s): %s", path.name, exc)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _upload_pending_images(
        self, keys: Sequence[str], upload_fn: Optional[UploadFn]
    ) -> str:
        """항목의 대기 이미지 선업로드. 반환: 'ok'|'hold'|'abort'.

        - 파일이 이미 없으면(선행 flush 에서 업로드됨) 완료로 간주.
        - TransportError(연결 오류) → abort(네트워크 다운 — flush 전체 중단).
        - 그 외 실패/업로더 부재 → hold(항목 보류, 다음 기회).
        """
        for key in keys:
            img = self.images_path / str(key).lstrip("/")
            if not img.is_file():
                continue  # 이미 업로드됨(또는 유실) — 재시도 불필요.
            if upload_fn is None:
                return "hold"
            try:
                upload_fn(str(key), img.read_bytes())
            except httpx.TransportError as exc:
                log.info("이미지 업로드 연결 오류(%s) — flush 중단", exc)
                return "abort"
            except Exception as exc:  # noqa: BLE001
                log.warning("스풀 이미지 업로드 실패(%s): %s — 항목 보류", key, exc)
                return "hold"
            try:
                img.unlink(missing_ok=True)
            except OSError:
                pass
        return "ok"

    def flush(
        self,
        post_fn: PostFn,
        *,
        upload_fn: Optional[UploadFn] = None,
        batch: Optional[int] = None,
    ) -> FlushReport:
        """pending 항목을 oldest-first 로 최대 batch 개 재전송.

        항목별: 대기 이미지 선업로드 → `_pending_images` 제거 → POST.
        - 2xx: 파일 삭제(성공).
        - 연결 오류(status 0/TransportError): 네트워크 아직 다운 — 즉시 중단.
        - 4xx: dead/ 로 이동(영구 오류 — 무한 루프 방지).
        - 5xx/이미지 실패: 항목 보류(다음 기회).
        """
        rep = FlushReport()
        limit = batch if batch is not None else self.flush_batch
        for path in self._pending_files()[: max(1, int(limit))]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("스풀 파일 파손(%s): %s — dead 이동", path.name, exc)
                self._move_dead(path)
                rep.dead += 1
                self.dead += 1
                continue
            keys = payload.pop(PENDING_IMAGES_KEY, None) or []
            state = self._upload_pending_images(keys, upload_fn)
            if state == "abort":
                rep.aborted = True
                rep.held += 1
                break
            if state == "hold":
                rep.held += 1
                continue
            status, detail = post_fn(payload)
            if 200 <= status < 300:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                rep.sent += 1
                self.sent += 1
            elif status == 0:
                log.info("재전송 연결 오류(%s) — 네트워크 다운, flush 중단", detail)
                rep.aborted = True
                rep.held += 1
                break
            elif is_permanent_status(status):
                log.warning(
                    "재전송 영구 오류(status=%d, %s) — dead 이동: %s",
                    status,
                    detail,
                    path.name,
                )
                self._move_dead(path)
                rep.dead += 1
                self.dead += 1
            else:  # 5xx — 서버 일시 장애, 항목 보류.
                log.info("재전송 보류(status=%d, %s): %s", status, detail, path.name)
                rep.held += 1
        if rep.sent or rep.dead or rep.aborted:
            log.info(
                "스풀 flush: sent=%d held=%d dead=%d aborted=%s (대기=%d)",
                rep.sent,
                rep.held,
                rep.dead,
                rep.aborted,
                self.pending_count(),
            )
        return rep
