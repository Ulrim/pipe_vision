import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UpdateInfo, UpdateRemote } from "@/api/endpoints";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { Role } from "@aivis/shared-types";

// 엔드포인트 모킹(네트워크 차단) — 기존 페이지 테스트와 동일 패턴.
const fetchUpdateInfo = vi.fn();
const checkUpdate = vi.fn();
const startUpdate = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchUpdateInfo: (...a: unknown[]) => fetchUpdateInfo(...a),
  checkUpdate: (...a: unknown[]) => checkUpdate(...a),
  startUpdate: (...a: unknown[]) => startUpdate(...a),
}));

import { UpdatePage, POLL_MS, updatePhase, fmtKoreanDate } from "./UpdatePage";

/** 폴링 회귀 테스트를 빠르게 돌리기 위한 짧은 주기(운영 기본값은 POLL_MS). */
const FAST_POLL = 15;

// 날짜 픽스처는 정오 근처 UTC 로 둬 실행 시간대와 무관하게 같은 날짜로 표시된다.
const base: UpdateInfo = {
  current: {
    available: true,
    commit: "a1b2c3d",
    date: "2026-08-29T09:00:00Z",
    subject: "검사 화면 표시 개선",
    branch: "main",
    restart_supported: true,
  },
  progress: {
    state: "idle",
    started_at: null,
    finished_at: null,
    exit_code: null,
    log_tail: [],
  },
};

function infoWith(patch: {
  current?: Partial<UpdateInfo["current"]>;
  progress?: Partial<UpdateInfo["progress"]>;
}): UpdateInfo {
  return {
    current: { ...base.current, ...(patch.current ?? {}) },
    progress: { ...base.progress, ...(patch.progress ?? {}) },
  };
}

const remoteOk = (patch: Partial<UpdateRemote> = {}): UpdateRemote => ({
  reachable: true,
  behind: 0,
  latest_date: null,
  latest_subject: null,
  error: null,
  ...patch,
});

beforeEach(() => {
  useAuthStore.getState().setAuth({
    token: "t.t.t",
    username: "admin1",
    role: Role.ADMIN,
  });
  fetchUpdateInfo.mockReset().mockResolvedValue(base);
  checkUpdate.mockReset();
  startUpdate.mockReset();
});

afterEach(() => {
  useAuthStore.getState().clear();
});

describe("UpdatePage — 현재 버전(비개발자용 표기)", () => {
  it("설치 날짜와 한 줄 설명을 표시한다(커밋 해시는 보조)", async () => {
    renderApp(<UpdatePage />);
    expect(await screen.findByTestId("current-date")).toHaveTextContent(
      "2026년 8월 29일",
    );
    expect(screen.getByTestId("current-subject")).toHaveTextContent(
      "검사 화면 표시 개선",
    );
    // 해시/채널은 작은 보조 정보로만 노출.
    expect(screen.getByTestId("current-detail")).toHaveTextContent("a1b2c3d");
    expect(screen.queryByTestId("update-unavailable")).not.toBeInTheDocument();
  });

  it("available=false 면 자동 업데이트 불가 안내 + 확인 버튼 비활성", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({ current: { available: false, commit: null, branch: null } }),
    );
    renderApp(<UpdatePage />);
    expect(await screen.findByTestId("update-unavailable")).toHaveTextContent(
      "자동 업데이트를 지원하지 않습니다",
    );
    expect(screen.getByTestId("check-button")).toBeDisabled();
  });

  it("관리자가 아니면 권한 안내만 보인다(엔드포인트 호출 없음)", async () => {
    useAuthStore.getState().setAuth({
      token: "t.t.t",
      username: "op1",
      role: Role.OPERATOR,
    });
    renderApp(<UpdatePage />);
    expect(await screen.findByTestId("update-forbidden")).toHaveTextContent(
      "관리자 계정",
    );
    expect(fetchUpdateInfo).not.toHaveBeenCalled();
  });
});

describe("UpdatePage — 새 버전 확인", () => {
  it("behind=0 이면 '최신 버전입니다'(업데이트 버튼 없음)", async () => {
    checkUpdate.mockResolvedValue(remoteOk({ behind: 0 }));
    const user = userEvent.setup();
    renderApp(<UpdatePage />);
    await user.click(await screen.findByTestId("check-button"));

    expect(await screen.findByTestId("check-latest")).toHaveTextContent(
      "최신 버전입니다",
    );
    expect(screen.queryByTestId("start-button")).not.toBeInTheDocument();
  });

  it("behind=3 이면 새 버전 안내와 [지금 업데이트] 버튼이 나온다", async () => {
    checkUpdate.mockResolvedValue(
      remoteOk({
        behind: 3,
        latest_date: "2026-09-01T09:00:00Z",
        latest_subject: "길이 판정 정확도 개선",
      }),
    );
    const user = userEvent.setup();
    renderApp(<UpdatePage />);
    await user.click(await screen.findByTestId("check-button"));

    const box = await screen.findByTestId("check-behind");
    expect(box).toHaveTextContent("새 버전 3개 있음");
    expect(box).toHaveTextContent("2026년 9월 1일");
    expect(screen.getByTestId("check-behind-subject")).toHaveTextContent(
      "길이 판정 정확도 개선",
    );
    expect(screen.getByTestId("start-button")).toBeEnabled();
  });

  it("reachable=false 면 인터넷 확인 안내와 상세 사유를 보여준다", async () => {
    checkUpdate.mockResolvedValue(
      remoteOk({
        reachable: false,
        behind: null,
        error: "인터넷 연결 또는 저장소 접근 실패: timeout",
      }),
    );
    const user = userEvent.setup();
    renderApp(<UpdatePage />);
    await user.click(await screen.findByTestId("check-button"));

    const box = await screen.findByTestId("check-offline");
    expect(box).toHaveTextContent("인터넷에 연결되어 있는지 확인해 주세요");
    expect(box).toHaveTextContent("저장소 접근 실패");
    expect(screen.queryByTestId("start-button")).not.toBeInTheDocument();
  });
});

