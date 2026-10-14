/**
 * MES 연계현황 — 수신 상태, 지연 경고, "지금 가져오기".
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Role } from "@aivis/shared-types";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { intakeStatus, qualityRecord } from "@/test/mesFixtures";

const fetchIntakeStatus = vi.fn();
const fetchQuality = vi.fn();
const pollIntake = vi.fn();
vi.mock("@/api/mes", () => ({
  MES_UNREACHABLE: "MES 서버에 연결할 수 없습니다",
  fetchIntakeStatus: (...a: unknown[]) => fetchIntakeStatus(...a),
  fetchQuality: (...a: unknown[]) => fetchQuality(...a),
  pollIntake: (...a: unknown[]) => pollIntake(...a),
}));

import { MesLinkPage, pollSummary } from "./MesLinkPage";

function login(role: Role): void {
  useAuthStore.getState().setAuth({ token: "t.t.t", username: "kim", role });
}

beforeEach(() => {
  fetchIntakeStatus.mockReset().mockResolvedValue(intakeStatus());
  fetchQuality.mockReset().mockResolvedValue([
    qualityRecord(),
    qualityRecord({
      id: 902, inspection_stage: "CRATE_COUNT", tube_index: 0, final_verdict: "NG",
      defect_codes: ["COUNT"], count_detected: 18, count_expected: 20, manual_verdict: "OK",
      source: "rest",
    }),
  ]);
  pollIntake.mockReset().mockResolvedValue({
    scanned: 12, stored: 10, updated: 1, duplicate: 1, marked: 12, error: null,
  });
  login(Role.QUALITY);
});

describe("MesLinkPage", () => {
  it("연계 방식·폴러·수신 건수·최근 수신·품질이력을 보여 준다", async () => {
    renderApp(<MesLinkPage />);
    expect(await screen.findByTestId("link-mode")).toHaveTextContent("테이블 연계 켜짐");
    expect(screen.getByTestId("link-poller")).toHaveTextContent("동작 중");
    expect(screen.getByTestId("link-records")).toHaveTextContent("88");
    expect(screen.getByTestId("link-records")).toHaveTextContent("1,520");
    expect(screen.getByTestId("pending-count")).toHaveTextContent("0");
    expect(screen.queryByTestId("pending-warn")).not.toBeInTheDocument();
    expect(screen.getByText(/MES 수신 확인/)).toBeInTheDocument();

    const runs = screen.getByTestId("runs-table");
    expect(within(runs).getByText("테이블")).toBeInTheDocument();
    expect(within(runs).getByText("timeout")).toBeInTheDocument();

    const row = await screen.findByTestId("quality-row-901");
    expect(within(row).getByText("길이 검사")).toBeInTheDocument();
    expect(within(row).getByText("#3")).toBeInTheDocument(); // tube_index 0-base → #3
    expect(within(row).getByText("길이")).toBeInTheDocument();
    const crate = screen.getByTestId("quality-row-902");
    expect(within(crate).getByText("18/20개")).toBeInTheDocument();
    expect(within(crate).getByText("REST")).toBeInTheDocument();
    expect(fetchQuality).toHaveBeenCalledWith({ limit: 50 });
  });

  it("안 가져간 건이 있는데 마지막 폴링이 주기 2배보다 오래되면 경고", async () => {
    fetchIntakeStatus.mockResolvedValue(
      intakeStatus({
        interface_pending: 12,
        poll_interval_s: 10,
        last_poll_at: new Date(Date.now() - 60_000).toISOString(),
      }),
    );
    renderApp(<MesLinkPage />);
    expect(await screen.findByTestId("pending-warn")).toHaveTextContent("20초");
    expect(screen.getByTestId("pending-count")).toHaveTextContent("⚠ 12");
  });

  it("쌓여 있어도 방금 가져갔으면 경고하지 않는다", async () => {
    fetchIntakeStatus.mockResolvedValue(
      intakeStatus({ interface_pending: 12, last_poll_at: new Date().toISOString() }),
    );
    renderApp(<MesLinkPage />);
    expect(await screen.findByTestId("pending-count")).toHaveTextContent("12");
    expect(screen.queryByTestId("pending-warn")).not.toBeInTheDocument();
  });

  it("'지금 가져오기' → POST /intake/poll, 결과 요약 표시", async () => {
    const user = userEvent.setup();
    renderApp(<MesLinkPage />);
    await screen.findByTestId("link-mode");
    await user.click(screen.getByTestId("poll-now"));
    await waitFor(() => expect(pollIntake).toHaveBeenCalledTimes(1));
    const result = await screen.findByTestId("poll-result");
    expect(result).toHaveTextContent("가져오기 완료");
    expect(result).toHaveTextContent("조회 12 · 신규 10 · 갱신 1 · 중복 1 · 수신 표시 12");
    // 결과 반영을 위해 상태를 다시 읽는다.
    await waitFor(() => expect(fetchIntakeStatus.mock.calls.length).toBeGreaterThan(1));
  });

  it("가져오기 실패(409 등)는 서버 문구 그대로", async () => {
    pollIntake.mockRejectedValue(new Error("테이블 연계가 꺼져 있다"));
    const user = userEvent.setup();
    renderApp(<MesLinkPage />);
    await screen.findByTestId("link-mode");
    await user.click(screen.getByTestId("poll-now"));
    expect(await screen.findByTestId("poll-result")).toHaveTextContent(
      "가져오기 실패: 테이블 연계가 꺼져 있다",
    );
  });

  it("테이블 연계가 꺼져 있으면 이유를 적고 가져오기 버튼을 막는다", async () => {
    fetchIntakeStatus.mockResolvedValue(
      intakeStatus({
        table_enabled: false, poller_running: false, last_poll_at: null,
        last_result: null, interface_pending: null,
      }),
    );
    renderApp(<MesLinkPage />);
    const mode = await screen.findByTestId("link-mode");
    expect(mode).toHaveTextContent("테이블 연계 꺼짐");
    expect(mode).toHaveTextContent("REST 수신만 받는다(MES_AIVIS_DB_URL 미설정)");
    expect(screen.getByTestId("poll-now")).toBeDisabled();
    expect(screen.queryByTestId("pending-warn")).not.toBeInTheDocument();
  });

  it("인터페이스 테이블 오류는 오류 상자", async () => {
    fetchIntakeStatus.mockResolvedValue(
      intakeStatus({ interface_error: "OperationalError: connection refused", interface_pending: null }),
    );
    renderApp(<MesLinkPage />);
    expect(await screen.findByTestId("interface-error")).toHaveTextContent("connection refused");
  });

  it("MES 서버가 없으면 오류 상자, 품질이력이 없으면 빈 상태", async () => {
    fetchIntakeStatus.mockRejectedValue(new Error("MES 서버에 연결할 수 없습니다"));
    fetchQuality.mockResolvedValue([]);
    renderApp(<MesLinkPage />);
    expect(await screen.findByTestId("link-error")).toHaveTextContent(
      "연계 상태 조회 실패: MES 서버에 연결할 수 없습니다",
    );
    expect(await screen.findByTestId("quality-empty")).toBeInTheDocument();
  });

  it("작업자에게는 가져오기 버튼이 없다", async () => {
    login(Role.OPERATOR);
    renderApp(<MesLinkPage />);
    await screen.findByTestId("link-mode");
    expect(screen.queryByTestId("poll-now")).not.toBeInTheDocument();
  });
});

describe("pollSummary", () => {
  it("다섯 숫자를 한 줄로", () => {
    expect(
      pollSummary({ scanned: 1234, stored: 1, updated: 2, duplicate: 3, marked: 4, error: null }),
    ).toBe("조회 1,234 · 신규 1 · 갱신 2 · 중복 3 · 수신 표시 4");
  });
});
