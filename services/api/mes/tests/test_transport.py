"""MES 전송 추상화 테스트 (멱등 헤더/바디, 실패 변환)."""
from __future__ import annotations

import pytest

from mes.transport import FakeMesTransport, HttpxMesTransport, MesTransportError


def test_fake_records_idem_and_body():
    t = FakeMesTransport()
    resp = t.send({"lot": "L1"}, idem_key="K1")
    assert resp["status"] == "accepted"
    assert t.sent_keys == ["K1"]
    assert t.sent[0]["payload"]["lot"] == "L1"


def test_fake_duplicate_detection():
    t = FakeMesTransport()
    t.send({}, idem_key="K1")
    resp = t.send({}, idem_key="K1")
    assert resp["status"] == "duplicate"


def test_fake_transient_failure_then_success():
    t = FakeMesTransport(fail_times=2)
    for _ in range(2):
        with pytest.raises(MesTransportError):
            t.send({}, idem_key="K")
    assert t.send({}, idem_key="K")["status"] == "accepted"


def test_httpx_transport_injected_client_sends_idem():
    """주입 클라이언트로 멱등키가 헤더+바디 양쪽에 실리는지."""
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    class _Client:
        def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    t = HttpxMesTransport("http://mes/quality", idem_header="X-Idem", client=_Client())
    t.send({"lot": "L"}, idem_key="KEY1")
    assert captured["headers"]["X-Idem"] == "KEY1"
    assert captured["json"]["idem_key"] == "KEY1"
    assert captured["json"]["lot"] == "L"


def test_httpx_transport_error_status_raises():
    class _Resp:
        status_code = 503

        def json(self):
            return {}

    class _Client:
        def post(self, url, json, headers):
            return _Resp()

    t = HttpxMesTransport("http://mes", client=_Client())
    with pytest.raises(MesTransportError) as ei:
        t.send({}, idem_key="K")
    assert ei.value.status_code == 503
