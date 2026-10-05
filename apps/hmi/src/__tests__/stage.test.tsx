/**
 * 검사 모드 표시·NG 사유·모드 전환 (현장 요구 2026-10-05).
 *
 * "한 번에 다 하려니 NG 가 어떻게 나는지 모르겠다" → 모드가 헤더에 크게 보이고,
 * NG 면 무엇이 얼마나 벗어났는지 숫자로 적히며, 모드는 오더 설정에서 바꾼다.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { InspectionResult, ItemMaster } from "@aivis/shared-types";
import { InspectionStage, Role, Verdict } from "@aivis/shared-types";
import { ngReason, stageLabel, tallyCodes } from "@/lib/stage";
import { HmiHeader } from "@/components/HmiHeader";
import { InspectionCard } from "@/components/InspectionCard";
import { OrderSetup } from "@/components/OrderSetup";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";

const ITEM: ItemMaster = {
  item_code: "HP12",
  item_name: "헤더파이프",
  ref_length_mm: 430,
  tol_plus_mm: 0.1,
  tol_minus_mm: 0.1,
  px_to_mm_scale: 0.1,
  oil_threshold: 0.4,
  discolor_threshold: 0.3,
  scratch_threshold: 0.2,
  capture_recipe: null,
  expected_count: 20,
  outer_diameter_mm: null,
  version: 1,
  updated_by: null,
  updated_at: null,
};

function row(over: Partial<InspectionResult>): InspectionResult {
  return {
    id: 1,
    lot: "L1",
    item_code: "HP12",
    cam_id: "CAM1",
    inspected_at: "2026-10-05T00:00:00Z",
    final_verdict: Verdict.OK,
    defect_codes: [],
    ...over,
  } as InspectionResult;
}

afterEach(() => {
  vi.unstubAllGlobals();
  useLiveStore.setState({ status: null, statusAt: null, latest: null, feed: [] } as never);
});

// ---------------------------------------------------------------- 사유 문장

describe("ngReason — 무엇이 얼마나 벗어났는지", () => {
  it("양품이면 없다", () => {
    expect(ngReason(row({}), ITEM)).toBeNull();
  });

  it("길이: 편차와 허용치를 함께 적는다", () => {
    const r = row({
      final_verdict: Verdict.NG,
      defect_codes: ["LEN"] as never,
      deviation_mm: -0.18,
    });
    expect(ngReason(r, ITEM)).toBe("길이 -0.18mm (허용 −0.10)");
  });

  it("길이: 끝단을 못 찾은 경우를 구분한다", () => {
    const r = row({ final_verdict: Verdict.NG, defect_codes: ["LEN"] as never });
    expect(ngReason(r, ITEM)).toContain("끝단을 찾지 못함");
  });

  it("표면: 점수 > 기준 으로 적는다", () => {
    const r = row({
      final_verdict: Verdict.NG,
      defect_codes: ["OIL"] as never,
      oil_score: 0.62,
    });
    expect(ngReason(r, ITEM)).toBe("유분기 0.62 > 기준 0.40");
  });

  it("개수: 검출/기준/차이", () => {
    const r = row({ final_verdict: Verdict.NG, defect_codes: ["COUNT"] as never });
    expect(ngReason(r, ITEM, { detected: 18, expected: 20 })).toBe(
      "개수 18 / 기준 20 (-2)",
    );
  });

  it("코드 없는 NG 는 '판정 불가' 로 — 단계 오류다", () => {
    const r = row({ final_verdict: Verdict.NG, defect_codes: [] });
    expect(ngReason(r, ITEM)).toContain("판정 불가");
  });

  it("기준정보가 없어도 수치는 적는다", () => {
    const r = row({
      final_verdict: Verdict.NG,
      defect_codes: ["LEN"] as never,
      deviation_mm: 0.25,
    });
    expect(ngReason(r, null)).toBe("길이 +0.25mm");
  });
});

describe("stageLabel / tallyCodes", () => {
  it("세 모드 한글 라벨", () => {
    expect(stageLabel(InspectionStage.CUT_LENGTH)).toBe("길이 검사");
    expect(stageLabel(InspectionStage.POST_WASH_SURFACE)).toBe("표면 검사");
    expect(stageLabel(InspectionStage.CRATE_COUNT)).toBe("개수 확인");
    expect(stageLabel(null)).toBe("모드 미지정");
    expect(stageLabel("ODD")).toBe("ODD"); // 모르는 값은 숨기지 않는다
  });

  it("배치 불량 사유 집계 — MULTI 는 유형이 아니라 뺀다", () => {
    const rows = [
      row({ final_verdict: Verdict.NG, defect_codes: ["LEN"] as never }),
      row({ final_verdict: Verdict.NG, defect_codes: ["LEN", "SCR", "MULTI"] as never }),
      row({}),
    ];
    expect(tallyCodes(rows)).toBe("길이 2 · 스크래치 1");
    expect(tallyCodes([row({})])).toBeNull();
  });
});

// ---------------------------------------------------------------- 화면

describe("헤더 모드 배지", () => {
  it("하트비트의 stage 를 품목 앞에 크게 보여준다", () => {
    useAuthStore.setState({
      session: { token: "t", role: Role.OPERATOR, username: "op1" },
    } as never);
    useLiveStore.setState({
      conn: "open",
      status: {
        cam_id: "CAM1", item_code: "HP12", expected: 20, detected: 20, ng: 0,
        mismatch: false, proc_time_ms: 50, ts: "2026-10-05T00:00:00Z",
        error: null, stage: InspectionStage.CRATE_COUNT,
      },
      statusAt: Date.now(),
    } as never);
    render(<HmiHeader />);
    const badge = screen.getByTestId("header-stage");
    expect(badge).toHaveTextContent("개수 확인");
    expect(badge).toHaveAttribute("data-stage", "CRATE_COUNT");
  });
});

describe("InspectionCard — 모드별 표시", () => {
  it("NG 면 사유 문장이 보인다", () => {
    const r = row({
      final_verdict: Verdict.NG,
      defect_codes: ["LEN"] as never,
      deviation_mm: -0.18,
      inspection_stage: InspectionStage.CUT_LENGTH,
    });
    render(<InspectionCard result={r} item={ITEM} tolPlusMm={0.1} tolMinusMm={0.1} />);
    expect(screen.getByTestId("ng-reason")).toHaveTextContent("길이 -0.18mm");
  });

  it("개수 모드면 길이 수치 대신 검출/기준을 보여준다", () => {
    const r = row({
      final_verdict: Verdict.NG,
      defect_codes: ["COUNT"] as never,
      inspection_stage: InspectionStage.CRATE_COUNT,
    });
    render(
      <InspectionCard result={r} item={ITEM} count={{ detected: 18, expected: 20 }} />,
    );
    expect(screen.getByTestId("count-metrics")).toHaveTextContent("18");
    expect(screen.getByTestId("count-metrics")).toHaveTextContent("기준 20개");
    expect(screen.getByTestId("ng-reason")).toHaveTextContent("개수 18 / 기준 20");
    expect(screen.queryByText("편차")).toBeNull();
  });

  it("옛 행(단계 없음)은 prop 의 stage 로 보조한다", () => {
    const r = row({ final_verdict: Verdict.OK });
    render(
      <InspectionCard result={r} item={ITEM} stage={InspectionStage.CRATE_COUNT}
        count={{ detected: 20, expected: 20 }} />,
    );
    expect(screen.getByTestId("inspection-card")).toHaveAttribute("data-stage", "CRATE_COUNT");
  });
});

describe("OrderSetup — 모드 전환", () => {
  it("현재 모드가 눌린 상태로 보이고, 다른 모드를 누르면 서버에 PUT 한다", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return new Response(
          JSON.stringify({ item_code: "HP12", inspection_stage: "CRATE_COUNT" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const changed = vi.fn();
    render(
      <OrderSetup item={ITEM} onClose={() => {}}
        currentStage={InspectionStage.CUT_LENGTH} onStageChanged={changed} />,
    );
    expect(screen.getByTestId("stage-CUT_LENGTH")).toHaveAttribute("data-on", "yes");
    fireEvent.click(screen.getByTestId("stage-CRATE_COUNT"));
    await waitFor(() => expect(changed).toHaveBeenCalledWith("CRATE_COUNT"));
    const put = calls.find((c) => c.url.endsWith("/master/active/stage"));
    expect(put?.body).toEqual({ item_code: "HP12", inspection_stage: "CRATE_COUNT" });
    expect(screen.getByTestId("stage-CRATE_COUNT")).toHaveAttribute("data-on", "yes");
    expect(screen.getByTestId("stage-picker")).toHaveTextContent("워커가 15초 내 전환");
  });

  it("전환 실패는 그 자리에 이유를 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "권한 없음" }), { status: 403 })),
    );
    render(<OrderSetup item={ITEM} onClose={() => {}} currentStage={null} />);
    fireEvent.click(screen.getByTestId("stage-POST_WASH_SURFACE"));
    await waitFor(() =>
      expect(screen.getByTestId("stage-error")).toHaveTextContent("권한 없음"),
    );
  });
});
