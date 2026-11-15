/**
 * MES 범위 확대분 클라이언트 — 경로·메서드·본문이 docs/MES.md §11.5 표와 같은지,
 * 그리고 기존 MES 클라이언트(requestMes)를 그대로 타는지(주소·토큰·연결 실패 문구).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Role } from "@aivis/shared-types";
import { MES_UNREACHABLE } from "./mes";
import {
  completeOperation,
  createAdjustment,
  createMesItem,
  createReceipt,
  fetchBom,
  fetchEquipmentEvents,
  fetchMaterials,
  fetchMesItems,
  fetchOperations,
  fetchRouting,
  fetchStock,
  fetchStockTxns,
  fetchUtilization,
  issueMaterials,
  patchMesItem,
  putBom,
  putRouting,
  setEquipmentState,
  skipOperation,
  startOperation,
} from "./mesPlant";
import { MES_BASE } from "@/lib/env";
import { useAuthStore } from "@/store/auth";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const fetchMock = vi.fn();

/** n 번째 호출의 [경로(MES_BASE 뺀 것), 메서드, 본문]. */
function call(n = 0): { path: string; method: string; body: unknown; headers: Record<string, string> } {
  const [url, init] = fetchMock.mock.calls[n] as [string, RequestInit | undefined];
  expect(url.startsWith(MES_BASE)).toBe(true);
  return {
    path: url.slice(MES_BASE.length),
    method: init?.method ?? "GET",
    body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    headers: (init?.headers ?? {}) as Record<string, string>,
  };
}

beforeEach(() => {
  fetchMock.mockReset().mockImplementation(() => Promise.resolve(json([])));
  vi.stubGlobal("fetch", fetchMock);
  useAuthStore.getState().setAuth({ token: "tok.en", username: "kim", role: Role.QUALITY });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().clear();
});

describe("mesPlant — 기존 MES 클라이언트를 그대로 쓴다", () => {
  it("MES_BASE 로 요청하고 AIVIS 와 같은 토큰을 싣는다", async () => {
    await fetchMesItems();
    const c = call();
    expect(c.path).toBe("/items");
    expect(c.headers.Authorization).toBe("Bearer tok.en");
  });

  it("MES 가 꺼져 있으면 같은 문구(MES_UNREACHABLE)", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(fetchStock()).rejects.toMatchObject({ status: 0, message: MES_UNREACHABLE });
  });

  it("409 는 상태코드와 detail 을 그대로 올린다(자재 부족)", async () => {
    fetchMock.mockResolvedValue(json({ detail: "재고 부족: AL-TUBE 27.5" }, 409));
    await expect(issueMaterials(1)).rejects.toMatchObject({
      status: 409,
      message: "재고 부족: AL-TUBE 27.5",
    });
  });
});

