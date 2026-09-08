/**
 * 정답 라벨링 화면 (부록 A.2/A.5, M16).
 *
 * 여기서 지키려는 것은 정확도만이 아니라 **속도**다. 검수자가 수백 장을 넘겨야
 * 하므로 단축키와 "저장 후 자동 다음"이 동작하지 않으면 이 화면은 쓰이지 않고,
 * 쓰이지 않으면 정답셋이 안 생겨 표면 모델은 계속 막힌다.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Role } from "@aivis/shared-types";

const fetchLabelQueue = vi.fn();
const fetchLabelProgress = vi.fn();
const putLabel = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchLabelQueue: (...a: unknown[]) => fetchLabelQueue(...a),
  fetchLabelProgress: (...a: unknown[]) => fetchLabelProgress(...a),
  putLabel: (...a: unknown[]) => putLabel(...a),
}));

// 이미지 바이트는 이 테스트의 관심사가 아니다(별도 훅 테스트가 있다).
vi.mock("@/hooks/useAuthedImage", () => ({
  useAuthedImage: () => ({ url: "blob:x", loading: false, status: 200, error: false, blob: null }),
}));

import { LabelingPage } from "./LabelingPage";
import { useAuthStore } from "@/store/auth";

const QUEUE = [
  {
    inspection_id: 11, lot: "L1", item_code: "HP12",
    inspected_at: "2026-03-02T09:00:00Z", final_verdict: "OK",
    defect_codes: [], review_flag: true, meas_length_mm: 125.0,
    has_result_image: true, has_raw_image: true,
  },
  {
    inspection_id: 12, lot: "L1", item_code: "HP12",
    inspected_at: "2026-03-02T09:00:01Z", final_verdict: "NG",
    defect_codes: ["SCR"], review_flag: false, meas_length_mm: 125.4,
    has_result_image: true, has_raw_image: true,
  },
];

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LabelingPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchLabelQueue.mockReset().mockResolvedValue(QUEUE);
  fetchLabelProgress.mockReset().mockResolvedValue({
    labeled_total: 3, unlabeled_total: 97, border_count: 1,
    by_class: { OK: { count: 2, target: 150 }, SCR: { count: 1, target: 50 } },
  });
  putLabel.mockReset().mockResolvedValue({});
  useAuthStore.getState().setAuth({ token: "t", username: "qa", role: Role.QUALITY });
});

describe("LabelingPage", () => {
  it("선택한 불량유형을 배열로 저장한다(복합불량)", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("label-image");

    await user.click(screen.getByTestId("label-btn-OIL"));
    await user.click(screen.getByTestId("label-btn-DIS"));
    await user.click(screen.getByTestId("label-save"));

    await waitFor(() => expect(putLabel).toHaveBeenCalled());
    expect(putLabel.mock.calls[0][0]).toBe(11);
    expect(putLabel.mock.calls[0][1].labels).toEqual(["OIL", "DIS"]);
  });

  it("정상 버튼은 라벨을 비운다(빈 배열 = OK)", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("label-image");

    await user.click(screen.getByTestId("label-btn-SCR"));
    await user.click(screen.getByTestId("label-btn-OK"));
    await user.click(screen.getByTestId("label-save"));

    await waitFor(() => expect(putLabel).toHaveBeenCalled());
    expect(putLabel.mock.calls[0][1].labels).toEqual([]);
  });

  it("단축키로 라벨링하고 Enter 로 저장한다(수백 장을 넘기려면 필수)", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("label-image");

    await user.keyboard("2");      // OIL
    await user.keyboard("b");      // 경계
    await user.keyboard("{Enter}"); // 저장

    await waitFor(() => expect(putLabel).toHaveBeenCalled());
    expect(putLabel.mock.calls[0][1]).toMatchObject({
      labels: ["OIL"],
      border: true,
    });
  });

  it("저장하면 다음 장으로 넘어가고 선택이 초기화된다", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("label-image");

    await user.click(screen.getByTestId("label-btn-SCR"));
    await user.click(screen.getByTestId("label-save"));

    await waitFor(() =>
      expect(screen.getByTestId("label-image")).toHaveTextContent("검사 #12"),
    );
    // 직전 선택이 묻어나면 다음 장의 라벨이 오염된다.
    expect(screen.getByTestId("label-btn-SCR")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("label-btn-OK")).toHaveAttribute("aria-pressed", "true");
  });

  it("시스템 판정은 기본으로 감춘다(먼저 보면 사람이 끌려간다)", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("label-image");

    expect(screen.queryByTestId("label-system")).toBeNull();
    await user.click(screen.getByTestId("label-toggle-system"));
    expect(screen.getByTestId("label-system")).toHaveTextContent("OK");
  });

  it("작업자(operator)는 저장할 수 없다", async () => {
    useAuthStore.getState().setAuth({ token: "t", username: "op", role: Role.OPERATOR });
    renderPage();
    await screen.findByTestId("label-image");
    expect(screen.getByTestId("label-save")).toBeDisabled();
  });

  it("큐가 비면 안내를 보여준다", async () => {
    fetchLabelQueue.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByTestId("label-empty")).toBeInTheDocument();
  });

  it("클래스별 진척을 목표수량과 함께 보여준다(무엇이 부족한지)", async () => {
    renderPage();
    const bar = await screen.findByTestId("label-progress");
    expect(bar).toHaveTextContent("2/150");
    expect(bar).toHaveTextContent("1/50");
  });
});
