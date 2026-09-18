"""프로그램 자체 업데이트 실행기 (현장 사용자용).

현장 담당자는 개발자가 아니다 — 터미널을 열어 `git pull` 을 치게 할 수 없다.
그래서 화면(관리자 대시보드)의 버튼 하나로 업데이트가 되게 한다. 이 모듈은
그 버튼이 부르는 백엔드 쪽 실행기다.

**설계 원칙(셸을 실행하므로 안전이 최우선)**
- 실행 대상은 **저장소에 있는 고정 스크립트**(scripts/aivis-update.sh) 하나뿐이다.
  사용자 입력을 명령줄에 끼워 넣지 않는다(임의 명령 실행 불가). 인자는 코드가
  정한 화이트리스트 플래그만 쓴다.
- 요청 스레드를 붙잡지 않는다: 업데이트는 수 분이 걸리고 도중에 API 자신이
  재시작될 수도 있다. 그래서 **분리된 프로세스**로 띄우고 진행 상황은 로그
  파일로 남긴다. 화면은 상태를 폴링한다.
- 동시에 두 번 돌지 않게 잠금(락 파일)한다.
- 업데이트가 API 를 재시작하면 이 프로세스는 사라진다 — 그래서 상태는
  메모리가 아니라 **파일**에 둔다(재시작 후에도 결과를 읽을 수 있다).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 저장소 루트: services/api/core/updater.py → 3단계 위.
REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT = REPO_ROOT / "scripts" / "aivis-update.sh"

# 상태/로그는 데이터 디렉터리에 둔다(git 밖 — 업데이트가 덮어쓰지 않는다).
STATE_DIR = Path(os.getenv("AIVIS_HOME", "/var/lib/aivis")) / "update"
LOG_FILE = STATE_DIR / "last_update.log"
STATE_FILE = STATE_DIR / "state.json"

# 이 시간(초) 넘게 진행 중이면 죽은 것으로 본다(프로세스가 사라진 경우 대비).
STALE_RUN_S = 60 * 30


@dataclass
class UpdateState:
    """업데이트 진행 상태(화면이 그대로 보여줄 수 있는 형태)."""

    state: str = "idle"  # idle | running | success | failed
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    log_tail: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "log_tail": self.log_tail,
        }


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(data: dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        # 상태를 못 남겨도 업데이트 자체는 진행돼야 한다.
        pass


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _log_tail(n: int = 40) -> list[str]:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-n:]


def git_available() -> bool:
    """이 배포가 git 체크아웃인지(업데이트 가능한 형태인지)."""
    return shutil.which("git") is not None and (REPO_ROOT / ".git").exists()


# 부팅 자동시작 유닛(이게 있어야 업데이트 후 자동 재시작이 가능하다).
SERVICE_UNIT = Path("/etc/systemd/system/aivis-standalone.service")


def restart_supported() -> bool:
    """업데이트 후 **프로그램이 스스로 재시작**할 수 있는 설치인지.

    중요: systemd 유닛이 없으면 업데이트 스크립트는 파일만 갱신하고 재시작을
    건너뛴다. 그러면 사용자는 화면에서 "완료"를 보지만 **실행 중인 프로그램은
    여전히 이전 버전**이다 — 비개발자에게는 알아챌 방법이 없는 함정이다.
    그래서 이 값을 화면에 그대로 넘겨, 재시작이 불가한 설치에서는 "적용하려면
    다시 시작해야 한다"고 미리 알리게 한다.
    """
    return shutil.which("systemctl") is not None and SERVICE_UNIT.exists()


def _git(*args: str, timeout: float = 20.0) -> tuple[int, str]:
    """저장소에서 git 명령 1회. (returncode, stdout+stderr)."""
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def current_version() -> dict[str, Any]:
    """현재 설치된 버전 정보(화면 표시용).

    사용자에게 커밋 해시는 의미가 없으므로 **날짜와 한 줄 설명**을 함께 준다.
    """
    if not git_available():
        return {"available": False, "commit": None, "date": None, "subject": None}
    rc, out = _git("log", "-1", "--format=%h%x1f%cI%x1f%s")
    if rc != 0 or "\x1f" not in out:
        return {"available": False, "commit": None, "date": None, "subject": None}
    commit, date, subject = out.split("\x1f", 2)
    rc2, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    return {
        "available": True,
        "commit": commit,
        "date": date,
        "subject": subject,
        "branch": branch if rc2 == 0 else None,
    }


def check_remote(branch: Optional[str] = None) -> dict[str, Any]:
    """원격에 새 버전이 있는지 확인(네트워크 사용, 변경은 하지 않는다).

    반환: {reachable, behind(몇 개 뒤처졌나), latest_date, latest_subject, error}
    """
    if not git_available():
        return {"reachable": False, "behind": None, "error": "git 저장소가 아닙니다"}
    target = branch or os.getenv("AIVIS_BRANCH", "claude/eloquent-gauss-O6wDP")
    rc, out = _git("fetch", "origin", target, timeout=60.0)
    if rc != 0:
        return {
            "reachable": False,
            "behind": None,
            "error": f"인터넷 연결 또는 저장소 접근 실패: {out[:200]}",
        }
    rc, count = _git("rev-list", "--count", "HEAD..FETCH_HEAD")
    behind = int(count) if rc == 0 and count.isdigit() else None
    rc2, info = _git("log", "-1", "--format=%cI%x1f%s", "FETCH_HEAD")
    date = subject = None
    if rc2 == 0 and "\x1f" in info:
        date, subject = info.split("\x1f", 1)
    return {
        "reachable": True,
        "behind": behind,
        "latest_date": date,
        "latest_subject": subject,
        "error": None,
    }


def get_state() -> UpdateState:
    """현재 업데이트 상태. 프로세스가 사라졌으면 결과로 확정한다."""
    raw = _read_state()
    st = UpdateState(
        state=raw.get("state", "idle"),
        started_at=raw.get("started_at"),
        finished_at=raw.get("finished_at"),
        pid=raw.get("pid"),
        exit_code=raw.get("exit_code"),
    )
    if st.state == "running":
        alive = _pid_alive(st.pid)
        too_old = st.started_at and (time.time() - st.started_at) > STALE_RUN_S
        if not alive or too_old:
            # 스크립트가 서비스를 재시작했거나 죽었다 — 로그 끝으로 성패 판단.
            tail = "\n".join(_log_tail(80))
            ok = "업데이트 완료" in tail or "이미 최신" in tail
            st.state = "success" if ok else "failed"
            st.finished_at = st.finished_at or time.time()
            raw.update(
                {"state": st.state, "finished_at": st.finished_at, "pid": None}
            )
            _write_state(raw)
    st.log_tail = _log_tail()
    return st


def start_update(*, restart: bool = True) -> tuple[bool, str]:
    """업데이트를 분리 프로세스로 시작. (성공여부, 안내문).

    restart=True 면 스크립트가 끝나고 서비스를 자동 재시작한다(사용자가
    터미널을 못 쓰므로 기본값). 이때 API 도 함께 재시작되어 화면 연결이
    잠시 끊긴다 — 화면은 그것을 정상으로 처리해야 한다.
    """
    if not UPDATE_SCRIPT.exists():
        return False, "업데이트 스크립트를 찾을 수 없습니다."
    if not git_available():
        return False, "이 설치본은 자동 업데이트를 지원하지 않습니다(git 저장소 아님)."

    st = get_state()
    if st.state == "running":
        return False, "이미 업데이트가 진행 중입니다."

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # 인자는 코드가 정한 것만 — 사용자 입력은 절대 섞지 않는다.
    cmd = ["bash", str(UPDATE_SCRIPT)]
    if restart:
        cmd.append("--restart")
    try:
        log = open(LOG_FILE, "w", encoding="utf-8")  # noqa: SIM115 (자식이 소유)
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # 부모(API)가 재시작돼도 살아남도록 세션 분리.
            start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"업데이트를 시작하지 못했습니다: {exc}"

    _write_state(
        {
            "state": "running",
            "started_at": time.time(),
            "finished_at": None,
            "pid": proc.pid,
            "exit_code": None,
        }
    )
    return True, "업데이트를 시작했습니다."