describe("mesPlant — 경로·메서드·본문(§11.5)", () => {
  it("품목: 목록 필터 / 등록 / 수정", async () => {
    await fetchMesItems({ item_type: "RAW", active: true });
    expect(call(0).path).toBe("/items?item_type=RAW&active=true");

    await createMesItem({ item_code: "AL-TUBE", name: "튜브", item_type: "RAW", unit: "M", safety_stock: 100 });
    expect(call(1)).toMatchObject({
      path: "/items",
      method: "POST",
      body: { item_code: "AL-TUBE", name: "튜브", item_type: "RAW", unit: "M", safety_stock: 100 },
    });

    await patchMesItem("AL/TUBE 1", { active: false });
    expect(call(2)).toMatchObject({ path: "/items/AL%2FTUBE%201", method: "PATCH", body: { active: false } });
  });

  it("BOM: parent 로 조회, PUT 은 {lines} 로 통째 교체", async () => {
    await fetchBom("HP12");
    expect(call(0).path).toBe("/bom?parent=HP12");
    await fetchBom();
    expect(call(1).path).toBe("/bom");

    await putBom("HP12", [{ component_code: "AL-TUBE", qty_per: 0.25, scrap_pct: 2 }]);
    expect(call(2)).toMatchObject({
      path: "/bom/HP12",
      method: "PUT",
      body: { lines: [{ component_code: "AL-TUBE", qty_per: 0.25, scrap_pct: 2 }] },
    });
    await putBom("HP12", []);
    expect(call(3).body).toEqual({ lines: [] });
  });

  it("라우팅: 공통은 * 그대로, PUT 은 {steps}", async () => {
    await fetchRouting("*");
    expect(call(0).path).toBe("/routings/*");
    await putRouting("HP12", [{ process_code: "CUT", equipment_code: "SAW-1", std_sec_per_ea: 3 }]);
    expect(call(1)).toMatchObject({
      path: "/routings/HP12",
      method: "PUT",
      body: { steps: [{ process_code: "CUT", equipment_code: "SAW-1", std_sec_per_ea: 3 }] },
    });
  });

  it("설비: 상태 전환 / 이력 / 가동률", async () => {
    await setEquipmentState("SAW-1", { state: "DOWN", reason: "톱날 파손" });
    expect(call(0)).toMatchObject({
      path: "/equipment/SAW-1/state",
      method: "POST",
      body: { state: "DOWN", reason: "톱날 파손" },
    });
    await fetchEquipmentEvents("SAW-1", 50);
    expect(call(1).path).toBe("/equipment/SAW-1/events?limit=50");
    await fetchUtilization("2026-11-01", "2026-11-15");
    expect(call(2).path).toBe("/equipment-utilization?date_from=2026-11-01&date_to=2026-11-15");
    await fetchUtilization();
    expect(call(3).path).toBe("/equipment-utilization");
  });

  it("공정 진행: 목록 / 시작 / 완료 / 건너뛰기", async () => {
    await fetchOperations(3);
    expect(call(0).path).toBe("/work-orders/3/operations");
    await startOperation(3, 11, { equipment_code: "SAW-1", worker: "kim" });
    expect(call(1)).toMatchObject({
      path: "/work-orders/3/operations/11/start",
      method: "POST",
      body: { equipment_code: "SAW-1", worker: "kim" },
    });
    await completeOperation(3, 11, { good_qty: 495, defect_qty: 5 });
    expect(call(2)).toMatchObject({
      path: "/work-orders/3/operations/11/complete",
      method: "POST",
      body: { good_qty: 495, defect_qty: 5 },
    });
    await skipOperation(3, 12);
    expect(call(3)).toMatchObject({ path: "/work-orders/3/operations/12/skip", method: "POST" });
    expect(call(3).body).toBeUndefined();
  });

  it("자재: 소요표 / 투입(allow_negative)", async () => {
    await fetchMaterials(3);
    expect(call(0).path).toBe("/work-orders/3/materials");
    await issueMaterials(3, { allow_negative: true });
    expect(call(1)).toMatchObject({
      path: "/work-orders/3/materials/issue",
      method: "POST",
      body: { allow_negative: true },
    });
  });

  it("재고: 현재고 / 수불 이력 / 입고 / 조정", async () => {
    await fetchStock("RAW");
    expect(call(0).path).toBe("/stock?item_type=RAW");
    await fetchStockTxns({ item_code: "AL-TUBE", wo_id: 3, limit: 200 });
    expect(call(1).path).toBe("/stock/txns?item_code=AL-TUBE&wo_id=3&limit=200");
    await createReceipt({ item_code: "AL-TUBE", qty: 200, lot: "RM-001" });
    expect(call(2)).toMatchObject({
      path: "/stock/receipts",
      method: "POST",
      body: { item_code: "AL-TUBE", qty: 200, lot: "RM-001" },
    });
    await createAdjustment({ item_code: "AL-TUBE", qty: -3.5, note: "실사 차이" });
    expect(call(3)).toMatchObject({
      path: "/stock/adjustments",
      method: "POST",
      body: { item_code: "AL-TUBE", qty: -3.5, note: "실사 차이" },
    });
  });
});
