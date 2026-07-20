"""검사 워커 런타임 루프 (CLAUDE.md §4 ①~⑥).

트리거 → camera.grab() → InspectionPipeline.run() → to_inspection_result() →
POST /inspection. proc_time_ms 포함. 예외에도 절대 죽지 않고 계속 돈다.

기동 시퀀스(견고):
  1) API readiness 폴링(GET /health).
  2) ItemMaster 확보(GET /master/items/{code}; 인증 폴백 포함).
  3) 첫 루프 준비되면 /tmp/vision_ready 생성(Dockerfile healthcheck 계약).
  4) SIGTERM/SIGINT graceful 종료.
"""
from __future__ import annotations

import logging
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from aivis_types import ItemMaster

from ._bootstrap import (
    AcquisitionService,
    InspectionPipeline,
    create_camera,
    create_trigger,
    to_inspection_result,
)

# _bootstrap 이 vision 패키지 경로를 보강한 뒤 import (flat/패키지 양립).
from vision.imaging import (  # noqa: E402
    save_batch_images,
    save_inspection_images,
)
from vision.imaging.storage import (  # noqa: E402
    SUPABASE,
    StorageSettings,
    build_backend,
)
from vision.multi import (  # noqa: E402
    BatchMeta,
    inspect_batch,
    tube_to_inspection,
)

from .client import ApiClient
from .config import WorkerConfig
from .dataset import ensure_dataset
from .spool import SpoolQueue

log = logging.getLogger("aivis.vision.worker")


# 핫리로드 변경 감지 키(하나라도 바뀌면 self.item 을 교체). capture_recipe 는
# dict 비교, 나머지는 스칼라 비교. version 은 백엔드가 변경 시 자동 증가하므로
# 값 자체가 동일해도 version 만 오르면 갱신으로 본다(감사/추적 일관).
_RELOAD_KEYS = (
    "version",
    "px_to_mm_scale",
    "tol_plus_mm",
    "tol_minus_mm",
    "oil_threshold",
    "discolor_threshold",
    "scratch_threshold",
    "expected_count",
    "capture_recipe",
)


def _item_changed(old: ItemMaster, new: ItemMaster) -> bool:
    """기준정보 변경 여부(리로드 키 중 하나라도 다르면 True)."""
    for key in _RELOAD_KEYS:
        if getattr(old, key, None) != getattr(new, key, None):
            return True
    return False


def _ng_flag(final_verdict) -> int:
    """final_verdict → 하트비트 ng(0/1). Verdict enum/문자열 양쪽 안전.

    final_verdict 는 Verdict enum 이라 str(Verdict.NG)=="Verdict.NG" 이다.
    반드시 .value("NG")로 비교해야 한다(하트비트 ng 카운트 누락 방지).
    """
    val = getattr(final_verdict, "value", final_verdict)
    return 1 if val == "NG" else 0


