"""라벨링/정답셋 CLI (부록 A.4, §1.2).

사용 예(services/data-ops 에서):
  python -m labeling.cli build --dataset /data/dataset/raw --out gt.json
  python -m labeling.cli build --dataset /data/dataset/raw --view SIDE --out gt_side.json
  python -m labeling.cli inspect --image HP12_SIDE_SCR_20260610-141233_007.jpg

AIVIS_DATASET_DIR 환경변수(부록 A.6)를 --dataset 기본값으로 사용.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from labeling.groundtruth import build_groundtruth, load_item, write_manifest


def _default_dataset() -> str | None:
    return os.getenv("AIVIS_DATASET_DIR")


def _cmd_build(args: argparse.Namespace) -> int:
    dataset = args.dataset or _default_dataset()
    if not dataset:
        print("dataset 경로 미지정(--dataset 또는 AIVIS_DATASET_DIR)", file=sys.stderr)
        return 2
    items, errors = build_groundtruth(dataset, view=args.view, strict=args.strict)
    write_manifest(items, args.out, errors=errors)
    print(json.dumps({
        "dataset": dataset,
        "count": len(items),
        "ok": sum(1 for it in items if it.is_ok),
        "ng": sum(1 for it in items if not it.is_ok),
        "errors": len(errors),
        "manifest": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    item = load_item(args.image)
    print(json.dumps(item.as_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="labeling.cli", description="AIVIS 정답셋 빌더")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="dataset 에서 정답셋 매니페스트 빌드")
    b.add_argument("--dataset", default=None, help="dataset/raw 경로")
    b.add_argument("--view", choices=["END", "SIDE"], default=None, help="구도 필터")
    b.add_argument("--out", default="groundtruth.json", help="매니페스트 출력 경로")
    b.add_argument("--strict", action="store_true", help="파싱 오류 시 중단")

    i = sub.add_parser("inspect", help="단일 이미지 정답 항목 출력")
    i.add_argument("--image", required=True, help="이미지 경로")

    args = p.parse_args(argv)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "inspect":
        return _cmd_inspect(args)
    p.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
