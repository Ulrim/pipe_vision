import { describe, expect, it, beforeEach, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderApp } from "@/test/utils";
import { useAuthStore } from "@/store/auth";
import { Role } from "@aivis/shared-types";
import App from "./App";

// 페이지 데이터 fetch 가 보호 라우팅 검증과 무관하도록 엔드포인트 모킹(네트워크 차단).
vi.mock("@/api/endpoints", () => ({
  fetchKpiSummary: vi.fn().mockResolvedValue({}),
  fetchInspections: vi.fn().mockResolvedValue([]),
  fetchItems: vi.fn().mockResolvedValue([]),
  login: vi.fn(),
}));

beforeEach(() => {
  // 각 테스트 전 인증 스토어 초기화(persist 잔여 제거).
  useAuthStore.getState().clear();
});

describe("App 보호 라우팅 (사내 도구 — 전체 로그인 필수)", () => {
  it("미인증 시 보호 경로(/kpi) 접근 → /login 으로 리다이렉트", () => {
    renderApp(<App />, "/kpi");
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("미인증 시 루트(/) 접근 → 로그인 화면", () => {
    renderApp(<App />, "/");
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("미인증 시 알 수 없는 경로 → 로그인 화면", () => {
    renderApp(<App />, "/something-else");
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
  });

  it("인증 후 보호 경로(/kpi) 정상 접근(로그인 폼 미표시)", () => {
    useAuthStore.getState().setAuth({
      token: "t.t.t",
      username: "kim",
      role: Role.ADMIN,
    });
    renderApp(<App />, "/kpi");
    expect(screen.queryByTestId("login-form")).not.toBeInTheDocument();
    // 헤더에 사용자/로그아웃 노출.
    expect(screen.getByText("로그아웃")).toBeInTheDocument();
  });
});
