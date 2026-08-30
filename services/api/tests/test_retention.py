"""이미지 보관 정리 테스트 (core/retention.py).

현장 파이의 SD카드가 가득 차서(57GB 중 여유 401MB) 검사 저장·업데이트가
전부 실패한 사고 때문에 만든 기능이다. 여기서 지키려는 계약은 네 가지다.
1) 기간이 지난 것만 지운다(최근 것은 남는다).
2) NG 판정 이미지는 OK 보다 오래 남는다(품질 증빙·재학습 자산).
3) review/ 는 가장 오래 남지만 무한 보관은 아니다 — 시스템이 스스로 쌓기
   때문에(경계값 자동분류) 안 지우면 그것만으로 디스크가 찬다.
4) 어떤 실패에도 예외를 밖으로 내지 않는다 — 정리가 검사를 멈추면 안 된다.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from core.retention import (
    RetentionPolicy,
    _is_ng,
    cleanup_images,
    storage_usage,
)

DAY = 86400


def _mk(path: Path, *, age_days: float = 0.0, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        t = time.time() - age_days * DAY
        os.utime(path, (t, t))
    return path


def _tree(tmp_path: Path) -> Path:
    base = tmp_path / "images"
    for sub in ("raw", "result", "review"):
        (base / sub).mkdir(parents=True)
    return base


def test_expired_only_deleted(tmp_path: Path) -> None:
    """기간이 지난 것만 지우고 최근 것은 남긴다."""
    base = _tree(tmp_path)
    old = _mk(base / "raw" / "L1_HP12_20260101000000000_OK.jpg", age_days=10)
    new = _mk(base / "raw" / "L1_HP12_20260830000000000_OK.jpg", age_days=1)

    rep = cleanup_images(base, RetentionPolicy(raw_days=3, ok_days=0, ng_days=0, min_free_mb=0))

    assert not old.exists()
    assert new.exists()
    assert rep.deleted == 1


def test_ng_kept_longer_than_ok(tmp_path: Path) -> None:
    """같은 시각이라도 NG 는 남고 OK 는 지워진다 — 불량은 품질 증빙이다."""
    base = _tree(tmp_path)
    ok = _mk(base / "result" / "L1_HP12_20260101000000000_OK.jpg", age_days=30)
    ng = _mk(base / "result" / "L1_HP12_20260101000000001_NG.jpg", age_days=30)

    cleanup_images(base, RetentionPolicy(raw_days=0, ok_days=14, ng_days=365, min_free_mb=0))

    assert not ok.exists()
    assert ng.exists()


def test_review_kept_longest_but_not_forever(tmp_path: Path) -> None:
    """재확인본은 다른 어떤 이미지보다 오래 남지만, 기한이 지나면 지워진다.

    review/ 는 작업자가 아니라 **시스템이 경계값 자동분류로 스스로 쌓는다**
    (services/vision/verdict/combine.py). 실제 현장에서 9.8GB 까지 불어나
    디스크를 채웠으므로 '절대 삭제 안 함'은 정책이 될 수 없다.
    """
    base = _tree(tmp_path)
    ancient = _mk(base / "review" / "old_NG.jpg", age_days=1000)
    recent = _mk(base / "review" / "new_NG.jpg", age_days=300)

    pol = RetentionPolicy(
        raw_days=1, ok_days=1, ng_days=1, review_days=730, min_free_mb=0
    )
    cleanup_images(base, pol)

    assert not ancient.exists()
    assert recent.exists(), "보관기간 안의 재학습 샘플까지 지우면 안 된다"


def test_zero_days_disables_that_category(tmp_path: Path) -> None:
    """보관일수 0 이하 = 그 분류는 기간 정리를 하지 않는다."""
    base = _tree(tmp_path)
    raw = _mk(base / "raw" / "L1_HP12_20200101000000000_OK.jpg", age_days=999)

    rep = cleanup_images(
        base,
        RetentionPolicy(raw_days=0, ok_days=0, ng_days=0, review_days=0, min_free_mb=0),
    )

    assert raw.exists()
    assert rep.deleted == 0


def test_emergency_deletes_oldest_first_in_bucket_order(tmp_path: Path) -> None:
    """여유가 부족하면 원본 → OK → NG → 재확인본 순으로, 오래된 것부터 지운다.

    min_free_mb 를 절대 도달할 수 없게 크게 잡아 전체 순회를 강제한다.
    재확인본(review)이 가장 마지막까지 남는 보루인지 확인한다.
    """
    base = _tree(tmp_path)
    raw = _mk(base / "raw" / "a_OK.jpg", age_days=1)
    ok = _mk(base / "result" / "b_OK.jpg", age_days=1)
    ng = _mk(base / "result" / "c_NG.jpg", age_days=1)
    review = _mk(base / "review" / "d_NG.jpg", age_days=1)

    deleted_order: list[str] = []
    import core.retention as ret

    real_unlink = Path.unlink

    def spy(self: Path, *a, **kw):  # noqa: ANN002, ANN003
        deleted_order.append(self.name)
        return real_unlink(self, *a, **kw)

    # 긴급 정리 경로만 타도록 기간 정리는 끈다.
    pol = RetentionPolicy(
        raw_days=0, ok_days=0, ng_days=0, review_days=0, min_free_mb=10**9
    )
    orig = ret.Path.unlink
    ret.Path.unlink = spy  # type: ignore[method-assign]
    try:
        rep = cleanup_images(base, pol)
    finally:
        ret.Path.unlink = orig  # type: ignore[method-assign]

    assert rep.emergency is True
    assert deleted_order == [raw.name, ok.name, ng.name, review.name]
    assert rep.deleted == 4


def test_missing_dir_is_noop(tmp_path: Path) -> None:
    """이미지 폴더가 아직 없어도 예외 없이 조용히 끝난다(설치 직후)."""
    rep = cleanup_images(tmp_path / "nope")
    assert rep.deleted == 0
    assert rep.errors == []


def test_unreadable_file_recorded_not_raised(tmp_path: Path, monkeypatch) -> None:
    """삭제 실패는 오류 목록에만 남고 예외로 번지지 않는다."""
    base = _tree(tmp_path)
    _mk(base / "raw" / "x_OK.jpg", age_days=99)

    def boom(self: Path, *a, **kw):  # noqa: ANN002, ANN003
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "unlink", boom)
    rep = cleanup_images(base, RetentionPolicy(raw_days=1, min_free_mb=0))

    assert rep.deleted == 0
    assert rep.errors and "read-only" in rep.errors[0]


def test_is_ng_suffix_parsing() -> None:
    """§6.4 파일명 규칙의 판정 접미사만 본다. 이름 중간의 NG 는 오탐이 아니다."""
    assert _is_ng("LOT1_HP12_20260830101112123_NG.jpg")
    assert _is_ng("lot_item_stamp_ng.jpg")
    assert not _is_ng("LOT1_HP12_20260830101112123_OK.jpg")
    assert not _is_ng("NG_something_OK.jpg")
    assert not _is_ng("noextension_OK")


def test_policy_from_env(monkeypatch) -> None:
    """env 로 보관 정책을 바꿀 수 있고, 잘못된 값은 기본값으로 되돌아간다."""
    monkeypatch.setenv("AIVIS_RETAIN_RAW_DAYS", "1")
    monkeypatch.setenv("AIVIS_RETAIN_OK_DAYS", "7")
    monkeypatch.setenv("AIVIS_RETAIN_NG_DAYS", "abc")  # 오타 → 기본값
    monkeypatch.setenv("AIVIS_RETAIN_REVIEW_DAYS", "90")
    monkeypatch.setenv("AIVIS_DISK_MIN_FREE_MB", "2500")

    pol = RetentionPolicy.from_env()
    default_ng = RetentionPolicy().ng_days

    assert (pol.raw_days, pol.ok_days, pol.review_days, pol.min_free_mb) == (
        1,
        7,
        90,
        2500,
    )
    assert pol.ng_days == default_ng


def test_storage_usage_counts_all_buckets(tmp_path: Path) -> None:
    """사용량은 raw/result/review 를 모두 합산한다(모니터 표시용)."""
    base = _tree(tmp_path)
    _mk(base / "raw" / "a_OK.jpg", size=1024 * 1024)
    _mk(base / "result" / "b_NG.jpg", size=1024 * 1024)
    _mk(base / "review" / "c_NG.jpg", size=1024 * 1024)

    usage = storage_usage(base)

    assert usage["files"] == 3
    assert usage["images_mb"] == 3.0
    assert usage["free_mb"] is not None


def test_storage_usage_missing_dir(tmp_path: Path) -> None:
    usage = storage_usage(tmp_path / "nope")
    assert usage == {"images_mb": None, "files": None, "free_mb": None}
