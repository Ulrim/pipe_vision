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
from vision.imaging import save_inspection_images  # noqa: E402

from .client import ApiClient
from .config import WorkerConfig
from .dataset import ensure_dataset

log = logging.getLogger("aivis.vision.worker")


class Worker:
    """검사 워커. 의존성(client/pipeline/camera)을 주입 가능 → 테스트 용이."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        client: Optional[ApiClient] = None,
        pipeline: Optional[InspectionPipeline] = None,
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
        self.item: Optional[ItemMaster] = None
        self.camera = None
        self.trigger = None
        self.acq: Optional[AcquisitionService] = None
        self._stop = False
        self.success = 0
        self.failure = 0
        self.processed = 0

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
            self.acq = AcquisitionService(camera=self.camera, max_retries=3)
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
        if not self._setup_camera():
            return False
        return True

    # --- 단일 검사 사이클 ---
    def run_once(self) -> bool:
        """트리거 1회 → 검사 → POST. 성공 적재면 True. 절대 raise 하지 않는다."""
        assert self.item is not None and self.acq is not None
        try:
            grab = self.acq.grab_with_retry()
            if not grab.ok:
                log.warning("프레임 취득 실패: %s", grab.error)
                self.failure += 1
                return False
            inspected_at = datetime.now(timezone.utc)
            # proc_time_ms KPI(<300ms) 에 이미지 I/O 가 포함되지 않도록
            # 판정(pipeline.run) 을 먼저 끝낸 뒤 raw/result 를 저장한다.
            verdict = self.pipeline.run(grab.frame, self.item)
            saved = save_inspection_images(
                grab.frame,
                verdict,
                images_dir=self.cfg.images_dir,
                lot=self.cfg.lot,
                item_code=self.cfg.item_code,
                inspected_at=inspected_at,
                item=self.item,
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
            ok, detail = self.client.post_inspection(result)
        except Exception as exc:  # noqa: BLE001
            # 어떤 단계 예외도 루프를 죽이지 않는다(자동검사율/가동 유지).
            log.exception("검사 사이클 예외: %s", exc)
            self.failure += 1
            return False
        finally:
            self.processed += 1

        if ok:
            self.success += 1
            log.debug(
                "적재 OK verdict=%s proc=%dms (%s)",
                result.final_verdict,
                result.proc_time_ms,
                detail,
            )
        else:
            self.failure += 1
            log.warning("적재 실패: %s", detail)
        return ok

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
        if not self.startup():
            return 1

        # 첫 루프 준비 완료 → healthcheck 계약(/tmp/vision_ready).
        self._write_ready()
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
            self.run_once()
            n += 1
            if self.cfg.log_every and n % self.cfg.log_every == 0:
                log.info(
                    "진행: processed=%d success=%d failure=%d",
                    self.processed,
                    self.success,
                    self.failure,
                )
            if self.cfg.max_iterations and n >= self.cfg.max_iterations:
                log.info("max_iterations(%d) 도달 — 종료", self.cfg.max_iterations)
                break

        self.shutdown()
        log.info(
            "워커 종료: processed=%d success=%d failure=%d",
            self.processed,
            self.success,
            self.failure,
        )
        return 0

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
