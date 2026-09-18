"""프로그램 자체 업데이트 API (현장 사용자용).

현장 담당자는 개발자가 아니라 터미널을 못 쓴다 — 화면 버튼으로 업데이트가
되어야 한다. 여기서는 그 버튼을 뒷받침하는 계약과 **안전 성질**을 검증한다:
- 관리자 전용(교대 중 작업자가 눌러 검사가 멈추면 안 된다)
- 셸을 실행하지만 **사용자 입력이 명령줄에 절대 섞이지 않는다**
- 중복 실행 방지, 프로세스가 사라져도 결과를 파일에서 복구
"""
from __future__ import annotations

import json

import pytest

from core import updater


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """업데이트 상태/로그 파일을 테스트별 임시 경로로 격리."""
    monkeypatch.setattr(updater, "STATE_DIR", tmp_path / "update")
    monkeypatch.setattr(updater, "STATE_FILE", tmp_path / "update" / "state.json")
    monkeypatch.setattr(updater, "LOG_FILE", tmp_path / "update" / "last.log")


# --- 권한 ------------------------------------------------------------------
def test_update_endpoints_require_admin(client, auth):
    """관리자 전용. 작업자/품질관리자는 403, 무인증은 401."""
    assert client.get("/system/update").status_code == 401
    assert client.get("/system/update", headers=auth("op1")).status_code == 403
    assert client.get("/system/update", headers=auth("qa1")).status_code == 403
    assert client.post("/system/update/start", headers=auth("op1")).status_code == 403
    assert client.post("/system/update/check", headers=auth("op1")).status_code == 403
    # 관리자는 통과(내용은 환경에 따라 다름 — 200 이면 충분).
    assert client.get("/system/update", headers=auth("admin1")).status_code == 200


# --- 응답 계약 --------------------------------------------------------------
def test_update_info_shape(client, auth):
    """화면이 그대로 쓰는 필드가 모두 있어야 한다."""
    r = client.get("/system/update", headers=auth("admin1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"current", "progress"}
    assert set(body["current"]) >= {"available", "commit", "date", "subject"}
    prog = body["progress"]
    assert set(prog) >= {"state", "log_tail"}
    assert prog["state"] in ("idle", "running", "success", "failed")
    assert isinstance(prog["log_tail"], list)


def test_update_info_never_500_without_git(client, auth, monkeypatch):
    """git 이 없는 설치본(배포 패키지 등)에서도 200 + available=false 로 알린다.

    업데이트 화면이 500 으로 죽으면 사용자는 원인을 알 수 없다.
    """
    monkeypatch.setattr(updater, "git_available", lambda: False)
    r = client.get("/system/update", headers=auth("admin1"))
    assert r.status_code == 200
    assert r.json()["current"]["available"] is False


# --- 안전: 사용자 입력이 셸에 닿지 않는다 -----------------------------------
def test_start_update_command_is_fixed_and_has_no_user_input(monkeypatch):
    """실행 명령은 코드가 정한 고정 스크립트 + 화이트리스트 플래그뿐이어야 한다.

    회귀 방지: 어떤 경로로든 요청 본문/쿼리가 명령줄에 섞이면 임의 명령 실행
    (원격 코드 실행)이 된다. start_update 는 인자를 받지 않는 설계이며,
    실제로 Popen 에 넘어가는 argv 를 붙잡아 확인한다.
    """
    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(updater, "git_available", lambda: True)
    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)

    ok, msg = updater.start_update(restart=True)
    assert ok is True, msg

    cmd = captured["cmd"]
    assert cmd[0] == "bash"
    assert cmd[1] == str(updater.UPDATE_SCRIPT)
    # 나머지 인자는 코드가 정한 플래그만.
    assert set(cmd[2:]) <= {"--restart"}
    # 셸을 거치지 않는다(shell=True 면 문자열 주입 위험).
    assert captured["kwargs"].get("shell") in (None, False)
    # 부모(API)가 재시작돼도 살아남도록 세션 분리.
    assert captured["kwargs"].get("start_new_session") is True


def test_start_update_blocked_when_not_git(monkeypatch):
    """git 체크아웃이 아니면 실행하지 않고 이유를 한국어로 알려준다."""
    monkeypatch.setattr(updater, "git_available", lambda: False)
    ok, msg = updater.start_update()
    assert ok is False
    assert "자동 업데이트" in msg


def test_start_update_rejects_concurrent_run(monkeypatch):
    """이미 진행 중이면 두 번째 실행을 막는다(중복 업데이트 방지)."""
    monkeypatch.setattr(updater, "git_available", lambda: True)
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    # 살아있는 프로세스로 위장(현재 테스트 프로세스 pid).
    import os as _os

    updater.STATE_FILE.write_text(
        json.dumps(
            {"state": "running", "started_at": 9e9, "pid": _os.getpid()}
        ),
        encoding="utf-8",
    )
    ok, msg = updater.start_update()
    assert ok is False
    assert "진행 중" in msg


# --- 상태 복구 --------------------------------------------------------------
def test_state_resolves_when_process_gone(monkeypatch):
    """업데이트가 API 를 재시작하면 실행 프로세스는 사라진다.

    그때 'running' 에 영원히 머무르면 화면이 멈춘 것처럼 보이므로, 로그 끝으로
    성패를 확정해야 한다.
    """
    import time

    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LOG_FILE.write_text("...\n업데이트 완료\n", encoding="utf-8")
    updater.STATE_FILE.write_text(
        json.dumps(
            {"state": "running", "started_at": time.time(), "pid": 999_999}
        ),
        encoding="utf-8",
    )
    st = updater.get_state()
    assert st.state == "success"
    # 확정된 결과가 파일에도 남아 재조회 시 흔들리지 않는다.
    assert updater.get_state().state == "success"


def test_state_marks_failure_when_log_has_no_success(monkeypatch):
    """성공 표시가 없으면 실패로 확정한다(조용한 실패 방지)."""
    import time

    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LOG_FILE.write_text("빌드 실패\n", encoding="utf-8")
    updater.STATE_FILE.write_text(
        json.dumps(
            {"state": "running", "started_at": time.time(), "pid": 999_999}
        ),
        encoding="utf-8",
    )
    assert updater.get_state().state == "failed"


def test_log_tail_is_bounded():
    """로그가 길어도 화면에 넘겨주는 줄 수는 제한된다(응답 폭주 방지)."""
    updater.STATE_DIR.mkdir(parents=True, exist_ok=True)
    updater.LOG_FILE.write_text("\n".join(str(i) for i in range(500)), "utf-8")
    assert len(updater.get_state().log_tail) <= 40
