"""키오스크 자동 로그인 — 파이 자체 화면 전용 (M14 확장).

현장 문제: 작업자 화면(7인치 LCD)의 토큰이 8시간(1교대)마다 만료돼, 교대마다
장갑 낀 손으로 터치 키보드에 아이디·비밀번호를 입력해야 했다. 그래서 **파이
자신의 화면(루프백 요청)** 에 한해 작업자 권한 토큰을 자동 발급한다.

여기서 지키는 것은 그 **경계**다: 네트워크 너머(사무실 PC)로는 절대 열리지
않고, 권한은 작업자로 한정되며, 설정으로 끌 수 있어야 한다.
"""
from __future__ import annotations

from types import SimpleNamespace


def test_is_loopback_classifies_hosts():
    """자동 로그인을 열어줄지 가르는 판단 — 이 분류가 틀리면 네트워크 너머로
    작업자 계정이 새어나간다. 실제 함수를 직접 검증한다."""
    import routers.auth as auth_mod

    def req(host):
        return SimpleNamespace(client=SimpleNamespace(host=host))

    assert auth_mod._is_loopback(req("127.0.0.1")) is True
    assert auth_mod._is_loopback(req("::1")) is True
    # 사내망·외부 주소, 그리고 루프백처럼 보이는 호스트명은 모두 거절.
    for host in ("192.168.0.42", "10.0.0.5", "203.0.113.9", "", "127.0.0.1.evil.com"):
        assert auth_mod._is_loopback(req(host)) is False, host
    # 클라이언트 정보가 없으면 거절(안전 기본값).
    assert auth_mod._is_loopback(SimpleNamespace(client=None)) is False


def test_kiosk_login_from_loopback_gives_operator(client, monkeypatch):
    """파이 화면에서는 자동 로그인되고, 권한은 **작업자로 한정**된다."""
    import routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_is_loopback", lambda _req: True)
    r = client.post("/auth/kiosk")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["role"] == "operator"
    assert body["username"] == "kiosk"

    auth = {"Authorization": f"Bearer {body['access_token']}"}
    # 검사 조회는 가능(작업자 업무).
    assert client.get("/inspection", headers=auth).status_code == 200
    # 관리자 전용 동작(프로그램 업데이트)은 막힌다.
    assert client.get("/system/update", headers=auth).status_code == 403
    # 기준정보 변경도 막힌다(품질관리자 이상).
    assert client.put(
        "/master/active", headers=auth, json={"item_code": "X", "lot": None,
                                              "work_order": None}
    ).status_code == 403


def test_kiosk_login_rejected_from_remote_client(client, monkeypatch):
    """사무실 PC 등 외부에서 파이 IP 로 접속하면 자동 로그인은 거절된다.

    자동 로그인의 근거는 '이 장비에 물리적으로 접근할 수 있는 사람'이라는 점
    뿐이므로 네트워크 너머에는 열어주면 안 된다.
    """
    import routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_is_loopback", lambda _req: False)
    assert client.post("/auth/kiosk").status_code == 403


def test_kiosk_login_can_be_disabled(client, monkeypatch):
    """설정으로 끌 수 있다(AIVIS_KIOSK_AUTOLOGIN=false) → 항상 로그인 요구."""
    import routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_is_loopback", lambda _req: True)
    monkeypatch.setattr(
        auth_mod, "get_settings", lambda: SimpleNamespace(kiosk_autologin=False)
    )
    assert client.post("/auth/kiosk").status_code == 403


def test_kiosk_account_cannot_be_used_for_password_login(client, monkeypatch):
    """키오스크 계정은 무작위 비밀번호라 일반 로그인 경로로는 못 들어온다.

    (계정 이름이 알려져 있으므로 추측 가능한 비밀번호면 외부에서 그대로 로그인된다.)
    """
    import routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_is_loopback", lambda _req: True)
    client.post("/auth/kiosk")  # 계정 생성
    for pw in ("kiosk", "", "aivis1234", "password", "admin"):
        r = client.post("/auth/login", json={"username": "kiosk", "password": pw})
        assert r.status_code == 401, pw
