"""발주 기반 오더 전환 + 품목별 배치 방향(모양) — 워커 측 (M13 확장).

발주마다 품목(모양/외경/개수)·절단 길이가 다르다. 웹에서 PUT /master/active
로 오더를 설정하면 워커가 핫리로드 주기에 GET /master/active 를 폴링해
재시작 없이 품목/LOT/작업지시를 전환한다. 검증:
- active=null: 현행(env) 동작 그대로 — 회귀 없음.
- 오더 설정: 품목 전환 + 이후 적재 결과의 item_code/lot/work_order 반영.
- 품목 조회 실패: 전환 보류(원자성 — 옛 품목에 새 LOT 오염 금지), 루프 생존.
- capture_recipe.orientation → inspect_batch axis 전달(모양 대응).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

_SERVICES_DIR = Path(__file__).resolve().parents[2]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_worker import FakeBackend, _cfg, _client  # noqa: E402

from vision.worker.runner import Worker  # noqa: E402


def _item_json(code: str, *, scale: float = 0.25, expected: int = 1,
               recipe: dict | None = None) -> dict:
    return {
        "item_code": code,
        "item_name": f"Header Pipe {code}",
        "ref_length_mm": 125.0,
        "tol_plus_mm": 3.0,
        "tol_minus_mm": 3.0,
        "px_to_mm_scale": scale,
        "oil_threshold": 0.30,
        "discolor_threshold": 0.20,
        "scratch_threshold": 0.15,
        "capture_recipe": recipe,
        "expected_count": expected,
        "version": 1,
    }


class OrderBackend(FakeBackend):
    """FakeBackend + /master/active + 다품목 /master/items/{code}.

    active: None(미설정 → 200 "null") | dict. fail_item_codes 로 특정 품목의
    기준정보 조회를 503 으로 만들어 전환 보류(원자성)를 검증한다.
    """

    def __init__(self, items: dict[str, dict], **kw) -> None:
        super().__init__(**kw)
        self.items = items
        self.active: dict | None = None
        self.fail_item_codes: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("Authorization", "")
        if path == "/master/active":
            if self.master_requires_auth and auth != "Bearer JWT-OP-TOKEN":
                return httpx.Response(401, json={"detail": "인증 토큰 없음"})
            if self.active is None:
                # 미설정: 200 + JSON null (백엔드 계약).
                return httpx.Response(
                    200, content=b"null",
                    headers={"Content-Type": "application/json"},
                )
            return httpx.Response(200, json=self.active)
        for code, payload in self.items.items():
            if path == f"/master/items/{code}":
                if self.master_requires_auth and auth != "Bearer JWT-OP-TOKEN":
                    return httpx.Response(401, json={"detail": "인증 토큰 없음"})
                if code in self.fail_item_codes:
                    return httpx.Response(503, json={"detail": "db down"})
                return httpx.Response(200, json=payload)
        return super().handler(request)


def _backend(**hp20_over) -> OrderBackend:
    return OrderBackend(
        items={
            "HP12": _item_json("HP12"),
            "HP20": _item_json("HP20", scale=0.50, **hp20_over),
        },
        master_requires_auth=True,
    )


def _open_reload_window(worker: Worker) -> None:
    """리로드 주기를 강제로 연다(마지막 리로드를 과거로)."""
    worker._last_item_reload = datetime(2000, 1, 1, tzinfo=timezone.utc)


def test_active_null_keeps_env_behavior(tmp_path):
    """오더 미설정(null): env 품목/LOT 그대로 — 하위 호환 회귀."""
    backend = _backend()
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True

    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))
    assert worker.item.item_code == "HP12"

    assert worker.run_once() is True
    posted = backend.posted[-1]
    assert posted["item_code"] == "HP12"
    assert posted["lot"] == "LOTTEST"  # _cfg 기본 lot(env 동작).
    assert posted.get("work_order") in (None, "")
    worker.shutdown()


def test_order_switch_applies_item_lot_work_order(tmp_path):
    """오더 설정 → 품목/LOT/작업지시가 결과·하트비트에 반영(재시작 없이)."""
    backend = _backend()
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True

    backend.active = {"item_code": "HP20", "lot": "LOT-X7", "work_order": "WO-9"}
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))

    assert worker.item.item_code == "HP20"
    assert float(worker.item.px_to_mm_scale) == 0.50  # 전환 품목의 기준정보.

    assert worker.run_once() is True
    posted = backend.posted[-1]
    assert posted["item_code"] == "HP20"
    assert posted["lot"] == "LOT-X7"
    assert posted["work_order"] == "WO-9"
    # 하트비트도 전환 품목을 알린다(현장 확인용).
    assert backend.statuses[-1]["item_code"] == "HP20"
    worker.shutdown()


def test_same_item_new_lot_updates_labels_only(tmp_path):
    """같은 품목의 새 LOT(연속 발주): 품목 유지 + 라벨만 갱신."""
    backend = _backend()
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True

    backend.active = {"item_code": "HP12", "lot": "LOT-NEW", "work_order": None}
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))

    assert worker.item.item_code == "HP12"
    worker.run_once()
    assert backend.posted[-1]["lot"] == "LOT-NEW"
    worker.shutdown()


def test_switch_atomic_on_item_fetch_failure(tmp_path):
    """전환 원자성: 품목 조회 실패 시 LOT 도 바꾸지 않는다(라벨 오염 방지).

    일시 장애로 새 품목 기준정보를 못 받으면, 옛 품목에 새 LOT 이 찍히는
    혼합 라벨이 생기면 안 된다 — 전환 전체를 보류하고 다음 주기 재시도.
    """
    backend = _backend()
    backend.fail_item_codes = {"HP20"}
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True

    backend.active = {"item_code": "HP20", "lot": "LOT-X7", "work_order": "WO-9"}
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))

    assert worker.item.item_code == "HP12"          # 전환 보류.
    assert worker.run_once() is True                # 루프 생존.
    posted = backend.posted[-1]
    assert posted["item_code"] == "HP12"
    assert posted["lot"] == "LOTTEST"               # 옛 LOT 유지(오염 없음).

    # 장애 해소 → 다음 주기에 전환 완료.
    backend.fail_item_codes = set()
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))
    assert worker.item.item_code == "HP20"
    worker.shutdown()


def test_orientation_passed_to_inspect_batch(tmp_path, monkeypatch):
    """capture_recipe.orientation=vertical → inspect_batch(axis=vertical)."""
    import vision.worker.runner as runner_mod

    backend = _backend(
        expected=3, recipe={"orientation": "vertical"},
    )
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True

    backend.active = {"item_code": "HP20", "lot": "L", "work_order": None}
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))
    assert worker.item.item_code == "HP20"

    seen: dict = {}
    orig = runner_mod.inspect_batch

    def spy(frame, item, **kw):
        seen.update(kw)
        return orig(frame, item, **kw)

    monkeypatch.setattr(runner_mod, "inspect_batch", spy)
    worker.run_once()
    assert seen.get("axis") == "vertical"
    worker.shutdown()


def test_orientation_invalid_falls_back_horizontal(tmp_path):
    """orientation 무효값은 경고 후 horizontal(안전 기본값)."""
    backend = _backend(recipe={"orientation": "diagonal"})
    worker = Worker(_cfg(tmp_path, item_reload_s=1.0), client=_client(backend))
    assert worker.startup() is True
    backend.active = {"item_code": "HP20", "lot": "L", "work_order": None}
    _open_reload_window(worker)
    worker._maybe_reload_item(datetime.now(timezone.utc))
    assert worker._orientation() == "horizontal"
    worker.shutdown()
