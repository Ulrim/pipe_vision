/**
 * MES 기준정보 — 품목·BOM / 공정·라우팅 / 설비. BOM·라우팅은 PUT 으로 통째 저장.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { PROCESSES, bomLine, equipment, mesItem, routing } from "@/test/mesPlantFixtures";

const api = vi.hoisted(() => ({
  fetchMesItems: vi.fn(),
  createMesItem: vi.fn(),
  patchMesItem: vi.fn(),
  fetchBom: vi.fn(),
  putBom: vi.fn(),
  fetchProcesses: vi.fn(),
  createProcess: vi.fn(),
  patchProcess: vi.fn(),
  fetchRouting: vi.fn(),
  putRouting: vi.fn(),
  fetchEquipment: vi.fn(),
  createEquipment: vi.fn(),
  patchEquipment: vi.fn(),
}));
vi.mock("@/api/mesPlant", () => ({ ROUTING_DEFAULT: "*", ...api }));
vi.mock("@/api/mes", () => ({ MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다" }));

import { MesMasterPage } from "./MesMasterPage";

const ITEMS = [
  mesItem(),
  mesItem({ item_code: "AL-TUBE", name: "알루미늄 튜브", item_type: "RAW", unit: "M", safety_stock: 100 }),
  mesItem({ item_code: "BOX", name: "포장 박스", item_type: "SUB", active: false }),
];

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.fetchMesItems.mockResolvedValue(ITEMS);
  api.createMesItem.mockImplementation((b: { item_code: string }) => Promise.resolve(mesItem(b)));
  api.patchMesItem.mockImplementation((code: string, b: object) =>
    Promise.resolve(mesItem({ item_code: code, ...b })),
  );
  api.fetchBom.mockResolvedValue([bomLine()]);
  api.putBom.mockResolvedValue([]);
  api.fetchProcesses.mockResolvedValue(PROCESSES);
  api.createProcess.mockImplementation((b: object) => Promise.resolve({ active: true, ...b }));
  api.patchProcess.mockImplementation((code: string, b: { active: boolean }) =>
    Promise.resolve({ ...PROCESSES.find((p) => p.code === code), ...b }),
  );
  api.fetchRouting.mockImplementation((code: string) =>
    Promise.resolve(routing({ item_code: code, source: code === "*" ? "processes" : "default" })),
  );
  api.putRouting.mockImplementation((code: string) =>
    Promise.resolve(routing({ item_code: code, source: "item" })),
  );
  api.fetchEquipment.mockResolvedValue([equipment(), equipment({ code: "WASH-1", name: "세척기", process_code: "WASH" })]);
  api.createEquipment.mockImplementation((b: object) => Promise.resolve(equipment(b)));
  api.patchEquipment.mockImplementation((code: string, b: object) => Promise.resolve(equipment({ code, ...b })));
  login(Role.QUALITY);
});

describe("MesMasterPage — 품목", () => {
  it("품목 목록: 유형은 한국어, 사용 중지는 글자로도 표시", async () => {
    renderApp(<MesMasterPage />);
    const raw = await screen.findByTestId("item-row-AL-TUBE");
    expect(within(raw).getByText("원자재")).toBeInTheDocument();
    expect(within(raw).getByText("100")).toBeInTheDocument();
    expect(screen.getByTestId("item-row-BOX")).toHaveTextContent("사용 중지");
    // BOM 버튼은 제품(FG)에만.
    expect(screen.getByTestId("item-bom-HP12")).toBeInTheDocument();
    expect(screen.queryByTestId("item-bom-AL-TUBE")).not.toBeInTheDocument();
  });

  it("품목 등록 → POST 본문", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    const form = await screen.findByTestId("item-form");
    expect(within(form).getByTestId("item-submit")).toBeDisabled();
    await user.type(within(form).getByLabelText("품목 코드"), "OIL-1");
    await user.type(within(form).getByLabelText("이름"), "절삭유");
    await user.selectOptions(within(form).getByLabelText("유형"), "SUB");
    await user.clear(within(form).getByLabelText("단위"));
    await user.type(within(form).getByLabelText("단위"), "L");
    await user.clear(within(form).getByLabelText("안전재고"));
    await user.type(within(form).getByLabelText("안전재고"), "20");
    await user.click(within(form).getByTestId("item-submit"));
    await waitFor(() =>
      expect(api.createMesItem).toHaveBeenCalledWith({
        item_code: "OIL-1",
        name: "절삭유",
        item_type: "SUB",
        unit: "L",
        safety_stock: 20,
        spec: null,
      }),
    );
    expect(await screen.findByTestId("item-result")).toHaveTextContent("OIL-1");
  });

  it("품목 수정: 안전재고를 바꾸고 사용 중지 → PATCH", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    await user.click(await screen.findByTestId("item-edit-AL-TUBE"));
    const form = screen.getByTestId("item-form");
    expect(within(form).getByLabelText("품목 코드")).toBeDisabled();
    expect(within(form).getByLabelText("안전재고")).toHaveValue(100);
    await user.clear(within(form).getByLabelText("안전재고"));
    await user.type(within(form).getByLabelText("안전재고"), "150");
    await user.click(within(form).getByRole("checkbox"));
    await user.click(within(form).getByTestId("item-submit"));
    await waitFor(() =>
      expect(api.patchMesItem).toHaveBeenCalledWith("AL-TUBE", {
        name: "알루미늄 튜브",
        item_type: "RAW",
        unit: "M",
        safety_stock: 150,
        spec: null,
        active: false,
      }),
    );
  });

  it("MES 서버가 없으면 오류 상자", async () => {
    api.fetchMesItems.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    renderApp(<MesMasterPage />);
    expect(await screen.findByTestId("item-error")).toHaveTextContent(
      "품목 조회 실패: MES 서버에 연결할 수 없습니다",
    );
  });
});

describe("MesMasterPage — BOM", () => {
  it("제품을 고르면 BOM 이 나오고, 줄 추가·삭제 뒤 PUT 으로 통째 저장", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    await user.click(await screen.findByTestId("item-bom-HP12"));
    await screen.findByTestId("bom-line-0");
    expect(api.fetchBom).toHaveBeenCalledWith("HP12");
    expect(screen.getByLabelText("자재 1")).toHaveValue("AL-TUBE");
    expect(screen.getByLabelText("소요량 1")).toHaveValue(0.25);

    await user.click(screen.getByTestId("bom-add"));
    // 새 줄이 비어 있으면 저장할 수 없다.
    expect(screen.getByTestId("bom-save")).toBeDisabled();
    // 사용 중지 품목(BOX)과 자기 자신(HP12)은 선택지에 없다.
    const sel = screen.getByLabelText("자재 2");
    expect(within(sel).queryByRole("option", { name: /BOX/ })).not.toBeInTheDocument();
    expect(within(sel).queryByRole("option", { name: /HP12/ })).not.toBeInTheDocument();
    await user.click(screen.getByTestId("bom-del-1"));

    await user.clear(screen.getByLabelText("소요량 1"));
    await user.type(screen.getByLabelText("소요량 1"), "0.3");
    await user.click(screen.getByTestId("bom-save"));
    await waitFor(() =>
      expect(api.putBom).toHaveBeenCalledWith("HP12", [
        { component_code: "AL-TUBE", qty_per: 0.3, scrap_pct: 2, note: null },
      ]),
    );
    expect(await screen.findByTestId("bom-result")).toHaveTextContent("BOM 1줄을 저장");
  });

  it("줄을 모두 지우고 저장하면 빈 목록(삭제)", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    await user.click(await screen.findByTestId("item-bom-HP12"));
    await user.click(await screen.findByTestId("bom-del-0"));
    expect(screen.getByTestId("bom-empty")).toBeInTheDocument();
    await user.click(screen.getByTestId("bom-save"));
    await waitFor(() => expect(api.putBom).toHaveBeenCalledWith("HP12", []));
  });
});

describe("MesMasterPage — 공정·라우팅", () => {
  async function openRouting(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    renderApp(<MesMasterPage />);
    await user.click(screen.getByTestId("tab-routing"));
    await screen.findByTestId("proc-row-CUT");
  }

  it("공정 목록: 검사 공정 표시, 등록 → POST", async () => {
    const user = userEvent.setup();
    await openRouting(user);
    expect(screen.getByTestId("proc-row-INSP")).toHaveTextContent("검사기 집계");
    const form = screen.getByTestId("proc-form");
    await user.type(within(form).getByLabelText("공정 코드"), "PACK");
    await user.type(within(form).getByLabelText("공정 이름"), "포장");
    await user.type(within(form).getByLabelText("순서"), "50");
    await user.click(within(form).getByTestId("proc-submit"));
    await waitFor(() =>
      expect(api.createProcess).toHaveBeenCalledWith({
        code: "PACK", name: "포장", seq: 50, auto_from_inspection: false,
      }),
    );
  });

  it("공정 사용 중지 → PATCH active false", async () => {
    const user = userEvent.setup();
    await openRouting(user);
    await user.click(screen.getByTestId("proc-toggle-WASH"));
    await waitFor(() => expect(api.patchProcess).toHaveBeenCalledWith("WASH", { active: false }));
  });

  it("라우팅: 기본은 공통(*), 출처를 보여 주고 품목을 고르면 그 품목 것을 읽는다", async () => {
    const user = userEvent.setup();
    await openRouting(user);
    await screen.findByTestId("routing-step-0");
    expect(api.fetchRouting).toHaveBeenCalledWith("*");
    expect(screen.getByTestId("routing-source")).toHaveTextContent("라우팅 없음 — 사용 중인 공정 전부");

    await user.selectOptions(screen.getByTestId("routing-item"), "HP12");
    await waitFor(() => expect(api.fetchRouting).toHaveBeenCalledWith("HP12"));
    await waitFor(() =>
      expect(screen.getByTestId("routing-source")).toHaveTextContent("공통 라우팅(*) 적용 중"),
    );
  });

  it("라우팅 편집: 공정 삭제·설비 지정 뒤 PUT 으로 통째 저장", async () => {
    const user = userEvent.setup();
    await openRouting(user);
    await user.selectOptions(screen.getByTestId("routing-item"), "HP12");
    await waitFor(() => expect(screen.getByTestId("routing-source")).toHaveTextContent("공통"));
    await user.click(screen.getByTestId("routing-del-1")); // 세척 빼기
    await user.selectOptions(screen.getByLabelText("설비 1"), "SAW-1");
    await user.type(screen.getByLabelText("표준시간 1"), "3.5");
    await user.click(screen.getByTestId("routing-save"));
    await waitFor(() =>
      expect(api.putRouting).toHaveBeenCalledWith("HP12", [
        { process_code: "CUT", equipment_code: "SAW-1", std_sec_per_ea: 3.5 },
        { process_code: "INSP", equipment_code: null, std_sec_per_ea: null },
      ]),
    );
    expect(await screen.findByTestId("routing-result")).toHaveTextContent("HP12 전용 라우팅 2공정");
  });
});

describe("MesMasterPage — 설비", () => {
  it("설비 목록·등록·수정", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    await user.click(screen.getByTestId("tab-equipment"));
    const row = await screen.findByTestId("eq-row-SAW-1");
    expect(within(row).getByText("절단")).toBeInTheDocument(); // 공정 코드 → 이름

    const form = screen.getByTestId("eq-form");
    await user.type(within(form).getByLabelText("설비 코드"), "DEB-1");
    await user.type(within(form).getByLabelText("설비 이름"), "디버링기");
    await user.click(within(form).getByTestId("eq-submit"));
    await waitFor(() =>
      expect(api.createEquipment).toHaveBeenCalledWith({
        code: "DEB-1", name: "디버링기", process_code: null, note: null,
      }),
    );

    await user.click(screen.getByTestId("eq-edit-SAW-1"));
    const edit = screen.getByTestId("eq-form");
    await user.click(within(edit).getByRole("checkbox"));
    await user.click(within(edit).getByTestId("eq-submit"));
    await waitFor(() =>
      expect(api.patchEquipment).toHaveBeenCalledWith("SAW-1", {
        name: "절단기 1호", process_code: "CUT", note: null, active: false,
      }),
    );
  });
});

describe("MesMasterPage — 작업자(operator)", () => {
  beforeEach(() => login(Role.OPERATOR));

  it("조회만: 등록 폼·수정·저장 버튼이 없다", async () => {
    const user = userEvent.setup();
    renderApp(<MesMasterPage />);
    await screen.findByTestId("item-row-HP12");
    expect(screen.queryByTestId("item-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("item-edit-HP12")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("item-bom-HP12"));
    expect(await screen.findByTestId("bom-line-0")).toHaveTextContent("AL-TUBE");
    expect(screen.queryByTestId("bom-save")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("tab-routing"));
    await screen.findByTestId("routing-step-0");
    expect(screen.queryByTestId("proc-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("proc-toggle-CUT")).not.toBeInTheDocument();
    expect(screen.queryByTestId("routing-save")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("tab-equipment"));
    await screen.findByTestId("eq-row-SAW-1");
    expect(screen.queryByTestId("eq-form")).not.toBeInTheDocument();
  });
});
