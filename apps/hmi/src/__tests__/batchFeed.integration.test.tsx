import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "@/App";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";
import { MockWebSocket } from "./mockWebSocket";
import { makeBatch, makeResult } from "./factories";
import { Role } from "@aivis/shared-types";

function renderApp() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

function resetStore() {
  useLiveStore.setState({
    feed: [],
    latest: null,
    conn: "connecting",
    reconnectAttempts: 0,
    consecutiveNg: 0,
    lastAlarm: null,
    consecutiveAlarmActive: false,
    soundEnabled: false,
  });
  useAuthStore.setState({
    session: { token: "test-token", role: Role.OPERATOR, username: "tester" },
    loginPromptOpen: false,
  });
}

beforeEach(() => {
  resetStore();
  MockWebSocket.reset();
  vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
  // BatchCard/ImageView 인증 이미지 요청은 빈 jpeg 로 흘려보냄.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(new Blob([]), {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      }),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App 배치 통합 (다중 튜브 → 배치 카드 1개)", () => {
  it("같은 lot+inspected_at 다중 tube_index 이벤트를 배치 카드 1개로 묶는다", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();

    const tubes = makeBatch(4, { ngIdx: [1, 3] });
    for (const t of tubes) {
      ws.triggerMessage({ event: "inspection", data: t });
    }

    await waitFor(() =>
      expect(screen.getByTestId("batch-card")).toBeInTheDocument(),
    );
    // 단일 카드는 렌더되지 않는다(중복 카드 방지).
    expect(screen.queryByTestId("inspection-card")).not.toBeInTheDocument();

    const card = screen.getByTestId("batch-card");
    expect(card).toHaveAttribute("data-total", "4");
    expect(card).toHaveAttribute("data-ng", "2");
    expect(card).toHaveAttribute("data-verdict", "NG");
    // 튜브별 표기 4개.
    expect(screen.getAllByTestId("batch-tube")).toHaveLength(4);
    // NG 개수 요약.
    expect(screen.getByTestId("batch-ng-summary")).toHaveTextContent(
      "총 4개 중 2개 NG",
    );
    // 최근 검사(피드)에도 배치 요약 행 1개.
    expect(screen.getAllByTestId("batch-feed-row")).toHaveLength(1);
  });

  it("늦게 온 튜브를 배치에 누적한다(총 개수 증가)", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();

    const tubes = makeBatch(5, { ngIdx: [0] });
    // 먼저 3개.
    for (const t of tubes.slice(0, 3)) {
      ws.triggerMessage({ event: "inspection", data: t });
    }
    await waitFor(() =>
      expect(screen.getByTestId("batch-card")).toHaveAttribute(
        "data-total",
        "3",
      ),
    );
    // 나머지 2개 늦게 도착.
    for (const t of tubes.slice(3)) {
      ws.triggerMessage({ event: "inspection", data: t });
    }
    await waitFor(() =>
      expect(screen.getByTestId("batch-card")).toHaveAttribute(
        "data-total",
        "5",
      ),
    );
    expect(screen.getAllByTestId("batch-tube")).toHaveLength(5);
  });

  it("단일 튜브(tube_index 없음)는 기존 단일 카드를 유지한다(하위호환)", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();
    ws.triggerMessage({ event: "inspection", data: makeResult({ id: 77 }) });

    await waitFor(() =>
      expect(screen.getByTestId("inspection-card")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("batch-card")).not.toBeInTheDocument();
    // 피드도 단일 행.
    expect(screen.getAllByTestId("feed-row")).toHaveLength(1);
    expect(screen.queryByTestId("batch-feed-row")).not.toBeInTheDocument();
  });

  it("배치 NG 발생 시 알람 배너가 배치 단위(N개 중 M개 NG)로 표시된다", async () => {
    renderApp();
    const ws = MockWebSocket.last();
    ws.triggerOpen();

    const tubes = makeBatch(3, { ngIdx: [1] });
    for (const t of tubes) {
      ws.triggerMessage({ event: "inspection", data: t });
    }
    // 서버 alarm 이벤트(해당 배치 LOT).
    ws.triggerMessage({
      event: "alarm",
      data: { id: tubes[1].id, lot: "LOT-BATCH", defect_codes: ["SCR"] },
    });

    await waitFor(() =>
      expect(screen.getByTestId("ng-alarm-batch")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("ng-alarm-batch")).toHaveTextContent(
      "배치 3개 중 1개 NG",
    );
  });
});
