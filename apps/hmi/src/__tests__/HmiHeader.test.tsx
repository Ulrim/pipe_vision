/**
 * 상단 바 = 현장 화면의 유일한 상태 표시줄 (재설계).
 *
 * 이전에는 연결 배지(ConnectionIndicator) · 워커 상태 스트립(LiveStatusStrip) ·
 * 계정(AuthStatus) 이 각각 자리를 차지해 800x480 화면의 절반을 먹었다.
 * 셋을 이 한 줄로 합쳤으므로, 그 컴포넌트들이 검증하던 동작을 여기서 잇는다:
 * 품목 표시 / 검출 개수 / 워커 무신호·미검출·오류 감지 / 로그아웃 접근.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { HmiHeader } from "@/components/HmiHeader";
import { useLiveStore } from "@/store/liveStore";
import { useAuthStore } from "@/store/authStore";
import type { StatusData } from "@/types/ws";

function setStatus(over: Partial<StatusData> = {}, statusAt = Date.now()) {
  const status: StatusData = {
    cam_id: "CAM-1", item_code: "HP12", expected: 4, detected: 4,
    ng: 0, mismatch: false, proc_time_ms: 120,
    ts: "2026-08-29T10:00:00+09:00", error: null, ...over,
  };
  useLiveStore.setState({ status, statusAt, conn: "open" });
}

beforeEach(() => {
  // 상단 바는 1초 tick(무신호 판정)과 20초 tick(시계)을 돌린다. 실제 타이머로
  // 두면 테스트 도중 act 밖에서 갱신되어 경고가 난다 — 가짜 타이머로 고정한다.
  vi.useFakeTimers();
  useLiveStore.setState({ status: null, statusAt: null, conn: "open", latest: null });
  useAuthStore.setState({ session: { access_token: "t", username: "op1", role: "operator" } as never });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("HmiHeader (상단 상태 바)", () => {
  it("검사 중인 품목과 검출 개수를 보여준다", () => {
    setStatus({ item_code: "HP20", detected: 3, expected: 4 });
    render(<HmiHeader />);
    expect(screen.getByTestId("header-item")).toHaveTextContent("HP20");
    expect(screen.getByTestId("header-detected")).toHaveTextContent("검출 3/4");
  });

  it("연결이 끊기면 '연결 끊김' 을 기호와 함께 표기한다", () => {
    useLiveStore.setState({ conn: "reconnecting" });
    render(<HmiHeader />);
    const h = screen.getByTestId("header-health");
    expect(h).toHaveTextContent("연결 끊김");
    expect(h).toHaveAttribute("data-tone", "bad");
  });

  it("튜브 미검출이면 경고로 표기한다(조명·배치 확인 신호)", () => {
    setStatus({ detected: 0 });
    render(<HmiHeader />);
    const h = screen.getByTestId("header-health");
    expect(h).toHaveTextContent("미검출");
    expect(h).toHaveAttribute("data-tone", "warn");
  });

  it("취득 오류가 오면 이상 상태로 표기한다", () => {
    setStatus({ error: "camera timeout" });
    render(<HmiHeader />);
    expect(screen.getByTestId("header-health")).toHaveTextContent("취득 오류");
  });

  it("6초 넘게 하트비트가 없으면 '워커 응답 없음' 으로 바뀐다", () => {
    setStatus({}, Date.now() - 7000);
    render(<HmiHeader />);
    act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getByTestId("header-health")).toHaveTextContent(
      "워커 응답 없음",
    );
  });

  it("정상 검사 중에는 무채색으로 둔다(색은 이상 상태 전용 — 고성능 HMI)", () => {
    setStatus();
    render(<HmiHeader />);
    const h = screen.getByTestId("header-health");
    expect(h).toHaveTextContent("검사 중");
    expect(h).toHaveAttribute("data-tone", "ok");
    // 정상일 때 색 클래스(ok/ng 토큰)를 쓰지 않는다.
    expect(h.className).not.toMatch(/bg-ok|bg-ng/);
  });

  it("로그아웃은 ⋯ 메뉴 안에 감춰 둔다(현장 오조작 방지)", () => {
    setStatus();
    render(<HmiHeader />);
    expect(screen.queryByTestId("menu-logout")).toBeNull();
    fireEvent.click(screen.getByTestId("header-menu"));
    expect(screen.getByTestId("menu-logout")).toBeInTheDocument();
  });
});