describe("UpdatePage — 확인 대화상자(교대 중 오조작 방지)", () => {
  async function openConfirm() {
    checkUpdate.mockResolvedValue(
      remoteOk({ behind: 2, latest_date: "2026-09-01T09:00:00Z" }),
    );
    const user = userEvent.setup();
    renderApp(<UpdatePage />);
    await user.click(await screen.findByTestId("check-button"));
    await user.click(await screen.findByTestId("start-button"));
    return user;
  }

  it("[지금 업데이트] 는 즉시 실행하지 않고 확인 모달을 띄운다", async () => {
    await openConfirm();
    expect(await screen.findByTestId("update-confirm")).toHaveTextContent(
      "업데이트 중에는 검사가 잠시 멈추고 화면 연결이 끊길 수 있습니다",
    );
    expect(startUpdate).not.toHaveBeenCalled();
  });

  it("취소하면 업데이트를 시작하지 않는다", async () => {
    const user = await openConfirm();
    await user.click(screen.getByTestId("confirm-cancel"));
    await waitFor(() =>
      expect(screen.queryByTestId("update-confirm")).not.toBeInTheDocument(),
    );
    expect(startUpdate).not.toHaveBeenCalled();
  });

  it("[업데이트 시작] 을 눌러야 startUpdate 가 호출된다", async () => {
    startUpdate.mockResolvedValue({ started: true, message: "시작됨" });
    const user = await openConfirm();
    await user.click(screen.getByTestId("confirm-start"));
    await waitFor(() => expect(startUpdate).toHaveBeenCalledTimes(1));
  });

  it("started=false 면 사유를 안내한다(중복 실행 방지 등)", async () => {
    startUpdate.mockResolvedValue({
      started: false,
      message: "이미 업데이트가 진행 중입니다",
    });
    const user = await openConfirm();
    await user.click(screen.getByTestId("confirm-start"));
    expect(await screen.findByTestId("start-rejected")).toHaveTextContent(
      "이미 업데이트가 진행 중입니다",
    );
  });
});

describe("UpdatePage — 진행/완료/실패", () => {
  it("state=running 이면 진행 안내와 로그 끝부분을 보여준다", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({
        progress: {
          state: "running",
          started_at: 1_756_000_000,
          log_tail: ["새 버전 내려받는 중", "의존성 설치 중…"],
        },
      }),
    );
    renderApp(<UpdatePage />);
    expect(await screen.findByTestId("update-progress")).toHaveTextContent(
      "업데이트 중…",
    );
    expect(screen.getByTestId("update-progress")).toHaveTextContent(
      "화면이 잠시 끊길 수 있습니다",
    );
    expect(screen.getByTestId("update-log")).toHaveTextContent("의존성 설치 중…");
    // 진행 중에는 중복 실행 방지를 위해 확인 버튼도 잠근다.
    expect(screen.getByTestId("check-button")).toBeDisabled();
  });

  it("state=success 면 완료 + [화면 새로고침] 안내", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({
        progress: { state: "success", exit_code: 0, log_tail: ["업데이트 완료"] },
      }),
    );
    renderApp(<UpdatePage />);
    const box = await screen.findByTestId("update-success");
    expect(box).toHaveTextContent("업데이트 완료");
    expect(screen.getByTestId("reload-button")).toHaveTextContent("화면 새로고침");
    expect(screen.queryByTestId("success-manual-restart")).not.toBeInTheDocument();
  });

  it("state=failed 면 실패 + 로그 + '이전 버전으로 계속 사용' 안심 안내", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({
        progress: {
          state: "failed",
          exit_code: 1,
          log_tail: ["오류: 디스크 공간 부족"],
        },
      }),
    );
    renderApp(<UpdatePage />);
    const box = await screen.findByTestId("update-failed");
    expect(box).toHaveTextContent("업데이트 실패");
    expect(box).toHaveTextContent("이전 버전으로 계속 사용할 수");
    expect(screen.getByTestId("update-log")).toHaveTextContent(
      "오류: 디스크 공간 부족",
    );
  });
});

