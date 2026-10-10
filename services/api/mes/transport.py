"""MES REST 전송 추상화 (CLAUDE.md §7.3, §5 M9).

rest 모드에서 외부 MES 로 검사 핵심값을 POST 한다. 멱등키를 헤더+바디 양쪽에
실어 보내 MES 측이 중복 적재를 막을 수 있게 한다.

- MesTransport     : 전송 인터페이스(추상). adapter/watchdog 가 의존.
- HttpxMesTransport: 실제 httpx 기반 전송(외부 MES 통합 시).
- FakeMesTransport : 통합 전/테스트용. 성공/실패/지연을 주입해 재시도 검증.

외부 엔드포인트 미설정(MES_REST_URL 없음)이면 **연계를 보류**한다
(UnconfiguredMesTransport — 매번 실패 → 행은 mes_synced=false 로 남고 워치독이
주소가 생길 때까지 재시도). 2026-10-10 점검 전에는 이때 가짜 전송이 자동으로
켜져 연계율이 MES 와 무관하게 100% 로 나왔다. 가짜 전송은 이제
MES_REST_FAKE=true 로 **명시해야** 쓰이고, KPI 에 'rest_fake' 로 표시된다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class MesTransportError(Exception):
    """MES 전송 실패. 워치독/재시도 큐가 이 예외를 잡아 재시도한다."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MesTransport(ABC):
    """MES 전송 인터페이스. rest 모드에서 어댑터가 사용."""

    @abstractmethod
    def send(self, payload: Mapping[str, Any], *, idem_key: str) -> dict[str, Any]:
        """검사 핵심값을 MES 로 전송. 실패 시 MesTransportError 를 던진다.

        반환: MES 응답 dict(상태 등). 성공으로 간주되면 예외 없이 반환.
        멱등키는 헤더(`idem_header`)와 바디(`idem_key`) 양쪽에 실린다.
        """

    def close(self) -> None:  # pragma: no cover - 기본 no-op
        """리소스 정리(HTTP 클라이언트 등). 기본은 no-op."""


class HttpxMesTransport(MesTransport):
    """httpx 기반 실제 REST 전송(외부 MES 통합 단계).

    httpx 미설치/엔드포인트 미설정 시에는 사용하지 않는다(adapter 가 분기).
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 5.0,
        idem_header: str = "X-Idempotency-Key",
        client: Any | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self.idem_header = idem_header
        self._client = client  # 주입 가능(테스트). 미주입이면 지연 생성.

    def _get_client(self):
        if self._client is None:
            import httpx  # 지연 임포트(미사용 환경 부담 제거)

            self._client = httpx.Client(timeout=self.timeout_s)
        return self._client

    def send(self, payload: Mapping[str, Any], *, idem_key: str) -> dict[str, Any]:
        body = dict(payload)
        body["idem_key"] = idem_key
        headers = {self.idem_header: idem_key}
        try:
            client = self._get_client()
            resp = client.post(self.url, json=body, headers=headers)
        except Exception as exc:  # 네트워크/타임아웃 등
            raise MesTransportError(f"MES REST 전송 실패: {exc}") from exc

        status = getattr(resp, "status_code", None)
        if status is None or status >= 400:
            raise MesTransportError(
                f"MES REST 비정상 응답 status={status}", status_code=status
            )
        try:
            return resp.json()
        except Exception:
            return {"status": "ok", "http_status": status}

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()


class FakeMesTransport(MesTransport):
    """통합 전/테스트용 인메모리 전송.

    - 전송된 멱등키를 sent[] 에 기록(멱등 검증).
    - fail_times>0 이면 그 횟수만큼 MesTransportError(재시도 검증).
    - fail_keys 에 든 멱등키는 항상 실패(영구 실패 → 최대 재시도 검증).
    """

    def __init__(
        self,
        *,
        fail_times: int = 0,
        fail_keys: set[str] | None = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.calls: int = 0
        self._fail_times = fail_times
        self._fail_keys = fail_keys or set()

    def send(self, payload: Mapping[str, Any], *, idem_key: str) -> dict[str, Any]:
        self.calls += 1
        if idem_key in self._fail_keys:
            raise MesTransportError(f"강제 영구 실패: {idem_key}")
        if self._fail_times > 0:
            self._fail_times -= 1
            raise MesTransportError("강제 일시 실패(재시도 검증)")
        # 멱등: 동일 키 재전송이면 중복으로 표시하되 성공 처리.
        duplicate = any(s["idem_key"] == idem_key for s in self.sent)
        record = {"idem_key": idem_key, "payload": dict(payload)}
        self.sent.append(record)
        return {"status": "duplicate" if duplicate else "accepted", "idem_key": idem_key}

    @property
    def sent_keys(self) -> list[str]:
        return [s["idem_key"] for s in self.sent]


class UnconfiguredMesTransport(MesTransport):
    """rest 모드인데 MES_REST_URL 이 없다 — 보내지 않고 실패로 돌려준다.

    성공으로 치면 연계율이 거짓말을 한다. 실패로 두면 행이 미연계로 남고,
    주소가 설정되는 순간 워치독이 밀린 것을 전부 보낸다(유실 없음).
    """

    def send(self, payload: Mapping[str, Any], *, idem_key: str) -> dict[str, Any]:
        raise MesTransportError(
            "MES_REST_URL 미설정 — 연계 보류(주소를 설정하면 워치독이 재전송한다). "
            "시험용 가짜 전송은 MES_REST_FAKE=true"
        )
