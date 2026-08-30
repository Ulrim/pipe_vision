"""검사 이미지 보관 정리 (디스크 고갈 방지).

**왜 필요한가(실제로 터진 문제)**: 검사 1건마다 이미지를 저장하는데 지우는
규칙이 아예 없었다. 현장 파이에서 57GB 카드가 54GB 사용 / 여유 401MB 까지
몰렸고(raw 19G, result 19G, review 9.8G), 그 상태에서는 검사결과 저장·소프트웨어
업데이트·로그가 전부 실패한다.

**보관 정책 — 모든 이미지를 똑같이 취급하지 않는다**
- 원본(raw): 부피의 3분의 1을 차지하지만 판정 근거로는 **판정 이미지가 있으면
  충분**하다(오버레이에 측정선·수치가 그려져 있다). 가장 먼저, 가장 짧게.
- 판정 이미지(result): 양품(OK)은 대다수라 부피가 크지만 다시 볼 일이 드물다.
  불량(NG)은 **품질 증빙·재학습 자산**이므로 훨씬 오래 남긴다.
- review/: 경계값 자동분류(vision/verdict)로 **시스템이 스스로 쌓는다**. 가장
  가치 있는 재학습 자산이라 가장 오래 남기지만, **무한 보관은 아니다** —
  실제로 9.8GB 까지 불어나 디스크를 채운 전력이 있다.

정리 기준은 파일 수정시각(mtime)이다. 파일명에도 시각이 있지만 형식이 바뀌면
깨지므로, 더 견고한 mtime 을 쓴다. OK/NG 구분만 파일명 접미사에서 읽는다
(§6.4 규칙: {LOT}_{Item}_{stamp}_{verdict}.jpg).

기본 보관일수는 **파이(수십 GB SD카드) 기준**으로 잡았다. 저장 여력이 큰
산업용 PC 는 env 로 늘리면 된다.

DB 행은 지우지 않는다 — 검사 이력·KPI 는 그대로 남고, 이미지만 사라진다.
화면은 이미 "이미지 없음"을 정상 처리한다.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

log = logging.getLogger("aivis.api.retention")

_RAW = "raw"
_RESULT = "result"
_REVIEW = "review"


@dataclass
class RetentionPolicy:
    """보관 기간(일)과 최소 확보 용량(MB). 0 이하면 해당 정리를 하지 않는다."""

    raw_days: int = 2
    ok_days: int = 7
    ng_days: int = 180
    #: 재학습 자산이라 가장 길게. 그래도 무한은 아니다(디스크를 채운 전력).
    review_days: int = 730
    #: 이 용량 미만으로 남으면 기간과 무관하게 오래된 것부터 더 지운다.
    #: 소프트웨어 업데이트(npm 빌드)가 1.5GB 를 필요로 해서 그보다 넉넉히 잡는다.
    min_free_mb: int = 2000

    @classmethod
    def from_env(cls) -> "RetentionPolicy":
        d = cls()

        def _i(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError:
                return default

        return cls(
            raw_days=_i("AIVIS_RETAIN_RAW_DAYS", d.raw_days),
            ok_days=_i("AIVIS_RETAIN_OK_DAYS", d.ok_days),
            ng_days=_i("AIVIS_RETAIN_NG_DAYS", d.ng_days),
            review_days=_i("AIVIS_RETAIN_REVIEW_DAYS", d.review_days),
            min_free_mb=_i("AIVIS_DISK_MIN_FREE_MB", d.min_free_mb),
        )


@dataclass
class CleanupReport:
    """정리 결과(로그·모니터 표시용)."""

    deleted: int = 0
    freed_mb: float = 0.0
    free_mb_before: float = 0.0
    free_mb_after: float = 0.0
    emergency: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "deleted": self.deleted,
            "freed_mb": round(self.freed_mb, 1),
            "free_mb_before": round(self.free_mb_before, 1),
            "free_mb_after": round(self.free_mb_after, 1),
            "emergency": self.emergency,
            "errors": self.errors[:5],
        }


def _free_mb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return 0.0


def _is_ng(name: str) -> bool:
    """파일명 접미사로 불량 여부 판단(§6.4: ..._{verdict}.jpg)."""
    stem = name.rsplit(".", 1)[0]
    return stem.upper().endswith("_NG")


def _iter_files(d: Path) -> Iterable[Path]:
    if not d.is_dir():
        return []
    try:
        return [p for p in d.iterdir() if p.is_file()]
    except OSError:
        return []


def _delete(path: Path, report: CleanupReport) -> None:
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        path.unlink()
        report.deleted += 1
        report.freed_mb += size_mb
    except FileNotFoundError:
        pass
    except OSError as exc:
        report.errors.append(f"{path.name}: {exc}")


def cleanup_images(
    images_dir: str | Path, policy: RetentionPolicy | None = None
) -> CleanupReport:
    """보관 기간이 지난 이미지를 지우고, 그래도 부족하면 오래된 것부터 더 지운다.

    절대 예외를 밖으로 내보내지 않는다 — 정리 실패가 검사를 멈추면 안 된다.
    """
    pol = policy or RetentionPolicy.from_env()
    base = Path(images_dir)
    report = CleanupReport()
    if not base.is_dir():
        return report
    report.free_mb_before = _free_mb(base)
    now = time.time()

    def _expired(p: Path, days: int) -> bool:
        if days <= 0:
            return False
        try:
            return (now - p.stat().st_mtime) > days * 86400
        except OSError:
            return False

    # 1) 기간 경과분 정리.
    for p in _iter_files(base / _RAW):
        if _expired(p, pol.raw_days):
            _delete(p, report)
    for p in _iter_files(base / _RESULT):
        days = pol.ng_days if _is_ng(p.name) else pol.ok_days
        if _expired(p, days):
            _delete(p, report)
    for p in _iter_files(base / _REVIEW):
        if _expired(p, pol.review_days):
            _delete(p, report)

    # 2) 그래도 여유가 부족하면 **오래된 것부터** 추가로 지운다.
    #    순서 = 가치가 낮은 것부터: 원본 → 양품 판정 → 불량 판정 → 재확인본.
    #    재확인본(review)은 재학습 자산이라 마지막 보루지만, 디스크가 멈추는 것보다는
    #    오래된 학습 샘플을 잃는 편이 낫다.
    if pol.min_free_mb > 0 and _free_mb(base) < pol.min_free_mb:
        report.emergency = True
        log.warning(
            "디스크 여유 부족(%.0fMB < %dMB) — 오래된 이미지를 추가로 정리한다",
            _free_mb(base),
            pol.min_free_mb,
        )
        buckets = [
            _iter_files(base / _RAW),
            [p for p in _iter_files(base / _RESULT) if not _is_ng(p.name)],
            [p for p in _iter_files(base / _RESULT) if _is_ng(p.name)],
            _iter_files(base / _REVIEW),
        ]
        for bucket in buckets:
            if _free_mb(base) >= pol.min_free_mb:
                break
            items = list(bucket)
            try:
                items.sort(key=lambda p: p.stat().st_mtime)
            except OSError:
                continue
            for p in items:
                if _free_mb(base) >= pol.min_free_mb:
                    break
                _delete(p, report)

    report.free_mb_after = _free_mb(base)
    if report.deleted:
        log.info(
            "이미지 정리: %d개 삭제, %.0fMB 확보 (여유 %.0f→%.0fMB)%s",
            report.deleted,
            report.freed_mb,
            report.free_mb_before,
            report.free_mb_after,
            " [긴급]" if report.emergency else "",
        )
    return report


def storage_usage(images_dir: str | Path) -> dict:
    """이미지 저장소 사용량(모니터 표시용). 실패해도 예외를 내지 않는다."""
    base = Path(images_dir)
    out: dict = {"images_mb": None, "files": None, "free_mb": None}
    if not base.is_dir():
        return out
    total = 0
    count = 0
    try:
        for sub in (_RAW, _RESULT, _REVIEW):
            for p in _iter_files(base / sub):
                try:
                    total += p.stat().st_size
                    count += 1
                except OSError:
                    continue
    except Exception:  # noqa: BLE001
        return out
    out["images_mb"] = round(total / (1024 * 1024), 1)
    out["files"] = count
    out["free_mb"] = round(_free_mb(base), 1)
    return out
