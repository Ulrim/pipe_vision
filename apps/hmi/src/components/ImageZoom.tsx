/**
 * 판정 이미지 전체화면 보기.
 *
 * **왜 필요한가**: 검사 화면의 이미지는 7인치 화면의 절반(약 370x260)에 들어간다.
 * 그 크기를 팔 길이에서 보면 가는 스크래치나 옅은 변색은 분간할 수 없다 — 사실상
 * 장식이었다. 작업자가 재확인을 입력하려면 **직접 봐야** 하므로, 눌러서 화면
 * 전체로 키울 수 있어야 한다.
 *
 * 닫기는 어디를 눌러도 되게 한다. 장갑 낀 손으로 작은 X 를 노리게 하면 안 된다.
 */
import { useEffect } from "react";
import type { InspectionResult } from "@aivis/shared-types";
import { ImageView } from "./ImageView";

export interface ImageZoomProps {
  result: InspectionResult;
  onClose: () => void;
}

export function ImageZoom({ result, onClose }: ImageZoomProps) {
  // 키보드가 붙어 있는 경우(사무실에서 원격 확인)를 위해 Esc 도 받는다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/90 p-2"
      role="dialog"
      aria-modal="true"
      aria-label={`검사 ${result.id} 판정 이미지 확대`}
      onClick={onClose}
      data-testid="image-zoom"
    >
      <div className="flex flex-none items-center justify-between px-2 py-1 text-white">
        <span className="text-hmi-body font-bold tabular-nums">
          검사 #{result.id} · {result.item_code}
        </span>
        {/* 닫기 버튼은 두되, 배경 아무 데나 눌러도 닫힌다(장갑 터치). */}
        <span className="text-hmi-cap font-semibold opacity-80">
          아무 곳이나 눌러 닫기
        </span>
      </div>
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-xl bg-black">
        <ImageView
          label="판정 이미지(확대)"
          inspectionId={result.id}
          kind="result"
          fill
        />
      </div>
    </div>
  );
}
