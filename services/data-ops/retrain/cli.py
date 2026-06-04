"""재학습/임계보정 CLI (CLAUDE.md §5 M16).

사용 예(services/data-ops 에서, services/api 가 PYTHONPATH 에 있어야 함):
  python -m retrain.cli candidates --item HP12
  python -m retrain.cli build --out /data/retrain --item HP12 --copy --image-root /data
  python -m retrain.cli thresholds --item HP12 --min-samples 10

DB는 backend 의 DATABASE_URL(미설정 시 sqlite)을 그대로 사용한다.
"""
from __future__ import annotations

import argparse
import json
import sys

from db.base import SessionLocal, init_db
from retrain.review import build_retrain_manifest, extract_review_candidates
from retrain.threshold import suggest_thresholds


def _cmd_candidates(args: argparse.Namespace) -> int:
    init_db()
    db = SessionLocal()
    try:
        cands = extract_review_candidates(db, item_code=args.item)
    finally:
        db.close()
    print(json.dumps({
        "total": len(cands),
        "false_positive": sum(1 for c in cands if c.miss_kind == "system_ng_human_ok"),
        "false_negative": sum(1 for c in cands if c.miss_kind == "system_ok_human_ng"),
        "items": [c.as_dict() for c in cands],
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    init_db()
    db = SessionLocal()
    try:
        manifest = build_retrain_manifest(
            db, args.out, item_code=args.item,
            copy=args.copy, image_root=args.image_root,
        )
    finally:
        db.close()
    summary = {k: manifest[k] for k in
               ("total", "by_kind", "false_positive", "false_negative", "manifest_path")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _cmd_thresholds(args: argparse.Namespace) -> int:
    init_db()
    db = SessionLocal()
    try:
        sugg = suggest_thresholds(db, args.item, min_samples=args.min_samples)
    finally:
        db.close()
    print(json.dumps([s.as_dict() for s in sugg], ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="retrain.cli", description="AIVIS 재학습/임계보정")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("candidates", help="오검·미검 후보 추출")
    c.add_argument("--item", default=None, help="품목 코드 필터")

    b = sub.add_parser("build", help="재학습 데이터셋 매니페스트 빌드")
    b.add_argument("--out", required=True, help="출력 디렉터리")
    b.add_argument("--item", default=None, help="품목 코드 필터")
    b.add_argument("--copy", action="store_true", help="review 이미지 복사")
    b.add_argument("--image-root", default=None, help="상대 이미지 경로 기준 루트")

    t = sub.add_parser("thresholds", help="임계값 보정 제안")
    t.add_argument("--item", required=True, help="품목 코드")
    t.add_argument("--min-samples", type=int, default=10, help="최소 표본 수")

    args = p.parse_args(argv)
    if args.command == "candidates":
        return _cmd_candidates(args)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "thresholds":
        return _cmd_thresholds(args)
    p.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
