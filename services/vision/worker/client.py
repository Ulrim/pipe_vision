"""백엔드 API 클라이언트 (httpx) — 검사워커 ↔ services/api 연동.

책임:
  - wait_for_api(): GET /health 가 ok 될 때까지 백오프 폴링(무한 대기 금지).
  - fetch_item(): GET /master/items/{code}. service token 이 GET 에 안 통하면
    (operator+ JWT 가드) AIVIS_SEED_ADMIN_USER/PASSWORD 로 POST /auth/login →
    Bearer 확보 폴백.
  - post_inspection(): POST /inspection. 예외/타임아웃을 호출자에게 (ok, detail)
    로 돌려준다(절대 raise 로 루프를 죽이지 않는다).

httpx.Client(transport=...) 를 주입할 수 있어 테스트는 MockTransport / ASGI
TestClient transport 로 실 네트워크 없이 검증한다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx
from aivis_types import InspectionResult, ItemMaster

log = logging.getLogger("aivis.vision.worker.client")


class ApiClient:
    """검사워커용 backend 클라이언트. 인증/재시도/그레이스풀 오류 처리 포함."""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: Optional[str] = None,
        seed_user: str = "admin",
        seed_password: str = "admin1234",
        timeout_s: float = 5.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.seed_user = seed_user
        self.seed_password = seed_password
        self._timeout_s = timeout_s
        self._bearer: Optional[str] = None
        # base_url 이 비어있으면(transport 직결 테스트) httpx 가 요구하는
        # 절대 URL 을 위해 http://worker-test 더미를 쓴다.
        client_base = self.base_url or "http://worker-test"
        self._http = httpx.Client(
            base_url=client_base, timeout=timeout_s, transport=transport
        )

    # --- 인증 헤더 ---
    def _service_headers(self) -> dict[str, str]:
        """POST /inspection 용 내부 서비스 헤더(X-Service-Token + Bearer)."""
        headers: dict[str, str] = {}
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
            headers["Authorization"] = f"Bearer {self.service_token}"
        return headers

    # --- readiness ---
    def health_ok(self) -> bool:
        try:
            resp = self._http.get("/health")
        except httpx.HTTPError as exc:
            log.debug("health 요청 실패: %s", exc)
            return False
        if resp.status_code != 200:
            return False
        try:
            return resp.json().get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False

    def wait_for_api(self, timeout_s: int = 120, sleep_fn=time.sleep) -> bool:
        """GET /health ok 까지 지수 백오프 폴링. 무한 대기 금지(timeout 후 False)."""
        deadline = time.monotonic() + max(1, timeout_s)
        delay = 0.5
        while time.monotonic() < deadline:
            if self.health_ok():
                log.info("API readiness OK (%s)", self.base_url)
                return True
            log.info(
                "API 대기 중(%s) — %.1fs 후 재시도", self.base_url, delay
            )
            sleep_fn(delay)
            delay = min(delay * 1.6, 5.0)
        log.error("API readiness 타임아웃(%ss) — %s", timeout_s, self.base_url)
        return False

    # --- 로그인 폴백(Bearer 확보) ---
    def login(self) -> bool:
        """시드 계정으로 POST /auth/login → Bearer 확보. 성공 시 True."""
        try:
            resp = self._http.post(
                "/auth/login",
                json={"username": self.seed_user, "password": self.seed_password},
            )
        except httpx.HTTPError as exc:
            log.warning("auth/login 요청 실패: %s", exc)
            return False
        if resp.status_code != 200:
            log.warning("auth/login 실패 status=%s", resp.status_code)
            return False
        try:
            self._bearer = resp.json()["access_token"]
        except Exception as exc:  # noqa: BLE001
            log.warning("auth/login 응답 파싱 실패: %s", exc)
            return False
        log.info("시드 계정(%s) 로그인 성공 — Bearer 확보", self.seed_user)
        return True

    def _get_item_once(self, item_code: str) -> tuple[Optional[ItemMaster], int]:
        """GET /master/items/{code} 1회. (item|None, status_code)."""
        headers: dict[str, str] = {}
        if self._bearer:
            headers["Authorization"] = f"Bearer {self._bearer}"
        elif self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
            headers["X-Service-Token"] = self.service_token
        try:
            resp = self._http.get(f"/master/items/{item_code}", headers=headers)
        except httpx.HTTPError as exc:
            log.debug("master GET 실패: %s", exc)
            return None, 0
        if resp.status_code == 200:
            try:
                return ItemMaster(**resp.json()), 200
            except Exception as exc:  # noqa: BLE001
                log.warning("ItemMaster 파싱 실패: %s", exc)
                return None, 200
        return None, resp.status_code

    def fetch_item(
        self, item_code: str, *, timeout_s: int = 120, sleep_fn=time.sleep
    ) -> Optional[ItemMaster]:
        """ItemMaster 확보. GET 가드(operator+) 때문에 인증 폴백을 둔다.

        순서:
          1) (service_token 보유 시) 토큰 헤더로 GET 시도.
          2) 401/403 이면 시드 계정 login → Bearer 로 GET 재시도.
          3) 404(품목 미시드)면 backend 가 데모 품목을 시드한다고 가정해 재시도.
        무한 대기 금지: timeout_s 초과 시 None.
        """
        deadline = time.monotonic() + max(1, timeout_s)
        delay = 0.5
        while time.monotonic() < deadline:
            item, code = self._get_item_once(item_code)
            if item is not None:
                log.info(
                    "ItemMaster 확보: %s (ref=%.3fmm scale=%.6f)",
                    item.item_code,
                    float(item.ref_length_mm),
                    float(item.px_to_mm_scale),
                )
                return item
            if code in (401, 403):
                log.info("master GET 인증 필요(status=%s) — 로그인 폴백", code)
                if not self.login():
                    sleep_fn(delay)
                    delay = min(delay * 1.6, 5.0)
                    continue
                # 로그인 직후 즉시 한 번 더(같은 루프에서).
                item, code = self._get_item_once(item_code)
                if item is not None:
                    return item
            if code == 404:
                log.info(
                    "품목 %s 미존재(404) — backend 데모 시드 대기, 재시도", item_code
                )
            else:
                log.info("master GET 대기(status=%s) — 재시도", code)
            sleep_fn(delay)
            delay = min(delay * 1.6, 5.0)
        log.error("ItemMaster 확보 타임아웃: %s", item_code)
        return None

    def refetch_item(self, item_code: str) -> Optional[ItemMaster]:
        """기준정보 핫리로드용 **단발** 재조회(재시도/슬립 없음).

        fetch_item 은 무한/장기 대기를 피하는 재시도 루프(time.sleep)를 돌지만,
        핫리로드는 라이브 검사 루프 안에서 매 주기 호출되므로 절대 블로킹하면
        안 된다(§워커 요구: 라이브 검사 방해 금지). 따라서 여기서는 GET 을 1회만
        시도하고, 인증 가드(operator+)로 401/403 이면 이미 확보한 Bearer 로
        _get_item_once 가 통과한다 — 만약 Bearer 가 없거나 만료면 시드 로그인 1회
        후 딱 한 번 더 시도한다(대기 없음). 성공 시 ItemMaster, 그 외 None.
        """
        item, code = self._get_item_once(item_code)
        if item is not None:
            return item
        if code in (401, 403) and self.login():
            item, _ = self._get_item_once(item_code)
            return item
        return None

    # --- 결과 적재 ---
    def post_inspection_json(self, payload: dict) -> tuple[int, str]:
        """POST /inspection (payload dict). (status_code, detail). raise 금지.

        status_code 0 = 네트워크/전송 오류(연결 거부·타임아웃 등) — 호출자가
        스풀(재시도) 대상 여부를 분류할 수 있게 HTTP 코드를 그대로 돌려준다.
        스풀 재전송(spool.flush)도 이 메서드를 post_fn 으로 쓴다.
        """
        try:
            resp = self._http.post(
                "/inspection",
                json=payload,
                headers=self._service_headers(),
            )
        except httpx.HTTPError as exc:
            return 0, f"POST 예외: {type(exc).__name__}: {exc}"
        if resp.status_code in (200, 201):
            try:
                body: Any = resp.json()
                status = body.get("status", "stored")
                ident = body.get("id")
                return resp.status_code, f"status={status} id={ident}"
            except Exception:  # noqa: BLE001
                return resp.status_code, f"status={resp.status_code}"
        # backend 가 저장 실패 시 200+queued 를 줄 수도 있으나, 그 외 코드는 실패.
        return resp.status_code, f"status={resp.status_code} body={resp.text[:200]}"

    def post_inspection(self, result: InspectionResult) -> tuple[bool, str]:
        """POST /inspection. (ok, detail). 절대 raise 하지 않는다.

        ok=True 는 2xx(stored/queued 포함). 네트워크 예외/4xx/5xx 는 (False, detail).
        """
        status, detail = self.post_inspection_json(result.model_dump(mode="json"))
        return status in (200, 201), detail

    # --- 라이브니스 하트비트 ---
    def post_status(self, payload: dict) -> None:
        """POST /inspection/status (검사 사이클 상태 하트비트). 베스트에포트.

        HMI 가 "연결됨"인데 0검출/취득실패로 죽은 듯 보이는 문제를 막기 위해, 매
        검사 사이클(성공/0검출/취득실패)마다 순수 라이브니스 신호를 보낸다.
        멱등/스풀과 무관하며, 라이브 검사 루프를 굶기거나 죽이지 않도록:
          - 짧은 타임아웃(http_timeout 또는 2초 중 작은 값)을 쓰고,
          - 모든 예외를 삼켜 절대 raise 하지 않는다(실패는 log.debug 만).
        인증 헤더는 post_inspection_json 과 동일한 서비스토큰 정책을 재사용한다.
        """
        timeout = min(self._timeout_s, 2.0)
        try:
            self._http.post(
                "/inspection/status",
                json=payload,
                headers=self._service_headers(),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("status 하트비트 전송 실패(무시): %s", exc)

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass
