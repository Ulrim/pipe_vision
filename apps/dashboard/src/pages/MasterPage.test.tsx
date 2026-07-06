import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ItemMaster } from "@aivis/shared-types";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";

// 엔드포인트 모킹(네트워크 차단).
const fetchItems = vi.fn();
const updateItem = vi.fn();
const calibrateItem = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchItems: (...a: unknown[]) => fetchItems(...a),
  updateItem: (...a: unknown[]) => updateItem(...a),
  calibrateItem: (...a: unknown[]) => calibrateItem(...a),
}));

import { MasterPage } from "./MasterPage";

const item: ItemMaster = {
  item_code: "HP12",
  item_name: "헤더파이프12",
  ref_length_mm: 250,
  tol_plus_mm: 0.5,
  tol_minus_mm: 0.5,
  px_to_mm_scale: 0.25,
  oil_threshold: 0.8,
  discolor_threshold: 0.8,
  scratch_threshold: 0.8,
  capture_recipe: null,
  expected_count: 4,
  outer_diameter_mm: 12.7,
  version: 3,
};

beforeEach(() => {
  fetchItems.mockReset();
  updateItem.mockReset();
  calibrateItem.mockReset();
  useAuthStore.getState().setAuth({
    token: "t.t.t",
    username: "kim",
    role: Role.QUALITY,
  });
});

describe("MasterPage — 개수/외경 필드", () => {
  it("목록에 개수·외경을 표시", async () => {
    fetchItems.mockResolvedValue([item]);
    renderApp(<MasterPage />);
    expect(await screen.findByTestId("count-HP12")).toHaveTextContent("4");
    expect(screen.getByTestId("od-HP12")).toHaveTextContent("12.7");
  });

  it("편집 폼에 개수·외경이 채워지고 update 전송에 포함", async () => {
    fetchItems.mockResolvedValue([item]);
    updateItem.mockResolvedValue({ ...item, version: 4 });
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    const count = screen.getByTestId("edit-expected_count") as HTMLInputElement;
    const od = screen.getByTestId("edit-outer_diameter_mm") as HTMLInputElement;
    expect(count.value).toBe("4");
    expect(od.value).toBe("12.7");

    await userEvent.clear(count);
    await userEvent.type(count, "2");
    await userEvent.clear(od);
    await userEvent.type(od, "15.5");
    await userEvent.click(screen.getByTestId("edit-save"));

    await waitFor(() => {
      expect(updateItem).toHaveBeenCalledWith(
        "HP12",
        expect.objectContaining({ expected_count: 2, outer_diameter_mm: 15.5 }),
      );
    });
  });

  it("외경을 비우면 null 로 전송", async () => {
    fetchItems.mockResolvedValue([item]);
    updateItem.mockResolvedValue({ ...item, version: 4 });
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    await userEvent.clear(screen.getByTestId("edit-outer_diameter_mm"));
    await userEvent.click(screen.getByTestId("edit-save"));

    await waitFor(() => {
      expect(updateItem).toHaveBeenCalledWith(
        "HP12",
        expect.objectContaining({ outer_diameter_mm: null }),
      );
    });
  });

  it("개수가 0/비정수면 저장 비활성 + 에러 표시", async () => {
    fetchItems.mockResolvedValue([item]);
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    const count = screen.getByTestId("edit-expected_count");
    await userEvent.clear(count);
    await userEvent.type(count, "0");

    expect(screen.getByTestId("count-err")).toBeInTheDocument();
    expect(screen.getByTestId("edit-save")).toBeDisabled();
    expect(updateItem).not.toHaveBeenCalled();
  });
});

describe("MasterPage — 웹 캘리브레이션", () => {
  it("measured/actual 로 calibrate 호출 후 결과 반영", async () => {
    fetchItems.mockResolvedValue([item]);
    calibrateItem.mockResolvedValue({ ...item, px_to_mm_scale: 0.2517, version: 4 });
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    const section = screen.getByTestId("calib-section");
    await userEvent.type(within(section).getByTestId("calib-measured"), "100");
    await userEvent.type(within(section).getByTestId("calib-actual"), "100.68");

    // 미리보기: 현재계수 × (실제/측정)
    expect(within(section).getByTestId("calib-preview")).toBeInTheDocument();

    await userEvent.click(within(section).getByTestId("calib-submit"));

    await waitFor(() => {
      expect(calibrateItem).toHaveBeenCalledWith("HP12", {
        measured_mm: 100,
        actual_mm: 100.68,
      });
    });
    // 결과 배너 + px→mm 폼 필드 갱신.
    expect(await screen.findByTestId("calib-result")).toHaveTextContent("0.2517");
    await waitFor(() => {
      expect(
        (screen.getByTestId("edit-px_to_mm_scale") as HTMLInputElement).value,
      ).toBe("0.2517");
    });
  });

  it("입력이 비었거나 <=0 이면 보정 버튼 비활성", async () => {
    fetchItems.mockResolvedValue([item]);
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    const section = screen.getByTestId("calib-section");
    expect(within(section).getByTestId("calib-submit")).toBeDisabled();

    await userEvent.type(within(section).getByTestId("calib-measured"), "0");
    await userEvent.type(within(section).getByTestId("calib-actual"), "50");
    expect(within(section).getByTestId("calib-submit")).toBeDisabled();
    expect(calibrateItem).not.toHaveBeenCalled();
  });

  it("보정 실패 시 에러 표시", async () => {
    fetchItems.mockResolvedValue([item]);
    calibrateItem.mockRejectedValue(new Error("scale out of range"));
    renderApp(<MasterPage />);
    await userEvent.click(await screen.findByTestId("edit-HP12"));

    const section = screen.getByTestId("calib-section");
    await userEvent.type(within(section).getByTestId("calib-measured"), "100");
    await userEvent.type(within(section).getByTestId("calib-actual"), "80");
    await userEvent.click(within(section).getByTestId("calib-submit"));

    expect(await screen.findByTestId("calib-error")).toHaveTextContent(
      "scale out of range",
    );
  });
});