class Worker:
    """검사 워커. 의존성(client/pipeline/camera)을 주입 가능 → 테스트 용이."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        client: Optional[ApiClient] = None,
        pipeline: Optional[InspectionPipeline] = None,
        spool: Optional[SpoolQueue] = None,
        image_uploader=None,
    ) -> None:
        self.cfg = config
        self._owns_client = client is None
        self.client = client or ApiClient(
            config.api_url,
            service_token=config.service_token,
            seed_user=config.seed_admin_user,
            seed_password=config.seed_admin_password,
            timeout_s=config.http_timeout_s,
        )
        self.pipeline = pipeline or InspectionPipeline()
        # 오프라인 스풀(디스크 버퍼) — POST/이미지 업로드 실패 시 유실 방지.
        self.spool = spool or SpoolQueue(
            config.spool_dir,
            max_mb=config.spool_max_mb,
            flush_batch=config.spool_flush_batch,
        )
        # flush 시 pending 이미지 업로더((key, jpeg) -> None). 주입 없으면
        # supabase 설정이 있을 때 lazily 구성한다.
        self._image_uploader = image_uploader
        self._uploader_built = image_uploader is not None
        self.item: Optional[ItemMaster] = None
        # 마지막 기준정보 리로드 시각(UTC). startup 성공 시 now 로 세팅되어 이후
        # item_reload_s 주기로 재조회한다(핫리로드 — 재시작 없이 캘리브레이션 반영).
        self._last_item_reload: Optional[datetime] = None
        self.camera = None
        self.trigger = None
        self.acq: Optional[AcquisitionService] = None
        self._stop = False
        self.success = 0
        self.failure = 0
        self.processed = 0
        self.spooled = 0

    # --- 종료 시그널 ---
    def request_stop(self, *_args) -> None:
        log.info("종료 신호 수신 — graceful shutdown")
        self._stop = True

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError):
                # 메인 스레드가 아니면(테스트 등) 무시.
                log.debug("시그널 핸들러 설치 생략(non-main thread)")

    # --- 헬스 파일 ---
    def _write_ready(self) -> None:
        try:
            Path(self.cfg.ready_file).write_text(
                f"ready {datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8"
            )
            log.info("readiness 파일 생성: %s", self.cfg.ready_file)
        except Exception as exc:  # noqa: BLE001
            log.warning("readiness 파일 생성 실패(%s): %s", self.cfg.ready_file, exc)

    def _clear_ready(self) -> None:
        try:
            Path(self.cfg.ready_file).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    # --- 카메라/트리거 ---
    def _setup_camera(self) -> bool:
        dataset_dir = self.cfg.dataset_dir
        if self.cfg.camera_mode == "sim":
            dataset_dir = ensure_dataset(self.cfg.dataset_dir)
        try:
            self.camera = create_camera(dataset_dir=dataset_dir, view_filter=None)
            if self.item is not None and self.item.capture_recipe:
                self.camera.configure(self.item.capture_recipe)
            else:
                self.camera.configure({})
            self.acq = AcquisitionService(
                camera=self.camera,
                max_retries=3,
                grab_timeout_s=self.cfg.grab_timeout_s,
            )
            self.trigger = create_trigger(interval_s=self.cfg.interval_s)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("카메라/트리거 초기화 실패: %s", exc)
            return False

    # --- 기동 시퀀스 ---
    def startup(self) -> bool:
        """API readiness + ItemMaster 확보 + 카메라 준비. 성공 시 True."""
        if not self.client.wait_for_api(timeout_s=self.cfg.api_wait_timeout_s):
            log.error("API 미가동 — 워커 기동 중단")
            return False
        self.item = self.client.fetch_item(
            self.cfg.item_code, timeout_s=self.cfg.item_wait_timeout_s
        )
        if self.item is None:
            log.error("ItemMaster 미확보(%s) — 워커 기동 중단", self.cfg.item_code)
            return False
        # 기동 fetch 를 '마지막 리로드'로 기록 → 이후 item_reload_s 경과 시 재조회.
        self._last_item_reload = datetime.now(timezone.utc)
        if not self._setup_camera():
            return False
        return True

    # --- 기준정보 핫리로드 ---
    def _maybe_reload_item(self, now: datetime) -> None:
        """주기적으로 item_master 를 재조회해 캘리브레이션/공차/임계값/expected_count/
        촬영 레시피 변경을 **워커 재시작 없이** 반영한다(사용자 피드백①).

        정책:
          - item_reload_s <= 0 이면 비활성(기동 시 1회 fetch 후 고정).
          - 마지막 리로드 이후 item_reload_s 미경과면 아무 것도 안 한다.
          - 재조회는 client.refetch_item(단발, 비블로킹). 실패/None 이면 기존
            self.item 을 그대로 유지한다(라이브 검사 방해 금지).
          - 변경 감지(_item_changed) 시에만 self.item 을 교체하고 변경 요약을
            로깅한다. capture_recipe 가 바뀌면 camera.configure 재적용,
            expected_count 가 바뀌면 단일↔배치 전환을 로그로 알린다(run_once 가
            매 호출 self.item.expected_count 를 읽으므로 다음 사이클부터 자동 반영).
        어떤 예외도 루프로 새어나가지 않게 통째로 감싼다.
        """
        try:
            if self.cfg.item_reload_s <= 0 or self.item is None:
                return
            if self._last_item_reload is not None:
                elapsed = (now - self._last_item_reload).total_seconds()
                if elapsed < self.cfg.item_reload_s:
                    return
            # 재조회 시도 시각을 먼저 기록(성공/실패와 무관하게 주기 유지).
            self._last_item_reload = now
            fresh = self.client.refetch_item(self.cfg.item_code)
            if fresh is None:
                log.debug("기준정보 재조회 실패/무응답 — 기존 기준정보 유지")
                return
            if not _item_changed(self.item, fresh):
                return
            old = self.item
            self.item = fresh
            self._log_item_change(old, fresh)
            self._reapply_recipe_if_changed(old, fresh)
        except Exception as exc:  # noqa: BLE001
            log.warning("기준정보 재조회 예외(무시 — 기존 기준정보 유지): %s", exc)

    def _log_item_change(self, old: ItemMaster, new: ItemMaster) -> None:
        """기준정보 갱신 요약 로그(무엇이 어떻게 바뀌었는지 한눈에)."""
        old_exp = int(getattr(old, "expected_count", 1) or 1)
        new_exp = int(getattr(new, "expected_count", 1) or 1)
        log.info(
            "기준정보 갱신: version %s→%s, scale %s→%s, tol +%s/-%s→+%s/-%s, "
            "oil %s→%s, dis %s→%s, scr %s→%s, expected %s→%s",
            getattr(old, "version", None), getattr(new, "version", None),
            old.px_to_mm_scale, new.px_to_mm_scale,
            old.tol_plus_mm, old.tol_minus_mm, new.tol_plus_mm, new.tol_minus_mm,
            old.oil_threshold, new.oil_threshold,
            old.discolor_threshold, new.discolor_threshold,
            old.scratch_threshold, new.scratch_threshold,
            old_exp, new_exp,
        )
        if old_exp != new_exp:
            log.info(
                "expected_count 변경 %d→%d — 다음 사이클부터 %s 모드로 전환",
                old_exp, new_exp, "배치(다중 튜브)" if new_exp > 1 else "단일",
            )

    def _reapply_recipe_if_changed(self, old: ItemMaster, new: ItemMaster) -> None:
        """capture_recipe 가 바뀌었으면 카메라에 재적용(예외는 로깅 후 계속)."""
        if getattr(new, "capture_recipe", None) == getattr(old, "capture_recipe", None):
            return
        if self.camera is None:
            return
        try:
            self.camera.configure(new.capture_recipe or {})
            log.info("촬영 레시피 갱신 → 카메라 재설정 적용: %s", new.capture_recipe)
        except Exception as exc:  # noqa: BLE001
            log.warning("촬영 레시피 재적용 실패(계속 진행): %s", exc)

    # --- 스풀 재전송 지원 ---
    def _spool_uploader(self):
        """flush 용 pending 이미지 업로더((key, jpeg) -> None). 없으면 None.

        supabase 스토리지가 설정된 경우에만 원격 업로더를 lazily 만든다
        (pending 이미지는 supabase 업로드 실패에서만 생기므로 충분).
        """
        if not self._uploader_built:
            self._uploader_built = True
            if self.cfg.storage_backend == SUPABASE and self.cfg.supabase_configured:
                backend = build_backend(
                    StorageSettings(
                        backend=SUPABASE,
                        images_dir=self.cfg.images_dir,
                        supabase_url=self.cfg.supabase_url,
                        supabase_key=self.cfg.supabase_key,
                        supabase_bucket=self.cfg.supabase_bucket,
                    )
                )
                self._image_uploader = backend.put
        return self._image_uploader

    def flush_spool(self) -> None:
        """스풀 재전송(oldest-first, 배치 상한). 예외에도 루프를 죽이지 않는다."""
        try:
            if self.spool.pending_count() == 0:
                return
            self.spool.flush(
                self.client.post_inspection_json,
                upload_fn=self._spool_uploader(),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("스풀 flush 예외(계속): %s", exc)

    # --- 라이브니스 하트비트 ---
    def _send_status(
        self,
        *,
        expected: int,
        detected: int,
        ng: int,
        mismatch: bool,
        proc_time_ms: int,
        ts: str,
        error: Optional[str],
    ) -> None:
        """검사 사이클 상태 하트비트 1건을 API 에 베스트에포트로 보낸다.

        성공/0검출/취득실패 모든 사이클에서 호출되어 HMI 가 워커 생존을 인지하게
        한다(순수 라이브니스 — 멱등/스풀과 무관). client.post_status 가 이미 예외를
        삼키지만, payload 구성 중 예외도 라이브 루프에 새지 않도록 이중으로 감싼다.
        self.success/failure/return 값에는 절대 영향을 주지 않는다.
        """
        try:
            self.client.post_status(
                {
                    "cam_id": self.cfg.cam_id,
                    "item_code": self.cfg.item_code,
                    "expected": int(expected),
                    "detected": int(detected),
                    "ng": int(ng),
                    "mismatch": bool(mismatch),
                    "proc_time_ms": int(proc_time_ms),
                    "ts": ts,
                    "error": error,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("status 하트비트 구성/전송 예외(무시): %s", exc)

    # --- 검사 사이클 디스패치(단일 / 배치) ---
    def run_once(self) -> bool:
        """트리거 1회 처리. item.expected_count>1 이면 배치 모드, 아니면 단일.

        - 단일(기본, expected_count=1): 현행 동작 그대로(변경 없음).
        - 배치(expected_count>1): 1프레임 → inspect_batch → 튜브 N개를 각각
          InspectionResult 로 POST(이미지는 배치당 1회 저장, tube_index 로 구분).

        반환: 이 사이클의 (모든) 적재가 성공하면 True. raise 금지.
        """
        expected = int(getattr(self.item, "expected_count", 1) or 1)
        if expected > 1:
            return self._run_once_batch()
        return self._run_once_single()

    # --- 단일 검사 사이클 ---
    def _run_once_single(self) -> bool:
        """트리거 1회 → 검사 → POST(실패 시 스풀). 성공 적재면 True. raise 금지.

        실패 분류:
          - 2xx: 성공.
          - 연결 오류/타임아웃/5xx: 스풀 적재(오프라인 대비 — 서버 멱등이 중복 차단).
          - 4xx(401/403/422 등): 영구 오류 — 스풀하지 않고 오류 로그만.
          - 이미지 업로드 실패(supabase): JPEG 를 스풀에 보존하고 결과도 스풀
            (flush 가 이미지 선업로드 후 POST — 키는 결정적이라 경로 유지).
        """
        assert self.item is not None and self.acq is not None
        status = -1
        detail = ""
        pending_images: list[str] = []
        try:
            grab = self.acq.grab_with_retry()
            if not grab.ok:
                log.warning("프레임 취득 실패: %s", grab.error)
                self.failure += 1
                # 취득 실패(카메라 프리즈)도 하트비트로 알린다 — HMI 가 죽은
                # 듯 보이지 않도록. detected=0, error 로 원인 전달.
                self._send_status(
                    expected=1,
                    detected=0,
                    ng=0,
                    mismatch=True,
                    proc_time_ms=0,
                    ts=datetime.now(timezone.utc).isoformat(),
                    error=grab.error,
                )
                return False
            inspected_at = datetime.now(timezone.utc)
            # proc_time_ms KPI(<300ms) 에 이미지 I/O 가 포함되지 않도록
            # 판정(pipeline.run) 을 먼저 끝낸 뒤 raw/result 를 저장한다.
            # length_span(끝단 2점·측정선)을 함께 받아 결과 오버레이에 측정 근거를
            # 그린다(계측 이후 데이터라 처리속도 KPI 영향 없음 — 사용자 피드백②).
            verdict, length_span = self.pipeline.run_with_geometry(
                grab.frame, self.item
            )
            saved = save_inspection_images(
                grab.frame,
                verdict,
                images_dir=self.cfg.images_dir,
                lot=self.cfg.lot,
                item_code=self.cfg.item_code,
                inspected_at=inspected_at,
                item=self.item,
                pending_sink=self.spool.save_image,
                length_span=length_span,
            )
            if saved.error:
                # 디스크 쓰기 실패는 검사결과 적재를 막지 않는다(경로 None 로 진행).
                log.warning("이미지 저장 실패(계속 진행): %s", saved.error)
            result = to_inspection_result(
                verdict,
                lot=self.cfg.lot,
                item_code=self.cfg.item_code,
                cam_id=self.cfg.cam_id,
                inspected_at=inspected_at,
                shift=self.cfg.shift,
                operator=self.cfg.operator,
                raw_image_path=saved.raw_image_path,
                result_image_path=saved.result_image_path,
            )
            # 라이브니스 하트비트: 검출 1건 성공 사이클(적재 성공/실패와 무관).
            # 판정(NG)은 최종 result.final_verdict 로 판단한다.
            self._send_status(
                expected=1,
                detected=1,
                ng=_ng_flag(result.final_verdict),
                mismatch=False,
                proc_time_ms=int(result.proc_time_ms or 0),
                ts=inspected_at.isoformat(),
                error=None,
            )
            pending_images = list(getattr(saved, "pending_images", ()) or ())
            if pending_images:
                # 이미지가 아직 원격에 없다 — 결과도 스풀로 우회해 flush 가
                # 이미지 선업로드 후 POST 하도록 한다(경로 무결성 유지).
                self.spool.enqueue(result, pending_images=pending_images)
                self.spooled += 1
                log.warning(
                    "이미지 업로드 실패 → 결과 스풀 적재(pending_images=%d)",
                    len(pending_images),
                )
                return False
            status, detail = self.client.post_inspection_json(
                result.model_dump(mode="json")
            )
        except Exception as exc:  # noqa: BLE001
            # 어떤 단계 예외도 루프를 죽이지 않는다(자동검사율/가동 유지).
            log.exception("검사 사이클 예외: %s", exc)
            self.failure += 1
            return False
        finally:
            self.processed += 1

        if 200 <= status < 300:
            self.success += 1
            log.debug(
                "적재 OK verdict=%s proc=%dms (%s)",
                result.final_verdict,
                result.proc_time_ms,
                detail,
            )
            return True
        if status == 0 or status >= 500:
            # 연결 오류/타임아웃/5xx → 스풀(재시도 대상). 유실 금지.
            self.spool.enqueue(result)
            self.spooled += 1
            log.warning("적재 실패(%s) → 스풀 적재(재전송 대기)", detail)
            return False
        # 4xx 영구 오류(401/403/422 등) — 재시도 무의미, 스풀 금지.
        self.failure += 1
        log.error("적재 영구 오류(스풀 제외): %s", detail)
        return False

    # --- 배치(다중 튜브) 검사 사이클 ---
    def _post_or_classify(self, result) -> bool:
        """InspectionResult 1건 POST → 성공/스풀/영구오류 분류(단일 경로와 동일).

        반환: 2xx 적재 성공이면 True. 재시도 대상(연결/타임아웃/5xx)은 스풀,
        4xx 영구 오류는 실패 계상. 이미지 pending 은 호출자가 사전에 처리한다.
        """
        try:
            status, detail = self.client.post_inspection_json(
                result.model_dump(mode="json")
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("튜브 적재 예외: %s", exc)
            self.failure += 1
            return False
        if 200 <= status < 300:
            self.success += 1
            return True
        if status == 0 or status >= 500:
            self.spool.enqueue(result)
            self.spooled += 1
            log.warning("튜브 적재 실패(%s) → 스풀(재전송 대기)", detail)
            return False
        self.failure += 1
        log.error("튜브 적재 영구 오류(스풀 제외): %s", detail)
        return False

    def _run_once_batch(self) -> bool:
        """트리거 1회 → 배치 검사 → 튜브 N개 각각 POST. raise 금지.

        이미지 저장은 배치당 1회(raw 원본 1장 + 배치 오버레이 1장)이며 모든 튜브
        행이 동일 raw/result 경로·검사시각을 공유한다. 각 튜브는 tube_index(0..N-1)
        로 구분되어 서버 자연키 멱등이 보장한다(같은 tube_index 재전송=멱등).

        원격 이미지 업로드가 실패(pending)하면 튜브 전원을 스풀에 적재해(경로
        무결성 유지) flush 가 이미지 선업로드 후 POST 하도록 한다. 반환: 모든
        튜브가 적재 성공하면 True.
        """
        assert self.item is not None and self.acq is not None
        try:
            grab = self.acq.grab_with_retry()
            if not grab.ok:
                log.warning("프레임 취득 실패(배치): %s", grab.error)
                self.failure += 1
                # 취득 실패도 하트비트로 알린다(detected=0, error). HMI 라이브니스.
                self._send_status(
                    expected=int(getattr(self.item, "expected_count", 1) or 1),
                    detected=0,
                    ng=0,
                    mismatch=True,
                    proc_time_ms=0,
                    ts=datetime.now(timezone.utc).isoformat(),
                    error=grab.error,
                )
                return False
            inspected_at = datetime.now(timezone.utc)
            expected = int(getattr(self.item, "expected_count", 1) or 1)
            # 판정(proc_time KPI)을 먼저 끝낸 뒤 이미지 저장(§4).
            batch = inspect_batch(
                grab.frame, self.item, expected_count=expected
            )
            saved = save_batch_images(
                grab.frame,
                batch,
                images_dir=self.cfg.images_dir,
                lot=self.cfg.lot,
                item_code=self.cfg.item_code,
                inspected_at=inspected_at,
                pending_sink=self.spool.save_image,
            )
            if saved.error:
                log.warning("배치 이미지 저장 실패(계속 진행): %s", saved.error)

            meta = BatchMeta(
                lot=self.cfg.lot,
                item_code=self.cfg.item_code,
                cam_id=self.cfg.cam_id,
                inspected_at=inspected_at,
                ref_length_mm=float(self.item.ref_length_mm),
                work_order=None,
                shift=self.cfg.shift,
                operator=self.cfg.operator,
                raw_image_path=saved.raw_image_path,
                result_image_path=saved.result_image_path,
            )
            pending_images = list(getattr(saved, "pending_images", ()) or ())
            results = [
                tube_to_inspection(t, batch_meta=meta) for t in batch.tubes
            ]

            log.info(
                "배치 검사: detected=%d/%s NG=%d mismatch=%s tubes=%d",
                batch.count_detected,
                batch.count_expected,
                batch.ng_count,
                batch.count_mismatch,
                len(results),
            )

            if pending_images:
                # 이미지가 원격에 아직 없다 — 모든 튜브 행을 스풀로 우회해 flush 가
                # 이미지 선업로드 후 POST 하도록(경로 무결성). 서버 멱등이 중복 차단.
                for result in results:
                    self.spool.enqueue(result, pending_images=pending_images)
                    self.spooled += 1
                log.warning(
                    "배치 이미지 업로드 실패 → 튜브 %d건 스풀 적재(pending=%d)",
                    len(results),
                    len(pending_images),
                )
                return False

            all_ok = True
            for result in results:
                if not self._post_or_classify(result):
                    all_ok = False
            # 라이브니스 하트비트: 0검출(빈 배치) 사이클에서도 반드시 보낸다 —
            # detected=0 이면 POST 가 0건이라 HMI 가 죽은 듯 보이는 문제를 막는다.
            # proc_time 은 튜브들 proc_time_ms 중 최댓값(없으면 0)으로 근사한다.
            proc = max(
                (int(getattr(t, "proc_time_ms", 0) or 0) for t in batch.tubes),
                default=0,
            )
            self._send_status(
                expected=expected,
                detected=batch.count_detected,
                ng=batch.ng_count,
                mismatch=bool(batch.count_mismatch),
                proc_time_ms=proc,
                ts=inspected_at.isoformat(),
                error=None,
            )
            return all_ok and len(results) > 0
        except Exception as exc:  # noqa: BLE001
            log.exception("배치 검사 사이클 예외: %s", exc)
            self.failure += 1
            return False
        finally:
            self.processed += 1

    # --- 메인 루프 ---
    def run(self) -> int:
        """기동 → 루프. 반환: 종료 코드(0 정상 종료)."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        self._install_signal_handlers()
        log.info(
            "검사 워커 시작: camera=%s api=%s item=%s cam=%s lot=%s interval=%dms",
            self.cfg.camera_mode,
            self.cfg.api_url,
            self.cfg.item_code,
            self.cfg.cam_id,
            self.cfg.lot,
            self.cfg.interval_ms,
        )
        self.cfg.warn_if_misconfigured(log)
        if not self.startup():
            return 1

        # 첫 루프 준비 완료 → healthcheck 계약(/tmp/vision_ready).
        self._write_ready()
        # 이전 세션(오프라인 구간)의 스풀 잔량을 기동 시 1회 재전송 시도.
        self.flush_spool()
        log.info("검사 루프 진입")

        n = 0
        while not self._stop:
            # 트리거(주기 sleep). 종료 신호를 빠르게 반영하기 위해 짧은 timeout.
            try:
                self.trigger.wait_for_trigger(timeout=self.cfg.interval_s or None)
            except Exception as exc:  # noqa: BLE001
                log.warning("트리거 대기 예외(%s) — 계속", exc)
            if self._stop:
                break
            # 기준정보 핫리로드(주기 경과 시에만 실제 재조회) — 검사 직전에 반영해
            # 이번 사이클부터 최신 캘리브레이션/공차/임계값/expected_count 를 쓴다.
            self._maybe_reload_item(datetime.now(timezone.utc))
            self.run_once()
            # 매 루프 소량(flush_batch 상한) 재전송 — 라이브 검사를 굶기지 않는다.
            self.flush_spool()
            n += 1
            if self.cfg.log_every and n % self.cfg.log_every == 0:
                log.info(
                    "진행: processed=%d success=%d failure=%d spooled=%d spool=%s "
                    "grab_timeout=%d grab_reconnect=%d",
                    self.processed,
                    self.success,
                    self.failure,
                    self.spooled,
                    self.spool.stats(),
                    self._acq_timeout_count(),
                    self._acq_reconnect_count(),
                )
            if self.cfg.max_iterations and n >= self.cfg.max_iterations:
                log.info("max_iterations(%d) 도달 — 종료", self.cfg.max_iterations)
                break

        # graceful shutdown: flush 강제 금지(빠른 종료 유지) — 잔량은 다음
        # 기동 시(startup flush) 재전송된다.
        self.shutdown()
        log.info(
            "워커 종료: processed=%d success=%d failure=%d spooled=%d spool=%s "
            "grab_timeout=%d grab_reconnect=%d",
            self.processed,
            self.success,
            self.failure,
            self.spooled,
            self.spool.stats(),
            self._acq_timeout_count(),
            self._acq_reconnect_count(),
        )
        return 0

    # --- 취득 워치독 카운터(관측성) ---
    def _acq_timeout_count(self) -> int:
        return self.acq.timeout_count if self.acq is not None else 0

    def _acq_reconnect_count(self) -> int:
        return self.acq.reconnect_count if self.acq is not None else 0

    def shutdown(self) -> None:
        self._clear_ready()
        try:
            if self.camera is not None:
                self.camera.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.trigger is not None:
                self.trigger.close()
        except Exception:  # noqa: BLE001
            pass
        if self._owns_client:
            self.client.close()


def main() -> int:
    cfg = WorkerConfig.from_env()
    return Worker(cfg).run()
