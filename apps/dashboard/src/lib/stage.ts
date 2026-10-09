/**
 * 검사 모드(InspectionStage) 한글 표기 — 이력 필터·모니터 스테이션 목록용.
 * 라벨은 HMI(apps/hmi/src/lib/stage.ts)와 같은 말을 쓴다: 현장과 사무실이
 * 같은 단어로 통화해야 한다.
 */
import { InspectionStage } from "@aivis/shared-types";

export const STAGE_LABEL: Record<string, string> = {
  [InspectionStage.CUT_LENGTH]: "길이 검사",
  [InspectionStage.POST_WASH_SURFACE]: "표면 검사",
  [InspectionStage.CRATE_COUNT]: "개수 확인",
};

/** 필터 select 의 선택지 순서(공정 순). */
export const STAGE_OPTIONS: Array<{ value: string; label: string }> = [
  InspectionStage.CUT_LENGTH,
  InspectionStage.POST_WASH_SURFACE,
  InspectionStage.CRATE_COUNT,
].map((v) => ({ value: v, label: STAGE_LABEL[v] }));

/** 모르는 값은 원문 그대로(숨기지 않는다). 없으면 "-". */
export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "-";
  return STAGE_LABEL[stage] ?? stage;
}
