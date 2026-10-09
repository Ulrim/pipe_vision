"""라벨링/정답셋 CLI (부록 A.4, §1.2).

사용 예(services/data-ops 에서):
  python -m labeling.cli build --dataset /data/dataset/raw --out gt.json
  python -m labeling.cli build --dataset /data/dataset/raw --view SIDE --out gt_side.json
  python -m labeling.cli inspect --image HP12_SIDE_SCR_20260610-141233_007.jpg
  python -m labeling.cli dataset --manifest export.json --images /var/lib/aivis/images \
      --out dataset/raw

AIVIS_DATASET_DIR 환경변수(부록 A.6)를 --dataset 기본값으로 사용.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from labeling.dataset_build import build_dataset, load_manifest
from labeling.groundtruth import build_groundtruth, load_item, write_manifest
from labeling.points import (
    PointLabel,
    PointSet,
    auto_path,
    gt_path,
    iter_images,
    save_points,
    score_dir,
)


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


def _cmd_dataset(args: argparse.Namespace) -> int:
    """대시보드 라벨(GET /labels/export)을 학습용 폴더 구조로 펼친다.

    라벨을 붙여도 학습을 돌릴 수 없으면 아무 일도 일어나지 않는다 — 이 명령이
    라벨링 화면과 train_anomaly.py 사이의 빈 칸을 메운다.
    """
    images = args.images or os.getenv("AIVIS_IMAGES_DIR")
    if not images:
        print("이미지 경로 미지정(--images 또는 AIVIS_IMAGES_DIR)", file=sys.stderr)
        return 2
    report = build_dataset(load_manifest(args.manifest), images, args.out)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def _cmd_points_propose(args: argparse.Namespace) -> int:
    """검출기로 단면 점을 먼저 찍어 사람이 고칠 초안을 만든다.

    vision 은 여기서만 필요하므로 **지연 import** 한다. 채점(points-score)은
    cv2 없이도 돌아야 하기 때문이다.
    """
    try:
        import cv2  # noqa: WPS433
        from vision.multi.bundle import count_bundle
    except ImportError as exc:  # pragma: no cover - 환경 의존
        print(f"vision/cv2 를 불러올 수 없다: {exc}", file=sys.stderr)
        return 2

    images = iter_images(args.images)
    if not images:
        print(f"이미지가 없다: {args.images}", file=sys.stderr)
        return 2
    made = seeded = kept = 0
    for path in images:
        img = cv2.imread(path)
        if img is None:
            print(f"건너뜀(열 수 없음): {path}", file=sys.stderr)
            continue
        res = count_bundle(img)
        ps = PointSet(
            image=os.path.basename(path),
            width=int(img.shape[1]),
            height=int(img.shape[0]),
            source="auto",
            detector="count_bundle",
            points=[PointLabel(x=d.cx, y=d.cy, r=d.r) for d in res.detections],
        )
        ap, gp = auto_path(path), gt_path(path)
        if os.path.exists(ap) and not args.overwrite_auto:
            kept += 1
        else:
            save_points(ap, ps)
            made += 1
        # 정답 파일은 **절대 덮어쓰지 않는다** — 사람 작업을 날리면 안 된다.
        if not os.path.exists(gp):
            seed = PointSet(**{**ps.__dict__, "source": "human"})
            save_points(gp, seed)
            seeded += 1
    print(
        json.dumps(
            {
                "images": len(images),
                "auto_written": made,
                "auto_kept": kept,
                "gt_seeded": seeded,
                "hint": "*.points.json 을 열어 틀린 점만 고치세요(추가/삭제/이동).",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_points_score(args: argparse.Namespace) -> int:
    report = score_dir(args.images, tol=args.tol)
    rows = report.pop("rows")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        report["rows"] = rows
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if report["labelled_images"] == 0:
        print("정답(.points.json)이 하나도 없다 — 먼저 points-propose 후 교정하세요.",
              file=sys.stderr)
        return 1
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

    d = sub.add_parser(
        "dataset", help="라벨 매니페스트 -> 학습용 클래스 폴더(dataset/raw/<CLASS>)"
    )
    d.add_argument("--manifest", required=True, help="GET /labels/export 저장 파일")
    d.add_argument("--images", default=None, help="AIVIS_IMAGES_DIR")
    d.add_argument("--out", default="dataset/raw", help="출력 루트")

    pp = sub.add_parser(
        "points-propose",
        help="단면 점 라벨 초안 생성(검출기가 먼저 찍고 사람이 교정)",
    )
    pp.add_argument("--images", required=True, help="이미지 폴더(하위 포함)")
    pp.add_argument(
        "--overwrite-auto",
        action="store_true",
        help="기존 .points.auto.json 재생성(정답 .points.json 은 건드리지 않는다)",
    )

    psc = sub.add_parser("points-score", help="검출기 제안 vs 사람 정답 채점")
    psc.add_argument("--images", required=True, help="이미지 폴더(하위 포함)")
    psc.add_argument("--tol", type=float, default=1.0, help="허용 반경 배수")
    psc.add_argument("--json", default=None, help="상세 리포트 저장 경로")

    args = p.parse_args(argv)
    if args.command == "points-propose":
        return _cmd_points_propose(args)
    if args.command == "points-score":
        return _cmd_points_score(args)
    if args.command == "build":
        return _cmd_build(args)
    if args.command == "inspect":
        return _cmd_inspect(args)
    if args.command == "dataset":
        return _cmd_dataset(args)
    p.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