describe("UpdatePage — 자동 재시작 불가 설치본(restart_supported=false)", () => {
  it("업데이트 시작 전에 '직접 다시 시작해야 한다' 안내가 보인다(확인 모달 포함)", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({ current: { restart_supported: false } }),
    );
    checkUpdate.mockResolvedValue(
      remoteOk({ behind: 1, latest_date: "2026-09-01T09:00:00Z" }),
    );
    const user = userEvent.setup();
    renderApp(<UpdatePage />);

    const warn = await screen.findByTestId("restart-warning");
    expect(warn).toHaveTextContent("자동으로 다시 시작하지 못합니다");
    expect(warn).toHaveTextContent("직접 다시 시작해야 새 버전이 적용됩니다");
    expect(warn).toHaveTextContent("부팅 자동시작을 등록해두면");

    // 확인 모달에도 같은 경고가 반복된다.
    await user.click(screen.getByTestId("check-button"));
    await user.click(await screen.findByTestId("start-button"));
    expect(await screen.findByTestId("confirm-restart-warning")).toHaveTextContent(
      "직접 다시 시작해야 새 버전이 적용됩니다",
    );
  });

  it("완료 화면에서 '아직 적용되지 않았습니다 — 다시 시작' 을 강조한다", async () => {
    fetchUpdateInfo.mockResolvedValue(
      infoWith({
        current: { restart_supported: false },
        progress: { state: "success", exit_code: 0, log_tail: ["내려받기 완료"] },
      }),
    );
    renderApp(<UpdatePage />);
    const box = await screen.findByTestId("success-manual-restart");
    expect(box).toHaveTextContent("아직 적용되지 않았습니다");
    expect(box).toHaveTextContent("프로그램을 다시 시작해 주세요");
  });
});

describe("UpdatePage — API 재시작 구간(회귀 방지: 에러 화면 금지)", () => {
  it("진행 중 폴링이 실패하면 '재시작 중' 안내를 보여주고 폴링을 계속한다", async () => {
    fetchUpdateInfo
      .mockReset()
      .mockResolvedValueOnce(
        infoWith({
          progress: { state: "running", log_tail: ["서비스 재시작 중"] },
        }),
      )
      .mockRejectedValue(new Error("Failed to fetch"));

    renderApp(<UpdatePage pollMs={FAST_POLL} />);
    await screen.findByTestId("update-progress");

    // API 가 재시작되어 요청이 실패해도 오류 화면으로 튕기지 않는다.
    const box = await screen.findByTestId("update-restarting");
    expect(box).toHaveTextContent("재시작 중… 잠시만 기다려 주세요");
    expect(box).toHaveTextContent("정상 과정");
    expect(screen.queryByTestId("update-error")).not.toBeInTheDocument();

    // 폴링은 멈추지 않는다(재시작이 끝나면 스스로 복구되어야 하므로).
    const calls = fetchUpdateInfo.mock.calls.length;
    await waitFor(() =>
      expect(fetchUpdateInfo.mock.calls.length).toBeGreaterThan(calls),
    );
  });

  it("재시작이 끝나 API 가 살아나면 완료 화면으로 복구된다", async () => {
    fetchUpdateInfo
      .mockReset()
      .mockResolvedValueOnce(infoWith({ progress: { state: "running" } }))
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValue(
        infoWith({ progress: { state: "success", exit_code: 0 } }),
      );

    renderApp(<UpdatePage pollMs={FAST_POLL} />);
    await screen.findByTestId("update-progress");
    expect(await screen.findByTestId("update-success")).toHaveTextContent(
      "업데이트 완료",
    );
  });

  it("업데이트와 무관한 상태에서의 조회 실패는 평소대로 오류를 알린다", async () => {
    fetchUpdateInfo.mockReset().mockRejectedValue(new Error("Failed to fetch"));
    renderApp(<UpdatePage />);
    expect(await screen.findByTestId("update-error")).toHaveTextContent(
      "업데이트 정보를 불러오지 못했습니다",
    );
    expect(screen.queryByTestId("update-restarting")).not.toBeInTheDocument();
  });
});

describe("UpdatePage 표시 헬퍼", () => {
  it("폴링 주기는 3초(계약)", () => {
    expect(POLL_MS).toBe(3000);
  });

  it("fmtKoreanDate — 사람이 읽는 한국어 날짜", () => {
    expect(fmtKoreanDate("2026-08-29T09:00:00Z")).toBe("2026년 8월 29일");
    expect(fmtKoreanDate(null)).toBe("알 수 없음");
  });

  it("updatePhase — 요청 실패는 진행 중이던 경우에만 '재시작 중'", () => {
    expect(updatePhase({ state: "idle", started: false, offline: false })).toBe("idle");
    expect(updatePhase({ state: "running", started: false, offline: false })).toBe("running");
    expect(updatePhase({ state: "running", started: false, offline: true })).toBe("restarting");
    expect(updatePhase({ state: null, started: true, offline: true })).toBe("restarting");
    expect(updatePhase({ state: "idle", started: false, offline: true })).toBe("idle");
    expect(updatePhase({ state: "success", started: false, offline: false })).toBe("success");
    expect(updatePhase({ state: "failed", started: false, offline: false })).toBe("failed");
  });
});
